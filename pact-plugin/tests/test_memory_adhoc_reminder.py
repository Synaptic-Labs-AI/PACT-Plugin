"""
Tests for memory_adhoc_reminder.py — Stop hook that reminds about memory
saves for ad-hoc sessions (no formal PACT workflow ran).

Tests cover:
1. should_remind returns True for substantive ad-hoc sessions
2. should_remind returns False when breadcrumb file exists (workflow ran)
3. should_remind returns False for trivial sessions (< 500 chars)
4. should_remind returns False when no team_name
5. main() emits systemMessage JSON for ad-hoc sessions
6. main() emits nothing for workflow sessions
7. main() exits 0 on invalid JSON input
8. main() exits 0 when PACT_STOP_HOOK_ACTIVE is set (reentrancy guard)
9. main() exits 0 on unexpected errors (fail-silent)
"""
import json
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestShouldRemind:
    """Tests for memory_adhoc_reminder.should_remind()."""

    def test_true_for_adhoc_session(self, tmp_path):
        """Substantive session with no breadcrumb -> True."""
        from memory_adhoc_reminder import should_remind

        # Create team dir but no breadcrumb file
        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "x" * 500)

        assert result is True

    def test_false_when_breadcrumb_exists(self, tmp_path):
        """Breadcrumb file exists -> workflow handled memory -> False."""
        from memory_adhoc_reminder import should_remind

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "completed_handoffs.jsonl").write_text('{"task_id": "1"}\n')

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "x" * 500)

        assert result is False

    def test_false_for_trivial_session(self, tmp_path):
        """Short transcript (< 500 chars) -> trivial session -> False."""
        from memory_adhoc_reminder import should_remind

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "short session")

        assert result is False

    def test_false_when_no_team_name(self, tmp_path):
        """No team_name -> no session context -> False."""
        from memory_adhoc_reminder import should_remind

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("", "x" * 500)

        assert result is False

    def test_false_for_empty_transcript(self, tmp_path):
        """Empty transcript -> trivial -> False."""
        from memory_adhoc_reminder import should_remind

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "")

        assert result is False

    def test_false_at_boundary_499_chars(self, tmp_path):
        """Exactly 499 chars -> below threshold -> False."""
        from memory_adhoc_reminder import should_remind

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "x" * 499)

        assert result is False

    def test_true_at_boundary_500_chars(self, tmp_path):
        """Exactly 500 chars -> at threshold -> True."""
        from memory_adhoc_reminder import should_remind

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "x" * 500)

        assert result is True

    def test_false_when_team_dir_missing(self, tmp_path):
        """Team dir doesn't exist -> breadcrumb check still works (no crash)."""
        from memory_adhoc_reminder import should_remind

        # Don't create team dir at all
        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = should_remind("pact-test", "x" * 500)

        # breadcrumb.exists() returns False, transcript is long enough -> True
        assert result is True


class TestMain:
    """Tests for memory_adhoc_reminder.main() entry point."""

    def test_emits_system_message_for_adhoc(self, tmp_path, capsys):
        """Ad-hoc session -> JSON systemMessage on stdout, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": "x" * 600})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            # Remove reentrancy guard if set from prior test
            os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "memory agent" in output["systemMessage"]
        assert "SendMessage" in output["systemMessage"]

    def test_silent_for_workflow_session(self, tmp_path, capsys):
        """Workflow session (breadcrumb exists) -> no output, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "completed_handoffs.jsonl").write_text('{"task_id": "1"}\n')

        input_data = json.dumps({"transcript": "x" * 600})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_exits_0_on_invalid_json(self):
        """Invalid JSON input -> exit 0, no crash."""
        from memory_adhoc_reminder import main

        with patch("sys.stdin", io.StringIO("not json")):
            os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_exits_0_when_reentrancy_guard_set(self, capsys):
        """PACT_STOP_HOOK_ACTIVE set -> exit 0 immediately, no output."""
        from memory_adhoc_reminder import main

        os.environ["PACT_STOP_HOOK_ACTIVE"] = "1"
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            assert captured.out == ""
        finally:
            os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)

    def test_exits_0_on_unexpected_error(self, capsys):
        """Unexpected error -> exit 0 (fail-silent)."""
        from memory_adhoc_reminder import main

        os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)

        with patch("sys.stdin", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_no_output_for_trivial_session(self, tmp_path, capsys):
        """Short transcript -> no reminder, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": "hello"})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_uses_lowercased_team_name(self, tmp_path, capsys):
        """Team name from env is lowercased for path lookup."""
        from memory_adhoc_reminder import main

        # Create dir with lowercase name
        teams_dir = tmp_path / ".claude" / "teams" / "pact-upper"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": "x" * 600})
        env = {"CLAUDE_CODE_TEAM_NAME": "PACT-UPPER"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            os.environ.pop("PACT_STOP_HOOK_ACTIVE", None)
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
