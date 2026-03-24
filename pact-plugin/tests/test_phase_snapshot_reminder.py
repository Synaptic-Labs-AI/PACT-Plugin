"""
Tests for hooks/phase_snapshot_reminder.py — PostToolUse hook on TaskUpdate that
emits a reminder when a PACT phase task is marked completed.

Tests cover:
1. _read_task_subject reads task files from disk
2. check_phase_completion integrates disk read with phase keyword detection
3. Phase task completion triggers reminder (each phase keyword)
4. Non-phase task completion does NOT trigger
5. Status != "completed" does NOT trigger
6. Fail-open on malformed input and exceptions (subprocess)
"""
import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from phase_snapshot_reminder import (
    PHASE_KEYWORDS,
    REMINDER_MESSAGE,
    _read_task_subject,
    check_phase_completion,
)

HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "phase_snapshot_reminder.py")


def _create_task_file(
    tasks_dir: Path,
    team_name: str,
    task_id: str,
    subject: str,
) -> Path:
    """Create a task JSON file on disk for testing.

    Task files live at {tasks_dir}/{team_name}/{task_id}.json.
    Returns the path to the created file.
    """
    team_dir = tasks_dir / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    task_file = team_dir / f"{task_id}.json"
    task_file.write_text(
        json.dumps({"subject": subject, "status": "completed"}),
        encoding="utf-8",
    )
    return task_file


# =============================================================================
# _read_task_subject: disk-read behavior
# =============================================================================


class TestReadTaskSubject:
    """Verify _read_task_subject scans task directories correctly."""

    def test_reads_subject_from_task_file(self, tmp_path):
        _create_task_file(tmp_path, "pact-abc123", "42", "CODE: backend")
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result == "CODE: backend"

    def test_scans_multiple_team_directories(self, tmp_path):
        _create_task_file(tmp_path, "pact-team-a", "10", "unrelated task")
        _create_task_file(tmp_path, "pact-team-b", "42", "PREPARE: research")
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result == "PREPARE: research"

    def test_returns_none_for_missing_task(self, tmp_path):
        _create_task_file(tmp_path, "pact-abc123", "99", "some task")
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result is None

    def test_returns_none_for_empty_task_id(self, tmp_path):
        result = _read_task_subject("", tasks_base_dir=str(tmp_path))
        assert result is None

    def test_returns_none_for_nonexistent_base_dir(self):
        result = _read_task_subject("42", tasks_base_dir="/nonexistent/path")
        assert result is None

    def test_returns_empty_string_when_subject_missing(self, tmp_path):
        """Task file exists but has no subject field."""
        team_dir = tmp_path / "pact-abc123"
        team_dir.mkdir()
        task_file = team_dir / "42.json"
        task_file.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result == ""

    def test_handles_malformed_json_gracefully(self, tmp_path):
        """Malformed task file is skipped, returns None."""
        team_dir = tmp_path / "pact-abc123"
        team_dir.mkdir()
        task_file = team_dir / "42.json"
        task_file.write_text("not valid json", encoding="utf-8")
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result is None

    def test_skips_non_directory_entries(self, tmp_path):
        """Files in the tasks base dir (not directories) are skipped."""
        (tmp_path / "stray-file.txt").write_text("hello")
        _create_task_file(tmp_path, "pact-abc123", "42", "TEST: coverage")
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result == "TEST: coverage"

    def test_returns_none_for_empty_tasks_dir(self, tmp_path):
        """No team directories at all."""
        result = _read_task_subject("42", tasks_base_dir=str(tmp_path))
        assert result is None


# =============================================================================
# check_phase_completion: integration of disk read + phase detection
# =============================================================================


class TestCheckPhaseCompletion:
    """Unit tests for check_phase_completion with disk-backed task files."""

    @pytest.mark.parametrize("phase", ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"])
    def test_completed_phase_returns_true(self, tmp_path, phase):
        _create_task_file(tmp_path, "pact-team", "42", f"{phase} auth implementation")
        result = check_phase_completion(
            {"taskId": "42", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is True

    def test_non_completed_status_returns_false(self, tmp_path):
        _create_task_file(tmp_path, "pact-team", "42", "CODE: backend")
        result = check_phase_completion(
            {"taskId": "42", "status": "in_progress"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_non_phase_subject_returns_false(self, tmp_path):
        _create_task_file(tmp_path, "pact-team", "42", "Fix backend bug")
        result = check_phase_completion(
            {"taskId": "42", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_missing_task_id_returns_false(self, tmp_path):
        result = check_phase_completion(
            {"status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_empty_dict_returns_false(self, tmp_path):
        result = check_phase_completion({}, tasks_base_dir=str(tmp_path))
        assert result is False

    def test_missing_status_returns_false(self, tmp_path):
        _create_task_file(tmp_path, "pact-team", "42", "PREPARE: research")
        result = check_phase_completion(
            {"taskId": "42"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_task_not_on_disk_returns_false(self, tmp_path):
        """Task ID exists in tool_input but no matching file on disk."""
        result = check_phase_completion(
            {"taskId": "999", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_phase_keyword_in_middle_of_subject(self, tmp_path):
        _create_task_file(
            tmp_path, "pact-team", "42", "Feature: CODE: backend implementation"
        )
        result = check_phase_completion(
            {"taskId": "42", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is True

    def test_lowercase_phase_keyword_returns_false(self, tmp_path):
        """Phase keywords are case-sensitive (uppercase with colon)."""
        _create_task_file(tmp_path, "pact-team", "42", "prepare: research")
        result = check_phase_completion(
            {"taskId": "42", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_auditor_task_returns_false(self, tmp_path):
        _create_task_file(tmp_path, "pact-team", "42", "auditor observation")
        result = check_phase_completion(
            {"taskId": "42", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False

    def test_empty_subject_on_disk_returns_false(self, tmp_path):
        """Task file exists but subject is empty string."""
        _create_task_file(tmp_path, "pact-team", "42", "")
        result = check_phase_completion(
            {"taskId": "42", "status": "completed"},
            tasks_base_dir=str(tmp_path),
        )
        assert result is False


# =============================================================================
# Phase completion triggers reminder (output content)
# =============================================================================


class TestPhaseCompletionTriggerContent:
    """Verify reminder message content for phase completions."""

    def test_reminder_mentions_key_decisions(self):
        assert "key decisions" in REMINDER_MESSAGE

    def test_reminder_mentions_scope_or_assumption_changes(self):
        assert "scope or assumption" in REMINDER_MESSAGE

    def test_reminder_mentions_specialist_findings(self):
        assert "specialist findings" in REMINDER_MESSAGE

    def test_reminder_mentions_task_update(self):
        assert "TaskUpdate" in REMINDER_MESSAGE


# =============================================================================
# Fail-open behavior (subprocess tests — no disk I/O needed)
# =============================================================================


def run_hook(stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestPhaseSnapshotFailOpen:
    """Verify fail-open behavior on malformed input and errors."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json")
        assert result.returncode == 0

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0

    def test_missing_tool_input_exits_zero(self):
        result = run_hook(json.dumps({"tool_name": "TaskUpdate"}))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_status_exits_zero(self):
        """tool_input without status field — hook reads disk but finds no match."""
        result = run_hook(json.dumps({
            "tool_input": {"taskId": "nonexistent-999"},
        }))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_task_id_exits_zero(self):
        """tool_input with status but no taskId — returns early."""
        result = run_hook(json.dumps({
            "tool_input": {"status": "completed"},
        }))
        assert result.returncode == 0
        assert result.stdout.strip() == ""
