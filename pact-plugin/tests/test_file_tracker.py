"""
Tests for file_tracker.py — PostToolUse hook matching Edit|Write that tracks
which agent edits which files and warns on conflicts.

Tests cover:
1. Records file edit to tracking JSON
2. Detects conflict when different agent edits same file
3. No conflict when same agent edits same file again
4. Creates tracking file if missing
5. No-op when no agent name set
"""
import json
import sys
import time
from pathlib import Path

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
