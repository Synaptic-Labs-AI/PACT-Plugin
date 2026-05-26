"""Tests for shared/teachback_schema.py — canonical teachback schema constants
and the reasoning_reconstruction sub-key validator.

Pins:
  - REQUIRED_FIELDS: canonical 5-tuple (4 string fields + variety_acknowledgment)
  - REQUIRED_SUBKEYS: canonical 3-tuple (decision_attribution / assumption_trace /
    contingency_clause)
  - VARIETY_ACK_VALID_VALUES: 3-tuple enum (yes / no / concern)
  - REASONING_RECONSTRUCTION_REQUIRED_MIN: 11 (variety-band threshold mirror)
  - validate_reasoning_reconstruction:
      - None on well-formed 3-sub-key triangle of non-empty strings
      - "malformed_reasoning_reconstruction" on not-dict / wrong-keys
      - "empty_reasoning_reconstruction_field" on empty/non-string sub-key

These constants are consumed by hooks/task_lifecycle_gate.py at runtime and by
tests/test_teachback_reasoning_reconstruction.py at the lead-side band-routing
layer. The validator is the SSOT for the schema gate; band routing is
layered on top by callers.
"""

from __future__ import annotations

import pytest

from shared.teachback_schema import (
    REASONING_RECONSTRUCTION_REQUIRED_MIN,
    REQUIRED_FIELDS,
    REQUIRED_SUBKEYS,
    VARIETY_ACK_VALID_VALUES,
    validate_reasoning_reconstruction,
)


# ============================================================================
# Constant shape pins
# ============================================================================


class TestConstants:
    """Pin the exact canonical shapes — drift will require paired SKILL.md
    and pact-orchestrator §12 prose edits."""

    def test_required_fields_is_5_tuple(self):
        assert isinstance(REQUIRED_FIELDS, tuple)
        assert len(REQUIRED_FIELDS) == 5

    def test_required_fields_exact_names(self):
        assert REQUIRED_FIELDS == (
            "understanding",
            "most_likely_wrong",
            "least_confident_item",
            "first_action",
            "variety_acknowledgment",
        )

    def test_required_subkeys_is_3_tuple(self):
        assert isinstance(REQUIRED_SUBKEYS, tuple)
        assert len(REQUIRED_SUBKEYS) == 3

    def test_required_subkeys_exact_names(self):
        assert REQUIRED_SUBKEYS == (
            "decision_attribution",
            "assumption_trace",
            "contingency_clause",
        )

    def test_variety_ack_valid_values_exact(self):
        assert VARIETY_ACK_VALID_VALUES == ("yes", "no", "concern")

    def test_required_min_threshold_is_11(self):
        # Mirror of variety_scorer: ORCHESTRATE_MAX=10, PLAN_MODE_MAX=14
        # → plan-mode-and-above starts at >= 11.
        from shared.variety_scorer import ORCHESTRATE_MAX, PLAN_MODE_MAX

        assert REASONING_RECONSTRUCTION_REQUIRED_MIN == ORCHESTRATE_MAX + 1
        assert REASONING_RECONSTRUCTION_REQUIRED_MIN <= PLAN_MODE_MAX


# ============================================================================
# Validator behavior — well-formed input
# ============================================================================


def _well_formed_triangle() -> dict:
    return {
        "decision_attribution": "I understand X chose Y because Z",
        "assumption_trace": "This depends on A and B",
        "contingency_clause": "If A changes, the decision should change to C",
    }


class TestValidatorAccept:
    """Well-formed inputs return None."""

    def test_canonical_triangle_accepted(self):
        assert validate_reasoning_reconstruction(_well_formed_triangle()) is None

    def test_subkey_with_internal_whitespace_accepted(self):
        rr = _well_formed_triangle()
        rr["assumption_trace"] = "A long\nmulti-line\nrationale is fine"
        assert validate_reasoning_reconstruction(rr) is None


# ============================================================================
# Validator behavior — malformed inputs
# ============================================================================


class TestValidatorMalformed:
    """Wrong type / wrong keys → malformed_reasoning_reconstruction."""

    def test_not_dict_returns_malformed(self):
        assert (
            validate_reasoning_reconstruction("a string")
            == "malformed_reasoning_reconstruction"
        )

    def test_list_returns_malformed(self):
        assert (
            validate_reasoning_reconstruction(["d", "a", "c"])
            == "malformed_reasoning_reconstruction"
        )

    def test_none_returns_malformed(self):
        # Callers may pre-filter None (it means "absent"), but the validator
        # itself is conservative — None is not a dict.
        assert (
            validate_reasoning_reconstruction(None)
            == "malformed_reasoning_reconstruction"
        )

    def test_empty_dict_returns_malformed(self):
        assert (
            validate_reasoning_reconstruction({})
            == "malformed_reasoning_reconstruction"
        )

    def test_partial_keys_returns_malformed(self):
        partial = {"decision_attribution": "x", "assumption_trace": "y"}
        assert (
            validate_reasoning_reconstruction(partial)
            == "malformed_reasoning_reconstruction"
        )

    def test_extra_key_returns_malformed(self):
        rr = _well_formed_triangle()
        rr["surprise_key"] = "shouldn't be here"
        assert (
            validate_reasoning_reconstruction(rr)
            == "malformed_reasoning_reconstruction"
        )

    def test_substituted_key_returns_malformed(self):
        # Mirrors PR #834 cycle-3 wrong-shape (`what-I-learned` etc.)
        rr = {
            "what-I-learned": "x",
            "falsification-attempts": "y",
            "most-likely-wrong-prediction": "z",
        }
        assert (
            validate_reasoning_reconstruction(rr)
            == "malformed_reasoning_reconstruction"
        )


# ============================================================================
# Validator behavior — empty/non-string sub-key values
# ============================================================================


class TestValidatorEmptyField:
    """Right keys, wrong values → empty_reasoning_reconstruction_field."""

    def test_empty_string_subkey_returns_empty_field(self):
        rr = _well_formed_triangle()
        rr["contingency_clause"] = ""
        assert (
            validate_reasoning_reconstruction(rr)
            == "empty_reasoning_reconstruction_field"
        )

    def test_whitespace_only_subkey_returns_empty_field(self):
        rr = _well_formed_triangle()
        rr["assumption_trace"] = "   \n   "
        assert (
            validate_reasoning_reconstruction(rr)
            == "empty_reasoning_reconstruction_field"
        )

    def test_non_string_subkey_returns_empty_field(self):
        rr = _well_formed_triangle()
        rr["decision_attribution"] = ["list", "not", "string"]
        assert (
            validate_reasoning_reconstruction(rr)
            == "empty_reasoning_reconstruction_field"
        )

    def test_none_subkey_returns_empty_field(self):
        rr = _well_formed_triangle()
        rr["decision_attribution"] = None
        assert (
            validate_reasoning_reconstruction(rr)
            == "empty_reasoning_reconstruction_field"
        )


# ============================================================================
# Purity / never-raises contract
# ============================================================================


class TestValidatorPurity:
    """Validator never raises — pure function contract."""

    @pytest.mark.parametrize(
        "weird_input",
        [
            42,
            3.14,
            True,
            False,
            object(),
            (1, 2, 3),
        ],
    )
    def test_does_not_raise_on_weird_inputs(self, weird_input):
        # Should return malformed reason, not raise
        result = validate_reasoning_reconstruction(weird_input)
        assert result == "malformed_reasoning_reconstruction"
