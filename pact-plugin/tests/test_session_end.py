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

    def test_truncates_long_decision_summary(self, tmp_path):
        """Decision strings longer than 80 chars should be truncated to 77 + '...'."""
        from session_end import write_session_snapshot

        long_decision = "A" * 100  # 100 chars, well over 80-char threshold

        tasks = [
            {
                "id": "15",
                "subject": "CODE: auth",
                "status": "completed",
                "metadata": {
                    "handoff": {
                        "produced": ["src/auth.py"],
                        "decisions": [long_decision, "Short decision"],
                        "uncertainty": [],
                        "integration": [],
                        "open_questions": [],
                    }
                },
            }
        ]

        write_session_snapshot(
            tasks=tasks,
            project_slug="trunc-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "trunc-proj" / "last-session.md").read_text()
        # The first decision (used as summary) should be truncated
        expected_summary = "A" * 77 + "..."
        assert expected_summary in content
        # The full 100-char string should NOT appear in the completed task line
        assert long_decision not in content.split("## Key Decisions")[0]
        # But the full decision DOES appear in Key Decisions section (not truncated there)
        assert long_decision in content


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
    """Tests for session_end.cleanup_stale_teams() — session-scoped cleanup
    of the current team's directory and corresponding task directory."""

    def test_removes_specified_team(self, tmp_path):
        """Team directory should be removed when team_name is specified."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-abc123"
        team.mkdir(parents=True)
        (team / "config.json").write_text('{"members": []}')

        cleaned = cleanup_stale_teams(team_name="pact-abc123", teams_dir=str(teams_dir))
        assert "pact-abc123" in cleaned
        assert not team.exists()

    def test_removes_team_without_config(self, tmp_path):
        """Team directory with no config.json should still be removed."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-orphan"
        team.mkdir(parents=True)

        cleaned = cleanup_stale_teams(team_name="pact-orphan", teams_dir=str(teams_dir))
        assert "pact-orphan" in cleaned
        assert not team.exists()

    def test_does_not_touch_other_teams(self, tmp_path):
        """Only the specified team should be removed, not other pact-* teams."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"

        # Target team
        target = teams_dir / "pact-target"
        target.mkdir(parents=True)

        # Other team — must survive
        other = teams_dir / "pact-other"
        other.mkdir(parents=True)
        (other / "config.json").write_text('{"members": []}')

        cleaned = cleanup_stale_teams(team_name="pact-target", teams_dir=str(teams_dir))
        assert "pact-target" in cleaned
        assert not target.exists()
        assert other.exists()  # Untouched

    def test_also_removes_corresponding_task_dir(self, tmp_path):
        """When removing a team, should also remove ~/.claude/tasks/{team_name}."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        tasks_dir = tmp_path / "tasks"

        team = teams_dir / "pact-cleanup"
        team.mkdir(parents=True)

        task_dir = tasks_dir / "pact-cleanup"
        task_dir.mkdir(parents=True)
        (task_dir / "1.json").write_text('{"id": "1"}')

        cleaned = cleanup_stale_teams(team_name="pact-cleanup", teams_dir=str(teams_dir))
        assert "pact-cleanup" in cleaned
        assert not team.exists()
        assert not task_dir.exists()

    def test_no_error_when_task_dir_missing(self, tmp_path):
        """Should not fail if corresponding task dir doesn't exist."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-notasks"
        team.mkdir(parents=True)

        cleaned = cleanup_stale_teams(team_name="pact-notasks", teams_dir=str(teams_dir))
        assert "pact-notasks" in cleaned

    def test_returns_empty_when_team_dir_missing(self, tmp_path):
        """Should return empty list if the team directory doesn't exist."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        teams_dir.mkdir(parents=True)

        cleaned = cleanup_stale_teams(team_name="pact-nonexistent", teams_dir=str(teams_dir))
        assert cleaned == []

    def test_returns_empty_when_teams_dir_missing(self, tmp_path):
        """Should return empty list if the base teams directory doesn't exist."""
        from session_end import cleanup_stale_teams

        cleaned = cleanup_stale_teams(
            team_name="pact-abc",
            teams_dir=str(tmp_path / "nonexistent"),
        )
        assert cleaned == []

    def test_returns_empty_when_no_team_name(self, tmp_path):
        """Should return empty list if team_name is empty string."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        teams_dir.mkdir(parents=True)

        cleaned = cleanup_stale_teams(team_name="", teams_dir=str(teams_dir))
        assert cleaned == []

    def test_reads_team_name_from_env(self, tmp_path):
        """Should read team name from CLAUDE_CODE_TEAM_NAME env var."""
        from session_end import cleanup_stale_teams

        teams_dir = tmp_path / "teams"
        team = teams_dir / "pact-fromenv"
        team.mkdir(parents=True)

        env = {"CLAUDE_CODE_TEAM_NAME": "PACT-FROMENV"}
        with patch.dict("os.environ", env, clear=True):
            cleaned = cleanup_stale_teams(teams_dir=str(teams_dir))
        assert "pact-fromenv" in cleaned
        assert not team.exists()

    def test_returns_empty_when_no_env_and_no_arg(self, tmp_path):
        """Should return empty list when no team_name arg and no env var."""
        from session_end import cleanup_stale_teams

        with patch.dict("os.environ", {}, clear=True):
            cleaned = cleanup_stale_teams(teams_dir=str(tmp_path))
        assert cleaned == []

    def test_defaults_to_home_claude_teams(self):
        """Without teams_dir override, should use ~/.claude/teams/."""
        from session_end import cleanup_stale_teams

        # Just verify it doesn't crash when called with explicit team name
        # (it will look for the team under ~/.claude/teams/ which may not exist)
        result = cleanup_stale_teams(team_name="pact-nonexistent-test")
        assert isinstance(result, list)
        assert result == []
