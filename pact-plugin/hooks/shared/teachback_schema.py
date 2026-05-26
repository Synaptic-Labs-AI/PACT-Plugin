"""
Location: pact-plugin/hooks/shared/teachback_schema.py
Summary: Canonical teachback_submit schema constants and validators. SSOT
         for the 5-field shape (D10), the variety_acknowledgment object
         schema (D10), the reasoning_reconstruction 3-sub-key triangle
         (L1.5 method gate per pact-ct-teachback.md), and the variety-band
         threshold at which reasoning_reconstruction is REQUIRED.
Used by: hooks/task_lifecycle_gate.py (write-time + completion-time gates),
         tests/test_teachback_reasoning_reconstruction.py (schema gate),
         tests/test_task_lifecycle_gate.py (rule-fixture inputs),
         tests/test_teachback_schema.py (constants + validator).

When the 5-field shape, 3-sub-key triangle, or threshold changes, this is
the only Python edit site. Prose in skills/pact-teachback/SKILL.md mirrors
these values via grep-at-edit-time alignment.

Public surface:
- REQUIRED_FIELDS — canonical 5-tuple per D10 (4 string fields +
  variety_acknowledgment dict).
- REQUIRED_SUBKEYS — canonical 3-tuple for reasoning_reconstruction per
  pact-ct-teachback.md §When to Method-Reconstruct.
- VARIETY_ACK_VALID_VALUES — enum tuple for
  variety_acknowledgment.rationale_articulates_this_dispatch.
- REASONING_RECONSTRUCTION_REQUIRED_MIN — variety band threshold (>= 11
  → REQUIRED). Sourced from variety_scorer.PLAN_MODE_MAX + 1 semantics;
  align via grep-at-edit-time until variety_scorer.py exports an explicit
  PLAN_MODE_MIN.
- validate_reasoning_reconstruction(rr) — pure validator returning None
  on well-formed input or a reason-enum string on rejection.

Contract: pure module; no I/O, no global state, no platform dependencies.
Functions never raise.
"""

from __future__ import annotations


# Canonical 5 field names per D10. 4 string fields + variety_acknowledgment dict.
REQUIRED_FIELDS: tuple[str, ...] = (
    "understanding",
    "most_likely_wrong",
    "least_confident_item",
    "first_action",
    "variety_acknowledgment",
)

# Canonical 3 sub-keys for reasoning_reconstruction per pact-ct-teachback.md
# §When to Method-Reconstruct. Each value is a non-empty string at submit time.
REQUIRED_SUBKEYS: tuple[str, ...] = (
    "decision_attribution",
    "assumption_trace",
    "contingency_clause",
)

# Allowed enum values for variety_acknowledgment.rationale_articulates_this_dispatch
# per D10. Object schema:
#   {rationale_articulates_this_dispatch: <enum>, concern: <str when != yes>}.
VARIETY_ACK_VALID_VALUES: tuple[str, ...] = ("yes", "no", "concern")

# REQUIRED-band threshold for reasoning_reconstruction. Per variety_scorer.py:
# PLAN_MODE_MAX = 14, ORCHESTRATE_MAX = 10 → plan-mode-and-above starts at >= 11.
# When variety_scorer.py exports an explicit PLAN_MODE_MIN, this should be
# replaced with `from shared.variety_scorer import PLAN_MODE_MIN`.
REASONING_RECONSTRUCTION_REQUIRED_MIN: int = 11


def validate_reasoning_reconstruction(rr: object) -> str | None:
    """Return None if rr is a well-formed 3-sub-key triangle, or a short
    rejection-reason enum string.

    Schema:
      - dict with exactly REQUIRED_SUBKEYS as keys
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
    if set(rr.keys()) != set(REQUIRED_SUBKEYS):
        return "malformed_reasoning_reconstruction"
    for key in REQUIRED_SUBKEYS:
        value = rr[key]
        if not isinstance(value, str) or not value.strip():
            return "empty_reasoning_reconstruction_field"
    return None
