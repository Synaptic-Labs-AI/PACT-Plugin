"""
Tests for environment drift detection in file_tracker.py.

Tests cover:
1. get_environment_delta returns files modified since a given timestamp
2. get_environment_delta excludes edits by the requesting agent
3. get_environment_delta returns empty dict when no edits exist
4. get_environment_delta handles corrupted tracking JSON
"""
import json
import time
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestEnvironmentDrift:
    """Tests for file_tracker.get_environment_delta()."""

    def test_returns_files_modified_since_timestamp(self, tmp_path):
        from file_tracker import get_environment_delta

        tracking_file = tmp_path / "file-edits.json"
        now = int(time.time())
        entries = [
            {"file": "src/auth.ts", "agent": "backend-coder", "tool": "Edit", "ts": now - 10},
            {"file": "src/db.ts", "agent": "database-engineer", "tool": "Write", "ts": now - 5},
        ]
        tracking_file.write_text(json.dumps(entries))

        delta = get_environment_delta(
            since_ts=now - 15,
            requesting_agent="frontend-coder",
            tracking_path=str(tracking_file),
        )

        assert "src/auth.ts" in delta
        assert delta["src/auth.ts"] == "backend-coder"
        assert "src/db.ts" in delta
        assert delta["src/db.ts"] == "database-engineer"

    def test_excludes_requesting_agent_edits(self, tmp_path):
        from file_tracker import get_environment_delta

        tracking_file = tmp_path / "file-edits.json"
        now = int(time.time())
        entries = [
            {"file": "src/auth.ts", "agent": "backend-coder", "tool": "Edit", "ts": now - 10},
            {"file": "src/api.ts", "agent": "backend-coder", "tool": "Edit", "ts": now - 5},
        ]
        tracking_file.write_text(json.dumps(entries))

        delta = get_environment_delta(
            since_ts=now - 15,
            requesting_agent="backend-coder",
            tracking_path=str(tracking_file),
        )

        assert delta == {}

    def test_returns_empty_when_no_edits(self, tmp_path):
        from file_tracker import get_environment_delta

        tracking_file = tmp_path / "file-edits.json"

        delta = get_environment_delta(
            since_ts=0,
            requesting_agent="frontend-coder",
            tracking_path=str(tracking_file),
        )

        assert delta == {}

    def test_handles_corrupted_tracking_json(self, tmp_path):
        from file_tracker import get_environment_delta

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("not valid json{{{")

        delta = get_environment_delta(
            since_ts=0,
            requesting_agent="frontend-coder",
            tracking_path=str(tracking_file),
        )

        assert delta == {}

    def test_filters_by_timestamp(self, tmp_path):
        from file_tracker import get_environment_delta

        tracking_file = tmp_path / "file-edits.json"
        now = int(time.time())
        entries = [
            {"file": "src/old.ts", "agent": "backend-coder", "tool": "Edit", "ts": now - 100},
            {"file": "src/new.ts", "agent": "backend-coder", "tool": "Edit", "ts": now - 5},
        ]
        tracking_file.write_text(json.dumps(entries))

        delta = get_environment_delta(
            since_ts=now - 50,
            requesting_agent="frontend-coder",
            tracking_path=str(tracking_file),
        )

        assert "src/old.ts" not in delta
        assert "src/new.ts" in delta
