"""
Tests for file_tracker.py — PostToolUse hook matching Edit|Write that tracks
which agent edits which files and warns on conflicts.

Tests cover:
1. Records file edit to tracking JSON
2. Detects conflict when different agent edits same file
3. No conflict when same agent edits same file again
4. Creates tracking file if missing
5. No-op when no agent name set
6. main() entry point: stdin JSON parsing, exit codes, output format
7. Corrupted tracking JSON treated as empty list
8. Path normalization: different representations of same file match
"""
import io
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestFileTracker:
    """Tests for file_tracker.track_edit() and file_tracker.check_conflict()."""

    def test_records_edit(self, tmp_path):
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"

        # Use an absolute path to avoid cwd-dependent normalization
        abs_path = str(tmp_path / "src" / "auth.ts")
        track_edit(
            file_path=abs_path,
            agent_name="backend-coder",
            tool_name="Edit",
            tracking_path=str(tracking_file)
        )

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        assert entries[0]["file"] == os.path.realpath(abs_path)
        assert entries[0]["agent"] == "backend-coder"

    def test_detects_conflict(self, tmp_path):
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        # First edit by backend-coder
        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        # Check conflict for frontend-coder editing same file
        conflict = check_conflict("src/auth.ts", "frontend-coder", str(tracking_file))

        assert conflict is not None
        assert "backend-coder" in conflict

    def test_no_conflict_same_agent(self, tmp_path):
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))
        conflict = check_conflict("src/auth.ts", "backend-coder", str(tracking_file))

        assert conflict is None

    def test_creates_tracking_file(self, tmp_path):
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        assert not tracking_file.exists()

        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        assert tracking_file.exists()

    def test_noop_when_no_agent_name(self, tmp_path):
        from file_tracker import check_conflict

        tracking_file = tmp_path / "file-edits.json"
        conflict = check_conflict("src/auth.ts", "", str(tracking_file))

        assert conflict is None

    def test_corrupted_tracking_json_treated_as_empty(self, tmp_path):
        """Corrupted tracking file should be treated as empty list."""
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("not valid json{{{")

        abs_path = str(tmp_path / "src" / "auth.ts")
        # track_edit should overwrite with a fresh single-entry list
        track_edit(abs_path, "backend-coder", "Edit", str(tracking_file))

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        assert entries[0]["file"] == os.path.realpath(abs_path)

    def test_corrupted_tracking_json_no_conflict(self, tmp_path):
        """check_conflict with corrupted tracking file should return None."""
        from file_tracker import check_conflict

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("not valid json{{{")

        conflict = check_conflict("src/auth.ts", "backend-coder", str(tracking_file))

        assert conflict is None


class TestPathNormalization:
    """Tests for _normalize_path and its effect on conflict detection."""

    def test_relative_path_normalized_to_absolute(self, tmp_path, monkeypatch):
        """Relative paths are resolved to absolute before recording."""
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"

        # Use a real directory as cwd so os.path.realpath can resolve
        monkeypatch.chdir(tmp_path)
        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        # The stored path should be absolute (resolved from cwd)
        assert entries[0]["file"] == str(tmp_path / "src" / "auth.ts")

    def test_dotslash_and_plain_paths_match(self, tmp_path, monkeypatch):
        """'./src/auth.ts' and 'src/auth.ts' should detect as same file."""
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        monkeypatch.chdir(tmp_path)
        track_edit("./src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        conflict = check_conflict("src/auth.ts", "frontend-coder", str(tracking_file))
        assert conflict is not None
        assert "backend-coder" in conflict

    def test_dotdot_paths_normalized(self, tmp_path, monkeypatch):
        """Paths with '../' components are resolved correctly."""
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        monkeypatch.chdir(tmp_path)
        track_edit("src/../src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        conflict = check_conflict("src/auth.ts", "frontend-coder", str(tracking_file))
        assert conflict is not None
        assert "backend-coder" in conflict

    def test_normalize_path_helper(self):
        """_normalize_path produces absolute, resolved paths."""
        from file_tracker import _normalize_path

        result = _normalize_path("/tmp/foo/../bar/baz.ts")
        assert result == os.path.join(os.path.realpath("/tmp"), "bar", "baz.ts")
        assert ".." not in result


class TestLockRelease:
    """Tests that fcntl lock is released even on exception."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="fcntl not available on Windows"
    )
    def test_lock_released_on_write_exception(self, tmp_path):
        """Lock must be released if an exception occurs during write operations."""
        import fcntl
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("[]")

        # Patch json.dumps to raise during the write phase (after lock acquired)
        with patch("file_tracker.json.dumps", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                track_edit("/tmp/test.ts", "agent-a", "Edit", str(tracking_file))

        # If the lock was properly released via finally, we should be able
        # to acquire it again without blocking
        with open(tracking_file, "r") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)


class TestMainEntryPoint:
    """Tests for file_tracker.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_no_team_name(self):
        from file_tracker import main

        input_data = json.dumps({"tool_name": "Edit"})

        with patch("file_tracker.get_team_name", return_value=""), \
             patch("file_tracker.pact_context.init"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_valid_edit(self, tmp_path, pact_context):
        from file_tracker import main

        pact_context(team_name="pact-test")

        input_data = json.dumps({
            "tool_input": {"file_path": "src/auth.ts"},
            "tool_name": "Edit",
        })

        with patch("file_tracker.resolve_agent_name", return_value="backend-coder"), \
             patch("file_tracker.check_conflict", return_value=None), \
             patch("file_tracker.track_edit"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_invalid_json(self, pact_context):
        from file_tracker import main

        pact_context(team_name="pact-test")

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_file_path(self, pact_context):
        from file_tracker import main

        pact_context(team_name="pact-test")

        input_data = json.dumps({"tool_input": {}})

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_outputs_warning_on_conflict(self, capsys, pact_context):
        from file_tracker import main

        pact_context(team_name="pact-test")

        input_data = json.dumps({
            "tool_input": {"file_path": "src/auth.ts"},
            "tool_name": "Edit",
        })

        conflict_msg = "File conflict: src/auth.ts was also edited by backend-coder."
        with patch("file_tracker.resolve_agent_name", return_value="frontend-coder"), \
             patch("file_tracker.check_conflict", return_value=conflict_msg), \
             patch("file_tracker.track_edit"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "conflict" in output["hookSpecificOutput"]["additionalContext"].lower()
