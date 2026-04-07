"""
Tests for shared/session_resume.py -- session resume and snapshot management.

Tests cover:
update_session_info():
1. Returns None when CLAUDE_PROJECT_DIR not set
2. Creates project .claude/CLAUDE.md (new default) with template when no file exists
3. Replaces existing session block between markers (both locations)
4. Inserts session block before "## Retrieved Context" when no markers
5. Appends session block at end as fallback
6. Returns error message on exception
7. Created file has 0o600 permissions
8. Created file includes session_dir and plugin_root when provided
9. Dual location support: .claude/CLAUDE.md preferred over legacy ./CLAUDE.md
10. Legacy ./CLAUDE.md is still updated in place when only it exists

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

    def test_creates_file_when_missing(self, tmp_path, monkeypatch):
        """Should create .claude/CLAUDE.md (new default) when no project CLAUDE.md exists."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"
        assert not new_default.exists()
        assert not legacy.exists()

        result = update_session_info("session-123", "pact-session1")

        assert result == "Session info created in new project CLAUDE.md"
        assert new_default.exists()
        # Legacy location should NOT be created when neither exists
        assert not legacy.exists()
        content = new_default.read_text()
        # Header
        assert content.startswith("# Project Memory\n")
        # Auto-creation comment
        assert "PACT auto-creates this file" in content
        assert "SESSION_START/SESSION_END markers" in content
        # Session block written with provided values
        assert "<!-- SESSION_START -->" in content
        assert "<!-- SESSION_END -->" in content
        assert "## Current Session" in content
        assert "session-123" in content
        assert "pact-session1" in content

    def test_created_file_has_secure_permissions(self, tmp_path, monkeypatch):
        """Newly created project CLAUDE.md should have 0o600 permissions."""
        import stat

        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / ".claude" / "CLAUDE.md"

        update_session_info("session-456", "pact-session2")

        assert target.exists()
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"
        # The .claude/ parent should have been created with mode 0o700
        parent_mode = stat.S_IMODE(target.parent.stat().st_mode)
        assert parent_mode == 0o700, f"Expected .claude/ 0o700, got {oct(parent_mode)}"

    def test_created_file_includes_session_dir_and_plugin_root(
        self, tmp_path, monkeypatch
    ):
        """Created file should include optional session_dir and plugin_root lines."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / ".claude" / "CLAUDE.md"

        result = update_session_info(
            "session-789",
            "pact-session3",
            session_dir="/tmp/sessions/abc",
            plugin_root="/opt/plugins/PACT/3.16.1",
        )

        assert result == "Session info created in new project CLAUDE.md"
        content = target.read_text()
        assert "Session dir:" in content
        assert "Plugin root:" in content
        assert "/opt/plugins/PACT/3.16.1" in content

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

    def test_session_dir_not_abbreviated_with_tilde(self, tmp_path, monkeypatch):
        """Session dir must NOT be tilde-abbreviated (R4 regression).

        Parallel to `test_plugin_root_not_abbreviated_with_tilde`. Command
        files read `- Session dir:` via bash single-quoted expansion which
        does NOT perform tilde expansion, and
        `session_journal._validate_cli_session_dir` rejects non-absolute
        paths via `Path(session_dir).is_absolute()` — which returns False
        for `"~/..."`. A tilde-abbreviated value would therefore break
        every journal write from command files when the orchestrator falls
        back to reading CLAUDE.md post-compaction. The fix removes the
        tilde-abbreviation step in `update_session_info` so the absolute
        path is written verbatim, matching `plugin_root`.
        """
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\n")

        home = str(Path.home())
        sd = f"{home}/.claude/pact-sessions/myproject/abc-123"
        update_session_info("sess-sd1", "pact-sd1", session_dir=sd)

        content = target.read_text()
        # The full absolute path must appear, NOT a ~-abbreviated version.
        assert f"- Session dir: `{sd}`" in content
        # Sanity: no `- Session dir: \`~/` line survives the fix.
        assert "- Session dir: `~/" not in content

    def test_plugin_root_omitted_when_none(self, tmp_path, monkeypatch):
        """Plugin root line should be absent when plugin_root is not passed."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Project\n\n## Retrieved Context\n")

        update_session_info("sess-pr3", "pact-pr3")

        content = target.read_text()
        assert "Plugin root:" not in content


class TestUpdateSessionInfoDualLocation:
    """Tests for update_session_info() dual-location CLAUDE.md support.

    Claude Code accepts the project memory file at either:
      - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md   (preferred / new default)
      - $CLAUDE_PROJECT_DIR/CLAUDE.md           (legacy)
    """

    def test_dot_claude_only_writes_in_place(self, tmp_path, monkeypatch):
        """When only .claude/CLAUDE.md exists, update it in place; do NOT create legacy."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        dot_claude_file = dot_claude_dir / "CLAUDE.md"
        dot_claude_file.write_text("# Project\n\n## Retrieved Context\n")
        legacy = tmp_path / "CLAUDE.md"

        result = update_session_info("dc-sess", "pact-dc1")

        assert result == "Session info added to project CLAUDE.md"
        # Edit landed at .claude/CLAUDE.md
        assert "dc-sess" in dot_claude_file.read_text()
        # Legacy was NOT created as a side effect
        assert not legacy.exists()

    def test_legacy_only_writes_in_place(self, tmp_path, monkeypatch):
        """When only ./CLAUDE.md exists, update it in place; do NOT create .claude/."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# Project\n\n## Retrieved Context\n")
        new_default = tmp_path / ".claude" / "CLAUDE.md"

        result = update_session_info("lg-sess", "pact-lg1")

        assert result == "Session info added to project CLAUDE.md"
        # Edit landed at the legacy file
        assert "lg-sess" in legacy.read_text()
        # .claude/CLAUDE.md was NOT created as a side effect
        assert not new_default.exists()
        assert not (tmp_path / ".claude").exists()

    def test_both_exist_prefers_dot_claude(self, tmp_path, monkeypatch):
        """When both files exist, .claude/CLAUDE.md is preferred and legacy is untouched."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        dot_claude_file = dot_claude_dir / "CLAUDE.md"
        dot_claude_file.write_text("# Preferred\n\n## Retrieved Context\n")
        legacy = tmp_path / "CLAUDE.md"
        legacy.write_text("# Legacy untouched\n\n## Retrieved Context\n")

        result = update_session_info("both-sess", "pact-both1")

        assert result == "Session info added to project CLAUDE.md"
        # Preferred file got the edit
        assert "both-sess" in dot_claude_file.read_text()
        # Legacy file was untouched (still has its original content marker)
        legacy_content = legacy.read_text()
        assert "Legacy untouched" in legacy_content
        assert "both-sess" not in legacy_content

    def test_neither_exists_creates_dot_claude_default(self, tmp_path, monkeypatch):
        """When neither file exists, create at the new default .claude/CLAUDE.md."""
        from shared.session_resume import update_session_info

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        new_default = tmp_path / ".claude" / "CLAUDE.md"
        legacy = tmp_path / "CLAUDE.md"

        result = update_session_info("new-sess", "pact-new1")

        assert result == "Session info created in new project CLAUDE.md"
        assert new_default.exists()
        assert not legacy.exists()
        assert "new-sess" in new_default.read_text()


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

    @pytest.mark.parametrize(
        "bad_pr_number",
        [
            0,        # zero is falsy and a meaningless PR number
            -5,       # negative integer
            False,    # bool subclass of int — must be excluded explicitly
            True,     # bool subclass of int — must be excluded explicitly
            "42",     # string would format but is wrong shape
            "",       # empty string
            None,     # historically the only rejected case
            {"x": 1}, # dict
            [1, 2],   # list
        ],
        ids=["zero", "negative", "false", "true", "str", "empty_str",
             "none", "dict", "list"],
    )
    def test_rejects_non_positive_int_pr_number(self, tmp_path, bad_pr_number):
        """LOW: pr_number must be a positive int — bool/0/str/dict/etc rejected.

        Prior bug: ``if pr_number is None`` only filtered None, letting
        0/False/strings/dicts/lists fall through to the ``PR #{x}`` formatter.
        The fix tightens to ``isinstance(pr_number, int) and not bool and > 0``.
        """
        import json
        from shared.session_resume import _check_journal_paused_state

        sd = tmp_path / ".claude" / "pact-sessions" / "test" / "pr-narrowing"
        sd.mkdir(parents=True, exist_ok=True)
        journal = sd / "session-journal.jsonl"

        event = {
            "v": 1,
            "type": "session_paused",
            "pr_number": bad_pr_number,
            "branch": "feat/x",
            "worktree_path": "/tmp/wt",
            "ts": "2026-01-01T00:00:00Z",
        }
        with open(str(journal), "w") as f:
            f.write(json.dumps(event) + "\n")

        # All bad shapes must collapse to None — no formatted output.
        result = _check_journal_paused_state(str(sd))
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
                task_id="truncation-test",
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


# =============================================================================
# _build_journal_resume() Defensive Consumer Tests (BugF1 backstop)
# =============================================================================


class TestBuildJournalResumeDefensive:
    """Defensive consumer tests for _build_journal_resume (BugF1 backstop).

    The per-type schema validator in session_journal._validate_event_schema
    is the primary defense — well-formed writers cannot produce the malformed
    events these tests simulate. The defensive consumer in
    _build_journal_resume is the backstop for:
    - Events from prior schema versions already on disk
    - Hand-crafted journal files (debugging, migration)
    - Events written before per-type validation landed

    These tests write events DIRECTLY to the journal file (bypassing
    append_event) so we can simulate shapes the validator would reject.
    _build_journal_resume MUST NOT raise on any of these shapes — it must
    either drop the bad event or return a partial resume. If the inner
    function raises an unexpected exception, the outer wrapper must catch
    it, log to sys.stderr, and return None (the fail-open contract).
    """

    @pytest.fixture
    def session_dir(self, tmp_path):
        """Concrete on-disk session dir with a journal file we can write to."""
        sd = tmp_path / ".claude" / "pact-sessions" / "test" / "defensive-test"
        sd.mkdir(parents=True, exist_ok=True)
        return str(sd)

    @pytest.fixture
    def journal_file(self, session_dir):
        return Path(session_dir) / "session-journal.jsonl"

    def _write_raw_events(self, journal_file: Path, events: list) -> None:
        """Append raw events to the journal, bypassing append_event's validator."""
        import json
        with open(str(journal_file), "a") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

    def test_phase_transition_missing_phase_does_not_crash(
        self, session_dir, journal_file,
    ):
        """BugF1 primary: phase_transition event missing `phase` does not crash.

        The inner function uses .get("phase") with a walrus filter so events
        missing `phase` are dropped from the summary, not subscripted. This
        test writes a hand-crafted event that the current per-type validator
        would reject, simulating a pre-validator journal entry.
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            # Malformed: phase_transition with no `phase` key at all.
            {"v": 1, "type": "phase_transition", "status": "started",
             "ts": "2026-01-01T00:00:00Z"},
            # Also malformed: phase field present but None.
            {"v": 1, "type": "phase_transition", "phase": None, "status": "completed",
             "ts": "2026-01-01T00:00:01Z"},
            # Valid entry so the resume has something to report.
            {"v": 1, "type": "phase_transition", "phase": "CODE", "status": "started",
             "ts": "2026-01-01T00:00:02Z"},
        ])

        result = _build_journal_resume(session_dir)
        # Must not raise. Result is either None or a partial resume string.
        # The valid CODE event should land in the summary; the malformed
        # events must be silently dropped, not crash.
        if result is not None:
            assert "Last active phase: CODE" in result

    def test_decisions_first_element_is_dict(self, session_dir, journal_file):
        """BugF1 secondary: decisions[0] being a dict does not crash.

        Historical crash site: _build_journal_resume used `decisions[0]`
        assuming a string. Now routed through _coerce_decision_summary which
        stringifies non-string first elements via str(). No IndexError,
        KeyError, or TypeError should escape.
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            {
                "v": 1, "type": "agent_handoff",
                "agent": "coder", "task_id": "1",
                "task_subject": "CODE: dict decision",
                "handoff": {"decisions": [{"reason": "chose X over Y"}]},
                "ts": "2026-01-01T00:00:00Z",
            },
        ])

        # Must not raise.
        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "coder" in result
        # The dict gets stringified into the summary (bounded by truncation).
        assert "CODE: dict decision" in result

    def test_decisions_first_element_is_none(self, session_dir, journal_file):
        """decisions[0] being None produces empty summary, no crash."""
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            {
                "v": 1, "type": "agent_handoff",
                "agent": "coder", "task_id": "1",
                "task_subject": "CODE: none decision",
                "handoff": {"decisions": [None]},
                "ts": "2026-01-01T00:00:00Z",
            },
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        # Subject appears even though the decision summary is empty.
        assert "CODE: none decision" in result

    def test_decisions_not_a_list(self, session_dir, journal_file):
        """decisions field being a non-list value does not crash."""
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            {
                "v": 1, "type": "agent_handoff",
                "agent": "coder", "task_id": "1",
                "task_subject": "CODE: dict decisions field",
                # Historical schema drift: decisions as dict instead of list.
                "handoff": {"decisions": {"not": "a list"}},
                "ts": "2026-01-01T00:00:00Z",
            },
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "CODE: dict decisions field" in result

    def test_handoff_field_not_a_dict(self, session_dir, journal_file):
        """handoff field being a non-dict does not crash.

        _build_journal_resume_inner guards with isinstance(handoff_data, dict)
        before calling .get("decisions") on it. A string/list/None value
        flows through as an empty dict internally.
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            {
                "v": 1, "type": "agent_handoff",
                "agent": "a", "task_id": "1",
                "task_subject": "str handoff", "handoff": "oops string",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "v": 1, "type": "agent_handoff",
                "agent": "b", "task_id": "2",
                "task_subject": "none handoff", "handoff": None,
                "ts": "2026-01-01T00:00:01Z",
            },
            {
                "v": 1, "type": "agent_handoff",
                "agent": "c", "task_id": "3",
                "task_subject": "list handoff", "handoff": ["wrong"],
                "ts": "2026-01-01T00:00:02Z",
            },
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "str handoff" in result
        assert "none handoff" in result
        assert "list handoff" in result

    def test_phase_value_truncated_when_long_string(
        self, session_dir, journal_file,
    ):
        """RA3: a pathologically long phase string is bounded at 80 chars.

        Parallel to the decision-summary truncation: per-type validation
        does not constrain phase string LENGTH, only presence. A writer
        that mistakenly stashes an error message or a long identifier in
        `phase` would otherwise flood the SessionStart hook's
        additionalContext field. The defensive consumer now routes phase
        values through `_coerce_phase_string`, which truncates to 80 chars
        with a "..." tail identical to decision summaries.

        This test writes both a completed and an in-progress phase with a
        200-character identifier and confirms the rendered summary contains
        the 77-character prefix + "..." instead of the full string.
        """
        from shared.session_resume import _build_journal_resume

        long_phase = "P" * 200
        self._write_raw_events(journal_file, [
            {"v": 1, "type": "phase_transition", "phase": long_phase,
             "status": "completed", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "phase_transition", "phase": long_phase,
             "status": "started", "ts": "2026-01-01T00:00:01Z"},
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        # Full 200-char string must NOT appear — would indicate no truncation.
        assert "P" * 200 not in result
        # The 77-char prefix + "..." is the exact truncation shape used by
        # _coerce_phase_string (matches _coerce_decision_summary).
        assert ("P" * 77 + "...") in result
        # Both the completed and in-progress lines should have been rendered
        # through the helper — check both labels are present so we know the
        # truncation wasn't applied to only one of the two code paths.
        assert "Completed phases:" in result
        assert "Last active phase:" in result

    def test_phase_value_handles_non_string_type(
        self, session_dir, journal_file,
    ):
        """RA3/H1: dict/list/number phase values are dropped without crashing.

        The per-type validator rejects new writes where `phase` is not a
        non-empty string, but hand-crafted journal files and events from
        pre-validator sessions can carry a dict, list, or other non-string
        shape. The defensive consumer tightens its filter to require
        `isinstance(phase, str)` so bad-shape events render NOTHING rather
        than as garbled trailers like ``Completed phases: {'nested': 'dict'}``.
        Without the filter, an integer phase would surface as
        ``Last active phase: 42``, which is misleading noise in the
        SessionStart hook context.

        Writes three malformed phase values plus one valid event so the
        resume has something to render, and asserts:
          (a) the function returns without raising,
          (b) the valid event still renders, and
          (c) none of the malformed sentinels appear in the output.
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            {"v": 1, "type": "phase_transition",
             "phase": {"nested": "dict"}, "status": "completed",
             "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "phase_transition",
             "phase": [1, 2, 3], "status": "completed",
             "ts": "2026-01-01T00:00:01Z"},
            {"v": 1, "type": "phase_transition",
             "phase": 42, "status": "started",
             "ts": "2026-01-01T00:00:02Z"},
            # Valid event so the resume does not collapse to None via the
            # `len(lines) <= 2` early-return guard.
            {"v": 1, "type": "phase_transition",
             "phase": "CODE", "status": "started",
             "ts": "2026-01-01T00:00:03Z"},
        ])

        # Must not raise TypeError, ValueError, or any other exception —
        # the defensive consumer's whole point is fail-open rendering.
        result = _build_journal_resume(session_dir)
        assert result is not None

        # Bad-shape sentinels must NOT appear in the output. Their str()
        # forms would have leaked through prior to the H1 filter tightening.
        assert "{'nested': 'dict'}" not in result
        assert "[1, 2, 3]" not in result
        assert "Last active phase: 42" not in result

        # The valid CODE event still renders so we know the filter only
        # drops bad shapes, not the entire phase block.
        assert "Last active phase: CODE" in result

    def test_completed_phase_not_reported_as_active(
        self, session_dir, journal_file,
    ):
        """M5: a phase that started and then completed must NOT be 'active'.

        Prior bug: the in-progress list was populated from any event with
        status `started`, even when a later `completed` event for the same
        phase superseded it. This caused stale phases to surface on the
        ``Last active phase:`` line. The fix tracks the latest event per
        phase name and only marks phases whose terminal event is `started`
        as active.
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            # PREPARE started, then completed — must NOT be active.
            {"v": 1, "type": "phase_transition", "phase": "PREPARE",
             "status": "started", "ts": "2026-01-01T00:00:00Z"},
            {"v": 1, "type": "phase_transition", "phase": "PREPARE",
             "status": "completed", "ts": "2026-01-01T00:00:01Z"},
            # CODE started, no completion yet — should be the active phase.
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "started", "ts": "2026-01-01T00:00:02Z"},
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "Completed phases: PREPARE" in result
        assert "Last active phase: CODE" in result
        assert "Last active phase: PREPARE" not in result

    def test_phase_events_sorted_defensively_by_timestamp(
        self, session_dir, journal_file,
    ):
        """M3: phase events out of chronological order are still ranked correctly.

        Prior bug: the consumer relied on the (currently true but
        undocumented) chronological-order contract of read_events_from.
        The fix sorts phase_transition events by `ts` at the consumer
        site, so a journal where lines were appended in the wrong order
        (e.g. recovered from a crash + replay) still produces the correct
        ``Last active phase`` line.
        """
        from shared.session_resume import _build_journal_resume

        # Write events out of order: the latest CODE start is appended
        # FIRST, the older PREPARE start is appended LAST. Without the
        # defensive sort, "Last active phase" would surface PREPARE.
        self._write_raw_events(journal_file, [
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "started", "ts": "2026-01-01T00:00:05Z"},
            {"v": 1, "type": "phase_transition", "phase": "PREPARE",
             "status": "started", "ts": "2026-01-01T00:00:01Z"},
            {"v": 1, "type": "phase_transition", "phase": "PREPARE",
             "status": "completed", "ts": "2026-01-01T00:00:02Z"},
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        assert "Last active phase: CODE" in result

    def test_active_phase_uses_latest_ts(
        self, session_dir, journal_file,
    ):
        """R1: the "Last active phase" line must reflect the most recent ts,
        not dict insertion order.

        Prior regression: when two phases were both currently `started`, the
        renderer picked `active[-1]` from a dict-iteration-order list. Dict
        iteration follows FIRST-insertion order (reassigning an existing key
        does not move it to the tail in Python 3.7+), so the last-seen entry
        in insertion order is NOT necessarily the phase with the greatest
        `ts`. The fix selects via `max(ts)` across still-started entries.

        This test gives CODE the LATER ts but PREPARE the later first-seen
        position in the dict, so the pre-fix code would pick PREPARE (the
        last insertion-order entry) while the fix correctly picks CODE.

        After defensive sort-by-ts, events are processed in ts order:
          1. CODE @ ts=05  -> latest_per_phase[CODE] inserted first
          2. PREPARE @ ts=10 -> latest_per_phase[PREPARE] inserted second
        Dict order: [CODE, PREPARE]. Old code: active[-1] = PREPARE. Max ts
        selector: CODE @ ts=10 is NOT the latest — PREPARE @ ts=10 wins.
        ...so we need CODE's ts > PREPARE's ts but PREPARE inserted later.

        The only way to achieve that (insertion later but ts smaller) is to
        have CODE with a larger ts but a SMALLER ts in its FIRST-seen event
        than PREPARE's first-seen event. Concretely: CODE seen first with a
        ts that is LATER than PREPARE's first-seen ts is impossible when
        events are sorted by ts. So we must disable the sort effect — write
        a completed CODE older than PREPARE's start, then a started CODE
        with the greatest ts. That way CODE is first inserted (by completed
        event), PREPARE second, then CODE re-inserted-into-same-slot by the
        started event. Dict order stays [CODE, PREPARE]; still-started set
        is {CODE (ts=20), PREPARE (ts=10)}; active[-1] = PREPARE (wrong),
        max-ts pick = CODE (correct).
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            # 1) CODE completed at an early ts — inserts CODE first into
            #    latest_per_phase with status="completed".
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "completed", "ts": "2026-01-01T00:00:00Z"},
            # 2) PREPARE started — inserts PREPARE second into
            #    latest_per_phase. Dict insertion order: [CODE, PREPARE].
            {"v": 1, "type": "phase_transition", "phase": "PREPARE",
             "status": "started", "ts": "2026-01-01T00:00:10Z"},
            # 3) CODE re-started with the greatest ts — reassigns the CODE
            #    slot to ("...:20Z", "started") WITHOUT moving it to the
            #    tail of the dict. Dict order remains [CODE, PREPARE].
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "started", "ts": "2026-01-01T00:00:20Z"},
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        # CODE has the greatest ts among still-started phases — must win.
        assert "Last active phase: CODE" in result
        # PREPARE was the tail of dict insertion order; the pre-fix
        # active[-1] code would have picked it. The fix must NOT.
        assert "Last active phase: PREPARE" not in result

    def test_tie_break_uses_later_seen_status(
        self, session_dir, journal_file,
    ):
        """R2: when two events for the same phase share identical ts,
        the later-seen event must win the `latest_per_phase` slot.

        Prior regression: the comparator was strict `>`, so a second event
        with the same ts was dropped. Concretely, a `started` + `completed`
        pair written with the same timestamp would leave the phase marked
        as `started` (first-seen wins), and the phase would surface on the
        "Last active phase:" line instead of "Completed phases:". The fix
        changes the comparator to `>=` so the later-seen status replaces
        the earlier one on ties.

        Pair: CODE `started` at ts=05, then CODE `completed` at ts=05.
        Expected: CODE on "Completed phases", NOT on "Last active phase".

        NOTE: the defensive ts sort is stable, so input order is preserved
        when ts values are equal. We explicitly append the `started` event
        first so that the `completed` event is the second (later-seen)
        record in the iteration — that is the one the fix must respect.
        """
        from shared.session_resume import _build_journal_resume

        self._write_raw_events(journal_file, [
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "started", "ts": "2026-01-01T00:00:05Z"},
            {"v": 1, "type": "phase_transition", "phase": "CODE",
             "status": "completed", "ts": "2026-01-01T00:00:05Z"},
        ])

        result = _build_journal_resume(session_dir)
        assert result is not None
        # The later-seen `completed` must supersede the earlier-seen
        # `started` on the identical-ts tie.
        assert "Completed phases: CODE" in result
        assert "Last active phase: CODE" not in result

    def test_outer_wrapper_catches_unexpected_exception(
        self, session_dir, journal_file, capsys, monkeypatch,
    ):
        """Outer _build_journal_resume wrapper catches ANY unexpected exception.

        Critical test: this is the ONLY test that triggers the
        `except Exception: ... print(..., file=sys.stderr)` path. Without the
        `import sys` statement at the top of session_resume.py, this test
        fails with NameError instead of the expected fail-open contract
        (return None + stderr log). Any regression that drops `import sys`
        will be caught here.
        """
        import shared.session_resume as session_resume_module
        from shared.session_resume import _build_journal_resume

        # Ensure the journal exists so _build_journal_resume_inner gets past
        # the early `if not all_events: return None` path and actually
        # executes code that the patched function can replace.
        self._write_raw_events(journal_file, [
            {"v": 1, "type": "checkpoint", "phase": "CODE",
             "ts": "2026-01-01T00:00:00Z"},
        ])

        def _boom(_session_dir: str):
            del _session_dir  # mock signature match; argument intentionally unused
            raise RuntimeError("simulated unexpected shape")

        monkeypatch.setattr(
            session_resume_module, "_build_journal_resume_inner", _boom,
        )

        result = _build_journal_resume(session_dir)

        # Fail-open contract: return None.
        assert result is None

        # The wrapper logged to stderr. If `import sys` is missing,
        # execution never reaches this assertion — the wrapper itself
        # raises NameError on `sys.stderr` and the test fails with
        # NameError not AssertionError. This is the regression detector
        # for the missing-import bug.
        captured = capsys.readouterr()
        assert "_build_journal_resume failed" in captured.err
        assert "simulated unexpected shape" in captured.err

    def test_outer_wrapper_none_when_inner_returns_none(
        self, session_dir, journal_file,
    ):
        """Wrapper passes through a clean None when inner returns None.

        Baseline: empty journal path returns None without triggering the
        except clause. This complements the boom-test above by confirming
        the non-exception path still works.
        """
        from shared.session_resume import _build_journal_resume

        # Journal file does not exist at all — read_events_from returns [],
        # inner returns None, wrapper passes it through without logging.
        assert not journal_file.exists()
        result = _build_journal_resume(session_dir)
        assert result is None
