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
            "cleanup_old_teams": patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            "cleanup_old_tasks": patch("session_end.cleanup_old_tasks", return_value=(0, 0)),
            "_cleanup_old_checkpoints": patch("session_end._cleanup_old_checkpoints"),
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
        # (main() also emits a cleanup_summary event after the reapers,
        # so filter by type rather than inspecting the last call.)
        mock_append = mocks["append_event"]
        mock_append.assert_called()
        event_types = [c.args[0]["type"] for c in mock_append.call_args_list]
        assert "session_end" in event_types

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
        check_unpaused_pr -> cleanup_teachback_markers -> cleanup_old_sessions
        -> _cleanup_old_checkpoints.
        check_unpaused_pr now runs BEFORE the journal write so its return
        value can be merged into the single session_end event.
        """
        from session_end import main

        call_order = []

        def _record(name):
            def _side_effect(*args, **kw):
                call_order.append(name)
                return None  # check_unpaused_pr returns Optional[str]; _cleanup_old_checkpoints is called with no args
            return _side_effect

        def _record_tuple(name):
            def _side_effect(*args, **kw):
                call_order.append(name)
                return (0, 0)
            return _side_effect

        patches = self._patch_main_deps(
            check_unpaused_pr=patch("session_end.check_unpaused_pr",
                side_effect=_record("check_unpaused_pr")),
            cleanup_teachback_markers=patch("session_end.cleanup_teachback_markers",
                side_effect=_record("cleanup_teachback_markers")),
            cleanup_old_sessions=patch("session_end.cleanup_old_sessions",
                side_effect=_record("cleanup_old_sessions")),
            cleanup_old_teams=patch("session_end.cleanup_old_teams",
                side_effect=_record_tuple("cleanup_old_teams")),
            cleanup_old_tasks=patch("session_end.cleanup_old_tasks",
                side_effect=_record_tuple("cleanup_old_tasks")),
            _cleanup_old_checkpoints=patch("session_end._cleanup_old_checkpoints",
                side_effect=_record("_cleanup_old_checkpoints")),
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
            "cleanup_old_teams",
            "cleanup_old_tasks",
            "_cleanup_old_checkpoints",
        ]

    def test_main_emits_single_session_end_event_when_warning(self):
        """When check_unpaused_pr returns a warning, main() emits exactly
        ONE session_end event with the warning attached (not two events)."""
        from session_end import main

        warning_text = "Session ended without memory consolidation. PR #99 is open."
        patches = self._patch_main_deps(
            check_unpaused_pr=patch("session_end.check_unpaused_pr",
                                    return_value=warning_text),
        )
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                mocks = {name: stack.enter_context(p) for name, p in patches.items()}
                with pytest.raises(SystemExit):
                    main()

        mock_append = mocks["append_event"]
        # Exactly one session_end event — not two (regression test for
        # the old "session_end then session_end+warning" double-write bug).
        # Filter by type: main() also emits cleanup_summary after the reapers.
        session_end_events = [
            c.args[0] for c in mock_append.call_args_list
            if c.args[0]["type"] == "session_end"
        ]
        assert len(session_end_events) == 1
        assert session_end_events[0].get("warning") == warning_text

    def test_main_emits_single_session_end_event_no_warning(self):
        """When check_unpaused_pr returns None, main() emits exactly ONE
        session_end event with NO warning field."""
        from session_end import main

        patches = self._patch_main_deps(
            check_unpaused_pr=patch("session_end.check_unpaused_pr",
                                    return_value=None),
        )
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                mocks = {name: stack.enter_context(p) for name, p in patches.items()}
                with pytest.raises(SystemExit):
                    main()

        mock_append = mocks["append_event"]
        # Filter by type: main() also emits cleanup_summary after the reapers.
        session_end_events = [
            c.args[0] for c in mock_append.call_args_list
            if c.args[0]["type"] == "session_end"
        ]
        assert len(session_end_events) == 1
        assert "warning" not in session_end_events[0]

    def test_main_continues_cleanup_when_journal_write_fails(self):
        """If append_event raises, main() must still call cleanup functions
        (regression test for the bare-write single-point-of-failure bug)."""
        from session_end import main

        patches = self._patch_main_deps(
            append_event=patch("session_end.append_event",
                               side_effect=RuntimeError("disk full")),
        )
        with patch("sys.stdin", io.StringIO("{}")):
            with ExitStack() as stack:
                mocks = {name: stack.enter_context(p) for name, p in patches.items()}
                with pytest.raises(SystemExit) as exc_info:
                    main()

        # Exit 0 (fire-and-forget)
        assert exc_info.value.code == 0
        # Cleanup steps still ran despite the journal write failure
        mocks["cleanup_teachback_markers"].assert_called_once()
        mocks["cleanup_old_sessions"].assert_called_once()


# =============================================================================
# check_unpaused_pr() Tests
# =============================================================================

class TestCheckUnpausedPr:
    """Tests for session_end.check_unpaused_pr() — journal-based safety-net.

    Detects open PRs that were NOT paused (no memory consolidation), returning
    a warning string that the caller attaches to the single session_end event.

    Key behavior:
    - Compares session_paused vs review_dispatch event timestamps:
      pause covers PR only when last_pause_ts >= last_review_ts.
    - Reads review_dispatch events (primary PR detection from journal)
    - Falls back to task metadata scanning (safety net for non-journal PRs)
    - Returns the warning string (or None) instead of writing the journal
      directly — the caller emits the single session_end event.
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
        """Should return warning string when pr_number found in task metadata."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(288)]

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #288" in warning
        assert "pause-mode was not run" in warning

    def test_detects_pr_url_in_handoff_values(self):
        """Should extract PR number from github.com/pull/ URL in handoff metadata."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_url("https://github.com/owner/repo/pull/42")]

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

    def test_no_warning_when_session_paused_event_exists(self):
        """Should return None when journal has only session_paused (no review)."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(288)]

        def mock_read_events(event_type=None):
            if event_type == "session_paused":
                return [{"type": "session_paused", "pr_number": 288, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is None

    def test_detects_pr_from_review_dispatch_event(self):
        """Should detect PR from review_dispatch journal event (primary path)."""
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_paused":
                return []
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 55, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,  # No tasks needed — journal has PR
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #55" in warning

    def test_no_warning_when_no_pr_detected(self):
        """Should return None when no PR found in journal or tasks."""
        from session_end import check_unpaused_pr

        tasks = [
            {"id": "1", "subject": "CODE: auth", "status": "completed", "metadata": {}},
        ]

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is None

    def test_no_warning_when_tasks_is_none_and_no_journal_pr(self):
        """Should return None when tasks is None and no journal PR."""
        from session_end import check_unpaused_pr

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_no_warning_when_project_slug_empty(self):
        """Should return None early when project_slug is empty."""
        from session_end import check_unpaused_pr

        warning = check_unpaused_pr(
            tasks=[self._make_task_with_pr_number(100)],
            project_slug="",
        )

        assert warning is None

    def test_no_warning_when_tasks_empty_and_no_journal_pr(self):
        """Should return None for empty task list and no journal PR."""
        from session_end import check_unpaused_pr

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=[],
                project_slug="proj",
            )

        assert warning is None

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

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is None

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

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #100" in warning

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

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

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

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #123" in warning

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

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is None

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

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="proj",
            )

        assert warning is None

    def test_no_journal_write_when_project_slug_empty(self):
        """Should return None (no warning) when project_slug is empty."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(42)]

        with patch("session_end.read_events", return_value=[]):
            warning = check_unpaused_pr(
                tasks=tasks,
                project_slug="",
            )

        # Empty project_slug → early return, no warning
        assert warning is None

    # ========================================================================
    # M2 — pause-vs-review timestamp reconciliation tests
    # ========================================================================

    def test_unpaused_pr_after_earlier_pause(self):
        """pause→resume→new PR→quit: pause is OLDER than review → warn."""
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_paused":
                return [{"type": "session_paused", "pr_number": 10, "ts": "2026-01-01T00:00:00Z"}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 20, "ts": "2026-01-02T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #20" in warning

    def test_paused_after_review_no_warning(self):
        """Pause after review covers the current PR → no warning."""
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_paused":
                return [{"type": "session_paused", "pr_number": 20, "ts": "2026-01-02T00:00:01Z"}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 20, "ts": "2026-01-02T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_equal_timestamps_bias_toward_paused(self):
        """Equal pause/review timestamps → bias toward paused (no warning).

        ISO timestamps have 1-second precision; using `>=` means a tied
        timestamp is treated as covered by the pause to avoid spurious
        warnings.
        """
        from session_end import check_unpaused_pr

        ts = "2026-01-02T00:00:00Z"

        def mock_read_events(event_type=None):
            if event_type == "session_paused":
                return [{"type": "session_paused", "pr_number": 20, "ts": ts}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 20, "ts": ts}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_paused_only_no_review_no_warning(self):
        """Paused but no review_dispatch → no warning (paused, no PRs)."""
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_paused":
                return [{"type": "session_paused", "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None


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
        """Should not delete non-marker files in the slug directory."""
        from session_end import cleanup_teachback_markers

        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        (slug_dir / "notes.txt").write_text("keep me")
        (slug_dir / "config.json").write_text("{}")
        self._create_markers(slug_dir, ["teachback-warned-agent-1"])

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=None,
            sessions_dir=str(tmp_path),
        )

        assert (slug_dir / "notes.txt").exists()
        assert (slug_dir / "config.json").exists()
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

        # Create slug-level files (non-directory entries should be ignored)
        (slug_dir / "notes.txt").write_text("keep me")
        (slug_dir / "config.json").write_text("{}")

        self._create_session_dir(slug_dir, current_id, age_days=0)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
            max_age_days=7,
        )

        assert (slug_dir / "notes.txt").exists()
        assert (slug_dir / "config.json").exists()

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
            self._create_session_dir(slug_dir, oid, age_days=31)

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
        old_time = _time.time() - (31 * 86400)
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
        (slug_dir / "notes.txt").write_text("keep me")

        cleanup_teachback_markers(
            project_slug="my-project",
            session_dir=str(session_dir),
            sessions_dir=str(tmp_path),
        )

        # Both markers cleaned
        assert not (slug_dir / "teachback-warned-old-coder").exists()
        assert not (session_dir / "teachback-warned-new-coder-42").exists()
        # Non-marker preserved
        assert (slug_dir / "notes.txt").exists()

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
    -> cleanup_teachback_markers() -> cleanup_old_sessions() ->
    _cleanup_old_checkpoints() using the session context from stdin.
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
             patch("session_end._cleanup_old_checkpoints"), \
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
             patch("session_end._cleanup_old_checkpoints"), \
             pytest.raises(SystemExit):
            mock_ctx.init = MagicMock()
            from session_end import main
            main()

        mock_cleanup.assert_called_once_with(
            project_slug="proj",
            current_session_id="test-session",
        )

    def test_main_calls_cleanup_old_checkpoints(self):
        """main() should call _cleanup_old_checkpoints (pact-refresh TTL sweep).

        Wiring guard: removing the call from session_end.main() must break
        at least one test. Post-#413, _cleanup_old_checkpoints is the
        third cleanup step and touches ~/.claude/pact-refresh/.
        """
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
             patch("session_end.cleanup_old_sessions"), \
             patch("session_end._cleanup_old_checkpoints") as mock_cleanup, \
             pytest.raises(SystemExit):
            mock_ctx.init = MagicMock()
            from session_end import main
            main()

        mock_cleanup.assert_called_once_with()


# =============================================================================
# _is_paused_session() Tests
# =============================================================================

class TestIsPausedSession:
    """Tests for session_end._is_paused_session() — paused-session detection.

    Semantics: a session is "paused" iff its journal contains ANY
    session_paused event, regardless of later session_end events. This
    is a "has-ever-been-paused" predicate. The caller applies a longer
    TTL (180 days) to paused sessions to preserve in-progress work
    across the pause→quit→session_end race (AdvF1) and equal-timestamp
    ties (BugF2).
    """

    def _write_journal(self, session_dir, events):
        """Helper: write events to a session's journal file."""
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(e) + "\n" for e in events]
        journal.write_text("".join(lines))

    def test_returns_true_for_paused_only(self, tmp_path):
        """Session with session_paused but no session_end is paused."""
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-abc")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
        ])

        assert _is_paused_session(session_dir) is True

    def test_returns_true_for_paused_then_ended(self, tmp_path):
        """Paused → ended: still counts as paused under new semantics.

        Previously this returned False (old "is-currently-paused" predicate).
        Under the new "has-ever-been-paused" semantics, the presence of
        any session_paused event is sufficient — the subsequent
        session_end does not un-pause the session from the cleanup
        policy's perspective. The caller applies the 180-day paused
        TTL to this session instead of the 30-day active TTL.
        """
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-abc")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-02T00:00:00Z"},
        ])

        assert _is_paused_session(session_dir) is True

    def test_returns_true_for_pause_quit_race(self, tmp_path):
        """AdvF1: /PACT:pause then quit Claude Code ~1s later.

        The real-world flow: user runs /PACT:pause (writes
        session_paused), then quits Claude Code, which fires session_end
        a moment later. Under the old semantics, session_end.ts >=
        session_paused.ts caused _is_paused_session to return False and
        the paused state was deleted at the 30-day TTL. Under the new
        semantics, the session_paused event is sufficient.
        """
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-race")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
            # session_end fires ~1s later when the CC process shuts down
            {"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:01Z"},
        ])

        assert _is_paused_session(session_dir) is True

    def test_returns_true_for_equal_timestamp_tie(self, tmp_path):
        """BugF2: equal-ts tie (paused.ts == ended.ts) due to 1-Hz ISO precision.

        ISO timestamps have 1-second precision, so if /PACT:pause and
        the subsequent session_end both land in the same wall-clock
        second, their `ts` fields are equal. Under the old `>=` check
        this caused _is_paused_session to return False (data loss).
        Under the new semantics the session_paused event is sufficient.
        """
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-tie")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:00Z"},
        ])

        assert _is_paused_session(session_dir) is True

    def test_returns_true_for_paused_after_ended(self, tmp_path):
        """Paused → ended → paused sequence: still paused (was F1 fix, still holds).

        Under the new semantics this is trivially true — any
        session_paused event is sufficient. Kept as a regression test
        to ensure the read_last_event_from path still finds the latest
        session_paused without being confused by intervening
        session_end events.
        """
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-repaused")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-02T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 43, "ts": "2026-01-03T00:00:00Z"},
        ])

        assert _is_paused_session(session_dir) is True

    def test_returns_false_for_no_paused_event(self, tmp_path):
        """Session without session_paused is not paused."""
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-abc")
        self._write_journal(session_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:00Z"},
        ])

        assert _is_paused_session(session_dir) is False

    def test_returns_false_for_missing_journal(self, tmp_path):
        """Session directory with no journal file — returns False (fail-open)."""
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-missing")
        Path(session_dir).mkdir(parents=True, exist_ok=True)

        assert _is_paused_session(session_dir) is False

    def test_returns_false_for_malformed_journal(self, tmp_path):
        """Malformed journal — returns False (fail-open, Scenario 10)."""
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-bad")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("this is not json\nanother bad line\n")

        assert _is_paused_session(session_dir) is False

    def test_returns_false_for_empty_journal(self, tmp_path):
        """Empty journal file — returns False."""
        from session_end import _is_paused_session

        session_dir = str(tmp_path / "sess-empty")
        journal = Path(session_dir) / "session-journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text("")

        assert _is_paused_session(session_dir) is False


# =============================================================================
# cleanup_old_sessions() — Paused Session Preservation Tests
# =============================================================================

class TestCleanupPausedPreservation:
    """Tests for paused-session preservation in cleanup_old_sessions().

    Dual-TTL semantics (AdvF1/BugF2 fix): any session that has ever
    recorded a session_paused event uses the extended paused TTL
    (_PAUSED_SESSION_MAX_AGE_DAYS, default 180 days). Active sessions
    use the standard TTL (_SESSION_MAX_AGE_DAYS, default 30 days).
    The presence of a later session_end does NOT downgrade a paused
    session to the active TTL — this closes the pause→quit race and
    the equal-timestamp tie that previously caused silent data loss.

    Note: _set_age() must be called AFTER writing journal files, because
    writing into a directory updates its mtime on Unix/macOS.
    """

    def _create_session_dir(self, slug_dir, session_id):
        """Helper: create a session directory (without setting age)."""
        session_dir = slug_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pact-session-context.json").write_text("{}")
        return session_dir

    def _set_age(self, session_dir, age_days):
        """Set directory mtime to simulate age. Call AFTER writing all files."""
        import os as _os
        import time as _time
        old_time = _time.time() - (age_days * 86400)
        _os.utime(str(session_dir), (old_time, old_time))

    def _write_journal(self, session_dir, events):
        """Helper: write events to a session's journal."""
        journal = session_dir / "session-journal.jsonl"
        lines = [json.dumps(e) + "\n" for e in events]
        journal.write_text("".join(lines))

    def test_preserves_paused_session_beyond_ttl(self, tmp_path):
        """Scenario 9: Paused session (no session_end) survives cleanup."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        paused_id = "11111111-2222-3333-4444-555555555555"

        self._create_session_dir(slug_dir, current_id)
        paused_dir = self._create_session_dir(slug_dir, paused_id)
        self._write_journal(paused_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
        ])
        self._set_age(paused_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        # Paused session must survive despite being 35 days old
        assert paused_dir.exists()

    def test_preserves_paused_ended_session_at_35_days(self, tmp_path):
        """AdvF1/BugF2 fix: paused→ended session survives the 30-day TTL.

        Under the old semantics, a session that recorded session_paused
        and then session_end was treated as "no longer paused" and
        deleted at 30 days — the pause→quit race (AdvF1) and the
        equal-ts tie (BugF2) both produced this state and silently
        lost user data. Under dual-TTL semantics, any paused session
        uses the 180-day TTL, so a 35-day-old paused+ended session
        survives.
        """
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        paused_ended_id = "22222222-3333-4444-5555-666666666666"

        self._create_session_dir(slug_dir, current_id)
        paused_ended_dir = self._create_session_dir(slug_dir, paused_ended_id)
        self._write_journal(paused_ended_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:01Z"},
        ])
        self._set_age(paused_ended_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        # Paused session (even if also ended) survives beyond 30-day TTL
        assert paused_ended_dir.exists()

    def test_preserves_paused_session_at_100_days(self, tmp_path):
        """Dual-TTL: paused session 100 days old still survives.

        100 days > 30-day active TTL but < 180-day paused TTL, so the
        session must survive.
        """
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        paused_id = "33333333-4444-5555-6666-777777777777"

        self._create_session_dir(slug_dir, current_id)
        paused_dir = self._create_session_dir(slug_dir, paused_id)
        self._write_journal(paused_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2026-01-01T01:00:00Z"},
        ])
        self._set_age(paused_dir, 100)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert paused_dir.exists()

    def test_cleans_paused_session_beyond_paused_ttl(self, tmp_path):
        """Dual-TTL: paused sessions eventually age out past 180 days.

        A 200-day-old paused session exceeds the paused TTL and must
        be cleaned — the extended TTL is protection, not permanent
        retention.
        """
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        ancient_id = "44444444-5555-6666-7777-888888888888"

        self._create_session_dir(slug_dir, current_id)
        ancient_dir = self._create_session_dir(slug_dir, ancient_id)
        self._write_journal(ancient_dir, [
            {"v": 1, "type": "session_start", "ts": "2025-06-01T00:00:00Z"},
            {"v": 1, "type": "session_paused", "pr_number": 42, "ts": "2025-06-01T01:00:00Z"},
        ])
        self._set_age(ancient_dir, 200)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert not ancient_dir.exists()

    def test_malformed_journal_allows_cleanup(self, tmp_path):
        """Scenario 10: Malformed journal in old session — cleanup proceeds (fail-open)."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        bad_id = "33333333-4444-5555-6666-777777777777"

        self._create_session_dir(slug_dir, current_id)
        bad_dir = self._create_session_dir(slug_dir, bad_id)
        # Write malformed journal
        (bad_dir / "session-journal.jsonl").write_text("not json\ngarbage\n")
        self._set_age(bad_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        # Malformed journal -> _is_paused_session returns False -> cleaned
        assert not bad_dir.exists()

    def test_preserves_paused_cleans_non_paused(self, tmp_path):
        """Mixed cleanup: paused survives, non-paused cleaned."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        paused_id = "11111111-2222-3333-4444-555555555555"
        stale_id = "22222222-3333-4444-5555-666666666666"

        self._create_session_dir(slug_dir, current_id)

        # Paused session
        paused_dir = self._create_session_dir(slug_dir, paused_id)
        self._write_journal(paused_dir, [
            {"v": 1, "type": "session_paused", "pr_number": 99, "ts": "2026-01-01T00:00:00Z"},
        ])
        self._set_age(paused_dir, 35)

        # Non-paused stale session
        stale_dir = self._create_session_dir(slug_dir, stale_id)
        self._write_journal(stale_dir, [
            {"v": 1, "type": "session_start", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "session_end", "ts": "2026-01-01T01:00:00Z"},
        ])
        self._set_age(stale_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert paused_dir.exists(), "Paused session should survive"
        assert not stale_dir.exists(), "Stale non-paused session should be cleaned"

    def test_no_journal_allows_cleanup(self, tmp_path):
        """Session dir without journal file — cleanup proceeds."""
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        no_journal_id = "44444444-5555-6666-7777-888888888888"

        self._create_session_dir(slug_dir, current_id)
        no_journal_dir = self._create_session_dir(slug_dir, no_journal_id)
        self._set_age(no_journal_dir, 35)

        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        # No journal -> _is_paused_session returns False -> cleaned
        assert not no_journal_dir.exists()


# =============================================================================
# TTL Constant Verification — 30 Days Default
# =============================================================================

class TestTTLDefault:
    """Verify the 30-day default TTL constant (Scenario 8)."""

    def test_session_max_age_days_is_30(self):
        """The default TTL constant should be 30 days (changed from 7)."""
        from session_end import _SESSION_MAX_AGE_DAYS

        assert _SESSION_MAX_AGE_DAYS == 30

    def test_29_day_session_kept_at_default(self, tmp_path):
        """A 29-day-old session should be kept with default TTL."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        recent_id = "11111111-2222-3333-4444-555555555555"

        # Create current session
        current_dir = slug_dir / current_id
        current_dir.mkdir(parents=True, exist_ok=True)
        (current_dir / "context.json").write_text("{}")

        # Create 29-day-old session
        recent_dir = slug_dir / recent_id
        recent_dir.mkdir(parents=True, exist_ok=True)
        (recent_dir / "context.json").write_text("{}")
        old_time = _time.time() - (29 * 86400)
        _os.utime(str(recent_dir), (old_time, old_time))

        # Use default max_age_days (should be 30)
        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert recent_dir.exists(), "29-day-old session should survive with 30-day TTL"

    def test_31_day_session_cleaned_at_default(self, tmp_path):
        """A 31-day-old session should be cleaned with default TTL."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "my-project"
        current_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old_id = "11111111-2222-3333-4444-555555555555"

        # Create current session
        current_dir = slug_dir / current_id
        current_dir.mkdir(parents=True, exist_ok=True)
        (current_dir / "context.json").write_text("{}")

        # Create 31-day-old session
        old_dir = slug_dir / old_id
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "context.json").write_text("{}")
        old_time = _time.time() - (31 * 86400)
        _os.utime(str(old_dir), (old_time, old_time))

        # Use default max_age_days (should be 30)
        cleanup_old_sessions(
            project_slug="my-project",
            current_session_id=current_id,
            sessions_dir=str(tmp_path),
        )

        assert not old_dir.exists(), "31-day-old session should be cleaned with 30-day TTL"


class TestCleanupOldCheckpoints:
    """Tests for session_end._cleanup_old_checkpoints() — 7-day TTL sweep
    for legacy ~/.claude/pact-refresh/*.json files.

    Migrated from test_precompact_refresh.py:TestCleanupOldCheckpoints
    when the function was relocated in #413. Two new tests (default-path
    resolution, max_age_days kwarg) cover the expanded signature.
    """

    def test_nonexistent_dir_returns_zero(self, tmp_path: Path):
        """Non-existent checkpoint directory returns 0 without error."""
        from session_end import _cleanup_old_checkpoints

        result = _cleanup_old_checkpoints(tmp_path / "does-not-exist")

        assert result == 0

    def test_empty_dir_returns_zero(self, tmp_path: Path):
        """Empty checkpoint directory returns 0."""
        from session_end import _cleanup_old_checkpoints

        result = _cleanup_old_checkpoints(tmp_path)

        assert result == 0

    def test_old_json_file_deleted(self, tmp_path: Path):
        """Checkpoint files older than _CHECKPOINT_MAX_AGE_DAYS are deleted."""
        import os as _os
        import time as _time

        from session_end import _CHECKPOINT_MAX_AGE_DAYS, _cleanup_old_checkpoints

        old_time = _time.time() - (_CHECKPOINT_MAX_AGE_DAYS + 1) * 86400
        for name in ("old-project-a.json", "old-project-b.json"):
            f = tmp_path / name
            f.write_text(json.dumps({"old": True}))
            _os.utime(f, (old_time, old_time))

        result = _cleanup_old_checkpoints(tmp_path)

        assert result == 2
        assert not (tmp_path / "old-project-a.json").exists()
        assert not (tmp_path / "old-project-b.json").exists()

    def test_recent_json_file_preserved(self, tmp_path: Path):
        """Checkpoint files newer than _CHECKPOINT_MAX_AGE_DAYS are kept."""
        import os as _os
        import time as _time

        from session_end import _CHECKPOINT_MAX_AGE_DAYS, _cleanup_old_checkpoints

        old_time = _time.time() - (_CHECKPOINT_MAX_AGE_DAYS + 1) * 86400
        old_file = tmp_path / "old.json"
        old_file.write_text(json.dumps({"old": True}))
        _os.utime(old_file, (old_time, old_time))

        recent_file = tmp_path / "recent.json"
        recent_file.write_text(json.dumps({"recent": True}))
        # recent_file keeps its current mtime (just created).

        result = _cleanup_old_checkpoints(tmp_path)

        assert result == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_non_json_files_ignored(self, tmp_path: Path):
        """Non-.json files are not touched by cleanup regardless of age."""
        import os as _os
        import time as _time

        from session_end import _CHECKPOINT_MAX_AGE_DAYS, _cleanup_old_checkpoints

        old_time = _time.time() - (_CHECKPOINT_MAX_AGE_DAYS + 1) * 86400

        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("some notes")
        _os.utime(txt_file, (old_time, old_time))

        log_file = tmp_path / "audit.log"
        log_file.write_text("log line")
        _os.utime(log_file, (old_time, old_time))

        json_file = tmp_path / "old-checkpoint.json"
        json_file.write_text(json.dumps({"data": True}))
        _os.utime(json_file, (old_time, old_time))

        result = _cleanup_old_checkpoints(tmp_path)

        assert result == 1
        assert txt_file.exists()
        assert log_file.exists()
        assert not json_file.exists()

    def test_oserror_on_unlink_suppressed(self, tmp_path: Path):
        """OSError during individual file deletion is swallowed per fail-open invariant."""
        import os as _os
        import time as _time

        from session_end import _CHECKPOINT_MAX_AGE_DAYS, _cleanup_old_checkpoints

        old_time = _time.time() - (_CHECKPOINT_MAX_AGE_DAYS + 1) * 86400
        f = tmp_path / "undeletable.json"
        f.write_text(json.dumps({"data": True}))
        _os.utime(f, (old_time, old_time))

        original_unlink = Path.unlink

        def mock_unlink(self, *args, **kwargs):
            if self.name == "undeletable.json":
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", mock_unlink):
            result = _cleanup_old_checkpoints(tmp_path)

        # Deletion failed, so cleaned count stays 0; call did not raise.
        assert result == 0
        assert f.exists()

    def test_default_dir_resolves_home_pact_refresh(self, tmp_path: Path):
        """With no dir arg, defaults to ~/.claude/pact-refresh (via Path.home())."""
        from session_end import _cleanup_old_checkpoints

        # Point Path.home() at tmp_path; the default directory won't exist,
        # so the function should return 0 without touching anything.
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _cleanup_old_checkpoints()

        assert result == 0
        # Confirm the expected default path is what would have been used.
        expected_default = tmp_path / ".claude" / "pact-refresh"
        assert not expected_default.exists()

    def test_max_age_override_honored(self, tmp_path: Path):
        """max_age_days kwarg overrides the default TTL."""
        import os as _os
        import time as _time

        from session_end import _cleanup_old_checkpoints

        # File that is 2 days old: preserved at default (7d) but deleted at 1d override.
        two_day_old = _time.time() - (2 * 86400)
        f = tmp_path / "two-day.json"
        f.write_text(json.dumps({"data": True}))
        _os.utime(f, (two_day_old, two_day_old))

        # Override TTL to 1 day — file should now be past cutoff.
        result = _cleanup_old_checkpoints(tmp_path, max_age_days=1)

        assert result == 1
        assert not f.exists()


# =============================================================================
# cleanup_old_teams() Tests — #412 Fix B
# =============================================================================


def _make_team_dir(parent, name, age_days=0):
    """Create a team directory under parent with controlled mtime.

    Writes config.json (typical team fixture) then sets mtime so the parent
    dir's own stat().st_mtime reflects the intended age — teams/ uses
    parent-dir mtime (asymmetric with tasks/ which uses max-child mtime).
    """
    import os as _os
    import time as _time
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}")
    if age_days > 0:
        old = _time.time() - (age_days * 86400)
        _os.utime(str(d), (old, old))
    return d


def _make_task_dir(parent, name, child_ages_days=None, parent_age_days=None):
    """Create a tasks/{name}/ directory with per-child *.json mtimes.

    child_ages_days: list of ages (days) for child .json files; one file
    per entry (1.json, 2.json, ...). Pass [] for an empty dir.
    parent_age_days: if set, force parent dir mtime AFTER children are
    written (writing a child refreshes the parent on Unix).
    """
    import os as _os
    import time as _time
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    if child_ages_days:
        for idx, age in enumerate(child_ages_days, start=1):
            f = d / f"{idx}.json"
            f.write_text("{}")
            if age > 0:
                old = _time.time() - (age * 86400)
                _os.utime(str(f), (old, old))
    if parent_age_days is not None:
        old = _time.time() - (parent_age_days * 86400)
        _os.utime(str(d), (old, old))
    return d


class TestCleanupOldTeams:
    """Tests for session_end.cleanup_old_teams() — #412 Fix B."""

    def test_reaps_old_team_dirs_excluding_current(self, tmp_path):
        """Old sibling team dirs reap; current team_name entry preserved
        even when older than TTL."""
        from session_end import cleanup_old_teams

        current = "pact-abcd1234"
        _make_team_dir(tmp_path, current, age_days=60)  # old but current
        _make_team_dir(tmp_path, "pact-deadbeef", age_days=40)  # REAPED
        _make_team_dir(tmp_path, "43a2f95a-1111-2222-3333-444444444444", age_days=40)  # REAPED

        reaped, skipped = cleanup_old_teams(
            current_team_name=current,
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 2
        assert skipped == 0
        assert (tmp_path / current).exists()
        assert not (tmp_path / "pact-deadbeef").exists()
        assert not (tmp_path / "43a2f95a-1111-2222-3333-444444444444").exists()

    def test_preserves_fresh_team_dirs(self, tmp_path):
        """Mtime under TTL → preserved."""
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        _make_team_dir(tmp_path, "pact-recent", age_days=5)

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0
        assert (tmp_path / "pact-recent").exists()

    def test_fail_closed_on_empty_current_team_name(self, tmp_path):
        """COUNTER-TEST BY REVERT target: empty current_team_name → no-op.

        Load-bearing: the skip predicate IS the only defense layer (teams/
        has no secondary UUID filter). An empty skip value must NOT reap
        anything, or the live team dir could be deleted.
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-live", age_days=60)
        _make_team_dir(tmp_path, "pact-other", age_days=60)

        reaped, skipped = cleanup_old_teams(
            current_team_name="",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0
        assert skipped == 0
        # Both survive — empty skip-name fails closed, not open.
        assert (tmp_path / "pact-live").exists()
        assert (tmp_path / "pact-other").exists()

    def test_handles_missing_base_dir(self, tmp_path):
        """Non-existent base dir → (0, 0) silently, no raise."""
        from session_end import cleanup_old_teams

        ghost = tmp_path / "does-not-exist"
        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(ghost),
        )

        assert (reaped, skipped) == (0, 0)

    def test_skips_non_directory_entries(self, tmp_path):
        """Stray file at base → no raise, not counted as reaped."""
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        (tmp_path / "stray-file.txt").write_text("hi")

        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0
        assert skipped == 0
        assert (tmp_path / "stray-file.txt").exists()

    def test_skips_permission_denied_entries(self, tmp_path):
        """Per-entry stat OSError → entry counted in skipped, others still processed.

        Mock stat with call-count gating so the initial is_dir() probe
        (which also stats under the hood and would swallow OSError to
        False, making the entry invisible) succeeds, and only the explicit
        stat().st_mtime call inside the inner try raises.
        """
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        bad = _make_team_dir(tmp_path, "pact-bad", age_days=40)
        good = _make_team_dir(tmp_path, "pact-good", age_days=40)

        real_stat = _Path.stat
        stat_calls_by_path: dict[str, int] = {}

        def flaky_stat(self, *args, **kwargs):
            p = str(self)
            stat_calls_by_path[p] = stat_calls_by_path.get(p, 0) + 1
            # The bad entry: let is_dir() pass (first call) but fail the
            # explicit age-check stat (second call on the same Path).
            if p == str(bad) and stat_calls_by_path[p] >= 2:
                raise OSError("permission denied")
            return real_stat(self, *args, **kwargs)

        with _patch.object(_Path, "stat", flaky_stat):
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-current",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert skipped == 1
        assert reaped == 1
        assert bad.exists()  # skipped due to stat error
        assert not good.exists()  # reaped normally

    def test_legacy_name_shapes_all_reaped(self, tmp_path):
        """UUID, adjective-verb-noun, 'default', 'pact-xxx' — all reap when old."""
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        legacy = [
            "43a2f95a-1111-2222-3333-444444444444",
            "breezy-zooming-scroll",
            "default",
            "pact-legacy",
        ]
        for name in legacy:
            _make_team_dir(tmp_path, name, age_days=40)

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 4
        for name in legacy:
            assert not (tmp_path / name).exists()


# =============================================================================
# cleanup_old_tasks() Tests — #412 Fix B
# =============================================================================


class TestCleanupOldTasks:
    """Tests for session_end.cleanup_old_tasks() — #412 Fix B."""

    def test_reaps_via_max_child_mtime(self, tmp_path):
        """Dir with all-old children reaps; dir with one fresh child preserved.

        Pins the asymmetric probe — platform TaskUpdate rewrites child .json
        without touching parent dir's mtime, so max-child is the tight bound.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # Old dir: force parent mtime fresh but children all old — max-child
        # must win over parent mtime here.
        old = _make_task_dir(tmp_path, "pact-old", child_ages_days=[40, 45])
        # Refresh parent mtime to "now" so test proves children drive decision.
        _os.utime(str(old), (_time.time(), _time.time()))

        # Mixed dir: one old child + one fresh — max-child is fresh → preserved.
        mixed = _make_task_dir(tmp_path, "pact-mixed", child_ages_days=[40, 0])
        # Force old parent mtime to prove children override parent freshness.
        old_parent = _time.time() - (50 * 86400)
        _os.utime(str(mixed), (old_parent, old_parent))

        reaped, skipped = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert skipped == 0
        assert not (tmp_path / "pact-old").exists()
        assert (tmp_path / "pact-mixed").exists()

    def test_fallback_to_parent_mtime_on_empty_dir(self, tmp_path):
        """Empty dir with old parent mtime reaps via fallback."""
        from session_end import cleanup_old_tasks

        _make_task_dir(tmp_path, "pact-empty", child_ages_days=[], parent_age_days=40)

        reaped, _ = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert not (tmp_path / "pact-empty").exists()

    def test_skip_union(self, tmp_path):
        """Three skip-names (team_name, task_list_id, session_id) all preserved."""
        from session_end import cleanup_old_tasks

        team = "pact-abcd1234"
        task_list_id = "task-list-xyz"
        session_id = "98765432-aaaa-bbbb-cccc-dddddddddddd"

        _make_task_dir(tmp_path, team, child_ages_days=[40], parent_age_days=40)
        _make_task_dir(tmp_path, task_list_id, child_ages_days=[40], parent_age_days=40)
        _make_task_dir(tmp_path, session_id, child_ages_days=[40], parent_age_days=40)
        _make_task_dir(tmp_path, "pact-stale", child_ages_days=[40], parent_age_days=40)

        reaped, _ = cleanup_old_tasks(
            skip_names={team, task_list_id, session_id},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert (tmp_path / team).exists()
        assert (tmp_path / task_list_id).exists()
        assert (tmp_path / session_id).exists()
        assert not (tmp_path / "pact-stale").exists()

    def test_fail_closed_on_empty_skip_set(self, tmp_path):
        """COUNTER-TEST BY REVERT target: empty / all-blank skip_names → no-op.

        Same defense rationale as teams: skip-predicate is the only layer.
        Empty set AND set of only blanks must both fail closed.
        """
        from session_end import cleanup_old_tasks

        _make_task_dir(tmp_path, "pact-live", child_ages_days=[40], parent_age_days=40)
        _make_task_dir(tmp_path, "pact-other", child_ages_days=[40], parent_age_days=40)

        # Empty set
        reaped1, _ = cleanup_old_tasks(
            skip_names=set(),
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )
        # All-blank set
        reaped2, _ = cleanup_old_tasks(
            skip_names={"", "", ""},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped1 == 0
        assert reaped2 == 0
        assert (tmp_path / "pact-live").exists()
        assert (tmp_path / "pact-other").exists()

    def test_handles_unstatable_children(self, tmp_path):
        """Child.stat OSError → max-child stays 0.0 → parent-mtime fallback exercised."""
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_tasks

        d = _make_task_dir(tmp_path, "pact-quirky", child_ages_days=[5], parent_age_days=40)
        child_path = d / "1.json"

        real_stat = _Path.stat

        def flaky_stat(self, *args, **kwargs):
            if str(self) == str(child_path):
                raise OSError("transient")
            return real_stat(self, *args, **kwargs)

        with _patch.object(_Path, "stat", flaky_stat):
            reaped, _ = cleanup_old_tasks(
                skip_names={"pact-current"},
                tasks_base_dir=str(tmp_path),
                max_age_days=30,
            )

        # Child stat raised → latest stays 0.0 → fallback to parent mtime
        # (40 days old) → reaped.
        assert reaped == 1
        assert not d.exists()


# =============================================================================
# cleanup_summary journal event — #412 Fix B
# =============================================================================


class TestCleanupSummaryEvent:
    """main()-level integration: cleanup_summary journal event shape & emission."""

    def _run_main_with_reapers(self, *, team_return, env_task_list_id=""):
        """Helper: run main() with real reapers patched to return (r, s) tuples.

        Returns list of append_event call args for inspection.
        """
        from unittest.mock import patch, MagicMock
        from contextlib import ExitStack
        import io as _io

        captured = []

        def record(event):
            captured.append(event)

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": env_task_list_id}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-id"),
            patch("session_end.get_team_name", return_value=team_return),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(3, 1)),
            patch("session_end.cleanup_old_tasks", return_value=(2, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event", side_effect=record),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            from session_end import main
            with pytest.raises(SystemExit):
                main()
        return captured

    def test_cleanup_summary_event_shape_when_reaper_runs(self):
        """main() emits cleanup_summary with all 5 fields populated from reaper returns."""
        events = self._run_main_with_reapers(team_return="pact-current")

        summaries = [e for e in events if e["type"] == "cleanup_summary"]
        assert len(summaries) == 1
        s = summaries[0]
        assert s["teams_reaped"] == 3
        assert s["teams_skipped"] == 1
        assert s["tasks_reaped"] == 2
        assert s["tasks_skipped"] == 0
        assert s["ttl_days"] == 30

    def test_cleanup_summary_emitted_even_when_counts_zero(self):
        """Audit-trail invariant: event still written when all counts are 0."""
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        captured = []

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-id"),
            patch("session_end.get_team_name", return_value="pact-current"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end.cleanup_old_tasks", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event", side_effect=lambda e: captured.append(e)),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        summaries = [e for e in captured if e["type"] == "cleanup_summary"]
        assert len(summaries) == 1
        s = summaries[0]
        assert s["teams_reaped"] == 0
        assert s["teams_skipped"] == 0
        assert s["tasks_reaped"] == 0
        assert s["tasks_skipped"] == 0
        assert s["ttl_days"] == 30


# =============================================================================
# main() wiring for reapers — #412 Fix B
# =============================================================================


class TestMainReaperWiring:
    """main()-level wiring guards for the new reapers."""

    def _base_patches(self, *, team_return="pact-current", session_id="sess-id", env=None):
        from unittest.mock import patch
        import io as _io
        env = env or {}
        return [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", env, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value=session_id),
            patch("session_end.get_team_name", return_value=team_return),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]

    def test_main_skips_team_reaper_on_empty_team_name(self):
        """Callsite short-circuit: empty team_name → cleanup_old_teams NOT invoked.

        Belt-and-suspenders layer around the internal guard; this test
        pins the short-circuit specifically (the internal guard already
        returns (0,0) but we must not even call it when we know better).
        """
        from unittest.mock import patch
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in self._base_patches(team_return=""):
                stack.enter_context(p)
            mock_teams = stack.enter_context(
                patch("session_end.cleanup_old_teams", return_value=(0, 0))
            )
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_teams.assert_not_called()
        # tasks reaper still runs because session_id alone is a valid skip member
        mock_tasks.assert_called_once()

    def test_main_assembles_union_skip_set(self):
        """Env CLAUDE_CODE_TASK_LIST_ID, team_name, session_id all in skip_names."""
        from unittest.mock import patch
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in self._base_patches(
                team_return="team-A",
                session_id="sess-B",
                env={"CLAUDE_CODE_TASK_LIST_ID": "task-C"},
            ):
                stack.enter_context(p)
            stack.enter_context(
                patch("session_end.cleanup_old_teams", return_value=(0, 0))
            )
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_tasks.assert_called_once()
        call = mock_tasks.call_args
        assert call.kwargs["skip_names"] == {"team-A", "task-C", "sess-B"}

    def test_main_cleanup_summary_outer_tryexcept_absorbs_append_failure(self):
        """Journal write for cleanup_summary failing must not propagate.

        Regression guard for the outer try/except around append_event in
        main() — reaper success is independent of journal write success.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        call_count = {"n": 0}

        def flaky_append(event):
            call_count["n"] += 1
            if event["type"] == "cleanup_summary":
                raise RuntimeError("journal full")
            # session_end event ok

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-id"),
            patch("session_end.get_team_name", return_value="pact-current"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end.cleanup_old_tasks", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event", side_effect=flaky_append),
        ]
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            mock_chk = mocks[-2]  # _cleanup_old_checkpoints
            from session_end import main
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 0  # fire-and-forget
        # Checkpoint cleanup still ran despite cleanup_summary journal failure.
        mock_chk.assert_called_once()


# =============================================================================
# Reaper outer-try regression — #412 Fix B
# =============================================================================


class TestReaperOuterTryExcept:
    """Outer try/except in each reaper must absorb catastrophic OSError."""

    def test_cleanup_old_teams_iterdir_oserror_absorbed(self, tmp_path):
        """iterdir() raising OSError at outer level → return current counts, no raise."""
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)

        real_iterdir = _Path.iterdir

        def flaky_iterdir(self):
            if str(self) == str(tmp_path):
                raise OSError("EACCES on base")
            return real_iterdir(self)

        with _patch.object(_Path, "iterdir", flaky_iterdir):
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-current",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert (reaped, skipped) == (0, 0)

    def test_cleanup_old_tasks_iterdir_oserror_absorbed(self, tmp_path):
        """cleanup_old_tasks: outer iterdir raise absorbed, returns current counts."""
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_tasks

        real_iterdir = _Path.iterdir

        def flaky_iterdir(self):
            if str(self) == str(tmp_path):
                raise OSError("EACCES on base")
            return real_iterdir(self)

        # base dir must exist for the guard to pass before iterdir is called
        (tmp_path / "placeholder").mkdir()

        with _patch.object(_Path, "iterdir", flaky_iterdir):
            reaped, skipped = cleanup_old_tasks(
                skip_names={"pact-current"},
                tasks_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert (reaped, skipped) == (0, 0)


# =============================================================================
# Regression guard — cleanup_old_sessions unchanged — #412 Fix B
# =============================================================================


class TestCleanupOldSessionsUnchangedRegression:
    """Delta guard: adding the new reapers must not alter cleanup_old_sessions.

    Catches accidental edits to the parent reaper while the sibling
    reapers are being added.
    """

    def test_parent_reaper_still_reaps_uuid_sibling(self, tmp_path):
        """Smoke regression: basic UUID reap behavior intact."""
        import os as _os
        import time as _time
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "proj"
        current = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        old = "11111111-2222-3333-4444-555555555555"
        for sid in (current, old):
            d = slug_dir / sid
            d.mkdir(parents=True)
            (d / "ctx.json").write_text("{}")
        old_time = _time.time() - (40 * 86400)
        _os.utime(str(slug_dir / old), (old_time, old_time))

        cleanup_old_sessions(
            project_slug="proj",
            current_session_id=current,
            sessions_dir=str(tmp_path),
            max_age_days=30,
        )

        assert (slug_dir / current).exists()
        assert not (slug_dir / old).exists()
