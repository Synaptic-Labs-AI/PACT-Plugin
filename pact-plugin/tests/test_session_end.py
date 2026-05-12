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

        with patch("session_end.get_project_dir", return_value="/Users/example/Sites/my-project"):
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
                                     return_value="/Users/example/Sites/my-project"),
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

    #453 Fix A defensive-default: each test in this class runs with
    `session_end.check_pr_state` patched to return "OPEN" so the
    last-line-of-defense live gh call does NOT shell out to the real
    `gh pr view` (which would flake against arbitrary PR numbers used
    in fixtures). Individual tests that exercise the MERGED / CLOSED /
    fail-open branches patch check_pr_state explicitly with their own
    return_value (inner patch wins over the fixture).
    """

    @pytest.fixture(autouse=True)
    def _default_check_pr_state(self):
        with patch("session_end.check_pr_state", return_value="OPEN"):
            yield

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

    # ========================================================================
    # #453 Fix B — session_consolidated short-circuit tests
    # ========================================================================

    def test_session_consolidated_short_circuits_warning(self):
        """session_consolidated present → no warning regardless of PR state.

        Baseline #453 Fix B check: the wrap-up happy path emits
        session_consolidated and leaves the review_dispatch event in
        place. Without the short-circuit, the legacy timestamp
        comparison would warn. With the short-circuit, it returns None.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_consolidated":
                return [{"type": "session_consolidated", "ts": "2026-01-02T00:00:00Z"}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_session_consolidated_short_circuits_even_with_unpaused_pr(self):
        """Fix B covers the #453 root-cause-#1 scenario: merged-PR wrap-up.

        review_dispatch present, NO session_paused, session_consolidated
        present → no warning. Without the short-circuit, the fallback
        PR-detection path would surface a warning for PR #42.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_consolidated":
                return [{"type": "session_consolidated", "ts": "2026-01-02T00:00:00Z"}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_session_consolidated_missing_falls_through_to_legacy_logic(self):
        """No session_consolidated + legacy pause-covers-review → no warning.

        AC#3 guard: the legacy timestamp-comparison path is preserved
        for sessions that never consolidated but did pause.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_consolidated":
                return []
            if event_type == "session_paused":
                return [{"type": "session_paused", "pr_number": 42, "ts": "2026-01-02T00:00:00Z"}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_session_consolidated_missing_warns_on_true_positive(self):
        """AC#3 true-positive pin: no consolidation, unpaused PR → warn.

        Regression guard — the Fix B short-circuit must NOT swallow
        genuine unpaused-PR warnings. Without session_consolidated in
        the journal, an unpaused open PR must still surface the
        warning for the user to act on.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_consolidated":
                return []
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

    def test_session_consolidated_empty_list_falls_through(self):
        """read_events returning [] for session_consolidated falls through.

        Pins the falsy-check contract: `if read_events(...)` treats an
        empty list as "not consolidated" and allows the legacy logic
        to run. A regression that flipped this to `if read_events(...) is not None`
        would silently break AC#3 true-positive detection.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_consolidated":
                return []  # Explicit empty list
            if event_type == "session_paused":
                return []
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 99, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #99" in warning

    # ========================================================================
    # #453 Fix A — live PR-state defense-in-depth tests
    # ========================================================================

    def test_live_pr_check_merged_short_circuits(self):
        """gh reports MERGED → no warning (AC#2).

        Catches the case where a PR was merged on the GitHub web UI
        mid-session and no wrap-up ran. Fix B is empty (no
        session_consolidated event); Fix A covers the gap via live
        gh check.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", return_value="MERGED"):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_live_pr_check_closed_short_circuits(self):
        """gh reports CLOSED → no warning (AC#2 sibling)."""
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", return_value="CLOSED"):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None

    def test_live_pr_check_open_still_warns(self):
        """gh reports OPEN → warning fires (genuine unpaused-PR case).

        Happy-path for the warning: the PR is actually open on GitHub
        and the session ended without consolidation.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", return_value="OPEN"):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

    def test_live_pr_check_unknown_state_warns(self):
        """gh returns empty string ("" sentinel) → conservative warn.

        Empty string is the fail-open sentinel (gh missing / timeout /
        auth expired / OSError). Not in ("MERGED", "CLOSED"), so the
        function falls through to warn — we cannot distinguish
        "offline" from "PR actually open" without gh.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", return_value=""):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

    def test_live_pr_check_not_called_when_consolidated_short_circuits(self):
        """AC#4 pin: wrap-up path makes zero gh calls.

        When session_consolidated is present, check_pr_state MUST NOT
        be invoked — the short-circuit at the top of check_unpaused_pr
        returns before we even resolve the PR number. Pins the zero-
        network-calls guarantee for the wrap-up happy path.
        """
        from session_end import check_unpaused_pr

        def mock_read_events(event_type=None):
            if event_type == "session_consolidated":
                return [{"type": "session_consolidated", "ts": "2026-01-02T00:00:00Z"}]
            if event_type == "review_dispatch":
                return [{"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state") as mock_check:
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is None
        mock_check.assert_not_called()

    # ========================================================================
    # #453 T19-T21 — fail-open at the real subprocess boundary
    #
    # Unlike the tests above (which patch session_end.check_pr_state directly
    # with a "" return value to simulate the fail-open sentinel), these tests
    # patch shared.gh_helpers.subprocess.run to raise each canonical error
    # type and let the REAL gh_helpers.check_pr_state code convert the
    # exception into the empty-string sentinel. This pins the full
    # plumbing path: subprocess raises → gh_helpers catches → returns
    # "" → session_end falls through to warn.
    # ========================================================================

    def test_live_pr_check_gh_missing_warns(self):
        """T19: gh not installed (FileNotFoundError) → empty sentinel → warn.

        Override the autouse check_pr_state fixture with the REAL
        implementation so FileNotFoundError raised from subprocess.run
        actually flows through gh_helpers.check_pr_state's except clause.
        """
        from session_end import check_unpaused_pr
        from shared.gh_helpers import check_pr_state as real_check_pr_state

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [
                    {"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}
                ]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", real_check_pr_state), \
             patch(
                 "shared.gh_helpers.subprocess.run",
                 side_effect=FileNotFoundError("gh not found"),
             ):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

    def test_live_pr_check_gh_timeout_warns(self):
        """T20: gh times out → empty sentinel → warn.

        Plumbing pin: subprocess.TimeoutExpired raised inside the 5-second
        cap must be caught by gh_helpers' except tuple and surfaced as ""
        so the detector falls through to the conservative warning.
        """
        import subprocess
        from session_end import check_unpaused_pr
        from shared.gh_helpers import check_pr_state as real_check_pr_state

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [
                    {"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}
                ]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", real_check_pr_state), \
             patch(
                 "shared.gh_helpers.subprocess.run",
                 side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
             ):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning

    def test_live_pr_check_gh_oserror_warns(self):
        """T21: gh raises OSError (permission denied, ENOMEM, etc.) → warn.

        Plumbing pin: the OSError branch of gh_helpers' except tuple
        converts unexpected OS errors to the "" sentinel without
        propagating. Regression guard against a change that shrinks
        the caught exception list (e.g. dropping OSError would let
        permission-denied exceptions crash the SessionEnd hook).
        """
        from session_end import check_unpaused_pr
        from shared.gh_helpers import check_pr_state as real_check_pr_state

        def mock_read_events(event_type=None):
            if event_type == "review_dispatch":
                return [
                    {"type": "review_dispatch", "pr_number": 42, "ts": "2026-01-01T00:00:00Z"}
                ]
            return []

        with patch("session_end.read_events", side_effect=mock_read_events), \
             patch("session_end.check_pr_state", real_check_pr_state), \
             patch(
                 "shared.gh_helpers.subprocess.run",
                 side_effect=OSError("permission denied"),
             ):
            warning = check_unpaused_pr(
                tasks=None,
                project_slug="proj",
            )

        assert warning is not None
        assert "PR #42" in warning


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

    # Cycle-8 Test 1 — symlink guard on the 4th reaper
    def test_symlink_with_old_target_is_skipped(self, tmp_path: Path):
        """Symlink whose TARGET is old-mtime is SKIPPED, not unlinked.

        Cycle-8 parity with cycle-1 sibling reapers: `_cleanup_old_checkpoints`
        now has `if checkpoint_file.is_symlink(): continue` BEFORE the
        `lstat/unlink` path. Without the guard, a planted symlink whose
        TARGET has an old mtime would be unlinked by the reaper — either
        deleting user-planted links (pure destruction since unlink on a
        symlink never touches the target) OR, if the guard were absent
        AND the helper naively used `stat()`, deleting based on target-
        mtime (oracle leak).

        COUNTER-TEST BY REVERT target: removing the `is_symlink` guard
        flips this test — under the current `lstat()` probe the link's
        own (fresh) mtime saves it; but if lstat ALSO gets reverted to
        stat() (cycle-2 belt-and-suspenders), the target's old mtime
        would drive the unlink. Either regression the guard defends
        against shows up as test failure via the link-is-unlinked
        assertion.
        """
        import os as _os
        import time as _time
        from session_end import _cleanup_old_checkpoints

        # External target with OLD mtime (40d > 7d default TTL).
        target = tmp_path.parent / f"{tmp_path.name}-ext-target.json"
        target.write_text(json.dumps({"target": True}))
        old = _time.time() - (40 * 86400)
        _os.utime(str(target), (old, old))

        # Symlink planted inside checkpoint_dir, with FRESH link-mtime
        # so even under lstat the link is within TTL. Guard is what
        # prevents the unlink irrespective of the TTL math.
        link = tmp_path / "evil.json"
        link.symlink_to(target)

        try:
            result = _cleanup_old_checkpoints(tmp_path, max_age_days=7)

            assert result == 0, (
                "Symlink must NOT be counted as cleaned. Guard removal "
                "flips this to 1."
            )
            assert link.is_symlink(), (
                "Symlink itself must survive. If this fails, either the "
                "`is_symlink` guard was removed OR lstat was reverted to "
                "stat() (target mtime would then drive unlink)."
            )
            assert target.exists(), "Target must survive"
        finally:
            if link.is_symlink():
                link.unlink()
            if target.exists():
                target.unlink()


# =============================================================================
# cleanup_old_teams() Tests — #412 Fix B
# =============================================================================


def _make_team_dir(parent, name, age_days=0):
    """Create a team directory under parent with controlled mtime.

    Writes config.json (typical team fixture) and sets mtime on BOTH the
    config.json child AND the parent dir to `age_days` old. The teams
    reaper now uses max-child-mtime via `_dir_max_child_mtime(glob="*")`
    (cycle-4 fix for POSIX in-place-overwrite semantics: parent-dir
    mtime is NOT bumped when config.json is rewritten in place, only on
    create/unlink/rename). Honestly aging the child makes the fixture
    model the new reaper's actual probe. Parent-dir mtime is kept aged
    too as belt-and-suspenders — if a future reaper reverts to parent
    probe, tests still represent the intended age.
    """
    import os as _os
    import time as _time
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "config.json"
    cfg.write_text("{}")
    if age_days > 0:
        old = _time.time() - (age_days * 86400)
        _os.utime(str(cfg), (old, old))
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
        """Old PACT-shaped sibling team dirs reap; current team_name entry
        preserved even when older than TTL; non-PACT-shaped names
        preserved by `_TEAM_NAME_PATTERN` gate (cycle-4 defense layer)."""
        from session_end import cleanup_old_teams

        current = "pact-abcd1234"
        _make_team_dir(tmp_path, current, age_days=60)  # old but current
        _make_team_dir(tmp_path, "pact-deadbeef", age_days=40)  # REAPED (PACT-shaped)
        # UUID-shaped name: cycle-4 `_TEAM_NAME_PATTERN = ^pact-[a-f0-9-]+$`
        # rejects this even when old. Pre-cycle-4 this was reaped; the new
        # defense layer treats ~/.claude/teams/ as shared space.
        _make_team_dir(tmp_path, "43a2f95a-1111-2222-3333-444444444444", age_days=40)

        reaped, skipped = cleanup_old_teams(
            current_team_name=current,
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1, "only the PACT-shaped old dir should reap"
        assert skipped == 0
        assert (tmp_path / current).exists()
        assert not (tmp_path / "pact-deadbeef").exists()
        # Non-PACT-shaped UUID dir is preserved by the pattern gate even
        # though its mtime is older than TTL. Preservation is the *point*
        # of the gate: teams/ is shared space, not PACT-owned space.
        assert (tmp_path / "43a2f95a-1111-2222-3333-444444444444").exists()

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

        Load-bearing: the skip predicate IS the only defense layer once the
        pattern gate admits an entry. An empty skip value must NOT reap
        anything, or the live team dir could be deleted.

        Fixture names MUST pass `_TEAM_NAME_PATTERN = ^pact-[a-f0-9-]+$`
        so the pattern gate admits them — otherwise the gate masks the
        fail-closed guard's sensitivity (PR #433 cycle-7 F1 remediation).
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-abcd1234", age_days=60)
        _make_team_dir(tmp_path, "pact-deadbeef", age_days=60)

        reaped, skipped = cleanup_old_teams(
            current_team_name="",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0
        assert skipped == 0
        # Both survive — empty skip-name fails closed, not open.
        assert (tmp_path / "pact-abcd1234").exists()
        assert (tmp_path / "pact-deadbeef").exists()

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
        """Per-entry TTL-probe OSError → entry counted in `skipped`, others
        still processed.

        Cycle-4 rework: the age probe is no longer `entry.stat().st_mtime`
        — it's `_dir_max_child_mtime(entry, glob="*")` which walks
        children. Directly mock `_dir_max_child_mtime` to raise OSError
        for the `bad` entry only. This tests the same invariant (inner
        try/except around TTL probe → skipped++) more directly than the
        previous stat-call-count mock.
        """
        from unittest.mock import patch as _patch
        from session_end import cleanup_old_teams
        import session_end as _se

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        # Hex-only names (cycle-4 pattern gate `^pact-[a-f0-9-]+$`).
        # "pact-bad"/"pact-good" would fail the gate (g not in [a-f]).
        bad = _make_team_dir(tmp_path, "pact-badd1111", age_days=40)
        good = _make_team_dir(tmp_path, "pact-cafe2222", age_days=40)

        real_probe = _se._dir_max_child_mtime

        def flaky_probe(entry, glob="*.json"):
            if str(entry) == str(bad):
                raise OSError("permission denied")
            return real_probe(entry, glob=glob)

        with _patch.object(_se, "_dir_max_child_mtime", flaky_probe):
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-current",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert skipped == 1
        assert reaped == 1
        assert bad.exists()  # skipped due to TTL-probe error
        assert not good.exists()  # reaped normally

    def test_legacy_name_shapes_preserved_by_pattern_gate(self, tmp_path):
        """Non-PACT-shaped names (UUID, adjective-verb-noun, 'default',
        non-hex 'pact-legacy') are preserved by `_TEAM_NAME_PATTERN`
        (cycle-4). `~/.claude/teams/` is shared space; the reaper only
        touches names matching `^pact-[a-f0-9-]+$` (the INVARIANT shape
        produced by session_init.generate_team_name).

        Pre-cycle-4 spec: all 4 of these reaped (no pattern gate).
        Post-cycle-4 spec: none reap — the gate is the blast-radius
        contract declaring "this reaper only touches PACT-shaped dirs,
        anything else belongs to someone else."
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        # All four names fail `^pact-[a-f0-9-]+$`: UUID has separators but
        # also doesn't start with "pact-"; "breezy-zooming-scroll" lacks
        # the prefix; "default" lacks the prefix; "pact-legacy" starts
        # correctly but contains l/g/y which are non-hex.
        non_pact = [
            "43a2f95a-1111-2222-3333-444444444444",
            "breezy-zooming-scroll",
            "default",
            "pact-legacy",
        ]
        for name in non_pact:
            _make_team_dir(tmp_path, name, age_days=40)

        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, "pattern gate must reject all non-PACT names"
        assert skipped == 0, (
            "pattern gate `continue`s before the inner try/except, so "
            "skipped must stay 0 (skipped only increments on TTL-probe "
            "OSError)"
        )
        for name in non_pact:
            assert (tmp_path / name).exists(), (
                f"{name!r} must be preserved by pattern gate even though "
                f"mtime > TTL"
            )


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
        """All-child-stat-OSError → sentinel → caller marks skipped, NOT reaped.

        Cycle-5 spec change (PR #433 cycle-5, sentinel hardening): when
        `_dir_max_child_mtime` saw at least one child but every child's
        `lstat()` raised OSError, the helper returns `None` rather than
        falling back to parent mtime. Falling back to parent would
        false-reap dirs whose age can't actually be observed (permission-
        regression scenario). The caller treats `None` as "skip this
        entry, count as skipped."

        Pre-cycle-5 spec asserted `reaped == 1` here (parent-mtime
        fallback). The new spec asserts `skipped == 1` and `reaped == 0`.
        """
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_tasks

        d = _make_task_dir(tmp_path, "pact-quirky", child_ages_days=[5], parent_age_days=40)
        child_path = d / "1.json"

        real_lstat = _Path.lstat

        def flaky_lstat(self, *args, **kwargs):
            if str(self) == str(child_path):
                raise OSError("transient")
            return real_lstat(self, *args, **kwargs)

        with _patch.object(_Path, "lstat", flaky_lstat):
            reaped, skipped = cleanup_old_tasks(
                skip_names={"pact-current"},
                tasks_base_dir=str(tmp_path),
                max_age_days=30,
            )

        # Child lstat raised → saw_any_child=True + latest=0.0 → sentinel
        # `None` → caller skipped += 1. Dir survives (no false-reap).
        assert reaped == 0
        assert skipped == 1
        assert d.exists()


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
        # Cycle-8 split: single `ttl_days` replaced by per-reaper fields.
        assert s["teams_ttl_days"] == 30
        assert s["tasks_ttl_days"] == 30

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
        # Cycle-8 split: single `ttl_days` replaced by per-reaper fields.
        assert s["teams_ttl_days"] == 30
        assert s["tasks_ttl_days"] == 30


# =============================================================================
# _assemble_tasks_skip_set — cycle-8 extracted helper (direct unit test)
# =============================================================================


class TestAssembleTasksSkipSet:
    """Cycle-8 Test 2 — direct unit test of the module-level helper.

    Extracted from `main()` in cycle-8 (Architect M3). Takes only
    primitives, returns a deterministic `set[str]` — no session-context
    mocking needed. Coverage: happy path (all 3 channels pass), one
    channel rejected by allowlist, all rejected, empty-string inputs
    pruned.

    No counter-test-by-revert needed: the helper IS the system under
    test; behavioral contract is the assertion target.
    """

    def test_happy_path_all_three_channels_pass(self):
        """All 3 non-empty allowlist-safe inputs populate skip_names."""
        from session_end import _assemble_tasks_skip_set

        result = _assemble_tasks_skip_set(
            team_name="pact-0001639f",
            task_list_id="task-list-abc",
            session_id="5ddd5636-d408-4892",
        )

        assert result == {"pact-0001639f", "task-list-abc", "5ddd5636-d408-4892"}

    def test_one_channel_rejected_by_allowlist(self):
        """Hostile task_list_id is dropped; other channels still populate."""
        from session_end import _assemble_tasks_skip_set

        result = _assemble_tasks_skip_set(
            team_name="pact-abcd1234",
            task_list_id="../etc/passwd",  # fails is_safe_path_component
            session_id="sess-1",
        )

        assert "../etc/passwd" not in result
        assert result == {"pact-abcd1234", "sess-1"}

    def test_all_channels_rejected_yields_empty(self):
        """All three hostile → empty set (fail-closed at caller)."""
        from session_end import _assemble_tasks_skip_set

        result = _assemble_tasks_skip_set(
            team_name="bad name",     # space
            task_list_id="\u2028",    # LINE SEPARATOR
            session_id="name\nwith\nnewline",
        )

        assert result == set()

    def test_empty_string_inputs_are_pruned(self):
        """Empty-string channels don't leak into skip_names as `""`."""
        from session_end import _assemble_tasks_skip_set

        result = _assemble_tasks_skip_set(
            team_name="pact-deadbeef",
            task_list_id="",
            session_id="",
        )

        assert "" not in result
        assert result == {"pact-deadbeef"}

    def test_all_empty_yields_empty_set(self):
        """All-empty inputs → empty set."""
        from session_end import _assemble_tasks_skip_set

        result = _assemble_tasks_skip_set(
            team_name="",
            task_list_id="",
            session_id="",
        )

        assert result == set()


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


# =============================================================================
# Cycle-1 remediation pins (No-Narration/Raw-Read-Metadata/Emit-Nothing-If-Empty/G6/G7) — #412 Fix B
# =============================================================================


class TestReaperBehaviorPins:
    """Pin load-bearing behaviors that prior coverage left implicit.

    Each test protects an invariant against silent-refactor drift. Every
    test docstring names the specific refactor it would catch.
    """

    # No-Narration — TTL boundary semantics
    def test_ttl_boundary_29d_survives_30d_reaps(self, tmp_path):
        """29d-aged survives; 30d-aged reaps — pins effective `>= ~30d reaps`.

        Source uses `age_days > max_age_days` (strict). Because
        `time.time()` advances between utime() and the reaper's own
        wall-clock read, a dir utime'd to `now - 30*86400` has effective
        age 30.0000...ns > 30 and reaps. Two asymmetric assertions pin
        the practical boundary; flipping `>` to `>=` would pass the `30d
        reaps` half and fail the `29d survives` half — and vice versa for
        regressions that relax the comparison. Catches any future drift.
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        # 29d survives
        _make_team_dir(tmp_path, "pact-29d", age_days=29)
        # 30d reaps (practical: time.time() advances → age > 30)
        _make_team_dir(tmp_path, "pact-30d", age_days=30)

        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert skipped == 0
        assert (tmp_path / "pact-29d").exists(), "29d must survive (age < TTL)"
        assert not (tmp_path / "pact-30d").exists(), (
            "30d must reap (effective age is 30.0 + epsilon due to "
            "time.time() drift between utime and reaper read)"
        )
        assert reaped == 1

    # Raw-Read-Metadata — _task_dir_mtime glob scope
    def test_task_dir_mtime_ignores_non_json_sidecar(self, tmp_path):
        """Max-child probe scans `*.json` ONLY; fresh sidecars don't keep dir alive.

        Prevents future `glob("*")` refactor from silently changing
        retention semantics. Verified live during review:
        `_task_dir_mtime` glob is `*.json`, so a fresh user-dropped
        `.md` sidecar is invisible to the probe. Today this is correct
        (platform only writes .json); a test pins it.
        """
        from session_end import cleanup_old_tasks

        d = _make_task_dir(
            tmp_path, "pact-stale-with-sidecar",
            child_ages_days=[40, 45], parent_age_days=40,
        )
        # Fresh sidecar the probe must ignore.
        sidecar = d / "user-notes.md"
        sidecar.write_text("user-dropped content I intended to keep")

        reaped, skipped = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert not d.exists(), (
            "Sidecar does NOT keep dir alive — max-child probe ignores "
            "non-.json files. If a future refactor changes glob to `*`, "
            "this test flips to 0 reaped and catches the change."
        )

    # Emit-Nothing-If-Empty — main() reaper → cleanup_summary emission ordering
    def test_cleanup_summary_emitted_after_both_reapers(self):
        """`append_event(cleanup_summary)` fires AFTER both reaper calls.

        Pins the invariant that counts in the event reflect POST-reaper
        state. If a future refactor moves the append_event call above
        either reaper, counts would be stale and this test catches it.
        Uses a recording mock_calls timeline to assert call ordering.
        """
        from unittest.mock import patch, call
        from contextlib import ExitStack
        import io as _io

        # Shared recorder: every call to a patched target appends a tag.
        timeline: list[str] = []

        def rec_teams(*a, **kw):
            timeline.append("teams_reaper")
            return (0, 0)

        def rec_tasks(*a, **kw):
            timeline.append("tasks_reaper")
            return (0, 0)

        def rec_append(event):
            timeline.append(f"append:{event.get('type')}")

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
            patch("session_end.cleanup_old_teams", side_effect=rec_teams),
            patch("session_end.cleanup_old_tasks", side_effect=rec_tasks),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event", side_effect=rec_append),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        # cleanup_summary must appear AFTER both reaper tags.
        cs_idx = timeline.index("append:cleanup_summary")
        assert "teams_reaper" in timeline[:cs_idx], (
            f"teams_reaper did not run before cleanup_summary emit — timeline: {timeline}"
        )
        assert "tasks_reaper" in timeline[:cs_idx], (
            f"tasks_reaper did not run before cleanup_summary emit — timeline: {timeline}"
        )

    # G6 — skip-set exact-match semantics
    def test_skip_set_exact_match_not_substring(self, tmp_path):
        """skip_names uses set membership (==), NOT prefix/substring match.

        Pins against a future regression where someone refactors to
        `any(entry.name.startswith(s) for s in skip_names)` — that would
        over-preserve. Entry "pact-abc-old" must NOT be shielded by
        skip_names={"pact-abc"}.
        """
        from session_end import cleanup_old_tasks

        _make_task_dir(
            tmp_path, "pact-abc-old",
            child_ages_days=[40], parent_age_days=40,
        )

        reaped, _ = cleanup_old_tasks(
            skip_names={"pact-abc"},  # substring of "pact-abc-old"
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert not (tmp_path / "pact-abc-old").exists(), (
            "Substring skip name must NOT shield — exact-match semantics "
            "are load-bearing."
        )

    # G7 — hostile/pathological team names survive traversal
    def test_reaper_survives_pathological_team_name(self, tmp_path):
        """Unicode/control-char team name in iterdir output doesn't crash reaper.

        Team names containing surrogate-emoji, NEL (U+0085), or LS
        (U+2028) could theoretically land in ~/.claude/teams/. Reaper
        must survive: per-entry try/except absorbs anything that goes
        wrong when stat'ing or comparing such names, and outer try/except
        absorbs anything that surfaces at the iterdir level.
        """
        from session_end import cleanup_old_teams

        # Emoji + NEL + LS + PS (mirrors PR #426 sanitizer strip set).
        hostile = "pact-\U0001f600\u0085\u2028\u2029team"
        _make_team_dir(tmp_path, hostile, age_days=40)
        _make_team_dir(tmp_path, "pact-current", age_days=0)

        # Must not raise. Reaper either reaps or skips the hostile dir.
        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        # Current dir survives regardless.
        assert (tmp_path / "pact-current").exists()
        # Either outcome is acceptable for the hostile entry; the
        # invariant is "no exception propagates."
        total = reaped + skipped
        assert total in (0, 1), (
            f"Hostile-named entry produced unexpected counts: reaped={reaped} "
            f"skipped={skipped}"
        )


# =============================================================================
# Cycle-2 remediation: M3 CLAUDE_CODE_TASK_LIST_ID allowlist rejection pin
# =============================================================================


class TestTaskListIdAllowlistRejection:
    """Pins `re.fullmatch(r"[A-Za-z0-9_-]+", ...)` guard at session_end.py:615.

    The cycle-1 happy-path test (`test_main_assembles_union_skip_set`)
    covers a well-formed `task-C` passing through to skip_names. But it
    doesn't cover the REJECTION path — if someone deletes the allowlist
    line, hostile env values would silently enter skip_names and could
    shield malicious entries in `~/.claude/tasks/` from reaping.

    This class provides the counter-test pin: each hostile value is
    asserted NOT to appear in the skip_names passed to cleanup_old_tasks.
    Counter-test-by-revert confirmed: removing the allowlist line makes
    these tests fail (hostile values leak into skip_names).
    """

    @pytest.mark.parametrize("hostile_value", [
        "../etc",           # path traversal
        "\u2028",            # LINE SEPARATOR (role-marker injection class)
        "\x00",              # null byte (also blocked by OS at env layer,
                             # but allowlist is the in-process defense)
        "pact abc",          # space (breaks shell/path assumptions)
        "name\nwith\nnewline",  # newline injection
        "name;rm -rf /",     # shell metachar
        "name/with/slash",   # path separator
    ])
    def test_hostile_task_list_id_excluded_from_skip_names(self, hostile_value):
        """Each hostile CLAUDE_CODE_TASK_LIST_ID must be filtered out.

        Invokes main() with a hostile env value. Asserts the skip_names
        kwarg passed to cleanup_old_tasks does NOT contain the hostile
        string. The other skip members (team_name, session_id) are
        fixed non-empty so cleanup_old_tasks always runs.

        Uses `patch("os.environ.get", ...)` rather than
        `patch.dict("os.environ", ...)` because `os.environ` rejects
        embedded null bytes at the OS API layer — but the in-process
        allowlist is the defense-in-depth layer we're pinning here, so
        we simulate the env read directly.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io
        import os as _os

        real_env_get = _os.environ.get

        def fake_env_get(key, default=None):
            if key == "CLAUDE_CODE_TASK_LIST_ID":
                return hostile_value
            return real_env_get(key, default)

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch("session_end.os.environ.get", side_effect=fake_env_get),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-B"),
            patch("session_end.get_team_name", return_value="team-A"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        mock_tasks_ref = {}
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks_ref["m"] = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_tasks = mock_tasks_ref["m"]
        mock_tasks.assert_called_once()
        skip_names = mock_tasks.call_args.kwargs["skip_names"]

        assert hostile_value not in skip_names, (
            f"Hostile CLAUDE_CODE_TASK_LIST_ID={hostile_value!r} leaked into "
            f"skip_names={skip_names!r}. Regex allowlist at "
            f"session_end.py:615 must be filtering it."
        )
        # Sanity: the other two skip members still pass through.
        assert "team-A" in skip_names
        assert "sess-B" in skip_names

    def test_empty_task_list_id_short_circuits_before_regex(self):
        """Empty env value hits `if task_list_id and not re.fullmatch(...)`
        short-circuit on the first conjunct — regex is NOT called on empty.

        Pins the short-circuit half of the guard alongside the regex half.
        An empty string is also `not in skip_names` after the discard("")
        downstream, so this is mainly documentation-of-intent.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": ""}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-B"),
            patch("session_end.get_team_name", return_value="team-A"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_tasks.assert_called_once()
        skip_names = mock_tasks.call_args.kwargs["skip_names"]
        # Empty string must be discarded by skip_names.discard("") below
        # the regex filter, independent of the regex path.
        assert "" not in skip_names
        assert skip_names == {"team-A", "sess-B"}

    def test_allowlist_passes_through_valid_task_list_id(self):
        """Well-formed id still passes the allowlist.

        Regression guard: prevents a future over-tight regex from
        silently rejecting valid platform-issued ids. The `task-C` shape
        is identical to the cycle-1 happy-path test but pins the
        non-rejection branch HERE in the same class for locality.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict(
                "os.environ",
                {"CLAUDE_CODE_TASK_LIST_ID": "task-C_123"},
                clear=False,
            ),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-B"),
            patch("session_end.get_team_name", return_value="team-A"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        skip_names = mock_tasks.call_args.kwargs["skip_names"]
        assert "task-C_123" in skip_names


# =============================================================================
# Cycle-3 remediation: F2 inner symlink mtime oracle pin — #412 Fix B
# =============================================================================


class TestTaskDirMtimeInnerSymlink:
    """Pins `child.stat(follow_symlinks=False)` at session_end.py:358.

    `_task_dir_mtime` iterates each `*.json` child to compute the tight
    max-mtime used as the dir's reap decision. If `stat()` FOLLOWED
    symlinks, an attacker who plants `tasks/{real-dir}/x.json` as a
    symlink to an external file could force the reaper's decision to
    reflect the TARGET's mtime rather than the LINK's — an oracle leak
    (probe "is /etc/passwd fresh?") and a potential reap-suppression
    vector (point at a file that's always "touched" to keep the dir
    alive past its TTL).

    The `follow_symlinks=False` flag makes `stat()` use lstat semantics
    so the LINK's own mtime drives the decision, not the target's.
    These two tests pin that invariant asymmetrically: removing the
    flag flips both tests to fail (counter-test-by-revert verified).
    """

    def test_old_link_with_fresh_target_causes_reap(self, tmp_path):
        """Oracle-suppression scenario: old link, fresh target → dir reaps.

        With `follow_symlinks=False`, probe sees link lstat (old) →
        max-child is old → dir reaped. Without the flag, probe follows
        to target (fresh) → max-child is fresh → dir preserved
        (attacker wins: the planted symlink suppresses reap).
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # External target with FRESH mtime (attacker-chosen probe target).
        target = tmp_path / "external-target.json"
        target.write_text("{}")
        _os.utime(str(target), (_time.time(), _time.time()))

        # tasks subdir with single symlinked `.json` child whose LINK
        # mtime is OLD (40d). lstat must win.
        d = tmp_path / "pact-planted"
        d.mkdir()
        link = d / "1.json"
        link.symlink_to(target)
        old = _time.time() - (40 * 86400)
        _os.utime(str(link), (old, old), follow_symlinks=False)
        # Force parent dir mtime old too so fallback wouldn't mask the
        # intended decision path.
        _os.utime(str(d), (old, old))

        reaped, _ = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        # Link-mtime (40d) > TTL → reap. If target (fresh) were used
        # instead, this would return 0 and the test would fail.
        assert reaped == 1, (
            "Old-link-with-fresh-target scenario must reap — link lstat "
            "mtime (40d) should win over target stat mtime (fresh). "
            "If this fails, `follow_symlinks=False` was likely removed "
            "from session_end.py:358."
        )
        assert not d.exists()
        # Target must survive (rmtree on the parent dir unlinks the
        # symlink entry but doesn't recurse through it).
        assert target.exists()

    def test_fresh_link_with_old_target_preserves_dir(self, tmp_path):
        """Mirror scenario: fresh link, old target → dir preserved.

        With `follow_symlinks=False`, probe sees link lstat (fresh) →
        max-child is fresh → dir preserved. Without the flag, probe
        follows to target (old) → max-child is old → dir reaped
        (legitimate live task set destroyed).
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # External target with OLD mtime.
        target = tmp_path / "external-old-target.json"
        target.write_text("{}")
        old_time = _time.time() - (60 * 86400)
        _os.utime(str(target), (old_time, old_time))

        # tasks subdir with fresh-link-to-old-target child.
        d = tmp_path / "pact-live"
        d.mkdir()
        link = d / "1.json"
        link.symlink_to(target)
        # Link lstat mtime: fresh (now).
        _os.utime(str(link), (_time.time(), _time.time()), follow_symlinks=False)
        # Force parent dir mtime old so a regression that lost lstat
        # semantics couldn't accidentally be masked by a fresh parent.
        _os.utime(str(d), (old_time, old_time))

        reaped, _ = cleanup_old_tasks(
            skip_names={"pact-current"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        # Link-mtime (fresh) < TTL → preserve. If target (old) were
        # used instead, dir would reap (reaped == 1) and this would fail.
        assert reaped == 0, (
            "Fresh-link-with-old-target scenario must preserve — link "
            "lstat mtime (fresh) should win over target stat mtime (60d). "
            "If this fails, `follow_symlinks=False` was likely removed "
            "from session_end.py:358."
        )
        assert d.exists()
        assert target.exists()


# =============================================================================
# Cycle-1 remediation: symlink pinning (②) — #412 Fix B
# =============================================================================


class TestReaperSymlinkHandling:
    """Symlinks under the reaper base-dirs must be SKIPPED entirely.

    Counter-test-by-revert target: remove the `if entry.is_symlink():
    continue` guard from any of the three reapers and exactly one of
    these tests fails. See HANDOFF evidence.
    """

    def _aged_target(self, tmp_path, name, age_days=60):
        """Create an aged-target directory OUTSIDE the reaper base-dir.

        Placed as a sibling of tmp_path so the reaper's iterdir scan
        cannot see the target itself — only the symlink that points to
        it. Uses `tmp_path.parent / (tmp_path.name + suffix)` so the
        pytest fixture still cleans it up.

        Ages ALL children AND the parent dir to `age_days` old. Both a
        `*.json` child (load-bearing for the tasks-reaper path, which
        globs `*.json`) AND a non-json `precious.txt` (for the teams
        path, which globs `*`) are aged. Aging the children is load-
        bearing for guard-sensitivity (PR #433 cycle-7 F2 remediation):
        without it, `_dir_max_child_mtime` sees a fresh child (write_text
        stamps current time) and the symlink guard's removal is masked —
        the dir would be preserved for the WRONG reason (fresh child,
        not guard).
        """
        import os as _os
        import time as _time
        victim = tmp_path.parent / f"{tmp_path.name}-victim-{name}"
        victim.mkdir(exist_ok=True)
        precious = victim / "precious.txt"
        precious.write_text("user data that must survive")
        # Also drop an aged `*.json` child so the tasks-reaper probe
        # (glob="*.json") returns the aged mtime rather than falling
        # back to parent lstat on the symlink (which is fresh).
        json_child = victim / "payload.json"
        json_child.write_text("{}")
        old = _time.time() - (age_days * 86400)
        _os.utime(str(precious), (old, old))
        _os.utime(str(json_child), (old, old))
        _os.utime(str(victim), (old, old))
        return victim

    def test_cleanup_old_teams_skips_symlinks(self, tmp_path):
        """Symlink in ~/.claude/teams/ is SKIPPED — target preserved, link preserved.

        Without the is_symlink guard, is_dir() follows the link, stat
        reads the target's (ancient) mtime, and rmtree unlinks the link
        entry. The guard short-circuits BEFORE is_dir so the symlink
        isn't even considered a candidate.
        """
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        # Hex-only name — cycle-4 pattern gate `^pact-[a-f0-9-]+$` rejects
        # letters outside [a-f]. BOTH the real old dir AND the symlink
        # name must be hex-valid, otherwise the pattern gate filters them
        # before either the is_symlink guard or the TTL probe runs —
        # which would mask the guard's sensitivity (PR #433 cycle-7 F2).
        _make_team_dir(tmp_path, "pact-0dd0eaf1", age_days=40)
        victim = self._aged_target(tmp_path, "teams")
        # "deadbeef" is all-hex so the pattern gate admits the symlink.
        link = tmp_path / "pact-deadbeef"
        link.symlink_to(victim)

        try:
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-current",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

            # Real old dir reaped; symlink NOT counted as reaped (guard skipped it).
            assert reaped == 1, f"expected 1 real reap, got {reaped}"
            assert skipped == 0
            assert link.is_symlink(), "symlink itself must survive (not rmtree'd)"
            assert victim.exists(), "target must survive"
            assert (victim / "precious.txt").exists(), "target contents must survive"
            assert not (tmp_path / "pact-0dd0eaf1").exists(), "real old dir reaped"
        finally:
            import shutil as _shutil
            if link.is_symlink():
                link.unlink()
            _shutil.rmtree(victim, ignore_errors=True)

    def test_cleanup_old_tasks_skips_symlinks(self, tmp_path):
        """Symlink in ~/.claude/tasks/ is SKIPPED — target preserved, link preserved.

        Parallel to teams case; pins identical invariant for the tasks
        reaper. Guard runs BEFORE _task_dir_mtime so the mtime probe
        never touches the target.
        """
        from session_end import cleanup_old_tasks

        _make_task_dir(
            tmp_path, "pact-old-real",
            child_ages_days=[40], parent_age_days=40,
        )
        victim = self._aged_target(tmp_path, "tasks")
        link = tmp_path / "pact-evil-link"
        link.symlink_to(victim)

        try:
            reaped, skipped = cleanup_old_tasks(
                skip_names={"pact-current"},
                tasks_base_dir=str(tmp_path),
                max_age_days=30,
            )

            assert reaped == 1, f"expected 1 real reap, got {reaped}"
            assert skipped == 0
            assert link.is_symlink()
            assert victim.exists()
            assert (victim / "precious.txt").exists()
            assert not (tmp_path / "pact-old-real").exists()
        finally:
            import shutil as _shutil
            if link.is_symlink():
                link.unlink()
            _shutil.rmtree(victim, ignore_errors=True)

    def test_cleanup_old_sessions_skips_symlinks(self, tmp_path):
        """Symlink at a UUID slot in pact-sessions/{slug}/ is SKIPPED.

        Parallel to teams/tasks; documents the guard on the third reaper.

        ⚠️ SENSITIVITY CAVEAT: this test is NOT guard-sensitive today
        (verified via in-memory counter-test-by-revert: removing the
        is_symlink guard from cleanup_old_sessions leaves all assertions
        passing). Under the current control flow, rmtree of a symlink
        with ignore_errors=True either unlinks only the link and leaves
        the target intact (benign) OR the symlink's target-mtime check +
        paused-check raises OSError in fail-open paths that absorb it.
        The observed result: link + target both preserved regardless.

        This test is kept as a BEHAVIORAL PIN — it documents "live
        symlink survives" under current semantics so a future refactor
        that follows symlinks into rmtree recursion (or flips to
        follow_symlinks=True on stat) would break the pin. The test
        DOES pin the current invariant; it just doesn't independently
        prove the guard is load-bearing today. Teams + tasks variants
        ARE guard-sensitive and cover that defense.
        """
        from session_end import cleanup_old_sessions

        slug_dir = tmp_path / "proj"
        slug_dir.mkdir()
        current = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        real_old = "11111111-2222-3333-4444-555555555555"
        link_uuid = "22222222-3333-4444-5555-666666666666"

        # Current + real-old sessions
        import time as _time, os as _os
        for sid in (current, real_old):
            d = slug_dir / sid
            d.mkdir()
            (d / "ctx.json").write_text("{}")
        old_time = _time.time() - (40 * 86400)
        _os.utime(str(slug_dir / real_old), (old_time, old_time))

        # Symlink with a valid UUID name pointing at aged external target
        victim = self._aged_target(tmp_path, "sessions")
        link = slug_dir / link_uuid
        link.symlink_to(victim)
        assert link.is_symlink(), "precondition: link exists"

        try:
            cleanup_old_sessions(
                project_slug="proj",
                current_session_id=current,
                sessions_dir=str(tmp_path),
                max_age_days=30,
            )

            assert (slug_dir / current).exists(), "current session survives"
            assert not (slug_dir / real_old).exists(), "real old session reaped"
            # Guard-sensitive: without `if entry.is_symlink(): continue`,
            # rmtree would unlink the symlink entry. Guard preserves it.
            assert link.is_symlink(), (
                "symlink must survive the reaper — if this fails, the "
                "is_symlink guard in cleanup_old_sessions was removed"
            )
            assert link.exists(), "symlink still resolves to target"
            assert victim.exists(), "target survives"
            assert (victim / "precious.txt").exists(), "target contents survive"
        finally:
            import shutil as _shutil
            if link.is_symlink():
                link.unlink()
            _shutil.rmtree(victim, ignore_errors=True)


# =============================================================================
# Cycle-1 remediation: teams_ran/tasks_ran bools — #412 Fix B M6-gap closure
# =============================================================================


class TestCleanupSummaryReaperRan:
    """`teams_ran`/`tasks_ran` discriminate "ran, found nothing" from
    "short-circuited" per side.

    Without these bools, a (0,0,0,0) counts row is ambiguous: did both
    reapers run and find nothing, or did either side short-circuit at
    the callsite guard? Cycle-8 splits the single `reaper_ran` bool
    into per-reaper bools so an auditor can tell WHICH side ran.
    """

    def _run_with(self, *, team_return, session_id="sess-id", env=None):
        """Run main() with real reapers no-op'd; record cleanup_summary event."""
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        env = env or {}
        captured = []

        patches = [
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
        assert len(summaries) == 1, f"expected 1 cleanup_summary, got {len(summaries)}"
        return summaries[0]

    def test_only_teams_ran_when_team_name_only_skip_channel(self):
        """team_name set (non-allowlist-safe) → teams runs, tasks short-circuits.

        Cycle-8 un-skips the previously-unreachable "only teams ran"
        row. With the new `_assemble_tasks_skip_set` helper that runs
        `is_safe_path_component` on team_name, a deliberately-unsafe
        team_name drops from skip_names — so skip_names = empty (all
        three channels rejected) → tasks reaper short-circuits on
        `if skip_names:`. Meanwhile the teams-reaper callsite check is
        `if current_team_name:` (truthy-only, no allowlist) so it still
        runs. This is the reachable "only teams ran" row.

        In practice this requires a team_name that fails the allowlist.
        Construct with `pact/weird` which has a slash.
        """
        ev = self._run_with(
            team_return="pact/weird",  # fails is_safe_path_component → drops from skip_names
            session_id="",
            env={"CLAUDE_CODE_TASK_LIST_ID": ""},
        )
        assert ev["teams_ran"] is True
        assert ev["tasks_ran"] is False

    def test_only_tasks_ran_when_team_name_empty(self):
        """team_name empty, session_id set → teams short-circuits, tasks runs."""
        ev = self._run_with(team_return="", session_id="sess-X")
        assert ev["teams_ran"] is False
        assert ev["tasks_ran"] is True
        assert ev["teams_reaped"] == 0
        assert ev["tasks_reaped"] == 0

    def test_both_ran_when_both_channels_populated(self):
        """team_name and session_id both set → both reapers run."""
        ev = self._run_with(team_return="pact-current", session_id="sess-X")
        assert ev["teams_ran"] is True
        assert ev["tasks_ran"] is True

    def test_neither_ran_when_both_channels_empty(self):
        """team_name empty, session_id empty, env empty → BOTH short-circuit.

        M6-gap closure: distinguishes this row from "ran-and-found-
        nothing" in the audit journal.
        """
        ev = self._run_with(team_return="", session_id="", env={"CLAUDE_CODE_TASK_LIST_ID": ""})
        assert ev["teams_ran"] is False
        assert ev["tasks_ran"] is False
        assert ev["teams_reaped"] == 0
        assert ev["teams_skipped"] == 0
        assert ev["tasks_reaped"] == 0
        assert ev["tasks_skipped"] == 0


# =============================================================================
# Cycle-4 remediation: teams child-mtime + name-shape gate pins — #412 Fix B
# =============================================================================


def _touch_child(child_path, age_days):
    """Set mtime on a file (or symlink via lstat) to `age_days` ago."""
    import os as _os
    import time as _time
    old = _time.time() - (age_days * 86400)
    _os.utime(str(child_path), (old, old))


class TestTeamsChildMtimeProbe:
    """Cycle-4 Test 1 — teams reaper walks child mtimes, not parent mtime.

    Post-cycle-4, cleanup_old_teams invokes `_dir_max_child_mtime(entry,
    glob="*")`. This pins the invariant that a fresh child inside an
    old-parent team dir preserves the dir (because the child-walk
    dominates the parent's mtime).

    Why load-bearing: POSIX in-place overwrite of `config.json` does NOT
    bump the parent dir's mtime. A reaper that read parent-dir mtime
    alone would false-reap live teams whose config.json was recently
    rewritten without rename/unlink. Max-child-mtime is the tight upper
    bound.
    """

    def test_fresh_child_preserves_old_parent_team_dir(self, tmp_path):
        """Old parent mtime + fresh child → dir PRESERVED (child walk wins).

        Construction mirrors the platform shape: parent dir mtime 40d old
        (simulates a team dir that hasn't had members added/removed
        recently), but `config.json` inside is fresh (simulates a recent
        in-place rewrite). The reaper must read the child and preserve.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        current = "pact-current"
        _make_team_dir(tmp_path, current, age_days=0)

        # Stale-parent-but-fresh-child team dir.
        # Name must match _TEAM_NAME_PATTERN=^pact-[a-f0-9-]+$ (hex only).
        target = tmp_path / "pact-abcd1234"
        target.mkdir()
        config = target / "config.json"
        config.write_text('{"members": []}')
        # Force fresh child (just in case utime defaults differ).
        _os.utime(str(config), (_time.time(), _time.time()))
        # Force aged parent dir mtime AFTER the child write.
        old = _time.time() - (40 * 86400)
        _os.utime(str(target), (old, old))

        reaped, skipped = cleanup_old_teams(
            current_team_name=current,
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert skipped == 0
        assert reaped == 0, (
            "Fresh child inside aged-parent team dir must PRESERVE the dir. "
            "If this test shows reaped == 1, the reaper is probing parent "
            "mtime instead of max-child mtime — likely regression of "
            "_dir_max_child_mtime back to direct entry.stat()."
        )
        assert target.exists()

    def test_aged_child_in_old_parent_still_reaps(self, tmp_path):
        """Aged parent + aged child → dir REAPED (both signals agree).

        Asymmetric partner of the preservation pin. Without this test,
        the preservation pin could pass spuriously (e.g. if the reaper
        silently short-circuits all teams). This confirms the reap path
        still fires when the child signal agrees with the parent signal.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        target = tmp_path / "pact-dead"
        target.mkdir()
        config = target / "config.json"
        config.write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(config), (old, old))
        _os.utime(str(target), (old, old))

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert not target.exists()

    def test_fresh_member_subdir_preserves_team_dir(self, tmp_path):
        """Fresh SubagentStart member subdir inside aged team dir → PRESERVED.

        Pins the `glob="*"` (not `glob="*.json"`) choice at the teams
        call site. A team dir whose only fresh artifact is a subdir
        (not a *.json) must still preserve — SubagentStart creates
        member-named subdirs, and those touches are the signal of a
        live team under the new child-walk semantics.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        # Name must match _TEAM_NAME_PATTERN=^pact-[a-f0-9-]+$ (hex only).
        _make_team_dir(tmp_path, "pact-aaaa", age_days=0)
        target = tmp_path / "pact-bbbb-cccc"
        target.mkdir()
        # Aged config.json
        config = target / "config.json"
        config.write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(config), (old, old))
        # FRESH member subdir (SubagentStart shape).
        member = target / "member-engineer"
        member.mkdir()
        _os.utime(str(member), (_time.time(), _time.time()))
        # Aged parent dir mtime.
        _os.utime(str(target), (old, old))

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            "Fresh member subdir must preserve the team — the teams reaper "
            "must pass glob='*' (not glob='*.json') to _dir_max_child_mtime. "
            "If this test shows reaped == 1, the teams call-site is "
            "passing the wrong glob or reverted to entry.stat()."
        )
        assert target.exists()


class TestTeamNameShapeGate:
    """Cycle-4 Test 2 — _TEAM_NAME_PATTERN gate preserves non-PACT dirs.

    Post-cycle-4, `cleanup_old_teams` filters entries through
    `_TEAM_NAME_PATTERN = r"^pact-[a-f0-9-]+$"` before considering them
    for age-check. This treats `~/.claude/teams/` as shared space —
    non-PACT tooling that creates team dirs under that path is protected
    from reaping.
    """

    def test_non_pact_name_preserved_even_when_old(self, tmp_path):
        """Old `foo-bar/` and `pact-XYZ/` (uppercase) dirs PRESERVED.

        The gate rejects on the POSITIVE allowlist: only `^pact-[a-f0-9-]+$`
        passes. Uppercase hex, leading-capital names, and missing prefix
        all filtered out. Load-bearing: without the gate, a third-party
        tool's ~/.claude/teams/myapp/ would be reaped.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)

        non_pact_names = [
            "foo-bar",                    # no pact- prefix
            "pact-UPPERCASE",              # uppercase (non-hex)
            "PACT-lowerhex",               # uppercase prefix
            "myapp",                       # bare name
            "pact",                        # prefix-without-hyphen
            "pact_underscore",             # underscore, not hyphen
        ]
        for name in non_pact_names:
            d = tmp_path / name
            d.mkdir()
            (d / "config.json").write_text("{}")
            old = _time.time() - (40 * 86400)
            _os.utime(str(d / "config.json"), (old, old))
            _os.utime(str(d), (old, old))

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            f"Non-PACT-shaped team dirs must be preserved by the "
            f"_TEAM_NAME_PATTERN gate. If this shows reaped > 0, the "
            f"gate was likely removed or loosened."
        )
        for name in non_pact_names:
            assert (tmp_path / name).exists(), f"{name} should survive"

    def test_pact_shaped_name_still_reaps_when_old(self, tmp_path):
        """Asymmetric partner: well-formed pact-xxx name DOES reap when aged.

        Without this, the pattern-gate preservation pin could pass
        spuriously if the reaper short-circuited all teams. This confirms
        the gate is PERMISSIVE for valid names.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)
        target = tmp_path / "pact-deadbeef"
        target.mkdir()
        (target / "config.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(target / "config.json"), (old, old))
        _os.utime(str(target), (old, old))

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1
        assert not target.exists()


class TestTeamNameRegexStrictAnchor:
    """Cycle-7 N2 — `_TEAM_NAME_PATTERN` uses `\\Z` (strict end-of-string).

    Python `re` treats `$` as end-of-string OR immediately before a
    trailing newline. Without `\\Z`, a crafted team dir name like
    `pact-deadbeef\\n` would PASS the gate and land in the skip / reap
    eligibility path.  `\\Z` anchors strictly and rejects trailing
    newlines. Bounded today because `generate_team_name` never produces
    such a name, but a same-user attacker or a filesystem tool that
    creates a dir like this could bypass the invariant.

    POSIX permits `\\n` in filenames. macOS APFS was empirically verified
    to accept `mkdir("pact-deadbeef\\n")` and round-trip the literal name
    through `iterdir()`.
    """

    def test_trailing_newline_name_rejected_by_strict_anchor(self, tmp_path):
        """Dir named `pact-deadbeef\\n` is PRESERVED by the `\\Z` gate.

        COUNTER-TEST BY REVERT target: switching `\\Z` back to `$` in
        `_TEAM_NAME_PATTERN` flips this test — the newline-suffixed name
        matches under `$` and the dir gets reaped. Evidence (documented
        in HANDOFF):
          - With `\\Z`: `pact-deadbeef\\n` rejected by gate → preserved.
          - With `$`:   `pact-deadbeef\\n` matches → dir reaped.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        _make_team_dir(tmp_path, "pact-current", age_days=0)

        # Trailing-newline name. POSIX allows; macOS APFS verified.
        hostile = "pact-deadbeef\n"
        d = tmp_path / hostile
        try:
            d.mkdir()
        except OSError as e:
            pytest.skip(
                f"Filesystem rejects `\\n` in directory names ({e!r}); "
                f"strict-anchor pin cannot be exercised here"
            )
        (d / "config.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "config.json"), (old, old))
        _os.utime(str(d), (old, old))

        reaped, skipped = cleanup_old_teams(
            current_team_name="pact-current",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            "Trailing-newline name must be REJECTED by `\\Z` strict "
            "anchor. If reaped == 1, the regex was likely reverted from "
            "`\\Z` to `$` (which matches end-of-string OR immediately "
            "before a trailing newline)."
        )
        assert d.exists(), "hostile-named dir must survive the pattern gate"


class TestTaskDirMtimeLstatPortability:
    """Cycle-4 Test 3 — child.lstat() matches prior stat(follow_symlinks=False).

    Cycle-4 changed `child.stat(follow_symlinks=False)` to `child.lstat()`
    for Python pre-3.10 portability. Semantics are identical: both
    return the link's own mtime without dereferencing. This pin confirms
    the lstat form preserves the oracle-suppression defense.

    Existing TestTaskDirMtimeInnerSymlink (cycle-3) already pins the
    defense behaviorally — if those tests still pass after the cycle-4
    rename, the contract holds. This class adds ONE symmetric test
    directly against the generalized helper to pin the lstat call-form.
    """

    def test_lstat_returns_link_mtime_not_target_mtime(self, tmp_path):
        """Direct probe: _dir_max_child_mtime on a dir with old-link-fresh-target.

        Invokes `_dir_max_child_mtime` directly (cycle-8 removed the
        `_task_dir_mtime` back-compat wrapper; callers now use the
        generalized helper with an explicit glob). The probe MUST return
        the link's lstat mtime (old), not the target's stat mtime
        (fresh). If `lstat()` were reverted to `stat()`, the returned
        value would be fresh and this test fails.
        """
        import os as _os
        import time as _time
        from session_end import _dir_max_child_mtime

        # Fresh external target
        target = tmp_path / "external-target.json"
        target.write_text("{}")
        _os.utime(str(target), (_time.time(), _time.time()))

        # tasks dir with OLD symlink-child pointing to FRESH target
        d = tmp_path / "pact-probe"
        d.mkdir()
        link = d / "1.json"
        link.symlink_to(target)
        old = _time.time() - (40 * 86400)
        _os.utime(str(link), (old, old), follow_symlinks=False)

        result = _dir_max_child_mtime(d, glob="*.json")

        # Must be ~40d old (link lstat mtime), NOT fresh (target stat mtime).
        age_days = (_time.time() - result) / 86400
        assert age_days > 30, (
            f"_dir_max_child_mtime returned {age_days:.1f}d old — expected >30d "
            f"(link lstat mtime). If this test fails with a fresh (<1d) "
            f"result, lstat() was reverted to stat() (follow_symlinks=True)."
        )


# =============================================================================
# Cycle-5 defensive-hardening pins — #412 Fix B (cycle-5 contract)
# =============================================================================


class TestTeamsCaseInsensitiveSkip:
    """Cycle-5 Test 1 — `cleanup_old_teams` skip uses case-insensitive compare.

    `pact_context.get_team_name()` returns a lowercased name and the
    `generate_team_name` INVARIANT pins lowercase, so byte-exact compare
    is correct-by-coincidence today. Cycle-5 changed the comparison to
    `entry.name.lower() == current_team_name.lower()` as belt-and-
    suspenders against future drift in either producer.
    """

    def test_mixed_case_team_dir_preserved_when_caller_passes_lowercase(self, tmp_path):
        """Mixed-case dir on disk + lowercase current_team_name → PRESERVED.

        Construct a team dir whose on-disk name is `pact-AABB1122` (mixed
        case, all hex-valid). Pass `pact-aabb1122` (lowercase) as
        current_team_name. Under case-insensitive compare, the dir is
        SKIPPED (preserved). Under byte-exact compare, the dir would be
        treated as a sibling and reaped.

        Note: the directory name must still pass `_TEAM_NAME_PATTERN`
        which is lowercase-only `^pact-[a-f0-9-]+$`. Mixed-case
        normally wouldn't match — but on case-insensitive filesystems
        (macOS HFS+/APFS-default), `mkdir("pact-AABB1122")` creates a
        directory whose `iterdir()` may return either the literal
        `"pact-AABB1122"` or the canonical lowercased form. We force
        the test fixture to write a directory whose name preserves
        case. If iterdir returns lowercase, the pattern gate accepts;
        if mixed case, the pattern gate rejects regardless of compare.
        Either way, this test pins the COMPARE semantic; the pattern
        gate is orthogonal.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        # Use ALL-LOWERCASE on-disk name (so pattern gate accepts) but
        # pass a different-case current_team_name. The semantic we're
        # pinning: skip predicate is case-insensitive in the COMPARE
        # direction (caller-provided value can differ in case).
        ondisk = "pact-aabb1122"
        d = tmp_path / ondisk
        d.mkdir()
        (d / "config.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "config.json"), (old, old))
        _os.utime(str(d), (old, old))

        # Pass mixed-case current_team_name. With .lower()==.lower(),
        # this must skip the on-disk dir.
        reaped, skipped = cleanup_old_teams(
            current_team_name="PACT-AABB1122",
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0, (
            "Mixed-case current_team_name must skip lowercase on-disk "
            "match via .lower() compare. If reaped == 1, the compare "
            "regressed to byte-exact (==)."
        )
        assert d.exists()

    def test_different_name_does_not_skip_via_case_match(self, tmp_path):
        """Asymmetric partner (G7) — entry name that is a SUBSTRING of
        current_team_name must still reap (not skip).

        Defends against a substring-regression: if the compare drifted
        from `entry.name.lower() == current_team_name.lower()` to
        `entry.name.lower() in current_team_name.lower()` (or the
        reverse), a positive pin with unrelated names would NOT flip
        red — so this fixture deliberately constructs a substring
        relationship (`pact-abc` is a prefix of `pact-abcd1234`).
        Under `==`: different strings → reap (correct).
        Under `in`: "pact-abc" in "pact-abcd1234" → True → skip
        (regressed — stale sibling survives).

        The on-disk shorter name must pass `_TEAM_NAME_PATTERN`
        (`^pact-[a-f0-9-]+$`) — "pact-abc" does (a,b,c are hex).
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_teams

        # Substring-relationship fixture: on-disk "pact-abc" is a prefix
        # (and therefore a substring) of current_team_name "pact-abcd1234".
        # Both pass the pattern gate. Under EQUALITY, different strings
        # → reap. Under `in`-substring, shorter matches longer → skip
        # (the regression we're guarding against).
        ondisk = "pact-abc"
        d = tmp_path / ondisk
        d.mkdir()
        (d / "config.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "config.json"), (old, old))
        _os.utime(str(d), (old, old))

        reaped, _ = cleanup_old_teams(
            current_team_name="pact-abcd1234",  # contains "pact-abc"
            teams_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1, (
            "Substring-relationship sibling must REAP, not skip. If "
            "reaped == 0, the compare likely regressed to substring "
            "semantics (e.g. `entry.name.lower() in current_team_name."
            "lower()`) rather than equality."
        )
        assert not d.exists()


class TestTasksByteExactSkip:
    """Cycle-8 Test 6 — `cleanup_old_tasks` skip uses byte-exact (in)
    membership, NOT case-insensitive compare.

    Gap identified in PR #433 blind review (MED Read-Filesystem-Only): cycle-5 added
    `.lower()==.lower()` to teams-reaper skip (TestTeamsCaseInsensitiveSkip
    pins it). tasks-reaper intentionally uses byte-exact `entry.name in
    skip_names`. Without a pin on the tasks side, a future reviewer could
    add `.lower()` "for consistency" and no test would catch the
    semantic change.

    Asymmetric partner shape: a mixed-case on-disk dir + lowercase
    skip_names. Under byte-exact, they do NOT match → dir reaps. Under
    `.lower()` (the regression we're guarding against), they WOULD match
    → dir spuriously shielded.
    """

    def test_mixed_case_task_dir_reaps_despite_lowercase_skip_name(self, tmp_path):
        """On-disk `Pact-AABB1122` is REAPED when skip_names={`pact-aabb1122`}.

        COUNTER-TEST BY REVERT target: adding `.lower()` symmetry to the
        tasks-side compare (e.g. `entry.name.lower() in
        {s.lower() for s in skip_names}`) flips this — the mixed-case
        on-disk dir would be treated as a skip match and preserved.
        Byte-exact semantic REAPS it (the dir name literally differs
        byte-for-byte from the skip key).
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        # Mixed-case on-disk name. The tasks reaper has no pattern gate
        # (tasks/ allows arbitrary id shapes — uuid, hex, mixed-case),
        # so this name is ADMITTED for TTL consideration.
        ondisk = "Pact-AABB1122"
        d = tmp_path / ondisk
        d.mkdir()
        (d / "1.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "1.json"), (old, old))
        _os.utime(str(d), (old, old))

        # Lowercase skip_name. Under byte-exact `in`, "Pact-AABB1122" is
        # NOT in {"pact-aabb1122"} → dir is NOT skipped → reap path.
        reaped, skipped = cleanup_old_tasks(
            skip_names={"pact-aabb1122"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 1, (
            "Mixed-case on-disk name must REAP despite lowercase skip. "
            "If reaped == 0, the tasks-side skip was likely refactored to "
            "case-insensitive (e.g. `entry.name.lower() in "
            "{s.lower() for s in skip_names}`) — the byte-exact semantic "
            "is LOAD-BEARING (preserves asymmetric-trust model with "
            "teams-side case-insensitive defense)."
        )
        assert not d.exists()

    def test_exact_case_match_still_skips(self, tmp_path):
        """Asymmetric partner: byte-exact match DOES skip (positive pin).

        Without this, the preservation pin above could pass spuriously
        if the reaper ignored all dirs. Confirms the exact-match path
        still shields.
        """
        import os as _os
        import time as _time
        from session_end import cleanup_old_tasks

        ondisk = "pact-exact-match"
        d = tmp_path / ondisk
        d.mkdir()
        (d / "1.json").write_text("{}")
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "1.json"), (old, old))
        _os.utime(str(d), (old, old))

        reaped, _ = cleanup_old_tasks(
            skip_names={"pact-exact-match"},
            tasks_base_dir=str(tmp_path),
            max_age_days=30,
        )

        assert reaped == 0
        assert d.exists()


class TestDirMaxChildMtimeFallbackLstat:
    """Cycle-5 Test 2 — `_dir_max_child_mtime` parent fallback uses `lstat()`.

    Cycle-5 changed the empty-dir fallback from `entry.stat().st_mtime`
    to `entry.lstat().st_mtime`. The caller (`cleanup_old_teams`,
    `cleanup_old_tasks`) already has an outer `is_symlink()` guard, but
    the helper-in-isolation should not follow symlinks — defensive against
    future callers that forget the guard. Same pattern as cycle-2's F2 fix.
    """

    def test_fallback_uses_lstat_not_stat_on_dir_symlink(self, tmp_path):
        """Empty-dir fallback path: probe a symlink-dir, expect link's own mtime.

        The fallback branch is only reached when the dir has no children
        matched by the glob (`saw_any_child=False`). Construct an empty
        dir AND a symlink pointing at a fresh external dir; probe the
        symlink directly. Under `entry.lstat()`, returns the link's
        (old) mtime. Under `entry.stat()`, would dereference and return
        the target's (fresh) mtime.

        Note: this directly probes `_dir_max_child_mtime` rather than
        going through `cleanup_old_tasks`, because the caller's
        `is_symlink()` guard would short-circuit the symlink before the
        helper runs in normal flow. We're pinning the helper's defense-
        in-isolation behavior.
        """
        import os as _os
        import time as _time
        from session_end import _dir_max_child_mtime

        # Fresh external target dir
        target = tmp_path / "external-fresh-dir"
        target.mkdir()
        _os.utime(str(target), (_time.time(), _time.time()))

        # Symlink with OLD lstat mtime pointing at fresh target
        link = tmp_path / "old-symlink-dir"
        link.symlink_to(target)
        old = _time.time() - (40 * 86400)
        _os.utime(str(link), (old, old), follow_symlinks=False)

        # Probe the symlink with default glob (no children match → fallback).
        result = _dir_max_child_mtime(link, glob="*.json")

        assert result is not None, (
            "Empty-dir fallback should return a float, not sentinel"
        )
        age_days = (_time.time() - result) / 86400
        assert age_days > 30, (
            f"_dir_max_child_mtime fallback returned {age_days:.1f}d old — "
            f"expected >30d (link lstat). If returned ~0d (target stat), "
            f"the fallback was reverted from entry.lstat() to entry.stat()."
        )


class TestSessionIdAllowlist:
    """Cycle-5 Test 3 — `current_session_id` filtered by same regex as task_list_id.

    `task_list_id` flows through `re.fullmatch(r"[A-Za-z0-9_-]+", ...)`
    before insertion into skip_names. Cycle-5 applies the same allowlist
    to `current_session_id` for trust symmetry — without it, a hostile
    session_id (e.g. set via env-var injection on bare Claude Code)
    could land in skip_names and shield malicious entries from reaping.
    Mirrors `TestTaskListIdAllowlistRejection` shape.
    """

    @pytest.mark.parametrize("hostile_value", [
        "../etc",           # path traversal
        "\u2028",           # LINE SEPARATOR (role-marker injection class)
        "\x00",             # null byte
        "bad space",        # space (breaks shell/path assumptions)
        "name\nwith\nnewline",
        "name;rm -rf /",    # shell metachar
        "name/with/slash",  # path separator
    ])
    def test_hostile_session_id_excluded_from_skip_names(self, hostile_value):
        """Each hostile current_session_id must be filtered out of skip_names.

        Invokes main() with a hostile session_id. Asserts skip_names
        passed to cleanup_old_tasks does NOT contain the hostile value.
        Other skip members (team_name, task_list_id) must still pass.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": "task-C"}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value=hostile_value),
            patch("session_end.get_team_name", return_value="team-A"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        mock_tasks_ref = {}
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks_ref["m"] = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_tasks = mock_tasks_ref["m"]
        mock_tasks.assert_called_once()
        skip_names = mock_tasks.call_args.kwargs["skip_names"]

        assert hostile_value not in skip_names, (
            f"Hostile current_session_id={hostile_value!r} leaked into "
            f"skip_names={skip_names!r}. Cycle-5 allowlist on session_id "
            f"must filter it (mirroring task_list_id treatment)."
        )
        # Sanity: the other two skip members still pass through.
        assert "team-A" in skip_names
        assert "task-C" in skip_names

    def test_well_formed_session_id_passes_through(self):
        """Regression guard: well-formed UUID-shaped session_id still admitted."""
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        good_session = "5ddd5636-d408-4892-aaad-7c4eed80765d"
        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": "task-C"}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value=good_session),
            patch("session_end.get_team_name", return_value="team-A"),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        skip_names = mock_tasks.call_args.kwargs["skip_names"]
        assert good_session in skip_names


class TestTeamNameAllowlist:
    """Cycle-7 N3 — `current_team_name` filtered by same regex as
    task_list_id / session_id at skip-set construction.

    Before cycle-7, team_name entered `skip_names` unvalidated while
    `task_list_id` (line 744) and `session_id` (line 755) both flowed
    through `re.fullmatch(r"[A-Za-z0-9_-]+", ...)`. Bounded today by
    `generate_team_name`'s producer-side filter, but defense-in-depth
    should not asymmetrically trust one of three channels. Mirrors
    `TestSessionIdAllowlist` / `TestTaskListIdAllowlistRejection`.

    Note: the teams REAPER still receives the raw `current_team_name`
    (not `safe_team_name`) — pattern gate inside cleanup_old_teams is
    the teams-side defense. The allowlist at line 770 only guards the
    skip_names set passed to cleanup_old_tasks.
    """

    @pytest.mark.parametrize("hostile_value", [
        "../etc",           # path traversal
        "\u2028",           # LINE SEPARATOR (role-marker injection class)
        "\x00",             # null byte
        "name with space",  # space (breaks shell/path assumptions)
        "name\nwith\nnewline",
        "name;rm -rf /",    # shell metachar
        "name/with/slash",  # path separator
    ])
    def test_hostile_team_name_excluded_from_skip_names(self, hostile_value):
        """Each hostile current_team_name is filtered from skip_names.

        COUNTER-TEST BY REVERT target: removing the `safe_team_name`
        allowlist at session_end.py:769-773 (restoring the direct
        `{current_team_name, task_list_id, safe_session_id}` shape)
        flips this test — hostile team_names leak into skip_names.
        """
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": "task-C"}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-B"),
            patch("session_end.get_team_name", return_value=hostile_value),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        mock_tasks_ref = {}
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks_ref["m"] = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        mock_tasks = mock_tasks_ref["m"]
        mock_tasks.assert_called_once()
        skip_names = mock_tasks.call_args.kwargs["skip_names"]

        assert hostile_value not in skip_names, (
            f"Hostile current_team_name={hostile_value!r} leaked into "
            f"skip_names={skip_names!r}. Cycle-7 allowlist on team_name "
            f"(session_end.py:769-773) must filter it — mirroring the "
            f"task_list_id and session_id channels."
        )
        # Sanity: the other two skip members still pass through.
        assert "task-C" in skip_names
        assert "sess-B" in skip_names

    def test_well_formed_team_name_passes_through(self):
        """Regression guard: well-formed `pact-xxxxxxxx` team_name still admitted."""
        from unittest.mock import patch
        from contextlib import ExitStack
        import io as _io

        good_team = "pact-0001639f"
        patches = [
            patch("sys.stdin", _io.StringIO("{}")),
            patch.dict("os.environ", {"CLAUDE_CODE_TASK_LIST_ID": "task-C"}, clear=False),
            patch("session_end.pact_context.init"),
            patch("session_end.get_project_dir", return_value="/t/proj"),
            patch("session_end.get_session_dir", return_value=""),
            patch("session_end.get_session_id", return_value="sess-B"),
            patch("session_end.get_team_name", return_value=good_team),
            patch("session_end.get_task_list", return_value=[]),
            patch("session_end.check_unpaused_pr", return_value=None),
            patch("session_end.cleanup_teachback_markers"),
            patch("session_end.cleanup_old_sessions"),
            patch("session_end.cleanup_old_teams", return_value=(0, 0)),
            patch("session_end._cleanup_old_checkpoints"),
            patch("session_end.append_event"),
        ]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            mock_tasks = stack.enter_context(
                patch("session_end.cleanup_old_tasks", return_value=(0, 0))
            )
            from session_end import main
            with pytest.raises(SystemExit):
                main()

        skip_names = mock_tasks.call_args.kwargs["skip_names"]
        assert good_team in skip_names


class TestSentinelFalseReapHardening:
    """Cycle-5 Test 4 (LOAD-BEARING) — sentinel from `_dir_max_child_mtime`
    causes caller to skip, NOT false-reap.

    Architect M1 / Backend L5 hardening: under a permission regression
    where every child stat raises, the OLD helper fell back to parent
    mtime — meaning a permission-anomaly dir whose parent happens to be
    aged would be REAPED on stale-but-unobserved age. Cycle-5 returns
    `None` sentinel in this case; caller treats as `skipped`.

    This is the most consequential cycle-5 invariant — counter-test-by-
    revert is required.
    """

    def test_sentinel_returned_when_all_child_stats_fail(self, tmp_path):
        """Total probe failure → `_dir_max_child_mtime` returns None.

        Construct a dir with one child; mock `Path.lstat` to raise
        OSError on that child. The helper should return `None` because
        `saw_any_child=True` but `latest` stays 0.0.
        """
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import _dir_max_child_mtime

        d = tmp_path / "pact-probetarget"
        d.mkdir()
        child = d / "1.json"
        child.write_text("{}")

        real_lstat = _Path.lstat

        def flaky_lstat(self, *args, **kwargs):
            if str(self) == str(child):
                raise OSError("permission denied")
            return real_lstat(self, *args, **kwargs)

        with _patch.object(_Path, "lstat", flaky_lstat):
            result = _dir_max_child_mtime(d, glob="*.json")

        assert result is None, (
            f"Expected sentinel None, got {result!r}. saw_any_child=True "
            f"but every child.lstat() raised → must return sentinel, NOT "
            f"fall back to parent mtime (false-reap risk)."
        )

    def test_caller_skips_on_sentinel_no_false_reap(self, tmp_path):
        """`cleanup_old_tasks` increments skipped (not reaped) on sentinel.

        Same scenario as above wrapped in the caller. Pre-cycle-5 the
        caller used parent-mtime fallback (40d) → reaped. Post-cycle-5
        the caller honors the sentinel → skipped == 1, reaped == 0.
        """
        from unittest.mock import patch as _patch
        from pathlib import Path as _Path
        from session_end import cleanup_old_tasks

        d = _make_task_dir(
            tmp_path, "pact-probetarget",
            child_ages_days=[5], parent_age_days=40,
        )
        child = d / "1.json"

        real_lstat = _Path.lstat

        def flaky_lstat(self, *args, **kwargs):
            if str(self) == str(child):
                raise OSError("permission denied")
            return real_lstat(self, *args, **kwargs)

        with _patch.object(_Path, "lstat", flaky_lstat):
            reaped, skipped = cleanup_old_tasks(
                skip_names={"pact-current"},
                tasks_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert reaped == 0, (
            "Caller must NOT reap on sentinel. If reaped == 1, the "
            "sentinel-handling guard in cleanup_old_tasks (`if mtime is "
            "None: skipped += 1; continue`) was removed or short-circuited."
        )
        assert skipped == 1
        assert d.exists()

    def test_caller_skips_on_sentinel_no_false_reap_teams_path(self, tmp_path):
        """G8 — teams-side sentinel guard mirror.

        Mirrors `test_caller_skips_on_sentinel_no_false_reap` but via
        `cleanup_old_teams` rather than `cleanup_old_tasks`. The
        sentinel-handling guard exists in BOTH reapers (cleanup_old_teams
        at session_end.py:527-529 and cleanup_old_tasks at 600-602). Test
        4's original caller-integration pin only probes the tasks path —
        a regression that removes ONLY the teams-side guard would not be
        caught. This pin closes that coverage gap.

        Mock `_dir_max_child_mtime` at the module level to force a
        sentinel return for the target dir; the teams caller must honor
        it with `skipped += 1`, not false-reap. Team name must pass the
        pattern gate (hex-only) AND differ from current_team_name.
        """
        from unittest.mock import patch as _patch
        import session_end as _se
        from session_end import cleanup_old_teams

        # Hex-shaped stale sibling (will be probed once pattern gate + skip
        # check pass) and a hex-shaped current-team skip value.
        d = tmp_path / "pact-deadbeef"
        d.mkdir()
        (d / "config.json").write_text("{}")
        import os as _os
        import time as _time
        old = _time.time() - (40 * 86400)
        _os.utime(str(d / "config.json"), (old, old))
        _os.utime(str(d), (old, old))

        real_probe = _se._dir_max_child_mtime

        def sentinel_probe(entry, glob="*.json"):
            if str(entry) == str(d):
                return None  # force sentinel for target
            return real_probe(entry, glob=glob)

        with _patch.object(_se, "_dir_max_child_mtime", sentinel_probe):
            reaped, skipped = cleanup_old_teams(
                current_team_name="pact-abcd1234",
                teams_base_dir=str(tmp_path),
                max_age_days=30,
            )

        assert reaped == 0, (
            "teams caller must NOT reap on sentinel. If reaped == 1, the "
            "teams-side `if mtime is None: skipped += 1; continue` guard "
            "(session_end.py:527-529) was removed or short-circuited."
        )
        assert skipped == 1
        assert d.exists()
