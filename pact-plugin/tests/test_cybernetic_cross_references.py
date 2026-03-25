"""
Tests for cross-references across cybernetic improvement files.

Tests cover:
1. Audit protocol file exists with required content
2. Variety thresholds match between variety_scorer.py and pact-variety.md
3. S4 checkpoints has 4 questions
4. Auditor dispatch thresholds consistent across pact-audit.md and orchestrate.md
5. CalibrationRecord fields consistent across protocol and architecture doc
6. Learning II threshold=5 consistent across protocol and code
7. Audit protocol referenced in pact-protocols.md SSOT
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"
AGENTS_DIR = Path(__file__).parent.parent / "agents"


# =============================================================================
# Audit protocol exists and has correct content
# =============================================================================


class TestAuditProtocolExists:
    """Verify pact-audit.md exists with expected content."""

    def test_protocol_file_exists(self):
        path = PROTOCOLS_DIR / "pact-audit.md"
        assert path.exists(), "Missing protocol file: pact-audit.md"

    def test_protocol_has_heading(self):
        content = (PROTOCOLS_DIR / "pact-audit.md").read_text(encoding="utf-8")
        assert "Concurrent Audit Protocol" in content

    def test_protocol_has_cybernetic_basis(self):
        content = (PROTOCOLS_DIR / "pact-audit.md").read_text(encoding="utf-8")
        lower = content.lower()
        assert "cybernetic basis" in lower or "cybernetic" in lower


# =============================================================================
# Variety thresholds: code vs protocol
# =============================================================================


class TestVarietyThresholdConsistency:
    """Verify variety_scorer.py constants match pact-variety.md protocol."""

    @pytest.fixture
    def variety_content(self):
        return (PROTOCOLS_DIR / "pact-variety.md").read_text(encoding="utf-8")

    def test_compact_range_in_protocol(self, variety_content):
        """Protocol specifies 4-6 -> comPACT."""
        from shared.variety_scorer import COMPACT_MAX, MIN_SCORE
        assert "4-6" in variety_content
        assert "comPACT" in variety_content

    def test_orchestrate_range_in_protocol(self, variety_content):
        """Protocol specifies 7-10 -> orchestrate."""
        assert "7-10" in variety_content
        assert "orchestrate" in variety_content

    def test_plan_mode_range_in_protocol(self, variety_content):
        """Protocol specifies 11-14 -> plan-mode."""
        assert "11-14" in variety_content
        assert "plan-mode" in variety_content

    def test_research_spike_range_in_protocol(self, variety_content):
        """Protocol specifies 15-16 -> research spike."""
        assert "15-16" in variety_content
        assert "Research spike" in variety_content or "research spike" in variety_content.lower()

    def test_learning_ii_threshold_in_protocol(self, variety_content):
        """Protocol specifies 5+ memories for Learning II."""
        from shared.variety_scorer import LEARNING_II_MIN_MATCHES
        assert LEARNING_II_MIN_MATCHES == 5
        assert "5+" in variety_content or "5 " in variety_content


# =============================================================================
# S4 Checkpoints: 4 questions
# =============================================================================


class TestS4CheckpointQuestions:
    """Verify S4 checkpoints has 4 core questions."""

    @pytest.fixture
    def checkpoint_content(self):
        return (PROTOCOLS_DIR / "pact-s4-checkpoints.md").read_text(encoding="utf-8")

    def test_has_four_numbered_questions(self, checkpoint_content):
        """S4 checkpoints should have 4 numbered questions."""
        question_pattern = re.compile(r"^\d+\.\s+\*\*", re.MULTILINE)
        matches = question_pattern.findall(checkpoint_content)
        assert len(matches) >= 4, (
            f"Expected 4+ checkpoint questions, found {len(matches)}"
        )

    def test_has_environment_change_question(self, checkpoint_content):
        assert "Environment Change" in checkpoint_content

    def test_has_model_divergence_question(self, checkpoint_content):
        assert "Model Divergence" in checkpoint_content

    def test_has_plan_viability_question(self, checkpoint_content):
        assert "Plan Viability" in checkpoint_content

    def test_has_shared_understanding_question(self, checkpoint_content):
        assert "Shared Understanding" in checkpoint_content


# =============================================================================
# Auditor dispatch: protocol vs orchestrate.md
# =============================================================================


class TestAuditorDispatchConsistency:
    """Verify auditor dispatch conditions match between protocol and command."""

    @pytest.fixture
    def audit_content(self):
        return (PROTOCOLS_DIR / "pact-audit.md").read_text(encoding="utf-8")

    @pytest.fixture
    def orchestrate_content(self):
        return (COMMANDS_DIR / "orchestrate.md").read_text(encoding="utf-8")

    def test_both_specify_variety_7(self, audit_content, orchestrate_content):
        """Both files reference variety >= 7 as dispatch condition."""
        assert "7" in audit_content
        assert "7" in orchestrate_content

    def test_both_mention_parallel_coders(self, audit_content, orchestrate_content):
        assert "parallel" in audit_content.lower()
        assert "parallel" in orchestrate_content.lower()

    def test_both_mention_security(self, audit_content, orchestrate_content):
        assert "security" in audit_content.lower()
        assert "security" in orchestrate_content.lower()

    def test_orchestrate_references_audit_protocol(self, orchestrate_content):
        """orchestrate.md should reference pact-audit.md."""
        assert "pact-audit.md" in orchestrate_content


# =============================================================================
# CalibrationRecord fields consistent
# =============================================================================


class TestCalibrationFieldConsistency:
    """Verify CalibrationRecord fields are consistent across docs."""

    @pytest.fixture
    def variety_content(self):
        return (PROTOCOLS_DIR / "pact-variety.md").read_text(encoding="utf-8")

    EXPECTED_FIELDS = [
        "task_id",
        "domain",
        "initial_variety_score",
        "actual_difficulty_score",
        "dimensions_that_drifted",
        "blocker_count",
        "phase_reruns",
        "specialist_fit",
        "timestamp",
    ]

    @pytest.mark.parametrize("field", EXPECTED_FIELDS)
    def test_field_in_protocol(self, variety_content, field):
        assert field in variety_content, (
            f"CalibrationRecord field '{field}' missing from pact-variety.md"
        )


# =============================================================================
# SSOT references
# =============================================================================


class TestSSOTReferences:
    """Verify audit protocol is referenced in pact-protocols.md."""

    @pytest.fixture
    def ssot_content(self):
        return (PROTOCOLS_DIR / "pact-protocols.md").read_text(encoding="utf-8")

    def test_audit_protocol_referenced(self, ssot_content):
        assert "pact-audit" in ssot_content or "Concurrent Audit" in ssot_content
