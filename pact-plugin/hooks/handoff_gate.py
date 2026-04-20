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
import sys

from shared.handoff_example import format_handoff_example
import shared.pact_context as pact_context
from shared.pact_context import get_team_name
from shared.session_journal import append_event, make_event
from shared.task_utils import _read_task_json, read_task_metadata, read_task_owner

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


def validate_variety_dimensions(
    task_metadata: dict,
    teammate_name: str | None,
) -> str | None:
    """
    Belt-and-suspenders check at task completion: verify that
    metadata.variety.total equals the sum of its dimensions (novelty,
    scope, uncertainty, risk). #401 Commit #6 defense-in-depth.

    An inconsistent score at completion indicates either (a) dimensions
    were mutated post-dispatch without updating total, or (b) total was
    written without proper per-dimension scoring (the issue-body's
    "hand-computed sum" failure mode). task_schema_validator.py rejects
    at CREATE time when dimensions are MISSING; this check catches
    sum-mismatch at COMPLETE time when dimensions exist but don't add up.

    Bypass conditions (mirror validate_task_handoff:48-58):
      - Non-agent task (teammate_name absent)
      - metadata.skipped truthy
      - Signal tasks (metadata.type in ("blocker", "algedonic"))
      - variety field absent (pre-#401 tasks and below-threshold tasks)
      - variety dimensions partially missing (validator handles at
        CREATE; at complete time partial shape means the orchestrator
        intentionally skipped full scoring — pass)

    Args:
        task_metadata: Task metadata dict (from task file).
        teammate_name: Name of completing teammate (None for non-agent).

    Returns:
        Error message on sum-mismatch, None otherwise. Fail-open on any
        malformed input (non-int dimensions, etc.).
    """
    if not teammate_name:
        return None
    if task_metadata.get("skipped"):
        return None
    if task_metadata.get("type") in ("blocker", "algedonic"):
        return None

    variety = task_metadata.get("variety")
    if not isinstance(variety, dict):
        return None  # not a variety-scored task

    try:
        total = variety.get("total")
        novelty = variety.get("novelty")
        scope = variety.get("scope")
        uncertainty = variety.get("uncertainty")
        risk = variety.get("risk")
        dims = (novelty, scope, uncertainty, risk)

        # Partial variety (any dim None or total None) -> pass. The schema
        # validator handles missing dims at CREATE time; at COMPLETE time
        # a partial shape means the orchestrator skipped full scoring
        # deliberately (below threshold, or pre-#401 task).
        if total is None or any(d is None for d in dims):
            return None

        # bool is int subclass — reject as invalid type (mirrors
        # task_schema_validator and session_journal bool-in-int traps).
        if isinstance(total, bool) or any(isinstance(d, bool) for d in dims):
            return None
        if not all(isinstance(x, int) for x in (total,) + dims):
            return None

        actual_sum = sum(dims)
        if total != actual_sum:
            return (
                f"Task completion blocked: variety score inconsistent. "
                f"metadata.variety.total={total} but sum of dimensions is "
                f"{actual_sum} (novelty={novelty}, scope={scope}, "
                f"uncertainty={uncertainty}, risk={risk}). "
                f"Fix via TaskUpdate(metadata={{\"variety\": {{\"total\": "
                f"{actual_sum}, \"novelty\": {novelty}, \"scope\": {scope}, "
                f"\"uncertainty\": {uncertainty}, \"risk\": {risk}}}}}) so "
                f"total matches the dimension sum."
            )
    except (TypeError, AttributeError):
        # Fail-open on any malformed variety shape
        return None

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

    # #401 Commit #6 defense-in-depth: verify variety.total matches its
    # dimension sum before completion. Bypasses same as validate_task_handoff.
    variety_error = validate_variety_dimensions(
        task_metadata=task_metadata,
        teammate_name=teammate_name,
    )
    if variety_error:
        print(variety_error, file=sys.stderr)
        sys.exit(2)

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

    # Signal-task carve-out: blocker/algedonic completions bypass handoff
    # validation (lines 56-58) and must NOT emit a phantom agent_handoff event.
    # Writing one would pollute `read_events("agent_handoff")` and mis-route
    # secretary harvest + memory_adhoc_reminder gating with empty handoff dicts.
    if task_metadata.get("type") in ("blocker", "algedonic"):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # All gates passed — write agent_handoff event to session journal (GC-proof).
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
