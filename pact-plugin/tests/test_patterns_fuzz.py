"""
Location: pact-plugin/tests/test_patterns_fuzz.py
Summary: Edge case tests for the patterns module.
Used by: pytest test suite.

Tests regex patterns for robustness against pathological inputs,
ReDoS vulnerabilities, unicode handling, and edge cases.
"""

import re
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from refresh.patterns import (
    TRIGGER_PATTERNS,
    TERMINATION_SIGNALS,
    CONTEXT_EXTRACTORS,
    PENDING_ACTION_PATTERNS,
    PACT_AGENT_PATTERN,
    TASK_TOOL_PATTERN,
    SUBAGENT_TYPE_PATTERN,
    TEAM_CREATE_PATTERN,
    TEAM_DELETE_PATTERN,
    SEND_MESSAGE_PATTERN,
    TEAM_NAME_PATTERN,
    TASK_WITH_TEAM_PATTERN,
    is_termination_signal,
    extract_context_value,
)


# Edge case test inputs - manually crafted to cover problematic scenarios
EDGE_CASE_INPUTS = [
    # Empty and whitespace
    "",
    " ",
    "\t",
    "\n",
    "   \t\n   ",
    # Very long strings
    "a" * 1000,
    "x" * 5000,
    # Unicode and emoji
    "emoji test",
    "mixed content with emojis",
    "CJK characters test",
    "Arabic text test",
    # Control characters
    "\x00\x01\x02",
    "text\x00with\x00nulls",
    "\r\n\r\n",
    # Newlines
    "line1\nline2",
    "line1\r\nline2\r\nline3",
    # Special regex metacharacters
    "[.*+?^${}()|\\",
    "regex.*test+pattern?",
    "^start$end",
    "(group)[class]{quantifier}",
    # Backslashes
    "\\\\\\\\",
    "path\\to\\file",
    # Mixed content
    "PR #123 with emoji and [brackets]",
    "/PACT:peer-review with special chars: .*+?",
    "Normal text with\nnewlines\tand\ttabs",
]


class TestTriggerPatternsEdgeCases:
    """Edge case tests for workflow trigger patterns."""

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_trigger_patterns_no_crash(self, content: str):
        """Test trigger patterns don't crash on edge case input."""
        for name, pattern in TRIGGER_PATTERNS.items():
            # Should not raise any exception
            result = pattern.search(content)
            # Result should be None or a Match object
            assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", [
        "a" * 10000,
        "." * 10000,
        "*" * 10000,
        " " * 10000,
        "\n" * 10000,
    ])
    def test_trigger_patterns_no_redos(self, content: str):
        """Test trigger patterns don't exhibit ReDoS behavior on repeated chars."""
        start_time = time.time()

        for name, pattern in TRIGGER_PATTERNS.items():
            pattern.search(content)

        elapsed = time.time() - start_time
        # Should complete in reasonable time (< 1 second for all patterns)
        assert elapsed < 1.0, f"Patterns took too long: {elapsed}s"


class TestTerminationSignalsEdgeCases:
    """Edge case tests for termination signal patterns."""

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_termination_signals_no_crash(self, content: str):
        """Test termination signal detection doesn't crash on edge case input."""
        workflows = ["peer-review", "orchestrate", "plan-mode", "comPACT", "rePACT"]

        for workflow in workflows:
            # Should not raise any exception
            result = is_termination_signal(content, workflow)
            assert isinstance(result, bool)

    @pytest.mark.parametrize("content", [
        "a" * 5000,
        "merged " * 500,
        "PR PR PR " * 500,
    ])
    def test_termination_signals_no_redos(self, content: str):
        """Test termination signal patterns don't exhibit ReDoS behavior."""
        start_time = time.time()

        for workflow, signals in TERMINATION_SIGNALS.items():
            for signal_pattern in signals:
                re.search(signal_pattern, content, re.IGNORECASE)

        elapsed = time.time() - start_time
        assert elapsed < 1.0, f"Termination signals took too long: {elapsed}s"

    @pytest.mark.parametrize("workflow,content", [
        ("peer-review", ""),
        ("peer-review", "random text"),
        ("orchestrate", ""),
        ("orchestrate", "random text"),
        ("plan-mode", ""),
        ("comPACT", "unicode "),
        ("rePACT", "control\x00chars"),
    ])
    def test_is_termination_signal_deterministic(self, workflow: str, content: str):
        """Test is_termination_signal returns consistent results."""
        result1 = is_termination_signal(content, workflow)
        result2 = is_termination_signal(content, workflow)
        assert result1 == result2


class TestContextExtractorsEdgeCases:
    """Edge case tests for context extraction patterns."""

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_context_extractors_no_crash(self, content: str):
        """Test context extractors don't crash on edge case input."""
        for key, pattern in CONTEXT_EXTRACTORS.items():
            result = pattern.search(content)
            assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", [
        "x" * 5000,
        "PR #" + "9" * 5000,
        "branch: " + "a" * 5000,
    ])
    def test_context_extractors_no_redos(self, content: str):
        """Test context extractor patterns don't exhibit ReDoS behavior."""
        start_time = time.time()

        for key, pattern in CONTEXT_EXTRACTORS.items():
            pattern.search(content)

        elapsed = time.time() - start_time
        assert elapsed < 1.0, f"Context extractors took too long: {elapsed}s"

    @pytest.mark.parametrize("key,content", [
        ("pr_number", ""),
        ("pr_number", "no pr here"),
        ("pr_number", "PR #123 valid"),
        ("branch_name", ""),
        ("branch_name", "no branch"),
        ("task_summary", ""),
        ("task_summary", "unicode "),
    ])
    def test_extract_context_value_no_crash(self, key: str, content: str):
        """Test extract_context_value doesn't crash on edge cases."""
        result = extract_context_value(content, key)
        assert result is None or isinstance(result, str)

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_extract_context_value_unknown_key(self, content: str):
        """Test extract_context_value handles unknown keys gracefully."""
        result = extract_context_value(content, "nonexistent_key_xyz")
        assert result is None


class TestPendingActionPatternsEdgeCases:
    """Edge case tests for pending action patterns."""

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_pending_action_patterns_no_crash(self, content: str):
        """Test pending action patterns don't crash on edge case input."""
        for name, pattern in PENDING_ACTION_PATTERNS.items():
            result = pattern.search(content)
            assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", [
        "waiting for " * 500,
        "agent " * 500,
        "x" * 5000,
    ])
    def test_pending_action_patterns_no_redos(self, content: str):
        """Test pending action patterns don't exhibit ReDoS behavior."""
        start_time = time.time()

        for name, pattern in PENDING_ACTION_PATTERNS.items():
            pattern.search(content)

        elapsed = time.time() - start_time
        assert elapsed < 1.0, f"Pending action patterns took too long: {elapsed}s"


class TestAgentPatternsEdgeCases:
    """Edge case tests for agent-related patterns."""

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_pact_agent_pattern_no_crash(self, content: str):
        """Test PACT agent pattern doesn't crash on edge cases."""
        result = PACT_AGENT_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_task_tool_pattern_no_crash(self, content: str):
        """Test Task tool pattern doesn't crash on edge cases."""
        result = TASK_TOOL_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_subagent_type_pattern_no_crash(self, content: str):
        """Test subagent type pattern doesn't crash on edge cases."""
        result = SUBAGENT_TYPE_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_team_create_pattern_no_crash(self, content: str):
        """Test TeamCreate pattern doesn't crash on edge cases."""
        result = TEAM_CREATE_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_team_delete_pattern_no_crash(self, content: str):
        """Test TeamDelete pattern doesn't crash on edge cases."""
        result = TEAM_DELETE_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_send_message_pattern_no_crash(self, content: str):
        """Test SendMessage pattern doesn't crash on edge cases."""
        result = SEND_MESSAGE_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_team_name_pattern_no_crash(self, content: str):
        """Test team_name pattern doesn't crash on edge cases."""
        result = TEAM_NAME_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')

    @pytest.mark.parametrize("content", EDGE_CASE_INPUTS)
    def test_task_with_team_pattern_no_crash(self, content: str):
        """Test Task-with-team pattern doesn't crash on edge cases."""
        result = TASK_WITH_TEAM_PATTERN.search(content)
        assert result is None or hasattr(result, 'group')


class TestPathologicalInputs:
    """Tests for known pathological regex inputs."""

    def test_repeated_characters(self):
        """Test patterns handle extremely repeated characters."""
        # Common ReDoS triggers
        pathological_inputs = [
            "a" * 10000,
            "." * 10000,
            "*" * 10000,
            " " * 10000,
            "\n" * 10000,
            "\\n" * 5000,
            "PR #" + "9" * 10000,
            "/PACT:" + "x" * 10000,
        ]

        for content in pathological_inputs:
            start_time = time.time()

            # Test all patterns
            for name, pattern in TRIGGER_PATTERNS.items():
                pattern.search(content)

            for key, pattern in CONTEXT_EXTRACTORS.items():
                pattern.search(content)

            for name, pattern in PENDING_ACTION_PATTERNS.items():
                pattern.search(content)

            PACT_AGENT_PATTERN.search(content)
            TASK_TOOL_PATTERN.search(content)
            SUBAGENT_TYPE_PATTERN.search(content)
            TEAM_CREATE_PATTERN.search(content)
            TEAM_DELETE_PATTERN.search(content)
            SEND_MESSAGE_PATTERN.search(content)
            TEAM_NAME_PATTERN.search(content)
            TASK_WITH_TEAM_PATTERN.search(content)

            elapsed = time.time() - start_time
            assert elapsed < 1.0, f"Pathological input took too long: {elapsed}s on input length {len(content)}"

    def test_nested_patterns(self):
        """Test patterns handle nested/recursive-like structures."""
        nested_inputs = [
            "(((" * 1000 + ")))" * 1000,
            "[[[[" * 500 + "]]]]" * 500,
            "{{{" * 1000 + "}}}" * 1000,
            "<<<" * 1000 + ">>>" * 1000,
        ]

        for content in nested_inputs:
            start_time = time.time()

            for name, pattern in TRIGGER_PATTERNS.items():
                pattern.search(content)

            elapsed = time.time() - start_time
            assert elapsed < 1.0, f"Nested pattern took too long: {elapsed}s"

    def test_alternating_patterns(self):
        """Test patterns handle alternating character sequences."""
        alternating_inputs = [
            "ab" * 5000,
            "PR PR PR " * 1000,
            "/PACT:x /PACT:y " * 500,
            "merged closed merged closed " * 500,
        ]

        for content in alternating_inputs:
            start_time = time.time()

            for workflow in ["peer-review", "orchestrate"]:
                is_termination_signal(content, workflow)

            elapsed = time.time() - start_time
            assert elapsed < 1.0, f"Alternating pattern took too long: {elapsed}s"


class TestUnicodeEdgeCases:
    """Tests for unicode edge cases."""

    def test_unicode_categories(self):
        """Test patterns handle various unicode categories."""
        unicode_inputs = [
            # CJK characters
            "" * 1000,
            # Arabic
            "" * 1000,
            # Emoji
            "" * 500,
            # Mixed scripts
            "Hello " + "world",
            # Zero-width characters
            "\u200b" * 1000,  # Zero-width space
            "\u200d" * 1000,  # Zero-width joiner
            # RTL markers
            "\u200f" * 1000,  # Right-to-left mark
            # Combining characters
            "e\u0301" * 1000,  # e + combining acute accent
        ]

        for content in unicode_inputs:
            # Should not crash
            for name, pattern in TRIGGER_PATTERNS.items():
                result = pattern.search(content)
                assert result is None or hasattr(result, 'group')

    def test_mixed_unicode_with_patterns(self):
        """Test patterns work when unicode is mixed with expected content."""
        # These should still match even with unicode around them
        assert TRIGGER_PATTERNS["peer-review"].search("/PACT:peer-review ") is not None
        assert TRIGGER_PATTERNS["orchestrate"].search("/PACT:orchestrate") is not None

        # These shouldn't crash
        assert CONTEXT_EXTRACTORS["pr_number"].search("PR #123 ") is not None
        assert is_termination_signal("PR merged ", "peer-review") is True

    @pytest.mark.parametrize("content", [
        "/PACT:peer-review",
        "test /PACT:orchestrate more",
        "PR #456 in text",
        "branch: feature/test-123",
    ])
    def test_realistic_mixed_content(self, content: str):
        """Test patterns with realistic mixed alphanumeric content."""
        for name, pattern in TRIGGER_PATTERNS.items():
            result = pattern.search(content)
            assert result is None or hasattr(result, 'group')
