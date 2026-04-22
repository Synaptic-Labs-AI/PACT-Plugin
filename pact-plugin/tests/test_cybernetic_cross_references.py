"""
Tests for cross-references across cybernetic improvement files.

Tests cover:
1. Audit protocol file exists with required content
2. Variety thresholds match between variety_scorer.py and pact-variety.md
3. S4 checkpoints has 4 questions
4. Auditor dispatch thresholds and opt-out framing consistent across all dispatch files
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
    """Verify auditor dispatch conditions match across all files with dispatch sections."""

    # All files that define auditor dispatch conditions
    DISPATCH_FILES = {
        "pact-audit.md": PROTOCOLS_DIR / "pact-audit.md",
        "orchestrate.md": COMMANDS_DIR / "orchestrate.md",
        "comPACT.md": COMMANDS_DIR / "comPACT.md",
        "pact-workflows.md": PROTOCOLS_DIR / "pact-workflows.md",
        "pact-protocols.md": PROTOCOLS_DIR / "pact-protocols.md",
    }

    @pytest.fixture
    def audit_content(self):
        return (PROTOCOLS_DIR / "pact-audit.md").read_text(encoding="utf-8")

    @pytest.fixture
    def orchestrate_content(self):
        return (COMMANDS_DIR / "orchestrate.md").read_text(encoding="utf-8")

    @pytest.fixture
    def dispatch_contents(self):
        """Load all files that contain auditor dispatch sections."""
        return {
            name: path.read_text(encoding="utf-8")
            for name, path in self.DISPATCH_FILES.items()
        }

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

    # --- Opt-out framing consistency across all dispatch files ---

    @pytest.mark.parametrize("filename", sorted(DISPATCH_FILES.keys()))
    def test_all_files_use_opt_out_skip_framing(self, dispatch_contents, filename):
        """All auditor dispatch files use opt-out 'skip' framing."""
        content = dispatch_contents[filename].lower()
        assert "skip" in content, (
            f"{filename} missing 'skip' — auditor dispatch must use opt-out framing"
        )

    @pytest.mark.parametrize("filename", sorted(DISPATCH_FILES.keys()))
    def test_all_files_mention_justification(self, dispatch_contents, filename):
        """All auditor dispatch files require justification to skip."""
        content = dispatch_contents[filename].lower()
        assert "justification" in content, (
            f"{filename} missing 'justification' — skip must require justification"
        )


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


# =============================================================================
# Structural Verification Discipline (#502) — cross-file cascade
# =============================================================================


class TestStructuralVerificationDisciplineConsistency:
    """Verify the STRUCTURAL VERIFICATION DISCIPLINE terminology cascades to every
    auditor-touching file: dispatch sites, protocol anchor, SSOT, and agent body.

    Reuses `TestAuditorDispatchConsistency.DISPATCH_FILES` via dict-spread so this
    inventory cannot drift from the dispatch-consistency inventory. Adds
    `pact-auditor.md` (the agent body) which is not a dispatch site but is the
    primary rule-carrying surface at auditor execution time.

    A failing parametrized case names the specific file where the cascade broke —
    localizing future regressions to the exact drop site.
    """

    # Extends the dispatch inventory with the agent body (rule-carrying surface).
    # Dict-spread keeps DISPATCH_FILES as the single source of truth.
    DISCIPLINE_FILES = {
        **TestAuditorDispatchConsistency.DISPATCH_FILES,
        "pact-auditor.md": AGENTS_DIR / "pact-auditor.md",
    }

    @pytest.fixture
    def discipline_contents(self):
        return {
            name: path.read_text(encoding="utf-8")
            for name, path in self.DISCIPLINE_FILES.items()
        }

    @pytest.mark.parametrize("filename", sorted(DISCIPLINE_FILES.keys()))
    def test_all_files_reference_structural_verification_discipline(
        self, discipline_contents, filename
    ):
        """Every auditor-touching file carries the discipline terminology.

        Agent body and pact-audit.md / pact-protocols.md use the full discipline
        name as a section heading; dispatch-site files use a one-line pointer.
        All must mention 'Structural Verification Discipline' (case-insensitive).
        """
        content = discipline_contents[filename]
        lower = content.lower()
        assert "structural verification discipline" in lower, (
            f"{filename} missing 'Structural Verification Discipline' — "
            f"discipline terminology must cascade to all auditor-touching files "
            f"(see #502)"
        )

    @pytest.mark.parametrize("filename", sorted(DISCIPLINE_FILES.keys()))
    def test_discipline_reference_colocated_with_git_diff(
        self, discipline_contents, filename
    ):
        """Each file pairs the discipline terminology with 'git diff' as its substrate.

        Proximity check: within a 500-char window of every 'structural verification
        discipline' occurrence, 'git diff' MUST also appear. Catches the
        degenerate case where the discipline name is mentioned but its ground-truth
        requirement (git diff as substrate) is disconnected — rule carried by name
        without its substrate. A blunt file-wide 'git diff' check passes on
        pre-#502 baseline because pre-#502 SIGNAL FORMAT already contained 'git diff
        excerpt' as a vague alternate; only colocation with the discipline name is
        a #502-specific invariant.
        """
        content = discipline_contents[filename]
        lower = content.lower()
        idx = lower.find("structural verification discipline")
        assert idx != -1, (
            f"{filename} missing discipline terminology — "
            f"upstream cascade test should have caught this"
        )
        # Scan every discipline mention; at least one must be within 500 chars
        # of a 'git diff' reference.
        window = 500
        found_pairing = False
        while idx != -1:
            start = max(0, idx - window)
            end = min(len(lower), idx + len("structural verification discipline") + window)
            if "git diff" in lower[start:end]:
                found_pairing = True
                break
            idx = lower.find("structural verification discipline", idx + 1)
        assert found_pairing, (
            f"{filename} mentions 'Structural Verification Discipline' but no "
            f"'git diff' reference within {window} chars — rule name is carried "
            f"without its ground-truth substrate (see #502)"
        )

    # Dispatch-pointer consumer files only. Rule-bearing files (pact-audit.md,
    # pact-auditor.md, pact-protocols.md) are excluded — the dispatch-anchor
    # proximity invariant is only meaningful where the discipline phrase is
    # delivering spawn-time priming within an auditor-dispatch block, not
    # where it is the rule body itself or the SSOT of the rule body.
    DISPATCH_POINTER_FILES = [
        "orchestrate.md",
        "comPACT.md",
        "pact-workflows.md",
    ]

    @pytest.mark.parametrize("filename", DISPATCH_POINTER_FILES)
    def test_discipline_pointer_near_auditor_dispatch_anchor(
        self, discipline_contents, filename
    ):
        """Discipline phrase sits within the auditor-dispatch block.

        Proximity check: at each dispatch-pointer consumer file, the
        'Structural Verification Discipline' phrase MUST appear within 2000
        chars of an 'Auditor skipped' anchor. The anchor is the shared
        opt-out marker at the top of every auditor-dispatch block; proximity
        to it is what makes the phrase act as dispatch priming rather than
        incidental mention elsewhere in the file.

        A refactor that moves the discipline phrase to an unrelated section
        (e.g., a 'Principles' appendix) would still pass
        `test_all_files_reference_structural_verification_discipline` but
        silently break the dispatch-priming property the runtime-behavior
        trace in docs/review/502-backend-coder.md depends on.

        Window sized at ~3x the maximum measured actual (610 chars in
        orchestrate.md at time of landing); leaves headroom for dispatch-block
        expansion without false failures.
        """
        content = discipline_contents[filename]
        lower = content.lower()
        window = 2000

        anchor_idx = 0
        found_pairing = False
        while True:
            anchor_idx = lower.find("auditor skipped", anchor_idx)
            if anchor_idx < 0:
                break
            start = max(0, anchor_idx - window)
            end = min(
                len(lower),
                anchor_idx + len("auditor skipped") + window,
            )
            if "structural verification discipline" in lower[start:end]:
                found_pairing = True
                break
            anchor_idx += 1

        assert found_pairing, (
            f"{filename} contains 'Auditor skipped' anchor but no "
            f"'Structural Verification Discipline' phrase within {window} "
            f"chars — dispatch-priming property broken. A future refactor "
            f"may have moved the phrase out of the dispatch block (see #502 F7)."
        )
