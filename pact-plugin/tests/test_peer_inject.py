# pact-plugin/tests/test_peer_inject.py
"""
Tests for peer_inject.py — SubagentStart hook that injects peer teammate
list into newly spawned PACT agents.

Tests cover:
1. Injects peer names when team has multiple members
2. Excludes the spawning agent from peer list
3. Returns None when no team config exists
4. Returns "only active teammate" when alone
5. No-op when CLAUDE_CODE_TEAM_NAME not set
6. main() entry point: stdin JSON parsing, exit codes, output format
7. Corrupted config.json returns None
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestPeerInject:
    """Tests for peer_inject.get_peer_context()."""

    def test_injects_peer_names(self, tmp_path):
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
                {"name": "frontend-coder", "agentType": "pact-frontend-coder"},
                {"name": "database-engineer", "agentType": "pact-database-engineer"},
            ]
        }
        (team_dir / "config.json").write_text(json.dumps(config))

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="pact-test",
            teams_dir=str(tmp_path / "teams")
        )

        assert "frontend-coder" in result
        assert "database-engineer" in result
        assert "backend-coder" not in result

    def test_excludes_spawning_agent(self, tmp_path):
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "architect", "agentType": "pact-architect"},
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ]
        }
        (team_dir / "config.json").write_text(json.dumps(config))

        result = get_peer_context(
            agent_type="pact-architect",
            team_name="pact-test",
            teams_dir=str(tmp_path / "teams")
        )

        assert "backend-coder" in result
        assert "architect" not in result

    def test_returns_none_when_no_team_config(self, tmp_path):
        from peer_inject import get_peer_context

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="pact-nonexistent",
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_alone_message_when_only_member(self, tmp_path):
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ]
        }
        (team_dir / "config.json").write_text(json.dumps(config))

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="pact-test",
            teams_dir=str(tmp_path / "teams")
        )

        assert "only active teammate" in result.lower()

    def test_noop_when_no_team_name(self, tmp_path):
        from peer_inject import get_peer_context

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="",
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_returns_none_on_corrupted_config_json(self, tmp_path):
        """Corrupted config.json should return None gracefully."""
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("not valid json{{{")

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="pact-test",
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None


class TestMainEntryPoint:
    """Tests for peer_inject.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_with_peer_context(self, capsys):
        from peer_inject import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
        })

        peer_context = "Active teammates on your team: frontend-coder"
        with patch("peer_inject.get_peer_context", return_value=peer_context), \
             patch.dict("os.environ", {"CLAUDE_CODE_TEAM_NAME": "pact-test"}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "frontend-coder" in output["hookSpecificOutput"]["additionalContext"]

    def test_main_exits_0_on_invalid_json(self):
        from peer_inject import main

        with patch.dict("os.environ", {"CLAUDE_CODE_TEAM_NAME": "pact-test"}), \
             patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_team_name(self):
        from peer_inject import main

        input_data = json.dumps({"agent_type": "pact-backend-coder"})

        with patch.dict("os.environ", {}, clear=True), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_peer_context(self):
        from peer_inject import main

        input_data = json.dumps({"agent_type": "pact-backend-coder"})

        with patch("peer_inject.get_peer_context", return_value=None), \
             patch.dict("os.environ", {"CLAUDE_CODE_TEAM_NAME": "pact-test"}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
