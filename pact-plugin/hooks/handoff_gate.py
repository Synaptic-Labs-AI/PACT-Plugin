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
from pathlib import Path

from shared.handoff_example import format_handoff_example
import shared.pact_context as pact_context
from shared.pact_context import get_team_name
from shared.session_journal import append_event, make_event

# reasoning_chain (item 3) intentionally excluded — optional per CT Phase 1
REQUIRED_HANDOFF_FIELDS = ["produced", "decisions", "uncertainty", "integration", "open_questions"]

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def validate_task_handoff(
    task_metadata: dict,
    teammate_name: str | None,
) -> str | None:
    """
    Validate that a task has complete handoff metadata.

    Args:
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


def _read_task_json(task_id: str, team_name: str | None, tasks_base_dir: str | None = None) -> dict:
    """
    Read the raw task JSON from disk.

    Shared logic for read_task_metadata() and read_task_owner(). Locates the
    task file in the team directory first, then falls back to the base directory.

    Args:
        task_id: Task identifier
        team_name: Team name for scoped task lookup
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        Full task dict from the JSON file, or empty dict if not found
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
                return json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return {}

    return {}


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
    return _read_task_json(task_id, team_name, tasks_base_dir).get("metadata", {})


def read_task_owner(task_id: str, team_name: str | None, tasks_base_dir: str | None = None) -> str | None:
    """
    Read the task owner from the task file.

    Used as a fallback when the platform doesn't provide teammate_name in hook
    input (e.g., orchestrator marks a task completed on behalf of an agent).

    Args:
        task_id: Task identifier
        team_name: Team name for scoped task lookup
        tasks_base_dir: Override for tasks base directory (for testing)

    Returns:
        Owner string if present, None otherwise
    """
    return _read_task_json(task_id, team_name, tasks_base_dir).get("owner")



def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    # Defensive substitution: the RA1+RG2 schema validator (commit 2d6448c)
    # rejects empty strings for str-typed required fields. If the platform
    # ever omits task_id/task_subject from the TaskCompleted payload, the
    # downstream agent_handoff event would be silently dropped by
    # append_event() (which is fail-open). Substitute fallback values and
    # emit a stderr warning so the substitution is visible — preserving the
    # HANDOFF event is more important than rejecting it on missing metadata.
    raw_task_id = input_data.get("task_id")
    raw_task_subject = input_data.get("task_subject")
    task_id_was_missing = not raw_task_id
    task_subject_was_missing = not raw_task_subject
    task_id = raw_task_id or "unknown"
    task_subject = raw_task_subject or "(no subject)"
    if task_id_was_missing or task_subject_was_missing:
        print(
            f"handoff_gate: missing required field(s) in TaskCompleted payload "
            f"(task_id={'MISSING' if task_id_was_missing else 'present'}, "
            f"task_subject={'MISSING' if task_subject_was_missing else 'present'}); "
            f"using fallback values to preserve agent_handoff event",
            file=sys.stderr,
        )
    team_name = (input_data.get("team_name") or get_team_name()).lower()

    # Read task file once — used for both owner resolution and metadata.
    task_data = _read_task_json(task_id, team_name)

    # Owner field (set during dispatch) is the authoritative teammate identity.
    # Platform-provided teammate_name is fallback for tasks without an owner.
    teammate_name = task_data.get("owner") or input_data.get("teammate_name")

    # No teammate after both sources → genuine non-agent completion.
    if not teammate_name:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    task_metadata = task_data.get("metadata", {})

    error = validate_task_handoff(
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

    # Both gates passed — write agent_handoff event to session journal (GC-proof).
    # This is the sole HANDOFF persistence path. The secretary reads HANDOFFs from
    # journal events via read_events("agent_handoff").
    append_event(
        make_event(
            "agent_handoff",
            agent=teammate_name,
            task_id=task_id,
            task_subject=task_subject,
            handoff=task_metadata.get("handoff", {}),
        ),
    )

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
