"""
Tests for handoff_gate.py — TaskCompleted hook that blocks task completion
if handoff metadata is missing or incomplete.

Tests cover:
1. Complete handoff metadata -> allow (exit 0)
2. Missing metadata.handoff -> block (exit 2)
3. Missing required field (e.g., no 'produced') -> block (exit 2)
4. Empty produced list -> block (exit 2)
5. Skipped task (metadata.skipped: true) -> allow (bypass)
6. Blocker task (metadata.type: "blocker") -> allow (bypass)
7. Algedonic task (metadata.type: "algedonic") -> allow (bypass)
8. Subject starts with "BLOCKER:" -> allow (bypass)
9. No teammate_name in input -> allow (non-agent completion)
10. memory_saved enforcement nudge (non-blocking)
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
    """Tests for handoff_gate.check_memory_saved() — non-blocking nudge."""

    def test_no_nudge_when_memory_saved_true(self):
        """P0: HANDOFF present + memory_saved true -> no nudge."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF, "memory_saved": True},
            teammate_name="backend-coder",
        )
        assert result is None

    def test_nudge_when_memory_saved_false(self):
        """P0: HANDOFF present + memory_saved false -> nudge fires."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF, "memory_saved": False},
            teammate_name="backend-coder",
        )
        assert result is not None
        assert "Save domain learnings" in result

    def test_nudge_when_memory_saved_absent(self):
        """P0: HANDOFF present + memory_saved absent -> nudge fires."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="backend-coder",
        )
        assert result is not None
        assert "Save domain learnings" in result

    def test_no_nudge_when_no_teammate(self):
        """P1: Non-agent task -> no nudge."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name=None,
        )
        assert result is None

    def test_no_nudge_when_no_handoff(self):
        """P1: No HANDOFF in metadata -> no nudge (validate_task_handoff handles this)."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={},
            teammate_name="backend-coder",
        )
        assert result is None

    def test_no_nudge_when_handoff_is_none(self):
        """P1: handoff key present but None -> no nudge."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": None},
            teammate_name="backend-coder",
        )
        assert result is None

    def test_nudge_contains_agent_name(self):
        """P2: Nudge message includes the teammate name."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="test-engineer",
        )
        assert "test-engineer" in result

    def test_nudge_contains_memory_path(self):
        """P2: Nudge message includes the agent-memory path."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="frontend-coder",
        )
        assert "~/.claude/agent-memory/frontend-coder/" in result

    def test_nudge_contains_taskupdate_instruction(self):
        """P2: Nudge message tells agent how to set memory_saved."""
        from handoff_gate import check_memory_saved

        result = check_memory_saved(
            task_metadata={"handoff": VALID_HANDOFF},
            teammate_name="backend-coder",
        )
        assert "memory_saved" in result
        assert "TaskUpdate" in result

    def test_memory_saved_truthy_values_suppress_nudge(self):
        """Any truthy value for memory_saved should suppress the nudge."""
        from handoff_gate import check_memory_saved

        for truthy in [True, 1, "yes", {"saved": True}]:
            result = check_memory_saved(
                task_metadata={"handoff": VALID_HANDOFF, "memory_saved": truthy},
                teammate_name="backend-coder",
            )
            assert result is None, f"memory_saved={truthy!r} should suppress nudge"

    def test_memory_saved_falsy_values_trigger_nudge(self):
        """Falsy values for memory_saved should trigger the nudge."""
        from handoff_gate import check_memory_saved

        for falsy in [False, 0, "", None]:
            result = check_memory_saved(
                task_metadata={"handoff": VALID_HANDOFF, "memory_saved": falsy},
                teammate_name="backend-coder",
            )
            assert result is not None, f"memory_saved={falsy!r} should trigger nudge"


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

    def test_falls_back_to_base_dir(self, tmp_path):
        """When team subdirectory doesn't have the task, falls back to base."""
        from handoff_gate import read_task_metadata

        # Put task file in base dir, not team subdir
        task_data = {"metadata": {"handoff": VALID_HANDOFF}}
        (tmp_path / "42.json").write_text(json.dumps(task_data))

        result = read_task_metadata("42", "pact-nonexistent", tasks_base_dir=str(tmp_path))

        assert "handoff" in result


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

        metadata = {"handoff": VALID_HANDOFF, "memory_saved": True}
        with patch("handoff_gate.read_task_metadata", return_value=metadata), \
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

        with patch("handoff_gate.read_task_metadata", return_value={}), \
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

        with patch("handoff_gate.read_task_metadata", return_value={}), \
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

        with patch("handoff_gate.read_task_metadata", return_value={"handoff": VALID_HANDOFF}), \
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

    def test_main_no_nudge_when_memory_saved(self, capsys):
        """Integration: valid handoff + memory_saved=true -> exit 0, no stdout output."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        metadata = {"handoff": VALID_HANDOFF, "memory_saved": True}
        with patch("handoff_gate.read_task_metadata", return_value=metadata), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_main_no_nudge_when_handoff_blocked(self, capsys):
        """Integration: missing handoff -> exit 2 (blocked), no nudge on stdout."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata", return_value={}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        # Error on stderr, nothing on stdout (nudge doesn't fire when handoff blocked)
        assert captured.out == ""
        assert "handoff" in captured.err.lower()
