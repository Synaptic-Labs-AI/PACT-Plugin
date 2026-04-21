"""
Location: pact-plugin/hooks/shared/intentional_wait.py
Summary: Metadata schema and staleness predicate for the `intentional_wait`
         task-metadata flag. Used by both TeammateIdle hooks to distinguish
         protocol-defined waits from genuine stalls.
Used by: teammate_completion_gate.py, teammate_idle.py (detect_stall)

Contract: pure functions; never raises. `wait_stale` returns True (stale /
invalid) on any parse error so that a malformed flag fails open to the
normal nag path — a broken flag must not silently suppress stall detection.
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
