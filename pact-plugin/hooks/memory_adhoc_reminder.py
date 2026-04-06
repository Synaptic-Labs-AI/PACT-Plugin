#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/memory_adhoc_reminder.py
Summary: Stop hook that emits memory-related reminders at session end.
Used by: hooks.json Stop hook

Non-blocking (always exit 0). Three reminder paths (checked in priority order):
1. "uncompleted_tasks" — agent-owned tasks still in_progress at session end.
   Last-resort safety net for agents that crashed or missed the TeammateIdle gate.
2. "unprocessed_handoffs" — session journal contains agent_handoff events at session
   end, meaning workflow HANDOFFs were captured but never processed by the secretary.
3. "adhoc_save" — no agent_handoff events but session had substantive ad-hoc work
   outside formal PACT workflows.

Uses a file-based reentrancy guard (~/.claude/teams/{team_name}/.adhoc_reminded)
to prevent duplicate reminders across process invocations. The guard file is
cleaned up automatically when the team directory is removed (TeamDelete).

Input: JSON from stdin with transcript (Stop hook payload)
Output: JSON with systemMessage if reminder needed, nothing otherwise
"""

import json
import os
import sys
from pathlib import Path

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.pact_context import get_session_dir, get_team_name
from shared.session_journal import read_events

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

MIN_TRANSCRIPT_LENGTH = 500

REMINDER_UNCOMPLETED_TASKS = "uncompleted_tasks"
REMINDER_UNPROCESSED_HANDOFFS = "unprocessed_handoffs"
REMINDER_ADHOC_SAVE = "adhoc_save"


def find_uncompleted_tasks(
    team_name: str,
    tasks_base_dir: str | None = None,
) -> list[dict]:
    """
    Scan task files for agent-owned tasks still in_progress at session end.

    Fails open — returns empty list on any I/O or parse error.

    Args:
        team_name: Session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        List of dicts with 'id' and 'subject' for each uncompleted task
    """
    if not team_name:
        return []

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    task_dir = Path(tasks_base_dir) / team_name
    if not task_dir.exists():
        return []

    uncompleted = []
    try:
        for task_file in task_dir.iterdir():
            if not task_file.name.endswith(".json"):
                continue
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if data.get("status") != "in_progress":
                continue
            if not data.get("owner"):
                continue  # Unowned tasks are not agent tasks

            task_id = task_file.stem
            subject = data.get("subject", "unknown")
            uncompleted.append({"id": task_id, "subject": subject})
    except OSError:
        return []

    return uncompleted


def get_reminder_type(team_name: str, transcript: str) -> str | None:
    """
    Determine which reminder to emit, if any.

    Checks in priority order: uncompleted_tasks > unprocessed_handoffs > adhoc_save.

    Returns:
        REMINDER_UNCOMPLETED_TASKS — agent tasks still in_progress at session end
        REMINDER_UNPROCESSED_HANDOFFS — session journal has agent_handoff events (workflow ran but memory not processed)
        REMINDER_ADHOC_SAVE — no agent_handoff events but substantive ad-hoc work detected
        None — no reminder needed

    Args:
        team_name: Session team name from env
        transcript: Session transcript text
    """
    if not team_name:
        return None

    # Guard file lives in session dir (survives independently of team dir)
    session_dir = get_session_dir()
    if session_dir:
        guard_dir = Path(session_dir)
    else:
        guard_dir = Path.home() / ".claude" / "teams" / team_name

    # If already reminded this session, skip
    if (guard_dir / ".adhoc_reminded").exists():
        return None

    # Path 0: Agent-owned tasks still in_progress (highest priority)
    if find_uncompleted_tasks(team_name):
        return REMINDER_UNCOMPLETED_TASKS

    # Path 1: session journal has agent_handoff events → unprocessed HANDOFFs
    if read_events(event_type="agent_handoff"):
        return REMINDER_UNPROCESSED_HANDOFFS

    # Path 2: No agent_handoff events but substantive ad-hoc work
    if len(transcript) < MIN_TRANSCRIPT_LENGTH:
        return None

    # Only remind for work sessions (file modifications), not pure chat.
    # Match quoted tool names ('"Edit"', '"Write"') to avoid false-positives
    # on words like "Editorial" or "Rewrite" in discussion text.
    if '"Edit"' not in transcript and '"Write"' not in transcript:
        return None

    return REMINDER_ADHOC_SAVE


def _write_guard_file(team_name: str) -> None:
    """Write the .adhoc_reminded guard file to prevent duplicate reminders."""
    session_dir = get_session_dir()
    if session_dir:
        guard_dir = Path(session_dir)
    else:
        guard_dir = Path.home() / ".claude" / "teams" / team_name
    if not guard_dir.exists():
        return
    guard_path = guard_dir / ".adhoc_reminded"
    try:
        fd = os.open(str(guard_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    except OSError:
        pass  # Already exists or write failure — either way, safe to continue


def format_uncompleted_message(uncompleted: list[dict]) -> str:
    """
    Format the uncompleted tasks warning message.

    Dynamic message — includes task count and subjects so the orchestrator
    knows which tasks were left in_progress.

    Args:
        uncompleted: List of dicts with 'id' and 'subject'

    Returns:
        Warning message string for systemMessage JSON
    """
    count = len(uncompleted)
    subjects = ", ".join(t["subject"] for t in uncompleted)
    return (
        f"Warning: {count} task(s) still in_progress at session end: "
        f"{subjects}. These may have incomplete HANDOFFs."
    )


_MESSAGES = {
    REMINDER_UNPROCESSED_HANDOFFS: (
        "Unprocessed HANDOFFs detected from this session's workflow. "
        "Consider running /PACT:wrap-up or ensuring the secretary "
        "processes them in the next session."
    ),
    REMINDER_ADHOC_SAVE: (
        "This session had work outside formal PACT workflows. "
        "If significant decisions or discoveries were made, consider "
        "sending the secretary a save request via SendMessage."
    ),
}


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        team_name = get_team_name()
        transcript = input_data.get("transcript", "")

        reminder_type = get_reminder_type(team_name, transcript)
        if not reminder_type:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Dynamic message for uncompleted tasks (needs task details)
        printed = False
        if reminder_type == REMINDER_UNCOMPLETED_TASKS:
            uncompleted = find_uncompleted_tasks(team_name)
            if uncompleted:
                _write_guard_file(team_name)
                message = format_uncompleted_message(uncompleted)
                print(json.dumps({"systemMessage": message}))
                printed = True
        elif reminder_type in _MESSAGES:
            _write_guard_file(team_name)
            print(json.dumps({"systemMessage": _MESSAGES[reminder_type]}))
            printed = True

        if not printed:
            print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Fail open — never block session stop
        print(f"Hook warning (memory_adhoc_reminder): {e}", file=sys.stderr)
        print(hook_error_json("memory_adhoc_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
