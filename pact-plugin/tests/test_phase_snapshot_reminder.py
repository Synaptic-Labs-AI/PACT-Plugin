"""
Tests for hooks/phase_snapshot_reminder.py — PostToolUse hook on TaskUpdate that
emits a reminder when a PACT phase task is marked completed.

Tests cover:
1. Phase task completion triggers reminder (each phase keyword)
2. Non-phase task completion does NOT trigger
3. Status != "completed" does NOT trigger
4. Fail-open on malformed input and exceptions
5. check_phase_completion unit tests
"""
import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "phase_snapshot_reminder.py")


def run_hook(stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
    )


def _make_input(subject: str, status: str = "completed") -> str:
    """Create a valid hook input JSON string."""
    return json.dumps({
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "42",
            "subject": subject,
            "status": status,
        },
        "tool_output": {},
    })


# =============================================================================
# Phase completion triggers reminder
# =============================================================================


class TestPhaseCompletionTrigger:
    """Verify each phase keyword triggers the reminder."""

    @pytest.mark.parametrize("phase", ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"])
    def test_phase_keyword_triggers_reminder(self, phase):
        result = run_hook(_make_input(f"{phase} auth implementation"))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output

    @pytest.mark.parametrize("phase", ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"])
    def test_reminder_mentions_organizational_state(self, phase):
        result = run_hook(_make_input(f"{phase} task"))
        output = json.loads(result.stdout.strip())
        assert "organizational state" in output["systemMessage"].lower()

    @pytest.mark.parametrize("phase", ["PREPARE:", "ARCHITECT:", "CODE:", "TEST:"])
    def test_reminder_mentions_active_agents(self, phase):
        result = run_hook(_make_input(f"{phase} task"))
        output = json.loads(result.stdout.strip())
        assert "active agents" in output["systemMessage"]

    def test_phase_keyword_in_middle_of_subject(self):
        result = run_hook(_make_input("Feature: CODE: backend implementation"))
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output


# =============================================================================
# Non-phase tasks do NOT trigger
# =============================================================================


class TestNonPhaseTasks:
    """Verify non-phase task completions do not trigger the reminder."""

    def test_regular_task_no_reminder(self):
        result = run_hook(_make_input("Fix backend bug"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_auditor_task_no_reminder(self):
        result = run_hook(_make_input("auditor observation"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_empty_subject_no_reminder(self):
        result = run_hook(_make_input(""))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_lowercase_phase_keyword_no_reminder(self):
        """Phase keywords are case-sensitive (uppercase with colon)."""
        result = run_hook(_make_input("prepare: research"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# =============================================================================
# Status != "completed" does NOT trigger
# =============================================================================


class TestNonCompletedStatus:
    """Verify only 'completed' status triggers the reminder."""

    def test_in_progress_no_reminder(self):
        result = run_hook(_make_input("PREPARE: research", status="in_progress"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_pending_no_reminder(self):
        result = run_hook(_make_input("CODE: implementation", status="pending"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_deleted_no_reminder(self):
        result = run_hook(_make_input("TEST: coverage", status="deleted"))
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# =============================================================================
# Fail-open behavior
# =============================================================================


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
        result = run_hook(json.dumps({
            "tool_input": {"subject": "PREPARE: research"},
        }))
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# =============================================================================
# Unit tests for check_phase_completion
# =============================================================================


class TestCheckPhaseCompletion:
    """Unit tests for the check_phase_completion function."""

    def test_completed_prepare_returns_true(self):
        from phase_snapshot_reminder import check_phase_completion
        assert check_phase_completion({"status": "completed", "subject": "PREPARE: research"})

    def test_completed_architect_returns_true(self):
        from phase_snapshot_reminder import check_phase_completion
        assert check_phase_completion({"status": "completed", "subject": "ARCHITECT: design"})

    def test_completed_code_returns_true(self):
        from phase_snapshot_reminder import check_phase_completion
        assert check_phase_completion({"status": "completed", "subject": "CODE: backend"})

    def test_completed_test_returns_true(self):
        from phase_snapshot_reminder import check_phase_completion
        assert check_phase_completion({"status": "completed", "subject": "TEST: coverage"})

    def test_non_completed_returns_false(self):
        from phase_snapshot_reminder import check_phase_completion
        assert not check_phase_completion({"status": "in_progress", "subject": "CODE: backend"})

    def test_non_phase_returns_false(self):
        from phase_snapshot_reminder import check_phase_completion
        assert not check_phase_completion({"status": "completed", "subject": "Fix bug"})

    def test_empty_dict_returns_false(self):
        from phase_snapshot_reminder import check_phase_completion
        assert not check_phase_completion({})

    def test_missing_subject_returns_false(self):
        from phase_snapshot_reminder import check_phase_completion
        assert not check_phase_completion({"status": "completed"})

    def test_missing_status_returns_false(self):
        from phase_snapshot_reminder import check_phase_completion
        assert not check_phase_completion({"subject": "PREPARE: research"})
