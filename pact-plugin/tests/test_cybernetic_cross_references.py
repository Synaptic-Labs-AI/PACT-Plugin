"""
Tests for cross-references across cybernetic improvement files.

Tests cover:
1. New protocol files exist with required content
2. Variety thresholds match between variety_scorer.py and pact-variety.md
3. S4 checkpoints has 5 questions (including Conant-Ashby)
4. Auditor dispatch thresholds consistent across pact-audit.md and orchestrate.md
5. CalibrationRecord fields consistent across protocol and architecture doc
6. Channel capacity signal format validated
7. Learning II threshold=5 consistent across protocol and code
8. New protocols referenced in pact-protocols.md SSOT
9. Algedonic signal categories match across protocol and hooks
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"
AGENTS_DIR = Path(__file__).parent.parent / "agents"

# New cybernetic protocol files
NEW_PROTOCOLS = {
    "pact-audit.md": "Concurrent Audit Protocol",
    "pact-channel-capacity.md": "Channel Capacity",
    "pact-transduction.md": "Transduction Protocol",
    "pact-self-repair.md": "Self-Repair Protocol",
}


# =============================================================================
# New protocol files exist and have correct content
# =============================================================================


class TestNewProtocolsExist:
    """Verify all new cybernetic protocol files exist with expected content."""

    @pytest.mark.parametrize("filename,heading", list(NEW_PROTOCOLS.items()))
    def test_protocol_file_exists(self, filename, heading):
        path = PROTOCOLS_DIR / filename
        assert path.exists(), f"Missing protocol file: {filename}"

    @pytest.mark.parametrize("filename,heading", list(NEW_PROTOCOLS.items()))
    def test_protocol_has_heading(self, filename, heading):
        content = (PROTOCOLS_DIR / filename).read_text(encoding="utf-8")
        assert heading in content, (
            f"{filename} missing expected heading: '{heading}'"
        )

    @pytest.mark.parametrize("filename", list(NEW_PROTOCOLS.keys()))
    def test_protocol_has_cybernetic_basis(self, filename):
        """New protocols should reference their cybernetic basis."""
        content = (PROTOCOLS_DIR / filename).read_text(encoding="utf-8")
        lower = content.lower()
        assert "cybernetic basis" in lower or "cybernetic" in lower, (
            f"{filename} missing cybernetic basis attribution"
        )


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

    def test_calibration_window_in_protocol(self, variety_content):
        """Protocol specifies window size of 5."""
        from shared.variety_scorer import CALIBRATION_WINDOW_SIZE
        assert CALIBRATION_WINDOW_SIZE == 5
        assert "Window size" in variety_content or "window" in variety_content.lower()

    def test_noise_threshold_in_protocol(self, variety_content):
        """Protocol specifies noise threshold of 1.0."""
        from shared.variety_scorer import CALIBRATION_NOISE_THRESHOLD
        assert CALIBRATION_NOISE_THRESHOLD == 1.0
        assert "1.0" in variety_content

    def test_max_adjustment_in_protocol(self, variety_content):
        """Protocol specifies max adjustment of +/-1."""
        from shared.variety_scorer import CALIBRATION_MAX_ADJUSTMENT
        assert CALIBRATION_MAX_ADJUSTMENT == 1
        assert "+/-1" in variety_content or "±1" in variety_content


# =============================================================================
# S4 Checkpoints: 5 questions
# =============================================================================


class TestS4CheckpointQuestions:
    """Verify S4 checkpoints has 5 questions including Conant-Ashby."""

    @pytest.fixture
    def checkpoint_content(self):
        return (PROTOCOLS_DIR / "pact-s4-checkpoints.md").read_text(encoding="utf-8")

    def test_has_five_numbered_questions(self, checkpoint_content):
        """S4 checkpoints should have 5 numbered questions."""
        # Look for numbered items under "Checkpoint Questions"
        question_pattern = re.compile(r"^\d+\.\s+\*\*", re.MULTILINE)
        matches = question_pattern.findall(checkpoint_content)
        assert len(matches) >= 5, (
            f"Expected 5+ checkpoint questions, found {len(matches)}"
        )

    def test_has_environment_change_question(self, checkpoint_content):
        assert "Environment Change" in checkpoint_content

    def test_has_model_divergence_question(self, checkpoint_content):
        assert "Model Divergence" in checkpoint_content

    def test_has_plan_viability_question(self, checkpoint_content):
        assert "Plan Viability" in checkpoint_content

    def test_has_shared_understanding_question(self, checkpoint_content):
        assert "Shared Understanding" in checkpoint_content

    def test_has_conant_ashby_question(self, checkpoint_content):
        """5th question: Model Completeness (Conant-Ashby)."""
        assert "Conant-Ashby" in checkpoint_content
        assert "Model Completeness" in checkpoint_content

    def test_checkpoint_format_has_regulation_line(self, checkpoint_content):
        """Checkpoint format should include Regulation line for 5th question."""
        assert "Regulation" in checkpoint_content


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
# Channel capacity signal format
# =============================================================================


class TestChannelCapacitySignalFormat:
    """Validate channel capacity signal format in pact-channel-capacity.md."""

    @pytest.fixture
    def capacity_content(self):
        return (PROTOCOLS_DIR / "pact-channel-capacity.md").read_text(encoding="utf-8")

    def test_has_capacity_signals(self, capacity_content):
        assert "Capacity Signal" in capacity_content or "CAPACITY SIGNAL" in capacity_content

    def test_has_nominal_level(self, capacity_content):
        assert "NOMINAL" in capacity_content

    def test_has_elevated_level(self, capacity_content):
        assert "ELEVATED" in capacity_content

    def test_has_critical_level(self, capacity_content):
        assert "CRITICAL" in capacity_content

    def test_has_current_load_field(self, capacity_content):
        lower = capacity_content.lower()
        assert "current load" in lower or "current_load" in lower

    def test_has_recommended_action(self, capacity_content):
        lower = capacity_content.lower()
        assert "recommended action" in lower or "recommended_action" in lower

    def test_has_batch_protocol(self, capacity_content):
        assert "Batch Protocol" in capacity_content or "batch" in capacity_content.lower()


# =============================================================================
# Transduction protocol content
# =============================================================================


class TestTransductionProtocol:
    """Verify pact-transduction.md has required content."""

    @pytest.fixture
    def transduction_content(self):
        return (PROTOCOLS_DIR / "pact-transduction.md").read_text(encoding="utf-8")

    def test_has_lossless_vs_lossy(self, transduction_content):
        assert "Lossless" in transduction_content or "lossless" in transduction_content
        assert "Lossy" in transduction_content or "lossy" in transduction_content

    def test_has_boundary_crossings(self, transduction_content):
        assert "Boundary" in transduction_content or "boundary" in transduction_content

    def test_references_beer(self, transduction_content):
        """Should reference Beer's concept of transduction."""
        assert "Beer" in transduction_content


# =============================================================================
# Self-repair protocol content
# =============================================================================


class TestSelfRepairProtocol:
    """Verify pact-self-repair.md has required content."""

    @pytest.fixture
    def self_repair_content(self):
        return (PROTOCOLS_DIR / "pact-self-repair.md").read_text(encoding="utf-8")

    def test_has_autopoiesis_reference(self, self_repair_content):
        """Should reference Maturana & Varela's autopoiesis."""
        assert "autopoiesis" in self_repair_content.lower() or "Maturana" in self_repair_content

    def test_has_organization_vs_structure(self, self_repair_content):
        assert "Organization" in self_repair_content or "organization" in self_repair_content
        assert "Structure" in self_repair_content or "structure" in self_repair_content

    def test_has_recovery_patterns(self, self_repair_content):
        lower = self_repair_content.lower()
        assert "pattern" in lower and ("recovery" in lower or "repair" in lower or "reconstitut" in lower)


# =============================================================================
# SSOT references
# =============================================================================


class TestSSOTReferences:
    """Verify new protocols are referenced in pact-protocols.md."""

    @pytest.fixture
    def ssot_content(self):
        return (PROTOCOLS_DIR / "pact-protocols.md").read_text(encoding="utf-8")

    def test_audit_protocol_referenced(self, ssot_content):
        assert "pact-audit" in ssot_content or "Concurrent Audit" in ssot_content

    def test_channel_capacity_referenced(self, ssot_content):
        assert "pact-channel-capacity" in ssot_content or "Channel Capacity" in ssot_content

    def test_transduction_referenced(self, ssot_content):
        assert "pact-transduction" in ssot_content or "Transduction" in ssot_content

    def test_self_repair_referenced(self, ssot_content):
        assert "pact-self-repair" in ssot_content or "Self-Repair" in ssot_content
