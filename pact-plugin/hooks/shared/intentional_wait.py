"""
Location: pact-plugin/hooks/shared/intentional_wait.py
Summary: `intentional_wait` metadata schema — the teammate-facing contract
         for signalling a legitimate wait on an in_progress task (teachback
         approval, inter-commit hold, peer reply, etc.).
Used by: teammate-authored metadata on Task records. No hook reads this
         schema post-#538; the schema survives as the teammate-facing
         contract documented in skills/pact-agent-teams/SKILL.md.

Contract: pure functions; never raise. Malformed flags fail loud — e.g.
`wait_stale` returns True on any parse error so a broken flag cannot
silently be interpreted as a fresh wait.

Public surface:
- KNOWN_REASONS / KNOWN_RESOLVERS — vocabulary hints (free-form strings
  still accepted by validate_wait).
- DEFAULT_THRESHOLD_MINUTES — staleness horizon (30 min).
- canonical_since() — ISO-8601 UTC timestamp helper for the `since` field.
- validate_wait(wait_metadata) — True iff the flag is well-formed.
- wait_stale(wait_metadata) — True iff the flag has aged past threshold.

Removed in #538 C3 (with detect_stall in teammate_idle.py):
- is_signal_task(task) — agent_handoff_emitter uses the inline literal
  `metadata.type in ("blocker", "algedonic")` (matches task_utils.py:184
  and session_resume.py:525 convention).
- should_silence_stall_nag(task) — stall-nag removed entirely.
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
    Return True iff the intentional_wait flag is stale (wait should be
    treated as expired; fresh SET needed to keep the semantic).

    Stale when:
    - wait_metadata fails validation (malformed flag → fail loud), OR
    - elapsed time since `since` is >= threshold_minutes.

    Future-dated `since` (clock drift or tampering) yields a negative age
    and is treated as NOT stale — conservative: don't treat a teammate
    whose clock skewed forward as stale. The threshold still re-triggers
    once wall-clock catches up.

    _now is for deterministic unit tests; production callers omit it.
    """
    if not validate_wait(wait_metadata):
        return True

    since = datetime.fromisoformat(wait_metadata["since"].replace("Z", "+00:00"))
    now = _now or datetime.now(timezone.utc)
    age_minutes = (now - since).total_seconds() / 60
    return age_minutes >= threshold_minutes
