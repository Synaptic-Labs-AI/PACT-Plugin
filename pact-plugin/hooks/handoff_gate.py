#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/handoff_gate.py
Summary: TaskCompleted hook that blocks task completion if handoff metadata
         is missing or incomplete, and warns if memory_used metadata is absent.
         Exit code 2 prevents completion; warnings go to stderr without blocking.
Used by: hooks.json TaskCompleted hook

This is the highest-leverage hook in the SDK leverage design — by ensuring
upstream tasks always have proper metadata, downstream chain-reads via
TaskGet are guaranteed to find data. Additionally, it verifies that PACT
work agents saved to pact-memory as part of the Memory Lifecycle Protocol.

Input: JSON from stdin with task_id, task_subject, task_description,
       teammate_name, team_name
Output: stderr message on block (exit 2), warnings on stderr (exit 0)
"""

import json
import re
import sys
import os
from pathlib import Path

# reasoning_chain (item 3) intentionally excluded — optional per CT Phase 1
REQUIRED_HANDOFF_FIELDS = ["produced", "decisions", "uncertainty", "integration", "open_questions"]

BYPASS_SUBJECT_PREFIXES = ("BLOCKER:", "HALT:", "ALERT:")

# PACT agents that do work requiring memory saves (mirrors memory_enforce.py)
# Explicitly excludes pact-memory-agent to avoid recursion
PACT_WORK_AGENTS = [
    "pact-preparer",
    "pact-architect",
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-n8n",
    "pact-test-engineer",
    "pact-security-engineer",
    "pact-qa-engineer",
]


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
            f"Update via TaskUpdate(metadata={{\"handoff\": {{...}}}}) with all required fields."
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


def is_pact_work_agent(teammate_name: str) -> bool:
    """Check if this teammate is a PACT agent that requires memory saves."""
    if not teammate_name:
        return False
    name_lower = teammate_name.lower()
    return any(agent in name_lower for agent in PACT_WORK_AGENTS)


def check_memory_metadata(
    task_metadata: dict,
    teammate_name: str | None,
) -> str | None:
    """
    Check if a PACT work agent included memory_used metadata.

    This is a non-blocking warning — task completion is NOT prevented,
    but the orchestrator is alerted that memory was not saved.

    Args:
        task_metadata: Task metadata dict
        teammate_name: Name of completing teammate

    Returns:
        Warning message if memory_used is missing, None if OK or not applicable
    """
    if not teammate_name or not is_pact_work_agent(teammate_name):
        return None

    # Skip signal/bypass tasks (same conditions as handoff validation)
    if task_metadata.get("skipped"):
        return None
    if task_metadata.get("type") in ("blocker", "algedonic"):
        return None

    memory_used = task_metadata.get("memory_used", False)
    if not memory_used:
        return (
            "⚠️ MEMORY NOT SAVED: Agent completed without saving to pact-memory. "
            "This agent should have included memory_used: true in task metadata. "
            "Consider requesting the agent save its learnings before proceeding."
        )

    return None


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

    # Non-blocking: warn if memory_used metadata is missing
    memory_warning = check_memory_metadata(
        task_metadata=task_metadata,
        teammate_name=teammate_name,
    )
    if memory_warning:
        print(memory_warning, file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
