"""
Tests for memory_adhoc_reminder.py — Stop hook that emits memory-related
reminders at session end.

Tests cover:
1. get_reminder_type returns "adhoc_save" for substantive ad-hoc work sessions
2. get_reminder_type returns "unprocessed_handoffs" when breadcrumb file exists
3. get_reminder_type returns None for trivial sessions (< 500 chars)
4. get_reminder_type returns None when no team_name
5. get_reminder_type returns None when no Edit/Write evidence in transcript
6. get_reminder_type returns None when .adhoc_reminded guard file exists
7. main() emits systemMessage JSON for ad-hoc work sessions
8. main() emits unprocessed_handoffs message for workflow sessions with breadcrumbs
9. main() exits 0 on invalid JSON input
10. main() exits 0 on unexpected errors (fail-silent)
11. main() writes .adhoc_reminded guard file on reminder
12. main() guard file has 0o600 permissions
"""
import json
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# Transcript that meets both conditions: >= 500 chars AND contains quoted "Edit"/"Write" tool names
WORK_TRANSCRIPT = "Some discussion about the feature... " + "x" * 450 + ' "Edit" the file...'
CHAT_TRANSCRIPT = "x" * 600  # Long but no "Edit"/"Write" evidence


class TestGetReminderType:
    """Tests for memory_adhoc_reminder.get_reminder_type()."""

    def test_adhoc_save_for_work_session(self, tmp_path):
        """Substantive work session with no breadcrumb -> 'adhoc_save'."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "adhoc_save"

    def test_unprocessed_handoffs_when_breadcrumb_exists(self, tmp_path):
        """Breadcrumb file exists -> unprocessed HANDOFFs warning."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "completed_handoffs.jsonl").write_text('{"task_id": "1"}\n')

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "unprocessed_handoffs"

    def test_none_for_trivial_session(self, tmp_path):
        """Short transcript (< 500 chars) -> no reminder."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", 'short session with "Edit"')

        assert result is None

    def test_none_when_no_team_name(self, tmp_path):
        """No team_name -> no session context -> None."""
        from memory_adhoc_reminder import get_reminder_type

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("", WORK_TRANSCRIPT)

        assert result is None

    def test_none_for_empty_transcript(self, tmp_path):
        """Empty transcript -> trivial -> None."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", "")

        assert result is None

    def test_none_at_boundary_499_chars(self, tmp_path):
        """Exactly 499 chars -> below threshold -> None."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", '"Edit" ' + "x" * 492)

        assert result is None

    def test_adhoc_save_at_boundary_500_chars_with_edit(self, tmp_path):
        """Exactly 500 chars with quoted "Edit" evidence -> at threshold -> 'adhoc_save'."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", '"Edit" ' + "x" * 493)

        assert result == "adhoc_save"

    def test_adhoc_save_when_team_dir_missing(self, tmp_path):
        """Team dir doesn't exist -> breadcrumb check still works (no crash)."""
        from memory_adhoc_reminder import get_reminder_type

        # Don't create team dir at all
        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        # breadcrumb.exists() returns False, .adhoc_reminded.exists() returns False,
        # transcript is long enough and has quoted "Edit" evidence -> "adhoc_save"
        assert result == "adhoc_save"

    def test_none_for_chat_only_session(self, tmp_path):
        """Long transcript but no Edit/Write -> chat session -> None."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", CHAT_TRANSCRIPT)

        assert result is None

    def test_adhoc_save_with_write_evidence(self, tmp_path):
        """Transcript with quoted "Write" (not "Edit") evidence -> 'adhoc_save'."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        transcript = "Discussing the feature... " + "x" * 470 + ' "Write" the config...'
        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", transcript)

        assert result == "adhoc_save"

    def test_none_when_guard_file_exists(self, tmp_path):
        """Guard file .adhoc_reminded exists -> already reminded -> None."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / ".adhoc_reminded").write_text("")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result is None

    def test_guard_file_blocks_unprocessed_handoffs_too(self, tmp_path):
        """Guard file blocks even unprocessed_handoffs path."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "completed_handoffs.jsonl").write_text('{"task_id": "1"}\n')
        (teams_dir / ".adhoc_reminded").write_text("")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result is None

    def test_unprocessed_handoffs_ignores_transcript_length(self, tmp_path):
        """Breadcrumbs trigger unprocessed_handoffs regardless of transcript length."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "completed_handoffs.jsonl").write_text('{"task_id": "1"}\n')

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", "short")

        assert result == "unprocessed_handoffs"


class TestMain:
    """Tests for memory_adhoc_reminder.main() entry point."""

    def test_emits_adhoc_save_message(self, tmp_path, capsys):
        """Ad-hoc work session -> JSON systemMessage with adhoc_save content, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "outside formal PACT workflows" in output["systemMessage"]
        assert "SendMessage" in output["systemMessage"]

    def test_emits_unprocessed_handoffs_message(self, tmp_path, capsys):
        """Workflow session (breadcrumb exists) -> unprocessed HANDOFFs warning, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "completed_handoffs.jsonl").write_text('{"task_id": "1"}\n')

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "Unprocessed HANDOFFs" in output["systemMessage"]
        assert "wrap-up" in output["systemMessage"]

    def test_exits_0_on_invalid_json(self):
        """Invalid JSON input -> exit 0, no crash."""
        from memory_adhoc_reminder import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_exits_0_on_unexpected_error(self, capsys):
        """Unexpected error -> exit 0 (fail-silent)."""
        from memory_adhoc_reminder import main

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
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_no_output_for_chat_only_session(self, tmp_path, capsys):
        """Long transcript but no Edit/Write -> no reminder, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": CHAT_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
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

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "PACT-UPPER"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output

    def test_guard_file_written_on_reminder(self, tmp_path, capsys):
        """When reminder fires, .adhoc_reminded guard file is created."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        guard = teams_dir / ".adhoc_reminded"
        assert guard.exists()

    def test_guard_file_permissions_0o600(self, tmp_path, capsys):
        """Guard file has 0o600 permissions."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        guard = teams_dir / ".adhoc_reminded"
        mode = os.stat(guard).st_mode & 0o777
        assert mode == 0o600

    def test_no_reminder_when_guard_file_exists(self, tmp_path, capsys):
        """Guard file already exists -> no reminder, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / ".adhoc_reminded").write_text("")

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_no_guard_file_when_no_reminder(self, tmp_path, capsys):
        """When reminder doesn't fire (chat session), no guard file is created."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": CHAT_TRANSCRIPT})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        guard = teams_dir / ".adhoc_reminded"
        assert not guard.exists()
