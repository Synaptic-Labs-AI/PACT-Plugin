#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teammate_completion_gate.py
Summary: TeammateIdle hook that blocks agents from going idle when they own
         in_progress tasks — either with HANDOFF metadata (forgot to mark
         complete) or without HANDOFF metadata (stuck looping on handoff gate).
Used by: hooks.json TeammateIdle hook (runs before teammate_idle.py)

Two safety nets in one hook:
1. "Agent forgot to self-complete": Has HANDOFF metadata but didn't mark the
   task completed. Tells agent to run TaskUpdate(status="completed").
2. "Agent stuck on handoff gate loop" (#296): Agent went idle without ever
   storing HANDOFF metadata. Provides a concrete copy-paste example so the
   agent can self-correct instead of looping.

Exit codes:
    0 — allow idle (no actionable tasks found, or fail-open on error)
    2 — block idle (agent has tasks that need attention)

Input: JSON from stdin with teammate_name, team_name
Output: stderr feedback on block (exit 2), nothing on allow (exit 0)
"""

import json
import os
import sys
from pathlib import Path

from shared.error_output import hook_error_json
from shared.handoff_example import format_handoff_example


def _scan_owned_tasks(
    teammate_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Scan task directory for in_progress tasks owned by this teammate.

    Single directory scan that partitions tasks into two categories:
    - completable: have HANDOFF metadata (work done, need to mark complete)
    - missing_handoff: no HANDOFF metadata (stuck or still working)

    Fails open on any I/O or parsing error (returns empty lists).

    Args:
        teammate_name: Name of the idle teammate
        team_name: Session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        Tuple of (completable, missing_handoff) — each a list of dicts
        with 'id' and 'subject'.
    """
    if not teammate_name or not team_name:
        return [], []

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    task_dir = Path(tasks_base_dir) / team_name
    if not task_dir.exists():
        return [], []

    completable = []
    missing_handoff = []

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

            task_id = task_file.stem  # filename without .json
            subject = data.get("subject", "unknown")
            metadata = data.get("metadata", {})

            # Semantic dispatch: branch on what the completion IS,
            # not who the agent IS (extensible to any signal-only agent)
            completion_type = metadata.get("completion_type", "handoff")
            entry = {
                "id": task_id,
                "subject": subject,
                "completion_type": completion_type,
            }

            if completion_type == "signal":
                # Signal-based completion: accept audit_summary as artifact
                if metadata.get("audit_summary"):
                    completable.append(entry)
                else:
                    missing_handoff.append(entry)
            elif metadata.get("handoff"):
                completable.append(entry)
            else:
                missing_handoff.append(entry)
    except OSError:
        return [], []  # Can't scan directory — fail open

    return completable, missing_handoff


def find_completable_tasks(
    teammate_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> list[dict]:
    """
    Find tasks owned by this teammate that have HANDOFF metadata but are
    still in_progress — meaning the work is done but the task wasn't completed.

    Args:
        teammate_name: Name of the idle teammate
        team_name: Session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        List of dicts with 'id' and 'subject' for each completable task
    """
    completable, _ = _scan_owned_tasks(teammate_name, team_name, tasks_base_dir)
    return completable


def find_missing_handoff_tasks(
    teammate_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> list[dict]:
    """
    Find tasks owned by this teammate that are in_progress but have NO handoff
    metadata at all — meaning the agent went idle without storing its HANDOFF.

    This is the safety net for the "agent loops on handoff gate" bug (#296).

    Args:
        teammate_name: Name of the idle teammate
        team_name: Session team name
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        List of dicts with 'id' and 'subject' for tasks missing handoff
    """
    _, missing = _scan_owned_tasks(teammate_name, team_name, tasks_base_dir)
    return missing


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


def format_missing_handoff_feedback(missing: list[dict]) -> str:
    """
    Format feedback for an idle agent whose tasks are missing completion artifacts.

    For standard handoff-type tasks, provides a concrete HANDOFF example.
    For signal-type tasks (e.g., auditor), references audit_summary instead.

    Args:
        missing: List of dicts with 'id', 'subject', and optionally
                 'completion_type' ("handoff" or "signal")

    Returns:
        Feedback message string for stderr
    """
    if len(missing) == 1:
        task = missing[0]
        task_ref = f"Task #{task['id']} ({task['subject']})"
    else:
        task_ref = ", ".join(f"#{t['id']} ({t['subject']})" for t in missing)

    # Partition by completion type for type-appropriate guidance
    signal_tasks = [
        t for t in missing if t.get("completion_type") == "signal"
    ]
    handoff_tasks = [
        t for t in missing if t.get("completion_type", "handoff") == "handoff"
    ]

    parts = []

    if signal_tasks:
        sig_ref = ", ".join(f"#{t['id']} ({t['subject']})" for t in signal_tasks)
        sig_id = signal_tasks[0]["id"]
        parts.append(
            f"Signal-type tasks missing audit_summary: {sig_ref}. "
            f"Store your audit summary via "
            f'TaskUpdate(taskId="{sig_id}", '
            f'metadata={{"audit_summary": {{"signal": "GREEN|YELLOW|RED", '
            f'"findings": [...]}}}}) then mark the task completed.'
        )

    if handoff_tasks:
        ho_ref = ", ".join(f"#{t['id']} ({t['subject']})" for t in handoff_tasks)
        ho_id = handoff_tasks[0]["id"]
        parts.append(
            f"Handoff-type tasks missing HANDOFF metadata: {ho_ref}. "
            f"You must store handoff metadata BEFORE marking the task completed.\n\n"
            + format_handoff_example(ho_id)
        )

    return (
        f"You went idle with in_progress tasks missing completion artifacts: "
        f"{task_ref}.\n\n" + "\n\n".join(parts)
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

        # Single scan — partitions tasks into completable vs missing handoff
        completable, missing = _scan_owned_tasks(teammate_name, team_name)

        if completable:
            print(format_feedback(completable), file=sys.stderr)
            sys.exit(2)  # Block idle — agent has tasks to complete

        # Safety net: catch agents that went idle without storing HANDOFF.
        # These agents were likely looping on handoff_gate rejection.
        if missing:
            print(format_missing_handoff_feedback(missing), file=sys.stderr)
            sys.exit(2)  # Block idle — agent needs to store HANDOFF first

        sys.exit(0)

    except Exception as e:
        # Fail open — never trap an agent in unrecoverable state
        print(f"Hook warning (teammate_completion_gate): {e}", file=sys.stderr)
        print(hook_error_json("teammate_completion_gate", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
