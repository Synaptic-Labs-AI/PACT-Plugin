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
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
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
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
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
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "missing HANDOFF metadata" in captured.err
        assert "TaskUpdate" in captured.err
        assert "produced" in captured.err

    def test_allows_when_no_tasks(self, tmp_path, capsys):
        """P0: Empty task directory → exit 0."""
        from teammate_completion_gate import main

        self._make_task_dir(tmp_path)

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_allows_when_no_teammate_name(self, tmp_path):
        """P1: Missing teammate_name → exit 0."""
        from teammate_completion_gate import main

        input_data = json.dumps({"team_name": "pact-test"})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_allows_when_no_team_name(self, tmp_path):
        """P1: Missing team_name (env and input) → exit 0."""
        from teammate_completion_gate import main

        input_data = json.dumps({"teammate_name": "backend-coder"})
        env = {}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=True):
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
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
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
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "#5" in captured.err
        assert "#8" in captured.err
        assert "2 tasks" in captured.err

    def test_team_name_from_env_fallback(self, tmp_path, capsys):
        """P1: team_name not in input but in env var → works correctly."""
        from teammate_completion_gate import main

        task_dir = self._make_task_dir(tmp_path)
        _make_task_file(task_dir, "5", "backend-coder", "in_progress",
                        {"handoff": VALID_HANDOFF})

        # No team_name in input
        input_data = json.dumps({"teammate_name": "backend-coder"})
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
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
        env = {"CLAUDE_CODE_TEAM_NAME": "pact-test"}

        with patch("teammate_completion_gate.Path.home", return_value=tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, env, clear=False):
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

        with patch("teammate_completion_gate.find_completable_tasks",
                    side_effect=RuntimeError("unexpected")), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ, {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
