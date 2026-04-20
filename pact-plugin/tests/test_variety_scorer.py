"""
Tests for hooks/shared/variety_scorer.py — deterministic variety scoring
for PACT orchestration.

Tests cover:
1. validate_dimension: in-range, out-of-range, non-int, bool rejection
2. score_variety: all boundary cases, invalid inputs
3. route_workflow: all 4 routing thresholds at exact boundaries
4. Constants consistency with architecture spec
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.variety_scorer import (
    COMPACT_MAX,
    DIMENSION_COUNT,
    LEARNING_II_MAX_BUMP,
    LEARNING_II_MIN_MATCHES,
    MAX_DIMENSION,
    MAX_SCORE,
    MIN_DIMENSION,
    MIN_SCORE,
    ORCHESTRATE_MAX,
    PLAN_MODE_MAX,
    ROUTE_COMPACT,
    ROUTE_ORCHESTRATE,
    ROUTE_PLAN_MODE,
    ROUTE_RESEARCH_SPIKE,
    TEACHBACK_BLOCKING_THRESHOLD,
    TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS,
    TEACHBACK_FULL_PROTOCOL_VARIETY,
    TEACHBACK_MODE_ADVISORY,
    TEACHBACK_MODE_BLOCKING,
    auditor_required_for_score,
    gates_for_score,
    route_workflow,
    score_variety,
    teachback_mode_for_score,
    validate_dimension,
)


# =============================================================================
# Constants consistency
# =============================================================================


class TestConstants:
    """Verify module constants match architecture spec and are self-consistent."""

    def test_dimension_bounds(self):
        assert MIN_DIMENSION == 1
        assert MAX_DIMENSION == 4

    def test_dimension_count(self):
        assert DIMENSION_COUNT == 4

    def test_score_range_derived_from_dimensions(self):
        assert MIN_SCORE == DIMENSION_COUNT * MIN_DIMENSION
        assert MAX_SCORE == DIMENSION_COUNT * MAX_DIMENSION

    def test_score_range_values(self):
        assert MIN_SCORE == 4
        assert MAX_SCORE == 16

    def test_routing_thresholds_ordered(self):
        assert MIN_SCORE <= COMPACT_MAX < ORCHESTRATE_MAX < PLAN_MODE_MAX <= MAX_SCORE

    def test_routing_threshold_values(self):
        assert COMPACT_MAX == 6
        assert ORCHESTRATE_MAX == 10
        assert PLAN_MODE_MAX == 14

    def test_route_names(self):
        assert ROUTE_COMPACT == "comPACT"
        assert ROUTE_ORCHESTRATE == "orchestrate"
        assert ROUTE_PLAN_MODE == "plan-mode"
        assert ROUTE_RESEARCH_SPIKE == "research-spike"

    def test_learning_ii_min_matches(self):
        """Architecture Decision 3: raised from 3 to 5."""
        assert LEARNING_II_MIN_MATCHES == 5

    def test_learning_ii_max_bump(self):
        assert LEARNING_II_MAX_BUMP == 1


# =============================================================================
# validate_dimension
# =============================================================================


class TestValidateDimension:
    """Tests for validate_dimension()."""

    @pytest.mark.parametrize("value", [1, 2, 3, 4])
    def test_valid_dimensions_accepted(self, value):
        # Should not raise
        validate_dimension(value)

    @pytest.mark.parametrize("value", [0, -1, -100, 5, 6, 100])
    def test_out_of_range_raises_value_error(self, value):
        with pytest.raises(ValueError, match="must be between"):
            validate_dimension(value)

    @pytest.mark.parametrize(
        "value",
        [1.0, 2.5, 3.14, None, "2", [1], {"a": 1}],
    )
    def test_non_integer_raises_type_error(self, value):
        with pytest.raises(TypeError, match="must be an integer"):
            validate_dimension(value)

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_rejected_as_type_error(self, value):
        """Booleans are explicitly rejected despite being int subclass."""
        with pytest.raises(TypeError, match="must be an integer"):
            validate_dimension(value)

    def test_custom_name_in_error_message(self):
        with pytest.raises(ValueError, match="novelty"):
            validate_dimension(0, "novelty")

    def test_custom_name_in_type_error_message(self):
        with pytest.raises(TypeError, match="risk"):
            validate_dimension(1.5, "risk")

    def test_default_name_is_dimension(self):
        with pytest.raises(ValueError, match="dimension"):
            validate_dimension(0)


# =============================================================================
# score_variety
# =============================================================================


class TestScoreVariety:
    """Tests for score_variety()."""

    def test_minimum_score(self):
        assert score_variety(1, 1, 1, 1) == 4

    def test_maximum_score(self):
        assert score_variety(4, 4, 4, 4) == 16

    def test_mixed_dimensions(self):
        assert score_variety(1, 2, 3, 4) == 10

    def test_all_same_dimension(self):
        assert score_variety(3, 3, 3, 3) == 12

    @pytest.mark.parametrize(
        "dims,expected",
        [
            ((1, 1, 1, 2), 5),
            ((1, 1, 2, 2), 6),
            ((1, 2, 2, 2), 7),
            ((2, 2, 2, 2), 8),
            ((2, 2, 3, 3), 10),
            ((3, 3, 3, 3), 12),
            ((3, 4, 4, 4), 15),
            ((4, 4, 4, 4), 16),
        ],
    )
    def test_score_is_sum_of_dimensions(self, dims, expected):
        assert score_variety(*dims) == expected

    # --- Input validation ---

    @pytest.mark.parametrize(
        "dims",
        [
            (0, 1, 1, 1),
            (1, 0, 1, 1),
            (1, 1, 0, 1),
            (1, 1, 1, 0),
        ],
    )
    def test_zero_dimension_raises_value_error(self, dims):
        with pytest.raises(ValueError):
            score_variety(*dims)

    @pytest.mark.parametrize(
        "dims",
        [
            (5, 1, 1, 1),
            (1, 5, 1, 1),
            (1, 1, 5, 1),
            (1, 1, 1, 5),
        ],
    )
    def test_dimension_above_max_raises_value_error(self, dims):
        with pytest.raises(ValueError):
            score_variety(*dims)

    def test_negative_dimension_raises_value_error(self):
        with pytest.raises(ValueError):
            score_variety(-1, 1, 1, 1)

    def test_float_dimension_raises_type_error(self):
        with pytest.raises(TypeError):
            score_variety(1.0, 1, 1, 1)

    def test_none_dimension_raises_type_error(self):
        with pytest.raises(TypeError):
            score_variety(None, 1, 1, 1)

    def test_bool_dimension_raises_type_error(self):
        with pytest.raises(TypeError):
            score_variety(True, 1, 1, 1)

    def test_string_dimension_raises_type_error(self):
        with pytest.raises(TypeError):
            score_variety("2", 1, 1, 1)

    def test_each_dimension_validated_independently(self):
        """Each dimension is validated; first invalid one triggers error."""
        with pytest.raises(TypeError):
            score_variety(1, 1, "3", 1)


# =============================================================================
# route_workflow
# =============================================================================


class TestRouteWorkflow:
    """Tests for route_workflow()."""

    # --- Boundary values ---

    @pytest.mark.parametrize("score", [4, 5, 6])
    def test_compact_range(self, score):
        assert route_workflow(score) == ROUTE_COMPACT

    @pytest.mark.parametrize("score", [7, 8, 9, 10])
    def test_orchestrate_range(self, score):
        assert route_workflow(score) == ROUTE_ORCHESTRATE

    @pytest.mark.parametrize("score", [11, 12, 13, 14])
    def test_plan_mode_range(self, score):
        assert route_workflow(score) == ROUTE_PLAN_MODE

    @pytest.mark.parametrize("score", [15, 16])
    def test_research_spike_range(self, score):
        assert route_workflow(score) == ROUTE_RESEARCH_SPIKE

    # --- Exact boundary transitions ---

    def test_boundary_6_compact(self):
        assert route_workflow(6) == ROUTE_COMPACT

    def test_boundary_7_orchestrate(self):
        assert route_workflow(7) == ROUTE_ORCHESTRATE

    def test_boundary_10_orchestrate(self):
        assert route_workflow(10) == ROUTE_ORCHESTRATE

    def test_boundary_11_plan_mode(self):
        assert route_workflow(11) == ROUTE_PLAN_MODE

    def test_boundary_14_plan_mode(self):
        assert route_workflow(14) == ROUTE_PLAN_MODE

    def test_boundary_15_research_spike(self):
        assert route_workflow(15) == ROUTE_RESEARCH_SPIKE

    # --- Out-of-range ---

    def test_below_min_raises_value_error(self):
        with pytest.raises(ValueError, match="must be between"):
            route_workflow(3)

    def test_above_max_raises_value_error(self):
        with pytest.raises(ValueError, match="must be between"):
            route_workflow(17)

    def test_zero_raises_value_error(self):
        with pytest.raises(ValueError):
            route_workflow(0)

    def test_negative_raises_value_error(self):
        with pytest.raises(ValueError):
            route_workflow(-1)

    # --- Type validation ---

    def test_float_raises_type_error(self):
        with pytest.raises(TypeError, match="must be an integer"):
            route_workflow(7.0)

    def test_bool_raises_type_error(self):
        with pytest.raises(TypeError, match="must be an integer"):
            route_workflow(True)

    def test_string_raises_type_error(self):
        with pytest.raises(TypeError):
            route_workflow("10")

    def test_none_raises_type_error(self):
        with pytest.raises(TypeError):
            route_workflow(None)


# =============================================================================
# Teachback gate constants and helpers (#401)
# =============================================================================


class TestTeachbackConstants:
    """Verify teachback-gate constants match architecture spec and are self-consistent."""

    def test_blocking_threshold_literal(self):
        assert TEACHBACK_BLOCKING_THRESHOLD == 7

    def test_full_protocol_variety_literal(self):
        assert TEACHBACK_FULL_PROTOCOL_VARIETY == 9

    def test_full_protocol_scope_items_literal(self):
        assert TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS == 2

    def test_blocking_threshold_inside_score_range(self):
        assert MIN_SCORE <= TEACHBACK_BLOCKING_THRESHOLD <= MAX_SCORE

    def test_full_protocol_variety_inside_score_range(self):
        assert MIN_SCORE <= TEACHBACK_FULL_PROTOCOL_VARIETY <= MAX_SCORE

    def test_full_protocol_variety_ge_blocking_threshold(self):
        """Full-protocol threshold must be >= blocking threshold — otherwise
        simplified protocol is unreachable."""
        assert TEACHBACK_FULL_PROTOCOL_VARIETY >= TEACHBACK_BLOCKING_THRESHOLD

    def test_mode_constants(self):
        assert TEACHBACK_MODE_BLOCKING == "blocking"
        assert TEACHBACK_MODE_ADVISORY == "advisory"


class TestTeachbackModeForScore:
    """Verify teachback_mode_for_score boundary behavior and validation."""

    def test_below_threshold_is_advisory(self):
        assert teachback_mode_for_score(6) == TEACHBACK_MODE_ADVISORY

    def test_at_threshold_is_blocking(self):
        """Boundary: score == 7 must be blocking (>= threshold)."""
        assert teachback_mode_for_score(7) == TEACHBACK_MODE_BLOCKING

    def test_above_threshold_is_blocking(self):
        assert teachback_mode_for_score(8) == TEACHBACK_MODE_BLOCKING
        assert teachback_mode_for_score(16) == TEACHBACK_MODE_BLOCKING

    def test_min_score_is_advisory(self):
        assert teachback_mode_for_score(MIN_SCORE) == TEACHBACK_MODE_ADVISORY

    def test_literal_threshold_matches_constant(self):
        """Regression: if TEACHBACK_BLOCKING_THRESHOLD moves, this test
        forces a downstream audit of every `variety >= 7` literal in the
        codebase (command .md files, hook constants)."""
        assert teachback_mode_for_score(TEACHBACK_BLOCKING_THRESHOLD) == TEACHBACK_MODE_BLOCKING
        assert teachback_mode_for_score(TEACHBACK_BLOCKING_THRESHOLD - 1) == TEACHBACK_MODE_ADVISORY

    # --- Validation ---

    def test_below_min_raises_value_error(self):
        with pytest.raises(ValueError):
            teachback_mode_for_score(MIN_SCORE - 1)

    def test_above_max_raises_value_error(self):
        with pytest.raises(ValueError):
            teachback_mode_for_score(MAX_SCORE + 1)

    def test_bool_raises_type_error(self):
        """bool is an int subclass; the validator must reject it explicitly."""
        with pytest.raises(TypeError, match="must be an integer"):
            teachback_mode_for_score(True)

    def test_float_raises_type_error(self):
        with pytest.raises(TypeError, match="must be an integer"):
            teachback_mode_for_score(7.0)

    def test_string_raises_type_error(self):
        with pytest.raises(TypeError):
            teachback_mode_for_score("7")

    def test_none_raises_type_error(self):
        with pytest.raises(TypeError):
            teachback_mode_for_score(None)


class TestAuditorRequiredForScore:
    """Verify auditor_required_for_score tracks blocking threshold."""

    def test_below_threshold_not_required(self):
        assert auditor_required_for_score(6) is False

    def test_at_threshold_required(self):
        assert auditor_required_for_score(7) is True

    def test_above_threshold_required(self):
        assert auditor_required_for_score(16) is True

    def test_min_score_not_required(self):
        assert auditor_required_for_score(MIN_SCORE) is False

    def test_bool_raises_type_error(self):
        with pytest.raises(TypeError, match="must be an integer"):
            auditor_required_for_score(True)

    def test_out_of_range_raises_value_error(self):
        with pytest.raises(ValueError):
            auditor_required_for_score(17)


class TestGatesForScore:
    """Verify gates_for_score returns the canonical three-key dict."""

    def test_shape_has_three_keys(self):
        result = gates_for_score(7)
        assert set(result.keys()) == {"teachback_mode", "auditor_required", "workflow_route"}

    def test_blocking_tier_at_threshold(self):
        assert gates_for_score(7) == {
            "teachback_mode": TEACHBACK_MODE_BLOCKING,
            "auditor_required": True,
            "workflow_route": ROUTE_ORCHESTRATE,
        }

    def test_advisory_tier_below_threshold(self):
        assert gates_for_score(6) == {
            "teachback_mode": TEACHBACK_MODE_ADVISORY,
            "auditor_required": False,
            "workflow_route": ROUTE_COMPACT,
        }

    def test_plan_mode_route_at_variety_11(self):
        result = gates_for_score(11)
        assert result["teachback_mode"] == TEACHBACK_MODE_BLOCKING
        assert result["auditor_required"] is True
        assert result["workflow_route"] == ROUTE_PLAN_MODE

    def test_research_spike_route_at_variety_15(self):
        result = gates_for_score(15)
        assert result["workflow_route"] == ROUTE_RESEARCH_SPIKE
        assert result["teachback_mode"] == TEACHBACK_MODE_BLOCKING

    def test_bool_raises_type_error(self):
        with pytest.raises(TypeError, match="must be an integer"):
            gates_for_score(True)

    def test_float_raises_type_error(self):
        with pytest.raises(TypeError):
            gates_for_score(7.0)

    def test_out_of_range_raises_value_error(self):
        with pytest.raises(ValueError):
            gates_for_score(17)
        with pytest.raises(ValueError):
            gates_for_score(3)


# Q2 tier matrix — variety-vs-scope-items classification (documentation-level
# test; actual protocol-level decision lives in teachback_gate.py Commit #7).
# Here we verify the primitives that Commit #7's _protocol_level helper will
# compose.
class TestProtocolLevelTierMatrix:
    """Ground the simplified-vs-full tier decisions in the primitives."""

    @pytest.mark.parametrize("variety,scope_items,expected_blocks,expected_full", [
        # (variety, scope_items, expected_blocking?, expected_full_protocol_via_variety_OR_scope?)
        (4, 0, False, False),   # exempt: below blocking threshold
        (6, 5, False, False),   # exempt: below blocking threshold even with many scope items
        (7, 0, True, False),    # blocking, simplified (variety<9 and scope<2)
        (7, 1, True, False),    # blocking, simplified
        (7, 2, True, True),     # blocking, full via scope_items cardinality
        (8, 0, True, False),    # blocking, simplified
        (8, 2, True, True),     # blocking, full via scope_items
        (9, 0, True, True),     # blocking, full via variety alone
        (9, 5, True, True),     # blocking, full via both
        (10, 1, True, True),    # blocking, full via variety
        (16, 0, True, True),    # max variety, full
    ])
    def test_tier_classification(self, variety, scope_items, expected_blocks, expected_full):
        blocks = teachback_mode_for_score(variety) == TEACHBACK_MODE_BLOCKING
        assert blocks is expected_blocks

        # Full protocol applies when blocked AND (variety >= 9 OR scope_items >= 2)
        full = blocks and (
            variety >= TEACHBACK_FULL_PROTOCOL_VARIETY
            or scope_items >= TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS
        )
        assert full is expected_full
