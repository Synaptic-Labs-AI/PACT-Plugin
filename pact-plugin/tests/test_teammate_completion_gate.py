"""
Tests for teammate_completion_gate.py — TeammateIdle hook that blocks agents
from going idle when they own in_progress tasks with HANDOFF metadata, or
when they have in_progress tasks with NO handoff metadata (safety net for
agents stuck looping on handoff_gate rejection).

Tests cover:
1. Agent with in_progress task + HANDOFF → blocked (exit 2)
2. Agent with completed tasks → allowed (exit 0)
3. find_completable_tasks: in_progress without HANDOFF → not in completable list
4. Agent with no tasks → allowed (exit 0)
5. Multiple in_progress tasks with HANDOFF → lists all in feedback
6. Malformed task files → fail-open (allow idle)
7. Missing team directory → allow idle
8. No teammate_name → allow idle
9. No team_name → allow idle
10. Invalid JSON input → allow idle (exit 0)
11. Mixed tasks: some completable, some still working → only lists completable
12. Task owned by different agent → not included
13. Integration: main() exit codes
14. Integration: main() stderr feedback content
15. find_missing_handoff_tasks: finds in_progress tasks without handoff
16. format_missing_handoff_feedback: concrete example in feedback
17. Integration: idle agent with no handoff → blocked with guidance (exit 2)
"""
import json
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


def _make_task_file(task_dir, task_id, owner, status, metadata=None):
    """Helper: create a task file in the given directory."""
    task_data = {
        "id": str(task_id),
        "subject": f"Task {task_id}",
        "status": status,
        "owner": owner,
        "metadata": metadata or {},
    }
    task_file = task_dir / f"{task_id}.json"
    task_file.write_text(json.dumps(task_data), encoding="utf-8")
    return task_file


VALID_HANDOFF = {
    "produced": ["file.py"],
    "decisions": ["used pattern X"],
    "uncertainty": [{"level": "LOW", "description": "minor"}],
    "integration": ["integrates with Y"],
    "open_questions": [],
}


class TestFindCompletableTasks:
    """Tests for teammate_completion_gate.find_completable_tasks()."""

    def _make_task_dir(self, tmp_path, team_name="pact-test"):
        task_dir = tmp_path / ".claude" / "tasks" / team_name
        task_dir.mkdir(parents=True)
        return task_dir

    def test_in_progress_with_handoff_is_completable(self, tmp_path):
        """P0: in_progress + HANDOFF → found as completable."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF, "memory_saved": True})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 1
        assert result[0]["id"] == "5"
        assert result[0]["subject"] == "Task 5"

    def test_completed_task_not_flagged(self, tmp_path):
        """P0: completed task → not flagged as completable."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "completed",
                        {"handoff": VALID_HANDOFF})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_in_progress_without_handoff_allowed(self, tmp_path):
        """P0: in_progress but no HANDOFF → agent still working, not flagged."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_no_tasks_returns_empty(self, tmp_path):
        """P0: empty task directory → empty list."""
        from teammate_completion_gate import find_completable_tasks

        self._make_task_dir(tmp_path)

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_multiple_completable_tasks(self, tmp_path):
        """P0: Multiple in_progress tasks with HANDOFF → all listed."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})
        _make_task_file(task_dir, "8", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"5", "8"}

    def test_malformed_task_file_skipped(self, tmp_path):
        """P1: Malformed JSON in task file → skipped, no crash."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        (task_dir / "5.json").write_text("not valid json")
        _make_task_file(task_dir, "6", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 1
        assert result[0]["id"] == "6"

    def test_missing_team_directory_returns_empty(self, tmp_path):
        """P1: Team directory doesn't exist → empty list, no crash."""
        from teammate_completion_gate import find_completable_tasks

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_empty_teammate_name_returns_empty(self, tmp_path):
        """P1: No teammate_name → empty list."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        result = find_completable_tasks(
            "", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_empty_team_name_returns_empty(self, tmp_path):
        """P1: No team_name → empty list."""
        from teammate_completion_gate import find_completable_tasks

        result = find_completable_tasks(
            "backend-coder", "",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_different_owner_not_included(self, tmp_path):
        """P0: Task owned by different agent → not flagged."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "frontend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_mixed_tasks_only_completable_listed(self, tmp_path):
        """P0: Mix of completable, still-working, completed, other-owner tasks."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        # Completable: in_progress + HANDOFF
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})
        # Still working: in_progress, no HANDOFF
        _make_task_file(task_dir, "6", "backend-coder", "in_progress", {})
        # Already completed
        _make_task_file(task_dir, "7", "backend-coder", "completed",
                        {"handoff": VALID_HANDOFF})
        # Different owner
        _make_task_file(task_dir, "8", "frontend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})
        # Pending (not started)
        _make_task_file(task_dir, "9", "backend-coder", "pending", {})

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 1
        assert result[0]["id"] == "5"

    def test_non_json_files_ignored(self, tmp_path):
        """P1: Non-.json files in task dir are ignored."""
        from teammate_completion_gate import find_completable_tasks

        task_dir = self._make_task_dir(tmp_path)
        (task_dir / "readme.txt").write_text("not a task")
        (task_dir / ".hidden").write_text("{}")

        result = find_completable_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_os_error_during_scan_returns_empty(self, tmp_path):
        """P1: OSError during directory scan → fail-open, empty list."""
        from teammate_completion_gate import find_completable_tasks

        self._make_task_dir(tmp_path)

        with patch("teammate_completion_gate.Path.iterdir",
                    side_effect=OSError("permission denied")):
            result = find_completable_tasks(
                "backend-coder", "pact-test",
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )
        assert len(result) == 0


class TestFormatFeedback:
    """Tests for teammate_completion_gate.format_feedback()."""

    def test_single_task_message(self):
        """Single completable task → includes task ID and TaskUpdate command."""
        from teammate_completion_gate import format_feedback

        msg = format_feedback([{"id": "5", "subject": "CODE: auth"}])
        assert "#5" in msg
        assert "CODE: auth" in msg
        assert 'TaskUpdate(taskId="5", status="completed")' in msg

    def test_multiple_tasks_message(self):
        """Multiple completable tasks → lists all IDs."""
        from teammate_completion_gate import format_feedback

        msg = format_feedback([
            {"id": "5", "subject": "CODE: auth"},
            {"id": "8", "subject": "CODE: api"},
        ])
        assert "#5" in msg
        assert "#8" in msg
        assert "2 tasks" in msg


class TestFindMissingHandoffTasks:
    """Tests for teammate_completion_gate.find_missing_handoff_tasks()."""

    def _make_task_dir(self, tmp_path, team_name="pact-test"):
        task_dir = tmp_path / ".claude" / "tasks" / team_name
        task_dir.mkdir(parents=True)
        return task_dir

    def test_finds_in_progress_without_handoff(self, tmp_path):
        """P0: in_progress + no HANDOFF → found as missing."""
        from teammate_completion_gate import find_missing_handoff_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {})

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 1
        assert result[0]["id"] == "5"

    def test_excludes_tasks_with_handoff(self, tmp_path):
        """P0: in_progress + HANDOFF present → not in missing list."""
        from teammate_completion_gate import find_missing_handoff_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_excludes_completed_tasks(self, tmp_path):
        """P0: completed task without handoff → not flagged."""
        from teammate_completion_gate import find_missing_handoff_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "completed", {})

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_excludes_different_owner(self, tmp_path):
        """P0: Task owned by different agent → not included."""
        from teammate_completion_gate import find_missing_handoff_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "frontend-coder", "in_progress", {})

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_empty_teammate_returns_empty(self, tmp_path):
        """P1: No teammate_name → empty list."""
        from teammate_completion_gate import find_missing_handoff_tasks

        result = find_missing_handoff_tasks(
            "", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_missing_team_directory_returns_empty(self, tmp_path):
        """P1: Team directory doesn't exist → empty list."""
        from teammate_completion_gate import find_missing_handoff_tasks

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0

    def test_multiple_missing_tasks(self, tmp_path):
        """P0: Multiple in_progress tasks without handoff → all listed."""
        from teammate_completion_gate import find_missing_handoff_tasks

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {})
        _make_task_file(task_dir, "8", "backend-coder", "in_progress", {})

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 2
        ids = {r["id"] for r in result}
        assert ids == {"5", "8"}

    def test_malformed_task_file_skipped(self, tmp_path):
        """P1: Malformed JSON in task file → skipped, no crash."""
        from teammate_completion_gate import find_missing_handoff_tasks

        task_dir = self._make_task_dir(tmp_path)
        (task_dir / "5.json").write_text("not valid json")
        _make_task_file(task_dir, "6", "backend-coder", "in_progress", {})

        result = find_missing_handoff_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 1
        assert result[0]["id"] == "6"

    def test_os_error_during_scan_returns_empty(self, tmp_path):
        """P1: OSError during directory scan → fail-open, empty list."""
        from teammate_completion_gate import find_missing_handoff_tasks

        self._make_task_dir(tmp_path)

        with patch("teammate_completion_gate.Path.iterdir",
                    side_effect=OSError("permission denied")):
            result = find_missing_handoff_tasks(
                "backend-coder", "pact-test",
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )
        assert len(result) == 0

    def test_none_team_name_returns_empty(self, tmp_path):
        """P1: None team_name → empty list."""
        from teammate_completion_gate import find_missing_handoff_tasks

        result = find_missing_handoff_tasks(
            "backend-coder", None,
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(result) == 0


class TestFormatMissingHandoffFeedback:
    """Tests for teammate_completion_gate.format_missing_handoff_feedback()."""

    def test_single_task_contains_example(self):
        """P0: Single task → feedback includes concrete TaskUpdate example."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([{"id": "5", "subject": "CODE: auth"}])
        assert "#5" in msg
        assert "CODE: auth" in msg
        assert "produced" in msg
        assert "decisions" in msg
        assert 'TaskUpdate(taskId="5"' in msg

    def test_single_task_contains_two_step_instruction(self):
        """P0: Feedback instructs two-step process: metadata first, then complete."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([{"id": "5", "subject": "CODE: auth"}])
        assert "metadata" in msg
        assert 'status="completed"' in msg

    def test_multiple_tasks_lists_all(self):
        """P0: Multiple tasks → all listed in feedback."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([
            {"id": "5", "subject": "CODE: auth"},
            {"id": "8", "subject": "CODE: api"},
        ])
        assert "#5" in msg
        assert "#8" in msg

    def test_feedback_mentions_missing_handoff(self):
        """P0: Feedback clearly states the problem is missing HANDOFF."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([{"id": "5", "subject": "CODE: auth"}])
        assert "missing HANDOFF metadata" in msg

    def test_example_has_balanced_parens(self):
        """F1: Example output must have matching parentheses."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([{"id": "5", "subject": "CODE: auth"}])
        assert msg.count("(") == msg.count(")"), (
            f"Unbalanced parens: {msg.count('(')} open vs {msg.count(')')} close"
        )

    # --- Signal-type completion feedback ---

    def test_signal_type_all_missing_produces_audit_summary_guidance(self):
        """Signal-type tasks get audit_summary-specific feedback."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([
            {"id": "42", "subject": "auditor observation", "completion_type": "signal"},
        ])
        assert "audit_summary" in msg
        assert "GREEN|YELLOW|RED" in msg
        assert 'TaskUpdate(taskId="42"' in msg
        # Should NOT mention HANDOFF metadata
        assert "missing HANDOFF metadata" not in msg

    def test_signal_type_multiple_all_signal_produces_audit_guidance(self):
        """Multiple signal-type tasks all get audit_summary guidance."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([
            {"id": "42", "subject": "auditor obs 1", "completion_type": "signal"},
            {"id": "43", "subject": "auditor obs 2", "completion_type": "signal"},
        ])
        assert "audit_summary" in msg
        assert "#42" in msg
        assert "#43" in msg

    def test_mixed_signal_and_handoff_provides_both_guidance(self):
        """Mixed signal + handoff tasks get type-specific guidance for each."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([
            {"id": "42", "subject": "auditor observation", "completion_type": "signal"},
            {"id": "5", "subject": "CODE: auth", "completion_type": "handoff"},
        ])
        # Mixed case provides guidance for both types
        assert "audit_summary" in msg  # signal-type guidance
        assert "missing HANDOFF metadata" in msg  # handoff-type guidance
        assert "#42" in msg
        assert "#5" in msg

    def test_signal_type_feedback_has_balanced_parens(self):
        """Signal-type feedback must have matching parentheses."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([
            {"id": "42", "subject": "auditor observation", "completion_type": "signal"},
        ])
        assert msg.count("(") == msg.count(")"), (
            f"Unbalanced parens in signal feedback: "
            f"{msg.count('(')} open vs {msg.count(')')} close"
        )

    def test_no_completion_type_defaults_to_handoff_path(self):
        """Tasks without completion_type use the handoff template."""
        from teammate_completion_gate import format_missing_handoff_feedback

        msg = format_missing_handoff_feedback([
            {"id": "5", "subject": "CODE: auth"},
        ])
        assert "missing HANDOFF metadata" in msg
        assert "audit_summary" not in msg


class TestMain:
    """Integration tests for teammate_completion_gate.main()."""

    def _make_task_dir(self, tmp_path, team_name="pact-test"):
        task_dir = tmp_path / ".claude" / "tasks" / team_name
        task_dir.mkdir(parents=True)
        return task_dir

    def test_blocks_when_completable_task_exists(self, tmp_path, capsys):
        """P0: Completable task found → exit 2 with stderr feedback."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "#5" in captured.err
        assert "TaskUpdate" in captured.err

    def test_allows_when_no_completable_tasks(self, tmp_path, capsys):
        """P0: No completable tasks → exit 0, no stderr."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "completed",
                        {"handoff": VALID_HANDOFF})

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_blocks_when_idle_with_missing_handoff(self, tmp_path, capsys):
        """P0: in_progress but no HANDOFF + agent idle → exit 2 with guidance.

        An idle agent with in_progress tasks but no HANDOFF metadata is stuck
        (likely looping on handoff_gate rejection). The safety net blocks idle
        and provides a concrete example to help the agent self-correct.
        """
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {})

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "missing HANDOFF metadata" in captured.err
        assert "TaskUpdate" in captured.err
        assert "produced" in captured.err

    def test_blocks_signal_type_with_audit_summary(self, tmp_path, capsys):
        """Signal-type task with audit_summary → exit 2 (completable)."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "9", "backend-coder", "in_progress", {
            "completion_type": "signal",
            "audit_summary": "Signal: GREEN\nCoverage: 85%",
        })

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "#9" in captured.err
        assert "TaskUpdate" in captured.err

    def test_blocks_signal_type_missing_audit_summary(self, tmp_path, capsys):
        """Signal-type task without audit_summary → exit 2 with audit guidance."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "9", "backend-coder", "in_progress", {
            "completion_type": "signal",
        })

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "audit_summary" in captured.err

    def test_unrecognized_completion_type_warns_and_falls_through(
        self, tmp_path, capsys,
    ):
        """Unrecognized completion_type emits stderr warning and falls through
        to handoff behavior. Without handoff metadata → missing_handoff → exit 2."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "11", "backend-coder", "in_progress", {
            "completion_type": "unknown_type",
        })

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        # Verify the warning message was emitted
        assert "unrecognized completion_type" in captured.err
        assert "'unknown_type'" in captured.err
        assert "falling through to handoff" in captured.err
        # Verify it still gives missing-completion guidance (falls through to handoff path)
        assert "missing completion artifacts" in captured.err

    def test_allows_when_no_tasks(self, tmp_path, capsys):
        """P0: Empty task directory → exit 0."""
        from teammate_completion_gate import main

        self._make_task_dir(tmp_path)

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_allows_when_no_teammate_name(self, tmp_path):
        """P1: Missing teammate_name → exit 0."""
        from teammate_completion_gate import main

        input_data = json.dumps({"team_name": "pact-test"})
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_allows_when_no_team_name(self, tmp_path, pact_context):
        """P1: Missing team_name (context file and input) → exit 0."""
        pact_context(team_name="")  # No team_name available
        from teammate_completion_gate import main

        input_data = json.dumps({"teammate_name": "backend-coder"})

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_allows_on_invalid_json_input(self):
        """P1: Invalid JSON input → exit 0, no crash."""
        from teammate_completion_gate import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_allows_on_missing_task_directory(self, tmp_path):
        """P1: Team task directory doesn't exist → exit 0."""
        from teammate_completion_gate import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_blocks_multiple_completable_tasks(self, tmp_path, capsys):
        """P0: Multiple completable tasks → exit 2 with all listed in feedback."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})
        _make_task_file(task_dir, "8", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "#5" in captured.err
        assert "#8" in captured.err
        assert "2 tasks" in captured.err

    def test_team_name_from_context_fallback(self, tmp_path, capsys, pact_context):
        """P1: team_name not in input but in context file → works correctly."""
        pact_context(team_name="pact-test")
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        # No team_name in input — falls back to pact context file
        input_data = json.dumps({"teammate_name": "backend-coder"})
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2

    def test_allows_when_tasks_owned_by_different_teammate(self, tmp_path, capsys):
        """P0: Teammate A goes idle but all completable tasks belong to
        teammate B → exit 0 (A has nothing to complete)."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        # Task owned by frontend-coder, NOT backend-coder
        _make_task_file(task_dir, "5", "frontend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})
        _make_task_file(task_dir, "8", "frontend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        # backend-coder goes idle
        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_fail_open_on_unexpected_exception(self, tmp_path):
        """P1: Unexpected exception → exit 0 (fail-open, not trapped)."""
        from teammate_completion_gate import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })

        with patch("teammate_completion_gate._scan_owned_tasks",
                    side_effect=RuntimeError("unexpected")), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


# =============================================================================
# #497 — _scan_owned_tasks honors metadata.intentional_wait
# =============================================================================

from datetime import datetime, timedelta, timezone


def _iso_seconds(dt):
    return dt.isoformat(timespec="seconds")


def _fresh_wait_payload(reason="awaiting_teachback_approved",
                       resolver="lead",
                       since_offset_seconds=-60):
    return {
        "reason": reason,
        "expected_resolver": resolver,
        "since": _iso_seconds(
            datetime.now(timezone.utc) + timedelta(seconds=since_offset_seconds)
        ),
    }


def _stale_wait_payload(minutes=60):
    return {
        "reason": "awaiting_teachback_approved",
        "expected_resolver": "lead",
        "since": _iso_seconds(datetime.now(timezone.utc) - timedelta(minutes=minutes)),
    }


def _make_task_dir(tmp_path, team_name="pact-test"):
    task_dir = tmp_path / ".claude" / "tasks" / team_name
    task_dir.mkdir(parents=True)
    return task_dir


class TestIntentionalWaitCompletionGatePredicate:
    """_scan_owned_tasks honors a fresh intentional_wait in BOTH branches:
    completable (has HANDOFF) and missing_handoff (no HANDOFF).

    Plan rows 13-16. Suppression in BOTH branches is load-bearing because
    the root livelock path is the missing-handoff branch (teammate waiting
    on teachback_approved BEFORE producing HANDOFF) — but the completable
    branch must also suppress for symmetric reasons (teammate waiting on
    lead commit AFTER producing HANDOFF).
    """

    def test_fresh_wait_suppresses_completable_branch(self, tmp_path):
        """Row 13: in_progress + HANDOFF + fresh intentional_wait -> NOT completable.

        Typical shape: teammate finished work, stored HANDOFF, and is now
        waiting for lead commit before calling TaskUpdate(status=completed).
        """
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "handoff": VALID_HANDOFF,
            "intentional_wait": _fresh_wait_payload(
                reason="awaiting_lead_commit",
                resolver="lead",
            ),
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []

    def test_fresh_wait_suppresses_missing_handoff_branch(self, tmp_path):
        """Row 14: in_progress + no HANDOFF + fresh intentional_wait -> NOT missing.

        Typical shape: teammate has sent teachback and is waiting on approval
        before starting implementation work. No HANDOFF yet, but the nag would
        livelock the wait.
        """
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "intentional_wait": _fresh_wait_payload(),
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []

    def test_stale_wait_re_surfaces_completable(self, tmp_path):
        """Row 15a: stale intentional_wait + HANDOFF -> completable re-appears."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "handoff": VALID_HANDOFF,
            "intentional_wait": _stale_wait_payload(minutes=60),
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(completable) == 1
        assert completable[0]["id"] == "5"

    def test_stale_wait_re_surfaces_missing(self, tmp_path):
        """Row 15b: stale intentional_wait + no HANDOFF -> missing re-appears."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "intentional_wait": _stale_wait_payload(minutes=60),
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(missing) == 1
        assert missing[0]["id"] == "5"

    def test_missing_wait_completes_normally(self, tmp_path):
        """Row 16: no intentional_wait -> pre-fix behavior unchanged."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(completable) == 1

    def test_malformed_wait_fails_loud(self, tmp_path):
        """Malformed intentional_wait -> nag path re-enables (fail-loud)."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "handoff": VALID_HANDOFF,
            "intentional_wait": {"reason": "x"},  # missing resolver + since
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(completable) == 1, (
            "Malformed flag must NOT silently suppress — nag re-enables"
        )

    def test_none_wait_value_treated_as_absent(self, tmp_path):
        """intentional_wait explicitly None behaves like the key is absent."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "handoff": VALID_HANDOFF,
            "intentional_wait": None,  # explicit cleared state
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(completable) == 1, (
            "intentional_wait=None is the explicit-cleared form; must not silence"
        )

    def test_signal_type_audit_task_with_fresh_wait_suppresses(self, tmp_path):
        """Row 13 variant: signal-type task (auditor) + fresh wait still suppresses.

        Signal-type has its own completion_type branch in _scan_owned_tasks
        but the intentional_wait skip runs BEFORE that branch — so signal
        tasks get the same suppression treatment.
        """
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "42", "auditor", "in_progress", {
            "completion_type": "signal",
            "audit_summary": {"signal": "GREEN", "findings": []},
            "intentional_wait": _fresh_wait_payload(resolver="lead"),
        })
        completable, missing = _scan_owned_tasks(
            "auditor", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []

    def test_only_in_progress_tasks_checked(self, tmp_path):
        """completed + fresh wait -> already filtered by status check; wait
        not even consulted."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "completed", {
            "handoff": VALID_HANDOFF,
            "intentional_wait": _fresh_wait_payload(),
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []


class TestIntentionalWaitCompletionGateCardinality:
    """Parametrized cardinality pin for counter-test-by-revert.

    Reverting the intentional_wait skip in _scan_owned_tasks flips the fresh
    rows (2 of 5) RED. Stale/malformed/missing rows stay GREEN either way
    (because they are already expected to surface).
    """

    @pytest.mark.parametrize("wait_payload,handoff_present,expected_completable,expected_missing", [
        # Fresh wait suppresses BOTH branches
        ("fresh", True, 0, 0),
        ("fresh", False, 0, 0),
        # Stale wait does not suppress
        ("stale", True, 1, 0),
        ("stale", False, 0, 1),
        # Missing wait -> unchanged pre-fix behavior
        ("absent", True, 1, 0),
        ("absent", False, 0, 1),
    ])
    def test_branch_matrix(self, tmp_path, wait_payload, handoff_present,
                           expected_completable, expected_missing):
        from teammate_completion_gate import _scan_owned_tasks

        metadata = {}
        if handoff_present:
            metadata["handoff"] = VALID_HANDOFF
        if wait_payload == "fresh":
            metadata["intentional_wait"] = _fresh_wait_payload()
        elif wait_payload == "stale":
            metadata["intentional_wait"] = _stale_wait_payload()
        # "absent" -> no key

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", metadata)

        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert len(completable) == expected_completable, (
            f"wait={wait_payload}, handoff={handoff_present} "
            f"-> completable expected {expected_completable}"
        )
        assert len(missing) == expected_missing, (
            f"wait={wait_payload}, handoff={handoff_present} "
            f"-> missing expected {expected_missing}"
        )


class TestIntentionalWaitCompletionGateMain:
    """Row 17 (hook integration at main-entry level): main() exits 0 with
    suppressOutput for a fresh intentional_wait on a missing-handoff task.

    This is the load-bearing path for the livelock — the teammate is idle
    without HANDOFF during a teachback wait; pre-fix main() would exit 2
    and nag.
    """

    def test_main_exits_0_for_fresh_wait_missing_handoff(self, tmp_path, capsys):
        from teammate_completion_gate import main

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "intentional_wait": _fresh_wait_payload(),
        })

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_completion_gate.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0, (
            "Fresh intentional_wait must allow idle (exit 0), not block"
        )
        captured = capsys.readouterr()
        # stderr should NOT contain block feedback
        assert "HANDOFF" not in captured.err
        assert "completed" not in captured.err

    def test_main_exits_2_for_stale_wait_missing_handoff(self, tmp_path, capsys):
        """Stale wait -> nag path re-enables via the missing-handoff branch."""
        from teammate_completion_gate import main

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "intentional_wait": _stale_wait_payload(),
        })

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_completion_gate.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "HANDOFF" in captured.err

    def test_main_exits_0_for_fresh_wait_with_handoff(self, tmp_path, capsys):
        """Completable branch: fresh wait + HANDOFF -> still suppress."""
        from teammate_completion_gate import main

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "handoff": VALID_HANDOFF,
            "intentional_wait": _fresh_wait_payload(reason="awaiting_lead_commit"),
        })

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_completion_gate.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


class TestIntentionalWaitAC11BlockedByStillNags:
    """Row 23: blockedBy without intentional_wait still nags.

    Documents the empirical NO-GO for Option 4 (blockedBy hook-awareness)
    as a standalone fix. Preparer verified that neither TeammateIdle hook
    reads blockedBy — so a teammate whose task is blocked but lacks an
    intentional_wait flag still triggers the nag. This test pins that
    behavior as a regression guard: if someone adds blockedBy awareness
    later, they must also consider intentional_wait interaction.
    """

    def test_blockedby_alone_does_not_silence(self, tmp_path):
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        # blockedBy set but no intentional_wait flag
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "blockedBy": ["3"],
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        # No HANDOFF + no wait flag -> surfaces as missing (nag path)
        assert len(missing) == 1, (
            "AC #11: blockedBy without intentional_wait must still nag — "
            "Option 4 is deferred as standalone fix. Add intentional_wait "
            "to skip."
        )

    def test_blockedby_with_fresh_wait_silences(self, tmp_path):
        """The fix (Option 1): blockedBy + fresh intentional_wait -> silenced."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {
            "blockedBy": ["3"],
            "intentional_wait": _fresh_wait_payload(
                reason="awaiting_blocker_resolution",
                resolver="peer",
            ),
        })
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert missing == []


class TestIntentionalWaitSharedThresholdContract:
    """Row 27: both hooks import wait_stale from the same shared module.

    Regression guard against duplicate-constant drift. If someone later
    inlines the threshold into one hook, the two hooks could diverge on
    staleness cutoff — this test pins that they import the same symbol.
    """

    def test_both_hooks_share_wait_stale_import(self):
        """Same wait_stale object reached via both hook modules' import graphs."""
        import teammate_idle
        import teammate_completion_gate

        # Both modules import the same function object
        assert teammate_idle.wait_stale is teammate_completion_gate.wait_stale, (
            "Row 27: hooks must share a single wait_stale — divergence creates "
            "inconsistent staleness semantics across the parallel-fired hooks"
        )

    def test_shared_default_threshold_is_30(self):
        """DEFAULT_THRESHOLD_MINUTES remains 30 (AC #1)."""
        from shared.intentional_wait import DEFAULT_THRESHOLD_MINUTES
        assert DEFAULT_THRESHOLD_MINUTES == 30


class TestCompletionGateAsymmetryFix:
    """Rows 28-30: completion_gate mirror-adds the type + stalled skips that
    teammate_idle.py::detect_stall already honored (L122, L124).

    Root cause preparer surfaced: _scan_owned_tasks nagged on tasks that
    detect_stall silently skipped — cross-hook silencer asymmetry. These
    tests pin the fix as load-bearing.
    """

    def test_type_blocker_is_skipped(self, tmp_path):
        """Row 28: metadata.type='blocker' -> skip (no completable, no missing)."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        # No HANDOFF; pre-asymmetry-fix would surface this as missing_handoff
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"type": "blocker"})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == [], "blocker-type task must not surface as completable"
        assert missing == [], "blocker-type task must not surface as missing_handoff"

    def test_type_algedonic_is_skipped(self, tmp_path):
        """Row 29: metadata.type='algedonic' -> skip."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"type": "algedonic"})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []

    def test_stalled_true_is_skipped(self, tmp_path):
        """Row 30: metadata.stalled=true -> skip."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"stalled": True})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []

    def test_stalled_true_with_handoff_also_skipped(self, tmp_path):
        """Row 30 variant: stalled=true silences even when HANDOFF is present."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"stalled": True, "handoff": VALID_HANDOFF})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        assert completable == []
        assert missing == []

    def test_type_other_random_string_not_skipped(self, tmp_path):
        """Guardrail: only 'blocker' and 'algedonic' are skip-types.

        Arbitrary type values (including unrecognized ones) must NOT be
        treated as silencers; otherwise a typo could silently disable
        the gate.
        """
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"type": "handoff", "handoff": VALID_HANDOFF})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        # type='handoff' is not a silencer; handoff is present -> completable
        assert len(completable) == 1

    def test_stalled_false_not_skipped(self, tmp_path):
        """stalled=False (explicit) must not silence — only truthy value does."""
        from teammate_completion_gate import _scan_owned_tasks

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"stalled": False})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        # stalled=False -> task surfaces as missing_handoff (no HANDOFF, no wait)
        assert len(missing) == 1


class TestDualHookParityAllFourSkips:
    """Row 31: dual-hook parity across all four metadata-keyed skips.

    Parametrized: for each of {type=blocker, type=algedonic, stalled=true,
    intentional_wait=fresh}, both hooks must produce identical
    suppress decisions. This is the structural enforcement of AC #6 at
    the behavioral level — if one hook diverges from the other, a
    teammate's idle event will see asymmetric nagging across the parallel
    hook fires.

    The test intentionally covers ONLY the silencing cases (fresh wait)
    and the always-silence cases (blocker/algedonic/stalled); the parity
    for non-silencing cases is implicit (both will nag).
    """

    @pytest.mark.parametrize("metadata_key,metadata_value", [
        ("type_blocker", {"type": "blocker"}),
        ("type_algedonic", {"type": "algedonic"}),
        ("stalled_true", {"stalled": True}),
        ("intentional_wait_fresh", {"intentional_wait": _fresh_wait_payload()}),
    ])
    def test_both_hooks_silence_for_metadata_skip(self, tmp_path,
                                                   metadata_key, metadata_value):
        """Both TeammateIdle hooks must suppress for each metadata-keyed skip."""
        # completion_gate path
        from teammate_completion_gate import _scan_owned_tasks
        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        metadata_value)
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        cg_silenced = (completable == [] and missing == [])

        # idle hook path (via detect_stall on in-memory tasks)
        from teammate_idle import detect_stall
        tasks = [{
            "id": "5",
            "subject": "Task 5",
            "status": "in_progress",
            "owner": "backend-coder",
            "metadata": metadata_value,
        }]
        idle_silenced = detect_stall(tasks, "backend-coder") is None

        assert cg_silenced == idle_silenced, (
            f"Dual-hook parity failure for {metadata_key}: "
            f"completion_gate silenced={cg_silenced}, "
            f"detect_stall silenced={idle_silenced}. AC #6 requires "
            f"both hooks to produce identical suppress decisions for each "
            f"metadata-keyed skip; divergence re-creates the cross-hook "
            f"silencer asymmetry that #497 fixes."
        )
        # And specifically: they must BOTH silence
        assert cg_silenced, (
            f"{metadata_key} must silence completion_gate"
        )
        assert idle_silenced, (
            f"{metadata_key} must silence detect_stall"
        )

    def test_nag_path_also_symmetric(self, tmp_path):
        """Complement: missing-wait tasks nag in both hooks."""
        from teammate_completion_gate import _scan_owned_tasks
        from teammate_idle import detect_stall

        task_dir = _make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress", {})
        completable, missing = _scan_owned_tasks(
            "backend-coder", "pact-test",
            tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
        )
        tasks = [{
            "id": "5",
            "subject": "Task 5",
            "status": "in_progress",
            "owner": "backend-coder",
            "metadata": {},
        }]
        assert len(missing) == 1, "completion_gate must surface missing-handoff"
        assert detect_stall(tasks, "backend-coder") is not None, (
            "detect_stall must fire stall"
        )
