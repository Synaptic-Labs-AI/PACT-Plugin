"""
Tests for hooks/precompact_state_reminder.py — PreCompact hook that reminds
the orchestrator to persist workflow state before compaction.

Tests cover:
1. PreCompact event triggers the reminder
2. systemMessage contains expected guidance text
3. Fail-open on malformed input
4. Fail-open on exceptions
"""
import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "precompact_state_reminder.py")


def run_hook(stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestPrecompactStateReminderOutput:
    """Verify the hook emits the expected systemMessage."""

    def test_emits_reminder_with_valid_input(self):
        result = run_hook(json.dumps({"transcript_path": "/tmp/test.jsonl"}))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output

    def test_reminder_mentions_compaction(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "Compaction" in output["systemMessage"] or "compaction" in output["systemMessage"].lower()

    def test_reminder_mentions_task_update(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "TaskUpdate" in output["systemMessage"]

    def test_reminder_mentions_phase_status(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "phase status" in output["systemMessage"]

    def test_reminder_mentions_active_agents(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "active agents" in output["systemMessage"]

    def test_reminder_mentions_variety_score(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "variety score" in output["systemMessage"]


class TestPrecompactStateReminderFailOpen:
    """Verify fail-open behavior on malformed input and errors."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json at all")
        assert result.returncode == 0

    def test_malformed_json_still_emits_reminder(self):
        """Even with bad input, the reminder should still be emitted."""
        result = run_hook("not json at all")
        output = json.loads(result.stdout.strip())
        assert "systemMessage" in output

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0

    def test_array_input_exits_zero(self):
        result = run_hook("[]")
        assert result.returncode == 0


class TestPrecompactStateReminderUnit:
    """Unit tests for the reminder constant and main function."""

    def test_reminder_message_constant(self):
        from precompact_state_reminder import REMINDER_MESSAGE
        assert "Compaction" in REMINDER_MESSAGE or "compaction" in REMINDER_MESSAGE.lower()
        assert "TaskUpdate" in REMINDER_MESSAGE

    def test_main_exits_zero_always(self):
        """Main function always exits 0 regardless of input."""
        from precompact_state_reminder import main
        # Use subprocess to verify exit code (already covered above)
        # This is a redundant check that the module-level main works
        result = run_hook(json.dumps({"transcript_path": "/nonexistent"}))
        assert result.returncode == 0
