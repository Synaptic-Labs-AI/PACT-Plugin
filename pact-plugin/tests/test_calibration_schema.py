"""
Tests for CalibrationRecord schema validation and consistency.

Tests cover:
1. CalibrationRecord schema: required fields, types
2. Schema consistency between architecture doc and pact-variety.md protocol
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.variety_scorer import (
    LEARNING_II_MIN_MATCHES,
    MAX_SCORE,
    MIN_SCORE,
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
# Cross-reference: secretary calibration gathering vs CalibrationRecord schema
# =============================================================================

SECRETARY_AGENT = Path(__file__).parent.parent / "agents" / "pact-secretary.md"

# Fields the secretary must reference (exact snake_case or semantic equivalent)
_SECRETARY_FIELD_CHECKS = {
    "initial_variety_score": ["initial_variety_score"],
    "domain": ["domain"],
    "blocker_count": ["blocker_count", "blocker count"],
    "phase_reruns": ["phase_reruns", "phase rerun"],
    "specialist_fit": ["specialist_fit", "specialist fit"],
    "actual_difficulty_score": ["actual_difficulty_score", "actual difficulty"],
}


class TestSecretaryCalibrationCrossRef:
    """Verify secretary's calibration gathering (step 13) references CalibrationRecord fields."""

    @pytest.fixture
    def secretary_content(self):
        return SECRETARY_AGENT.read_text(encoding="utf-8")

    def test_secretary_has_calibration_gathering_step(self, secretary_content):
        """Secretary agent definition must include calibration gathering instructions."""
        assert "calibration" in secretary_content.lower()
        assert "CalibrationRecord" in secretary_content

    @pytest.mark.parametrize(
        "field,search_terms",
        list(_SECRETARY_FIELD_CHECKS.items()),
        ids=list(_SECRETARY_FIELD_CHECKS.keys()),
    )
    def test_secretary_references_calibration_field(self, secretary_content, field, search_terms):
        """Each CalibrationRecord field must be referenced in secretary's calibration protocol."""
        found = any(term in secretary_content for term in search_terms)
        assert found, (
            f"CalibrationRecord field '{field}' not found in pact-secretary.md. "
            f"Searched for: {search_terms}"
        )
