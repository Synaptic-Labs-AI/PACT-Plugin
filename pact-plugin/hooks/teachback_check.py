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

from shared import TEACHBACK_MODE_ADVISORY
from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.pact_context import get_session_dir, get_team_name, resolve_agent_name
from shared.session_journal import append_event, make_event

# Mirror teachback_gate.py _TEACHBACK_MODE semantics. Legacy advisory emit is
# a Phase 1 observability surface only; once teachback_gate flips to blocking
# (_TEACHBACK_MODE="blocking"), the legacy emit here must also stop — the
# check_teachback_phase2_readiness.py diagnostic reads a single advisory-event
# stream and a mixed-mode stream poisons the readiness signal (C12, round 3).
_TEACHBACK_MODE: str = TEACHBACK_MODE_ADVISORY

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


def _get_marker_path(
    agent_name: str,
    task_id: str = "",
    sessions_dir: str | None = None,
) -> Path:
    """
    Build the path for the one-shot warning marker file.

    Per-task markers ensure warnings re-fire when an agent is reassigned to a
    new task within the same session. Markers are session-scoped so parallel
    sessions on the same project don't interfere.

    Path: ~/.claude/pact-sessions/{slug}/{session_id}/teachback-warned-{agent_name}-{task_id}
    Fallback (no task_id): .../{session_id}/teachback-warned-{agent_name}

    Args:
        agent_name: The agent's unique name
        task_id: The task ID (file basename without .json extension)
        sessions_dir: Override for session directory (for testing). When
            provided, used directly as the parent directory for markers
            (no slug/session_id appended).

    Returns:
        Path object for the marker file
    """
    suffix = f"-{task_id}" if task_id else ""
    marker_name = f"teachback-warned-{agent_name}{suffix}"

    if sessions_dir is not None:
        return Path(sessions_dir) / marker_name

    session_dir = get_session_dir()
    if session_dir:
        return Path(session_dir) / marker_name

    # Fallback: no session context available — use bare pact-sessions root.
    # Markers here are orphaned; cleanup sweeps only cover slug-level and
    # session-scoped dirs, so root-level markers persist until manual removal.
    return Path.home() / ".claude" / "pact-sessions" / marker_name


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


# Why a custom scanner instead of calling into shared/session_state.py:
# - session_state summarizes the WHOLE session (task counts + roster + journal-
#   derived fields). This hook only needs per-task teachback_sent metadata for
#   the current agent's in_progress tasks — narrower scope, fewer I/O operations.
# - Returns (bool, task_id) tuple after scanning ALL matching tasks (sorted for
#   deterministic iteration order), vs session_state which aggregates counts.
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


def _emit_legacy_advisory(task_id: str, agent_name: str, tool_name: str) -> None:
    """Emit a teachback_gate_advisory journal event for the legacy
    missing_teachback_sent warning path.

    Phase 1 observability: scripts/check_teachback_phase2_readiness.py reads
    teachback_gate_advisory events to classify would_have_blocked observations
    and drive the Phase 2 flip decision. The legacy PostToolUse warning here
    must emit with reason="missing_teachback_sent" so the diagnostic can
    distinguish legacy-advisory false positives from the new teachback_gate
    reason codes (missing_submit / invalid_submit / awaiting_approval /
    unaddressed_items / corrections_pending).

    Per COMPONENT-DESIGN.md §Hook 5 (lines 645-678), JOURNAL-EVENTS.md
    §Writer site audit (line 341), and RISK-MAP.md §Risk #5 (de-dup-by-reason
    diagnostic). Schema per session_journal.py _REQUIRED_FIELDS_BY_TYPE
    (task_id, agent) + _OPTIONAL_FIELDS_BY_TYPE (would_have_blocked, reason,
    tool_name). Mirrors teachback_gate._emit_advisory_event shape verbatim.

    SACROSANCT fail-open: any journal error is swallowed; observability must
    never block tool execution.
    """
    try:
        append_event(
            make_event(
                "teachback_gate_advisory",
                task_id=task_id,
                agent=agent_name,
                would_have_blocked=True,
                reason="missing_teachback_sent",
                tool_name=tool_name,
            )
        )
    except Exception:
        pass


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

        # Extract tool_name for advisory-event attribution. PostToolUse fires
        # on Edit|Write|Bash (matcher in hooks.json), so tool_name varies.
        tool_name = input_data.get("tool_name", "")
        if not isinstance(tool_name, str):
            tool_name = ""

        warn, task_id = should_warn(agent_name, team_name)
        if warn:
            _mark_warned(agent_name, task_id)
            # C12 (round 3): gate the legacy advisory emit on Phase-1 advisory
            # mode so it mirrors teachback_gate.py:578 symmetry. Post-Phase-2
            # flip, the readiness diagnostic must observe a consistent single-
            # mode advisory stream — emitting here while teachback_gate has
            # moved to blocking mode would inject stale false-positive advisory
            # events alongside real teachback_gate_blocked events.
            if _TEACHBACK_MODE == TEACHBACK_MODE_ADVISORY:
                _emit_legacy_advisory(task_id, agent_name, tool_name)
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
