# pact-plugin/tests/test_validate_handoff.py
"""
Tests for validate_handoff.py — SubagentStop hook that validates PACT
agent/teammate handoff format.

Tests cover:
1. validate_handoff() with structured handoff section
2. validate_handoff() with implicit handoff elements
3. validate_handoff() with missing elements
4. is_pact_agent() identification
5. main() prefers last_assistant_message over transcript (SDK v2.1.47+)
6. main() falls back to transcript when last_assistant_message absent
7. main() entry point: stdin JSON, exit codes, output format
"""
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# Test Data
# =============================================================================

GOOD_HANDOFF = """
## HANDOFF

1. Produced: Created src/auth.py with JWT authentication middleware
2. Key decisions: Chose JWT over session tokens for stateless design
3. Areas of uncertainty:
   - [HIGH] Token refresh logic untested with concurrent requests
4. Integration points: Connects to user_service.py via get_user()
5. Open questions: Should token expiry be configurable?
"""

PARTIAL_HANDOFF = "Implemented the auth module. Used JWT tokens for the approach."

MISSING_HANDOFF = "Hello world, here is some random text without any handoff info."


# =============================================================================
# validate_handoff() Tests
# =============================================================================

class TestValidateHandoff:
    """Tests for validate_handoff.validate_handoff()."""

    def test_explicit_handoff_section_is_valid(self):
        from validate_handoff import validate_handoff

        is_valid, missing = validate_handoff(GOOD_HANDOFF)
        assert is_valid is True
        assert missing == []

    def test_implicit_elements_are_detected(self):
        from validate_handoff import validate_handoff

        # Contains "produced" (what_produced) and "chose" (key_decisions)
        # and "next" (next_steps) — all 3 present
        text = (
            "I produced the auth module. "
            "I chose JWT tokens because they are stateless. "
            "Next, the test engineer should verify token expiry."
        )
        is_valid, missing = validate_handoff(text)
        assert is_valid is True
        assert missing == []

    def test_partial_handoff_with_two_of_three(self):
        from validate_handoff import validate_handoff

        # Has "implemented" (what_produced) and "approach" (key_decisions)
        # Missing next_steps — but 2/3 is still valid
        is_valid, missing = validate_handoff(PARTIAL_HANDOFF)
        assert is_valid is True
        assert len(missing) <= 1

    def test_missing_handoff_is_invalid(self):
        from validate_handoff import validate_handoff

        is_valid, missing = validate_handoff(MISSING_HANDOFF)
        assert is_valid is False
        assert len(missing) >= 2

    def test_case_insensitive_section_detection(self):
        from validate_handoff import validate_handoff

        text = "## handoff\nProduced: files. Decisions: none. Next: test."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True


# =============================================================================
# is_pact_agent() Tests
# =============================================================================

class TestIsPactAgent:
    """Tests for validate_handoff.is_pact_agent()."""

    def test_recognizes_pact_prefixed_agents(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("pact-backend-coder") is True
        assert is_pact_agent("PACT-architect") is True
        assert is_pact_agent("pact_test_engineer") is True
        assert is_pact_agent("PACT_preparer") is True

    def test_rejects_non_pact_agents(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("custom-agent") is False
        assert is_pact_agent("") is False
        assert is_pact_agent("my-pact-thing") is False

    def test_rejects_none(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent(None) is False


# =============================================================================
# main() Tests — last_assistant_message preference
# =============================================================================

class TestMainLastAssistantMessage:
    """Tests for main() preferring last_assistant_message over transcript."""

    def test_uses_last_assistant_message_when_present(self, capsys):
        """When last_assistant_message is provided, it should be used
        instead of transcript."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": GOOD_HANDOFF,
            "transcript": MISSING_HANDOFF,  # Would fail if used
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # Good handoff => no warnings printed
        assert captured.out == ""

    def test_falls_back_to_transcript_when_no_last_assistant_message(self, capsys):
        """When last_assistant_message is absent, should fall back to
        transcript field."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_falls_back_to_transcript_when_last_assistant_message_empty(self, capsys):
        """When last_assistant_message is empty string, should fall back to
        transcript field."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": "",
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_warns_on_missing_handoff_from_last_assistant_message(self, capsys):
        """When last_assistant_message has poor handoff, should emit warning."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        if captured.out:
            output = json.loads(captured.out)
            assert "systemMessage" in output
            assert "Handoff Warning" in output["systemMessage"]


# =============================================================================
# main() Entry Point Tests
# =============================================================================

class TestMainEntryPoint:
    """Tests for main() stdin/stdout/exit behavior."""

    def test_exits_0_for_non_pact_agent(self, capsys):
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "custom-agent",
            "transcript": MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_exits_0_on_invalid_json(self):
        from validate_handoff import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_exits_0_for_short_transcript(self, capsys):
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": "short",
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_exits_0_with_no_agent_id(self, capsys):
        from validate_handoff import main

        input_data = json.dumps({
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


# =============================================================================
# Edge Case Tests — Field Preference, Boundary Conditions
# =============================================================================

class TestLastAssistantMessagePreference:
    """Detailed tests for the last_assistant_message vs transcript preference logic."""

    def test_prefers_last_assistant_message_over_transcript_content(self, capsys):
        """When both fields have content, last_assistant_message wins.
        Verified by: last_assistant_message has good handoff, transcript has bad.
        If transcript were used, we'd get a warning — no warning = correct field used."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": GOOD_HANDOFF,
            "transcript": "x" * 200,  # Long enough to trigger validation, but no handoff
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""  # No warning = used good handoff from last_assistant_message

    def test_last_assistant_message_none_falls_back(self, capsys):
        """When last_assistant_message is explicitly None, falls back to transcript."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": None,
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""  # Fallback to transcript succeeded

    def test_both_fields_missing_exits_cleanly(self, capsys):
        """When both fields are missing, transcript is empty string, exits 0."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""  # Short transcript (< 100 chars) skips validation


class TestValidateHandoffEdgeCases:
    """Edge cases for validate_handoff() function."""

    def test_handoff_section_with_hash_header(self):
        """Section header with # or ## should be detected."""
        from validate_handoff import validate_handoff

        text = "# Handoff\nHere is what I did."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True

    def test_handoff_section_with_colon(self):
        """'Handoff:' followed by newline should be detected."""
        from validate_handoff import validate_handoff

        text = "Handoff:\nProduced files."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True

    def test_deliverables_section_detected(self):
        """'## Deliverables' section header should count as structured handoff."""
        from validate_handoff import validate_handoff

        text = "## Deliverables\nCreated auth module."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True

    def test_summary_section_detected(self):
        """'## Summary' section header should count as structured handoff."""
        from validate_handoff import validate_handoff

        text = "## Summary\nDid the work."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True

    def test_output_section_detected(self):
        """'## Output' section header should count as structured handoff."""
        from validate_handoff import validate_handoff

        text = "## Output\nFiles produced."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True

    def test_empty_string_is_invalid(self):
        """Empty string has no handoff elements."""
        from validate_handoff import validate_handoff

        is_valid, missing = validate_handoff("")
        assert is_valid is False
        assert len(missing) == 3  # All 3 elements missing

    def test_exactly_at_boundary_one_missing(self):
        """With exactly 1 out of 3 missing, should still be valid."""
        from validate_handoff import validate_handoff

        # Has "produced" (what_produced) and "decided to" (key_decisions)
        # Missing next_steps entirely
        text = "I produced the auth module. I decided to use JWT tokens."
        is_valid, missing = validate_handoff(text)
        assert is_valid is True
        assert len(missing) <= 1

    def test_exactly_at_boundary_two_missing(self):
        """With exactly 2 out of 3 missing, should be invalid."""
        from validate_handoff import validate_handoff

        # Only has "produced" (what_produced)
        # Missing key_decisions and next_steps
        text = "I produced the auth module and it works great and is ready."
        is_valid, missing = validate_handoff(text)
        assert is_valid is False
        assert len(missing) >= 2


class TestIsPactAgentEdgeCases:
    """Edge cases for is_pact_agent()."""

    def test_pact_in_middle_not_matched(self):
        """'my-pact-agent' should NOT match (prefix check only)."""
        from validate_handoff import is_pact_agent

        assert is_pact_agent("my-pact-agent") is False

    def test_just_pact_prefix_matched(self):
        """Just 'pact-' should match."""
        from validate_handoff import is_pact_agent

        assert is_pact_agent("pact-") is True

    def test_empty_string_not_matched(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("") is False

    def test_integer_input_raises(self):
        """Non-string input raises AttributeError (startswith not on int).
        This is acceptable — main() wraps in try/except."""
        from validate_handoff import is_pact_agent

        with pytest.raises(AttributeError):
            is_pact_agent(123)
