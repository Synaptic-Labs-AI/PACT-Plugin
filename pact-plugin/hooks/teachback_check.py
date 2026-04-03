#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teachback_check.py
Summary: PostToolUse hook on Edit|Write|Bash that emits a one-shot warning if an
         agent uses implementation tools before setting teachback_sent metadata.
Used by: hooks.json PostToolUse hook (matcher: Edit|Write|Bash)

Layer 3 of the teachback enforcement architecture. Checks task metadata for
teachback_sent: true on the agent's in_progress task. If missing, emits a
non-blocking systemMessage reminder on the first implementation tool call, then
suppresses further warnings via a session-scoped, per-task marker file.

Markers are per-task (teachback-warned-{agent}-{task_id}) so warnings re-fire
when an agent is reassigned to a new task within the same session.

Exemptions: secretary (custom On Start flow), auditor (observation only).
Non-PACT agents and the orchestrator (no agent identity resolvable) are skipped.

Exit codes:
    0 — always (non-blocking; this is a warning layer, not a gate)

Input: JSON from stdin with tool_name, tool_input, tool_output
Output: JSON systemMessage on stdout if warning needed, suppressOutput otherwise
"""

import json
import os
import sys
from pathlib import Path

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.pact_context import get_team_name, resolve_agent_name

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Agent names exempt from teachback check.
# Secretary has a custom On Start flow (session briefing at spawn).
# Auditor is observation-only and uses signal-based completion.
_EXEMPT_AGENTS = frozenset({
    "secretary",
    "pact-secretary",
    "auditor",
    "pact-auditor",
})

_WARNING_MESSAGE = (
    "\u26a0\ufe0f TEACHBACK REMINDER: You are modifying files but no teachback "
    "has been recorded for your current task.\n"
    "Per the agent-teams protocol (On Start step 4), you should send a "
    "teachback via SendMessage BEFORE implementation work.\n"
    "If you already sent a teachback, please set metadata:\n"
    'TaskUpdate(taskId, metadata={"teachback_sent": true})'
)


def _get_project_slug() -> str:
    """Derive project slug from CLAUDE_PROJECT_DIR (basename)."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if project_dir:
        return Path(project_dir).name
    return ""


def _get_marker_path(
    agent_name: str,
    task_id: str = "",
    sessions_dir: str | None = None,
) -> Path:
    """
    Build the path for the one-shot warning marker file.

    Per-task markers ensure warnings re-fire when an agent is reassigned to a
    new task within the same session.

    Path: ~/.claude/pact-sessions/{slug}/teachback-warned-{agent_name}-{task_id}
    Fallback (no task_id): ~/.claude/pact-sessions/{slug}/teachback-warned-{agent_name}

    Args:
        agent_name: The agent's unique name
        task_id: The task ID (file basename without .json extension)
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        Path object for the marker file
    """
    slug = _get_project_slug()
    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")
    suffix = f"-{task_id}" if task_id else ""
    return Path(sessions_dir) / slug / f"teachback-warned-{agent_name}{suffix}"


def _was_already_warned(
    agent_name: str,
    task_id: str = "",
    sessions_dir: str | None = None,
) -> bool:
    """Check if this agent+task has already been warned in this session."""
    return _get_marker_path(agent_name, task_id, sessions_dir).exists()


def _mark_warned(
    agent_name: str,
    task_id: str = "",
    sessions_dir: str | None = None,
) -> None:
    """Write the one-shot marker file to suppress future warnings."""
    marker = _get_marker_path(agent_name, task_id, sessions_dir)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Use 0o600 for user-only read/write (project security convention)
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
    except OSError:
        pass  # Non-critical — worst case is repeated warnings


# Why a custom scanner instead of shared/task_scanner.py:
# - task_scanner.scan_all_tasks() scans ALL team directories (needed by compaction
#   hooks that operate across the full task tree). This hook only needs the current
#   team's tasks — narrower scope, fewer I/O operations.
# - Returns (bool, task_id) tuple after scanning ALL matching tasks (sorted for
#   deterministic iteration order), vs task_scanner which collects all tasks
#   into a list for analysis.
# - Needs per-file task_id (filename stem) for per-task marker support.
def check_teachback_sent(
    agent_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> tuple[bool, str]:
    """
    Check if ALL in_progress tasks for the agent have teachback_sent set.

    Scans the team's task directory for tasks owned by this agent that are
    in_progress. Returns confirmed only when every such task has
    metadata.teachback_sent == true. This prevents a stale teachback on an
    older task from satisfying the check for a newer task (e.g., after agent
    reuse via SendMessage).

    Args:
        agent_name: The agent's unique name (e.g., "backend-coder-1")
        team_name: The session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        Tuple of (confirmed, task_id):
        - (True, "") if ALL in_progress tasks have teachback, or on fail-open error
        - (False, task_id) if an in_progress task needs teachback warning
        The task_id is the file basename (without .json) of the first
        unconfirmed in_progress task, used for per-task marker files.
    """
    if not agent_name or not team_name:
        return (True, "")  # Can't identify agent — fail open

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    task_dir = Path(tasks_base_dir) / team_name
    if not task_dir.exists():
        return (True, "")  # No task directory — fail open

    first_unconfirmed_task_id = ""
    found_any_in_progress = False
    try:
        for task_file in sorted(task_dir.iterdir()):
            if not task_file.name.endswith(".json"):
                continue

            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if data.get("owner") != agent_name:
                continue
            if data.get("status") != "in_progress":
                continue

            found_any_in_progress = True
            metadata = data.get("metadata") or {}
            if metadata.get("teachback_sent") is not True:
                # Track first unconfirmed task for per-task marker
                if not first_unconfirmed_task_id:
                    first_unconfirmed_task_id = task_file.stem
    except OSError:
        return (True, "")  # Can't scan — fail open

    # If any in_progress task lacks teachback, warn for that task
    if first_unconfirmed_task_id:
        return (False, first_unconfirmed_task_id)

    # All in_progress tasks have teachback confirmed
    if found_any_in_progress:
        return (True, "")

    # No in_progress tasks found for this agent
    return (False, "")


def should_warn(
    agent_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
    sessions_dir: str | None = None,
) -> tuple[bool, str]:
    """
    Determine if a teachback warning should be emitted.

    Returns (True, task_id) if:
    1. Agent is not exempt (secretary, auditor)
    2. Agent has not been warned already for this task (per-task marker)
    3. Any in_progress task lacks teachback_sent metadata

    Args:
        agent_name: The agent's unique name
        team_name: The session team name
        tasks_base_dir: Override for tasks base directory (for testing)
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        Tuple of (warn, task_id):
        - (True, task_id) if warning should be emitted for this task
        - (False, "") if no warning needed
    """
    # Exempt agents skip the check entirely
    if agent_name.lower() in _EXEMPT_AGENTS:
        return (False, "")

    # Check task metadata for teachback confirmation
    confirmed, task_id = check_teachback_sent(
        agent_name, team_name, tasks_base_dir
    )
    if confirmed:
        return (False, "")

    # Per-task one-shot: already warned for this specific task
    if _was_already_warned(agent_name, task_id, sessions_dir):
        return (False, "")

    return (True, task_id)


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        team_name = get_team_name()
        if not team_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # PostToolUse hooks require agent context to be meaningful.
        # If no agent name, this is the orchestrator or a non-PACT context.
        # resolve_agent_name needs parsed stdin, so it must come after json.load.
        agent_name = resolve_agent_name(input_data)
        if not agent_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        warn, task_id = should_warn(agent_name, team_name)
        if warn:
            _mark_warned(agent_name, task_id)
            print(json.dumps({"systemMessage": _WARNING_MESSAGE}))
        else:
            print(_SUPPRESS_OUTPUT)

        sys.exit(0)

    except Exception as e:
        # Fail open — never block implementation work
        print(f"Hook warning (teachback_check): {e}", file=sys.stderr)
        print(hook_error_json("teachback_check", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
