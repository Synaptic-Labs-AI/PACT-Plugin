# pact-plugin/tests/test_session_end.py
"""
Tests for session_end.py — SessionEnd hook that writes last-session snapshots.

Tests cover:
1. Writes structured markdown snapshot
2. Creates sessions directory if missing
3. Includes completed task summaries with handoff decisions
4. Includes incomplete tasks with status
5. Handles empty task list gracefully
6. Handles None task list gracefully
7. main() entry point: exit codes and error handling
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestGetProjectSlug:
    """Tests for session_end.get_project_slug()."""

    def test_returns_basename_from_env(self):
        from session_end import get_project_slug

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": "/Users/mj/Sites/my-project"}):
            assert get_project_slug() == "my-project"

    def test_returns_empty_when_no_env(self):
        from session_end import get_project_slug

        with patch.dict("os.environ", {}, clear=True):
            assert get_project_slug() == ""


class TestWriteSessionSnapshot:
    """Tests for session_end.write_session_snapshot()."""

    def test_writes_markdown_snapshot(self, tmp_path):
        from session_end import write_session_snapshot

        tasks = [
            {
                "id": "1",
                "subject": "Implement auth",
                "status": "completed",
                "metadata": {
                    "handoff": {
                        "produced": ["src/auth.ts"],
                        "decisions": ["Used JWT for stateless auth"],
                        "uncertainty": [],
                        "integration": [],
                        "open_questions": [],
                    }
                },
            }
        ]

        write_session_snapshot(
            tasks=tasks,
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        snapshot_file = tmp_path / "my-project" / "last-session.md"
        assert snapshot_file.exists()
        content = snapshot_file.read_text()
        assert "# Last Session:" in content
        assert "## Completed Tasks" in content
        assert "#1 Implement auth" in content
        assert "Used JWT" in content

    def test_creates_directory_if_missing(self, tmp_path):
        from session_end import write_session_snapshot

        sessions_dir = tmp_path / "deep" / "nested"

        write_session_snapshot(
            tasks=[],
            project_slug="new-project",
            sessions_dir=str(sessions_dir),
        )

        snapshot_file = sessions_dir / "new-project" / "last-session.md"
        assert snapshot_file.exists()

    def test_includes_completed_tasks_with_decisions(self, tmp_path):
        from session_end import write_session_snapshot

        tasks = [
            {
                "id": "2",
                "subject": "PREPARE: research",
                "status": "completed",
                "metadata": {
                    "handoff": {
                        "produced": ["docs/prep.md"],
                        "decisions": ["Chose REST over GraphQL", "Use PostgreSQL"],
                        "uncertainty": [],
                        "integration": [],
                        "open_questions": [],
                    }
                },
            },
            {
                "id": "3",
                "subject": "ARCHITECT: design",
                "status": "completed",
                "metadata": {},
            },
        ]

        write_session_snapshot(
            tasks=tasks,
            project_slug="test-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "test-proj" / "last-session.md").read_text()
        assert "#2 PREPARE: research -> Chose REST over GraphQL" in content
        assert "#3 ARCHITECT: design" in content
        assert "Chose REST over GraphQL" in content
        assert "Use PostgreSQL" in content

    def test_includes_incomplete_tasks(self, tmp_path):
        from session_end import write_session_snapshot

        tasks = [
            {
                "id": "5",
                "subject": "CODE: implement API",
                "status": "in_progress",
                "metadata": {},
            },
            {
                "id": "6",
                "subject": "TEST: write tests",
                "status": "pending",
                "metadata": {},
            },
        ]

        write_session_snapshot(
            tasks=tasks,
            project_slug="test-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "test-proj" / "last-session.md").read_text()
        assert "## Incomplete Tasks" in content
        assert "#5 CODE: implement API -- in_progress" in content
        assert "#6 TEST: write tests -- pending" in content

    def test_handles_empty_task_list(self, tmp_path):
        from session_end import write_session_snapshot

        write_session_snapshot(
            tasks=[],
            project_slug="empty-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "empty-proj" / "last-session.md").read_text()
        assert "## Completed Tasks" in content
        assert "- (none)" in content

    def test_handles_none_task_list(self, tmp_path):
        from session_end import write_session_snapshot

        write_session_snapshot(
            tasks=None,
            project_slug="none-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "none-proj" / "last-session.md").read_text()
        assert "## Completed Tasks" in content
        assert "- (none)" in content

    def test_skips_when_no_project_slug(self, tmp_path):
        from session_end import write_session_snapshot

        write_session_snapshot(
            tasks=[{"id": "1", "subject": "test", "status": "completed", "metadata": {}}],
            project_slug="",
            sessions_dir=str(tmp_path),
        )

        # No file should be created
        assert not list(tmp_path.iterdir())

    def test_includes_unresolved_blockers(self, tmp_path):
        from session_end import write_session_snapshot

        tasks = [
            {
                "id": "10",
                "subject": "BLOCKER: missing API key",
                "status": "in_progress",
                "metadata": {"type": "blocker"},
            },
        ]

        write_session_snapshot(
            tasks=tasks,
            project_slug="blocker-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "blocker-proj" / "last-session.md").read_text()
        assert "## Unresolved" in content
        assert "#10 BLOCKER: missing API key" in content


class TestMainEntryPoint:
    """Tests for session_end.main() exit behavior."""

    def test_main_exits_0_on_success(self):
        from session_end import main

        env = {
            "CLAUDE_PROJECT_DIR": "/Users/mj/project",
        }

        with patch.dict("os.environ", env, clear=True), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.write_session_snapshot"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_exception(self):
        """main() should exit 0 even on errors (fire-and-forget)."""
        from session_end import main

        env = {
            "CLAUDE_PROJECT_DIR": "/Users/mj/project",
        }

        with patch.dict("os.environ", env, clear=True), \
             patch("session_end.get_task_list", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_env_vars(self):
        from session_end import main

        with patch.dict("os.environ", {}, clear=True), \
             patch("session_end.get_task_list", return_value=None), \
             patch("session_end.write_session_snapshot"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_calls_write_snapshot_with_tasks(self):
        from session_end import main

        env = {
            "CLAUDE_PROJECT_DIR": "/Users/mj/Sites/my-project",
        }

        mock_tasks = [{"id": "1", "subject": "test", "status": "completed", "metadata": {}}]

        with patch.dict("os.environ", env, clear=True), \
             patch("session_end.get_task_list", return_value=mock_tasks), \
             patch("session_end.write_session_snapshot") as mock_snapshot:
            with pytest.raises(SystemExit):
                main()

        mock_snapshot.assert_called_once()
        call_args = mock_snapshot.call_args
        assert call_args.kwargs["tasks"] == mock_tasks
        assert call_args.kwargs["project_slug"] == "my-project"

    def test_main_calls_cleanup_stale_teams(self):
        """main() should call cleanup_stale_teams() after write_session_snapshot()."""
        from session_end import main

        env = {"CLAUDE_PROJECT_DIR": "/Users/mj/Sites/my-project"}

        with patch.dict("os.environ", env, clear=True), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.write_session_snapshot"), \
             patch("session_end.cleanup_stale_teams") as mock_cleanup:
            with pytest.raises(SystemExit):
                main()

        mock_cleanup.assert_called_once()


# =============================================================================
# cleanup_stale_teams() Tests
# =============================================================================

class TestCleanupStaleTeams:
    """Tests for session_end.cleanup_stale_teams() — filesystem cleanup of
    stale pact-* team and task directories."""

    def test_removes_team_with_zero_members(self, tmp_path):
        """Team with empty members list should be cleaned up."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-abc123"
        team.mkdir(parents=True)
        (team / "config.json").write_text('{"members": []}')

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert "pact-abc123" in cleaned
        assert not team.exists()

    def test_removes_team_with_one_member(self, tmp_path):
        """Team with 1 member (just lead) should be cleaned up."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-xyz789"
        team.mkdir(parents=True)
        (team / "config.json").write_text(
            '{"members": [{"name": "team-lead", "status": "active"}]}'
        )

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert "pact-xyz789" in cleaned
        assert not team.exists()

    def test_preserves_team_with_multiple_members(self, tmp_path):
        """Team with 2+ members should NOT be cleaned up."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-multi"
        team.mkdir(parents=True)
        (team / "config.json").write_text(
            '{"members": [{"name": "lead"}, {"name": "coder-a"}]}'
        )

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert cleaned == []
        assert team.exists()

    def test_removes_team_without_config(self, tmp_path):
        """Team directory with no config.json is stale, should be cleaned."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-orphan"
        team.mkdir(parents=True)
        # No config.json created

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert "pact-orphan" in cleaned
        assert not team.exists()

    def test_skips_corrupted_config(self, tmp_path):
        """Team with unreadable config.json should be skipped (not removed)."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-corrupt"
        team.mkdir(parents=True)
        (team / "config.json").write_text("not valid json{{{")

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert cleaned == []
        assert team.exists()  # Not removed — skipped

    def test_skips_non_pact_directories(self, tmp_path):
        """Directories not starting with 'pact-' should be ignored."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        other_team = teams_dir / "my-custom-team"
        other_team.mkdir(parents=True)
        (other_team / "config.json").write_text('{"members": []}')

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert cleaned == []
        assert other_team.exists()

    def test_skips_files_in_teams_dir(self, tmp_path):
        """Regular files in teams dir should not cause errors."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        teams_dir.mkdir(parents=True)
        (teams_dir / "some_file.txt").write_text("not a directory")

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert cleaned == []

    def test_returns_empty_when_teams_dir_missing(self, tmp_path):
        """Should return empty list if teams directory doesn't exist."""
        from session_end import cleanup_stale_teams

        cleaned = cleanup_stale_teams(str(tmp_path / "nonexistent"))
        assert cleaned == []

    def test_also_removes_corresponding_task_dir(self, tmp_path):
        """When removing a team, should also remove ~/.claude/tasks/{team_name}."""
        from session_end import cleanup_stale_teams

        # Create teams/ and tasks/ as siblings (like ~/.claude/teams and ~/.claude/tasks)
        teams_dir = tmp_path / "teams"
        tasks_dir = tmp_path / "tasks"

        team = teams_dir / "pact-cleanup"
        team.mkdir(parents=True)
        (team / "config.json").write_text('{"members": []}')

        task_dir = tasks_dir / "pact-cleanup"
        task_dir.mkdir(parents=True)
        (task_dir / "1.json").write_text('{"id": "1"}')

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert "pact-cleanup" in cleaned
        assert not team.exists()
        assert not task_dir.exists()

    def test_no_error_when_task_dir_missing(self, tmp_path):
        """Should not fail if corresponding task dir doesn't exist."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-notasks"
        team.mkdir(parents=True)
        (team / "config.json").write_text('{"members": []}')
        # No tasks/ directory created

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert "pact-notasks" in cleaned

    def test_handles_empty_teams_directory(self, tmp_path):
        """Empty teams directory should return empty list."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        teams_dir.mkdir(parents=True)

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert cleaned == []

    def test_cleans_multiple_stale_teams(self, tmp_path):
        """Should clean all qualifying teams in a single call."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"

        # Stale team 1: no config
        (teams_dir / "pact-old1").mkdir(parents=True)

        # Stale team 2: empty members
        team2 = teams_dir / "pact-old2"
        team2.mkdir(parents=True)
        (team2 / "config.json").write_text('{"members": []}')

        # Active team: 2 members (should survive)
        team3 = teams_dir / "pact-active"
        team3.mkdir(parents=True)
        (team3 / "config.json").write_text('{"members": ["a", "b"]}')

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert len(cleaned) == 2
        assert "pact-old1" in cleaned
        assert "pact-old2" in cleaned
        assert "pact-active" not in cleaned
        assert (teams_dir / "pact-active").exists()

    def test_config_missing_members_key(self, tmp_path):
        """Config with valid JSON but no 'members' key should be treated as
        having 0 members (cleaned up)."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-nomembers"
        team.mkdir(parents=True)
        (team / "config.json").write_text('{"description": "old team"}')

        cleaned = cleanup_stale_teams(str(teams_dir))
        assert "pact-nomembers" in cleaned

    def test_defaults_to_home_claude_teams(self):
        """Without teams_dir override, should use ~/.claude/teams/."""
        from session_end import cleanup_stale_teams

        # Just verify it doesn't crash when called with no args
        # (it will scan the real ~/.claude/teams/ which may or may not exist)
        result = cleanup_stale_teams()
        assert isinstance(result, list)
