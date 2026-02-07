"""
Tests for the shared team_utils module.

Tests team name derivation, branch detection, team configuration reading,
and team existence checking for the Agent Teams integration.

Location: pact-plugin/tests/test_team_utils.py
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.team_utils import (
    derive_team_name,
    get_current_branch,
    get_team_config_dir,
    get_team_config_path,
    team_exists,
    get_team_members,
    find_active_teams,
)


# =============================================================================
# Tests for derive_team_name()
# =============================================================================

class TestDeriveTeamName:
    """Tests for derive_team_name function."""

    def test_strips_feature_prefix(self):
        """Test stripping feature/ prefix from branch name."""
        assert derive_team_name("feature/v3-agent-teams") == "v3-agent-teams"

    def test_strips_bugfix_prefix(self):
        """Test stripping bugfix/ prefix from branch name."""
        assert derive_team_name("bugfix/fix-login") == "fix-login"

    def test_strips_hotfix_prefix(self):
        """Test stripping hotfix/ prefix from branch name."""
        assert derive_team_name("hotfix/critical-patch") == "critical-patch"

    def test_strips_fix_prefix(self):
        """Test stripping fix/ prefix from branch name."""
        assert derive_team_name("fix/auth-bug") == "auth-bug"

    def test_strips_chore_prefix(self):
        """Test stripping chore/ prefix from branch name."""
        assert derive_team_name("chore/update-deps") == "update-deps"

    def test_strips_release_prefix(self):
        """Test stripping release/ prefix from branch name."""
        assert derive_team_name("release/v2.0") == "v2-0"

    def test_no_prefix_passthrough(self):
        """Test branch names without known prefixes pass through."""
        assert derive_team_name("main") == "main"
        assert derive_team_name("develop") == "develop"

    def test_nested_path_normalized(self):
        """Test nested path separators are replaced with hyphens."""
        assert derive_team_name("feature/scope/nested-path") == "scope-nested-path"

    def test_dots_replaced(self):
        """Test dots in branch names are replaced with hyphens."""
        assert derive_team_name("feature/v2.1.0") == "v2-1-0"

    def test_underscores_replaced(self):
        """Test underscores in branch names are replaced with hyphens."""
        assert derive_team_name("feature/my_feature_name") == "my-feature-name"

    def test_backslashes_replaced(self):
        """Test backslashes are replaced with hyphens."""
        assert derive_team_name("feature\\windows\\path") == "feature-windows-path"

    def test_multiple_hyphens_collapsed(self):
        """Test consecutive hyphens are collapsed to single hyphen."""
        assert derive_team_name("feature/a--b---c") == "a-b-c"

    def test_leading_trailing_hyphens_stripped(self):
        """Test leading and trailing hyphens are removed."""
        assert derive_team_name("feature/-leading-") == "leading"

    def test_empty_string_returns_fallback(self):
        """Test empty string returns 'pact-session' fallback."""
        assert derive_team_name("") == "pact-session"

    def test_prefix_only_returns_fallback(self):
        """Test prefix-only branch name returns fallback."""
        # "feature/" strips to "", which after normalization is empty
        assert derive_team_name("feature/") == "pact-session"

    def test_only_slashes_returns_fallback(self):
        """Test only slashes returns fallback."""
        # After stripping, all slashes become hyphens, then stripped
        assert derive_team_name("///") == "pact-session"

    def test_realistic_branch_names(self):
        """Test realistic branch name derivation."""
        assert derive_team_name("feature/PACT-123-add-auth") == "PACT-123-add-auth"
        assert derive_team_name("bugfix/issue-42-null-check") == "issue-42-null-check"
        assert derive_team_name("feature/v3-agent-teams") == "v3-agent-teams"

    def test_only_strips_first_matching_prefix(self):
        """Test only the first matching prefix is stripped."""
        # "feature/" is stripped, leaving "bugfix/thing"
        result = derive_team_name("feature/bugfix/thing")
        assert result == "bugfix-thing"


# =============================================================================
# Tests for get_current_branch()
# =============================================================================

class TestGetCurrentBranch:
    """Tests for get_current_branch function."""

    def test_returns_branch_name_on_success(self):
        """Test returns branch name when git command succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "feature/v3-agent-teams\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            branch = get_current_branch()

        assert branch == "feature/v3-agent-teams"
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_returns_empty_on_failure(self):
        """Test returns empty string when git command fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            branch = get_current_branch()

        assert branch == ""

    def test_returns_empty_on_timeout(self):
        """Test returns empty string on subprocess timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            branch = get_current_branch()

        assert branch == ""

    def test_returns_empty_on_file_not_found(self):
        """Test returns empty string when git is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            branch = get_current_branch()

        assert branch == ""

    def test_returns_empty_on_generic_exception(self):
        """Test returns empty string on unexpected exception."""
        with patch("subprocess.run", side_effect=OSError("unexpected")):
            branch = get_current_branch()

        assert branch == ""

    def test_strips_whitespace_from_output(self):
        """Test trailing whitespace is stripped from branch name."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  main  \n"

        with patch("subprocess.run", return_value=mock_result):
            branch = get_current_branch()

        assert branch == "main"


# =============================================================================
# Tests for get_team_config_dir()
# =============================================================================

class TestGetTeamConfigDir:
    """Tests for get_team_config_dir function."""

    def test_returns_claude_teams_path(self):
        """Test returns ~/.claude/teams/ path."""
        result = get_team_config_dir()

        assert result == Path.home() / ".claude" / "teams"

    def test_returns_path_object(self):
        """Test returns a Path object."""
        result = get_team_config_dir()

        assert isinstance(result, Path)


# =============================================================================
# Tests for get_team_config_path()
# =============================================================================

class TestGetTeamConfigPath:
    """Tests for get_team_config_path function."""

    def test_returns_config_json_path(self):
        """Test returns correct config.json path for team."""
        result = get_team_config_path("v3-agent-teams")

        expected = Path.home() / ".claude" / "teams" / "v3-agent-teams" / "config.json"
        assert result == expected

    def test_different_team_names(self):
        """Test path varies by team name."""
        path1 = get_team_config_path("team-a")
        path2 = get_team_config_path("team-b")

        assert path1 != path2
        assert "team-a" in str(path1)
        assert "team-b" in str(path2)


# =============================================================================
# Tests for team_exists()
# =============================================================================

class TestTeamExists:
    """Tests for team_exists function."""

    def test_returns_true_when_team_dir_exists(self, tmp_path, monkeypatch):
        """Test returns True when team directory exists."""
        # Create mock team directory
        teams_dir = tmp_path / ".claude" / "teams" / "test-team"
        teams_dir.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert team_exists("test-team") is True

    def test_returns_false_when_team_dir_missing(self, tmp_path, monkeypatch):
        """Test returns False when team directory doesn't exist."""
        # Create teams parent but not the specific team
        teams_dir = tmp_path / ".claude" / "teams"
        teams_dir.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert team_exists("nonexistent-team") is False

    def test_returns_false_for_empty_name(self):
        """Test returns False for empty team name."""
        assert team_exists("") is False

    def test_returns_false_when_path_is_file(self, tmp_path, monkeypatch):
        """Test returns False when path exists but is a file, not directory."""
        teams_dir = tmp_path / ".claude" / "teams"
        teams_dir.mkdir(parents=True)
        # Create a file instead of directory
        (teams_dir / "not-a-dir").write_text("file content")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert team_exists("not-a-dir") is False

    def test_returns_false_when_teams_dir_missing(self, tmp_path, monkeypatch):
        """Test returns False when ~/.claude/teams/ itself doesn't exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        assert team_exists("any-team") is False


# =============================================================================
# Tests for get_team_members()
# =============================================================================

class TestGetTeamMembers:
    """Tests for get_team_members function."""

    def test_returns_members_from_config(self, tmp_path, monkeypatch):
        """Test returns members list from config.json."""
        team_dir = tmp_path / ".claude" / "teams" / "test-team"
        team_dir.mkdir(parents=True)

        config = {
            "members": [
                {"name": "backend-1", "type": "pact-backend-coder", "status": "active"},
                {"name": "architect-1", "type": "pact-architect", "status": "active"},
            ]
        }
        (team_dir / "config.json").write_text(json.dumps(config))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        members = get_team_members("test-team")

        assert len(members) == 2
        assert members[0]["name"] == "backend-1"
        assert members[1]["name"] == "architect-1"

    def test_returns_empty_when_config_missing(self, tmp_path, monkeypatch):
        """Test returns empty list when config.json doesn't exist."""
        team_dir = tmp_path / ".claude" / "teams" / "test-team"
        team_dir.mkdir(parents=True)
        # No config.json created

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        members = get_team_members("test-team")

        assert members == []

    def test_returns_empty_on_malformed_json(self, tmp_path, monkeypatch):
        """Test returns empty list when config.json has invalid JSON."""
        team_dir = tmp_path / ".claude" / "teams" / "test-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("{ invalid json")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        members = get_team_members("test-team")

        assert members == []

    def test_returns_empty_when_no_members_key(self, tmp_path, monkeypatch):
        """Test returns empty list when config has no 'members' key."""
        team_dir = tmp_path / ".claude" / "teams" / "test-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({"name": "test-team"}))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        members = get_team_members("test-team")

        assert members == []

    def test_returns_empty_when_team_dir_missing(self, tmp_path, monkeypatch):
        """Test returns empty list when team directory doesn't exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        members = get_team_members("nonexistent-team")

        assert members == []

    def test_handles_empty_members_list(self, tmp_path, monkeypatch):
        """Test handles config with empty members array."""
        team_dir = tmp_path / ".claude" / "teams" / "test-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({"members": []}))

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        members = get_team_members("test-team")

        assert members == []


# =============================================================================
# Tests for find_active_teams()
# =============================================================================

class TestFindActiveTeams:
    """Tests for find_active_teams function."""

    def test_finds_team_directories(self, tmp_path, monkeypatch):
        """Test finds all team directories under ~/.claude/teams/."""
        teams_dir = tmp_path / ".claude" / "teams"
        teams_dir.mkdir(parents=True)
        (teams_dir / "team-alpha").mkdir()
        (teams_dir / "team-beta").mkdir()

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        teams = find_active_teams()

        assert len(teams) == 2
        assert set(teams) == {"team-alpha", "team-beta"}

    def test_returns_empty_when_no_teams_dir(self, tmp_path, monkeypatch):
        """Test returns empty list when ~/.claude/teams/ doesn't exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        teams = find_active_teams()

        assert teams == []

    def test_returns_empty_when_teams_dir_empty(self, tmp_path, monkeypatch):
        """Test returns empty list when teams directory is empty."""
        teams_dir = tmp_path / ".claude" / "teams"
        teams_dir.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        teams = find_active_teams()

        assert teams == []

    def test_ignores_files_in_teams_dir(self, tmp_path, monkeypatch):
        """Test ignores non-directory entries in teams directory."""
        teams_dir = tmp_path / ".claude" / "teams"
        teams_dir.mkdir(parents=True)
        (teams_dir / "team-alpha").mkdir()
        (teams_dir / "some-file.txt").write_text("not a team")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        teams = find_active_teams()

        assert len(teams) == 1
        assert "team-alpha" in teams

    def test_handles_os_error_gracefully(self, tmp_path, monkeypatch):
        """Test handles OSError during directory iteration."""
        teams_dir = tmp_path / ".claude" / "teams"
        teams_dir.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock iterdir to raise OSError
        original_iterdir = Path.iterdir

        def mock_iterdir(self):
            if "teams" in str(self):
                raise OSError("Permission denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", mock_iterdir)

        teams = find_active_teams()

        assert teams == []
