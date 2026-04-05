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
7. Returns None when no snapshot file exists
8. Returns content with header if file exists
9. Rotates file to last-session.prev.md
10. Returns None when project_slug is empty
11. Returns None when snapshot file is empty
12. Returns None on IOError during read
13. Continues on rotation failure (best-effort)

check_resumption_context():
14. Returns None when no in_progress or pending tasks
15. Returns feature task names
16. Returns phase names
17. Returns agent count
18. Returns blocker count with bold formatting
19. Mixed task types
20a. metadata: None in task dict does not crash (or {} guard)

check_paused_state():
20. Returns formatted context string when paused-state.json exists
21. Returns None when no paused-state.json
22. Returns None when project_slug is empty
23. Includes PR number, branch, and worktree path in output
24. Adds consolidation guidance when consolidation_completed is false
25. No guidance note when consolidation_completed is true
26. Returns None on corrupt/invalid JSON (fail-open)
27. Returns None when pr_number field is missing
28. Returns None on empty file
29. Returns None on IOError during read (fail-open)
30. Handles missing optional fields with defaults
31. Returns None on UnicodeDecodeError via non-UTF-8 bytes (fail-open)

check_paused_state() -- active PR validation (parameterized):
32-36. Parameterized: MERGED (cleanup), CLOSED (cleanup), OPEN (fall-through),
       timeout (fall-through), non-zero exit (fall-through)
37. Asserts exact subprocess arguments including timeout=5
38. Returns merged message even when state_file.unlink() raises OSError

check_paused_state() -- TTL cleanup:
39. Cleans up paused state older than 14 days
40. Does NOT clean up recent paused state
41. Skips TTL check when paused_at is missing
42. Skips TTL check when paused_at is unparseable

_check_pr_state() -- direct tests:
43. Returns "OPEN" for open PRs
44. Returns "MERGED" for merged PRs
45. Returns "CLOSED" for closed PRs
46. Uppercases lowercase state
47. Returns "" on FileNotFoundError (gh not installed)
48. Returns "" on TimeoutExpired
49. Returns "" on OSError
50. Returns "" on non-zero exit code
51. Accepts string PR number

_check_slug_paused_state() -- direct tests:
52. Returns formatted context for valid paused-state.json
53. Returns None for missing file
54. Returns None for empty slug
55. Returns None for corrupt JSON
56. Returns None when pr_number missing
57. Returns stale message and deletes file for >14 day state
58. Returns merged message and deletes file for MERGED PR
59. Returns closed message and deletes file for CLOSED PR
60. Includes consolidation warning when not completed
61. Returns None on IOError (fail-open)
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


class TestRestoreLastSession:
    """Tests for restore_last_session() -- cross-session continuity."""

    def test_returns_none_when_no_snapshot(self, tmp_path):
        """Should return None when no last-session.md exists."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(
            project_slug="nonexistent",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_content_with_header(self, tmp_path):
        """Should return snapshot content with descriptive header."""
        from shared.session_resume import restore_last_session

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        snapshot = "# Last Session\n## Completed Tasks\n- #1 auth\n"
        (proj_dir / "last-session.md").write_text(snapshot)

        result = restore_last_session(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        assert result is not None
        assert "Previous session summary" in result
        assert "read-only reference" in result
        assert "# Last Session" in result

    def test_rotates_file_to_prev(self, tmp_path):
        """Should move last-session.md to last-session.prev.md after reading."""
        from shared.session_resume import restore_last_session

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        content = "# Last Session\n"
        (proj_dir / "last-session.md").write_text(content)

        restore_last_session(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        assert not (proj_dir / "last-session.md").exists()
        assert (proj_dir / "last-session.prev.md").exists()
        assert (proj_dir / "last-session.prev.md").read_text() == content

    def test_returns_none_when_empty_slug(self, tmp_path):
        """Should return None when project_slug is empty."""
        from shared.session_resume import restore_last_session

        result = restore_last_session(
            project_slug="",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_when_empty_file(self, tmp_path):
        """Should return None when snapshot file is empty."""
        from shared.session_resume import restore_last_session

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        (proj_dir / "last-session.md").write_text("")

        result = restore_last_session(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

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


class TestRestoreLastSessionErrorPaths:
    """Tests for restore_last_session() error handling paths."""

    def test_returns_none_on_ioerror(self, tmp_path):
        """Should return None when snapshot read raises IOError."""
        from shared.session_resume import restore_last_session
        from unittest.mock import patch as mock_patch

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        snapshot = proj_dir / "last-session.md"
        snapshot.write_text("content")

        with mock_patch.object(Path, "read_text", side_effect=IOError("read error")):
            result = restore_last_session(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        assert result is None

    def test_returns_none_on_unicode_error(self, tmp_path):
        """Should return None when snapshot has encoding issues."""
        from shared.session_resume import restore_last_session
        from unittest.mock import patch as mock_patch

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        snapshot = proj_dir / "last-session.md"
        snapshot.write_text("content")

        with mock_patch.object(
            Path, "read_text",
            side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "bad"),
        ):
            result = restore_last_session(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        assert result is None

    def test_continues_on_rotation_failure(self, tmp_path):
        """Should still return content even when rotation to .prev fails."""
        from shared.session_resume import restore_last_session
        from unittest.mock import patch as mock_patch

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        content = "# Last Session\n## Tasks\n- #1\n"
        snapshot = proj_dir / "last-session.md"
        snapshot.write_text(content)

        # Make prev file write fail, but original read should succeed
        original_write = Path.write_text
        def failing_write(self, *args, **kwargs):
            if "last-session.prev.md" in str(self):
                raise IOError("disk full")
            return original_write(self, *args, **kwargs)

        with mock_patch.object(Path, "write_text", failing_write):
            result = restore_last_session(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "Previous session summary" in result
        assert "# Last Session" in result


# =============================================================================
# check_paused_state() Tests
# =============================================================================

# Use a dynamic date within the 14-day TTL window so tests don't rot over time.
# The TTL check in check_paused_state() cleans up paused states older than 14 days,
# so a hardcoded date would cause test failures once it ages past the threshold.
from datetime import datetime, timezone, timedelta as _timedelta

_RECENT_PAUSED_AT = (
    datetime.now(timezone.utc) - _timedelta(days=1)
).strftime("%Y-%m-%dT%H:%M:%SZ")

VALID_PAUSED_STATE = {
    "pr_number": 288,
    "pr_url": "https://github.com/owner/repo/pull/288",
    "branch": "feat/pause-mode-289",
    "worktree_path": "/path/to/.worktrees/feat-pause-mode-289",
    "paused_at": _RECENT_PAUSED_AT,
    "consolidation_completed": True,
    "team_name": "pact-d7ab1edb",
}


class TestCheckPausedState:
    """Tests for check_paused_state() -- paused work detection."""

    def _write_paused_state(self, sessions_dir, project_slug, state):
        """Helper: write a paused-state.json file."""
        import json

        proj_dir = Path(sessions_dir) / project_slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        state_file = proj_dir / "paused-state.json"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        return state_file

    def test_returns_context_string_when_paused_state_exists(self, tmp_path):
        """Should return formatted context when paused-state.json exists and PR is open."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        self._write_paused_state(tmp_path, "my-project", VALID_PAUSED_STATE)

        # Mock gh as unavailable so we test the fail-open (lazy validation) path
        with mock_patch("shared.session_resume.subprocess.run", side_effect=FileNotFoundError):
            result = check_paused_state(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "Paused work detected" in result
        assert "PR #288" in result
        assert "feat/pause-mode-289" in result
        assert "/path/to/.worktrees/feat-pause-mode-289" in result
        assert "/PACT:peer-review" in result

    def test_returns_none_when_no_paused_state(self, tmp_path):
        """Should return None when paused-state.json does not exist."""
        from shared.session_resume import check_paused_state

        # Create the project dir but no paused-state.json
        (tmp_path / "my-project").mkdir()

        result = check_paused_state(
            project_slug="my-project",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_when_empty_project_slug(self, tmp_path):
        """Should return None when project_slug is empty."""
        from shared.session_resume import check_paused_state

        result = check_paused_state(
            project_slug="",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_when_sessions_dir_missing(self, tmp_path):
        """Should return None when sessions directory doesn't exist."""
        from shared.session_resume import check_paused_state

        result = check_paused_state(
            project_slug="my-project",
            sessions_dir=str(tmp_path / "nonexistent"),
        )

        assert result is None

    def test_includes_pr_number_branch_worktree(self, tmp_path):
        """Output should include all key paused state fields."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        state = {**VALID_PAUSED_STATE, "pr_number": 42, "branch": "fix/login-bug"}
        self._write_paused_state(tmp_path, "proj", state)

        # Mock gh as unavailable so we test the lazy validation path
        with mock_patch("shared.session_resume.subprocess.run", side_effect=FileNotFoundError):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert "PR #42" in result
        assert "fix/login-bug" in result

    def test_no_consolidation_note_when_completed_true(self, tmp_path):
        """Should NOT include consolidation guidance when consolidation_completed is true."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        state = {**VALID_PAUSED_STATE, "consolidation_completed": True}
        self._write_paused_state(tmp_path, "proj", state)

        # Mock gh as unavailable so we test the lazy validation path
        with mock_patch("shared.session_resume.subprocess.run", side_effect=FileNotFoundError):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "Paused work detected" in result
        assert "did NOT complete" not in result

    def test_consolidation_note_when_completed_false(self, tmp_path):
        """Should include consolidation guidance when consolidation_completed is false."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        state = {**VALID_PAUSED_STATE, "consolidation_completed": False}
        self._write_paused_state(tmp_path, "proj", state)

        # Mock gh as unavailable so we test the lazy validation path
        with mock_patch("shared.session_resume.subprocess.run", side_effect=FileNotFoundError):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "did NOT complete" in result
        assert "/PACT:pause" in result or "/PACT:wrap-up" in result

    def test_consolidation_note_when_field_missing(self, tmp_path):
        """Should default to consolidation_completed=False when field missing."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        state = {
            "pr_number": 100,
            "branch": "main",
            "worktree_path": "/tmp/wt",
        }
        self._write_paused_state(tmp_path, "proj", state)

        # Mock gh as unavailable so we test the lazy validation path
        with mock_patch("shared.session_resume.subprocess.run", side_effect=FileNotFoundError):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "did NOT complete" in result

    def test_returns_none_on_corrupt_json(self, tmp_path):
        """Should return None when paused-state.json contains invalid JSON (fail-open)."""
        from shared.session_resume import check_paused_state

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "paused-state.json").write_text("not valid json{{{")

        result = check_paused_state(
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_when_pr_number_missing(self, tmp_path):
        """Should return None when pr_number field is absent."""
        from shared.session_resume import check_paused_state
        import json

        state = {"branch": "main", "worktree_path": "/tmp/wt", "consolidation_completed": True}
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "paused-state.json").write_text(json.dumps(state))

        result = check_paused_state(
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_on_empty_file(self, tmp_path):
        """Should return None when paused-state.json is empty (fail-open)."""
        from shared.session_resume import check_paused_state

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "paused-state.json").write_text("")

        result = check_paused_state(
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    def test_returns_none_on_ioerror(self, tmp_path):
        """Should return None when file read raises IOError (fail-open)."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        self._write_paused_state(tmp_path, "proj", VALID_PAUSED_STATE)

        original_read = Path.read_text
        def failing_read(self_path, *args, **kwargs):
            if "paused-state.json" in str(self_path):
                raise IOError("disk error")
            return original_read(self_path, *args, **kwargs)

        with mock_patch.object(Path, "read_text", failing_read):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is None

    def test_handles_missing_optional_fields_with_defaults(self, tmp_path):
        """Should use 'unknown' defaults for missing branch and worktree_path."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch
        import json

        # Only pr_number required; branch and worktree_path should default
        state = {"pr_number": 55}
        self._write_paused_state(tmp_path, "proj", state)

        # Mock gh as unavailable so we test the lazy validation path
        with mock_patch("shared.session_resume.subprocess.run", side_effect=FileNotFoundError):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "PR #55" in result
        assert "unknown" in result  # Default for missing branch/worktree

    def test_returns_none_on_unicode_decode_error(self, tmp_path):
        """Should return None when file contains non-UTF-8 bytes (fail-open)."""
        from shared.session_resume import check_paused_state

        # Write a valid paused-state.json first (so the file exists for .exists() check)
        self._write_paused_state(tmp_path, "proj", VALID_PAUSED_STATE)

        # Overwrite with raw bytes that trigger UnicodeDecodeError
        state_file = tmp_path / "proj" / "paused-state.json"
        state_file.write_bytes(b"\x80\x81\x82\xff\xfe")

        result = check_paused_state(
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        assert result is None

    @pytest.mark.parametrize(
        "subprocess_return, expected_behavior",
        [
            pytest.param(
                {"returncode": 0, "stdout": "MERGED\n"},
                {"contains": ["merged", "PR #288", "Cleaned up paused state"], "state_file_removed": True},
                id="MERGED-cleanup",
            ),
            pytest.param(
                {"returncode": 0, "stdout": "CLOSED\n"},
                {"contains": ["closed", "PR #288", "Cleaned up paused state"], "state_file_removed": True},
                id="CLOSED-cleanup",
            ),
            pytest.param(
                {"returncode": 0, "stdout": "OPEN\n"},
                {"contains": ["Paused work detected", "PR #288"], "state_file_removed": False},
                id="OPEN-fall-through",
            ),
            pytest.param(
                "timeout",
                {"contains": ["Paused work detected"], "state_file_removed": False},
                id="timeout-fall-through",
            ),
            pytest.param(
                {"returncode": 1, "stdout": "", "stderr": "not found"},
                {"contains": ["Paused work detected"], "state_file_removed": False},
                id="nonzero-exit-fall-through",
            ),
        ],
    )
    def test_active_pr_validation(self, tmp_path, subprocess_return, expected_behavior):
        """Parameterized: active PR validation with MERGED/CLOSED/OPEN/timeout/error."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch, MagicMock
        import subprocess as sp

        self._write_paused_state(tmp_path, "my-project", VALID_PAUSED_STATE)
        state_file = tmp_path / "my-project" / "paused-state.json"

        if subprocess_return == "timeout":
            side_effect = sp.TimeoutExpired(cmd="gh", timeout=5)
            mock_ctx = mock_patch("shared.session_resume.subprocess.run", side_effect=side_effect)
        else:
            mock_result = MagicMock(**subprocess_return)
            mock_ctx = mock_patch("shared.session_resume.subprocess.run", return_value=mock_result)

        with mock_ctx:
            result = check_paused_state(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        for text in expected_behavior["contains"]:
            assert text in result, f"Expected '{text}' in result"

        if expected_behavior["state_file_removed"]:
            assert not state_file.exists(), "State file should be removed"
        else:
            assert state_file.exists(), "State file should persist"

    def test_subprocess_called_with_exact_args(self, tmp_path):
        """Should call gh pr view with exact arguments and timeout=5."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch, MagicMock

        self._write_paused_state(tmp_path, "my-project", VALID_PAUSED_STATE)

        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result) as mock_subprocess:
            check_paused_state(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        mock_subprocess.assert_called_once_with(
            ["gh", "pr", "view", str(VALID_PAUSED_STATE["pr_number"]), "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_returns_merged_message_when_unlink_fails(self, tmp_path):
        """Should still return merged message when state_file.unlink() raises OSError."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch, MagicMock

        self._write_paused_state(tmp_path, "my-project", VALID_PAUSED_STATE)

        mock_result = MagicMock(returncode=0, stdout="MERGED\n")

        original_unlink = Path.unlink

        def failing_unlink(self_path, *args, **kwargs):
            if "paused-state.json" in str(self_path):
                raise OSError("Permission denied")
            return original_unlink(self_path, *args, **kwargs)

        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result), \
             mock_patch.object(Path, "unlink", failing_unlink):
            result = check_paused_state(
                project_slug="my-project",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "merged" in result
        assert "PR #288" in result
        assert "Cleaned up paused state" in result


class TestCheckPausedStateTTL:
    """Tests for check_paused_state() -- TTL cleanup of stale paused state."""

    def _write_paused_state(self, sessions_dir, project_slug, state):
        """Helper: write a paused-state.json file."""
        import json

        proj_dir = Path(sessions_dir) / project_slug
        proj_dir.mkdir(parents=True, exist_ok=True)
        state_file = proj_dir / "paused-state.json"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        return state_file

    def test_cleans_up_stale_paused_state_older_than_14_days(self, tmp_path):
        """Should clean up paused state when paused_at is older than 14 days."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch

        stale_state = {
            **VALID_PAUSED_STATE,
            "paused_at": "2026-02-01T09:30:00Z",  # well over 14 days ago
        }
        state_file = self._write_paused_state(tmp_path, "proj", stale_state)

        result = check_paused_state(
            project_slug="proj",
            sessions_dir=str(tmp_path),
        )

        assert result is not None
        assert "Stale" in result or "stale" in result or "older than 14 days" in result.lower() or "cleaned up" in result.lower()
        assert not state_file.exists()

    def test_does_not_clean_up_recent_paused_state(self, tmp_path):
        """Should NOT clean up paused state when paused_at is within 14 days."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch, MagicMock

        # Use a date that's recent (within 14 days)
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_state = {**VALID_PAUSED_STATE, "paused_at": recent}
        state_file = self._write_paused_state(tmp_path, "proj", recent_state)

        # gh reports OPEN so we fall through to normal paused-work message
        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "Paused work detected" in result
        assert state_file.exists()

    def test_skips_ttl_check_when_paused_at_missing(self, tmp_path):
        """Should skip TTL check when paused_at field is missing."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch, MagicMock

        state = {
            "pr_number": 100,
            "branch": "feat/test",
            "worktree_path": "/tmp/wt",
            # No paused_at field
        }
        self._write_paused_state(tmp_path, "proj", state)

        # gh reports OPEN so we fall through to normal paused-work message
        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "Paused work detected" in result

    def test_skips_ttl_check_when_paused_at_unparseable(self, tmp_path):
        """Should skip TTL check when paused_at is not a valid ISO timestamp."""
        from shared.session_resume import check_paused_state
        from unittest.mock import patch as mock_patch, MagicMock

        state = {
            **VALID_PAUSED_STATE,
            "paused_at": "not-a-date",
        }
        self._write_paused_state(tmp_path, "proj", state)

        # gh reports OPEN so we fall through to normal paused-work message
        mock_result = MagicMock(returncode=0, stdout="OPEN\n")
        with mock_patch("shared.session_resume.subprocess.run", return_value=mock_result):
            result = check_paused_state(
                project_slug="proj",
                sessions_dir=str(tmp_path),
            )

        assert result is not None
        assert "Paused work detected" in result


# =============================================================================
# VALID_PAUSED_STATE Relative Date Robustness (Test Engineer)
# =============================================================================

class TestPausedStateFixtureRobustness:
    """Verify the dynamic date fixture doesn't introduce time-bombs.

    The VALID_PAUSED_STATE fixture was changed from a hardcoded date
    (2026-03-18) to a relative date (now - 1 day). This class verifies
    the dynamic date stays within the 14-day TTL window used by
    check_paused_state() and won't cause flaky tests.
    """

    def test_recent_paused_at_within_ttl_window(self):
        """_RECENT_PAUSED_AT should be within the 14-day TTL window."""
        paused = datetime.strptime(_RECENT_PAUSED_AT, "%Y-%m-%dT%H:%M:%SZ")
        paused = paused.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        age = now - paused
        # Should be approximately 1 day old, well within 14-day TTL
        assert age.days <= 2, f"Fixture date is {age.days} days old, should be ~1"
        assert age.days >= 0, f"Fixture date is in the future: {_RECENT_PAUSED_AT}"

    def test_valid_paused_state_is_not_stale(self):
        """VALID_PAUSED_STATE's paused_at should not trigger TTL cleanup."""
        paused = datetime.strptime(
            VALID_PAUSED_STATE["paused_at"], "%Y-%m-%dT%H:%M:%SZ"
        )
        paused = paused.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        age_days = (now - paused).days
        # TTL is 14 days — fixture must be younger
        assert age_days < 14, (
            f"VALID_PAUSED_STATE.paused_at is {age_days} days old "
            f"(TTL=14 days). The relative date fixture should prevent this."
        )

    def test_paused_at_format_is_valid_iso(self):
        """paused_at should be valid ISO 8601 format."""
        # This should not raise
        paused = datetime.strptime(_RECENT_PAUSED_AT, "%Y-%m-%dT%H:%M:%SZ")
        assert paused is not None

    def test_fixture_produces_utc_timestamp(self):
        """Dynamic date should be in UTC (matching the production format)."""
        assert _RECENT_PAUSED_AT.endswith("Z"), (
            f"Expected UTC 'Z' suffix, got: {_RECENT_PAUSED_AT}"
        )


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


# ---------------------------------------------------------------------------
# _check_slug_paused_state() -- direct tests
# ---------------------------------------------------------------------------


class TestCheckSlugPausedState:
    """Direct tests for _check_slug_paused_state() -- legacy slug-level paused state.

    Previously tested only indirectly via check_paused_state() fallback.
    These tests exercise the function directly for edge case coverage.
    """

    def _write_state(self, sessions_dir, slug, state):
        """Helper: write paused-state.json."""
        import json

        slug_dir = Path(sessions_dir) / slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        state_file = slug_dir / "paused-state.json"
        state_file.write_text(json.dumps(state), encoding="utf-8")
        return state_file

    def test_returns_formatted_context_for_valid_state(self, tmp_path):
        """Returns formatted context string for valid paused-state.json."""
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_slug_paused_state

        state = {
            "pr_number": 42,
            "branch": "feat/test",
            "worktree_path": "/tmp/wt",
            "paused_at": _RECENT_PAUSED_AT,
            "consolidation_completed": True,
        }
        self._write_state(tmp_path, "my-project", state)

        with mock_patch(
            "shared.session_resume.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _check_slug_paused_state("my-project", str(tmp_path))

        assert result is not None
        assert "PR #42" in result
        assert "feat/test" in result
        assert "Paused work detected" in result

    def test_returns_none_for_missing_file(self, tmp_path):
        """Returns None when paused-state.json doesn't exist."""
        from shared.session_resume import _check_slug_paused_state

        result = _check_slug_paused_state("my-project", str(tmp_path))
        assert result is None

    def test_returns_none_for_empty_slug(self):
        """Returns None when project_slug is empty."""
        from shared.session_resume import _check_slug_paused_state

        result = _check_slug_paused_state("")
        assert result is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        """Returns None for corrupt/invalid JSON (fail-open)."""
        from shared.session_resume import _check_slug_paused_state

        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        (slug_dir / "paused-state.json").write_text("not valid json{{{")

        result = _check_slug_paused_state("my-project", str(tmp_path))
        assert result is None

    def test_returns_none_when_pr_number_missing(self, tmp_path):
        """Returns None when pr_number field is absent."""
        from shared.session_resume import _check_slug_paused_state

        state = {"branch": "feat/test", "worktree_path": "/tmp/wt"}
        self._write_state(tmp_path, "my-project", state)

        result = _check_slug_paused_state("my-project", str(tmp_path))
        assert result is None

    def test_stale_state_returns_message_and_deletes_file(self, tmp_path):
        """Returns stale message and deletes file for >14 day old state."""
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_slug_paused_state

        old_ts = (
            datetime.now(timezone.utc) - _timedelta(days=15)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        state = {
            "pr_number": 55,
            "branch": "feat/old",
            "worktree_path": "/tmp/old",
            "paused_at": old_ts,
        }
        state_file = self._write_state(tmp_path, "my-project", state)
        assert state_file.exists()

        result = _check_slug_paused_state("my-project", str(tmp_path))

        assert result is not None
        assert "Stale" in result
        assert "PR #55" in result
        assert "older than 14 days" in result
        # File should be deleted
        assert not state_file.exists()

    def test_merged_pr_returns_message_and_deletes_file(self, tmp_path):
        """Returns merged message and deletes file for MERGED PR."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_slug_paused_state

        state = {
            "pr_number": 77,
            "branch": "feat/done",
            "worktree_path": "/tmp/done",
            "paused_at": _RECENT_PAUSED_AT,
        }
        state_file = self._write_state(tmp_path, "my-project", state)

        mock_result = MagicMock(returncode=0, stdout="MERGED\n")
        with mock_patch(
            "shared.session_resume.subprocess.run", return_value=mock_result
        ):
            result = _check_slug_paused_state("my-project", str(tmp_path))

        assert result is not None
        assert "merged" in result.lower()
        assert "PR #77" in result
        assert "Cleaned up" in result
        # File should be deleted
        assert not state_file.exists()

    def test_closed_pr_returns_message_and_deletes_file(self, tmp_path):
        """Returns closed message and deletes file for CLOSED PR."""
        from unittest.mock import patch as mock_patch, MagicMock
        from shared.session_resume import _check_slug_paused_state

        state = {
            "pr_number": 88,
            "branch": "feat/abandoned",
            "worktree_path": "/tmp/abandoned",
            "paused_at": _RECENT_PAUSED_AT,
        }
        state_file = self._write_state(tmp_path, "my-project", state)

        mock_result = MagicMock(returncode=0, stdout="CLOSED\n")
        with mock_patch(
            "shared.session_resume.subprocess.run", return_value=mock_result
        ):
            result = _check_slug_paused_state("my-project", str(tmp_path))

        assert result is not None
        assert "closed" in result.lower()
        assert not state_file.exists()

    def test_includes_consolidation_warning_when_not_completed(self, tmp_path):
        """Includes consolidation warning when consolidation_completed is False."""
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_slug_paused_state

        state = {
            "pr_number": 99,
            "branch": "feat/test",
            "worktree_path": "/tmp/wt",
            "paused_at": _RECENT_PAUSED_AT,
            "consolidation_completed": False,
        }
        self._write_state(tmp_path, "my-project", state)

        with mock_patch(
            "shared.session_resume.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _check_slug_paused_state("my-project", str(tmp_path))

        assert result is not None
        assert "Memory consolidation did NOT complete" in result

    def test_returns_none_on_ioerror(self, tmp_path):
        """Returns None on IOError during read (fail-open)."""
        from unittest.mock import patch as mock_patch
        from shared.session_resume import _check_slug_paused_state

        # Create the file so existence check passes, then mock read to fail
        slug_dir = tmp_path / "my-project"
        slug_dir.mkdir(parents=True)
        state_file = slug_dir / "paused-state.json"
        state_file.write_text('{"pr_number": 42}')

        with mock_patch.object(Path, "read_text", side_effect=IOError("disk error")):
            result = _check_slug_paused_state("my-project", str(tmp_path))

        assert result is None
