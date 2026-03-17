#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teammate_completion_gate.py
Summary: TeammateIdle hook that blocks agents from going idle when they own
         in_progress tasks with HANDOFF metadata already present.
Used by: hooks.json TeammateIdle hook (runs before teammate_idle.py)

This is a mechanical safety net for the "agent forgot to self-complete" bug.
When an agent finishes work, it stores HANDOFF metadata via TaskUpdate but
sometimes neglects to mark the task as completed. This hook catches that case
and sends feedback via exit 2, telling the agent exactly which tasks to complete.

Exit codes:
    0 — allow idle (no completable tasks found, or fail-open on error)
    2 — block idle (agent has tasks ready to complete)

Input: JSON from stdin with teammate_name, team_name
Output: stderr feedback on block (exit 2), nothing on allow (exit 0)
"""

import json
import os
import sys
from pathlib import Path


def find_completable_tasks(
    teammate_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> list[dict]:
    """
    Find tasks owned by this teammate that have HANDOFF metadata but are
    still in_progress — meaning the work is done but the task wasn't completed.

    Scans ~/.claude/tasks/{team_name}/ for JSON task files. Fails open on
    any I/O or parsing error (returns empty list).

    Args:
        teammate_name: Name of the idle teammate
        team_name: Session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        List of dicts with 'id' and 'subject' for each completable task
    """
    if not teammate_name or not team_name:
        return []

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    task_dir = Path(tasks_base_dir) / team_name
    if not task_dir.exists():
        return []

    completable = []

    try:
        for task_file in task_dir.iterdir():
            if not task_file.name.endswith(".json"):
                continue

            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue  # Malformed or unreadable — skip

            if data.get("owner") != teammate_name:
                continue
            if data.get("status") != "in_progress":
                continue

            metadata = data.get("metadata", {})
            if not metadata.get("handoff"):
                continue  # No HANDOFF yet — agent is still working

            task_id = task_file.stem  # filename without .json
            subject = data.get("subject", "unknown")
            completable.append({"id": task_id, "subject": subject})
    except OSError:
        return []  # Can't scan directory — fail open

    return completable


def format_feedback(completable: list[dict]) -> str:
    """
    Format the feedback message telling the agent which tasks to complete.

    Args:
        completable: List of dicts with 'id' and 'subject'

    Returns:
        Feedback message string for stderr
    """
    if len(completable) == 1:
        task = completable[0]
        return (
            f"You have a completed task that needs to be marked done. "
            f"Task #{task['id']} ({task['subject']}) has HANDOFF metadata "
            f"but is still in_progress. Run: "
            f"TaskUpdate(taskId=\"{task['id']}\", status=\"completed\")"
        )

    task_list = ", ".join(
        f"#{t['id']} ({t['subject']})" for t in completable
    )
    task_ids = ", ".join(f"\"{t['id']}\"" for t in completable)
    return (
        f"You have {len(completable)} tasks with HANDOFF metadata that are "
        f"still in_progress: {task_list}. Mark each completed via "
        f"TaskUpdate(taskId=<id>, status=\"completed\") for IDs: {task_ids}"
    )


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        teammate_name = input_data.get("teammate_name", "")
        team_name = (
            input_data.get("team_name")
            or os.environ.get("CLAUDE_CODE_TEAM_NAME", "")
        ).lower()

        if not teammate_name or not team_name:
            sys.exit(0)

        completable = find_completable_tasks(teammate_name, team_name)

        if completable:
            print(format_feedback(completable), file=sys.stderr)
            sys.exit(2)  # Block idle — agent has tasks to complete

        sys.exit(0)

    except Exception:
        # Fail open — never trap an agent in unrecoverable state
        sys.exit(0)


if __name__ == "__main__":
    main()
