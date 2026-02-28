"""
Tests for agent progress signals convention in pact-agent-teams SKILL.md.

Tests cover:
1. Progress Signals section exists in SKILL.md
2. Format specification is present
3. Natural breakpoints are defined
4. Timing guidance is included
"""
from pathlib import Path

import pytest


SKILL_PATH = Path(__file__).parent.parent / "skills" / "pact-agent-teams" / "SKILL.md"


class TestProgressSignals:
    """Tests for progress signals convention in agent-teams skill."""

    @pytest.fixture
    def skill_content(self):
        return SKILL_PATH.read_text(encoding="utf-8")

    def test_progress_signals_section_exists(self, skill_content):
        assert "### Progress Signals" in skill_content

    def test_format_specification_present(self, skill_content):
        assert "[sender→lead] Progress:" in skill_content

    def test_natural_breakpoints_defined(self, skill_content):
        assert "Natural breakpoints" in skill_content
        assert "After modifying a file" in skill_content
        assert "After running tests" in skill_content

    def test_timing_guidance_included(self, skill_content):
        assert "2-4 signals per task" in skill_content
