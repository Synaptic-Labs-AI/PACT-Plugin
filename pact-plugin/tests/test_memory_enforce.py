"""
Tests for memory_enforce.py — SubagentStop hook that enforces memory saves
after PACT agent work.

Tests cover:
1. is_pact_work_agent: agent ID matching, exclusion of memory agent
2. did_meaningful_work: work pattern detection, decision detection, short transcripts
3. format_enforcement_message: message formatting
4. main: stdin parsing, exit codes, output format, skip conditions
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# ---------------------------------------------------------------------------
# is_pact_work_agent
# ---------------------------------------------------------------------------

class TestIsPactWorkAgent:
    """Tests for is_pact_work_agent() — agent ID matching."""

    def test_recognizes_backend_coder(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("pact-backend-coder") is True

    def test_recognizes_test_engineer(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("pact-test-engineer") is True

    def test_recognizes_preparer(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("pact-preparer") is True

    def test_recognizes_architect(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("pact-architect") is True

    def test_excludes_memory_agent(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("pact-memory-agent") is False

    def test_excludes_non_pact_agents(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("some-other-agent") is False

    def test_handles_empty_string(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("") is False

    def test_handles_none(self):
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent(None) is False

    def test_case_insensitive_via_lower(self):
        """Agent ID is lowercased before matching."""
        from memory_enforce import is_pact_work_agent
        assert is_pact_work_agent("PACT-BACKEND-CODER") is True

    def test_recognizes_all_work_agents(self):
        from memory_enforce import PACT_WORK_AGENTS, is_pact_work_agent
        for agent in PACT_WORK_AGENTS:
            assert is_pact_work_agent(agent) is True, f"Failed for {agent}"


# ---------------------------------------------------------------------------
# did_meaningful_work
# ---------------------------------------------------------------------------

class TestDidMeaningfulWork:
    """Tests for did_meaningful_work() — transcript analysis."""

    def test_detects_file_creation_work(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 200 + " created src/app.py with the main handler"
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True
        assert "work completed" in reasons

    def test_detects_implementation_work(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 200 + " implemented the new function handler"
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True

    def test_detects_decisions(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 200 + " decided to use the factory pattern because it provides flexibility"
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True
        assert "decisions made" in reasons

    def test_detects_file_operations(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 200 + " working in docs/preparation directory"
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True
        assert "file operations" in reasons

    def test_short_transcript_returns_false(self):
        from memory_enforce import did_meaningful_work
        did_work, reasons = did_meaningful_work("short")
        assert did_work is False
        assert reasons == []

    def test_empty_transcript_returns_false(self):
        from memory_enforce import did_meaningful_work
        did_work, reasons = did_meaningful_work("")
        assert did_work is False

    def test_none_transcript_returns_false(self):
        from memory_enforce import did_meaningful_work
        did_work, reasons = did_meaningful_work(None)
        assert did_work is False

    def test_transcript_below_200_chars_returns_false(self):
        from memory_enforce import did_meaningful_work
        did_work, reasons = did_meaningful_work("a" * 199)
        assert did_work is False

    def test_no_patterns_matched(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 250  # Long enough but no patterns
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is False
        assert reasons == []

    def test_multiple_reasons(self):
        from memory_enforce import did_meaningful_work
        transcript = (
            "x" * 200
            + " created src/app.py and decided to use the approach because "
            + " working in docs/architecture"
        )
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True
        assert len(reasons) >= 2

    def test_detects_research_work(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 200 + " researched and documented the api patterns"
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True

    def test_detects_architecture_work(self):
        from memory_enforce import did_meaningful_work
        transcript = "x" * 200 + " designed the interface contract"
        did_work, reasons = did_meaningful_work(transcript)
        assert did_work is True


# ---------------------------------------------------------------------------
# format_enforcement_message
# ---------------------------------------------------------------------------

class TestFormatEnforcementMessage:
    """Tests for format_enforcement_message() — message formatting."""

    def test_includes_agent_id(self):
        from memory_enforce import format_enforcement_message
        msg = format_enforcement_message("pact-backend-coder", ["work completed"])
        assert "pact-backend-coder" in msg

    def test_includes_reasons(self):
        from memory_enforce import format_enforcement_message
        msg = format_enforcement_message("agent", ["work completed", "decisions made"])
        assert "work completed" in msg
        assert "decisions made" in msg

    def test_default_reason_when_empty(self):
        from memory_enforce import format_enforcement_message
        msg = format_enforcement_message("agent", [])
        assert "work completed" in msg

    def test_includes_mandatory_language(self):
        from memory_enforce import format_enforcement_message
        msg = format_enforcement_message("agent", ["work completed"])
        assert "MANDATORY" in msg
        assert "MUST" in msg

    def test_includes_action_instructions(self):
        from memory_enforce import format_enforcement_message
        msg = format_enforcement_message("agent", ["work completed"])
        assert "SendMessage" in msg
        assert "pact-memory-agent" in msg


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    """Tests for main() — stdin parsing, output, skip conditions."""

    def test_skips_non_pact_agents(self):
        from memory_enforce import main
        input_data = {"agent_id": "other-agent", "transcript": "x" * 300}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_skips_when_stop_hook_active(self):
        from memory_enforce import main
        input_data = {
            "agent_id": "pact-backend-coder",
            "transcript": "x" * 300 + " implemented function",
            "stop_hook_active": True,
        }
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_outputs_enforcement_for_meaningful_work(self, capsys):
        from memory_enforce import main
        input_data = {
            "agent_id": "pact-backend-coder",
            "transcript": "x" * 300 + " implemented the auth handler function",
        }
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "hookSpecificOutput" in output
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "MANDATORY" in output["hookSpecificOutput"]["additionalContext"]

    def test_no_output_for_no_meaningful_work(self, capsys):
        from memory_enforce import main
        input_data = {
            "agent_id": "pact-backend-coder",
            "transcript": "x" * 300,  # No patterns
        }
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_handles_invalid_json(self):
        from memory_enforce import main
        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0

    def test_handles_exception_gracefully(self, capsys):
        from memory_enforce import main
        with patch("sys.stdin", side_effect=Exception("boom")):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
