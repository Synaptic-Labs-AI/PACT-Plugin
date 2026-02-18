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
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestPeerInject:
    """Tests for peer_inject.get_peer_context()."""

    def test_injects_peer_names(self, tmp_path):
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "PACT-test"
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
            team_name="PACT-test",
            teams_dir=str(tmp_path / "teams")
        )

        assert "frontend-coder" in result
        assert "database-engineer" in result
        assert "backend-coder" not in result

    def test_excludes_spawning_agent(self, tmp_path):
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "PACT-test"
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
            team_name="PACT-test",
            teams_dir=str(tmp_path / "teams")
        )

        assert "backend-coder" in result
        assert "architect" not in result

    def test_returns_none_when_no_team_config(self, tmp_path):
        from peer_inject import get_peer_context

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="PACT-nonexistent",
            teams_dir=str(tmp_path / "teams")
        )

        assert result is None

    def test_alone_message_when_only_member(self, tmp_path):
        from peer_inject import get_peer_context

        team_dir = tmp_path / "teams" / "PACT-test"
        team_dir.mkdir(parents=True)
        config = {
            "members": [
                {"name": "backend-coder", "agentType": "pact-backend-coder"},
            ]
        }
        (team_dir / "config.json").write_text(json.dumps(config))

        result = get_peer_context(
            agent_type="pact-backend-coder",
            team_name="PACT-test",
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
