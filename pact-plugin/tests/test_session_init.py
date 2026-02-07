"""
Tests for session_init.py Agent Teams functions.

Tests _team_instruction() which generates Agent Teams instructions
for the orchestrator based on session source and team state.

Location: pact-plugin/tests/test_session_init.py
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from session_init import _team_instruction


# =============================================================================
# Tests for _team_instruction()
# =============================================================================

class TestTeamInstruction:
    """Tests for _team_instruction function."""

    # -------------------------------------------------------------------------
    # Branch unavailable (returns None)
    # -------------------------------------------------------------------------

    def test_returns_none_when_no_branch(self):
        """Test returns None when get_current_branch returns empty string."""
        with patch("session_init.get_current_branch", return_value=""):
            result = _team_instruction("new")

        assert result is None

    def test_returns_none_when_branch_empty_for_compact(self):
        """Test returns None for compact source when no branch available."""
        with patch("session_init.get_current_branch", return_value=""):
            result = _team_instruction("compact")

        assert result is None

    def test_returns_none_when_branch_empty_for_resume(self):
        """Test returns None for resume source when no branch available."""
        with patch("session_init.get_current_branch", return_value=""):
            result = _team_instruction("resume")

        assert result is None

    # -------------------------------------------------------------------------
    # source="compact" with team_exists=True
    # -------------------------------------------------------------------------

    def test_compact_team_exists_returns_survived_message(self):
        """Test compact source with existing team returns survived-compaction message."""
        with patch("session_init.get_current_branch", return_value="feature/v3-agent-teams"), \
             patch("session_init.derive_team_name", return_value="v3-agent-teams"), \
             patch("session_init.team_exists", return_value=True):
            result = _team_instruction("compact")

        assert result is not None
        assert "v3-agent-teams" in result
        assert "survived compaction" in result
        assert "SendMessage" in result
        assert "independent processes" in result

    # -------------------------------------------------------------------------
    # source="compact" with team_exists=False (falls through to creation)
    # -------------------------------------------------------------------------

    def test_compact_team_not_exists_falls_through_to_creation(self):
        """Test compact source with no team dir falls through to TeamCreate instruction."""
        with patch("session_init.get_current_branch", return_value="feature/v3-agent-teams"), \
             patch("session_init.derive_team_name", return_value="v3-agent-teams"), \
             patch("session_init.team_exists", return_value=False):
            result = _team_instruction("compact")

        assert result is not None
        assert "TeamCreate" in result
        assert "v3-agent-teams" in result
        assert "idempotent" in result

    # -------------------------------------------------------------------------
    # source="resume" with team_exists=True
    # -------------------------------------------------------------------------

    def test_resume_team_exists_returns_config_exists_message(self):
        """Test resume source with existing team config returns re-spawn message."""
        with patch("session_init.get_current_branch", return_value="feature/v3-agent-teams"), \
             patch("session_init.derive_team_name", return_value="v3-agent-teams"), \
             patch("session_init.team_exists", return_value=True):
            result = _team_instruction("resume")

        assert result is not None
        assert "v3-agent-teams" in result
        assert "config exists" in result
        assert "NOT running" in result
        assert "Re-spawn" in result

    # -------------------------------------------------------------------------
    # source="resume" with team_exists=False (falls through to creation)
    # -------------------------------------------------------------------------

    def test_resume_team_not_exists_falls_through_to_creation(self):
        """Test resume source with no team dir falls through to TeamCreate instruction."""
        with patch("session_init.get_current_branch", return_value="feature/v3-agent-teams"), \
             patch("session_init.derive_team_name", return_value="v3-agent-teams"), \
             patch("session_init.team_exists", return_value=False):
            result = _team_instruction("resume")

        assert result is not None
        assert "TeamCreate" in result
        assert "v3-agent-teams" in result

    # -------------------------------------------------------------------------
    # source="" (new session - default creation instruction)
    # -------------------------------------------------------------------------

    def test_new_session_returns_create_instruction(self):
        """Test new session (empty source) returns TeamCreate instruction."""
        with patch("session_init.get_current_branch", return_value="feature/v3-agent-teams"), \
             patch("session_init.derive_team_name", return_value="v3-agent-teams"), \
             patch("session_init.team_exists", return_value=False):
            result = _team_instruction("")

        assert result is not None
        assert "TeamCreate" in result
        assert "team_name='v3-agent-teams'" in result
        assert "idempotent" in result

    def test_new_session_ignores_team_exists_check(self):
        """Test new session does not branch on team_exists (goes to default)."""
        with patch("session_init.get_current_branch", return_value="main"), \
             patch("session_init.derive_team_name", return_value="main"), \
             patch("session_init.team_exists", return_value=True):
            result = _team_instruction("")

        # New session always falls through to the default creation instruction
        # because the "compact" and "resume" guards are not entered
        assert result is not None
        assert "TeamCreate" in result
        assert "main" in result

    # -------------------------------------------------------------------------
    # source="other" (unknown source - default creation instruction)
    # -------------------------------------------------------------------------

    def test_unknown_source_returns_create_instruction(self):
        """Test unknown source falls through to default TeamCreate instruction."""
        with patch("session_init.get_current_branch", return_value="develop"), \
             patch("session_init.derive_team_name", return_value="develop"), \
             patch("session_init.team_exists", return_value=False):
            result = _team_instruction("something-else")

        assert result is not None
        assert "TeamCreate" in result
        assert "develop" in result

    # -------------------------------------------------------------------------
    # Verify derive_team_name is called with branch output
    # -------------------------------------------------------------------------

    def test_passes_branch_to_derive_team_name(self):
        """Test that get_current_branch output is passed to derive_team_name."""
        with patch("session_init.get_current_branch", return_value="bugfix/login-fix") as mock_branch, \
             patch("session_init.derive_team_name", return_value="login-fix") as mock_derive, \
             patch("session_init.team_exists", return_value=False):
            result = _team_instruction("new")

        mock_branch.assert_called_once()
        mock_derive.assert_called_once_with("bugfix/login-fix")
        assert "login-fix" in result

    # -------------------------------------------------------------------------
    # Verify team_exists is called with derived name (for compact/resume)
    # -------------------------------------------------------------------------

    def test_compact_calls_team_exists_with_derived_name(self):
        """Test compact source passes derived team name to team_exists."""
        with patch("session_init.get_current_branch", return_value="feature/auth"), \
             patch("session_init.derive_team_name", return_value="auth"), \
             patch("session_init.team_exists", return_value=False) as mock_exists:
            _team_instruction("compact")

        mock_exists.assert_called_once_with("auth")

    def test_resume_calls_team_exists_with_derived_name(self):
        """Test resume source passes derived team name to team_exists."""
        with patch("session_init.get_current_branch", return_value="feature/auth"), \
             patch("session_init.derive_team_name", return_value="auth"), \
             patch("session_init.team_exists", return_value=False) as mock_exists:
            _team_instruction("resume")

        mock_exists.assert_called_once_with("auth")

    # -------------------------------------------------------------------------
    # Edge case: team name with special characters
    # -------------------------------------------------------------------------

    def test_team_name_with_special_chars_in_message(self):
        """Test team name with special characters appears correctly in output."""
        with patch("session_init.get_current_branch", return_value="feature/PACT-123-auth"), \
             patch("session_init.derive_team_name", return_value="PACT-123-auth"), \
             patch("session_init.team_exists", return_value=True):
            result = _team_instruction("compact")

        assert "PACT-123-auth" in result
