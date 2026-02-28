"""
Tests for Learning II pattern recognition in pact-variety.md.

Tests cover:
1. Learning II section exists
2. Memory search convention documented
3. Score adjustment rule documented
"""
from pathlib import Path

import pytest


VARIETY_PATH = Path(__file__).parent.parent / "protocols" / "pact-variety.md"


class TestLearningIIPatterns:
    """Tests for recurring pattern recognition in variety protocol."""

    @pytest.fixture
    def variety_content(self):
        return VARIETY_PATH.read_text(encoding="utf-8")

    def test_learning_ii_section_exists(self, variety_content):
        assert "### Learning II: Pattern-Adjusted Scoring" in variety_content

    def test_memory_search_convention(self, variety_content):
        assert "orchestration_calibration" in variety_content

    def test_score_adjustment_rule(self, variety_content):
        assert "3+ memories match" in variety_content or "3+" in variety_content
