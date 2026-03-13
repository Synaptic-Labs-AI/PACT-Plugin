"""
Tests for handoff_gate.py — TaskCompleted hook that blocks task completion
if handoff metadata is missing or incomplete, and nudges the orchestrator
to create a deferred save task when memory_saved metadata is absent for
PACT work agents.

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
10. is_pact_work_agent identifies PACT agents correctly
11. check_memory_metadata nudges when memory_saved is missing for PACT agents
12. main() emits action-required nudge on stderr without blocking (exit 0)
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

    def test_main_exits_0_on_valid_handoff(self):
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

    def test_main_exits_0_with_action_required_for_pact_agent(self, capsys):
        """PACT agent with valid handoff but no memory_saved -> exit 0 + action nudge on stderr."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "pact-backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata", return_value={"handoff": VALID_HANDOFF}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "ACTION REQUIRED" in captured.err
        assert "pact-backend-coder" in captured.err
        assert "task #1" in captured.err

    def test_main_exits_0_no_nudge_when_memory_saved(self, capsys):
        """PACT agent with valid handoff + memory_saved: true -> exit 0, no nudge."""
        from handoff_gate import main

        metadata = {"handoff": VALID_HANDOFF, "memory_saved": True}
        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: auth",
            "teammate_name": "pact-backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata", return_value=metadata), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "ACTION REQUIRED" not in captured.err


class TestIsPactWorkAgent:
    """Tests for handoff_gate.is_pact_work_agent()."""

    def test_recognizes_pact_backend_coder(self):
        from handoff_gate import is_pact_work_agent

        assert is_pact_work_agent("pact-backend-coder") is True

    def test_recognizes_scope_suffixed_agent(self):
        """Scope-prefixed names like pact-backend-coder-auth-scope should match."""
        from handoff_gate import is_pact_work_agent

        assert is_pact_work_agent("pact-backend-coder-auth-scope") is True

    def test_rejects_non_pact_agent(self):
        from handoff_gate import is_pact_work_agent

        assert is_pact_work_agent("random-agent") is False

    def test_rejects_empty_string(self):
        from handoff_gate import is_pact_work_agent

        assert is_pact_work_agent("") is False

    def test_rejects_none(self):
        from handoff_gate import is_pact_work_agent

        assert is_pact_work_agent(None) is False

    def test_rejects_false_positive_prefix(self):
        """Name containing but not starting with a PACT agent name should not match."""
        from handoff_gate import is_pact_work_agent

        assert is_pact_work_agent("not-pact-backend-coder") is False


class TestCheckMemoryMetadata:
    """Tests for handoff_gate.check_memory_metadata()."""

    def test_no_nudge_when_memory_saved_true(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={"memory_saved": True},
            teammate_name="pact-backend-coder"
        )
        assert result is None

    def test_nudges_when_memory_saved_absent(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="pact-backend-coder",
            task_id="42"
        )
        assert result is not None
        assert "ACTION REQUIRED" in result
        assert "pact-backend-coder" in result
        assert "task #42" in result

    def test_nudges_when_memory_saved_false(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={"memory_saved": False},
            teammate_name="pact-backend-coder"
        )
        assert result is not None
        assert "ACTION REQUIRED" in result

    def test_no_warning_for_non_pact_agent(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="random-agent"
        )
        assert result is None

    def test_no_warning_when_no_teammate(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name=None
        )
        assert result is None

    def test_bypasses_skipped_task(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={"skipped": True},
            teammate_name="pact-backend-coder"
        )
        assert result is None

    def test_bypasses_blocker_type(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={"type": "blocker"},
            teammate_name="pact-backend-coder"
        )
        assert result is None

    def test_bypasses_algedonic_type(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={"type": "algedonic"},
            teammate_name="pact-backend-coder"
        )
        assert result is None


# =============================================================================
# Comprehensive: Nudge fires for ALL PACT agent types
# =============================================================================

class TestCheckMemoryNudgeAllAgents:
    """Verify the memory nudge fires for every PACT work agent type."""

    @pytest.mark.parametrize("agent_name", [
        "pact-preparer",
        "pact-architect",
        "pact-backend-coder",
        "pact-frontend-coder",
        "pact-database-engineer",
        "pact-devops-engineer",
        "pact-n8n",
        "pact-test-engineer",
        "pact-security-engineer",
        "pact-qa-engineer",
    ])
    def test_nudge_fires_for_all_pact_agents(self, agent_name):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name=agent_name,
            task_id="99"
        )
        assert result is not None
        assert "ACTION REQUIRED" in result
        assert agent_name in result
        assert "task #99" in result

    @pytest.mark.parametrize("agent_name", [
        "pact-preparer",
        "pact-architect",
        "pact-backend-coder",
        "pact-frontend-coder",
        "pact-database-engineer",
        "pact-devops-engineer",
        "pact-n8n",
        "pact-test-engineer",
        "pact-security-engineer",
        "pact-qa-engineer",
    ])
    def test_no_nudge_when_memory_saved_for_all_agents(self, agent_name):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={"memory_saved": True},
            teammate_name=agent_name
        )
        assert result is None


class TestCheckMemoryNudgeNonPactAgents:
    """Verify the memory nudge does NOT fire for non-PACT agents."""

    @pytest.mark.parametrize("agent_name", [
        "random-agent",
        "backend-coder",  # no pact- prefix
        "explorer",
        "team-lead",
        "pact-memory-agent",
        "not-pact-backend-coder",
    ])
    def test_no_nudge_for_non_pact_agents(self, agent_name):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name=agent_name
        )
        assert result is None


class TestNudgeMessageContent:
    """Verify the ACTION REQUIRED message includes all expected components."""

    def test_nudge_includes_agent_name(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="pact-frontend-coder",
            task_id="7"
        )
        assert "pact-frontend-coder" in result

    def test_nudge_includes_task_id(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="pact-backend-coder",
            task_id="42"
        )
        assert "task #42" in result

    def test_nudge_omits_task_ref_when_no_id(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="pact-backend-coder",
            task_id=""
        )
        assert result is not None
        assert "ACTION REQUIRED" in result
        assert "task #" not in result

    def test_nudge_includes_save_task_instruction(self):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="pact-backend-coder",
            task_id="1"
        )
        assert "save task" in result.lower()
        assert "pact-memory" in result

    def test_nudge_contains_clipboard_emoji(self):
        """The nudge should start with the clipboard emoji."""
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name="pact-backend-coder",
            task_id="1"
        )
        assert "\U0001f4cb" in result  # clipboard emoji


class TestScopeSuffixedAgentsInHandoffGate:
    """Scope-suffixed agent names should be recognized by handoff_gate."""

    @pytest.mark.parametrize("agent_name", [
        "pact-backend-coder-auth-scope",
        "pact-frontend-coder-dashboard",
        "pact-test-engineer-unit",
        "pact-n8n-workflow-builder",
    ])
    def test_scope_suffixed_agents_get_nudge(self, agent_name):
        from handoff_gate import check_memory_metadata

        result = check_memory_metadata(
            task_metadata={},
            teammate_name=agent_name,
            task_id="10"
        )
        assert result is not None
        assert "ACTION REQUIRED" in result
        assert agent_name in result


class TestHandoffAndMemoryInteraction:
    """Both handoff validation (blocking) and memory check (non-blocking)
    should fire correctly on the same task completion."""

    def test_valid_handoff_no_memory_yields_nudge_only(self, capsys):
        """Valid handoff + no memory_saved = exit 0 + nudge on stderr."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "5",
            "task_subject": "CODE: implement feature",
            "teammate_name": "pact-backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata",
                    return_value={"handoff": VALID_HANDOFF}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "ACTION REQUIRED" in captured.err
        assert "pact-backend-coder" in captured.err
        assert "task #5" in captured.err

    def test_missing_handoff_blocks_even_with_memory_saved(self, capsys):
        """Missing handoff blocks (exit 2) regardless of memory_saved status."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "5",
            "task_subject": "CODE: implement feature",
            "teammate_name": "pact-backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata",
                    return_value={"memory_saved": True}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "handoff" in captured.err.lower()

    def test_valid_handoff_with_memory_saved_no_output(self, capsys):
        """Valid handoff + memory_saved: true = exit 0, no stderr output."""
        from handoff_gate import main

        metadata = {"handoff": VALID_HANDOFF, "memory_saved": True}
        input_data = json.dumps({
            "task_id": "5",
            "task_subject": "CODE: implement feature",
            "teammate_name": "pact-backend-coder",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata", return_value=metadata), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.err.strip() == ""

    def test_non_pact_agent_valid_handoff_no_nudge(self, capsys):
        """Non-PACT agent with valid handoff = exit 0, no nudge."""
        from handoff_gate import main

        input_data = json.dumps({
            "task_id": "5",
            "task_subject": "CODE: implement feature",
            "teammate_name": "regular-agent",
            "team_name": "pact-test"
        })

        with patch("handoff_gate.read_task_metadata",
                    return_value={"handoff": VALID_HANDOFF}), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "ACTION REQUIRED" not in captured.err


class TestReadTaskMetadataEdgeCases:
    """Additional edge cases for read_task_metadata."""

    def test_path_traversal_sanitization(self, tmp_path):
        from handoff_gate import read_task_metadata

        # Attempt path traversal
        result = read_task_metadata("../../../etc/passwd", "team", tasks_base_dir=str(tmp_path))
        assert result == {}

    def test_task_id_with_slashes_sanitized(self, tmp_path):
        from handoff_gate import read_task_metadata

        result = read_task_metadata("foo/bar", "team", tasks_base_dir=str(tmp_path))
        assert result == {}

    def test_reads_without_team_name(self, tmp_path):
        from handoff_gate import read_task_metadata

        task_data = {"metadata": {"handoff": VALID_HANDOFF}}
        (tmp_path / "42.json").write_text(json.dumps(task_data))

        result = read_task_metadata("42", None, tasks_base_dir=str(tmp_path))
        assert "handoff" in result

    def test_task_file_missing_metadata_key(self, tmp_path):
        from handoff_gate import read_task_metadata

        # Valid JSON but no metadata key
        task_data = {"subject": "test", "status": "completed"}
        (tmp_path / "42.json").write_text(json.dumps(task_data))

        result = read_task_metadata("42", None, tasks_base_dir=str(tmp_path))
        assert result == {}
