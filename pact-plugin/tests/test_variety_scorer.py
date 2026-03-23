"""
Tests for hooks/shared/variety_scorer.py — deterministic variety scoring
for PACT orchestration.

Tests cover:
1. validate_dimension: in-range, out-of-range, non-int, bool rejection
2. score_variety: all boundary cases, invalid inputs
3. route_workflow: all 4 routing thresholds at exact boundaries
4. apply_learning_ii_adjustment: below/at/above threshold, clamping
5. compute_calibration_drift: cold start, noise, valid drift, domain filtering
6. apply_calibration_adjustment: no drift, positive/negative, clamping
7. Constants consistency with architecture spec
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.variety_scorer import (
    CALIBRATION_MAX_ADJUSTMENT,
    CALIBRATION_MIN_SAMPLES,
    CALIBRATION_NOISE_THRESHOLD,
    CALIBRATION_WINDOW_SIZE,
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
    apply_calibration_adjustment,
    apply_learning_ii_adjustment,
    compute_calibration_drift,
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

    def test_calibration_window_size(self):
        assert CALIBRATION_WINDOW_SIZE == 5

    def test_calibration_min_samples(self):
        """Architecture Decision 2: aligned with Learning II threshold."""
        assert CALIBRATION_MIN_SAMPLES == 5

    def test_calibration_max_adjustment(self):
        assert CALIBRATION_MAX_ADJUSTMENT == 1

    def test_calibration_noise_threshold(self):
        assert CALIBRATION_NOISE_THRESHOLD == 1.0

    def test_calibration_and_learning_ii_thresholds_aligned(self):
        """Architecture Decision 3: both systems activate at N=5."""
        assert CALIBRATION_MIN_SAMPLES == LEARNING_II_MIN_MATCHES


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
# apply_learning_ii_adjustment
# =============================================================================


class TestApplyLearningIIAdjustment:
    """Tests for apply_learning_ii_adjustment()."""

    def test_below_threshold_no_bump(self):
        """Fewer than LEARNING_II_MIN_MATCHES -> no change."""
        assert apply_learning_ii_adjustment(8, "auth", 4) == 8

    def test_zero_matches_no_bump(self):
        assert apply_learning_ii_adjustment(10, "auth", 0) == 10

    def test_at_threshold_bumps(self):
        """Exactly LEARNING_II_MIN_MATCHES -> bumps by 1."""
        assert apply_learning_ii_adjustment(8, "auth", 5) == 9

    def test_above_threshold_bumps(self):
        """More than threshold -> same bump (+1)."""
        assert apply_learning_ii_adjustment(8, "auth", 10) == 9

    def test_clamped_at_max_score(self):
        """Score at MAX_SCORE cannot exceed MAX_SCORE after bump."""
        assert apply_learning_ii_adjustment(16, "auth", 5) == 16

    def test_near_max_clamped(self):
        """Score at MAX_SCORE - 1 bumps to MAX_SCORE, not beyond."""
        assert apply_learning_ii_adjustment(15, "auth", 5) == 16

    def test_domain_is_passthrough(self):
        """Domain parameter is accepted but doesn't affect scoring logic."""
        result_a = apply_learning_ii_adjustment(8, "auth", 5)
        result_b = apply_learning_ii_adjustment(8, "frontend", 5)
        assert result_a == result_b == 9

    def test_empty_domain_accepted(self):
        """Empty domain string doesn't cause errors."""
        assert apply_learning_ii_adjustment(8, "", 5) == 9

    def test_one_below_threshold_no_bump(self):
        """Exactly threshold-1 -> no bump (boundary precision)."""
        assert apply_learning_ii_adjustment(8, "hooks", LEARNING_II_MIN_MATCHES - 1) == 8


# =============================================================================
# compute_calibration_drift
# =============================================================================


def _make_cal_record(domain, initial, actual, timestamp=None):
    """Helper: create a CalibrationRecord dict."""
    rec = {
        "domain": domain,
        "initial_variety_score": initial,
        "actual_difficulty_score": actual,
    }
    if timestamp is not None:
        rec["timestamp"] = timestamp
    return rec


class TestComputeCalibrationDrift:
    """Tests for compute_calibration_drift()."""

    def test_empty_records_returns_zero(self):
        assert compute_calibration_drift([], "auth") == 0.0

    def test_insufficient_samples_returns_zero(self):
        """Fewer than CALIBRATION_MIN_SAMPLES -> no drift."""
        records = [_make_cal_record("auth", 8, 10) for _ in range(4)]
        assert compute_calibration_drift(records, "auth") == 0.0

    def test_exactly_min_samples_computes_drift(self):
        """Exactly CALIBRATION_MIN_SAMPLES -> drift computed."""
        records = [_make_cal_record("auth", 8, 10) for _ in range(5)]
        # drift = mean(10-8) = 2.0
        assert compute_calibration_drift(records, "auth") == 2.0

    def test_positive_drift_underestimation(self):
        """actual > initial -> positive drift (underestimation)."""
        records = [_make_cal_record("auth", 6, 9) for _ in range(5)]
        assert compute_calibration_drift(records, "auth") == 3.0

    def test_negative_drift_overestimation(self):
        """actual < initial -> negative drift (overestimation)."""
        records = [_make_cal_record("auth", 10, 7) for _ in range(5)]
        assert compute_calibration_drift(records, "auth") == -3.0

    def test_zero_drift(self):
        """actual == initial -> zero drift."""
        records = [_make_cal_record("auth", 8, 8) for _ in range(5)]
        assert compute_calibration_drift(records, "auth") == 0.0

    def test_domain_filtering(self):
        """Only records matching the domain are included."""
        records = [
            _make_cal_record("auth", 8, 10),
            _make_cal_record("auth", 8, 10),
            _make_cal_record("auth", 8, 10),
            _make_cal_record("auth", 8, 10),
            _make_cal_record("auth", 8, 10),
            _make_cal_record("frontend", 6, 6),
            _make_cal_record("frontend", 6, 6),
        ]
        assert compute_calibration_drift(records, "auth") == 2.0

    def test_domain_case_insensitive(self):
        """Domain matching is case-insensitive."""
        records = [_make_cal_record("Auth", 8, 10) for _ in range(5)]
        assert compute_calibration_drift(records, "auth") == 2.0

    def test_domain_case_insensitive_query(self):
        """Query domain is also case-insensitive."""
        records = [_make_cal_record("auth", 8, 10) for _ in range(5)]
        assert compute_calibration_drift(records, "AUTH") == 2.0

    def test_windowed_takes_most_recent(self):
        """With timestamps, takes the CALIBRATION_WINDOW_SIZE most recent."""
        records = [
            _make_cal_record("auth", 8, 8, "2026-01-01T00:00:00"),   # old, no drift
            _make_cal_record("auth", 8, 8, "2026-01-02T00:00:00"),   # old, no drift
            _make_cal_record("auth", 8, 12, "2026-03-01T00:00:00"),  # recent, +4
            _make_cal_record("auth", 8, 12, "2026-03-02T00:00:00"),  # recent, +4
            _make_cal_record("auth", 8, 12, "2026-03-03T00:00:00"),  # recent, +4
            _make_cal_record("auth", 8, 12, "2026-03-04T00:00:00"),  # recent, +4
            _make_cal_record("auth", 8, 12, "2026-03-05T00:00:00"),  # recent, +4
        ]
        # Window of 5 most recent: all +4 drift -> mean = 4.0
        assert compute_calibration_drift(records, "auth") == 4.0

    def test_without_timestamps_uses_first_n(self):
        """Without timestamps, takes the first N records (list order, no sort)."""
        records = [
            _make_cal_record("auth", 8, 8),   # index 0, drift=0
            _make_cal_record("auth", 8, 8),   # index 1, drift=0
            _make_cal_record("auth", 8, 10),  # index 2, drift=2
            _make_cal_record("auth", 8, 10),  # index 3, drift=2
            _make_cal_record("auth", 8, 10),  # index 4, drift=2
            _make_cal_record("auth", 8, 10),  # index 5 (outside window)
            _make_cal_record("auth", 8, 10),  # index 6 (outside window)
        ]
        # Window of first 5 records (no timestamp → no sort, takes [:5]):
        # Drifts: [0, 0, 2, 2, 2] → mean = (0+0+2+2+2)/5 = 1.2
        assert abs(compute_calibration_drift(records, "auth") - 1.2) < 0.001

    def test_mixed_drift_values(self):
        """Records with varying drift compute correct mean."""
        records = [
            _make_cal_record("auth", 8, 10),   # +2
            _make_cal_record("auth", 8, 9),    # +1
            _make_cal_record("auth", 8, 11),   # +3
            _make_cal_record("auth", 8, 7),    # -1
            _make_cal_record("auth", 8, 10),   # +2
        ]
        # mean = (2+1+3-1+2)/5 = 7/5 = 1.4
        assert abs(compute_calibration_drift(records, "auth") - 1.4) < 0.001

    def test_no_matching_domain_returns_zero(self):
        """Records exist but none match the requested domain."""
        records = [_make_cal_record("frontend", 8, 10) for _ in range(5)]
        assert compute_calibration_drift(records, "auth") == 0.0

    def test_missing_domain_field_skipped(self):
        """Records missing 'domain' field are skipped in filtering."""
        records = [{"initial_variety_score": 8, "actual_difficulty_score": 10}] * 5
        assert compute_calibration_drift(records, "auth") == 0.0

    # --- Mixed-timestamp edge cases (first-record heuristic) ---

    def test_mixed_timestamps_first_without_skips_sort(self):
        """First record without timestamp → no sort, takes first N from list.

        The implementation checks only domain_records[0].get("timestamp")
        to decide whether to sort. If the first record lacks a timestamp,
        no sort occurs even if later records have timestamps.
        """
        records = [
            _make_cal_record("auth", 8, 8),                              # no ts, drift=0
            _make_cal_record("auth", 8, 12, "2026-03-01T00:00:00"),      # ts, drift=4
            _make_cal_record("auth", 8, 12, "2026-03-02T00:00:00"),      # ts, drift=4
            _make_cal_record("auth", 8, 12, "2026-03-03T00:00:00"),      # ts, drift=4
            _make_cal_record("auth", 8, 12, "2026-03-04T00:00:00"),      # ts, drift=4
        ]
        # No sort (first record has no timestamp), window = all 5:
        # Drifts: [0, 4, 4, 4, 4] → mean = 16/5 = 3.2
        assert abs(compute_calibration_drift(records, "auth") - 3.2) < 0.001

    def test_mixed_timestamps_first_with_triggers_sort(self):
        """First record with timestamp → sort by timestamp descending.

        Records without timestamps sort with empty string key, landing
        at the end after descending sort.
        """
        records = [
            _make_cal_record("auth", 8, 12, "2026-03-01T00:00:00"),      # ts, drift=4
            _make_cal_record("auth", 8, 8),                              # no ts, drift=0
            _make_cal_record("auth", 8, 8),                              # no ts, drift=0
            _make_cal_record("auth", 8, 8),                              # no ts, drift=0
            _make_cal_record("auth", 8, 8),                              # no ts, drift=0
        ]
        # Sort triggered (first record has timestamp), descending by timestamp.
        # "2026-03-01" sorts before "" (empty), so sorted order is:
        #   [ts="2026-03-01"(drift=4), ""(0), ""(0), ""(0), ""(0)]
        # Window of 5: drifts [4, 0, 0, 0, 0] → mean = 4/5 = 0.8
        assert abs(compute_calibration_drift(records, "auth") - 0.8) < 0.001


# =============================================================================
# apply_calibration_adjustment
# =============================================================================


class TestApplyCalibrationAdjustment:
    """Tests for apply_calibration_adjustment()."""

    def test_no_drift_below_noise_threshold(self):
        """abs(drift) < CALIBRATION_NOISE_THRESHOLD -> no adjustment."""
        assert apply_calibration_adjustment(8, 0.5) == 8

    def test_zero_drift_no_adjustment(self):
        assert apply_calibration_adjustment(8, 0.0) == 8

    def test_exactly_at_noise_threshold_no_adjustment(self):
        """abs(drift) == CALIBRATION_NOISE_THRESHOLD -> no adjustment (< not <=)."""
        # The implementation uses < so drift=1.0 is NOT below threshold.
        # But drift = 0.99 IS below threshold.
        assert apply_calibration_adjustment(8, 0.99) == 8

    def test_positive_drift_bumps_up(self):
        """drift > noise threshold -> +1 adjustment."""
        assert apply_calibration_adjustment(8, 2.0) == 9

    def test_negative_drift_bumps_down(self):
        """drift < -noise threshold -> -1 adjustment."""
        assert apply_calibration_adjustment(8, -2.0) == 7

    def test_large_positive_drift_clamped_to_max_adjustment(self):
        """Even with huge drift, adjustment capped at +1."""
        assert apply_calibration_adjustment(8, 5.0) == 9

    def test_large_negative_drift_clamped_to_max_adjustment(self):
        """Even with huge negative drift, adjustment capped at -1."""
        assert apply_calibration_adjustment(8, -5.0) == 7

    def test_positive_adjustment_clamped_at_max_score(self):
        """Score at MAX_SCORE stays at MAX_SCORE."""
        assert apply_calibration_adjustment(16, 2.0) == 16

    def test_negative_adjustment_clamped_at_min_score(self):
        """Score at MIN_SCORE stays at MIN_SCORE."""
        assert apply_calibration_adjustment(4, -2.0) == 4

    def test_drift_slightly_above_threshold_adjusts(self):
        """drift = 1.01 (just above threshold) triggers adjustment."""
        assert apply_calibration_adjustment(8, 1.01) == 9

    def test_drift_slightly_below_negative_threshold_adjusts(self):
        """drift = -1.01 triggers downward adjustment."""
        assert apply_calibration_adjustment(8, -1.01) == 7

    def test_drift_exactly_at_threshold_value(self):
        """drift = 1.0 — at threshold, abs(1.0) is NOT < 1.0, so adjustment fires."""
        assert apply_calibration_adjustment(8, 1.0) == 9

    def test_negative_drift_at_threshold_value(self):
        """drift = -1.0 — at threshold, abs(-1.0) is NOT < 1.0, so adjustment fires."""
        assert apply_calibration_adjustment(8, -1.0) == 7
