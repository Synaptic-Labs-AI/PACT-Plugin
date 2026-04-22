"""
Tests for the pact-audit.md protocol and pact-auditor agent definition.

Tests cover:
1. Audit signal format validation (GREEN/YELLOW/RED with required fields)
2. Audit request format (dispatch conditions, completion lifecycle)
3. Auditor dispatch threshold consistency (protocol vs orchestrate.md)
4. Auditor agent definition structure
5. Completion lifecycle: signal-type with audit_summary
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"
AGENTS_DIR = Path(__file__).parent.parent / "agents"

AUDIT_PROTOCOL = PROTOCOLS_DIR / "pact-audit.md"
ORCHESTRATE_CMD = COMMANDS_DIR / "orchestrate.md"
AUDITOR_AGENT = AGENTS_DIR / "pact-auditor.md"


# =============================================================================
# Audit protocol structure
# =============================================================================


class TestAuditProtocolExists:
    """Verify pact-audit.md exists and has required content."""

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    def test_protocol_file_exists(self):
        assert AUDIT_PROTOCOL.exists()

    def test_has_concurrent_audit_heading(self, audit_content):
        # Wiring check: protocol must define the concurrent audit section
        assert "Concurrent Audit Protocol" in audit_content

    def test_has_dispatch_conditions(self, audit_content):
        # Wiring check: protocol must specify when to deploy auditor
        assert "Dispatch Conditions" in audit_content

    def test_has_observation_model(self, audit_content):
        # Wiring check: protocol must define observation approach
        assert "Hybrid Observation Model" in audit_content or "Observation" in audit_content

    def test_has_signal_format(self, audit_content):
        # Wiring check: protocol must define how auditor reports findings
        assert "Signal Format" in audit_content

    def test_has_signal_levels(self, audit_content):
        # Wiring check: protocol must define signal severity tiers
        assert "Signal Levels" in audit_content

    def test_has_completion_lifecycle(self, audit_content):
        # Wiring check: protocol must define signal-type completion
        assert "Completion Lifecycle" in audit_content or "completion_type" in audit_content


# =============================================================================
# Signal format validation
# =============================================================================


class TestAuditSignalFormat:
    """Validate audit signal format matches spec."""

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    def test_signal_levels_present(self, audit_content):
        # Wiring check: protocol must define all three signal levels
        assert "GREEN" in audit_content
        assert "YELLOW" in audit_content
        assert "RED" in audit_content

    def test_signal_format_has_reference_field(self, audit_content):
        # Wiring check: signal must include file/component reference
        assert "Reference" in audit_content

    def test_signal_format_has_scope_field(self, audit_content):
        # Wiring check: signal must specify observation scope
        assert "Scope" in audit_content

    def test_signal_format_has_finding_field(self, audit_content):
        # Wiring check: signal must describe the finding
        assert "Finding" in audit_content

    def test_signal_format_has_evidence_field(self, audit_content):
        # Wiring check: signal must cite evidence
        assert "Evidence" in audit_content

    def test_signal_format_has_action_field(self, audit_content):
        # Wiring check: signal must recommend action
        assert "Action" in audit_content

    def test_green_means_on_track(self, audit_content):
        """GREEN signal means implementation is on track."""
        # The protocol should define GREEN as meaning on-track
        lower = audit_content.lower()
        assert "green" in lower and "on track" in lower

    def test_red_means_intervene(self, audit_content):
        """RED signal means orchestrator should intervene."""
        lower = audit_content.lower()
        assert "red" in lower and "intervene" in lower


# =============================================================================
# Dispatch threshold consistency
# =============================================================================


class TestDispatchThresholdConsistency:
    """Verify auditor dispatch conditions match between protocol and orchestrate.md."""

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    @pytest.fixture
    def orchestrate_content(self):
        return ORCHESTRATE_CMD.read_text(encoding="utf-8")

    def test_protocol_has_variety_threshold(self, audit_content):
        """Protocol specifies variety >= 7 as dispatch condition."""
        # Wiring check: variety threshold must be defined
        assert ">= 7" in audit_content or "7" in audit_content

    def test_protocol_has_parallel_coders_condition(self, audit_content):
        """Protocol specifies parallel coders as dispatch condition."""
        # Wiring check: parallel-coders condition must be defined
        lower = audit_content.lower()
        assert "parallel" in lower and "coder" in lower

    def test_protocol_has_security_condition(self, audit_content):
        """Protocol specifies security-sensitive code as dispatch condition."""
        # Wiring check: security condition must be defined
        lower = audit_content.lower()
        assert "security" in lower

    def test_orchestrate_references_auditor(self, orchestrate_content):
        """orchestrate.md includes auditor dispatch instructions."""
        # Wiring check: orchestrate must reference auditor agent
        assert "pact-auditor" in orchestrate_content

    def test_orchestrate_has_variety_threshold(self, orchestrate_content):
        """orchestrate.md specifies variety >= 7 for auditor dispatch."""
        # Wiring check: orchestrate threshold must match protocol
        assert "variety >= 7" in orchestrate_content or "variety 7" in orchestrate_content.lower()

    def test_orchestrate_has_completion_type_signal(self, orchestrate_content):
        """orchestrate.md sets completion_type: signal for auditor tasks."""
        # Wiring check: orchestrate must use signal-type completion for auditor
        assert "completion_type" in orchestrate_content
        assert "signal" in orchestrate_content


# =============================================================================
# Auditor agent definition
# =============================================================================


class TestAuditorAgentDefinition:
    """Verify pact-auditor.md agent definition structure."""

    @pytest.fixture
    def agent_content(self):
        return AUDITOR_AGENT.read_text(encoding="utf-8")

    def test_agent_file_exists(self):
        assert AUDITOR_AGENT.exists()

    def test_has_frontmatter_name(self, agent_content):
        # Wiring check: agent must declare correct name in frontmatter
        assert "name: pact-auditor" in agent_content

    def test_has_observation_protocol(self, agent_content):
        """Agent has observation phases (A, B, C)."""
        # Wiring check: agent must define all three observation phases
        assert "Phase A" in agent_content
        assert "Phase B" in agent_content
        assert "Phase C" in agent_content

    def test_has_behavioral_rules(self, agent_content):
        # Wiring check: agent must include behavioral constraints
        lower = agent_content.lower()
        assert "behavioral rules" in lower or "behavioural rules" in lower

    def test_has_audit_criteria(self, agent_content):
        # Wiring check: agent must define what it evaluates
        assert "AUDIT CRITERIA" in agent_content or "Audit Criteria" in agent_content

    def test_has_signal_format(self, agent_content):
        # Wiring check: agent must define its output format
        assert "Signal Format" in agent_content or "AUDIT SIGNAL" in agent_content

    def test_has_completion_section(self, agent_content):
        # Wiring check: agent must define signal-type completion lifecycle
        assert "COMPLETION" in agent_content or "Completion" in agent_content
        assert "completion_type" in agent_content or "audit_summary" in agent_content

    def test_has_algedonic_escalation(self, agent_content):
        # Wiring check: agent must reference algedonic escalation path
        assert "algedonic" in agent_content.lower()

    def test_does_not_write_code_boundary(self, agent_content):
        """Agent explicitly states it does not write code."""
        # Wiring check: read-only observer boundary must be stated
        lower = agent_content.lower()
        assert "do not write" in lower or "do not modify" in lower


# =============================================================================
# Completion lifecycle: signal-type
# =============================================================================


class TestCompletionLifecycle:
    """Verify signal-type completion lifecycle works with completion gate."""

    def test_signal_type_with_audit_summary_is_completable(self, tmp_path):
        """Signal-type task with audit_summary classified as completable."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        task_data = {
            "id": "42",
            "subject": "auditor observation",
            "status": "in_progress",
            "owner": "auditor",
            "metadata": {
                "completion_type": "signal",
                "audit_summary": {
                    "signal": "GREEN",
                    "findings": [],
                    "scope": "all coders",
                },
            },
        }
        (task_dir / "42.json").write_text(json.dumps(task_data), encoding="utf-8")

        completable, missing = _scan_owned_tasks("auditor", "pact-test", str(tmp_path))
        assert len(completable) == 1
        assert completable[0]["id"] == "42"
        assert completable[0]["completion_type"] == "signal"
        assert len(missing) == 0

    def test_signal_type_without_audit_summary_is_missing(self, tmp_path):
        """Signal-type task without audit_summary classified as missing."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        task_data = {
            "id": "42",
            "subject": "auditor observation",
            "status": "in_progress",
            "owner": "auditor",
            "metadata": {
                "completion_type": "signal",
            },
        }
        (task_dir / "42.json").write_text(json.dumps(task_data), encoding="utf-8")

        completable, missing = _scan_owned_tasks("auditor", "pact-test", str(tmp_path))
        assert len(completable) == 0
        assert len(missing) == 1
        assert missing[0]["completion_type"] == "signal"

    def test_default_completion_type_is_handoff(self, tmp_path):
        """Task without completion_type defaults to handoff behavior."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        task_data = {
            "id": "42",
            "subject": "backend work",
            "status": "in_progress",
            "owner": "coder",
            "metadata": {
                "handoff": {"produced": ["file.py"]},
            },
        }
        (task_dir / "42.json").write_text(json.dumps(task_data), encoding="utf-8")

        completable, missing = _scan_owned_tasks("coder", "pact-test", str(tmp_path))
        assert len(completable) == 1
        assert completable[0]["completion_type"] == "handoff"


# =============================================================================
# Structural Verification Discipline (#502)
# =============================================================================


class TestStructuralVerificationDiscipline:
    """Verify STRUCTURAL VERIFICATION DISCIPLINE is present in agent body and protocol.

    Each assertion targets a specific load-bearing property of the discipline
    (canonical term, MUST voice, named failure modes, prior-art citation, incident
    citation, Evidence-field upgrade, BEHAVIORAL RULES integration). A failing
    assertion names the specific erosion shape rather than a generic "rule missing".
    Baseline-fail on b0e3f7e is required — these tests do not pass on pre-#502 HEAD.
    """

    @pytest.fixture
    def agent_content(self):
        return AUDITOR_AGENT.read_text(encoding="utf-8")

    @pytest.fixture
    def audit_content(self):
        return AUDIT_PROTOCOL.read_text(encoding="utf-8")

    def test_agent_has_discipline_section(self, agent_content):
        """Agent body declares the discipline section by canonical name."""
        assert "STRUCTURAL VERIFICATION DISCIPLINE" in agent_content

    def test_protocol_has_discipline_section(self, audit_content):
        """Protocol anchor mirrors the discipline section (H3-cased)."""
        assert "Structural Verification Discipline" in audit_content

    def test_agent_uses_must_voice(self, agent_content):
        """Discipline section uses MUST voice, not 'should' or 'recommended'."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "MUST" in section, "Discipline section must use MUST voice"

    def test_agent_references_git_diff_verification(self, agent_content):
        """Discipline requires git diff as the verification substrate."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "git diff" in section

    def test_agent_names_failure_modes(self, agent_content):
        """Discipline names the canonical failure modes for LLM-reader at execution time."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "PHANTOM-SYMMETRIC-CLAIM" in section
        assert "VAGUE-DIFF-CITATION" in section
        assert "STRUCTURAL-DRESSING-ON-JUDGMENT-CALL" in section

    def test_agent_cites_prior_art(self, agent_content):
        """Discipline cites file inspection beats HANDOFF inference rationale."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "file inspection beats HANDOFF inference" in section

    def test_agent_cites_pr_501_incident(self, agent_content):
        """Discipline cites the triggering incident so the rule's context survives compaction."""
        start = agent_content.index("STRUCTURAL VERIFICATION DISCIPLINE")
        end = agent_content.index("## SIGNAL FORMAT", start)
        section = agent_content[start:end]
        assert "bef7f24" in section or "PR #501" in section

    def test_agent_signal_format_evidence_field_upgraded(self, agent_content):
        """Agent's Evidence field requires structural ACs be cited via git diff."""
        start = agent_content.index("## SIGNAL FORMAT")
        end = agent_content.index("## SIGNAL LEVELS", start)
        section = agent_content[start:end]
        assert "structural" in section.lower()
        assert "git diff" in section

    def test_protocol_signal_format_evidence_field_upgraded(self, audit_content):
        """Protocol's Evidence field requires structural ACs be cited via git diff."""
        start = audit_content.index("### Signal Format")
        end = audit_content.index("### Signal Levels", start)
        section = audit_content[start:end]
        assert "structural" in section.lower()
        assert "git diff" in section

    def test_agent_has_behavioral_rule_for_structural_verification(self, agent_content):
        """BEHAVIORAL RULES table references the discipline."""
        start = agent_content.index("## BEHAVIORAL RULES")
        end = agent_content.index("## AUDIT CRITERIA", start)
        section = agent_content[start:end]
        assert "STRUCTURAL VERIFICATION DISCIPLINE" in section

    def test_verify_protocol_extracts_concurrent_audit_still_matches(self):
        """verify-protocol-extracts.sh exits 0 with every extract pair MATCH.

        Strict-pass semantics: asserts exit-code 0 from the verify script. Every
        extract pair — including Concurrent Audit (edited by #502) and State
        Recovery (re-extracted by #505) — must be in sync with its SSOT region.
        This test fails deterministically if any future edit to pact-protocols.md
        or any extract file desyncs the pair without bumping the script's
        line-ranges, or if the cascade-delta rule is violated when inserting
        content into the SSOT.
        """
        import subprocess

        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts" / "verify-protocol-extracts.sh"
        if not script.exists():
            pytest.skip("verify-protocol-extracts.sh not present")

        result = subprocess.run(
            ["bash", str(script)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"verify-protocol-extracts.sh exited {result.returncode}; expected 0. "
            f"One or more extract pairs have desynced from their SSOT region.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
