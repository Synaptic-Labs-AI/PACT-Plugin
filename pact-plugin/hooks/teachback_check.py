#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teachback_check.py
Summary: PostToolUse hook on Edit|Write that emits a one-shot warning if an
         agent uses implementation tools before setting teachback_sent metadata.
Used by: hooks.json PostToolUse hook (matcher: Edit|Write)

Layer 3 of the teachback enforcement architecture. Checks task metadata for
teachback_sent: true on the agent's in_progress task. If missing, emits a
non-blocking systemMessage reminder on the first Edit/Write call, then
suppresses further warnings via a session-scoped marker file.

Exemptions: secretary (custom On Start flow), auditor (observation only).
Non-PACT agents and the orchestrator (no CLAUDE_CODE_AGENT_NAME) are skipped.

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
    sessions_dir: str | None = None,
) -> Path:
    """
    Build the path for the one-shot warning marker file.

    Path: ~/.claude/pact-sessions/{slug}/teachback-warned-{agent_name}

    Args:
        agent_name: The agent's unique name
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        Path object for the marker file
    """
    slug = _get_project_slug()
    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")
    return Path(sessions_dir) / slug / f"teachback-warned-{agent_name}"


def _was_already_warned(
    agent_name: str,
    sessions_dir: str | None = None,
) -> bool:
    """Check if this agent has already been warned in this session."""
    return _get_marker_path(agent_name, sessions_dir).exists()


def _mark_warned(
    agent_name: str,
    sessions_dir: str | None = None,
) -> None:
    """Write the one-shot marker file to suppress future warnings."""
    marker = _get_marker_path(agent_name, sessions_dir)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        # Use 0o600 for user-only read/write (project security convention)
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
    except OSError:
        pass  # Non-critical — worst case is repeated warnings


def check_teachback_sent(
    agent_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> bool:
    """
    Check if the agent has set teachback_sent in any of their in_progress tasks.

    Scans the team's task directory for tasks owned by this agent that are
    in_progress and have metadata.teachback_sent == true.

    Args:
        agent_name: The agent's unique name (e.g., "backend-coder-1")
        team_name: The session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        True if teachback confirmed in any owned task, False if not found.
        Fails open (returns True) on any error.
    """
    if not agent_name or not team_name:
        return True  # Can't identify agent — fail open

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    task_dir = Path(tasks_base_dir) / team_name
    if not task_dir.exists():
        return True  # No task directory — fail open

    try:
        for task_file in task_dir.iterdir():
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

            metadata = data.get("metadata") or {}
            if metadata.get("teachback_sent") is True:
                return True
    except OSError:
        return True  # Can't scan — fail open

    return False


def should_warn(
    agent_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
    sessions_dir: str | None = None,
) -> bool:
    """
    Determine if a teachback warning should be emitted.

    Returns True if:
    1. Agent is not exempt (secretary, auditor)
    2. Agent has not been warned already (one-shot marker)
    3. No in_progress task has teachback_sent metadata

    Args:
        agent_name: The agent's unique name
        team_name: The session team name
        tasks_base_dir: Override for tasks base directory (for testing)
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        True if warning should be emitted, False otherwise
    """
    # Exempt agents skip the check entirely
    if agent_name.lower() in _EXEMPT_AGENTS:
        return False

    # One-shot: already warned this session
    if _was_already_warned(agent_name, sessions_dir):
        return False

    # Check task metadata for teachback confirmation
    if check_teachback_sent(agent_name, team_name, tasks_base_dir):
        return False

    return True


def main():
    try:
        # PostToolUse hooks require agent context to be meaningful.
        # If no agent name, this is the orchestrator or a non-PACT context.
        agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "")
        if not agent_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
        if not team_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        try:
            json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        if should_warn(agent_name, team_name):
            _mark_warned(agent_name)
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
