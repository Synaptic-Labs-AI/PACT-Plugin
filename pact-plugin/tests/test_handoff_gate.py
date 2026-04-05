"""
Tests for handoff_gate.py — TaskCompleted hook that blocks task completion
if handoff metadata is missing or incomplete.

Tests cover:
1. Complete handoff metadata -> allow (exit 0)
2. Missing metadata.handoff -> block (exit 2)
2a. Missing handoff block message includes concrete copy-paste example
3. Missing required field (e.g., no 'produced') -> block (exit 2)
4. Empty produced list -> block (exit 2)
5. Skipped task (metadata.skipped: true) -> allow (bypass)
6. Blocker task (metadata.type: "blocker") -> allow (bypass)
7. Algedonic task (metadata.type: "algedonic") -> allow (bypass)
8. Subject starts with "BLOCKER:" -> allow (bypass)
9. No teammate_name in input -> allow (non-agent completion)
10. memory_saved blocking enforcement (exit 2)
11. main() integration tests for exit codes and teammate resolution
12. read_task_owner: corrupted JSON returns None gracefully

Note: Tests for append_pending_handoff and completed_handoffs.jsonl were removed
when the session journal replaced the breadcrumb mechanism (Issue #342).
"""
import json
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


VALID_HANDOFF = {
    "produced": ["src/auth.ts"],
    "decisions": ["Used JWT"],
    "uncertainty": [],
    "integration": ["UserService"],
    "open_questions": []
}


class TestHandoffGate:
    """Tests for handoff_gate.validate_task_handoff()."""

    def test_allows_complete_handoff(self):
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="backend-coder"
        )
        assert result is None

    def test_blocks_missing_handoff(self):
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={},
            teammate_name="backend-coder"
        )
        assert result is not None
        assert "handoff" in result.lower()

    def test_missing_handoff_includes_concrete_example(self):
        """Rejection message includes a copy-paste-ready TaskUpdate example."""
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={},
            teammate_name="backend-coder"
        )
        assert result is not None
        # Verify the concrete example is present (not just schema description)
        assert "Example" in result
        assert "YOUR_ID" in result
        assert '"produced": ["file1.py"]' in result
        assert '"decisions": ["chose X because Y"]' in result
        # Verify two-step instruction
        assert 'status="completed"' in result
        # F1: Verify balanced parentheses in example output
        assert result.count("(") == result.count(")"), (
            f"Unbalanced parens: {result.count('(')} open vs {result.count(')')} close"
        )

    def test_blocks_missing_required_field(self):
        from handoff_gate import validate_task_handoff

        incomplete = {k: v for k, v in VALID_HANDOFF.items() if k != "produced"}
        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={"handoff": incomplete},
            teammate_name="backend-coder"
        )
        assert result is not None
        assert "produced" in result

    def test_blocks_empty_produced(self):
        from handoff_gate import validate_task_handoff

        empty_produced = {**VALID_HANDOFF, "produced": []}
        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={"handoff": empty_produced},
            teammate_name="backend-coder"
        )
        assert result is not None

    def test_bypasses_skipped_task(self):
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="PREPARE: research",
            task_metadata={"skipped": True},
            teammate_name="preparer"
        )
        assert result is None

    def test_bypasses_blocker_task(self):
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="BLOCKER: missing API key",
            task_metadata={"type": "blocker"},
            teammate_name="backend-coder"
        )
        assert result is None

    def test_bypasses_algedonic_task(self):
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="HALT: security issue",
            task_metadata={"type": "algedonic"},
            teammate_name="backend-coder"
        )
        assert result is None

    def test_bypasses_no_teammate(self):
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="Feature: auth system",
            task_metadata={},
            teammate_name=None
        )
        assert result is None

    def test_bypasses_alert_subject(self):
        """ALERT: prefix should also bypass validation."""
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="ALERT: quality issue",
            task_metadata={},
            teammate_name="test-engineer"
        )
        assert result is None

    def test_bypasses_halt_subject(self):
        """HALT: prefix should bypass validation."""
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="HALT: data breach",
            task_metadata={},
            teammate_name="backend-coder"
        )
        assert result is None

    def test_blocks_handoff_with_none_value(self):
        """handoff key present but set to None should block."""
        from handoff_gate import validate_task_handoff

        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={"handoff": None},
            teammate_name="backend-coder"
        )
        assert result is not None
        assert "handoff" in result.lower()

    def test_allows_optional_reasoning_chain(self):
        """Optional reasoning_chain field alongside required fields should not interfere."""
        from handoff_gate import validate_task_handoff

        handoff_with_reasoning = {
            **VALID_HANDOFF,
            "reasoning_chain": "Used JWT because stateless auth required"
        }
        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={"handoff": handoff_with_reasoning},
            teammate_name="backend-coder"
        )
        assert result is None

    def test_blocks_missing_required_field_despite_reasoning_chain(self):
        """reasoning_chain presence must not mask a missing required field."""
        from handoff_gate import validate_task_handoff

        incomplete = {k: v for k, v in VALID_HANDOFF.items() if k != "decisions"}
        incomplete["reasoning_chain"] = "Used JWT because stateless auth required"
        result = validate_task_handoff(
            task_subject="CODE: implement auth",
            task_metadata={"handoff": incomplete},
            teammate_name="backend-coder"
        )
        assert result is not None
        assert "decisions" in result


class TestCheckMemorySaved:
    """Tests for handoff_gate.check_memory_saved() — blocking enforcement."""

    def test_no_block_when_memory_saved_true(self):
        """P0: HANDOFF present + memory_saved true -> no block."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF, "memory_saved": True},
            teammate_name="backend-coder",
        )
        assert result is None

    def test_blocks_when_memory_saved_false(self):
        """P0: HANDOFF present + memory_saved false -> blocks completion."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF, "memory_saved": False},
            teammate_name="backend-coder",
        )
        assert result is not None
        assert "Save domain learnings" in result

    def test_blocks_when_memory_saved_absent(self):
        """P0: HANDOFF present + memory_saved absent -> blocks completion."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="backend-coder",
        )
        assert result is not None
        assert "Save domain learnings" in result

    def test_no_block_when_no_teammate(self):
        """P1: Non-agent task -> no block."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name=None,
        )
        assert result is None

    def test_no_block_when_no_handoff(self):
        """P1: No HANDOFF in metadata -> no block (validate_task_handoff handles this)."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={},
            teammate_name="backend-coder",
        )
        assert result is None

    def test_no_block_when_handoff_is_none(self):
        """P1: handoff key present but None -> no block."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": None},
            teammate_name="backend-coder",
        )
        assert result is None

    def test_feedback_contains_agent_name(self):
        """P2: Feedback message includes the teammate name."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="test-engineer",
        )
        assert result is not None
        assert "test-engineer" in result

    def test_feedback_contains_memory_path(self):
        """P2: Feedback message includes the agent-memory path."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="frontend-coder",
        )
        assert result is not None
        assert "~/.claude/agent-memory/frontend-coder/" in result

    def test_feedback_contains_taskupdate_instruction(self):
        """P2: Feedback message tells agent how to set memory_saved."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="backend-coder",
        )
        assert result is not None
        assert "memory_saved" in result
        assert "TaskUpdate" in result

    def test_memory_saved_truthy_values_suppress_block(self):
        """Any truthy value for memory_saved should suppress blocking."""
        from handoff_gate import check_memory_saved

        for truthy in [True, 1, "yes", {"saved": True}]:
            result = check_memory_saved(
                task_metadata={"handoff": VALID_HANDOFF, "memory_saved": truthy},
                teammate_name="backend-coder",
            )
            assert result is None, f"memory_saved={truthy!r} should suppress block"

    def test_memory_saved_falsy_values_trigger_block(self):
        """Falsy values for memory_saved should trigger blocking."""
        from handoff_gate import check_memory_saved

        for falsy in [False, 0, "", None]:
            result = check_memory_saved(
                task_metadata={"handoff": VALID_HANDOFF, "memory_saved": falsy},
                teammate_name="backend-coder",
            )
            assert result is not None, f"memory_saved={falsy!r} should trigger block"


class TestReadTaskMetadata:
    """Tests for handoff_gate.read_task_metadata()."""

    def test_reads_metadata_from_team_dir(self, tmp_path):
        from handoff_gate import read_task_metadata

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        task_data = {
            "subject": "test task",
            "metadata": {"handoff": VALID_HANDOFF}
        }
        (team_dir / "42.json").write_text(json.dumps(task_data))

        result = read_task_metadata("42", "pact-test", tasks_base_dir=str(tmp_path))

        assert "handoff" in result
        assert result["handoff"]["produced"] == ["src/auth.ts"]

    def test_returns_empty_for_missing_task(self, tmp_path):
        from handoff_gate import read_task_metadata

        result = read_task_metadata("999", "pact-test", tasks_base_dir=str(tmp_path))

        assert result == {}

    def test_returns_empty_for_empty_task_id(self):
        from handoff_gate import read_task_metadata

        result = read_task_metadata("", "pact-test")
        assert result == {}

    def test_returns_empty_for_corrupted_json(self, tmp_path):
        from handoff_gate import read_task_metadata

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "42.json").write_text("not valid json{{{")

        result = read_task_metadata("42", "pact-test", tasks_base_dir=str(tmp_path))

        assert result == {}

    def test_sanitizes_path_traversal_in_task_id(self, tmp_path):
        """Adversarial task_id with path traversal should be sanitized to empty -> {}."""
        from handoff_gate import read_task_metadata

        result = read_task_metadata("../../etc/passwd", "pact-test", tasks_base_dir=str(tmp_path))

        assert result == {}

    def test_reads_with_team_name_none(self, tmp_path):
        """team_name=None should skip team dir and fall back to base."""
        from handoff_gate import read_task_metadata

        task_data = {"metadata": {"handoff": VALID_HANDOFF}}
        (tmp_path / "42.json").write_text(json.dumps(task_data))

        result = read_task_metadata("42", None, tasks_base_dir=str(tmp_path))

        assert "handoff" in result
        assert result["handoff"]["produced"] == ["src/auth.ts"]

    def test_falls_back_to_base_dir(self, tmp_path):
        """When team subdirectory doesn't have the task, falls back to base."""
        from handoff_gate import read_task_metadata

        # Put task file in base dir, not team subdir
        task_data = {"metadata": {"handoff": VALID_HANDOFF}}
        (tmp_path / "42.json").write_text(json.dumps(task_data))

        result = read_task_metadata("42", "pact-nonexistent", tasks_base_dir=str(tmp_path))

        assert "handoff" in result


class TestReadTaskOwner:
    """Tests for handoff_gate.read_task_owner()."""

    def test_returns_owner_from_task_file(self, tmp_path):
        from handoff_gate import read_task_owner

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        task_data = {"subject": "test", "owner": "backend-coder", "metadata": {}}
        (team_dir / "42.json").write_text(json.dumps(task_data))

        result = read_task_owner("42", "pact-test", tasks_base_dir=str(tmp_path))

        assert result == "backend-coder"

    def test_returns_none_when_no_owner(self, tmp_path):
        from handoff_gate import read_task_owner

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        task_data = {"subject": "test", "metadata": {}}
        (team_dir / "42.json").write_text(json.dumps(task_data))

        result = read_task_owner("42", "pact-test", tasks_base_dir=str(tmp_path))

        assert result is None

    def test_returns_none_for_missing_task(self, tmp_path):
        from handoff_gate import read_task_owner

        result = read_task_owner("999", "pact-test", tasks_base_dir=str(tmp_path))

        assert result is None

    def test_returns_none_for_empty_task_id(self):
        from handoff_gate import read_task_owner

        result = read_task_owner("", "pact-test")
        assert result is None


class TestMainEntryPoint:
    """Tests for handoff_gate.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_on_valid_handoff_with_memory_saved(self):
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        task_data = {"metadata": {"handoff": VALID_HANDOFF, "memory_saved": True}}
        with patch("handoff_gate._read_task_json", return_value=task_data), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_2_on_missing_handoff(self, capsys):
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate._read_task_json", return_value={}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "handoff" in captured.err.lower()

    def test_main_exits_0_on_invalid_json(self):
        from handoff_gate import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_teammate(self):
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "Feature: auth",
        })

        with patch("handoff_gate._read_task_json", return_value={}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_blocks_when_memory_not_saved(self, capsys):
        """Integration: valid handoff but no memory_saved -> exit 2 + feedback on stderr."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        task_data = {"metadata": {"handoff": VALID_HANDOFF}}
        with patch("handoff_gate._read_task_json", return_value=task_data), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "backend-coder" in captured.err
        assert "agent-memory" in captured.err
        assert "memory_saved" in captured.err
        # No JSON on stdout
        assert captured.out == ""

    def test_main_no_block_when_memory_saved(self, capsys):
        """Integration: valid handoff + memory_saved=true -> exit 0, suppressOutput."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        task_data = {"metadata": {"handoff": VALID_HANDOFF, "memory_saved": True}}
        with patch("handoff_gate._read_task_json", return_value=task_data), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}
        assert captured.err == ""

    def test_main_no_memory_block_when_handoff_blocked(self, capsys):
        """Integration: missing handoff -> exit 2 (blocked), no memory feedback on stdout."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate._read_task_json", return_value={}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        # Error on stderr, nothing on stdout (memory check doesn't fire when handoff blocked)
        assert captured.out == ""
        assert "handoff" in captured.err.lower()

    def test_main_uses_context_file_for_team_name(self, pact_context):
        """team_name falls back to pact context file when absent from input."""
        pact_context(team_name="pact-from-context")
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            # No team_name in input
        })

        task_data = {"metadata": {"handoff": VALID_HANDOFF, "memory_saved": True}}
        with patch("handoff_gate._read_task_json", return_value=task_data) as mock_read, \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Verify _read_task_json was called with context file team name
        mock_read.assert_called_once_with("1", "pact-from-context")

    def test_main_uses_task_owner_as_primary_teammate(self, capsys):
        """Task file owner is primary; platform teammate_name is ignored when owner exists."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "platform-name",
            "team_name": "pact-test"
        })

        task_data = {
            "owner": "task-owner",
            "metadata": {"handoff": VALID_HANDOFF, "memory_saved": True},
        }
        with patch("handoff_gate._read_task_json", return_value=task_data), \
             patch("handoff_gate.append_event", return_value=True) as mock_append, \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Journal event uses task owner, not platform teammate_name
        mock_append.assert_called_once()
        event = mock_append.call_args[0][0]
        assert event["agent"] == "task-owner"

    def test_main_falls_back_to_platform_teammate_when_no_owner(self, capsys):
        """No owner in task file -> platform teammate_name used as fallback."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        task_data = {"metadata": {"handoff": VALID_HANDOFF, "memory_saved": True}}
        with patch("handoff_gate._read_task_json", return_value=task_data), \
             patch("handoff_gate.append_event", return_value=True) as mock_append, \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Journal event uses platform teammate_name as fallback
        mock_append.assert_called_once()
        event = mock_append.call_args[0][0]
        assert event["agent"] == "backend-coder"

    def test_main_bypasses_when_no_owner_and_no_teammate(self):
        """No owner + no teammate_name → genuine non-agent task, exit 0."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "Feature: auth",
            "team_name": "pact-test"
        })

        with patch("handoff_gate._read_task_json", return_value={}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


class TestReadTaskOwnerCorruptedJson:
    """read_task_owner returns None gracefully on corrupted JSON."""

    def test_corrupted_json_returns_none(self, tmp_path):
        """Corrupted JSON in task file -> returns None (not crash)."""
        from handoff_gate import read_task_owner

        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "42.json").write_text("{corrupted json!!!")

        result = read_task_owner("42", "pact-test", tasks_base_dir=str(tmp_path))

        assert result is None
