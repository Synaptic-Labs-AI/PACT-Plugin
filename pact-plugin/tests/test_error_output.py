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
from unittest.mock import patch

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

    def test_very_long_error_message_is_truncated(self):
        """Very long error messages are truncated to _ERROR_MAX_CHARS (200).

        Pathological exception messages (huge stdin echoed back, full
        traceback strings, base64 blobs) would otherwise produce a
        multi-megabyte JSON line and overwhelm the UI. The truncation
        cap matches failure_log._ERROR_MAX_CHARS for consistency.
        """
        from shared.error_output import _ERROR_MAX_CHARS, hook_error_json

        long_msg = "x" * 10_000
        error = RuntimeError(long_msg)
        result = hook_error_json("test_hook", error)
        parsed = json.loads(result)

        # Only the truncated prefix appears
        truncated = "x" * _ERROR_MAX_CHARS
        assert truncated in parsed["systemMessage"]
        # Full message does NOT appear
        assert long_msg not in parsed["systemMessage"]
        # The systemMessage is bounded: prefix ("PACT hook warning (test_hook): ")
        # plus exactly _ERROR_MAX_CHARS error chars
        prefix = "PACT hook warning (test_hook): "
        assert parsed["systemMessage"] == prefix + truncated

    def test_truncation_at_exact_boundary(self):
        """An error of exactly _ERROR_MAX_CHARS chars is preserved unchanged."""
        from shared.error_output import _ERROR_MAX_CHARS, hook_error_json

        msg = "y" * _ERROR_MAX_CHARS
        result = hook_error_json("test_hook", RuntimeError(msg))
        parsed = json.loads(result)
        assert msg in parsed["systemMessage"]

    def test_short_error_message_is_unchanged(self):
        """Short error messages (< _ERROR_MAX_CHARS) pass through verbatim."""
        from shared.error_output import hook_error_json

        msg = "small error"
        result = hook_error_json("test_hook", RuntimeError(msg))
        parsed = json.loads(result)
        assert parsed["systemMessage"] == f"PACT hook warning (test_hook): {msg}"

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

    def test_very_long_hook_name_is_truncated(self):
        """Pathological hook_name is truncated to _HOOK_NAME_MAX_CHARS."""
        from shared.error_output import _HOOK_NAME_MAX_CHARS, hook_error_json

        long_name = "h" * 500
        result = hook_error_json(long_name, RuntimeError("err"))
        parsed = json.loads(result)
        truncated = "h" * _HOOK_NAME_MAX_CHARS
        assert truncated in parsed["systemMessage"]
        assert long_name not in parsed["systemMessage"]


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


class TestSessionEndErrorOutput:
    """session_end.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from session_end import main

        env = {"CLAUDE_PROJECT_DIR": "/tmp/test-project"}
        with patch.dict("os.environ", env, clear=True), \
             patch("sys.stdin", io.StringIO("{}")), \
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
             patch("sys.stdin", io.StringIO("{}")), \
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


# TestCompactionRefreshErrorOutput removed in #444: compaction_refresh.py
# was deleted and its responsibilities folded into session_init.py's
# source=compact branch. The session_init exception handler is covered
# by TestSessionInitErrorOutput above (exception_outputs_json_with_system_message,
# exception_outputs_valid_json_structure, etc.) — its hook_error_json shape
# is the same.


class TestTeammateIdleErrorOutput:
    """teammate_idle.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from teammate_idle import main

        input_data = json.dumps({
            "teammate_name": "backend-coder",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_team_name", return_value="pact-test"), \
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
             patch("teammate_idle.get_team_name", return_value="pact-test"), \
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


class TestPrecompactStateReminderErrorOutput:
    """precompact_state_reminder.py exception handler produces JSON on stdout."""

    def test_exception_outputs_json_with_system_message(self, capsys):
        from precompact_state_reminder import main

        with patch("sys.stdin", io.StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "precompact_state_reminder")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from precompact_state_reminder import main

        with patch("sys.stdin", io.StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "precompact_state_reminder" in captured.err
        assert "boom" in captured.err


class TestPostcompactArchiveErrorOutput:
    """postcompact_archive.py exception handler produces JSON on stdout.

    Post-#444: build_verification_message was deleted. The outer try/except
    in main() now wraps write_compact_summary (the only external call
    remaining on the happy path), so tests patch that function instead.
    Post-PR-#447: module renamed from postcompact_verify to postcompact_archive.
    """

    def test_exception_outputs_json_with_system_message(self, capsys):
        from postcompact_archive import main

        input_data = json.dumps({
            "compact_summary": "test summary",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("postcompact_archive.write_compact_summary",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_error_json(captured.out, "postcompact_archive")

    def test_exception_preserves_stderr(self, capsys):
        """stderr should contain the hook name and the specific error message."""
        from postcompact_archive import main

        input_data = json.dumps({
            "compact_summary": "test summary",
        })

        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("postcompact_archive.write_compact_summary",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "postcompact_archive" in captured.err
        assert "boom" in captured.err


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


# =============================================================================
# Category C: Lifecycle hooks with _SUPPRESS_OUTPUT on bare exit paths (#316)
# =============================================================================

_SUPPRESS_EXPECTED = {"suppressOutput": True}


def _assert_suppress_output(captured_out: str):
    """Assert stdout contains exactly the suppressOutput JSON."""
    assert json.loads(captured_out.strip()) == _SUPPRESS_EXPECTED


# TestCompactionRefreshSuppressOutput removed in #444: compaction_refresh.py
# was deleted. The "no in-progress tasks → no checkpoint block" invariant is
# covered by TestSessionInitCompactPhantomWorkflow in test_session_init.py.


class TestSessionEndSuppressOutput:
    """session_end.py bare exit path outputs _SUPPRESS_OUTPUT (#316)."""

    def test_success_path_suppress(self, capsys, tmp_path):
        """Normal session end outputs suppressOutput."""
        from session_end import main

        env = {
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        }
        with patch("sys.stdin", io.StringIO("{}")), \
             patch.dict(os.environ, env, clear=False), \
             patch("session_end.get_task_list", return_value=[]), \
             patch("session_end.check_unpaused_pr"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from session_end import main

        with patch("session_end.get_project_slug",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed


class TestAgentHandoffEmitterSuppressOutput:
    """agent_handoff_emitter.py bare exit paths output _SUPPRESS_OUTPUT (#538)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from agent_handoff_emitter import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_success_path_suppress(self, tmp_path, monkeypatch, capsys):
        """All gates passed, journal entry written -> suppressOutput."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_handoff_emitter import main

        input_data = json.dumps({
            "task_id": "1",
            "task_subject": "CODE: test",
            "teammate_name": "coder",
            "team_name": "pact-test",
        })
        task_data = {
            "status": "completed",
            "owner": "coder",
            "metadata": {
                "handoff": {
                    "produced": "x", "decisions": "x",
                    "uncertainty": "x", "integration": "x",
                    "open_questions": "x",
                },
            },
        }
        with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
             patch("agent_handoff_emitter.append_event"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestTeammateIdleSuppressOutput:
    """teammate_idle.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_no_team_name_suppress(self, capsys):
        """Missing team name outputs suppressOutput."""
        from teammate_idle import main

        with patch("teammate_idle.get_team_name", return_value=""), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from teammate_idle import main

        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_tasks_suppress(self, capsys):
        """Empty task list outputs suppressOutput."""
        from teammate_idle import main

        input_data = json.dumps({"teammate_name": "coder"})
        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list", return_value=[]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_teammate_name_suppress(self, capsys):
        """Missing teammate_name outputs suppressOutput."""
        from teammate_idle import main

        input_data = json.dumps({"teammate_name": ""})
        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list", return_value=[{"id": "1"}]):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_messages_suppress(self, capsys, tmp_path):
        """No stall or cleanup messages outputs suppressOutput."""
        from teammate_idle import main

        # Completed task with no stall and below idle threshold -> no messages
        tasks = [{"id": "1", "subject": "test", "status": "completed", "owner": "other-agent"}]
        input_data = json.dumps({"teammate_name": "coder"})
        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list", return_value=tasks), \
             patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from teammate_idle import main

        input_data = json.dumps({"teammate_name": "coder"})
        with patch("teammate_idle.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("teammate_idle.get_task_list",
                   side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed


class TestValidateHandoffSuppressOutput:
    """validate_handoff.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from validate_handoff import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_non_pact_agent_suppress(self, capsys):
        """Non-PACT agent outputs suppressOutput."""
        from validate_handoff import main

        input_data = json.dumps({"agent_id": "custom-agent", "transcript": "hello"})
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from validate_handoff import main

        with patch("sys.stdin", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed


class TestPeerInjectSuppressOutput:
    """peer_inject.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from peer_inject import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_context_suppress(self, capsys):
        """No peer context outputs suppressOutput."""
        from peer_inject import main

        input_data = json.dumps({"agent_type": "pact-test"})
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestAuditorReminderSuppressOutput:
    """auditor_reminder.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from auditor_reminder import main

        with patch("sys.stdin", io.StringIO("bad")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_reminder_suppress(self, capsys):
        """No auditor reminder needed outputs suppressOutput."""
        from auditor_reminder import main

        input_data = json.dumps({
            "tool_name": "Task",
            "tool_input": {"subagent_type": "pact-test-engineer"},
        })
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from auditor_reminder import main

        with patch("sys.stdin", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed


class TestSessionInitSuppressOutput:
    """session_init.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_success_path_suppress(self, capsys, tmp_path):
        """Normal session init with no messages outputs suppressOutput."""
        from session_init import main

        input_data = json.dumps({"source": "startup", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("session_init.check_additional_directories", return_value=None), \
             patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.remove_stale_kernel_block", return_value=None), \
             patch("session_init.update_pact_routing", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.generate_team_name", return_value="pact-test123"), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(tmp_path)}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # session_init always produces output (team instructions at minimum)
        assert captured.out.strip()

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from session_init import main

        with patch("sys.stdin", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed


class TestFileSizeCheckSuppressOutput:
    """file_size_check.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from file_size_check import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_non_edit_write_tool_suppress(self, capsys):
        """Non-Edit/Write tool outputs suppressOutput."""
        from file_size_check import main

        input_data = json.dumps({"tool_name": "Read", "tool_input": {}})
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_file_path_suppress(self, capsys):
        """Missing file_path in tool_input outputs suppressOutput."""
        from file_size_check import main

        input_data = json.dumps({"tool_name": "Edit", "tool_input": {}})
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_excluded_path_suppress(self, capsys):
        """File in excluded directory outputs suppressOutput."""
        from file_size_check import main

        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/node_modules/pkg/index.js"},
        })
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_non_source_file_suppress(self, capsys):
        """Non-source file extension outputs suppressOutput."""
        from file_size_check import main

        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/README.md"},
        })
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_file_doesnt_exist_suppress(self, capsys, tmp_path):
        """Non-existent file outputs suppressOutput."""
        from file_size_check import main

        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "nonexistent.py")},
        })
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_below_threshold_suppress(self, capsys, tmp_path):
        """File below threshold outputs suppressOutput."""
        from file_size_check import main

        # Create a small Python file
        small_file = tmp_path / "small.py"
        small_file.write_text("x = 1\n" * 10)

        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(small_file)},
        })
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from file_size_check import main

        with patch("sys.stdin", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed


class TestFileTrackerSuppressOutput:
    """file_tracker.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_no_team_name_suppress(self, capsys):
        """Missing team name outputs suppressOutput."""
        from file_tracker import main

        with patch("file_tracker.get_team_name", return_value=""), \
             patch("sys.stdin", io.StringIO("{}")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from file_tracker import main

        with patch("file_tracker.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_file_path_suppress(self, capsys):
        """Missing file_path outputs suppressOutput."""
        from file_tracker import main

        input_data = json.dumps({"tool_input": {}})
        with patch("file_tracker.get_team_name", return_value="pact-test"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_no_conflict_suppress(self, capsys, tmp_path):
        """No conflict (normal edit) outputs suppressOutput."""
        from file_tracker import main

        input_data = json.dumps({
            "tool_input": {"file_path": "/tmp/test.py"},
            "tool_name": "Edit",
        })
        with patch("file_tracker.get_team_name", return_value="pact-test"), \
             patch("file_tracker.resolve_agent_name", return_value="coder"), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)


class TestTrackFilesSuppressOutput:
    """track_files.py bare exit paths output _SUPPRESS_OUTPUT (#316)."""

    def test_invalid_json_suppress(self, capsys):
        """JSONDecodeError path outputs suppressOutput."""
        from track_files import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_non_edit_write_tool_suppress(self, capsys):
        """Non-Edit/Write tool outputs suppressOutput."""
        from track_files import main

        input_data = json.dumps({"tool_name": "Read", "tool_input": {}})
        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_success_path_suppress(self, capsys):
        """Normal tracking (Edit tool with file_path) outputs suppressOutput."""
        from track_files import main

        input_data = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/test.py"},
        })
        with patch("sys.stdin", io.StringIO(input_data)), \
             patch("track_files.track_file"):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        _assert_suppress_output(captured.out)

    def test_error_path_uses_hook_error_json(self, capsys):
        """Exception path outputs hook_error_json, NOT suppressOutput."""
        from track_files import main

        with patch("sys.stdin", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "systemMessage" in parsed
        assert "suppressOutput" not in parsed
