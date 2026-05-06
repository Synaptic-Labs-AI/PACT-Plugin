"""
Tests for bootstrap_prompt_gate.py — UserPromptSubmit hook that injects
bootstrap-first instructions until bootstrap-complete marker exists.

Tests cover:
1. Marker exists → suppressOutput (fast path, zero tokens)
2. No marker + PACT team-lead session → inject additionalContext with bootstrap instruction
3. Non-PACT session (no session dir) → suppressOutput (no-op passthrough)
4. Teammate (agent_name non-empty) → suppressOutput (no-op passthrough)
5. Malformed stdin JSON → fail-open (suppressOutput, exit 0)
6. Exception in _check_bootstrap_needed → fail-open (suppressOutput, exit 0)
7. main() entry point: exit codes, output format, JSON structure
8. Error/suppress mutual exclusivity: never emits systemMessage
9. Injection content includes required instruction text
10. Marker file lifecycle: create → check → gate behavior changes
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import BOOTSTRAP_MARKER_NAME

_SUPPRESS_EXPECTED = {"suppressOutput": True}

# Session identity constants used across all tests
_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"


# =============================================================================
# Helpers
# =============================================================================


def _make_input(session_id=_SESSION_ID, source="startup"):
    """Build a minimal UserPromptSubmit hook input dict."""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": "Hello world",
        "source": source,
    }


def _run_main(input_data, capsys):
    """Run bootstrap_prompt_gate.main() with the given input, return (exit_code, stdout_json)."""
    from bootstrap_prompt_gate import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    return exc_info.value.code, json.loads(captured.out.strip())


def _setup_pact_session(monkeypatch, tmp_path, with_marker=False):
    """Set up a PACT session context with session dir under tmp_path.

    Monkeypatches Path.home to tmp_path so get_session_dir() returns a
    path under tmp_path. Writes a context file and patches pact_context
    module state.

    Returns the session_dir path.
    """
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Build session dir path matching what get_session_dir() will compute
    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    # Write context file in the session dir
    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": "",
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": "",
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    # Patch pact_context module to use this context file
    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_marker:
        (session_dir / BOOTSTRAP_MARKER_NAME).touch()

    return session_dir


# =============================================================================
# _check_bootstrap_needed — unit tests
# =============================================================================


class TestCheckBootstrapNeeded:
    """Tests for _check_bootstrap_needed() decision logic."""

    def test_returns_none_when_marker_exists(self, monkeypatch, tmp_path):
        """Marker exists → None (suppress path)."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        result = _check_bootstrap_needed(_make_input())
        assert result is None

    def test_returns_instruction_when_no_marker(self, monkeypatch, tmp_path):
        """No marker + team-lead session → bootstrap instruction string with session dir."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_bootstrap_needed(_make_input())
        assert result is not None
        assert 'Skill("PACT:bootstrap")' in result
        assert f"PACT_SESSION_DIR={session_dir}" in result

    def test_returns_none_when_no_session_dir(self, monkeypatch):
        """No session dir → None (non-PACT session)."""
        from bootstrap_prompt_gate import _check_bootstrap_needed
        import shared.pact_context as ctx_module

        # No context → get_session_dir() returns ""
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = _check_bootstrap_needed(_make_input())
        assert result is None

    def test_returns_none_for_teammate(self, monkeypatch, tmp_path):
        """Teammate (agent_name resolved) → None (passthrough)."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input()
        input_data["agent_name"] = "backend-coder"

        result = _check_bootstrap_needed(input_data)
        assert result is None

    def test_teammate_with_agent_id_format(self, monkeypatch, tmp_path):
        """Teammate identified via agent_id 'name@team' format → None."""
        from bootstrap_prompt_gate import _check_bootstrap_needed
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        # Override context to have a team_name (needed for agent_id resolution)
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": "pact-test1234",
            "session_id": _SESSION_ID,
            "project_dir": _PROJECT_DIR,
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        ctx_module._cache = None

        input_data = _make_input()
        input_data["agent_id"] = "backend-coder@pact-test1234"

        result = _check_bootstrap_needed(input_data)
        assert result is None

    def test_instruction_content_mentions_bootstrap_skill(self, monkeypatch, tmp_path):
        """The injected instruction must reference Skill("PACT:bootstrap")."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_bootstrap_needed(_make_input())
        assert result is not None
        assert 'Skill("PACT:bootstrap")' in result

    def test_instruction_mentions_blocked_tools(self, monkeypatch, tmp_path):
        """The injected instruction should mention which tools are blocked."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_bootstrap_needed(_make_input())
        assert result is not None
        assert "Edit" in result
        assert "Write" in result


# =============================================================================
# main() — integration tests
# =============================================================================


class TestMainEntryPoint:
    """Tests for main() stdin/stdout/exit behavior."""

    def test_exits_0_on_inject(self, monkeypatch, tmp_path, capsys):
        """Even when injecting, exit code is 0 (never blocks prompts)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        exit_code, output = _run_main(_make_input(), capsys)
        assert exit_code == 0
        assert "hookSpecificOutput" in output

    def test_injects_additional_context_when_no_marker(self, monkeypatch, tmp_path, capsys):
        """No marker → output has hookSpecificOutput.additionalContext."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input(), capsys)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in hso
        assert 'Skill("PACT:bootstrap")' in hso["additionalContext"]

    def test_suppress_when_marker_exists(self, monkeypatch, tmp_path, capsys):
        """Marker exists → suppressOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        _, output = _run_main(_make_input(), capsys)
        assert output == _SUPPRESS_EXPECTED

    def test_suppress_for_non_pact_session(self, capsys, monkeypatch):
        """Non-PACT session (no context) → suppressOutput."""
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        _, output = _run_main(_make_input(), capsys)
        assert output == _SUPPRESS_EXPECTED

    def test_suppress_for_teammate(self, monkeypatch, tmp_path, capsys):
        """Teammate → suppressOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input()
        input_data["agent_name"] = "backend-coder"

        _, output = _run_main(input_data, capsys)
        assert output == _SUPPRESS_EXPECTED


# =============================================================================
# Fail-open — P0 priority
# =============================================================================


class TestFailOpen:
    """P0: Every exception path must fail-open (exit 0, suppressOutput)."""

    def test_malformed_stdin_json(self, capsys):
        """Invalid JSON on stdin → fail-open."""
        from bootstrap_prompt_gate import main

        with patch("sys.stdin", io.StringIO("not valid json {")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_empty_stdin(self, capsys):
        """Empty stdin → fail-open."""
        from bootstrap_prompt_gate import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_exception_in_check_bootstrap_needed(self, capsys):
        """RuntimeError in _check_bootstrap_needed → fail-open."""
        from bootstrap_prompt_gate import main

        with patch(
            "bootstrap_prompt_gate._check_bootstrap_needed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_oserror_in_marker_check_treats_marker_absent(
        self, monkeypatch, tmp_path, capsys
    ):
        """OSError on the marker check is now caught INSIDE
        `is_marker_set` (post R2-B1 / commit 5b12f805) and treated as
        marker-absent → bootstrap directive injected, gate stays armed.
        Pre-R2-B1: `Path.exists()` raise propagated to the outer except
        and produced suppressOutput. Post-R2-B1: bootstrap_prompt_gate
        delegates to bootstrap_gate.is_marker_set, which has the same
        conservative-fail-closed semantics as the sibling gate (the
        Cycle-2 S2 fix established this contract for the gate; R2-B1
        propagates the same contract to the prompt-gate).

        The OUTER fail-open contract still holds for genuine programmer
        errors above the marker-check layer; this test pins the marker-
        layer-specific OSError → marker-absent classification."""
        from bootstrap_prompt_gate import main

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        # Patch os.lstat (used inside is_marker_set) to raise OSError.
        with patch("os.lstat", side_effect=OSError("disk error")):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        # Marker absent + lead session → bootstrap directive injected.
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "hookSpecificOutput" in output
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "Skill(\"PACT:bootstrap\")" in output["hookSpecificOutput"]["additionalContext"]


# =============================================================================
# Error/suppress mutual exclusivity — P0 priority
# =============================================================================


class TestErrorSuppressMutualExclusivity:
    """P0: These hooks use suppressOutput for fail-open, never systemMessage."""

    def test_malformed_stdin_no_system_message(self, capsys):
        """Malformed stdin outputs suppressOutput, not systemMessage."""
        from bootstrap_prompt_gate import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed

    def test_exception_no_system_message(self, capsys):
        """Exception in gate logic outputs suppressOutput, not systemMessage."""
        from bootstrap_prompt_gate import main

        with patch(
            "bootstrap_prompt_gate._check_bootstrap_needed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit):
                    main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed

    def test_inject_path_no_suppress_output(self, monkeypatch, tmp_path, capsys):
        """When injecting context, output has hookSpecificOutput, not suppressOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input(), capsys)
        assert "hookSpecificOutput" in output
        assert "suppressOutput" not in output


# =============================================================================
# Marker lifecycle — P3 priority
# =============================================================================


class TestMarkerLifecycle:
    """P3: Marker creation → gate self-disable → idempotent suppress."""

    def test_gate_transitions_on_marker_creation(self, monkeypatch, tmp_path, capsys):
        """Before marker: inject. After marker: suppress."""
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        # Before marker — should inject
        _, output_before = _run_main(_make_input(), capsys)
        assert "hookSpecificOutput" in output_before

        # Create marker
        (session_dir / BOOTSTRAP_MARKER_NAME).touch()

        # Reset cache for second call
        ctx_module._cache = None

        # After marker — should suppress
        _, output_after = _run_main(_make_input(), capsys)
        assert output_after == _SUPPRESS_EXPECTED

    def test_repeated_calls_with_marker_are_idempotent(self, monkeypatch, tmp_path, capsys):
        """Multiple calls with marker present all produce suppressOutput."""
        import shared.pact_context as ctx_module

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        for _ in range(3):
            ctx_module._cache = None
            _, output = _run_main(_make_input(), capsys)
            assert output == _SUPPRESS_EXPECTED
