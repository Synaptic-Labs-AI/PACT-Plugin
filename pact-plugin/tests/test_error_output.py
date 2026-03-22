# pact-plugin/tests/test_error_output.py
"""
Tests for structured JSON error output in hook exception handlers (Issue #280).

Tests cover:
1. Unit tests for hook_error_json() helper: valid JSON, correct keys,
   special characters, unicode, empty messages, very long messages
2. Integration tests for all 12 hooks' exception handlers: verify stdout
   JSON contains systemMessage when an exception occurs
3. Stderr output preserved alongside new stdout JSON
4. Category B hooks (git_commit_check, merge_guard_post) still use
   _SUPPRESS_OUTPUT on happy paths but not on error paths
5. Edge cases: nested exceptions, newlines, unicode
"""
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


# =============================================================================
# Unit Tests: hook_error_json() helper
# =============================================================================

class TestHookErrorJson:
    """Unit tests for the shared hook_error_json() helper function."""

    def test_returns_valid_json(self):
        """Output must be valid JSON."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError("something broke"))
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_contains_system_message_key(self):
        """Output must contain the 'systemMessage' key."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError("fail"))
        parsed = json.loads(result)
        assert "systemMessage" in parsed

    def test_system_message_format(self):
        """systemMessage must follow 'PACT hook warning ({name}): {error}' format."""
        from shared.error_output import hook_error_json

        result = hook_error_json("my_hook", ValueError("bad value"))
        parsed = json.loads(result)
        assert parsed["systemMessage"] == "PACT hook warning (my_hook): bad value"

    def test_no_extra_keys(self):
        """Output should contain only the systemMessage key."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError("x"))
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["systemMessage"]

    def test_empty_error_message(self):
        """Handles exceptions with empty string messages."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError(""))
        parsed = json.loads(result)
        assert parsed["systemMessage"] == "PACT hook warning (test_hook): "

    def test_special_characters_in_error(self):
        """JSON special characters in error messages must be escaped."""
        from shared.error_output import hook_error_json

        error = RuntimeError('path "C:\\Users\\test" not found')
        result = hook_error_json("test_hook", error)
        # Must be parseable (json.dumps handles escaping)
        parsed = json.loads(result)
        assert 'C:\\Users\\test' in parsed["systemMessage"]

    def test_unicode_in_error(self):
        """Unicode characters in error messages must survive JSON encoding."""
        from shared.error_output import hook_error_json

        error = RuntimeError("fichier introuvable: café.txt 日本語")
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert "café.txt" in parsed["systemMessage"]
        assert "日本語" in parsed["systemMessage"]

    def test_newlines_in_error(self):
        """Newline characters in error messages must be JSON-escaped."""
        from shared.error_output import hook_error_json

        error = RuntimeError("line1\nline2\nline3")
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert "line1\nline2\nline3" in parsed["systemMessage"]

    def test_very_long_error_message(self):
        """Very long error messages should be handled without truncation."""
        from shared.error_output import hook_error_json

        long_msg = "x" * 10_000
        error = RuntimeError(long_msg)
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert long_msg in parsed["systemMessage"]

    def test_hook_name_appears_in_output(self):
        """The hook_name parameter should appear in the systemMessage."""
        from shared.error_output import hook_error_json

        result = hook_error_json("validate_handoff", RuntimeError("err"))
        parsed = json.loads(result)
        assert "validate_handoff" in parsed["systemMessage"]

    def test_nested_exception_message(self):
        """Nested exception str() should produce a readable message."""
        from shared.error_output import hook_error_json

        try:
            try:
                raise ValueError("inner")
            except ValueError:
                raise RuntimeError("outer") from ValueError("inner")
        except RuntimeError as e:
            result = hook_error_json("test_hook", e)

        parsed = json.loads(result)
        # str(e) for chained exceptions just shows the outer message
        assert "outer" in parsed["systemMessage"]

    def test_returns_string_type(self):
        """Return type must be str, not bytes."""
        from shared.error_output import hook_error_json

        result = hook_error_json("test_hook", RuntimeError("err"))
        assert isinstance(result, str)

    def test_quotes_in_hook_name(self):
        """Hook names with special chars should be JSON-safe."""
        from shared.error_output import hook_error_json

        result = hook_error_json('hook"name', RuntimeError("err"))
        parsed = json.loads(result)
        assert 'hook"name' in parsed["systemMessage"]


# =============================================================================
# Integration Tests: Each hook's exception handler outputs JSON on stdout
# =============================================================================

def _parse_stdout_json(captured_out: str) -> dict:
    """Parse the last JSON line from captured stdout.

    Some hooks may output multiple lines before the error handler runs.
    The JSON error output is always the last printed line.
    """
    lines = captured_out.strip().split("\n")
    # Walk backwards to find the JSON line
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    pytest.fail(f"No JSON found in stdout: {captured_out!r}")


def _assert_error_json(captured_out: str, hook_name: str):
    """Assert that stdout contains valid JSON with systemMessage for the given hook."""
    parsed = _parse_stdout_json(captured_out)
    assert "systemMessage" in parsed, (
        f"Missing 'systemMessage' key in stdout JSON for {hook_name}. "
        f"Got: {parsed}"
    )
    assert hook_name in parsed["systemMessage"], (
        f"Hook name '{hook_name}' not found in systemMessage: "
        f"{parsed['systemMessage']}"
    )
    assert "PACT hook warning" in parsed["systemMessage"]


class TestValidateHandoffErrorOutput:
    """validate_handoff.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from validate_handoff import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "validate_handoff")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from validate_handoff import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "validate_handoff" in captured.err
        assert len(captured.err) > 0


class TestPhaseCompletionErrorOutput:
    """phase_completion.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from phase_completion import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "phase_completion")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from phase_completion import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "phase_completion" in captured.err
        assert len(captured.err) > 0


class TestSessionEndErrorOutput:
    """session_end.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from session_end import main

        env = {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}
        with patch.dict("os.environ", env, clear=True), \
             patch("session_end.get_task_list",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "session_end")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from session_end import main

        env = {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}
        with patch.dict("os.environ", env, clear=True), \
             patch("session_end.get_task_list",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "session_end" in captured.err
        assert "boom" in captured.err


class TestSessionInitErrorOutput:
    """session_init.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from session_init import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "session_init")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from session_init import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "session_init" in captured.err
        assert len(captured.err) > 0


class TestCompactionRefreshErrorOutput:
    """compaction_refresh.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from compaction_refresh import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "compaction_refresh")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain error info (the hook name may vary in format)."""
        from compaction_refresh import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        # compaction_refresh uses slightly different stderr format
        assert len(captured.err) > 0


class TestTeammateIdleErrorOutput:
    """teammate_idle.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ,
                        {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=False), \
             patch("teammate_idle.get_task_list",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "teammate_idle")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ,
                        {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=False), \
             patch("teammate_idle.get_task_list",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "teammate_idle" in captured.err
        assert "boom" in captured.err


class TestTrackFilesErrorOutput:
    """track_files.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from track_files import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "track_files")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from track_files import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "track_files" in captured.err
        assert len(captured.err) > 0


class TestFileSizeCheckErrorOutput:
    """file_size_check.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from file_size_check import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "file_size_check")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and error info."""
        from file_size_check import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "file_size_check" in captured.err
        assert len(captured.err) > 0


class TestTeammateCompletionGateErrorOutput:
    """teammate_completion_gate.py exception handler produces JSON on stdout.

    This hook previously had a bare sys.exit(0) with no stderr.
    Now it outputs both stderr and stdout JSON.
    """

    def test_exception_outputs_json_with_system_message(self, capsys):
        from teammate_completion_gate import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })

        with patch("teammate_completion_gate._scan_owned_tasks",
                    side_effect=RuntimeError("test error")), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ,
                        {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "teammate_completion_gate")

    def test_exception_now_outputs_stderr(self, capsys):
        """Previously had no stderr output — now it does."""
        from teammate_completion_gate import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
            "team_name": "pact-test",
        })

        with patch("teammate_completion_gate._scan_owned_tasks",
                    side_effect=RuntimeError("boom")), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch.dict(os.environ,
                        {"CLAUDE_CODE_TEAM_NAME": "pact-test"}, clear=False):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "teammate_completion_gate" in captured.err
        assert "boom" in captured.err


class TestMemoryAdhocReminderErrorOutput:
    """memory_adhoc_reminder.py exception handler produces JSON on stdout.

    This hook previously had a bare sys.exit(0) with no stderr.
    Now it outputs both stderr and stdout JSON.
    """

    def test_exception_outputs_json_with_system_message(self, capsys):
        from memory_adhoc_reminder import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "memory_adhoc_reminder")

    def test_exception_now_outputs_stderr(self, capsys):
        """Previously had no stderr output — now it does."""
        from memory_adhoc_reminder import main

        with patch("sys.stdin", side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "memory_adhoc_reminder" in captured.err
        assert len(captured.err) > 0


# =============================================================================
# Category B: Hooks that still use _SUPPRESS_OUTPUT on happy paths
# =============================================================================

class TestGitCommitCheckErrorOutput:
    """git_commit_check.py uses _SUPPRESS_OUTPUT on happy path, hook_error_json on error."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        """Error path: should output JSON with systemMessage, NOT _SUPPRESS_OUTPUT."""
        from git_commit_check import main

        input_data = json.dumps({
            "tool_input": {"command": "git commit -m 'test'"}
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("git_commit_check.get_staged_files",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "git_commit_check")
        # Must NOT contain suppressOutput in the error path
        parsed = _parse_stdout_json(captured.out)
        assert "suppressOutput" not in parsed

    def test_exception_preserves_stderr(self, capsys):
        from git_commit_check import main

        input_data = json.dumps({
            "tool_input": {"command": "git commit -m 'test'"}
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("git_commit_check.get_staged_files",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "git_commit_check" in captured.err
        assert "boom" in captured.err

    def test_happy_path_still_uses_suppress_output(self, capsys):
        """Non-commit commands should still output suppressOutput JSON."""
        from git_commit_check import main

        input_data = json.dumps({
            "tool_input": {"command": "git status"}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == {"suppressOutput": True}


class TestMergeGuardPostErrorOutput:
    """merge_guard_post.py uses _SUPPRESS_OUTPUT on happy path, hook_error_json on error."""

    def _make_merge_input(self):
        """Create valid AskUserQuestion input with merge question + affirmative."""
        return json.dumps({
            "tool_input": {
                "questions": [{"question": "Merge PR #42?"}]
            },
            "tool_output": {
                "answers": {"Merge PR #42?": "yes"}
            },
        })

    def test_exception_outputs_json_with_system_message(self, capsys):
        """Error path: should output JSON with systemMessage, NOT _SUPPRESS_OUTPUT."""
        from merge_guard_post import main

        with patch("sys.stdin", io.StringIO(self._make_merge_input())), \
             patch("merge_guard_post.is_merge_question", return_value=True), \
             patch("merge_guard_post.is_affirmative", return_value=True), \
             patch("merge_guard_post.extract_context",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "merge_guard_post")
        # Must NOT contain suppressOutput in the error path
        parsed = _parse_stdout_json(captured.out)
        assert "suppressOutput" not in parsed

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from merge_guard_post import main

        with patch("sys.stdin", io.StringIO(self._make_merge_input())), \
             patch("merge_guard_post.is_merge_question", return_value=True), \
             patch("merge_guard_post.is_affirmative", return_value=True), \
             patch("merge_guard_post.extract_context",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "merge_guard_post" in captured.err
        assert "boom" in captured.err

    def test_happy_path_still_uses_suppress_output(self, capsys):
        """Non-merge questions should still output suppressOutput JSON."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [{"question": "What color?"}]
            },
            "tool_output": {
                "answers": {"What color?": "blue"}
            },
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed == {"suppressOutput": True}


# =============================================================================
# Edge Cases: Error message content
# =============================================================================

class TestErrorOutputEdgeCases:
    """Edge cases for error messages that could break JSON or display."""

    def test_exception_with_curly_braces(self):
        """Error messages containing JSON-like content."""
        from shared.error_output import hook_error_json

        error = RuntimeError('Expected {"key": "value"} but got null')
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert '{"key": "value"}' in parsed["systemMessage"]

    def test_exception_with_null_bytes(self):
        """Error messages containing null bytes."""
        from shared.error_output import hook_error_json

        error = RuntimeError("data\x00corrupted")
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert "corrupted" in parsed["systemMessage"]

    def test_exception_with_tab_characters(self):
        """Error messages containing tabs."""
        from shared.error_output import hook_error_json

        error = RuntimeError("col1\tcol2\tcol3")
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert "col1\tcol2\tcol3" in parsed["systemMessage"]

    def test_os_error_with_errno(self):
        """OSError includes errno in str representation."""
        from shared.error_output import hook_error_json

        error = OSError(2, "No such file or directory")
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert "No such file or directory" in parsed["systemMessage"]

    def test_key_error_with_quotes(self):
        """KeyError wraps the key in quotes in its str representation."""
        from shared.error_output import hook_error_json

        error = KeyError("missing_key")
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)
        assert "missing_key" in parsed["systemMessage"]


# =============================================================================
# Module-level export: hook_error_json is importable from shared
# =============================================================================

class TestModuleExport:
    """Verify hook_error_json is properly exported from shared package."""

    def test_importable_from_shared(self):
        """hook_error_json should be importable from shared package."""
        from shared import hook_error_json as fn
        assert callable(fn)

    def test_importable_from_error_output(self):
        """hook_error_json should be importable from shared.error_output."""
        from shared.error_output import hook_error_json as fn
        assert callable(fn)

    def test_in_shared_all(self):
        """hook_error_json should be listed in shared.__all__."""
        import shared
        assert "hook_error_json" in shared.__all__
