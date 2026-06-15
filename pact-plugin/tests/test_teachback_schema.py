"""Tests for shared/teachback_schema.py — canonical teachback schema constants
and the reasoning_reconstruction sub-key validator.

Pins:
  - TEACHBACK_REQUIRED_FIELDS: canonical 5-tuple (4 string fields + variety_acknowledgment)
  - TEACHBACK_REQUIRED_SUBKEYS: canonical 3-tuple (decision_attribution / assumption_trace /
    contingency_clause)
  - TEACHBACK_VARIETY_ACK_VALID_VALUES: 3-tuple enum (yes / no / concern)
  - TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN: 11 (variety-band threshold mirror)
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

from pathlib import Path

import pytest

from shared.teachback_schema import (
    TEACHBACK_OBJECT_FIELDS,
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_REQUIRED_SUBKEYS,
    TEACHBACK_SCHEMA_ECHO,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
    resolve_variety_total,
    validate_reasoning_reconstruction,
)
from shared.variety_scorer import (
    MAX_DIMENSION,
    MAX_SCORE,
    MIN_DIMENSION,
    MIN_SCORE,
)


# ============================================================================
# Constant shape pins
# ============================================================================


class TestConstants:
    """Pin the exact canonical shapes — drift will require paired SKILL.md
    and pact-orchestrator §12 prose edits."""

    def test_required_fields_is_5_tuple(self):
        assert isinstance(TEACHBACK_REQUIRED_FIELDS, tuple)
        assert len(TEACHBACK_REQUIRED_FIELDS) == 5

    def test_required_fields_exact_names(self):
        assert TEACHBACK_REQUIRED_FIELDS == (
            "understanding",
            "most_likely_wrong",
            "least_confident_item",
            "first_action",
            "variety_acknowledgment",
        )

    def test_required_subkeys_is_3_tuple(self):
        assert isinstance(TEACHBACK_REQUIRED_SUBKEYS, tuple)
        assert len(TEACHBACK_REQUIRED_SUBKEYS) == 3

    def test_required_subkeys_exact_names(self):
        assert TEACHBACK_REQUIRED_SUBKEYS == (
            "decision_attribution",
            "assumption_trace",
            "contingency_clause",
        )

    def test_variety_ack_valid_values_exact(self):
        assert TEACHBACK_VARIETY_ACK_VALID_VALUES == ("yes", "no", "concern")

    def test_required_min_threshold_is_11(self):
        # Mirror the source derivation: the threshold binds to the plan-mode
        # floor PLAN_MODE_MIN, and PLAN_MODE_MIN itself is the SSOT relation
        # ORCHESTRATE_MAX + 1. Asserting both links catches drift at either
        # one — a change to PLAN_MODE_MIN's definition surfaces on the second
        # assertion even though the threshold tracks it on the first.
        from shared.variety_scorer import (
            ORCHESTRATE_MAX,
            PLAN_MODE_MAX,
            PLAN_MODE_MIN,
        )

        assert TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN == PLAN_MODE_MIN
        assert PLAN_MODE_MIN == ORCHESTRATE_MAX + 1
        assert TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN <= PLAN_MODE_MAX


# ============================================================================
# TEACHBACK_SCHEMA_ECHO — the remediation string appended to the schema-invalid
# deny message (#958). Pins the no-hardcoded-copy DERIVATION property and the
# variety_acknowledgment-is-an-OBJECT note. The gate-side integration (echo is
# present in the advisory message) is covered in test_task_lifecycle_gate.py;
# this class pins what the echo SAYS.
# ============================================================================


class TestSchemaEcho:
    """Pin TEACHBACK_SCHEMA_ECHO's content and its derivation from
    TEACHBACK_REQUIRED_FIELDS."""

    def test_echo_is_nonempty_string(self):
        assert isinstance(TEACHBACK_SCHEMA_ECHO, str)
        assert TEACHBACK_SCHEMA_ECHO.strip()

    def test_echo_names_every_required_field(self):
        # Derivation pin (no-hardcoded-copy property): EVERY canonical required
        # field name must appear in the echo. The 4 string fields appear in the
        # joined list; variety_acknowledgment appears in the is-OBJECT note. A
        # field added to TEACHBACK_REQUIRED_FIELDS that the echo's derivation
        # failed to surface would fail here — the echo cannot silently drift
        # from the enforced tuple.
        for field in TEACHBACK_REQUIRED_FIELDS:
            assert field in TEACHBACK_SCHEMA_ECHO, (
                f"echo must name required field {field!r}; "
                f"got: {TEACHBACK_SCHEMA_ECHO!r}"
            )

    def test_echo_notes_variety_acknowledgment_is_an_object(self):
        # The most common wrong shape is a free-text string, so the echo must
        # explicitly flag that variety_acknowledgment is an OBJECT.
        assert "variety_acknowledgment" in TEACHBACK_SCHEMA_ECHO
        assert "OBJECT" in TEACHBACK_SCHEMA_ECHO


# ============================================================================
# TEACHBACK_OBJECT_FIELDS — the string/object partition SSOT (#958 Stage 1).
# Both TEACHBACK_SCHEMA_ECHO's comma-list carve-out and the lifecycle gate's
# string-field validator derive their "skip the object fields" set from this
# one tuple. These tests pin the partition and couple the echo's carve-out
# back to it so the two cannot silently desync.
# ============================================================================


class TestObjectFieldsPartition:
    """#958 Future-2 (Stage-1 follow-through): pin TEACHBACK_OBJECT_FIELDS as
    the string/object partition SSOT."""

    def test_object_fields_is_nonempty_tuple(self):
        assert isinstance(TEACHBACK_OBJECT_FIELDS, tuple)
        assert TEACHBACK_OBJECT_FIELDS

    def test_object_fields_subset_of_required(self):
        # Partition validity: every object field must be a canonical required
        # field — an object field outside the required tuple would mean the
        # carve-out skips a name the schema never enforced.
        assert set(TEACHBACK_OBJECT_FIELDS) <= set(TEACHBACK_REQUIRED_FIELDS)

    def test_variety_acknowledgment_is_an_object_field(self):
        assert "variety_acknowledgment" in TEACHBACK_OBJECT_FIELDS

    def test_echo_string_list_is_required_minus_object(self):
        # Structural complement pin: the echo's comma-enumerated string-field
        # list (the prefix before " (non-empty strings)") must be exactly the
        # complement TEACHBACK_REQUIRED_FIELDS minus TEACHBACK_OBJECT_FIELDS:
        # every string field present, every object field absent. This couples
        # the echo's carve-out to the partition SSOT — if the echo derived its
        # carve-out from a DIFFERENT partition than TEACHBACK_OBJECT_FIELDS
        # (e.g. a hardcoded skip of the wrong field), a string field would go
        # missing from the comma-list and/or an object field would leak in,
        # failing here.
        #
        # NOTE: TEACHBACK_SCHEMA_ECHO is precomputed at import time, so this is a
        # STRUCTURAL check against the live constant — not a monkeypatch
        # flow-through (which cannot reach the already-built string).
        string_fields = tuple(
            f for f in TEACHBACK_REQUIRED_FIELDS
            if f not in TEACHBACK_OBJECT_FIELDS
        )
        prefix = TEACHBACK_SCHEMA_ECHO.split(" (non-empty strings)")[0]
        for f in string_fields:
            assert f in prefix, (
                f"string field {f!r} must appear in the echo's comma-list; "
                f"prefix={prefix!r}"
            )
        for f in TEACHBACK_OBJECT_FIELDS:
            assert f not in prefix, (
                f"object field {f!r} must NOT appear in the echo's string-field "
                f"comma-list (it belongs in the is-an-OBJECT note); "
                f"prefix={prefix!r}"
            )


# ============================================================================
# Doc-surface field enumeration (#958 Future-2a) — the LLM-loaded gate-template
# surfaces that enumerate the canonical teachback_submit fields must each name
# EVERY field in TEACHBACK_REQUIRED_FIELDS, so a future field-add can't silently
# leave a surface stale.
# ============================================================================

# Hardcoded list of the LLM-loaded surfaces that enumerate the canonical
# teachback_submit field names. LIMITATION: this list is hardcoded, so a NEW
# gate-template surface added later is NOT auto-covered — extend this list when
# adding a surface that enumerates the fields.
_FIELD_ENUMERATING_SURFACES: tuple[str, ...] = (
    "agents/pact-orchestrator.md",
    "commands/orchestrate.md",
    "commands/peer-review.md",
    "commands/comPACT.md",
    "commands/rePACT.md",
)

# pact-plugin/ root: this test lives at pact-plugin/tests/, so parent.parent.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class TestDocSurfaceFieldEnumeration:
    """#958 Future-2a: each LLM-loaded gate-template surface must name every
    canonical teachback_submit field. Substring-per-field (robust to rewording).

    LIMITATIONS (documented):
      - The surface file LIST (_FIELD_ENUMERATING_SURFACES) is hardcoded — a NEW
        gate-template surface added later is NOT auto-covered.
      - "understanding" is a common English word, so its per-file presence check
        is looser than the 4 distinctive snake_case field names; the snake_case
        fields carry the real anti-drift signal.
    """

    @pytest.mark.parametrize("surface", _FIELD_ENUMERATING_SURFACES)
    def test_surface_names_every_required_field(self, surface):
        path = _PLUGIN_ROOT / surface
        assert path.is_file(), (
            f"expected gate-template surface at {path} "
            f"(surface moved/renamed — update _FIELD_ENUMERATING_SURFACES?)"
        )
        text = path.read_text(encoding="utf-8")
        for field in TEACHBACK_REQUIRED_FIELDS:
            assert field in text, (
                f"{surface} must enumerate canonical field {field!r} "
                f"(stale surface after a field-add?)"
            )


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
        # Mirrors observed wrong-shape pattern (substituting non-canonical
        # sub-key names like `what-I-learned`).
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


# ============================================================================
# resolve_variety_total — per-candidate type matrix
#
# The resolver walks an ordered candidate chain (total → score →
# metadata.variety_score → dimension-sum) and returns the first valid
# in-range int, or None when nothing resolves. These tests isolate each
# candidate so the value-type acceptance/rejection contract is pinned for
# every candidate independently; precedence interactions are covered in the
# precedence section below.
#
# In-range means MIN_SCORE..MAX_SCORE (4..16) for the total/score/
# variety_score candidates, and MIN_DIMENSION..MAX_DIMENSION (1..4) per
# dimension for the dimension-sum candidate — referenced via the
# variety_scorer constants, never hard-coded.
# ============================================================================


# A set of values that are NOT a valid in-range int under any candidate.
# `id`s double as readable parametrize labels.
_REJECTED_VALUES = [
    pytest.param(True, id="bool_true"),
    pytest.param(False, id="bool_false"),
    pytest.param(8.0, id="float_whole"),
    pytest.param(8.5, id="float_fractional"),
    pytest.param("8", id="numeric_string"),
    pytest.param("high", id="non_numeric_string"),
    pytest.param(None, id="none"),
    pytest.param([8], id="list"),
    pytest.param({"x": 8}, id="dict"),
]

# Out-of-range ints for the [4, 16] total/score candidates: below MIN_SCORE
# and above MAX_SCORE. These are well-typed ints that still must NOT resolve.
_OUT_OF_RANGE_SCORE_INTS = [
    pytest.param(0, id="zero"),
    pytest.param(MIN_SCORE - 1, id="below_min"),  # 3
    pytest.param(MAX_SCORE + 1, id="above_max"),  # 17
    pytest.param(99, id="far_above_max"),
]

# Boundary + interior in-range ints. The 6/7 and 10/11 pairs straddle the
# band cuts the CALLER applies; the resolver itself returns the int unchanged
# for any of them (band mapping is the caller's job, asserted separately).
_IN_RANGE_SCORE_INTS = [4, 6, 7, 8, 10, 11, 16]


class TestResolverCandidateTotal:
    """Candidate 1: variety['total'] in isolation (no other candidate
    present, so a fall-through lands on None)."""

    @pytest.mark.parametrize("value", _IN_RANGE_SCORE_INTS)
    def test_in_range_total_resolves_to_itself(self, value):
        assert resolve_variety_total({"total": value}) == value

    @pytest.mark.parametrize("value", _OUT_OF_RANGE_SCORE_INTS)
    def test_out_of_range_total_does_not_resolve(self, value):
        assert resolve_variety_total({"total": value}) is None

    @pytest.mark.parametrize("value", _REJECTED_VALUES)
    def test_wrong_typed_total_does_not_resolve(self, value):
        assert resolve_variety_total({"total": value}) is None

    def test_absent_total_does_not_resolve(self):
        assert resolve_variety_total({}) is None


class TestResolverCandidateScore:
    """Candidate 2: variety['score'] in isolation (no 'total' key, so the
    canonical candidate is absent and the chain reaches 'score')."""

    @pytest.mark.parametrize("value", _IN_RANGE_SCORE_INTS)
    def test_in_range_score_resolves_to_itself(self, value):
        assert resolve_variety_total({"score": value}) == value

    @pytest.mark.parametrize("value", _OUT_OF_RANGE_SCORE_INTS)
    def test_out_of_range_score_does_not_resolve(self, value):
        assert resolve_variety_total({"score": value}) is None

    @pytest.mark.parametrize("value", _REJECTED_VALUES)
    def test_wrong_typed_score_does_not_resolve(self, value):
        assert resolve_variety_total({"score": value}) is None


class TestResolverCandidateVarietyScore:
    """Candidate 3: metadata['variety_score'] in isolation — a top-level
    sibling reached only when the `metadata` argument is supplied. The
    variety dict carries no resolvable candidate of its own."""

    @pytest.mark.parametrize("value", _IN_RANGE_SCORE_INTS)
    def test_in_range_variety_score_resolves_to_itself(self, value):
        assert resolve_variety_total({}, {"variety_score": value}) == value

    @pytest.mark.parametrize("value", _OUT_OF_RANGE_SCORE_INTS)
    def test_out_of_range_variety_score_does_not_resolve(self, value):
        assert resolve_variety_total({}, {"variety_score": value}) is None

    @pytest.mark.parametrize("value", _REJECTED_VALUES)
    def test_wrong_typed_variety_score_does_not_resolve(self, value):
        assert resolve_variety_total({}, {"variety_score": value}) is None

    def test_variety_score_skipped_when_metadata_absent(self):
        # The sibling lives on metadata, never inside the variety dict. With
        # no metadata argument it must NOT be consulted, even if a same-named
        # key sits inside the variety dict.
        assert resolve_variety_total({"variety_score": 8}) is None

    def test_variety_score_skipped_when_metadata_not_dict(self):
        assert resolve_variety_total({}, "not-a-dict") is None


def _dimensions(novelty, scope, uncertainty, risk):
    """A variety dict carrying ONLY the four per-dimension scores (no total,
    no score), so the dimension-sum candidate is the only one that can fire."""
    return {
        "novelty": novelty,
        "scope": scope,
        "uncertainty": uncertainty,
        "risk": risk,
    }


class TestResolverCandidateDimensionSum:
    """Candidate 4: sum of the four dimension scores, valid only when ALL
    four are in-range dimension ints (1..4 each). The sum is in [4, 16] by
    construction."""

    def test_all_min_dimensions_sums_to_min_score(self):
        v = _dimensions(MIN_DIMENSION, MIN_DIMENSION, MIN_DIMENSION, MIN_DIMENSION)
        assert resolve_variety_total(v) == MIN_SCORE  # 4

    def test_all_max_dimensions_sums_to_max_score(self):
        v = _dimensions(MAX_DIMENSION, MAX_DIMENSION, MAX_DIMENSION, MAX_DIMENSION)
        assert resolve_variety_total(v) == MAX_SCORE  # 16

    def test_mixed_in_range_dimensions_sum(self):
        assert resolve_variety_total(_dimensions(2, 3, 1, 4)) == 10

    @pytest.mark.parametrize(
        "position", ["novelty", "scope", "uncertainty", "risk"]
    )
    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param(MIN_DIMENSION - 1, id="below_min_dim"),  # 0
            pytest.param(MAX_DIMENSION + 1, id="above_max_dim"),  # 5
            pytest.param(True, id="bool"),
            pytest.param(2.0, id="float"),
            pytest.param("2", id="numeric_string"),
            pytest.param(None, id="none"),
        ],
    )
    def test_one_bad_dimension_invalidates_the_sum(self, bad, position):
        # A single out-of-range / wrong-typed dimension means the all-four
        # guard fails → the candidate does not fire → None (no other source).
        # Parametrized across every dimension position so an asymmetric guard
        # that skipped checking one dimension would surface here.
        v = _dimensions(2, 2, 2, 2)
        v[position] = bad
        assert resolve_variety_total(v) is None

    @pytest.mark.parametrize(
        "position", ["novelty", "scope", "uncertainty", "risk"]
    )
    def test_one_missing_dimension_invalidates_the_sum(self, position):
        # Every dimension position must be required by the all-four guard;
        # dropping any one yields None.
        v = _dimensions(2, 2, 2, 2)
        del v[position]
        assert resolve_variety_total(v) is None


# ============================================================================
# resolve_variety_total — precedence & conflict combinations
# ============================================================================


class TestResolverPrecedence:
    """Ordered first-valid-match precedence and the fall-through-on-invalid
    robustness property: a junk higher-precedence candidate never shadows a
    recoverable lower-precedence one."""

    def test_canonical_total_wins_over_all_other_candidates(self):
        v = {"total": 12, "score": 8, "novelty": 1, "scope": 1,
             "uncertainty": 1, "risk": 1}
        assert resolve_variety_total(v, {"variety_score": 8}) == 12

    def test_invalid_total_falls_through_to_valid_score(self):
        # Out-of-range canonical total must not halt the chain.
        assert resolve_variety_total({"total": 99, "score": 8}) == 8

    @pytest.mark.parametrize(
        "junk_total",
        [99, 0, True, 8.0, "8", "high", None, [8]],
    )
    def test_any_invalid_total_falls_through_to_score(self, junk_total):
        v = {"total": junk_total, "score": 9}
        assert resolve_variety_total(v) == 9

    def test_falls_through_total_and_score_to_variety_score(self):
        v = {"total": 99, "score": 0}
        assert resolve_variety_total(v, {"variety_score": 11}) == 11

    def test_score_absent_total_absent_resolves_variety_score(self):
        assert resolve_variety_total({}, {"variety_score": 7}) == 7

    def test_falls_through_to_dimension_sum_when_higher_candidates_invalid(self):
        # total junk, score junk, no variety_score, but all four dims valid.
        v = {"total": 99, "score": "bad", "novelty": 3, "scope": 3,
             "uncertainty": 2, "risk": 2}
        assert resolve_variety_total(v, {"variety_score": "bad"}) == 10

    def test_divergent_total_and_score_returns_total_silently(self):
        # Canonical wins; the resolver returns a single deterministic answer
        # and emits no divergence signal (it is a pure int-or-None function).
        result = resolve_variety_total({"total": 12, "score": 8})
        assert result == 12

    def test_all_candidates_absent_returns_none(self):
        assert resolve_variety_total({}, {}) is None

    def test_all_candidates_invalid_returns_none(self):
        v = {"total": 99, "score": 0, "novelty": 5, "scope": 0,
             "uncertainty": 2, "risk": 2}
        assert resolve_variety_total(v, {"variety_score": 99}) is None


# ============================================================================
# resolve_variety_total — exception-safety negative property
#
# The hook fires on every Task-tool use; the resolver must be total (defined
# for every input) and never raise. This is the helper-leg of the negative
# property; the hook-level leg (full evaluate_lifecycle envelopes) lives in
# test_task_lifecycle_gate.py.
# ============================================================================


class TestResolverNeverRaises:
    """No input — well-formed, malformed, or exotic — escapes as an
    exception; the resolver always returns an int or None."""

    @pytest.mark.parametrize(
        "variety",
        [
            None,
            [],
            "string",
            42,
            8.5,
            True,
            (1, 2),
            object(),
            {"total": object()},
            {"total": float("nan")},
            {"score": [1, 2, 3]},
            {"novelty": {"nested": "junk"}},
            {"total": {"deeply": {"nested": "junk"}}},
        ],
    )
    def test_malformed_variety_returns_int_or_none_never_raises(self, variety):
        result = resolve_variety_total(variety)
        assert result is None or isinstance(result, int)

    @pytest.mark.parametrize(
        "metadata",
        [None, [], "string", 42, object(), {"variety_score": object()}],
    )
    def test_malformed_metadata_returns_int_or_none_never_raises(self, metadata):
        result = resolve_variety_total({}, metadata)
        assert result is None or isinstance(result, int)

    def test_resolved_value_is_never_a_bool(self):
        # bool subclasses int; a True total must not surface as 1.
        for v in ({"total": True}, {"score": False}):
            result = resolve_variety_total(v)
            assert result is None or not isinstance(result, bool)
