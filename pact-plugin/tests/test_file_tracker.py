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
"""
import io
import json
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

        track_edit(
            file_path="src/auth.ts",
            agent_name="backend-coder",
            tool_name="Edit",
            tracking_path=str(tracking_file)
        )

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        assert entries[0]["file"] == "src/auth.ts"
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

        # track_edit should overwrite with a fresh single-entry list
        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        assert entries[0]["file"] == "src/auth.ts"

    def test_corrupted_tracking_json_no_conflict(self, tmp_path):
        """check_conflict with corrupted tracking file should return None."""
        from file_tracker import check_conflict

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("not valid json{{{")

        conflict = check_conflict("src/auth.ts", "backend-coder", str(tracking_file))

        assert conflict is None


class TestMainEntryPoint:
    """Tests for file_tracker.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_no_team_name(self):
        from file_tracker import main

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_valid_edit(self, tmp_path):
        from file_tracker import main

        input_data = json.dumps({
            "tool_input": {"file_path": "src/auth.ts"},
            "tool_name": "Edit",
        })

        tracking_path = str(tmp_path / "file-edits.json")
        teams_dir = tmp_path / "pact-test"
        teams_dir.mkdir(parents=True)

        env = {
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
            "CLAUDE_CODE_AGENT_NAME": "backend-coder",
        }

        with patch.dict("os.environ", env, clear=True), \
             patch("file_tracker.check_conflict", return_value=None), \
             patch("file_tracker.track_edit"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_invalid_json(self):
        from file_tracker import main

        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}
        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_file_path(self):
        from file_tracker import main

        input_data = json.dumps({"tool_input": {}})

        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}
        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_outputs_warning_on_conflict(self, capsys):
        from file_tracker import main

        input_data = json.dumps({
            "tool_input": {"file_path": "src/auth.ts"},
            "tool_name": "Edit",
        })

        env = {
            "CLAUDE_CODE_TEAM_NAME": "pact-test",
            "CLAUDE_CODE_AGENT_NAME": "frontend-coder",
        }

        conflict_msg = "File conflict: src/auth.ts was also edited by backend-coder."
        with patch.dict("os.environ", env, clear=True), \
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
