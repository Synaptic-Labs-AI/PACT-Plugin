"""
Tests for shared/session_resume.py -- session resume and snapshot management.

Tests cover:
update_session_info():
1. Returns None when CLAUDE_PROJECT_DIR not set
2. Returns None when project CLAUDE.md doesn't exist
3. Replaces existing session block between markers
4. Inserts session block before "## Retrieved Context" when no markers
5. Appends session block at end as fallback
6. Returns error message on exception

restore_last_session():
7. Returns None when no prev_session_dir

check_resumption_context():
8. Returns None when no in_progress or pending tasks
9. Returns feature task names
10. Returns phase names
11. Returns agent count
12. Returns blocker count with bold formatting
13. Mixed task types
14. metadata: None in task dict does not crash (or {} guard)

check_paused_state():
15. Returns None when no prev_session_dir

_check_pr_state() -- direct tests:
16. Returns "OPEN" for open PRs
17. Returns "MERGED" for merged PRs
18. Returns "CLOSED" for closed PRs
19. Uppercases lowercase state
20. Returns "" on FileNotFoundError (gh not installed)
21. Returns "" on TimeoutExpired
22. Returns "" on OSError
23. Returns "" on non-zero exit code
24. Accepts string PR number

_build_journal_resume() -- truncation boundary:
25-28. Parameterized: decision length 79 (no truncation), 80 (boundary, no truncation),
       81 (truncated to 77+"..."), 120 (well over, truncated)
"""

import sys
from pathlib import Path

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestUpdateSessionInfo:
    """Tests for update_session_info() -- session info in project CLAUDE.md."""

    def test_returns_none_when_no_project_dir(self, monkeypatch):
        """Should return None when CLAUDE_PROJECT_DIR not set."""
        from shared.session_resume import update_session_info

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        result = update_session_info("session-123", "pact-session1")

        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        """Should return None when project CLAUDE.md doesn't exist."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = update_session_info("session-123", "pact-session1")

        assert result is None

    def test_replaces_existing_session_block(self, tmp_path, monkeypatch):
        """Should replace content between session markers."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text(
            "# Project\n\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\nOld session info\n"
            "<!-- SESSION_END -->\n\n"
            "## Other Section\n"
        )

        result = update_session_info("new-session-id", "pact-newsess")

        assert result == "Session info updated in project CLAUDE.md"
        content = target.read_text()
        assert "new-session-id" in content
        assert "pact-newsess" in content
        assert "Old session info" not in content
        assert "## Other Section" in content

    def test_inserts_before_retrieved_context(self, tmp_path, monkeypatch):
        """Should insert session block before '## Retrieved Context' when no markers."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\nSome context\n")

        result = update_session_info("sess-abc", "pact-sessabc")

        assert result == "Session info added to project CLAUDE.md"
        content = target.read_text()
        assert "sess-abc" in content
        assert "pact-sessabc" in content
        # Session block should come before Retrieved Context
        session_pos = content.index("<!-- SESSION_START -->")
        context_pos = content.index("## Retrieved Context")
        assert session_pos < context_pos

    def test_appends_at_end_as_fallback(self, tmp_path, monkeypatch):
        """Should append session block when no markers or Retrieved Context."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\nSome content\n")

        result = update_session_info("sess-xyz", "pact-sessxyz")

        assert result == "Session info added to project CLAUDE.md"
        content = target.read_text()
        assert "sess-xyz" in content
        assert "<!-- SESSION_START -->" in content

    def test_session_dir_line_roundtrips_with_extract(self, tmp_path, monkeypatch):
        """Session dir written by update_session_info can be parsed back by _extract_prev_session_dir."""
        from shared.session_resume import update_session_info
        from session_init import _extract_prev_session_dir

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\n")

        session_dir = str(
            Path.home() / ".claude" / "pact-sessions" / "myproject" / "abc-123"
        )
        result = update_session_info("abc-123", "pact-abc123", session_dir)
        assert result is not None

        # Verify Session dir line is present
        content = target.read_text()
        assert "Session dir:" in content

        # Roundtrip: _extract_prev_session_dir should recover the same path
        extracted = _extract_prev_session_dir(str(tmp_path))
        assert extracted == session_dir

    def test_plugin_root_written_when_provided(self, tmp_path, monkeypatch):
        """Plugin root line should appear in CLAUDE.md when plugin_root is passed."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\n")

        result = update_session_info(
            "sess-pr1", "pact-pr1", plugin_root="/Users/me/.claude/plugins/cache/PACT/1.0"
        )
        assert result is not None

        content = target.read_text()
        assert "- Plugin root: `/Users/me/.claude/plugins/cache/PACT/1.0`" in content

    def test_plugin_root_not_abbreviated_with_tilde(self, tmp_path, monkeypatch):
        """Plugin root must NOT be tilde-abbreviated (Bash needs the literal path)."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\n")

        home = str(Path.home())
        pr = f"{home}/.claude/plugins/cache/PACT/2.0"
        update_session_info("sess-pr2", "pact-pr2", plugin_root=pr)

        content = target.read_text()
        # The full absolute path must appear, NOT a ~-abbreviated version
        assert f"- Plugin root: `{pr}`" in content
        assert "- Plugin root: `~/" not in content

    def test_plugin_root_omitted_when_none(self, tmp_path, monkeypatch):
        """Plugin root line should be absent when plugin_root is not passed."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\n")

        update_session_info("sess-pr3", "pact-pr3")

        content = target.read_text()
        assert "Plugin root:" not in content


class TestRestoreLastSession:
    """Tests for restore_last_session() -- journal-only path."""

    def test_returns_none_when_no_team_name(self):
        """Should return None when prev_session_dir is None."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(prev_session_dir=None)
        assert result is None

    def test_returns_none_when_empty_team_name(self):
        """Should return None when prev_session_dir is empty string."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(prev_session_dir="")
        assert result is None


class TestCheckResumptionContext:
    """Tests for check_resumption_context() -- resumption detection."""

    def test_returns_none_when_no_active_tasks(self):
        """Should return None when all tasks are completed."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "1", "subject": "auth feature", "status": "completed", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is None

    def test_returns_none_when_empty_list(self):
        """Should return None for empty task list."""
        from shared.session_resume import check_resumption_context

        result = check_resumption_context([])

        assert result is None

    def test_returns_feature_task_names(self):
        """Should include feature task names in resumption context."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "1", "subject": "Implement auth system", "status": "in_progress", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Features:" in result
        assert "Implement auth system" in result

    def test_returns_phase_names(self):
        """Should include phase names in resumption context."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "2", "subject": "ARCHITECT: design", "status": "in_progress", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Phases:" in result
        assert "ARCHITECT" in result

    def test_returns_agent_count(self):
        """Should include count of active agents."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "3", "subject": "pact-backend-coder", "status": "in_progress", "metadata": {}},
            {"id": "4", "subject": "pact-frontend-coder", "status": "in_progress", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Active agents: 2" in result

    def test_returns_blocker_count(self):
        """Should include blocker count with bold formatting."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {
                "id": "5",
                "subject": "BLOCKER: missing API key",
                "status": "in_progress",
                "metadata": {"type": "blocker"},
            },
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "**Blockers: 1**" in result

    def test_mixed_task_types(self):
        """Should handle mix of feature, phase, agent, and blocker tasks."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {"id": "1", "subject": "Implement auth", "status": "in_progress", "metadata": {}},
            {"id": "2", "subject": "CODE: backend", "status": "in_progress", "metadata": {}},
            {"id": "3", "subject": "pact-backend-coder", "status": "in_progress", "metadata": {}},
            {
                "id": "4",
                "subject": "BLOCKER: missing key",
                "status": "in_progress",
                "metadata": {"type": "blocker"},
            },
            {"id": "5", "subject": "TEST: write tests", "status": "pending", "metadata": {}},
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        assert "Features:" in result
        assert "Phases:" in result
        assert "Active agents: 1" in result
        assert "**Blockers: 1**" in result
        assert "(1 pending)" in result

    def test_handles_metadata_none(self):
        """Task with 'metadata': None should not crash (or {} guard handles it)."""
        from shared.session_resume import check_resumption_context

        tasks = [
            {
                "id": "1",
                "subject": "BLOCKER: missing API key",
                "status": "in_progress",
                "metadata": None,
            },
        ]

        result = check_resumption_context(tasks)

        assert result is not None
        # With metadata=None, or {} guard prevents crash.
        # The task is in_progress but won't be classified as a blocker
        # (metadata.get("type") requires a dict, and or {} provides one).
        assert "Features:" in result


class TestUpdateSessionInfoErrorPaths:
    """Tests for update_session_info() exception handling."""

    def test_returns_error_message_on_exception(self, tmp_path, monkeypatch):
        """Should return truncated error message when file operations fail."""
        from shared.session_resume import update_session_info
        from unittest.mock import patch as mock_patch

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n")

        with mock_patch.object(Path, "read_text", side_effect=IOError("disk error")):
            result = update_session_info("sess-123", "pact-sess123")

        assert result is not None
        assert "Session info failed:" in result


class TestCheckPausedState:
    """Tests for check_paused_state() -- journal-only path."""

    def test_returns_none_when_no_team_name(self):
        """Should return None when prev_session_dir is None."""
        from shared.session_resume import check_paused_state

        result = check_paused_state(prev_session_dir=None)
        assert result is None

    def test_returns_none_when_empty_team_name(self):
        """Should return None when prev_session_dir is empty string."""
        from shared.session_resume import check_paused_state

        result = check_paused_state(prev_session_dir="")
        assert result is None


# ---------------------------------------------------------------------------
# _check_pr_state() -- direct tests
# ---------------------------------------------------------------------------


class TestCheckPrState:
    """Direct tests for _check_pr_state() -- gh CLI wrapper.

    This function is always mocked in the paused_state tests. These tests
    verify the function itself: subprocess call, return value normalization,
    and fail-open error handling.
    """

    def test_returns_open_for_open_pr(self):
        """Returns 'OPEN' when gh pr view reports OPEN."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = _check_pr_state(42)

        assert result == "OPEN"

    def test_returns_merged_for_merged_pr(self):
        """Returns 'MERGED' when gh pr view reports MERGED."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_pr_state

        mock_result = MagicMock(returncode=0, stdout="MERGED\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = _check_pr_state(77)

        assert result == "MERGED"

    def test_returns_closed_for_closed_pr(self):
        """Returns 'CLOSED' when gh pr view reports CLOSED."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_pr_state

        mock_result = MagicMock(returncode=0, stdout="CLOSED\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = _check_pr_state(99)

        assert result == "CLOSED"

    def test_uppercases_lowercase_state(self):
        """Normalizes lowercase state to uppercase."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_pr_state

        mock_result = MagicMock(returncode=0, stdout="open\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = _check_pr_state(42)

        assert result == "OPEN"

    def test_returns_empty_on_file_not_found(self):
        """Returns '' when gh is not installed (FileNotFoundError)."""
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_pr_state

        with mock_patch(
            "shared.session_resume.subprocess.run",
            side_effect=FileNotFoundError("gh not found"),
        ):
            result = _check_pr_state(42)

        assert result == ""

    def test_returns_empty_on_timeout(self):
        """Returns '' when gh times out."""
        import subprocess as sp
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_pr_state

        with mock_patch(
            "shared.session_resume.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="gh", timeout=5),
        ):
            result = _check_pr_state(42)

        assert result == ""

    def test_returns_empty_on_oserror(self):
        """Returns '' on OSError (e.g., permission denied)."""
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_pr_state

        with mock_patch(
            "shared.session_resume.subprocess.run",
            side_effect=OSError("permission denied"),
        ):
            result = _check_pr_state(42)

        assert result == ""

    def test_returns_empty_on_nonzero_exit(self):
        """Returns '' when gh exits with non-zero code."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_pr_state

        mock_result = MagicMock(returncode=1, stdout="")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = _check_pr_state(42)

        assert result == ""

    def test_accepts_string_pr_number(self):
        """Accepts string PR number (converted to str in subprocess call)."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_pr_state

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with mock_patch(
            "shared.session_resume.subprocess.run", return_value=mock_result
        ) as mock_sub:
            result = _check_pr_state("42")

        assert result == "OPEN"
        # Verify str(pr_number) is used in the command
        call_args = mock_sub.call_args[0][0]
        assert "42" in call_args



# =============================================================================
# _build_journal_resume() Truncation Boundary Tests
# =============================================================================


class TestBuildJournalResumeTruncation:
    """Tests for decision string truncation boundary in _build_journal_resume()."""

    @pytest.fixture
    def session_dir(self, tmp_path, monkeypatch):
        """Set up session dir and patch _get_session_dir for implicit API."""
        import shared.session_journal as sj
        sd = str(tmp_path / ".claude" / "pact-sessions" / "test" / "truncation-test")
        monkeypatch.setattr(sj, "_get_session_dir", lambda: sd)
        return sd

    def _write_handoff(self, decision: str) -> None:
        """Write a single agent_handoff event with one decision string."""
        from shared.session_journal import append_event, make_event

        append_event(
            make_event(
                "agent_handoff",
                agent="coder",
                task_subject="CODE: boundary",
                handoff={"decisions": [decision]},
            ),
        )

    @pytest.mark.parametrize(
        "length, should_truncate",
        [
            (79, False),   # Under boundary -- no truncation
            (80, False),   # At boundary -- no truncation (> 80 triggers)
            (81, True),    # Over boundary -- truncated to 77+"..."
            (120, True),   # Well over boundary
        ],
        ids=["79_under", "80_at_boundary", "81_over", "120_well_over"],
    )
    def test_decision_truncation_boundary(
        self, session_dir, length, should_truncate
    ):
        """Decision strings are truncated only when len > 80."""
        from shared.session_resume import _build_journal_resume

        decision = "D" * length
        self._write_handoff(decision)

        result = _build_journal_resume(session_dir)
        assert result is not None

        if should_truncate:
            assert "D" * 77 + "..." in result
            assert "D" * length not in result
        else:
            assert "D" * length in result
