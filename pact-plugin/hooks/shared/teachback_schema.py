"""
Location: pact-plugin/hooks/shared/teachback_schema.py
Summary: Canonical teachback_submit schema constants and validators. SSOT
         for the 5-field shape (D10), the variety_acknowledgment object
         schema (D10), the reasoning_reconstruction 3-sub-key triangle
         (L1.5 method gate per pact-ct-teachback.md), and the variety-band
         threshold at which reasoning_reconstruction is REQUIRED.
Used by: hooks/task_lifecycle_gate.py (write-time + completion-time gates),
         hooks/precompact_state_reminder.py (resolve_variety_total for the
         compaction-state render),
         tests/test_teachback_reasoning_reconstruction.py (schema gate),
         tests/test_task_lifecycle_gate.py (rule-fixture inputs),
         tests/test_teachback_schema.py (constants + validator).

When the 5-field shape, 3-sub-key triangle, or threshold changes, this is
the only Python edit site. Prose in skills/pact-teachback/SKILL.md mirrors
these values via grep-at-edit-time alignment.

Contract: pure module; no I/O, no global state, no platform dependencies.
Functions never raise.

Public surface:
- TEACHBACK_REQUIRED_FIELDS — canonical 5-tuple per D10 (4 string fields +
  variety_acknowledgment dict).
- TEACHBACK_REQUIRED_SUBKEYS — canonical 3-tuple for reasoning_reconstruction
  per pact-ct-teachback.md §When to Method-Reconstruct.
- TEACHBACK_VARIETY_ACK_VALID_VALUES — enum tuple for
  variety_acknowledgment.rationale_articulates_this_dispatch.
- TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN — variety band threshold
  (>= 11 → REQUIRED). Bound at module-load to `variety_scorer.PLAN_MODE_MIN`
  (the plan-mode band floor).
- TEACHBACK_RECOMMENDED_BAND_MIN — variety band threshold
  (>= 7 → RECOMMENDED, below it → SKIPPED). Derived from
  `variety_scorer.COMPACT_MAX + 1`.
- TEACHBACK_SCHEMA_ECHO — reusable human-readable echo of the canonical
  5-field schema (derived from TEACHBACK_REQUIRED_FIELDS), appended to the
  schema-invalid deny message so a teammate that trips the gate self-corrects
  in one read.
- validate_reasoning_reconstruction(rr) — pure validator returning None
  on well-formed input or a reason-enum string on rejection.
- resolve_variety_total(variety, metadata=None) — pure resolver returning
  the per-dispatch variety total as an in-range int, or None when no
  candidate resolves. Ordered precedence: variety["total"] →
  variety["score"] → metadata["variety_score"] → sum of the four
  dimension scores. Shared SSOT for the lifecycle gate's read-time band
  resolver and write-time validator (and precompact's render).
"""

from __future__ import annotations

from shared.variety_scorer import (
    COMPACT_MAX,
    MAX_DIMENSION,
    MAX_SCORE,
    MIN_DIMENSION,
    MIN_SCORE,
    PLAN_MODE_MIN,
)


# Canonical 5 field names per D10. 4 string fields + variety_acknowledgment dict.
TEACHBACK_REQUIRED_FIELDS: tuple[str, ...] = (
    "understanding",
    "most_likely_wrong",
    "least_confident_item",
    "first_action",
    "variety_acknowledgment",
)

# Canonical 3 sub-keys for reasoning_reconstruction per pact-ct-teachback.md
# §When to Method-Reconstruct. Each value is a non-empty string at submit time.
TEACHBACK_REQUIRED_SUBKEYS: tuple[str, ...] = (
    "decision_attribution",
    "assumption_trace",
    "contingency_clause",
)

# Allowed enum values for variety_acknowledgment.rationale_articulates_this_dispatch
# per D10. Object schema:
#   {rationale_articulates_this_dispatch: <enum>, concern: <str when != yes>}.
TEACHBACK_VARIETY_ACK_VALID_VALUES: tuple[str, ...] = ("yes", "no", "concern")

# REQUIRED-band threshold for reasoning_reconstruction: reasoning_reconstruction
# is REQUIRED at plan-mode-and-above (>= 11). Bound to variety_scorer's
# PLAN_MODE_MIN (the plan-mode band floor) so the threshold tracks the SSOT
# band cuts directly instead of an off-by-one expression.
TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN: int = PLAN_MODE_MIN

# RECOMMENDED-band floor for reasoning_reconstruction. Totals strictly above
# COMPACT_MAX (i.e. >= COMPACT_MAX + 1 = 7) and below the REQUIRED threshold
# are the recommended-not-required band; totals <= COMPACT_MAX are skipped.
# Named here (symmetric with the REQUIRED threshold above) so the lifecycle
# gate's band mapping references a constant instead of hard-coding 7, while
# keeping the gate's reach to variety_scorer strictly 2-hop via this module.
TEACHBACK_RECOMMENDED_BAND_MIN: int = COMPACT_MAX + 1

# Human-readable echo of the full canonical teachback_submit schema, derived
# from TEACHBACK_REQUIRED_FIELDS so the remediation text can never drift from
# the tuple. The 4 string fields are listed in canonical order, then
# variety_acknowledgment with the is-an-OBJECT note (the most common wrong
# shape is a free-text string). Reused by the schema-invalid deny message in
# task_lifecycle_gate.py so a teammate that trips the gate self-corrects in
# one read without opening the skill.
TEACHBACK_SCHEMA_ECHO: str = (
    "Expected canonical teachback_submit schema (all 5 fields): "
    + ", ".join(
        field
        for field in TEACHBACK_REQUIRED_FIELDS
        if field != "variety_acknowledgment"
    )
    + " (non-empty strings) + variety_acknowledgment (an OBJECT, not a string)."
    " See the pact-teachback skill for field semantics."
)


def validate_reasoning_reconstruction(rr: object) -> str | None:
    """Return None if rr is a well-formed 3-sub-key triangle, or a short
    rejection-reason enum string.

    Schema:
      - dict with exactly TEACHBACK_REQUIRED_SUBKEYS as keys
      - each value: non-empty string (after .strip())

    Returns the reason string for the lead-side rejection enum mapping:
      - "malformed_reasoning_reconstruction" → not-dict / wrong-keys
      - "empty_reasoning_reconstruction_field" → empty/non-string sub-key

    Pure function; never raises. Non-dict input returns the malformed
    reason rather than raising TypeError — callers can treat the return
    value as the load-bearing signal.
    """
    if not isinstance(rr, dict):
        return "malformed_reasoning_reconstruction"
    if set(rr.keys()) != set(TEACHBACK_REQUIRED_SUBKEYS):
        return "malformed_reasoning_reconstruction"
    for key in TEACHBACK_REQUIRED_SUBKEYS:
        value = rr[key]
        if not isinstance(value, str) or not value.strip():
            return "empty_reasoning_reconstruction_field"
    return None


# The four per-dimension keys whose sum is the definitional variety total
# (see variety_scorer.score_variety). Used by the candidate-4 dimension-sum
# fallback in resolve_variety_total.
_VARIETY_DIMENSIONS: tuple[str, ...] = (
    "novelty",
    "scope",
    "uncertainty",
    "risk",
)

# Canonical key set for a per-dispatch variety stamp PROJECTED to the journal
# (pact-variety.md §5.1: the 4 dimensions + their total — the rationale strings
# are NOT mirrored). Derived from _VARIETY_DIMENSIONS so the dimension names are
# never duplicated: a future dimension rename/add edits ONE list and both the
# resolve-fallback above and the dispatch_variety projection in
# task_lifecycle_gate follow. Read by the #955 dispatch_variety emit.
DISPATCH_VARIETY_KEYS: tuple[str, ...] = _VARIETY_DIMENSIONS + ("total",)


def _is_in_range_int(value: object, low: int, high: int) -> bool:
    """True iff value is a non-bool int within the inclusive [low, high]
    range. bool is rejected because it subclasses int (True→1, False→0)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and low <= value <= high
    )


def resolve_variety_total(variety: object, metadata: object = None) -> int | None:
    """Resolve the per-dispatch variety total from a (possibly non-canonical
    or malformed) variety stamp.

    Precedence (first valid in-range int wins):
      1. variety["total"]          — canonical
      2. variety["score"]          — non-canonical, field-evidenced
      3. metadata["variety_score"] — non-canonical sibling, field-evidenced
      4. sum(variety[d] for d in the 4 dimensions) when all four are valid
         in-range dimension ints (1..4 each)

    Returns an int in [MIN_SCORE, MAX_SCORE] (4..16), or None when no
    candidate yields a resolvable in-range total.

    Pure; NEVER raises. Non-dict / missing / malformed input → None.
    bool is rejected at every key (bool subclasses int). A present-but-
    out-of-range or wrong-typed candidate does NOT halt the chain — the
    resolver falls through to the next candidate, so a junk canonical
    total cannot shadow a recoverable fallback.
    """
    # Belt-and-suspenders: the body below is structurally non-raising, but
    # the outermost guard ensures no exotic input can ever escape as an
    # exception — this hook fires on every Task-tool use.
    try:
        if not isinstance(variety, dict):
            return None

        # Candidate 1: canonical total.
        if _is_in_range_int(variety.get("total"), MIN_SCORE, MAX_SCORE):
            return variety["total"]

        # Candidate 2: non-canonical score (inside the variety dict).
        if _is_in_range_int(variety.get("score"), MIN_SCORE, MAX_SCORE):
            return variety["score"]

        # Candidate 3: non-canonical top-level sibling (metadata.variety_score).
        # Skipped when metadata is absent or not a dict.
        if isinstance(metadata, dict) and _is_in_range_int(
            metadata.get("variety_score"), MIN_SCORE, MAX_SCORE
        ):
            return metadata["variety_score"]

        # Candidate 4: dimension-sum — only when ALL four dimensions are
        # valid in-range ints. In-range by construction (4×1..4×4 = 4..16).
        if all(
            _is_in_range_int(variety.get(d), MIN_DIMENSION, MAX_DIMENSION)
            for d in _VARIETY_DIMENSIONS
        ):
            return sum(variety[d] for d in _VARIETY_DIMENSIONS)

        return None
    except Exception:  # noqa: BLE001 — never raise out of an every-Task hook
        return None
