# pact-plugin/tests/test_session_end.py
"""
Tests for session_end.py — SessionEnd hook for session lifecycle management.

session_end.py is purely observational — no destructive operations on project files.

Tests cover:
1. main() entry point: exit codes, error handling, journal event emission
2. check_unpaused_pr() — journal-based safety-net for unpaused PRs:
   - Reads session_paused events from journal (skip warning if paused)
   - Reads review_dispatch events from journal (primary PR detection)
   - Falls back to task metadata/handoff scanning for PR number
   - Writes warning to journal via append_event
3. cleanup_teachback_markers() — session-scoped marker cleanup
4. cleanup_old_sessions() — stale session directory removal
"""
import io
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestGetProjectSlug:
    """Tests for session_end.get_project_slug() — reads via get_project_dir()."""

    def test_returns_basename_from_project_dir(self):
        from session_end import get_project_slug

        with patch("session_end.get_project_dir", return_value="/Users/mj/Sites/my-project"):
            assert get_project_slug() == "my-project"

    def test_returns_empty_when_no_project_dir(self):
        from session_end import get_project_slug

        with patch("session_end.get_project_dir", return_value=""):
            assert get_project_slug() == ""


class TestMainEntryPoint:
    """Tests for session_end.main() exit behavior and call orchestration."""

    def _patch_main_deps(self, **overrides):
        """Return a combined context manager mocking main()'s dependencies.

        Default mocks: pact_context.init, get_project_dir, get_session_dir,
        get_session_id, get_team_name, get_task_list, append_event,
        check_unpaused_pr, cleanup_teachback_markers, cleanup_old_sessions.

        Pass keyword overrides to replace defaults (e.g., get_task_list=...).
        """
        from contextlib import ExitStack
        from unittest.mock import MagicMock, DEFAULT

        defaults = {
            "pact_context_init": patch("session_end.pact_context.init"),
            "get_project_dir": patch("session_end.get_project_dir",
                                     return_value="/Users/mj/Sites/my-project"),
            "get_session_dir": patch("session_end.get_session_dir", return_value=""),
            "get_session_id": patch("session_end.get_session_id", return_value=""),
            "get_team_name": patch("session_end.get_team_name", return_value="pact-abc12345"),
            "get_task_list": patch("session_end.get_task_list", return_value=[]),
            "append_event": patch("session_end.append_event"),
            "check_unpaused_pr": patch("session_end.check_unpaused_pr"),
            "cleanup_teachback_markers": patch("session_end.cleanup_teachback_markers"),
            "cleanup_old_sessions": patch("session_end.cleanup_old_sessions"),
        }
        defaults.update(overrides)
        return defaults

    def test_main_exits_0_on_success(self):
        from session_end import main

        patches = self._patch_main_deps()
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                for p in patches.values():
                    stack.enter_context(p)
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_exception(self):
        """main() should exit 0 even on errors (fire-and-forget)."""
        from session_end import main

        with patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.pact_context.init"), \
             patch("session_end.get_team_name", return_value=""), \
             patch("session_end.get_task_list", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_env_vars(self):
        from session_end import main

        patches = self._patch_main_deps()
        with patch.dict("os.environ", {}, clear=True), \
             patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                for p in patches.values():
                    stack.enter_context(p)
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0

    def test_main_writes_session_end_journal_event(self):
        """main() should write a session_end event to the journal."""
        from session_end import main

        patches = self._patch_main_deps()
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                mocks = {name: stack.enter_context(p) for name, p in patches.items()}
                with pytest.raises(SystemExit):
                    main()

        # append_event should have been called with a session_end event
        mock_append = mocks["append_event"]
        mock_append.assert_called()
        event_arg = mock_append.call_args[0][0]
        assert event_arg["type"] == "session_end"

    def test_main_passes_tasks_to_check_unpaused_pr(self):
        from session_end import main

        mock_tasks = [{"id": "1", "subject": "test", "status": "completed", "metadata": {}}]

        patches = self._patch_main_deps(
            get_task_list=patch("session_end.get_task_list", return_value=mock_tasks),
        )
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                mocks = {name: stack.enter_context(p) for name, p in patches.items()}
                with pytest.raises(SystemExit):
                    main()

        mock_unpaused = mocks["check_unpaused_pr"]
        mock_unpaused.assert_called_once()
        call_args = mock_unpaused.call_args
        assert call_args.kwargs["tasks"] == mock_tasks
        assert call_args.kwargs["project_slug"] == "my-project"

    def test_main_call_ordering(self):
        """main() must call functions in correct order:
        check_unpaused_pr -> cleanup_teachback_markers -> cleanup_old_sessions.
        """
        from session_end import main

        call_order = []

        patches = self._patch_main_deps(
            check_unpaused_pr=patch("session_end.check_unpaused_pr",
                side_effect=lambda **kw: call_order.append("check_unpaused_pr")),
            cleanup_teachback_markers=patch("session_end.cleanup_teachback_markers",
                side_effect=lambda **kw: call_order.append("cleanup_teachback_markers")),
            cleanup_old_sessions=patch("session_end.cleanup_old_sessions",
                side_effect=lambda **kw: call_order.append("cleanup_old_sessions")),
        )
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                for p in patches.values():
                    stack.enter_context(p)
                with pytest.raises(SystemExit):
                    main()

        assert call_order == [
            "check_unpaused_pr",
            "cleanup_teachback_markers",
            "cleanup_old_sessions",
        ]


# =============================================================================
# check_unpaused_pr() Tests
# =============================================================================

class TestCheckUnpausedPr:
    """Tests for session_end.check_unpaused_pr() — journal-based safety-net.

    Detects open PRs that were NOT paused (no memory consolidation), writing
    a warning event to the session journal for next-session pickup.

    Key behavior:
    - Reads session_paused events (if present → no warning)
    - Reads review_dispatch events (primary PR detection from journal)
    - Falls back to task metadata scanning (safety net for non-journal PRs)
    - Writes session_end warning event to journal
    """

    def _make_task_with_pr_number(self, pr_number):
        """Helper: task with pr_number in metadata."""
        return {
            "id": "1",
            "subject": "Review: auth feature",
            "status": "completed",
            "metadata": {"pr_number": pr_number},
        }

    def _make_task_with_pr_url(self, pr_url):
        """Helper: task with PR URL in handoff metadata."""
        return {
            "id": "2",
            "subject": "backend-coder: implement auth",
            "status": "completed",
            "metadata": {
                "handoff": {
                    "produced": ["src/auth.py"],
                    "decisions": ["Used JWT"],
                    "artifact": pr_url,
                }
            },
        }

    def test_detects_pr_number_in_task_metadata(self):
        """Should write warning event when pr_number found in task metadata."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(288)]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_called_once()
        event = mock_append.call_args[0][0]
        assert event["type"] == "session_end"
        assert "PR #288" in event["warning"]
        assert "pause-mode was not run" in event["warning"]

    def test_detects_pr_url_in_handoff_values(self):
        """Should extract PR number from github.com/pull/ URL in handoff metadata."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_url("https://github.com/owner/repo/pull/42")]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_called_once()
        event = mock_append.call_args[0][0]
        assert "PR #42" in event["warning"]

    def test_no_warning_when_session_paused_event_exists(self):
        """Should skip warning when journal has session_paused event."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(288)]

        def mock_read_events(team, event_type=None):
            if event_type == "session_paused":
                return [{"type": "session_paused", "pr_number": 288}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_detects_pr_from_review_dispatch_event(self):
        """Should detect PR from review_dispatch journal event (primary path)."""
        from session_end import check_unpaused_pr

        def mock_read_events(team, event_type=None):
            if event_type == "session_paused":
                return []
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 55}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=None,  # No tasks needed — journal has PR
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_called_once()
        event = mock_append.call_args[0][0]
        assert "PR #55" in event["warning"]

    def test_no_warning_when_no_pr_detected(self):
        """Should not write event when no PR found in journal or tasks."""
        from session_end import check_unpaused_pr

        tasks = [
            {"id": "1", "subject": "CODE: auth", "status": "completed", "metadata": {}},
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_no_warning_when_tasks_is_none_and_no_journal_pr(self):
        """Should return safely when tasks is None and no journal PR."""
        from session_end import check_unpaused_pr

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=None,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_no_warning_when_project_slug_empty(self):
        """Should return early when project_slug is empty."""
        from session_end import check_unpaused_pr

        with patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=[self._make_task_with_pr_number(100)],
                project_slug="",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_no_warning_when_tasks_empty_and_no_journal_pr(self):
        """Should return with no event for empty task list and no journal PR."""
        from session_end import check_unpaused_pr

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=[],
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_handles_malformed_pr_url(self):
        """Bare /pull/ without github.com domain should not detect PR."""
        from session_end import check_unpaused_pr

        tasks = [
            {
                "id": "1",
                "subject": "CODE: feature",
                "status": "completed",
                "metadata": {
                    "handoff": {
                        "produced": ["file.py"],
                        "notes": "See /pull/",
                    }
                },
            }
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_pr_number_metadata_takes_priority_over_url(self):
        """When task has both pr_number and URL, pr_number is used first."""
        from session_end import check_unpaused_pr

        tasks = [
            {
                "id": "1",
                "subject": "Review: feature",
                "status": "completed",
                "metadata": {
                    "pr_number": 100,
                    "handoff": {
                        "artifact": "https://github.com/org/repo/pull/999",
                    },
                },
            }
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        event = mock_append.call_args[0][0]
        assert "PR #100" in event["warning"]

    def test_non_string_handoff_values_skipped(self):
        """Non-string handoff values (dict/list) should be skipped without error."""
        from session_end import check_unpaused_pr

        tasks = [
            {
                "id": "1",
                "subject": "CODE: feature",
                "status": "completed",
                "metadata": {
                    "pr_number": 42,
                    "handoff": {
                        "produced": ["src/auth.py"],
                        "decisions": {"key": "value"},
                        "integration": 12345,
                        "notes": None,
                    },
                },
            }
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        event = mock_append.call_args[0][0]
        assert "PR #42" in event["warning"]

    def test_detects_full_github_pr_url(self):
        """Should detect PR from full github.com/org/repo/pull/N URL."""
        from session_end import check_unpaused_pr

        tasks = [
            {
                "id": "1",
                "subject": "backend-coder: implement auth",
                "status": "completed",
                "metadata": {
                    "handoff": {
                        "artifact": "https://github.com/owner/repo/pull/123",
                    }
                },
            }
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        event = mock_append.call_args[0][0]
        assert "PR #123" in event["warning"]

    def test_non_url_pull_text_not_detected(self):
        """Non-URL text with '/pull/' should NOT trigger detection."""
        from session_end import check_unpaused_pr

        tasks = [
            {
                "id": "1",
                "subject": "CODE: feature",
                "status": "completed",
                "metadata": {
                    "handoff": {
                        "notes": "See the /pull/ request for details",
                    }
                },
            }
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_handles_metadata_none_in_task(self):
        """Task with 'metadata': None should not crash (or {} guard handles it)."""
        from session_end import check_unpaused_pr

        tasks = [
            {
                "id": "1",
                "subject": "CODE: feature",
                "status": "completed",
                "metadata": None,
            },
        ]

        with patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                team_name="pact-test123",
            )

        mock_append.assert_not_called()

    def test_no_journal_write_when_team_name_empty(self):
        """Should not write journal event when team_name resolves to empty."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(42)]

        with patch("session_end.get_team_name", return_value=""), \
             patch("session_end.read_events", return_value=[]), \
             patch("session_end.append_event") as mock_append:
            check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
                # No team_name param — falls back to get_team_name() which returns ""
            )

        # No team → cannot read journal or write events
        mock_append.assert_not_called()


# =============================================================================
# cleanup_teachback_markers() Tests
# =============================================================================

class TestCleanupTeachbackMarkers:
    """Tests for session_end.cleanup_teachback_markers() — session-scoped cleanup."""

    def _create_markers(self, directory, names):
        """Helper: create teachback marker files in a directory."""
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).touch()

    def test_cleans_session_scoped_markers(self, tmp_path):
        """Should remove teachback-warned-* files from session_dir."""
        from session_end import cleanup_teachback_markers

        session_dir = tmp_path / "my-project" / "abc-123"
        self._create_markers(session_dir, [
            "teachback-warned-coder-1-42",
            "teachback-warned-coder-2-7",
        ])

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=str(session_dir),
            sessions_dir=str(tmp_path),
        )

        assert not list(session_dir.glob("teachback-warned-*"))

    def test_cleans_legacy_slug_level_markers(self, tmp_path):
        """Should sweep orphaned teachback markers at slug level (migration)."""
        from session_end import cleanup_teachback_markers

        slug_dir = tmp_path / "my-project"
        self._create_markers(slug_dir, [
            "teachback-warned-old-agent-1",
        ])

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=None,
            sessions_dir=str(tmp_path),
        )

        assert not list(slug_dir.glob("teachback-warned-*"))

    def test_preserves_non_marker_files(self, tmp_path):
        """Should not delete non-marker files (last-session.md, paused-state.json)."""
        from session_end import cleanup_teachback_markers

        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        (slug_dir / "last-session.md").write_text("# Session")
        (slug_dir / "paused-state.json").write_text("{}")
        self._create_markers(slug_dir, ["teachback-warned-agent-1"])

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=None,
            sessions_dir=str(tmp_path),
        )

        assert (slug_dir / "last-session.md").exists()
        assert (slug_dir / "paused-state.json").exists()
        assert not (slug_dir / "teachback-warned-agent-1").exists()

    def test_skips_when_no_project_slug(self, tmp_path):
        from session_end import cleanup_teachback_markers

        # Should not raise
        cleanup_teachback_markers(
            project_slug="",
            session_dir=None,
            sessions_dir=str(tmp_path),
        )

    def test_handles_missing_directories(self, tmp_path):
        from session_end import cleanup_teachback_markers

        # Should not raise even if directories don't exist
        cleanup_teachback_markers(
            project_slug="nonexistent",
            session_dir=str(tmp_path / "missing" / "session"),
            sessions_dir=str(tmp_path),
        )

    def test_continues_when_single_unlink_fails(self, tmp_path):
        """If one marker file can't be deleted, sweep should continue to others.

        Exercises the inner `except OSError: pass` in _sweep_teachback_markers()
        (session_end.py ~line 275). A read-only marker should not prevent
        cleanup of subsequent markers in the same directory.
        """
        from session_end import _sweep_teachback_markers

        directory = tmp_path / "session-dir"
        directory.mkdir()

        # Create three marker files
        marker_a = directory / "teachback-warned-agent-a-1"
        marker_b = directory / "teachback-warned-agent-b-2"
        marker_c = directory / "teachback-warned-agent-c-3"
        marker_a.touch()
        marker_b.touch()
        marker_c.touch()

        # Make marker_b read-only so unlink() raises PermissionError (OSError)
        marker_b.chmod(0o444)
        # Also make the parent dir read-only to prevent unlink on that file
        # (on some systems, unlink requires write permission on parent dir)
        # Instead, mock unlink for just that file to guarantee the OSError
        original_unlink = Path.unlink

        def selective_unlink(self_path, *args, **kwargs):
            if self_path.name == "teachback-warned-agent-b-2":
                raise OSError("Permission denied (simulated)")
            return original_unlink(self_path, *args, **kwargs)

        from unittest.mock import patch
        with patch.object(Path, "unlink", selective_unlink):
            _sweep_teachback_markers(directory)

        # marker_a and marker_c should be deleted; marker_b should survive
        assert not marker_a.exists(), "marker_a should have been deleted"
        assert marker_b.exists(), "marker_b should survive (unlink failed)"
        assert not marker_c.exists(), "marker_c should have been deleted"


# =============================================================================
# cleanup_old_sessions() Tests
# =============================================================================

class TestCleanupOldSessions:
    """Tests for session_end.cleanup_old_sessions() — stale session directory removal."""

    def _create_session_dir(self, slug_dir, session_id, age_days=0):
        """Helper: create a session directory with controlled mtime."""
        import time as _time
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        # Write a file so the directory has content
        (session_dir / "pact-session-context.json").write_text("{}")
        if age_days > 0:
            old_time = _time.time() - (age_days * 86400)
            import os as _os
            _os.utime(str(session_dir), (old_time, old_time))
        return session_dir

    def test_removes_old_session_directories(self, tmp_path):
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        self._create_session_dir(slug_dir, old_id, age_days=10)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert (slug_dir / current_id).exists()
        assert not (slug_dir / old_id).exists()

    def test_skips_current_session(self, tmp_path):
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        self._create_session_dir(slug_dir, current_id, age_days=30)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        # Current session must survive even if older than threshold
        assert (slug_dir / current_id).exists()

    def test_skips_non_uuid_directories(self, tmp_path):
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        # Create a non-UUID directory
        non_uuid_dir = slug_dir / "not-a-uuid"
        non_uuid_dir.mkdir()

        self._create_session_dir(slug_dir, current_id, age_days=0)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert non_uuid_dir.exists()

    def test_skips_files_at_slug_level(self, tmp_path):
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        # Create slug-level files
        (slug_dir / "last-session.md").write_text("# Session")
        (slug_dir / "paused-state.json").write_text("{}")

        self._create_session_dir(slug_dir, current_id, age_days=0)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert (slug_dir / "last-session.md").exists()
        assert (slug_dir / "paused-state.json").exists()

    def test_keeps_recent_sessions(self, tmp_path):
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        recent_id = "22222222-3333-4444-5555-666666666666"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        self._create_session_dir(slug_dir, recent_id, age_days=3)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert (slug_dir / recent_id).exists()

    def test_handles_missing_slug_directory(self, tmp_path):
        from session_end import cleanup_old_sessions

        # Should not raise
        cleanup_old_sessions(
            project_slug="nonexistent",
            current_session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

    def test_skips_when_no_project_slug(self, tmp_path):
        from session_end import cleanup_old_sessions

        # Should not raise
        cleanup_old_sessions(
            project_slug="",
            current_session_id="abc",
            sessions_dir=str(tmp_path),
        )

    def test_skips_when_no_current_session_id(self, tmp_path):
        from session_end import cleanup_old_sessions

        # Should not raise
        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id="",
            sessions_dir=str(tmp_path),
        )


# =============================================================================
# cleanup_old_sessions() — Adversarial/Boundary Cases (Test Engineer)
# =============================================================================

class TestCleanupOldSessionsBoundary:
    """Boundary and adversarial tests for cleanup_old_sessions()."""

    def _create_session_dir(self, slug_dir, session_id, age_days=0):
        """Helper: create a session directory with controlled mtime."""
        import os as _os
        import time as _time
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text("{}")
        if age_days > 0:
            old_time = _time.time() - (age_days * 86400)
            _os.utime(str(session_dir), (old_time, old_time))
        return session_dir

    def test_exactly_at_boundary_not_deleted(self, tmp_path):
        """Directory at 6.9 days age should NOT be deleted.

        The code uses `age_days > max_age_days` (strictly greater than).
        We use 6.9 days (safely under 7) to avoid flakiness from time
        elapsing between utime() and the stat() call inside cleanup.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        boundary_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        boundary_dir = slug_dir / boundary_id
        boundary_dir.mkdir(parents=True, exist_ok=True)
        (boundary_dir / "context.json").write_text("{}")
        # Set to 6.9 days — safely under threshold
        under_time = _time.time() - (6.9 * 86400)
        _os.utime(str(boundary_dir), (under_time, under_time))

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        # Under threshold — should survive
        assert boundary_dir.exists()

    def test_just_over_boundary_deleted(self, tmp_path):
        """Directory at 7.01 days should be deleted (strictly greater than)."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        old_dir = slug_dir / old_id
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "context.json").write_text("{}")
        over_time = _time.time() - (7.01 * 86400)
        _os.utime(str(old_dir), (over_time, over_time))

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert not old_dir.exists()

    def test_multiple_old_dirs_all_cleaned(self, tmp_path):
        """Multiple stale session dirs should all be removed in a single sweep."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        old_ids = [
            "11111111-2222-3333-4444-555555555555",
            "22222222-3333-4444-5555-666666666666",
            "33333333-4444-5555-6666-777777777777",
        ]
        for oid in old_ids:
            self._create_session_dir(slug_dir, oid, age_days=14)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        for oid in old_ids:
            assert not (slug_dir / oid).exists()
        assert (slug_dir / current_id).exists()

    def test_non_empty_old_dir_still_removed(self, tmp_path):
        """Old session dirs with files inside should be fully removed (shutil.rmtree)."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        # Create dir WITHOUT age_days first — write files, THEN set mtime
        old_dir = slug_dir / old_id
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "pact-session-context.json").write_text("{}")
        (old_dir / "teachback-warned-coder-1-42").touch()
        (old_dir / "some-other-artifact.json").write_text("{}")
        # Set mtime AFTER all writes (writing updates dir mtime on Unix)
        old_time = _time.time() - (10 * 86400)
        _os.utime(str(old_dir), (old_time, old_time))

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert not old_dir.exists()

    def test_uuid_format_validation_rejects_partial_uuid(self, tmp_path):
        """Partial UUIDs (too short, wrong format) should not be cleaned up."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        # These look UUID-ish but don't match the full pattern
        partial = slug_dir / "aaaaaaaa-bbbb-cccc-dddd"
        partial.mkdir(parents=True)
        old_time = _time.time() - (30 * 86400)
        _os.utime(str(partial), (old_time, old_time))

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        # Partial UUID should survive — regex doesn't match
        assert partial.exists()

    def test_uuid_regex_rejects_uppercase(self, tmp_path):
        """UUID regex should only match lowercase hex characters [0-9a-f].

        On case-sensitive filesystems, uppercase UUIDs would be separate
        directories. The regex explicitly requires lowercase. This test
        verifies the regex behavior by checking the pattern directly.
        """
        import re
        from session_end import _UUID_PATTERN

        # Lowercase should match
        assert _UUID_PATTERN.match("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        # Uppercase should NOT match
        assert not _UUID_PATTERN.match("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
        # Mixed case should NOT match
        assert not _UUID_PATTERN.match("Aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    def test_custom_max_age_days(self, tmp_path):
        """Custom max_age_days parameter should be respected."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        target_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id, age_days=0)
        self._create_session_dir(slug_dir, target_id, age_days=4)

        # With default 7-day threshold, 4-day-old dir should survive
        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )
        assert (slug_dir / target_id).exists()

        # With 3-day threshold, 4-day-old dir should be cleaned
        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=3,
        )
        assert not (slug_dir / target_id).exists()


# =============================================================================
# Cleanup Migration Scenario — Combined Session + Slug Level (Test Engineer)
# =============================================================================

class TestCleanupMigrationScenario:
    """Test the migration scenario where both legacy (slug-level) and new
    (session-scoped) markers coexist.

    After upgrading to #345, existing projects may have orphaned teachback
    markers at the slug level from previous sessions. The cleanup should
    remove both levels without interfering with non-marker files.
    """

    def test_both_levels_cleaned_simultaneously(self, tmp_path):
        """Both session-scoped and legacy markers should be cleaned in one call."""
        from session_end import cleanup_teachback_markers

        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        session_dir = slug_dir / "abc-123-session"
        session_dir.mkdir()

        # Legacy (slug-level) marker
        (slug_dir / "teachback-warned-old-coder").touch()
        # Session-scoped marker
        (session_dir / "teachback-warned-new-coder-42").touch()
        # Non-marker file at slug level
        (slug_dir / "last-session.md").write_text("# Session")

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=str(session_dir),
            sessions_dir=str(tmp_path),
        )

        # Both markers cleaned
        assert not (slug_dir / "teachback-warned-old-coder").exists()
        assert not (session_dir / "teachback-warned-new-coder-42").exists()
        # Non-marker preserved
        assert (slug_dir / "last-session.md").exists()

    def test_session_dir_markers_not_affected_by_slug_sweep(self, tmp_path):
        """Slug-level sweep should not descend into session directories.

        _sweep_teachback_markers() uses iterdir() (not recursive glob), so
        markers in subdirectories are only cleaned if session_dir is explicitly
        provided.
        """
        from session_end import cleanup_teachback_markers

        slug_dir = tmp_path / "my-project"
        session_dir = slug_dir / "session-abc"
        session_dir.mkdir(parents=True)

        (session_dir / "teachback-warned-coder-1").touch()

        # Only slug-level sweep (session_dir=None)
        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=None,
            sessions_dir=str(tmp_path),
        )

        # Session-dir markers should survive because slug sweep doesn't recurse
        assert (session_dir / "teachback-warned-coder-1").exists()

    def test_empty_session_dir_survives_cleanup(self, tmp_path):
        """Session directory itself should not be removed by marker cleanup."""
        from session_end import cleanup_teachback_markers

        slug_dir = tmp_path / "my-project"
        session_dir = slug_dir / "session-abc"
        session_dir.mkdir(parents=True)

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=str(session_dir),
            sessions_dir=str(tmp_path),
        )

        # Directory itself should survive
        assert session_dir.exists()


# =============================================================================
# main() Integration — Full SessionEnd Flow (Test Engineer)
# =============================================================================

class TestMainIntegrationCleanup:
    """Integration tests for main() exercising cleanup functions with session context.

    Verifies that main() correctly chains pact_context.init() -> get_session_dir()
    -> cleanup_teachback_markers() -> cleanup_old_sessions() using the session
    context from stdin.
    """

    def test_main_calls_cleanup_teachback_markers(self):
        """main() should call cleanup_teachback_markers with session context."""
        from unittest.mock import patch, MagicMock
        import io

        input_data = json.dumps({"session_id": "test-session"})

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("session_end.pact_context") as mock_ctx, \
             patch("session_end.get_project_dir", return_value="/test/proj"), \
             patch("session_end.get_session_dir", return_value="/tmp/session"), \
             patch("session_end.get_session_id", return_value="test-session"), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.check_unpaused_pr"), \
             patch("session_end.cleanup_teachback_markers") as mock_cleanup, \
             patch("session_end.cleanup_old_sessions"), \
             pytest.raises(SystemExit):
            mock_ctx.init = MagicMock()
            from session_end import main
            main()

        mock_cleanup.assert_called_once_with(
            project_slug="proj",
            session_dir="/tmp/session",
        )

    def test_main_calls_cleanup_old_sessions(self):
        """main() should call cleanup_old_sessions with session context."""
        from unittest.mock import patch, MagicMock
        import io

        input_data = json.dumps({"session_id": "test-session"})

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("session_end.pact_context") as mock_ctx, \
             patch("session_end.get_project_dir", return_value="/test/proj"), \
             patch("session_end.get_session_dir", return_value="/tmp/session"), \
             patch("session_end.get_session_id", return_value="test-session"), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.check_unpaused_pr"), \
             patch("session_end.cleanup_teachback_markers"), \
             patch("session_end.cleanup_old_sessions") as mock_cleanup, \
             pytest.raises(SystemExit):
            mock_ctx.init = MagicMock()
            from session_end import main
            main()

        mock_cleanup.assert_called_once_with(
            project_slug="proj",
            current_session_id="test-session",
        )
