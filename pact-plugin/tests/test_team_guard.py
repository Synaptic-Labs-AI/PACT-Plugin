"""
Tests for team_guard.py — PreToolUse hook that blocks Task dispatch
if team_name is specified but team doesn't exist.

Tests cover:
1. Task call with team_name and team exists -> allow (exit 0)
2. Task call with team_name and team doesn't exist -> block (exit 2)
3. Task call without team_name -> allow (always, no check needed)
4. Non-Task tool call -> allow (hook shouldn't even fire, but graceful no-op)
5. Missing CLAUDE_CODE_TEAM_NAME env var -> allow (no team context)
6. main() entry point: stdin JSON parsing, exit codes, output format
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestTeamGuard:
    """Tests for team_guard.check_team_exists()."""

    def test_allows_when_team_exists(self, tmp_path):
        from team_guard import check_team_exists

        team_dir = tmp_path / "teams" / "pact-abc12345"
        team_dir.mkdir(parents=True)
        config = team_dir / "config.json"
        config.write_text('{"members": []}')

        result = check_team_exists(
            tool_input={"team_name": "pact-abc12345"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None  # None means allow

    def test_blocks_when_team_missing(self, tmp_path):
        from team_guard import check_team_exists

        result = check_team_exists(
            tool_input={"team_name": "pact-abc12345"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is not None
        assert "does not exist" in result

    def test_allows_when_no_team_name(self, tmp_path):
        from team_guard import check_team_exists

        result = check_team_exists(
            tool_input={"prompt": "explore the codebase"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_allows_when_empty_tool_input(self, tmp_path):
        from team_guard import check_team_exists

        result = check_team_exists(
            tool_input={},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_normalizes_uppercase_team_name(self, tmp_path):
        """Uppercase team_name should be normalized to lowercase for directory lookup."""
        from team_guard import check_team_exists

        # Create team directory with lowercase name (as TeamCreate does)
        team_dir = tmp_path / "teams" / "pact-abc12345"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

        # Pass uppercase team_name — should still find the lowercase directory
        result = check_team_exists(
            tool_input={"team_name": "PACT-abc12345"},
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None  # None means allow (found the team)


class TestMainEntryPoint:
    """Tests for team_guard.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_team_exists(self, tmp_path):
        from team_guard import main

        # Create team directory with config
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

        input_data = json.dumps({
            "tool_input": {"team_name": "pact-test"}
        })

        with patch("team_guard.check_team_exists", return_value=None), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_2_when_team_missing(self, capsys):
        from team_guard import main

        input_data = json.dumps({
            "tool_input": {"team_name": "pact-nonexistent"}
        })

        error_msg = "Team 'pact-nonexistent' does not exist yet."
        with patch("team_guard.check_team_exists", return_value=error_msg), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_main_exits_0_on_invalid_json(self):
        from team_guard import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_team_name(self):
        from team_guard import main

        input_data = json.dumps({
            "tool_input": {"prompt": "explore codebase"}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
