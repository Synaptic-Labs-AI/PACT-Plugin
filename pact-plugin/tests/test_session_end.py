# pact-plugin/tests/test_session_end.py
"""
Tests for session_end.py — SessionEnd hook that writes last-session snapshots.

session_end.py is purely observational — no destructive operations.

Tests cover:
1. Writes structured markdown snapshot
2. Creates sessions directory if missing
3. Includes completed task summaries with handoff decisions
4. Includes incomplete tasks with status
5. Handles empty task list gracefully
6. Handles None task list gracefully
7. main() entry point: exit codes and error handling

check_unpaused_pr() — safety-net for unpaused PRs:
8. Detects PR number in task metadata → appends warning to snapshot
9. Detects PR URL in handoff values → appends warning
10. No warning when paused-state.json exists (consolidation already done)
11. No warning when no PR detected in tasks
12. No warning when tasks is None
13. No warning when project_slug is empty
14. No warning when snapshot file missing
15. Skips warning when tasks is empty list
16. Handles malformed handoff PR URL gracefully
17. Best-effort: no crash on IOError during append
18. main() calls check_unpaused_pr after write_session_snapshot
19. main() call ordering: write_session_snapshot -> check_unpaused_pr
20. Non-string handoff values (dict/list) are skipped without error
21. Full github.com PR URL is detected by regex
22. Non-URL "/pull/" text is NOT detected by regex
23. metadata: None in task dict does not crash check_unpaused_pr (or {} guard)

write_session_snapshot() — metadata None guard:
24. metadata: None in task dict does not crash write_session_snapshot

File permission hardening:
25. write_session_snapshot creates directory with 0o700
26. write_session_snapshot creates file with 0o600
27. check_unpaused_pr re-applies 0o600 after appending warning
"""
import io
import json
import sys
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

    def test_handles_metadata_none(self, tmp_path):
        """Task with 'metadata': None should not crash (or {} guard handles it)."""
        from session_end import write_session_snapshot

        tasks = [
            {
                "id": "20",
                "subject": "CODE: implement feature",
                "status": "completed",
                "metadata": None,
            },
            {
                "id": "21",
                "subject": "TEST: write tests",
                "status": "in_progress",
                "metadata": None,
            },
        ]

        write_session_snapshot(
            tasks=tasks,
            project_slug="none-meta-proj",
            sessions_dir=str(tmp_path),
        )

        content = (tmp_path / "none-meta-proj" / "last-session.md").read_text()
        assert "## Completed Tasks" in content
        assert "#20 CODE: implement feature" in content
        assert "## Incomplete Tasks" in content
        assert "#21 TEST: write tests -- in_progress" in content


class TestMainEntryPoint:
    """Tests for session_end.main() exit behavior."""

    def test_main_exits_0_on_success(self):
        from session_end import main

        env = {
            "CLAUDE_PROJECT_DIR": "/Users/mj/project",
        }

        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO("{}")), \
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
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_env_vars(self):
        from session_end import main

        with patch.dict("os.environ", {}, clear=True), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list", return_value=None), \
             patch("session_end.write_session_snapshot"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_calls_write_snapshot_with_tasks(self):
        from session_end import main

        mock_tasks = [{"id": "1", "subject": "test", "status": "completed", "metadata": {}}]

        with patch("session_end.pact_context.init"), \
             patch("session_end.get_project_dir", return_value="/Users/mj/Sites/my-project"), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list", return_value=mock_tasks), \
             patch("session_end.write_session_snapshot") as mock_snapshot:
            with pytest.raises(SystemExit):
                main()

        mock_snapshot.assert_called_once()
        call_args = mock_snapshot.call_args
        assert call_args.kwargs["tasks"] == mock_tasks
        assert call_args.kwargs["project_slug"] == "my-project"

    def test_main_call_ordering(self):
        """main() must call functions in correct order:
        write_session_snapshot -> check_unpaused_pr -> cleanup_teachback_markers
        -> cleanup_old_sessions.

        Ordering is critical: snapshot creates the file, check_unpaused_pr
        appends to it. Cleanup runs last.
        """
        from session_end import main

        call_order = []

        env = {"CLAUDE_PROJECT_DIR": "/Users/mj/Sites/my-project"}

        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.write_session_snapshot",
                   side_effect=lambda **kw: call_order.append("write_session_snapshot")), \
             patch("session_end.check_unpaused_pr",
                   side_effect=lambda **kw: call_order.append("check_unpaused_pr")), \
             patch("session_end.cleanup_teachback_markers",
                   side_effect=lambda **kw: call_order.append("cleanup_teachback_markers")), \
             patch("session_end.cleanup_old_sessions",
                   side_effect=lambda **kw: call_order.append("cleanup_old_sessions")):
            with pytest.raises(SystemExit):
                main()

        assert call_order == [
            "write_session_snapshot",
            "check_unpaused_pr",
            "cleanup_teachback_markers",
            "cleanup_old_sessions",
        ]


# =============================================================================
# check_unpaused_pr() Tests
# =============================================================================

class TestCheckUnpausedPr:
    """Tests for session_end.check_unpaused_pr() — safety-net for unpaused PRs.

    Detects open PRs that were NOT paused (no memory consolidation), appending
    a warning to the last-session.md snapshot for next-session pickup.
    """

    def _setup_snapshot(self, sessions_dir, project_slug, content="# Last Session\n"):
        """Helper: create a last-session.md file."""
        proj_dir = sessions_dir / project_slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        snapshot = proj_dir / "last-session.md"
        snapshot.write_text(content, encoding="utf-8")
        return snapshot

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

    def test_detects_pr_number_in_task_metadata(self, tmp_path):
        """Should append warning when pr_number found in task metadata."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        tasks = [self._make_task_with_pr_number(288)]

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        assert "## Pause-Mode Warning" in content
        assert "PR #288" in content
        assert "pause-mode was not run" in content

    def test_detects_pr_url_in_handoff_values(self, tmp_path):
        """Should extract PR number from /pull/ URL in handoff metadata."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        tasks = [self._make_task_with_pr_url("https://github.com/owner/repo/pull/42")]

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        assert "## Pause-Mode Warning" in content
        assert "PR #42" in content

    def test_no_warning_when_paused_state_exists(self, tmp_path):
        """Should skip warning when paused-state.json exists (already consolidated)."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        # Write a paused-state.json
        import json
        paused = tmp_path / "proj" / "paused-state.json"
        paused.write_text(json.dumps({"pr_number": 288}), encoding="utf-8")

        tasks = [self._make_task_with_pr_number(288)]

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        assert "## Pause-Mode Warning" not in content

    def test_no_warning_when_no_pr_detected(self, tmp_path):
        """Should not append warning when no PR found in task metadata."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        tasks = [
            {"id": "1", "subject": "CODE: auth", "status": "completed", "metadata": {}},
        ]

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        assert "## Pause-Mode Warning" not in content

    def test_no_warning_when_tasks_is_none(self, tmp_path):
        """Should return early when tasks is None."""
        from session_end import check_unpaused_pr

        self._setup_snapshot(tmp_path, "proj")

        # Should not raise
        check_unpaused_pr(
            tasks=None,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

    def test_no_warning_when_project_slug_empty(self, tmp_path):
        """Should return early when project_slug is empty."""
        from session_end import check_unpaused_pr

        tasks = [self._make_task_with_pr_number(100)]

        # Should not raise
        check_unpaused_pr(
            tasks=tasks,
            project_slug="",
            sessions_dir=str(tmp_path),
        )

    def test_no_warning_when_snapshot_file_missing(self, tmp_path):
        """Should not crash when last-session.md doesn't exist."""
        from session_end import check_unpaused_pr

        # Create project dir but NOT the snapshot file
        (tmp_path / "proj").mkdir()
        tasks = [self._make_task_with_pr_number(99)]

        # Should not raise
        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

    def test_no_warning_when_tasks_empty(self, tmp_path):
        """Should return early for empty task list."""
        from session_end import check_unpaused_pr

        self._setup_snapshot(tmp_path, "proj")

        check_unpaused_pr(
            tasks=[],
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

    def test_handles_malformed_pr_url(self, tmp_path):
        """Should handle handoff values with /pull/ but no valid number."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
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

        # Should not crash; may or may not detect depending on parsing
        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

    def test_best_effort_no_crash_on_ioerror(self, tmp_path):
        """Should not crash when appending to snapshot raises IOError."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        tasks = [self._make_task_with_pr_number(288)]

        # Make the snapshot read-only to trigger IOError on write
        import os
        os.chmod(str(snapshot), 0o444)

        # Should not raise
        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        # Restore permissions for cleanup
        os.chmod(str(snapshot), 0o644)

    def test_main_calls_check_unpaused_pr(self):
        """main() should call check_unpaused_pr after write_session_snapshot."""
        from session_end import main

        with patch("session_end.pact_context.init"), \
             patch("session_end.get_project_dir", return_value="/Users/mj/Sites/my-project"), \
             patch("sys.stdin", io.StringIO("{}")), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.write_session_snapshot"), \
             patch("session_end.check_unpaused_pr") as mock_check:
            with pytest.raises(SystemExit):
                main()

        mock_check.assert_called_once()
        call_args = mock_check.call_args
        assert call_args.kwargs["tasks"] == []
        assert call_args.kwargs["project_slug"] == "my-project"

    def test_pr_number_metadata_takes_priority_over_url(self, tmp_path):
        """When both pr_number and URL exist, pr_number in metadata should be used."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
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

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        assert "PR #100" in content

    def test_non_string_handoff_values_skipped(self, tmp_path):
        """Non-string handoff values (dict/list) should be skipped without error."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        tasks = [
            {
                "id": "1",
                "subject": "CODE: feature",
                "status": "completed",
                "metadata": {
                    "pr_number": 42,
                    "handoff": {
                        "produced": ["src/auth.py"],  # list value
                        "decisions": {"key": "value"},  # dict value
                        "integration": 12345,  # int value
                        "notes": None,  # None value
                    },
                },
            }
        ]

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        # Should detect PR via pr_number (primary path) despite non-string handoff values
        assert "## Pause-Mode Warning" in content
        assert "PR #42" in content

    def test_detects_full_github_pr_url(self, tmp_path):
        """Should detect PR from full github.com/org/repo/pull/N URL."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
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

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        assert "## Pause-Mode Warning" in content
        assert "PR #123" in content

    def test_non_url_pull_text_not_detected(self, tmp_path):
        """Non-URL text containing '/pull/' should NOT be detected after regex change."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
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

        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        # After regex change (#11), bare "/pull/" without github.com URL should NOT match
        assert "## Pause-Mode Warning" not in content

    def test_handles_metadata_none_in_task(self, tmp_path):
        """Task with 'metadata': None should not crash (or {} guard handles it)."""
        from session_end import check_unpaused_pr

        snapshot = self._setup_snapshot(tmp_path, "proj")
        tasks = [
            {
                "id": "1",
                "subject": "CODE: feature",
                "status": "completed",
                "metadata": None,
            },
        ]

        # Should not raise
        check_unpaused_pr(
            tasks=tasks,
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        content = snapshot.read_text()
        # No PR detected, so no warning
        assert "## Pause-Mode Warning" not in content


# =============================================================================
# File Permission Tests for session_end.py
# =============================================================================

class TestSessionEndFilePermissions:
    """Tests for file permission hardening in session_end.py.

    Verifies that:
    - write_session_snapshot creates directory with 0o700
    - write_session_snapshot creates file with 0o600
    - check_unpaused_pr re-applies 0o600 after appending to snapshot
    """

    def test_write_snapshot_creates_directory_with_700(self, tmp_path):
        """write_session_snapshot() should create project dir with mode 0o700."""
        import stat
        from session_end import write_session_snapshot

        write_session_snapshot(
            tasks=[],
            project_slug="perm-proj",
            sessions_dir=str(tmp_path),
        )

        proj_dir = tmp_path / "perm-proj"
        dir_mode = stat.S_IMODE(proj_dir.stat().st_mode)
        assert dir_mode == 0o700, (
            f"Directory should have mode 0o700, got {oct(dir_mode)}"
        )

    def test_write_snapshot_creates_file_with_600(self, tmp_path):
        """write_session_snapshot() should set snapshot file to mode 0o600."""
        import stat
        from session_end import write_session_snapshot

        write_session_snapshot(
            tasks=[],
            project_slug="perm-proj",
            sessions_dir=str(tmp_path),
        )

        snapshot_file = tmp_path / "perm-proj" / "last-session.md"
        file_mode = stat.S_IMODE(snapshot_file.stat().st_mode)
        assert file_mode == 0o600, (
            f"Snapshot file should have mode 0o600, got {oct(file_mode)}"
        )

    def test_check_unpaused_pr_reapplies_600_after_append(self, tmp_path):
        """check_unpaused_pr() should re-apply 0o600 after appending warning."""
        import stat
        from session_end import check_unpaused_pr

        # Set up snapshot with known permissions
        proj_dir = tmp_path / "perm-proj"
        proj_dir.mkdir(parents=True)
        snapshot = proj_dir / "last-session.md"
        snapshot.write_text("# Last Session\n", encoding="utf-8")

        tasks = [
            {
                "id": "1",
                "subject": "Review: feature",
                "status": "completed",
                "metadata": {"pr_number": 288},
            },
        ]

        check_unpaused_pr(
            tasks=tasks,
            project_slug="perm-proj",
            sessions_dir=str(tmp_path),
        )

        # Verify warning was appended
        content = snapshot.read_text()
        assert "## Pause-Mode Warning" in content

        # Verify permissions re-applied
        file_mode = stat.S_IMODE(snapshot.stat().st_mode)
        assert file_mode == 0o600, (
            f"Snapshot file should have mode 0o600 after append, got {oct(file_mode)}"
        )


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
