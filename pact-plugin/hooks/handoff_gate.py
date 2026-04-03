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
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.handoff_example import format_handoff_example
import shared.pact_context as pact_context
from shared.pact_context import get_team_name

# reasoning_chain (item 3) intentionally excluded — optional per CT Phase 1
REQUIRED_HANDOFF_FIELDS = ["produced", "decisions", "uncertainty", "integration", "open_questions"]

BYPASS_SUBJECT_PREFIXES = ("BLOCKER:", "HALT:", "ALERT:")

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


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
            "\"open_questions\": [...]}}) BEFORE marking task completed.\n\n"
            + format_handoff_example()
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


# Note: The secretary processes HANDOFFs sequentially ("read all before saving")
# for deduplication. This serializes writes but produces cleaner entries.
# Acceptable at current scale (2-5 HANDOFFs per workflow).
def check_memory_saved(
    task_metadata: dict,
    teammate_name: str | None,
) -> str | None:
    """
    Check if agent saved domain learnings to persistent memory.

    Returns a blocking feedback message if memory_saved is absent or false,
    or None if no action is needed. When returned, the caller should
    exit 2 to block task completion — the message feeds back to the agent.

    Args:
        task_metadata: Task metadata dict (from task file)
        teammate_name: Name of completing teammate (None for non-agent)

    Returns:
        Feedback message string if memory_saved is missing/false, None otherwise
    """
    # Skip: non-agent tasks
    if not teammate_name:
        return None

    # Skip: no handoff means validate_task_handoff already blocked or bypassed
    handoff = task_metadata.get("handoff")
    if not handoff:
        return None

    # Skip: already saved
    if task_metadata.get("memory_saved"):
        return None

    return (
        f"Save domain learnings to persistent memory (~/.claude/agent-memory/{teammate_name}/). "
        f"Save codepaths, patterns, and conventions discovered during this task. "
        f"If you have nothing new to save, that's OK — just set the flag. "
        f"Then set memory_saved: true via TaskUpdate(taskId, metadata={{\"memory_saved\": true}})."
    )


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


def append_pending_handoff(
    task_id: str,
    teammate_name: str,
    team_name: str,
    task_metadata: dict | None = None,
    task_subject: str = "",
) -> None:
    """
    Append a breadcrumb to the pending handoffs file for secretary consumption.

    Writes a single JSONL line to ~/.claude/teams/{team_name}/completed_handoffs.jsonl
    so the secretary can discover completed tasks without the orchestrator needing
    to enumerate task IDs. Uses POSIX atomic append (O_WRONLY|O_APPEND|O_CREAT) with
    0o600 permissions for concurrent safety and security.

    When task_metadata is provided, the entry is enriched with the full HANDOFF
    content and task_subject. This makes the breadcrumb GC-proof — the secretary
    can read HANDOFFs directly from the breadcrumb without needing TaskGet (which
    fails for GC'd tasks). Old-format entries (without handoff) remain valid;
    the secretary falls back to TaskGet for those.

    Dedup guard: reads the file before appending and skips if task_id is already
    present. This prevents cascade duplicates when TaskCompleted fires for multiple
    tasks owned by the same agent. Fails open — if the read fails, appends anyway.

    Fails silently — breadcrumb loss is acceptable; blocking task completion is not.
    """
    if not teammate_name or not team_name:
        return
    teams_dir = Path.home() / ".claude" / "teams" / team_name
    if not teams_dir.exists():
        return
    filepath = teams_dir / "completed_handoffs.jsonl"

    # Dedup guard: check if task_id already recorded (fail-open on read error).
    # Uses POSIX fd read for consistency with the append path below.
    try:
        rfd = os.open(str(filepath), os.O_RDONLY)
        try:
            size = os.fstat(rfd).st_size
            content = os.read(rfd, size).decode("utf-8") if size > 0 else ""
        finally:
            os.close(rfd)
        for line in content.strip().splitlines():
            if line.strip():
                try:
                    if json.loads(line).get("task_id") == task_id:
                        return  # Already captured — skip
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass  # Can't read or file missing? Proceed with append (fail-open)

    try:
        entry_dict: dict[str, Any] = {
            "task_id": task_id,
            "teammate_name": teammate_name,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        # Enrich with HANDOFF content when available (GC-proof breadcrumb).
        # task_subject is nested inside `if task_metadata` intentionally:
        # when metadata is None/empty, we produce a legacy-format entry with no
        # extra fields. task_subject is only useful alongside metadata context.
        # "v": 1 marks enriched format for forward compat (session-journal #342).
        if task_metadata:
            entry_dict["v"] = 1
            handoff = task_metadata.get("handoff")
            if handoff:
                entry_dict["handoff"] = handoff
            if task_subject:
                entry_dict["task_subject"] = task_subject
        entry = json.dumps(entry_dict) + "\n"
        fd = os.open(str(filepath), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, entry.encode())
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        pass  # Fail-open: TypeError/ValueError from json.dumps on non-serializable data


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    task_id = input_data.get("task_id", "")
    task_subject = input_data.get("task_subject", "")
    teammate_name = input_data.get("teammate_name")
    team_name = (input_data.get("team_name") or get_team_name()).lower()

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

    # Blocking enforcement: agent must acknowledge memory save before completing.
    # Exit 2 blocks task completion and feeds stderr back to the agent as
    # actionable feedback. The agent must set memory_saved: true before it
    # can complete.
    memory_feedback = check_memory_saved(
        task_metadata=task_metadata,
        teammate_name=teammate_name,
    )
    if memory_feedback:
        print(memory_feedback, file=sys.stderr)
        sys.exit(2)  # Block completion — feedback goes to agent

    # Both gates passed — append breadcrumb for secretary consumption.
    # This is the LAST action before exit: every breadcrumb = fully complete task.
    append_pending_handoff(
        task_id, teammate_name, team_name,
        task_metadata=task_metadata,
        task_subject=task_subject,
    )

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
