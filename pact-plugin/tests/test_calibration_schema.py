"""
Tests for CalibrationRecord schema validation and consistency.

Tests cover:
1. CalibrationRecord schema: required fields, types
2. Schema consistency between architecture doc and pact-variety.md protocol
3. Learning II + calibration interaction (both at threshold=5)
4. Calibration record roundtrip validation
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
    LEARNING_II_MIN_MATCHES,
    MAX_SCORE,
    MIN_SCORE,
    apply_calibration_adjustment,
    apply_learning_ii_adjustment,
    compute_calibration_drift,
)

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
VARIETY_PROTOCOL = PROTOCOLS_DIR / "pact-variety.md"

# CalibrationRecord schema fields (from architecture doc lines 170-195)
REQUIRED_FIELDS = {
    "task_id": str,
    "domain": str,
    "initial_variety_score": int,
    "actual_difficulty_score": int,
    "dimensions_that_drifted": list,
    "blocker_count": int,
    "phase_reruns": int,
    "specialist_fit": (str, type(None)),
    "timestamp": str,
}


def _make_valid_record():
    """Create a fully valid CalibrationRecord dict."""
    return {
        "task_id": "feat-42",
        "domain": "auth",
        "initial_variety_score": 8,
        "actual_difficulty_score": 10,
        "dimensions_that_drifted": [
            {"dimension": "scope", "predicted": 2, "actual": 3},
        ],
        "blocker_count": 1,
        "phase_reruns": 0,
        "specialist_fit": "good",
        "timestamp": "2026-03-23T12:00:00Z",
    }


# =============================================================================
# Schema validation
# =============================================================================


class TestCalibrationRecordSchema:
    """Validate CalibrationRecord schema matches spec."""

    def test_valid_record_has_all_required_fields(self):
        record = _make_valid_record()
        for field in REQUIRED_FIELDS:
            assert field in record, f"Missing required field: {field}"

    @pytest.mark.parametrize("field", list(REQUIRED_FIELDS.keys()))
    def test_each_required_field_has_correct_type(self, field):
        record = _make_valid_record()
        expected_type = REQUIRED_FIELDS[field]
        assert isinstance(record[field], expected_type), (
            f"Field '{field}' expected type {expected_type}, "
            f"got {type(record[field])}"
        )

    def test_dimensions_that_drifted_structure(self):
        record = _make_valid_record()
        for entry in record["dimensions_that_drifted"]:
            assert "dimension" in entry
            assert "predicted" in entry
            assert "actual" in entry
            assert entry["dimension"] in ("novelty", "scope", "uncertainty", "risk")
            assert 1 <= entry["predicted"] <= 4
            assert 1 <= entry["actual"] <= 4

    def test_specialist_fit_valid_values(self):
        """specialist_fit must be one of the allowed values or None."""
        valid_values = {"good", "undermatched", "overmatched", None}
        record = _make_valid_record()
        assert record["specialist_fit"] in valid_values

    def test_specialist_fit_null_accepted(self):
        record = _make_valid_record()
        record["specialist_fit"] = None
        assert record["specialist_fit"] is None

    def test_score_fields_in_valid_range(self):
        record = _make_valid_record()
        assert MIN_SCORE <= record["initial_variety_score"] <= MAX_SCORE
        assert MIN_SCORE <= record["actual_difficulty_score"] <= MAX_SCORE


# =============================================================================
# Schema consistency with protocol
# =============================================================================


class TestSchemaProtocolConsistency:
    """Verify CalibrationRecord fields match pact-variety.md protocol text."""

    @pytest.fixture
    def variety_content(self):
        return VARIETY_PROTOCOL.read_text(encoding="utf-8")

    def test_protocol_has_calibration_record_section(self, variety_content):
        assert "CalibrationRecord" in variety_content

    def test_protocol_has_task_id_field(self, variety_content):
        assert "task_id" in variety_content

    def test_protocol_has_domain_field(self, variety_content):
        assert "domain" in variety_content

    def test_protocol_has_initial_variety_score(self, variety_content):
        assert "initial_variety_score" in variety_content

    def test_protocol_has_actual_difficulty_score(self, variety_content):
        assert "actual_difficulty_score" in variety_content

    def test_protocol_has_dimensions_that_drifted(self, variety_content):
        assert "dimensions_that_drifted" in variety_content

    def test_protocol_has_blocker_count(self, variety_content):
        assert "blocker_count" in variety_content

    def test_protocol_has_phase_reruns(self, variety_content):
        assert "phase_reruns" in variety_content

    def test_protocol_has_specialist_fit(self, variety_content):
        assert "specialist_fit" in variety_content

    def test_protocol_has_timestamp(self, variety_content):
        assert "timestamp" in variety_content


# =============================================================================
# Learning II + Calibration interaction
# =============================================================================


class TestLearningCalibrationInteraction:
    """Test the two feedback layers operating together."""

    def test_both_layers_same_threshold(self):
        """Both Learning II and calibration have 5-sample threshold."""
        assert LEARNING_II_MIN_MATCHES == CALIBRATION_MIN_SAMPLES == 5

    def test_learning_ii_fires_before_calibration(self):
        """Learning II (qualitative) can fire even if calibration data insufficient."""
        # 5 matching memories but 0 calibration records
        base = 8
        adjusted = apply_learning_ii_adjustment(base, "auth", 5)
        assert adjusted == 9  # Learning II bumps

        # Calibration has no records -> no drift
        drift = compute_calibration_drift([], "auth")
        assert drift == 0.0

        # Final score after both layers: 9 (only Learning II fired)
        final = apply_calibration_adjustment(adjusted, drift)
        assert final == 9

    def test_both_layers_fire_independently(self):
        """When both have enough data, both adjust."""
        base = 8

        # Learning II: 5+ matches -> +1
        after_l2 = apply_learning_ii_adjustment(base, "auth", 6)
        assert after_l2 == 9

        # Calibration: positive drift -> +1 more
        records = [
            {"domain": "auth", "initial_variety_score": 8,
             "actual_difficulty_score": 11}
            for _ in range(5)
        ]
        drift = compute_calibration_drift(records, "auth")
        assert drift == 3.0  # mean(11-8) = 3.0

        after_cal = apply_calibration_adjustment(after_l2, drift)
        assert after_cal == 10  # 9 + 1 (clamped adjustment)

    def test_combined_clamped_at_max(self):
        """Combined adjustments cannot exceed MAX_SCORE."""
        base = 15

        # Learning II: +1 -> 16
        after_l2 = apply_learning_ii_adjustment(base, "auth", 5)
        assert after_l2 == 16

        # Calibration: large positive drift -> would want +1 more, but clamped
        records = [
            {"domain": "auth", "initial_variety_score": 8,
             "actual_difficulty_score": 14}
            for _ in range(5)
        ]
        drift = compute_calibration_drift(records, "auth")
        after_cal = apply_calibration_adjustment(after_l2, drift)
        assert after_cal == MAX_SCORE  # Cannot exceed 16

    def test_calibration_fires_without_learning_ii(self):
        """Calibration works even with insufficient Learning II matches."""
        base = 8

        # Learning II: only 2 matches -> no bump
        after_l2 = apply_learning_ii_adjustment(base, "auth", 2)
        assert after_l2 == 8

        # Calibration: 5 records with positive drift -> +1
        records = [
            {"domain": "auth", "initial_variety_score": 8,
             "actual_difficulty_score": 10}
            for _ in range(5)
        ]
        drift = compute_calibration_drift(records, "auth")
        after_cal = apply_calibration_adjustment(after_l2, drift)
        assert after_cal == 9

    def test_opposing_adjustments_cancel(self):
        """Learning II bumps up, calibration bumps down -> net effect."""
        base = 10

        # Learning II: +1 -> 11
        after_l2 = apply_learning_ii_adjustment(base, "auth", 5)
        assert after_l2 == 11

        # Calibration: overestimation (negative drift) -> -1
        records = [
            {"domain": "auth", "initial_variety_score": 10,
             "actual_difficulty_score": 7}
            for _ in range(5)
        ]
        drift = compute_calibration_drift(records, "auth")
        assert drift == -3.0

        after_cal = apply_calibration_adjustment(after_l2, drift)
        assert after_cal == 10  # 11 - 1 = 10 (net zero)
