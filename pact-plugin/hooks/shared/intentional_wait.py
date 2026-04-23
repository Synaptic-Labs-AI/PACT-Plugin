"""
Location: pact-plugin/hooks/shared/intentional_wait.py
Summary: intentional_wait metadata schema + staleness check, plus legacy
         silencer predicates used by teammate_idle.py::detect_stall.
Used by: teammate_idle.py::detect_stall (scheduled for removal in #538 C3
         alongside is_signal_task + should_silence_stall_nag).

Contract: pure functions; never raise. Malformed flags fail loud — e.g.
`wait_stale` returns True on any parse error so a broken flag cannot
silently suppress stall detection.

Public predicates:
- is_signal_task(task): True iff task is a blocker/algedonic signal task.
- should_silence_stall_nag(task): True iff a TeammateIdle nag should be
  suppressed for this task (signal-task OR stalled OR fresh intentional_wait).
- wait_stale(wait_metadata): True iff an intentional_wait flag is stale.
- validate_wait(wait_metadata): True iff the flag is well-formed.
- canonical_since(): canonical ISO-8601 UTC timestamp for the `since` field.
"""

from datetime import datetime, timezone
from typing import Any


# Reason vocabulary — frozenset, not Enum, so teammates can include custom
# reasons when instrumentation hasn't caught up. validate_wait only checks
# non-empty string; reasons are for humans and audit logs.
KNOWN_REASONS = frozenset({
    "awaiting_teachback_approved",
    "awaiting_lead_commit",
    "awaiting_amendment_review",
    "awaiting_post_handoff_decision",
    "awaiting_peer_response",
    "awaiting_user_decision",
    "awaiting_blocker_resolution",
})

KNOWN_RESOLVERS = frozenset({"lead", "peer", "user", "external"})

DEFAULT_THRESHOLD_MINUTES = 30


def canonical_since() -> str:
    """
    Return the canonical ISO-8601 UTC `since` timestamp used by auto-set
    sites and the manual-set convention.

    Round-trip guarantee: the return value parses cleanly via the same
    `fromisoformat(s.replace("Z", "+00:00"))` path validate_wait uses.
    timespec="seconds" drops microseconds for stable equality across
    cross-system timestamps and avoids platform-specific formatting drift.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_wait(wait_metadata: Any) -> bool:
    """
    Return True iff wait_metadata is a well-formed intentional_wait dict.

    Required keys: reason (non-empty str), expected_resolver (non-empty str —
    KNOWN_RESOLVERS preferred but any non-empty string accepted), since
    (tz-aware ISO-8601 timestamp parseable by datetime.fromisoformat, with
    a trailing 'Z' accepted as UTC).

    Unknown keys are preserved (forward-compat).
    """
    if not isinstance(wait_metadata, dict):
        return False

    reason = wait_metadata.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False

    resolver = wait_metadata.get("expected_resolver")
    if not isinstance(resolver, str) or not resolver.strip():
        return False

    since = wait_metadata.get("since")
    if not isinstance(since, str):
        return False

    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False

    # Reject tz-naive timestamps. A naive timestamp from a non-UTC teammate
    # could be hours off real UTC, producing silently wrong age computations.
    # Instrumentation always emits tz-aware via canonical_since(), so a naive
    # value is always a teammate bug — surface it fast rather than paper over.
    if parsed.tzinfo is None:
        return False

    return True


def wait_stale(
    wait_metadata: Any,
    threshold_minutes: int = DEFAULT_THRESHOLD_MINUTES,
    _now: datetime | None = None,
) -> bool:
    """
    Return True iff the intentional_wait flag is stale (nag should re-enable).

    Stale when:
    - wait_metadata fails validation (malformed flag → fail loud to nag), OR
    - elapsed time since `since` is >= threshold_minutes.

    Future-dated `since` (clock drift or tampering) yields a negative age
    and is treated as NOT stale — conservative: don't nag a teammate whose
    clock skewed forward. The threshold still re-enables the nag once
    wall-clock catches up.

    _now is for deterministic unit tests; production callers omit it.
    """
    if not validate_wait(wait_metadata):
        return True

    since = datetime.fromisoformat(wait_metadata["since"].replace("Z", "+00:00"))
    now = _now or datetime.now(timezone.utc)
    age_minutes = (now - since).total_seconds() / 60
    return age_minutes >= threshold_minutes


# Signal-task types. Single source of truth — the literal tuple must not
# appear elsewhere in pact-plugin/hooks/ (enforced by
# test_intentional_wait.py::test_signal_task_literal_lives_in_helper_only).
_SIGNAL_TASK_TYPES = ("blocker", "algedonic")


def is_signal_task(task: Any) -> bool:
    """
    Return True iff the task is a blocker/algedonic signal task.

    Signal tasks silence the TeammateIdle nag (detect_stall). Pure
    predicate on metadata.type — does NOT consider `stalled` or
    `intentional_wait`. Scheduled for removal in #538 C3 when detect_stall
    is deleted; the agent_handoff_emitter replacement uses the inline
    literal directly (matches task_utils.py:184 + session_resume.py:525).

    Accepts any input and returns False on non-dict / missing metadata —
    never raises.
    """
    if not isinstance(task, dict):
        return False
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("type") in _SIGNAL_TASK_TYPES


def should_silence_stall_nag(
    task: Any,
    threshold_minutes: int = DEFAULT_THRESHOLD_MINUTES,
    _now: datetime | None = None,
) -> bool:
    """
    Return True iff a TeammateIdle nag should be suppressed for this task.

    Silenced when any of:
    - task is a blocker/algedonic signal (is_signal_task), OR
    - metadata.stalled is truthy (already-marked stall — don't re-alert), OR
    - metadata.intentional_wait is set AND not stale (fresh protocol-defined wait).

    Used by teammate_idle.py::detect_stall (scheduled for removal in
    #538 C3, at which point this helper becomes dead code and is deleted).

    Accepts any input and returns False on non-dict / missing metadata —
    never raises.
    """
    if is_signal_task(task):
        return True
    # is_signal_task validated isinstance(task, dict) and dict-metadata —
    # safe to re-access.
    if not isinstance(task, dict):
        return False
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("stalled"):
        return True
    wait = metadata.get("intentional_wait")
    if wait is not None and not wait_stale(wait, threshold_minutes, _now):
        return True
    return False
