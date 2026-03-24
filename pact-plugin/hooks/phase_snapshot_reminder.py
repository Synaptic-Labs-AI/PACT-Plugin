#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/phase_snapshot_reminder.py
Summary: PostToolUse hook on TaskUpdate that reminds the orchestrator to save
         organizational state when a PACT phase task is marked completed.
Used by: hooks.json PostToolUse hook (matcher: TaskUpdate)

Reads the task file from disk (using taskId from tool_input) to get the task
subject, then checks whether it contains a PACT phase keyword. This is
necessary because TaskUpdate tool calls typically only include taskId and
status — the subject is stored on disk, not in tool_input.

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin with tool_name, tool_input, tool_output
Output: JSON systemMessage on stdout if phase completion detected, nothing otherwise
"""

import json
import os
import sys
from pathlib import Path

from shared.error_output import hook_error_json

PHASE_KEYWORDS = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")

REMINDER_MESSAGE = (
    "Phase complete -- save organizational state (active agents, phase status, "
    "memory layer status) to feature task metadata."
)


def _read_task_subject(
    task_id: str,
    tasks_base_dir: str | None = None,
) -> str | None:
    """Read the task subject from disk by scanning task directories.

    Task files live at ~/.claude/tasks/{team_name}/{taskId}.json.
    Since the team name isn't in tool_input, we scan all team directories.

    Args:
        task_id: The task ID to look up
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        The task subject string, or None if not found
    """
    if not task_id:
        return None

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    tasks_dir = Path(tasks_base_dir)
    if not tasks_dir.exists():
        return None

    # Scan all team directories for the task file
    try:
        for team_dir in tasks_dir.iterdir():
            if not team_dir.is_dir():
                continue
            task_file = team_dir / f"{task_id}.json"
            if task_file.exists():
                try:
                    data = json.loads(task_file.read_text(encoding="utf-8"))
                    return data.get("subject", "")
                except (json.JSONDecodeError, OSError):
                    continue
    except OSError:
        pass

    return None


def check_phase_completion(
    tool_input: dict,
    tasks_base_dir: str | None = None,
) -> bool:
    """Determine if a phase task is being marked completed.

    Args:
        tool_input: The TaskUpdate tool's input parameters
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        True if this is a phase task being set to completed
    """
    if tool_input.get("status") != "completed":
        return False

    task_id = tool_input.get("taskId", "")
    if not task_id:
        return False

    subject = _read_task_subject(task_id, tasks_base_dir)
    if not subject:
        return False

    return any(keyword in subject for keyword in PHASE_KEYWORDS)


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            sys.exit(0)

        tool_input = input_data.get("tool_input", {})

        if check_phase_completion(tool_input):
            print(json.dumps({"systemMessage": REMINDER_MESSAGE}))

        sys.exit(0)

    except Exception as e:
        # Fail open -- never block tool use
        print(f"Hook warning (phase_snapshot_reminder): {e}", file=sys.stderr)
        print(hook_error_json("phase_snapshot_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
