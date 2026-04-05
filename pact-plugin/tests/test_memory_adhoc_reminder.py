"""
Tests for memory_adhoc_reminder.py — Stop hook that emits memory-related
reminders at session end.

Tests cover:
1. get_reminder_type returns "adhoc_save" for substantive ad-hoc work sessions
2. get_reminder_type returns "unprocessed_handoffs" when session journal has agent_handoff events
3. get_reminder_type returns None for trivial sessions (< 500 chars)
4. get_reminder_type returns None when no team_name
5. get_reminder_type returns None when no Edit/Write evidence in transcript
6. get_reminder_type returns None when .adhoc_reminded guard file exists
7. main() emits systemMessage JSON for ad-hoc work sessions
8. main() emits unprocessed_handoffs message for workflow sessions with agent_handoff events
9. main() exits 0 on invalid JSON input
10. main() exits 0 on unexpected errors (fail-silent)
11. main() writes .adhoc_reminded guard file on reminder
12. main() guard file has 0o600 permissions
13. Edge: journal with no agent_handoff events does not trigger unprocessed_handoffs
14. Edge: guard file blocks both unprocessed_handoffs and adhoc_save via main()
15. Integration: main() guard file written for unprocessed_handoffs path too

Uncompleted tasks path (Path 0 — highest priority):
17. find_uncompleted_tasks returns owned in_progress tasks
18. find_uncompleted_tasks ignores completed tasks
19. find_uncompleted_tasks ignores unowned tasks
20. find_uncompleted_tasks returns empty for missing dir
21. find_uncompleted_tasks returns empty for empty team_name
22. find_uncompleted_tasks skips malformed JSON
23. find_uncompleted_tasks fails open on OSError
24. format_uncompleted_message single task
25. format_uncompleted_message multiple tasks
26. get_reminder_type returns uncompleted_tasks over unprocessed_handoffs
27. get_reminder_type returns uncompleted_tasks over adhoc_save
28. guard file blocks uncompleted_tasks path
29. main() emits dynamic uncompleted_tasks message
30. main() uncompleted_tasks writes guard file
31. main() uncompleted_tasks message includes task subjects
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


def _write_task(task_dir, task_id, status, owner=None, subject=None):
    """Helper to write a task JSON file for testing."""
    data = {"status": status}
    if owner:
        data["owner"] = owner
    if subject:
        data["subject"] = subject
    (task_dir / f"{task_id}.json").write_text(json.dumps(data))


def _write_journal_event(teams_dir, event_type="agent_handoff", **fields):
    """Helper to write a journal event to session-journal.jsonl.

    Creates the journal file if it doesn't exist, and appends a single
    JSONL event line. Used to set up journal state for tests.
    """
    event = {"v": 1, "type": event_type, "ts": "2026-01-01T00:00:00Z"}
    event.update(fields)
    journal = teams_dir / "session-journal.jsonl"
    with open(str(journal), "a") as f:
        f.write(json.dumps(event) + "\n")


class TestFindUncompletedTasks:
    """Tests for memory_adhoc_reminder.find_uncompleted_tasks()."""

    def test_returns_owned_in_progress_tasks(self, tmp_path):
        """In_progress task with owner -> returned."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        _write_task(task_dir, "1", "in_progress", owner="backend-coder", subject="Fix API")

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert len(result) == 1
        assert result[0] == {"id": "1", "subject": "Fix API"}

    def test_ignores_completed_tasks(self, tmp_path):
        """Completed task -> not returned."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        _write_task(task_dir, "1", "completed", owner="backend-coder", subject="Fix API")

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert result == []

    def test_ignores_unowned_tasks(self, tmp_path):
        """In_progress task without owner -> not returned (orchestrator tasks)."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        _write_task(task_dir, "1", "in_progress", subject="Feature task")

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert result == []

    def test_returns_empty_for_missing_dir(self, tmp_path):
        """Team dir doesn't exist -> empty list."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        result = find_uncompleted_tasks("no-such-team", tasks_base_dir=str(tmp_path))
        assert result == []

    def test_returns_empty_for_empty_team_name(self, tmp_path):
        """Empty team_name -> early return empty."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        result = find_uncompleted_tasks("", tasks_base_dir=str(tmp_path))
        assert result == []

    def test_skips_malformed_json(self, tmp_path):
        """Malformed JSON file -> skipped, other tasks still returned."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        (task_dir / "bad.json").write_text("not json")
        _write_task(task_dir, "2", "in_progress", owner="coder", subject="Good task")

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert len(result) == 1
        assert result[0]["id"] == "2"

    def test_skips_non_json_files(self, tmp_path):
        """Non-.json files -> ignored."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        (task_dir / "readme.txt").write_text("not a task")
        _write_task(task_dir, "1", "in_progress", owner="coder", subject="Task")

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert len(result) == 1

    def test_multiple_uncompleted_tasks(self, tmp_path):
        """Multiple in_progress owned tasks -> all returned."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        _write_task(task_dir, "1", "in_progress", owner="backend", subject="Task A")
        _write_task(task_dir, "2", "in_progress", owner="frontend", subject="Task B")
        _write_task(task_dir, "3", "completed", owner="test", subject="Task C")

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert len(result) == 2
        ids = {t["id"] for t in result}
        assert ids == {"1", "2"}

    def test_default_subject_when_missing(self, tmp_path):
        """Task without subject field -> defaults to 'unknown'."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)
        data = {"status": "in_progress", "owner": "coder"}
        (task_dir / "1.json").write_text(json.dumps(data))

        result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert result[0]["subject"] == "unknown"

    def test_fails_open_on_iterdir_error(self, tmp_path):
        """OSError during directory scan -> empty list (fail-open)."""
        from memory_adhoc_reminder import find_uncompleted_tasks

        task_dir = tmp_path / "pact-test"
        task_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.iterdir", side_effect=OSError("denied")):
            result = find_uncompleted_tasks("pact-test", tasks_base_dir=str(tmp_path))
        assert result == []


class TestFormatUncompletedMessage:
    """Tests for memory_adhoc_reminder.format_uncompleted_message()."""

    def test_single_task(self):
        """Single task -> '1 task(s)' with subject."""
        from memory_adhoc_reminder import format_uncompleted_message

        msg = format_uncompleted_message([{"id": "1", "subject": "Fix API"}])
        assert "1 task(s)" in msg
        assert "Fix API" in msg
        assert "incomplete HANDOFFs" in msg

    def test_multiple_tasks(self):
        """Multiple tasks -> count and comma-separated subjects."""
        from memory_adhoc_reminder import format_uncompleted_message

        tasks = [
            {"id": "1", "subject": "Fix API"},
            {"id": "2", "subject": "Update DB"},
        ]
        msg = format_uncompleted_message(tasks)
        assert "2 task(s)" in msg
        assert "Fix API" in msg
        assert "Update DB" in msg
        assert "Fix API, Update DB" in msg


class TestGetReminderType:
    """Tests for memory_adhoc_reminder.get_reminder_type()."""

    def test_adhoc_save_for_work_session(self, tmp_path):
        """Substantive work session with no agent_handoff events -> 'adhoc_save'."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "adhoc_save"

    def test_unprocessed_handoffs_when_journal_has_handoffs(self, tmp_path):
        """Session journal with agent_handoff events -> unprocessed HANDOFFs warning."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

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
        """Team dir doesn't exist -> completed_handoffs.jsonl check still works (no crash)."""
        from memory_adhoc_reminder import get_reminder_type

        # Don't create team dir at all
        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        # completed_handoffs.jsonl .exists() returns False, .adhoc_reminded.exists() returns False,
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
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")
        (teams_dir / ".adhoc_reminded").write_text("")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result is None

    def test_unprocessed_handoffs_ignores_transcript_length(self, tmp_path):
        """Journal agent_handoff events trigger unprocessed_handoffs regardless of transcript length."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", "short")

        assert result == "unprocessed_handoffs"

    def test_uncompleted_tasks_highest_priority(self, tmp_path):
        """Uncompleted tasks take priority over agent_handoff events AND adhoc_save."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        # Both journal agent_handoff events AND work transcript exist
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        # Also have uncompleted tasks
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Stuck task")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "uncompleted_tasks"

    def test_uncompleted_tasks_over_adhoc_save(self, tmp_path):
        """Uncompleted tasks take priority over adhoc_save (no completed_handoffs.jsonl)."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Active task")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "uncompleted_tasks"

    def test_guard_file_blocks_uncompleted_tasks(self, tmp_path):
        """Guard file blocks all paths including uncompleted_tasks."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / ".adhoc_reminded").write_text("")

        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Stuck task")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result is None

    def test_falls_through_when_no_uncompleted_tasks(self, tmp_path):
        """No uncompleted tasks + journal agent_handoff events → falls through to unprocessed_handoffs."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        # All tasks completed
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "completed", owner="coder", subject="Done task")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "unprocessed_handoffs"


class TestMain:
    """Tests for memory_adhoc_reminder.main() entry point."""

    def test_emits_adhoc_save_message(self, tmp_path, capsys):
        """Ad-hoc work session -> JSON systemMessage with adhoc_save content, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "outside formal PACT workflows" in output["systemMessage"]
        assert "SendMessage" in output["systemMessage"]

    def test_emits_unprocessed_handoffs_message(self, tmp_path, capsys):
        """Workflow session (journal has agent_handoff events) -> unprocessed HANDOFFs warning, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
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

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_no_output_for_chat_only_session(self, tmp_path, capsys):
        """Long transcript but no Edit/Write -> no reminder, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": CHAT_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_uses_lowercased_team_name(self, tmp_path, capsys):
        """Team name is already lowercased by get_team_name()."""
        from memory_adhoc_reminder import main

        # Create dir with lowercase name
        teams_dir = tmp_path / ".claude" / "teams" / "pact-upper"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-upper"):
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

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
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

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
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

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_no_guard_file_when_no_reminder(self, tmp_path, capsys):
        """When reminder doesn't fire (chat session), no guard file is created."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": CHAT_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        guard = teams_dir / ".adhoc_reminded"
        assert not guard.exists()


class TestEdgeCaseJournalContent:
    """Edge case tests for session journal content checks.

    The get_reminder_type() function calls read_events() to check for
    agent_handoff events. These tests verify behavior with edge-case
    journal content (empty journal, non-handoff events only, etc.).
    """

    def test_empty_journal_does_not_trigger_unprocessed(self, tmp_path):
        """Empty journal file → no agent_handoff events → 'adhoc_save' (not unprocessed).

        Unlike the old completed_handoffs.jsonl check (file existence = signal),
        the journal check is content-based: only agent_handoff events count.
        """
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / "session-journal.jsonl").write_text("")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "adhoc_save"

    def test_journal_with_non_handoff_events_does_not_trigger(self, tmp_path):
        """Journal with only session_start/session_end events → 'adhoc_save'.

        Only agent_handoff events indicate unprocessed work.
        """
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "session_start", team="pact-test")
        _write_journal_event(teams_dir, "session_end")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "adhoc_save"

    def test_journal_with_agent_handoff_triggers_unprocessed(self, tmp_path):
        """Journal with agent_handoff event → 'unprocessed_handoffs'."""
        from memory_adhoc_reminder import get_reminder_type

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "session_start", team="pact-test")
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path):
            result = get_reminder_type("pact-test", WORK_TRANSCRIPT)

        assert result == "unprocessed_handoffs"


class TestMainIntegrationBothPaths:
    """Integration tests verifying main() produces correct systemMessage JSON
    for both reminder paths and that guard file behavior is consistent."""

    def test_unprocessed_handoffs_writes_guard_file(self, tmp_path, capsys):
        """Guard file is written when unprocessed_handoffs reminder fires."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        input_data = json.dumps({"transcript": "short"})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert (teams_dir / ".adhoc_reminded").exists()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "Unprocessed HANDOFFs" in output["systemMessage"]

    def test_guard_blocks_unprocessed_handoffs_via_main(self, tmp_path, capsys):
        """Guard file prevents unprocessed_handoffs reminder in main()."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")
        (teams_dir / ".adhoc_reminded").write_text("")

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_adhoc_save_message_content(self, tmp_path, capsys):
        """Verify adhoc_save message contains actionable content."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        msg = output["systemMessage"]
        # Must mention outside workflows (differentiates from unprocessed path)
        assert "outside formal PACT workflows" in msg
        # Must provide actionable guidance
        assert "SendMessage" in msg

    def test_unprocessed_handoffs_message_content(self, tmp_path, capsys):
        """Verify unprocessed_handoffs message contains actionable content."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        msg = output["systemMessage"]
        # Must mention unprocessed HANDOFFs
        assert "Unprocessed HANDOFFs" in msg
        # Must suggest wrap-up as remediation
        assert "wrap-up" in msg

    def test_journal_handoff_triggers_warning_via_main(self, tmp_path, capsys):
        """Journal with agent_handoff event → unprocessed_handoffs warning via main()."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")

        input_data = json.dumps({"transcript": "short"})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "Unprocessed HANDOFFs" in output["systemMessage"]

    def test_no_team_name_no_crash(self, tmp_path, capsys):
        """Missing team name → no reminder, no crash."""
        from memory_adhoc_reminder import main

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value=""):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}


class TestMainUncompletedTasks:
    """Integration tests for main() with uncompleted_tasks reminder path."""

    def test_emits_uncompleted_tasks_message(self, tmp_path, capsys):
        """Uncompleted tasks → dynamic systemMessage with task subjects, exit 0."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Fix API")

        input_data = json.dumps({"transcript": "short"})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "systemMessage" in output
        assert "1 task(s)" in output["systemMessage"]
        assert "Fix API" in output["systemMessage"]
        assert "incomplete HANDOFFs" in output["systemMessage"]

    def test_uncompleted_tasks_writes_guard_file(self, tmp_path, capsys):
        """Guard file is written when uncompleted_tasks reminder fires."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Fix API")

        input_data = json.dumps({"transcript": "short"})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert (teams_dir / ".adhoc_reminded").exists()

    def test_uncompleted_tasks_multiple_subjects(self, tmp_path, capsys):
        """Multiple uncompleted tasks → message includes all subjects."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="backend", subject="Fix API")
        _write_task(tasks_dir, "6", "in_progress", owner="frontend", subject="Update UI")

        input_data = json.dumps({"transcript": "short"})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "2 task(s)" in output["systemMessage"]
        assert "Fix API" in output["systemMessage"]
        assert "Update UI" in output["systemMessage"]

    def test_uncompleted_tasks_takes_priority_via_main(self, tmp_path, capsys):
        """Uncompleted tasks + journal agent_handoff events → uncompleted_tasks message wins."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        _write_journal_event(teams_dir, "agent_handoff", agent="coder", task_id="1")
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Stuck task")

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        # Must be uncompleted_tasks message, NOT unprocessed_handoffs
        assert "task(s) still in_progress" in output["systemMessage"]
        assert "Unprocessed HANDOFFs" not in output["systemMessage"]

    def test_guard_blocks_uncompleted_tasks_via_main(self, tmp_path, capsys):
        """Guard file prevents uncompleted_tasks reminder in main()."""
        from memory_adhoc_reminder import main

        teams_dir = tmp_path / ".claude" / "teams" / "pact-test"
        teams_dir.mkdir(parents=True)
        (teams_dir / ".adhoc_reminded").write_text("")
        tasks_dir = tmp_path / ".claude" / "tasks" / "pact-test"
        tasks_dir.mkdir(parents=True)
        _write_task(tasks_dir, "5", "in_progress", owner="coder", subject="Stuck task")

        input_data = json.dumps({"transcript": WORK_TRANSCRIPT})

        with patch("memory_adhoc_reminder.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("memory_adhoc_reminder.get_team_name", return_value="pact-test"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}
