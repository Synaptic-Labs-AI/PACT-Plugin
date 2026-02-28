"""
Tests for orchestration retrospective in wrap-up.md.

Tests cover:
1. Orchestration Retrospective section exists
2. Four assessment questions defined
3. pact-memory save convention documented
"""
from pathlib import Path

import pytest


WRAPUP_PATH = Path(__file__).parent.parent / "commands" / "wrap-up.md"


class TestOrchestrationRetrospective:
    """Tests for orchestration retrospective in wrap-up command."""

    @pytest.fixture
    def wrapup_content(self):
        return WRAPUP_PATH.read_text(encoding="utf-8")

    def test_retrospective_section_exists(self, wrapup_content):
        assert "Orchestration Retrospective" in wrapup_content

    def test_variety_accuracy_question(self, wrapup_content):
        assert "Variety accuracy" in wrapup_content

    def test_phase_efficiency_question(self, wrapup_content):
        assert "Phase efficiency" in wrapup_content

    def test_specialist_fit_question(self, wrapup_content):
        assert "Specialist fit" in wrapup_content

    def test_memory_save_convention(self, wrapup_content):
        assert "orchestration_calibration" in wrapup_content
