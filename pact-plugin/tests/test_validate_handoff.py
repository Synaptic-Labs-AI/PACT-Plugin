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
10. check_lossless_fields() and declares_signal_completion() unit tests
11. Refusal shape: decision:block + reason on missing/low-quality HANDOFF
12. stop_hook_active loop guard: refusal degrades to a systemMessage warning
"""
import io
import json
from unittest.mock import patch
from pathlib import Path

import pytest


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


class TestPactNamespacedAgent:
    """is_pact_agent() accepts exactly the platform's `PACT:` spelling."""

    def test_a_pact_namespaced_agent_is_a_pact_agent(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("PACT:pact-preparer") is True

    def test_only_one_pact_namespace_is_stripped(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("PACT:PACT:pact-x") is False

    def test_the_namespace_is_case_sensitive(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("pact:pact-x") is False

    def test_another_plugins_namespace_is_not_stripped(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("Other:pact-x") is False

    def test_a_namespaced_non_pact_agent_is_not(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("PACT:custom-agent") is False

    def test_the_bare_namespace_is_not(self):
        from validate_handoff import is_pact_agent

        assert is_pact_agent("PACT:") is False

    def test_the_namespace_matches_the_plugin_name(self):
        from shared.pact_context import PACT_NAMESPACE

        manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        assert PACT_NAMESPACE == json.loads(manifest.read_text(encoding="utf-8"))["name"] + ":"


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
            "agent_type": "pact-backend-coder",
            "last_assistant_message": GOOD_HANDOFF,
            "transcript": MISSING_HANDOFF,  # Would fail if used
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # Good handoff => no refusal printed
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_falls_back_to_transcript_when_no_last_assistant_message(self, capsys):
        """When last_assistant_message is absent, should fall back to
        transcript field."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_falls_back_to_transcript_when_last_assistant_message_empty(self, capsys):
        """When last_assistant_message is empty string, should fall back to
        transcript field."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "",
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_refuses_on_missing_handoff_from_last_assistant_message(self, capsys):
        """When last_assistant_message has poor handoff, should refuse the stop."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "block"
        assert "Handoff Refusal" in output["reason"]


# =============================================================================
# main() Entry Point Tests
# =============================================================================

class TestMainEntryPoint:
    """Tests for main() stdin/stdout/exit behavior."""

    def test_exits_0_for_non_pact_agent(self, capsys):
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "custom-agent",
            "transcript": MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_exits_0_on_invalid_json(self):
        from validate_handoff import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_exits_0_for_short_transcript(self, capsys):
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "short",
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

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
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}


# =============================================================================
# Edge Case Tests — Field Preference, Boundary Conditions
# =============================================================================

class TestLastAssistantMessagePreference:
    """Detailed tests for the last_assistant_message vs transcript preference logic."""

    def test_prefers_last_assistant_message_over_transcript_content(self, capsys):
        """When both fields have content, last_assistant_message wins.
        Verified by: last_assistant_message has good handoff, transcript has bad.
        If transcript were used, we'd get a refusal — no refusal = correct field used."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": GOOD_HANDOFF,
            "transcript": "x" * 200,  # Long enough to trigger validation, but no handoff
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}  # No refusal = used good handoff from last_assistant_message

    def test_last_assistant_message_none_falls_back(self, capsys):
        """When last_assistant_message is explicitly None, falls back to transcript."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": None,
            "transcript": GOOD_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}  # Fallback to transcript succeeded

    def test_both_fields_missing_exits_cleanly(self, capsys):
        """When both fields are missing, transcript is empty string, exits 0."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}  # Short transcript (< 100 chars) skips validation


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

    def test_handoff_with_both_lossless_fields_none_missing(self):
        """HANDOFF with both Produced and Key decisions: no lossless fields missing."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_BOTH_LOSSLESS)
        assert is_valid is True
        assert missing == []
        assert lossless == []

    def test_handoff_missing_produced_flagged(self):
        """HANDOFF missing 'Produced:' subsection: flags Produced."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_MISSING_PRODUCED)
        assert is_valid is True  # Still valid — the flag is reported, main() decides severity
        assert missing == []
        assert "Produced" in lossless
        assert "Key decisions" not in lossless

    def test_handoff_missing_key_decisions_flagged(self):
        """HANDOFF missing 'Key decisions:' subsection: flags Key decisions."""
        from validate_handoff import validate_handoff

        is_valid, missing, lossless = validate_handoff(HANDOFF_MISSING_KEY_DECISIONS)
        assert is_valid is True
        assert missing == []
        assert "Key decisions" in lossless
        assert "Produced" not in lossless

    def test_handoff_missing_both_flags_both(self):
        """HANDOFF missing both lossless fields: flags both."""
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


class TestDeclaresSignalCompletion:
    """Unit tests for declares_signal_completion() function."""

    def test_audit_signal_detected(self):
        from validate_handoff import declares_signal_completion

        assert declares_signal_completion("AUDIT SIGNAL: quality check") is True

    def test_audit_summary_detected(self):
        from validate_handoff import declares_signal_completion

        assert declares_signal_completion("Stored audit_summary in metadata") is True

    def test_completion_type_signal_detected(self):
        from validate_handoff import declares_signal_completion

        assert declares_signal_completion('completion_type: "signal"') is True

    def test_normal_handoff_not_signal(self):
        from validate_handoff import declares_signal_completion

        assert declares_signal_completion("## HANDOFF\n1. Produced: files") is False

    def test_empty_string_not_signal(self):
        from validate_handoff import declares_signal_completion

        assert declares_signal_completion("") is False

    def test_case_insensitive(self):
        from validate_handoff import declares_signal_completion

        assert declares_signal_completion("audit signal: observation") is True

    def test_quoted_mention_deep_in_body_does_not_declare(self):
        """A body that MENTIONS the token far from its opener — here while
        DENYING it — must not suppress the HANDOFF refusal. This is the
        defect: any closing text quoting dispatch or protocol prose used to
        disable its own check."""
        from validate_handoff import declares_signal_completion

        body = (
            "## HANDOFF\n"
            "1. Produced: the remedy and its arms.\n"
            + ("Filler describing the change in detail. " * 40)
            + "\nTo be clear, this is NOT an audit_summary and carries no "
            "AUDIT SIGNAL; I am not a signal completion."
        )
        assert declares_signal_completion(body) is False

    def test_assertive_auditor_opener_still_declares(self):
        """The real auditor shape, recovered from delivered bodies: the
        declaration sits in the opener, after an agent tag and an emoji. It
        must keep bypassing lossless validation."""
        from validate_handoff import declares_signal_completion

        body = (
            "[auditor\u2192team-lead] \U0001f4cb AUDIT SIGNAL: GREEN "
            "(4 commits: d989b8f9, b04b4a1a, 47b5b295)\n\n"
            + ("Detail about each commit follows. " * 40)
        )
        assert declares_signal_completion(body) is True


class TestMainLosslessRefusals:
    """Integration tests for lossless-field refusals in main() output."""

    def test_main_refuses_when_produced_missing(self, capsys):
        """main() should refuse the stop when Produced is missing."""
        from validate_handoff import main

        # Pad to exceed 100 char minimum + has HANDOFF section but missing Produced
        transcript = HANDOFF_MISSING_PRODUCED + " " * max(0, 100 - len(HANDOFF_MISSING_PRODUCED))
        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": transcript,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "block"
        assert "Lossless Field Refusal" in output["reason"]
        assert "Produced" in output["reason"]

    def test_main_refusal_names_both_missing_lossless_fields(self, capsys):
        """main() should name both missing fields in the refusal reason."""
        from validate_handoff import main

        transcript = HANDOFF_MISSING_BOTH_LOSSLESS + " " * max(0, 100 - len(HANDOFF_MISSING_BOTH_LOSSLESS))
        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": transcript,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["decision"] == "block"
        assert "Lossless Field Refusal" in output["reason"]
        assert "Produced" in output["reason"]
        assert "Key decisions" in output["reason"]

    def test_main_no_refusal_when_both_lossless_present(self, capsys):
        """main() should allow the stop when both lossless fields are present."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": HANDOFF_BOTH_LOSSLESS,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_main_no_refusal_for_signal_completion(self, capsys):
        """main() should not refuse signal-type completions."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-auditor",
            "last_assistant_message": SIGNAL_COMPLETION_TRANSCRIPT,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}


# =============================================================================
# #812 role-class gate: keyed on agent_type (was agent_id, dormant at v4.4.0)
# =============================================================================

class TestRoleClassGateOnAgentType:
    """The role-class gate ("is this a PACT agent?") reads ``agent_type``, not
    ``agent_id`` (#812). agent_id is absent under the separate-process teammate
    model, so the prior agent_id-keyed check was DORMANT for all teammates;
    keying on agent_type re-enables teammate HANDOFF validation. The gate is
    the only thing between a refusal and a non-PACT agent: a PACT agent_type
    gets its stop blocked on a missing HANDOFF, any other agent_type exits
    clean.
    """

    def test_teammate_agent_type_fires_handoff_refusal(self, capsys):
        """A teammate's agent_type (e.g. "pact-preparer") matches the pact-
        prefix → the gate fires → a missing-HANDOFF transcript refuses the
        stop. This is the validation that was dormant before the #812 swap."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-preparer",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert output.get("decision") == "block", (
            "teammate agent_type 'pact-preparer' must engage the role-class gate "
            "(re-enabled #812 validation) and refuse the stop on a missing HANDOFF"
        )
        # The refusal reason labels the agent by its agent_type (no longer the absent agent_id).
        assert "pact-preparer" in output["reason"]

    def test_agent_id_only_no_agent_type_is_dormant(self, capsys):
        """DOCUMENTS THE v4.4.0 STATE THE SWAP FIXES: a frame carrying ONLY
        agent_id (no agent_type) does NOT engage the gate — the role-class check
        reads agent_type, which is absent → suppressOutput. Pins that agent_id
        is no longer consulted (so the check is no longer dormant-via-agent_id-
        absence in the normal teammate case, which DOES carry agent_type)."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_id": "pact-backend-coder",  # present, but NOT the gate field
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}, (
            "agent_id is no longer the gate field — an agent_id-only frame "
            "(no agent_type) must suppress, proving the gate reads agent_type"
        )

    def test_non_pact_agent_type_suppresses(self, capsys):
        """A non-PACT agent_type ("custom-agent") does not match the pact-
        prefix → suppress (no false-positive HANDOFF refusal for non-PACT
        agents)."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "custom-agent",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}, (
            "a non-PACT agent_type must not engage the role-class gate"
        )


# =============================================================================
# stop_hook_active loop guard: refusal degrades to a warning, never a re-block
# =============================================================================

class TestStopHookActiveLoopGuard:
    """When ``stop_hook_active`` is set, the agent is already continuing from
    a stop-hook block. Refusing again can loop an agent that cannot satisfy
    the check forever, so the hook MUST degrade to a systemMessage warning
    and let the stop land. When the field is absent or false, the refusal
    fires normally.
    """

    def test_stop_hook_active_true_degrades_to_warning(self, capsys):
        """Missing HANDOFF + stop_hook_active=true → warning, NOT a block."""
        from validate_handoff import main

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
            "stop_hook_active": True,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "decision" not in output, (
            "stop_hook_active=true must NOT re-block — that loops an agent "
            "that cannot satisfy the check"
        )
        # The degrade path is user-facing: the framing names the degrade, and
        # the shared detail (with its refusal-class label) rides inside it.
        assert "refusal degraded by stop_hook_active loop guard" in output["systemMessage"]
        assert "Handoff Refusal" in output["systemMessage"]

    def test_stop_hook_active_true_degrades_lossless_refusal(self, capsys, monkeypatch):
        """Lossless-field refusal + stop_hook_active=true → warning, not block."""
        import validate_handoff
        from validate_handoff import main

        events = []
        monkeypatch.setattr(validate_handoff, "append_event", events.append)

        transcript = HANDOFF_MISSING_PRODUCED + " " * max(0, 100 - len(HANDOFF_MISSING_PRODUCED))
        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": transcript,
            "stop_hook_active": True,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "decision" not in output
        assert "refusal degraded by stop_hook_active loop guard" in output["systemMessage"]
        assert "Lossless Field Refusal" in output["systemMessage"]
        assert events[0]["classes"] == ["lossless_fields"]

    def test_degrade_emits_handoff_refusal_degraded_event(self, monkeypatch):
        """Degrade path appends one handoff_refusal_degraded journal event
        carrying agent_type, the refusal detail, and the fired class."""
        import validate_handoff
        from validate_handoff import main

        events = []
        monkeypatch.setattr(validate_handoff, "append_event", events.append)

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
            "stop_hook_active": True,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(events) == 1
        event = events[0]
        assert event["type"] == "handoff_refusal_degraded"
        assert event["agent_type"] == "pact-backend-coder"
        assert "Handoff Refusal" in event["detail"]
        assert event["classes"] == ["missing_handoff"]

    def test_degrade_journal_failure_still_exits_zero(self, capsys, monkeypatch):
        """A journal-write failure on the degrade path is swallowed: the
        systemMessage still lands and the hook still exits 0 — telemetry is
        fail-open and never breaks the exit-0 contract."""
        import validate_handoff
        from validate_handoff import main

        def _raise(_event):
            raise RuntimeError("journal write exploded")

        monkeypatch.setattr(validate_handoff, "append_event", _raise)

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
            "stop_hook_active": True,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(capsys.readouterr().out.strip())
        assert "decision" not in output
        assert "refusal degraded by stop_hook_active loop guard" in output["systemMessage"]

    def test_stop_hook_active_false_still_blocks(self, capsys, monkeypatch):
        """stop_hook_active=false is the same as absent → the refusal fires."""
        import validate_handoff
        from validate_handoff import main

        events = []
        monkeypatch.setattr(validate_handoff, "append_event", events.append)

        input_data = json.dumps({
            "agent_type": "pact-backend-coder",
            "last_assistant_message": "x" * 100 + " " + MISSING_HANDOFF,
            "stop_hook_active": False,
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert output["decision"] == "block"
        assert "Handoff Refusal" in output["reason"]
        assert events == []  # telemetry fires only on the degrade path
