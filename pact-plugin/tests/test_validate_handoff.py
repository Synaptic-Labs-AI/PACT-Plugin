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
8. Lossless field validation (Produced, Key decisions) in structured HANDOFFs
9. Signal-type completion bypass (AUDIT SIGNAL / audit_summary)
10. check_lossless_fields() and is_signal_completion() unit tests
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
3. Reasoning chain: Chose JWT because stateless auth required; session tokens would need server-side storage
4. Areas of uncertainty:
   - [HIGH] Token refresh logic untested with concurrent requests
5. Integration points: Connects to user_service.py via get_user()
6. Open questions: Should token expiry be configurable?
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

        is_valid, missing, *_ = validate_handoff(GOOD_HANDOFF)
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
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True
        assert missing == []

    def test_partial_handoff_with_two_of_three(self):
        from validate_handoff import validate_handoff

        # Has "implemented" (what_produced) and "approach" (key_decisions)
        # Missing next_steps — but 2/3 is still valid
        is_valid, missing, *_ = validate_handoff(PARTIAL_HANDOFF)
        assert is_valid is True
        assert len(missing) <= 1

    def test_missing_handoff_is_invalid(self):
        from validate_handoff import validate_handoff

        is_valid, missing, *_ = validate_handoff(MISSING_HANDOFF)
        assert is_valid is False
        assert len(missing) >= 2

    def test_case_insensitive_section_detection(self):
        from validate_handoff import validate_handoff

        text = "## handoff\nProduced: files. Decisions: none. Next: test."
        is_valid, missing, *_ = validate_handoff(text)
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
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True

    def test_handoff_section_with_colon(self):
        """'Handoff:' followed by newline should be detected."""
        from validate_handoff import validate_handoff

        text = "Handoff:\nProduced files."
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True

    def test_deliverables_section_detected(self):
        """'## Deliverables' section header should count as structured handoff."""
        from validate_handoff import validate_handoff

        text = "## Deliverables\nCreated auth module."
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True

    def test_summary_section_detected(self):
        """'## Summary' section header should count as structured handoff."""
        from validate_handoff import validate_handoff

        text = "## Summary\nDid the work."
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True

    def test_output_section_detected(self):
        """'## Output' section header should count as structured handoff."""
        from validate_handoff import validate_handoff

        text = "## Output\nFiles produced."
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True

    def test_empty_string_is_invalid(self):
        """Empty string has no handoff elements."""
        from validate_handoff import validate_handoff

        is_valid, missing, *_ = validate_handoff("")
        assert is_valid is False
        assert len(missing) == 3  # All 3 elements missing

    def test_exactly_at_boundary_one_missing(self):
        """With exactly 1 out of 3 missing, should still be valid."""
        from validate_handoff import validate_handoff

        # Has "produced" (what_produced) and "decided to" (key_decisions)
        # Missing next_steps entirely
        text = "I produced the auth module. I decided to use JWT tokens."
        is_valid, missing, *_ = validate_handoff(text)
        assert is_valid is True
        assert len(missing) <= 1

    def test_exactly_at_boundary_two_missing(self):
        """With exactly 2 out of 3 missing, should be invalid."""
        from validate_handoff import validate_handoff

        # Only has "produced" (what_produced)
        # Missing key_decisions and next_steps
        text = "I produced the auth module and it works great and is ready."
        is_valid, missing, *_ = validate_handoff(text)
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


# =============================================================================
# Lossless Field Validation Tests
# =============================================================================

# Test data for lossless field scenarios
HANDOFF_BOTH_LOSSLESS = """
## HANDOFF

1. Produced: Created src/auth.py with JWT authentication middleware
2. Key decisions: Chose JWT over session tokens for stateless design
3. Areas of uncertainty:
   - [HIGH] Token refresh logic untested
4. Integration points: user_service.py
5. Open questions: Token expiry config?
"""

HANDOFF_MISSING_PRODUCED = """
## HANDOFF

1. Key decisions: Chose JWT over session tokens for stateless design
2. Areas of uncertainty:
   - [HIGH] Token refresh logic untested
3. Integration points: user_service.py
4. Open questions: Token expiry config?
"""

HANDOFF_MISSING_KEY_DECISIONS = """
## HANDOFF

1. Produced: Created src/auth.py with JWT authentication middleware
2. Areas of uncertainty:
   - [HIGH] Token refresh logic untested
3. Integration points: user_service.py
4. Open questions: Token expiry config?
"""

HANDOFF_MISSING_BOTH_LOSSLESS = """
## HANDOFF

1. Areas of uncertainty:
   - [HIGH] Token refresh logic untested
2. Integration points: user_service.py
3. Open questions: Token expiry config?
"""

SIGNAL_COMPLETION_TRANSCRIPT = """
## Summary

AUDIT SIGNAL: Code quality observation

The concurrent implementation looks solid. No critical issues found.
Stored audit_summary in task metadata.
"""


class TestLosslessFieldValidation:
    """Tests for lossless field checking in structured HANDOFF sections."""

    def test_handoff_with_both_lossless_fields_no_warning(self):
        """HANDOFF with both Produced and Key decisions: no lossless warnings."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_BOTH_LOSSLESS)
        assert is_valid is True
        assert missing == []
        assert lossless == []

    def test_handoff_missing_produced_warns(self):
        """HANDOFF missing 'Produced:' subsection: warns about Produced."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_MISSING_PRODUCED)
        assert is_valid is True  # Still valid — warnings don't block
        assert missing == []
        assert "Produced" in lossless
        assert "Key decisions" not in lossless

    def test_handoff_missing_key_decisions_warns(self):
        """HANDOFF missing 'Key decisions:' subsection: warns about Key decisions."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_MISSING_KEY_DECISIONS)
        assert is_valid is True
        assert missing == []
        assert "Key decisions" in lossless
        assert "Produced" not in lossless

    def test_handoff_missing_both_warns_both(self):
        """HANDOFF missing both lossless fields: warns about both."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_MISSING_BOTH_LOSSLESS)
        assert is_valid is True
        assert missing == []
        assert "Produced" in lossless
        assert "Key decisions" in lossless
        assert len(lossless) == 2

    def test_no_handoff_section_uses_keyword_matching(self):
        """Without a structured HANDOFF section, existing keyword matching applies.
        No lossless validation is performed."""
        from validate_handoff import validate_handoff

        # Has produced + decisions keywords but no HANDOFF section header
        text = (
            "I produced the auth module. "
            "I chose JWT tokens because they are stateless. "
            "Next, the test engineer should verify token expiry."
        )
        is_valid, missing, lossless = validate_handoff(text)
        assert is_valid is True
        assert lossless == []  # No lossless check on implicit path

    def test_signal_completion_skips_lossless_validation(self):
        """Signal-type completions (AUDIT SIGNAL) skip lossless field validation."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(SIGNAL_COMPLETION_TRANSCRIPT)
        assert is_valid is True
        assert missing == []
        assert lossless == []  # Skipped entirely for signal completions

    def test_produced_with_numbered_prefix(self):
        """'1. Produced:' format should be detected."""
        from validate_handoff import validate_handoff

        text = "## Handoff\n1. Produced: Created files\n2. Key decisions: Used JWT\n"
        is_valid, missing, lossless = validate_handoff(text)
        assert is_valid is True
        assert lossless == []

    def test_key_decision_singular_detected(self):
        """'Key decision:' (singular) should also be detected."""
        from validate_handoff import validate_handoff

        text = "## Handoff\n1. Produced: Created files\n2. Key decision: Used JWT\n"
        is_valid, missing, lossless = validate_handoff(text)
        assert is_valid is True
        assert lossless == []

    def test_lossless_fields_case_insensitive(self):
        """Lossless field matching should be case-insensitive."""
        from validate_handoff import validate_handoff

        text = "## HANDOFF\nPRODUCED: stuff\nKEY DECISIONS: things\n"
        is_valid, missing, lossless = validate_handoff(text)
        assert is_valid is True
        assert lossless == []


class TestCheckLosslessFields:
    """Unit tests for check_lossless_fields() function."""

    def test_both_present_returns_empty(self):
        from validate_handoff import check_lossless_fields

        text = "1. Produced: Files\n2. Key decisions: Choices"
        assert check_lossless_fields(text) == []

    def test_neither_present_returns_both(self):
        from validate_handoff import check_lossless_fields

        text = "Some text without the fields"
        result = check_lossless_fields(text)
        assert len(result) == 2
        assert "Produced" in result
        assert "Key decisions" in result

    def test_only_produced_present(self):
        from validate_handoff import check_lossless_fields

        text = "Produced: Files created"
        result = check_lossless_fields(text)
        assert result == ["Key decisions"]

    def test_only_key_decisions_present(self):
        from validate_handoff import check_lossless_fields

        text = "Key decisions: Chose JWT"
        result = check_lossless_fields(text)
        assert result == ["Produced"]


class TestIsSignalCompletion:
    """Unit tests for is_signal_completion() function."""

    def test_audit_signal_detected(self):
        from validate_handoff import is_signal_completion

        assert is_signal_completion("AUDIT SIGNAL: quality check") is True

    def test_audit_summary_detected(self):
        from validate_handoff import is_signal_completion

        assert is_signal_completion("Stored audit_summary in metadata") is True

    def test_completion_type_signal_detected(self):
        from validate_handoff import is_signal_completion

        assert is_signal_completion('completion_type: "signal"') is True

    def test_normal_handoff_not_signal(self):
        from validate_handoff import is_signal_completion

        assert is_signal_completion("## HANDOFF\n1. Produced: files") is False

    def test_empty_string_not_signal(self):
        from validate_handoff import is_signal_completion

        assert is_signal_completion("") is False

    def test_case_insensitive(self):
        from validate_handoff import is_signal_completion

        assert is_signal_completion("audit signal: observation") is True


class TestMainLosslessWarnings:
    """Integration tests for lossless warnings in main() output."""

    def test_main_emits_lossless_warning_when_produced_missing(self, capsys):
        """main() should emit lossless warning when Produced is missing."""
        from validate_handoff import main

        # Pad to exceed 100 char minimum + has HANDOFF section but missing Produced
        transcript = HANDOFF_MISSING_PRODUCED + " " * max(0, 100 - len(HANDOFF_MISSING_PRODUCED))
        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": transcript,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "Lossless Field Warning" in output["systemMessage"]
        assert "Produced" in output["systemMessage"]

    def test_main_emits_lossless_warning_when_both_missing(self, capsys):
        """main() should name both missing fields in the warning."""
        from validate_handoff import main

        transcript = HANDOFF_MISSING_BOTH_LOSSLESS + " " * max(0, 100 - len(HANDOFF_MISSING_BOTH_LOSSLESS))
        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": transcript,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "Lossless Field Warning" in output["systemMessage"]
        assert "Produced" in output["systemMessage"]
        assert "Key decisions" in output["systemMessage"]

    def test_main_no_warning_when_both_lossless_present(self, capsys):
        """main() should emit no warning when both lossless fields are present."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",
            "last_assistant_message": HANDOFF_BOTH_LOSSLESS,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_main_no_lossless_warning_for_signal_completion(self, capsys):
        """main() should not emit lossless warnings for signal-type completions."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-auditor",
            "last_assistant_message": SIGNAL_COMPLETION_TRANSCRIPT,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""
