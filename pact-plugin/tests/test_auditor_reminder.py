"""
Location: pact-plugin/tests/test_auditor_reminder.py
Summary: Tests for auditor_reminder PostToolUse hook.
Used by: pytest test suite

Tests the auditor dispatch reminder hook that nudges the orchestrator
when a coder is spawned without an auditor present on the team.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure hooks directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

from auditor_reminder import CODER_TYPES, _team_has_auditor, check_auditor_needed, main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def teams_dir(tmp_path):
    """Create a temporary teams directory."""
    return str(tmp_path / "teams")


def _write_team_config(teams_dir: str, team_name: str, members: list[dict]):
    """Helper to write a team config.json."""
    team_dir = Path(teams_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "name": team_name,
        "members": members,
    }
    (team_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _team_has_auditor
# ---------------------------------------------------------------------------


class TestTeamHasAuditor:
    """Tests for _team_has_auditor helper."""

    def test_auditor_present(self, teams_dir):
        """Returns True when auditor member exists."""
        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder", "agentType": "pact-backend-coder"},
            {"name": "auditor", "agentType": "pact-auditor"},
        ])
        assert _team_has_auditor("test-team", teams_dir) is True

    def test_auditor_absent(self, teams_dir):
        """Returns False when no auditor member exists."""
        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder", "agentType": "pact-backend-coder"},
        ])
        assert _team_has_auditor("test-team", teams_dir) is False

    def test_empty_team_name(self, teams_dir):
        """Suppresses reminder (returns True) for empty team name."""
        assert _team_has_auditor("", teams_dir) is True

    def test_no_team_config(self, teams_dir):
        """Suppresses reminder (returns True) when config doesn't exist."""
        assert _team_has_auditor("nonexistent-team", teams_dir) is True

    def test_malformed_config(self, teams_dir):
        """Suppresses reminder (returns True) on malformed JSON."""
        team_dir = Path(teams_dir) / "bad-team"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "config.json").write_text("not json", encoding="utf-8")
        assert _team_has_auditor("bad-team", teams_dir) is True

    def test_empty_members_list(self, teams_dir):
        """Returns False when members list is empty."""
        _write_team_config(teams_dir, "test-team", [])
        assert _team_has_auditor("test-team", teams_dir) is False

    def test_team_name_case_insensitive(self, teams_dir):
        """Team name is lowercased for directory lookup."""
        _write_team_config(teams_dir, "my-team", [
            {"name": "auditor", "agentType": "pact-auditor"},
        ])
        assert _team_has_auditor("MY-TEAM", teams_dir) is True

    def test_default_teams_dir(self):
        """Uses ~/.claude/teams when no override provided."""
        with patch.object(Path, "home", return_value=Path("/mock/home")):
            # Config won't exist at /mock/home — should suppress (True)
            assert _team_has_auditor("any-team") is True


# ---------------------------------------------------------------------------
# check_auditor_needed
# ---------------------------------------------------------------------------


class TestCheckAuditorNeeded:
    """Tests for check_auditor_needed main logic."""

    def test_non_coder_type_ignored(self, teams_dir):
        """Returns None for non-coder agent types."""
        tool_input = {
            "subagent_type": "pact-test-engineer",
            "team_name": "test-team",
        }
        assert check_auditor_needed(tool_input, teams_dir) is None

    def test_no_subagent_type(self, teams_dir):
        """Returns None when subagent_type is missing."""
        tool_input = {"team_name": "test-team"}
        assert check_auditor_needed(tool_input, teams_dir) is None

    @pytest.mark.parametrize("coder_type", sorted(CODER_TYPES))
    def test_coder_without_auditor_emits_reminder(self, coder_type, teams_dir):
        """Emits reminder for each coder type when no auditor present."""
        _write_team_config(teams_dir, "test-team", [
            {"name": "some-coder", "agentType": coder_type},
        ])
        tool_input = {
            "subagent_type": coder_type,
            "team_name": "test-team",
        }
        result = check_auditor_needed(tool_input, teams_dir)
        assert result is not None
        assert "pact-audit.md" in result
        assert "dispatch protocol" in result

    def test_coder_with_auditor_suppressed(self, teams_dir):
        """Returns None when auditor already present."""
        _write_team_config(teams_dir, "test-team", [
            {"name": "backend-coder", "agentType": "pact-backend-coder"},
            {"name": "auditor", "agentType": "pact-auditor"},
        ])
        tool_input = {
            "subagent_type": "pact-backend-coder",
            "team_name": "test-team",
        }
        assert check_auditor_needed(tool_input, teams_dir) is None

    def test_secretary_not_a_coder(self, teams_dir):
        """Secretary dispatch does not trigger reminder."""
        tool_input = {
            "subagent_type": "pact-secretary",
            "team_name": "test-team",
        }
        assert check_auditor_needed(tool_input, teams_dir) is None

    def test_architect_not_a_coder(self, teams_dir):
        """Architect dispatch does not trigger reminder."""
        tool_input = {
            "subagent_type": "pact-architect",
            "team_name": "test-team",
        }
        assert check_auditor_needed(tool_input, teams_dir) is None

    def test_auditor_not_a_coder(self, teams_dir):
        """Auditor dispatch does not trigger self-reminder."""
        tool_input = {
            "subagent_type": "pact-auditor",
            "team_name": "test-team",
        }
        assert check_auditor_needed(tool_input, teams_dir) is None

    def test_missing_team_name_suppressed(self, teams_dir):
        """Returns None when team_name is missing (no team context)."""
        tool_input = {
            "subagent_type": "pact-backend-coder",
        }
        assert check_auditor_needed(tool_input, teams_dir) is None


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the main() entry point."""

    def test_coder_without_auditor_outputs_json(self, teams_dir, capsys):
        """Outputs systemMessage JSON when reminder needed."""
        _write_team_config(teams_dir, "test-team", [])
        stdin_data = json.dumps({
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "pact-backend-coder",
                "team_name": "test-team",
            },
        })
        import io
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("auditor_reminder._team_has_auditor", return_value=False), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "pact-audit.md" in output["systemMessage"]

    def test_non_coder_silent(self, capsys):
        """No output for non-coder agent types."""
        stdin_data = json.dumps({
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "pact-test-engineer",
                "team_name": "test-team",
            },
        })
        import io
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_invalid_json_stdin(self, capsys):
        """Exits 0 on invalid JSON stdin."""
        import io
        with patch("sys.stdin", io.StringIO("not json")), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_exception_fail_open(self, capsys):
        """Exits 0 and emits error JSON on unexpected exception."""
        stdin_data = json.dumps({
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": "pact-backend-coder",
                "team_name": "test-team",
            },
        })
        import io
        with patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("auditor_reminder.check_auditor_needed",
                   side_effect=RuntimeError("boom")), \
             pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "boom" in captured.err
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "auditor_reminder" in output["systemMessage"]
