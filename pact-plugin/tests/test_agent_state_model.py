"""
Tests for agent state model in pact-variety.md.

Tests cover:
1. Agent State Model section exists
2. Three states defined (Converging, Exploring, Stuck)
3. State transitions documented
"""
from pathlib import Path

import pytest


VARIETY_PATH = Path(__file__).parent.parent / "protocols" / "pact-variety.md"


class TestAgentStateModel:
    """Tests for agent state model in variety protocol."""

    @pytest.fixture
    def variety_content(self):
        return VARIETY_PATH.read_text(encoding="utf-8")

    def test_agent_state_model_section_exists(self, variety_content):
        assert "### Agent State Model" in variety_content

    def test_three_states_defined(self, variety_content):
        assert "**Converging**" in variety_content
        assert "**Exploring**" in variety_content
        assert "**Stuck**" in variety_content

    def test_state_transitions_documented(self, variety_content):
        assert "State transitions" in variety_content
        assert "Exploring → Converging" in variety_content
