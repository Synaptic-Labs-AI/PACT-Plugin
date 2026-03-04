"""
Tests for track_files.py — PostToolUse hook that tracks files modified during
the session for the memory system's graph network.

Tests cover:
1. Timestamps use timezone-aware UTC (not naive datetime.utcnow())
2. Basic track/load/save cycle works
3. File locking is present in load/save (structural test via mock)
4. Update existing entry updates timestamp
5. main() entry point: stdin JSON parsing, exit codes
6. Atomic read-modify-write: track_file() holds single lock for full cycle
7. _update_data() pure function: new entry, update existing, no side effects
"""
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestTrackFilesTimestamps:
    """H4: Verify timestamps use timezone-aware UTC, not deprecated utcnow()."""

    def test_new_file_timestamps_are_timezone_aware(self, tmp_path):
        """first_seen and last_modified must contain timezone info ('+00:00')."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/app.py", "Edit")

        data = json.loads(tracking_file.read_text())
        entry = data["files"][0]

        # Timezone-aware ISO format includes '+00:00' suffix
        assert "+00:00" in entry["first_seen"], (
            f"first_seen lacks timezone info: {entry['first_seen']}"
        )
        assert "+00:00" in entry["last_modified"], (
            f"last_modified lacks timezone info: {entry['last_modified']}"
        )

    def test_updated_file_timestamp_is_timezone_aware(self, tmp_path):
        """When updating an existing entry, last_modified must be timezone-aware."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/app.py", "Edit")
            track_files.track_file("/src/app.py", "Write")

        data = json.loads(tracking_file.read_text())
        entry = data["files"][0]

        assert "+00:00" in entry["last_modified"], (
            f"Updated last_modified lacks timezone info: {entry['last_modified']}"
        )

    def test_timestamps_are_valid_iso_format(self, tmp_path):
        """Timestamps must be parseable as ISO 8601."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/app.py", "Edit")

        data = json.loads(tracking_file.read_text())
        entry = data["files"][0]

        # Should parse without error and have tzinfo
        parsed_first = datetime.fromisoformat(entry["first_seen"])
        parsed_last = datetime.fromisoformat(entry["last_modified"])
        assert parsed_first.tzinfo is not None
        assert parsed_last.tzinfo is not None


class TestTrackFilesCycle:
    """Basic track/load/save cycle."""

    def test_track_new_file(self, tmp_path):
        """Tracking a new file creates an entry with correct fields."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/auth.ts", "Edit")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "/src/auth.ts"
        assert data["files"][0]["tool"] == "Edit"

    def test_track_updates_existing_file(self, tmp_path):
        """Tracking same file again updates timestamp, no duplicate entry."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/auth.ts", "Edit")
            track_files.track_file("/src/auth.ts", "Write")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["tool"] == "Write"

    def test_track_multiple_files(self, tmp_path):
        """Tracking different files creates separate entries."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/auth.ts", "Edit")
            track_files.track_file("/src/db.ts", "Write")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 2
        paths = [f["path"] for f in data["files"]]
        assert "/src/auth.ts" in paths
        assert "/src/db.ts" in paths

    def test_track_empty_path_is_noop(self, tmp_path):
        """Empty file path should not create an entry."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("", "Edit")

        assert not tracking_file.exists()

    def test_load_missing_file_returns_default(self, tmp_path):
        """Loading from a non-existent file returns default structure."""
        import track_files

        tracking_file = tmp_path / "nonexistent.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            data = track_files.load_tracked_files()

        assert data["files"] == []

    def test_load_corrupted_json_returns_default(self, tmp_path):
        """Loading from a corrupted JSON file returns default structure."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        tracking_file.write_text("{invalid json")
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            data = track_files.load_tracked_files()

        assert data["files"] == []


class TestTrackFilesLocking:
    """H5: Verify file locking is used to prevent TOCTOU race conditions."""

    def test_save_uses_flock(self, tmp_path):
        """save_tracked_files must acquire exclusive lock via fcntl.flock."""
        import track_files

        tracking_file = tmp_path / "test-session.json"

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            # Mock fcntl to verify locking behavior
            mock_fcntl = MagicMock()
            with patch.object(track_files, "HAS_FLOCK", True), \
                 patch.object(track_files, "fcntl", mock_fcntl, create=True):
                track_files.save_tracked_files({"files": [], "session_id": "test"})

        # Verify flock was called with LOCK_EX (acquire) and LOCK_UN (release)
        flock_calls = mock_fcntl.flock.call_args_list
        assert len(flock_calls) >= 2, f"Expected at least 2 flock calls, got {len(flock_calls)}"
        # First call should be LOCK_EX
        assert flock_calls[0][0][1] == mock_fcntl.LOCK_EX
        # Last call should be LOCK_UN
        assert flock_calls[-1][0][1] == mock_fcntl.LOCK_UN

    def test_load_uses_flock(self, tmp_path):
        """load_tracked_files must acquire shared lock via fcntl.flock."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        tracking_file.write_text(json.dumps({"files": [], "session_id": "test"}))

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            mock_fcntl = MagicMock()
            with patch.object(track_files, "HAS_FLOCK", True), \
                 patch.object(track_files, "fcntl", mock_fcntl, create=True):
                track_files.load_tracked_files()

        flock_calls = mock_fcntl.flock.call_args_list
        assert len(flock_calls) >= 2, f"Expected at least 2 flock calls, got {len(flock_calls)}"

    def test_track_file_uses_single_lock(self, tmp_path):
        """track_file must hold exactly one LOCK_EX for the entire read-modify-write.

        This verifies the TOCTOU fix: a single lock acquisition covers read,
        modify, and write — not separate locks for load and save.
        """
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            mock_fcntl = MagicMock()
            with patch.object(track_files, "HAS_FLOCK", True), \
                 patch.object(track_files, "fcntl", mock_fcntl, create=True):
                track_files.track_file("/src/app.py", "Edit")
            flock_calls = mock_fcntl.flock.call_args_list
            # Exactly 2 flock calls: one LOCK_EX, one LOCK_UN (single lock cycle)
            assert len(flock_calls) == 2, (
                f"Expected exactly 2 flock calls (1 lock + 1 unlock), got {len(flock_calls)}: {flock_calls}"
            )
            assert flock_calls[0][0][1] == mock_fcntl.LOCK_EX
            assert flock_calls[1][0][1] == mock_fcntl.LOCK_UN

    def test_flock_released_on_exception(self, tmp_path):
        """Lock must be released even when an exception occurs (finally block)."""
        import track_files

        tracking_file = tmp_path / "test-session.json"

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            mock_fcntl = MagicMock()
            # Make json.dumps raise to simulate error during write
            with patch.object(track_files, "HAS_FLOCK", True), \
                 patch.object(track_files, "fcntl", mock_fcntl, create=True), \
                 patch("json.dump", side_effect=ValueError("test error")):
                try:
                    track_files.save_tracked_files({"files": [], "session_id": "test"})
                except (ValueError, IOError):
                    pass

            # Lock should still be released via finally block
            flock_calls = mock_fcntl.flock.call_args_list
            unlock_calls = [c for c in flock_calls if c[0][1] == mock_fcntl.LOCK_UN]
            assert len(unlock_calls) >= 1, "Lock was not released after exception"

    def test_fallback_without_flock(self, tmp_path):
        """When fcntl is unavailable, operations still work without locking."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file), \
             patch.object(track_files, "HAS_FLOCK", False):
            track_files.track_file("/src/app.py", "Edit")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "/src/app.py"


class TestUpdateData:
    """Tests for _update_data() pure function."""

    def test_adds_new_entry(self):
        """New file path creates an entry with correct fields."""
        import track_files

        data = {"files": [], "session_id": "test"}
        result = track_files._update_data(data, "/src/app.py", "Edit")

        assert len(result["files"]) == 1
        assert result["files"][0]["path"] == "/src/app.py"
        assert result["files"][0]["tool"] == "Edit"
        assert "+00:00" in result["files"][0]["first_seen"]

    def test_updates_existing_entry(self):
        """Existing file path updates tool and last_modified, keeps first_seen."""
        import track_files

        data = {"files": [{
            "path": "/src/app.py",
            "tool": "Edit",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_modified": "2026-01-01T00:00:00+00:00",
        }], "session_id": "test"}
        result = track_files._update_data(data, "/src/app.py", "Write")

        assert len(result["files"]) == 1
        assert result["files"][0]["tool"] == "Write"
        assert result["files"][0]["first_seen"] == "2026-01-01T00:00:00+00:00"
        assert result["files"][0]["last_modified"] != "2026-01-01T00:00:00+00:00"

    def test_does_not_duplicate(self):
        """Tracking same file twice via _update_data does not create duplicates."""
        import track_files

        data = {"files": [], "session_id": "test"}
        data = track_files._update_data(data, "/src/app.py", "Edit")
        data = track_files._update_data(data, "/src/app.py", "Write")

        assert len(data["files"]) == 1


class TestTrackFileAtomicity:
    """Verify track_file() does atomic read-modify-write under single lock."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="fcntl not available on Windows"
    )
    def test_lock_released_on_write_exception(self, tmp_path):
        """Lock must be released if an exception occurs during track_file write."""
        import fcntl
        import track_files

        tracking_file = tmp_path / "test-session.json"

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            with patch("json.dump", side_effect=RuntimeError("boom")):
                try:
                    track_files.track_file("/src/app.py", "Edit")
                except (RuntimeError, IOError):
                    pass

        # If the lock was properly released via finally, we can acquire it
        if tracking_file.exists():
            with open(tracking_file, "r") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)

    def test_track_file_handles_corrupted_json(self, tmp_path):
        """track_file recovers from corrupted JSON in the tracking file."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        tracking_file.write_text("{corrupted")

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file):
            track_files.track_file("/src/app.py", "Edit")

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "/src/app.py"


class TestTrackFilesMain:
    """Tests for main() entry point."""

    def test_main_tracks_edit_tool(self, tmp_path):
        """main() processes Edit tool input correctly."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/src/app.py"},
        })

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                track_files.main()
            assert exc_info.value.code == 0

        data = json.loads(tracking_file.read_text())
        assert len(data["files"]) == 1

    def test_main_ignores_non_edit_write(self, tmp_path):
        """main() exits early for non-Edit/Write tools."""
        import track_files

        tracking_file = tmp_path / "test-session.json"
        input_data = json.dumps({
            "tool_name": "Read",
            "tool_input": {"file_path": "/src/app.py"},
        })

        with patch.object(track_files, "get_session_tracking_file", return_value=tracking_file), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                track_files.main()
            assert exc_info.value.code == 0

        assert not tracking_file.exists()

    def test_main_handles_invalid_json(self):
        """main() exits 0 on invalid JSON input."""
        import track_files

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                track_files.main()
            assert exc_info.value.code == 0
