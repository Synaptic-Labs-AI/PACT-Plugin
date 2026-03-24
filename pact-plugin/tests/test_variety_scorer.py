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
    route_workflow,
    score_variety,
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
