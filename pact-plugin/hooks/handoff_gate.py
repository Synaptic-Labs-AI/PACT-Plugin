#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/handoff_gate.py
Summary: TaskCompleted hook that blocks task completion if handoff metadata
         is missing or incomplete. Exit code 2 prevents completion.
Used by: hooks.json TaskCompleted hook

This is the highest-leverage hook in the SDK leverage design — by ensuring
upstream tasks always have proper metadata, downstream chain-reads via
TaskGet are guaranteed to find data.

Input: JSON from stdin with task_id, task_subject, task_description,
       teammate_name, team_name
Output: stderr message on block (exit 2), nothing on allow (exit 0)
"""

import json
import re
import sys
import os
from pathlib import Path

REQUIRED_HANDOFF_FIELDS = ["produced", "decisions", "uncertainty", "integration", "open_questions"]

BYPASS_SUBJECT_PREFIXES = ("BLOCKER:", "HALT:", "ALERT:")


def validate_task_handoff(
    task_subject: str,
    task_metadata: dict,
    teammate_name: str | None,
) -> str | None:
    """
    Validate that a task has complete handoff metadata.

    Args:
        task_subject: Task subject line
        task_metadata: Task metadata dict (from task file)
        teammate_name: Name of completing teammate (None for non-agent)

    Returns:
        Error message if validation fails, None if OK
    """
    # Bypass: non-agent task completion
    if not teammate_name:
        return None

    # Bypass: skipped tasks
    if task_metadata.get("skipped"):
        return None

    # Bypass: signal tasks (blocker, algedonic)
    if task_metadata.get("type") in ("blocker", "algedonic"):
        return None

    # Bypass: subject-based signal tasks (legacy format)
    if task_subject and any(task_subject.startswith(p) for p in BYPASS_SUBJECT_PREFIXES):
        return None

    # Check: handoff exists
    handoff = task_metadata.get("handoff")
    if not handoff:
        return (
            "Task completion blocked: missing handoff metadata. "
            "Store your HANDOFF via TaskUpdate(metadata={\"handoff\": {\"produced\": [...], "
            "\"decisions\": [...], \"uncertainty\": [...], \"integration\": [...], "
            "\"open_questions\": [...]}}) before marking task completed."
        )

    # Check: all required fields present
    missing = [f for f in REQUIRED_HANDOFF_FIELDS if f not in handoff]
    if missing:
        return (
            f"Task completion blocked: handoff metadata missing fields: {', '.join(missing)}. "
            f"Update via TaskUpdate(metadata={{\"handoff\": {{...}}}}) with all 5 required fields."
        )

    # Check: produced is non-empty
    if not handoff.get("produced"):
        return (
            "Task completion blocked: handoff 'produced' list is empty. "
            "List the files you created or modified before completing."
        )

    return None


def read_task_metadata(task_id: str, team_name: str | None, tasks_base_dir: str | None = None) -> dict:
    """
    Read task metadata from the task file.

    Args:
        task_id: Task identifier
        team_name: Team name for scoped task lookup
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        Task metadata dict, or empty dict if not found
    """
    if not task_id:
        return {}

    # Sanitize task_id to prevent path traversal
    task_id = re.sub(r'[/\\]|\.\.', '', task_id)
    if not task_id:
        return {}

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    base = Path(tasks_base_dir)

    # Try team task directory first, then default
    task_dirs = []
    if team_name:
        task_dirs.append(base / team_name)
    task_dirs.append(base)

    for task_dir in task_dirs:
        task_file = task_dir / f"{task_id}.json"
        if task_file.exists():
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
                return data.get("metadata", {})
            except (json.JSONDecodeError, IOError):
                return {}

    return {}


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    task_id = input_data.get("task_id", "")
    task_subject = input_data.get("task_subject", "")
    teammate_name = input_data.get("teammate_name")
    team_name = (input_data.get("team_name") or os.environ.get("CLAUDE_CODE_TEAM_NAME", "")).lower()

    # TaskCompleted input doesn't include metadata — read from task file
    task_metadata = read_task_metadata(task_id, team_name)

    error = validate_task_handoff(
        task_subject=task_subject,
        task_metadata=task_metadata,
        teammate_name=teammate_name,
    )

    if error:
        print(error, file=sys.stderr)
        sys.exit(2)  # Exit 2 = block completion

    sys.exit(0)


if __name__ == "__main__":
    main()
