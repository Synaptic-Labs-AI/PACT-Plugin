"""
Tests for cybernetics cross-references across PACT command and protocol files.

Tests cover:
L2. Conversation Failure Taxonomy in pact-workflows.md
L3. Progress monitoring dispatch instructions in orchestrate.md, comPACT.md, pact-workflows.md
L4. Environment drift cross-references in orchestrate.md, comPACT.md
L5. Review calibration save step in peer-review.md
L6. Agent state model cross-reference in pact-agent-stall.md
"""
from pathlib import Path

import pytest


PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"

WORKFLOWS_PATH = PROTOCOLS_DIR / "pact-workflows.md"
ORCHESTRATE_PATH = COMMANDS_DIR / "orchestrate.md"
COMPACT_PATH = COMMANDS_DIR / "comPACT.md"
PEER_REVIEW_PATH = COMMANDS_DIR / "peer-review.md"
AGENT_STALL_PATH = PROTOCOLS_DIR / "pact-agent-stall.md"


class TestConversationFailureTaxonomy:
    """L2: Conversation Failure Taxonomy exists in pact-workflows.md."""

    @pytest.fixture
    def workflows_content(self):
        return WORKFLOWS_PATH.read_text(encoding="utf-8")

    def test_taxonomy_section_exists(self, workflows_content):
        assert "Conversation Failure Taxonomy" in workflows_content

    def test_taxonomy_types_present(self, workflows_content):
        assert "Misunderstanding" in workflows_content
        assert "Derailment" in workflows_content
        assert "Discontinuity" in workflows_content
        assert "Absence" in workflows_content


class TestProgressMonitoringDispatch:
    """L3: Progress monitoring dispatch instructions in key files."""

    @pytest.fixture
    def orchestrate_content(self):
        return ORCHESTRATE_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def compact_content(self):
        return COMPACT_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def workflows_content(self):
        return WORKFLOWS_PATH.read_text(encoding="utf-8")

    def test_orchestrate_has_progress_monitoring(self, orchestrate_content):
        assert "progress monitoring" in orchestrate_content.lower()

    def test_compact_has_progress_monitoring(self, compact_content):
        assert "Send progress signals" in compact_content

    def test_workflows_has_progress_signals(self, workflows_content):
        assert "Send progress signals" in workflows_content


class TestEnvironmentDriftReferences:
    """L4: Environment drift cross-references in key files."""

    @pytest.fixture
    def orchestrate_content(self):
        return ORCHESTRATE_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def compact_content(self):
        return COMPACT_PATH.read_text(encoding="utf-8")

    def test_orchestrate_has_environment_drift(self, orchestrate_content):
        content_lower = orchestrate_content.lower()
        assert "environment drift" in content_lower

    def test_compact_has_environment_drift(self, compact_content):
        assert "Environment drift" in compact_content or "file-edits.json" in compact_content


class TestReviewCalibration:
    """L5: Review calibration save step in peer-review.md."""

    @pytest.fixture
    def peer_review_content(self):
        return PEER_REVIEW_PATH.read_text(encoding="utf-8")

    def test_peer_review_has_review_calibration(self, peer_review_content):
        assert "review_calibration" in peer_review_content


class TestAgentStallCrossReference:
    """L6: Agent state model cross-reference in pact-agent-stall.md."""

    @pytest.fixture
    def stall_content(self):
        return AGENT_STALL_PATH.read_text(encoding="utf-8")

    def test_stall_has_agent_state_model_reference(self, stall_content):
        assert "agent state model" in stall_content.lower()
