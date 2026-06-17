"""
Tests for bootstrap_prompt_gate.py — UserPromptSubmit hook that injects
bootstrap-first instructions until bootstrap-complete marker exists.

Tests cover:
1. Marker exists → suppressOutput (fast path, zero tokens)
2. No marker + PACT team-lead session → inject additionalContext with bootstrap instruction
3. Non-PACT session (no session dir) → suppressOutput (no-op passthrough)
4. Teammate / non-lead frame (non-lead agent_type) → suppressOutput (no-op passthrough)
5. Malformed stdin JSON → fail-open (suppressOutput, exit 0)
6. Exception in _check_bootstrap_needed → fail-open (suppressOutput, exit 0)
7. main() entry point: exit codes, output format, JSON structure
8. Error/suppress mutual exclusivity: never emits systemMessage
9. Injection content includes required instruction text
10. Marker file lifecycle: create → check → gate behavior changes
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import BOOTSTRAP_MARKER_NAME

_SUPPRESS_EXPECTED = {
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"},
}

# Session identity constants used across all tests
_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"


# =============================================================================
# Helpers
# =============================================================================


def _make_input(session_id=_SESSION_ID, source="startup",
                agent_type="pact-orchestrator"):
    """Build a minimal UserPromptSubmit hook input dict.

    #878: the gate now keys lead-detection on the harness-set agent_type via
    is_lead. The default is a LEAD frame (the unmarked case these tests
    historically assumed). Teammate/non-lead tests pass agent_type=<teammate>
    or agent_type=None to exercise the bypass branch.
    """
    data = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": "Hello world",
        "source": source,
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def _run_main(input_data, capsys):
    """Run bootstrap_prompt_gate.main() with the given input, return (exit_code, stdout_json)."""
    from bootstrap_prompt_gate import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    return exc_info.value.code, json.loads(captured.out.strip())


def _setup_pact_session(monkeypatch, tmp_path, with_marker=False,
                        plugin_version="9.9.9"):
    """Set up a PACT session context with session dir under tmp_path.

    Monkeypatches Path.home to tmp_path so get_session_dir() returns a
    path under tmp_path. Writes a context file and patches pact_context
    module state. When ``with_marker=True``, writes a properly-stamped
    properly-stamped marker (post-#662); empty `touch` markers no longer satisfy the
    gate.

    Returns the session_dir path.
    """
    import hashlib
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": plugin_version}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": "",
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_marker:
        sid = session_dir.name
        sig = hashlib.sha256(
            f"{sid}|{str(plugin_root).rstrip('/')}|{plugin_version}|1".encode()
        ).hexdigest()
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            json.dumps({"v": 1, "sid": sid, "sig": sig}),
            encoding="utf-8",
        )

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
        """Teammate (non-lead agent_type) → None (passthrough).

        #878: lead-detection migrated to is_lead, which keys on agent_type. A
        specialist agent_type is not a lead spelling, so the gate bypasses.
        """
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input(agent_type="pact-backend-coder")

        result = _check_bootstrap_needed(input_data)
        assert result is None

    def test_teammate_with_qualified_agent_type(self, monkeypatch, tmp_path):
        """Teammate carrying a qualified non-lead agent_type → None.

        #878: the gate no longer resolves agent_id/agent_name — it reads
        agent_type directly. A `PACT:`-qualified specialist type is still not a
        lead spelling, so the gate bypasses.
        """
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input(agent_type="PACT:pact-backend-coder")

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
        """Teammate (non-lead agent_type) → suppressOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input(agent_type="pact-backend-coder")

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
        `is_marker_set` and treated as marker-absent → bootstrap directive
        injected, gate stays armed. Previously, `Path.exists()` raises
        propagated to the outer except and produced suppressOutput. The
        current contract: bootstrap_prompt_gate delegates to
        bootstrap_gate.is_marker_set, which has the same
        conservative-fail-closed semantics as the sibling gate.

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
        """Before marker: inject. After marker stamp: suppress."""
        import hashlib
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"

        # Before marker — should inject
        _, output_before = _run_main(_make_input(), capsys)
        assert "hookSpecificOutput" in output_before

        # Stamp a properly-formed marker (post-#662)
        sid = session_dir.name
        sig = hashlib.sha256(
            f"{sid}|{str(plugin_root).rstrip('/')}|9.9.9|1".encode()
        ).hexdigest()
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            json.dumps({"v": 1, "sid": sid, "sig": sig}),
            encoding="utf-8",
        )

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


# =============================================================================
# Audit-anchor parity (mirror of writer's emit-shape invariant)
# =============================================================================


class TestAuditAnchorParity:
    """Every JSON output path bootstrap_prompt_gate produces MUST carry
    hookSpecificOutput.hookEventName == "UserPromptSubmit". Missing the
    field silently fails open at the platform layer (per pinned context).
    The invariant is parametrized over the two distinct emit shapes:

    - "advisory": _emit_load_failure_advisory module-load advisory
      (additionalContext path)
    - "suppress": every other exit path via the _SUPPRESS_OUTPUT constant

    Both MUST carry the audit anchor — parametrizing pins the invariant
    so no future emit path can be added without it. Mirrors
    bootstrap_marker_writer's test_every_emit_shape_carries_hook_event_name
    so all three bootstrap-related hooks share one parity contract.
    """

    @pytest.mark.parametrize("shape", ["advisory", "suppress"])
    def test_every_emit_shape_carries_hook_event_name(self, shape, capsys):
        if shape == "advisory":
            from bootstrap_prompt_gate import _emit_load_failure_advisory
            with pytest.raises(SystemExit):
                _emit_load_failure_advisory("module imports", RuntimeError("x"))
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "suppress":
            from bootstrap_prompt_gate import _SUPPRESS_OUTPUT
            out = json.loads(_SUPPRESS_OUTPUT)
        else:  # pragma: no cover
            pytest.fail(f"unknown shape param: {shape}")

        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"shape={shape} emit MUST carry hookSpecificOutput; missing "
            f"the field silently fails open at the platform layer."
        )
        assert hso.get("hookEventName") == "UserPromptSubmit", (
            f"shape={shape} emit MUST carry hookEventName=='UserPromptSubmit'; "
            f"got {hso!r}"
        )


class TestSelfHealWiring:
    """Wiring tests for the heal_context_if_missing call in
    _check_bootstrap_needed: a missing context file is healed in-line
    (lead frame + valid session_id), after which the SAME invocation
    resolves the session dir and flows into the normal no-marker inject
    branch — the heal unbricks the gate without forging bootstrap.
    """

    _SID = "deadbeef-7777-8888-9999-aaaabbbbcccc"

    def test_missing_context_healed_then_instruction_injected(
            self, monkeypatch, tmp_path):
        """Context file ABSENT + lead frame → healed + bootstrap
        instruction returned (chain effect; heal does NOT suppress)."""
        import shared.pact_context as ctx_module
        from bootstrap_prompt_gate import _check_bootstrap_needed

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/test/project")

        # init() is a no-op when _context_path is pre-set: derive the path
        # exactly as a fresh hook process would, but pointed under tmp_path.
        target = (tmp_path / ".claude" / "pact-sessions" / "project" /
                  self._SID / "pact-session-context.json")
        monkeypatch.setattr(ctx_module, "_context_path", target)
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert not target.exists()

        result = _check_bootstrap_needed(_make_input(session_id=self._SID))

        assert target.exists(), "gate should heal the missing context file"
        assert result is not None, (
            "healed lead session without marker must flow into the inject "
            "branch, not suppress"
        )
        assert "PACT:bootstrap" in result
        assert "PACT_SESSION_DIR=" in result

    def test_missing_context_plain_frame_no_heal_no_inject(
            self, monkeypatch, tmp_path):
        """Context file ABSENT + plain frame (agent_type absent) → no heal,
        no instruction (existing non-PACT passthrough preserved)."""
        import shared.pact_context as ctx_module
        from bootstrap_prompt_gate import _check_bootstrap_needed

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/test/project")

        target = (tmp_path / ".claude" / "pact-sessions" / "project" /
                  self._SID / "pact-session-context.json")
        monkeypatch.setattr(ctx_module, "_context_path", target)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = _check_bootstrap_needed(
            _make_input(session_id=self._SID, agent_type=None)
        )

        assert not target.exists()
        assert result is None


class TestStalenessDetection:
    """Unit tests for _detect_stale_session_block() — the stdlib-only
    Resume-line session_id compare that flags a stale CLAUDE.md 'Current
    Session' block after a session_init crash."""

    _ACTUAL = "deadbeef-0000-1111-2222-333344445555"
    _STALE = "01dcafe0-9999-8888-7777-666655554444"

    def _project_with_claude_md(self, tmp_path, recorded_sid,
                                location=".claude"):
        """Create a project dir whose CLAUDE.md records ``recorded_sid``."""
        project = tmp_path / "proj"
        if location == ".claude":
            target = project / ".claude" / "CLAUDE.md"
        else:
            target = project / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# Project\n\n## Current Session\n"
            f"- Resume: `claude --resume {recorded_sid}`\n"
            "- Team: `pact-old`\n",
            encoding="utf-8",
        )
        return project

    def test_mismatch_returns_warning(self, monkeypatch, tmp_path):
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = self._project_with_claude_md(tmp_path, self._STALE)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = _detect_stale_session_block({"session_id": self._ACTUAL})

        assert result is not None
        assert "stale session block" in result
        assert self._STALE in result
        assert self._ACTUAL in result

    def test_match_returns_none(self, monkeypatch, tmp_path):
        """Recorded == actual (healthy resume) → no warning."""
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = self._project_with_claude_md(tmp_path, self._ACTUAL)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        assert _detect_stale_session_block(
            {"session_id": self._ACTUAL}) is None

    def test_no_claude_md_returns_none(self, monkeypatch, tmp_path):
        """Worktree case: CLAUDE.md absent at both locations → silent skip."""
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        assert _detect_stale_session_block(
            {"session_id": self._ACTUAL}) is None

    def test_garbage_resume_line_returns_none(self, monkeypatch, tmp_path):
        """Tampered/garbage Resume line that misses the regex → no claim."""
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = tmp_path / "proj"
        target = project / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "## Current Session\n- Resume: claude --resume NOT_HEX_$(rm -rf)\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        assert _detect_stale_session_block(
            {"session_id": self._ACTUAL}) is None

    def test_missing_session_id_returns_none(self, monkeypatch, tmp_path):
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = self._project_with_claude_md(tmp_path, self._STALE)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        assert _detect_stale_session_block({}) is None
        assert _detect_stale_session_block({"session_id": ""}) is None

    def test_missing_project_dir_returns_none(self, monkeypatch):
        from bootstrap_prompt_gate import _detect_stale_session_block

        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        assert _detect_stale_session_block(
            {"session_id": self._ACTUAL}) is None

    def test_non_utf8_claude_md_returns_none(self, monkeypatch, tmp_path):
        """Non-UTF-8 CLAUDE.md (e.g. a latin-1 byte from a wrong-editor
        save, or a partial/corrupted session_init write — the very failure
        neighborhood this detector exists to flag) → silent skip, NOT a
        raise. UnicodeDecodeError is a ValueError, not an OSError; an
        OSError-only catch lets it escape (RED on reverting the widened
        catch tuple)."""
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = tmp_path / "proj"
        target = project / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        # Valid stale Resume line followed by one invalid UTF-8
        # continuation byte (0xE9 = latin-1 'é').
        target.write_bytes(
            f"- Resume: `claude --resume {self._STALE}`\n".encode("utf-8")
            + b"caf\xe9\n"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        assert _detect_stale_session_block(
            {"session_id": self._ACTUAL}) is None

    @pytest.mark.parametrize("layout", ["both", "preferred_only",
                                        "legacy_only", "neither"])
    def test_precedence_parity_with_resolver(self, monkeypatch, tmp_path,
                                             layout):
        """Precedence parity pin (the drift guard for the no-runtime-import
        decision): for every file-existence layout, the staleness reader's
        CHOSEN file must equal resolve_project_claude_md_path's result
        whenever source != 'new_default'. Distinct recorded ids per file
        reveal which one was read. (Tests MAY import claude_md_manager —
        tests are not hooks.)"""
        from bootstrap_prompt_gate import _detect_stale_session_block
        from shared.claude_md_manager import resolve_project_claude_md_path

        sid_by_location = {
            ".claude": "aaaa1111-aaaa-1111-aaaa-111111111111",
            "legacy": "bbbb2222-bbbb-2222-bbbb-222222222222",
        }
        project = tmp_path / "proj"
        project.mkdir()
        if layout in ("both", "preferred_only"):
            self._project_with_claude_md(
                tmp_path, sid_by_location[".claude"], location=".claude")
        if layout in ("both", "legacy_only"):
            self._project_with_claude_md(
                tmp_path, sid_by_location["legacy"], location="legacy")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = _detect_stale_session_block({"session_id": self._ACTUAL})

        resolved_path, source = resolve_project_claude_md_path(str(project))
        if source == "new_default":
            assert result is None, "neither file exists → silent skip"
        else:
            resolver_recorded = _RE_RESUME_TEST.search(
                resolved_path.read_text(encoding="utf-8")
            ).group(1)
            assert result is not None
            assert resolver_recorded in result, (
                f"staleness reader and resolver disagree on which CLAUDE.md "
                f"to read for layout={layout}"
            )


# Test-local mirror of the production regex, used ONLY to extract the
# resolver-chosen file's recorded id in the parity test above.
import re as _re_for_parity  # noqa: E402
_RE_RESUME_TEST = _re_for_parity.compile(
    r"- Resume:\s*`claude --resume\s+([0-9a-f-]+)`"
)


class TestStalenessComposition:
    """Placement tests: staleness composes onto the bootstrap instruction
    ONLY on the lead+no-marker inject branch; the marker-set fast path
    never reads CLAUDE.md (perf contract pin)."""

    _ACTUAL_HEX = "deadbeef-0000-1111-2222-333344445555"
    _STALE = "01dcafe0-9999-8888-7777-666655554444"

    def _stale_project(self, monkeypatch, tmp_path):
        project = tmp_path / "proj"
        target = project / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            f"- Resume: `claude --resume {self._STALE}`\n", encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        return project

    def test_mismatch_composes_instruction_and_warning(
            self, monkeypatch, tmp_path):
        """Lead + no marker + stale block → BOTH the bootstrap instruction
        AND the staleness warning in one additionalContext string."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        self._stale_project(monkeypatch, tmp_path)

        result = _check_bootstrap_needed(_make_input(
            session_id=self._ACTUAL_HEX))

        # NOTE: _setup_pact_session pre-writes the context file keyed on
        # _SESSION_ID; the heal is a no-op here (file present).
        assert result is not None
        assert "PACT:bootstrap" in result            # instruction present
        assert "stale session block" in result       # warning appended
        assert result.index("PACT:bootstrap") < result.index(
            "stale session block"), "warning is APPENDED, not prepended"

    def test_match_returns_instruction_only(self, monkeypatch, tmp_path):
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        project = tmp_path / "proj"
        target = project / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            f"- Resume: `claude --resume {self._ACTUAL_HEX}`\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = _check_bootstrap_needed(_make_input(
            session_id=self._ACTUAL_HEX))

        assert result is not None
        assert "PACT:bootstrap" in result
        assert "stale session block" not in result

    def test_non_utf8_claude_md_keeps_instruction(
            self, monkeypatch, tmp_path, capsys):
        """Blast-radius pin: a non-UTF-8 CLAUDE.md must degrade to
        instruction-only — NEVER suppress the bootstrap instruction.

        The advisory detector composes onto the load-bearing instruction by
        concatenation inside _check_bootstrap_needed; before the catch was
        widened to UnicodeDecodeError, the raise escaped to main()'s
        fail-open and the ENTIRE injection (primary instruction included)
        was silently suppressed on every prompt. End-to-end arm through
        main() so the pin covers the real escape path, not just the helper.
        (RED on reverting the widened catch tuple.)"""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        project = tmp_path / "proj"
        target = project / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(
            f"- Resume: `claude --resume {self._STALE}`\n".encode("utf-8")
            + b"caf\xe9\n"  # one invalid UTF-8 continuation byte
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        exit_code, output = _run_main(
            _make_input(session_id=self._ACTUAL_HEX), capsys)

        assert exit_code == 0
        injected = output.get("hookSpecificOutput", {}).get(
            "additionalContext", "")
        assert "PACT:bootstrap" in injected, (
            "non-UTF-8 CLAUDE.md must not suppress the bootstrap "
            "instruction — the advisory's failure budget is 'no warning', "
            "never 'no bootstrap'"
        )
        assert "stale session block" not in injected, (
            "unreadable CLAUDE.md → no staleness claim"
        )

    @pytest.mark.parametrize("invalid_id", [
        "   ",                                    # whitespace-only (truthy)
        "unknown-deadbeef",                       # sentinel shape
        "deadbeef-0000\nINJECTED LINE",           # C0 control char (newline)
    ], ids=["whitespace_only", "unknown_sentinel", "control_char"])
    def test_invalid_stdin_id_suppresses_warning_keeps_instruction(
            self, monkeypatch, tmp_path, invalid_id):
        """An invalid-but-truthy stdin session_id (whitespace-only,
        `unknown-*` sentinel, or control-char-bearing) must suppress the
        staleness warning — the unvalidated id is never interpolated into
        the warning's {actual} slot — while the bootstrap instruction
        stays intact. Gated by the canonical
        _is_unknown_or_missing_session predicate, shared with the heal
        gate; a plain truthiness check passes all three of these shapes."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        self._stale_project(monkeypatch, tmp_path)

        result = _check_bootstrap_needed(_make_input(
            session_id=invalid_id))

        assert result is not None
        assert "PACT:bootstrap" in result            # instruction intact
        assert "stale session block" not in result   # no warning
        assert "INJECTED LINE" not in result         # id never interpolated

    def test_no_claude_md_returns_instruction_only(
            self, monkeypatch, tmp_path):
        """Worktree case: no CLAUDE.md → instruction only, no warning."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = _check_bootstrap_needed(_make_input())

        assert result is not None
        assert "PACT:bootstrap" in result
        assert "stale session block" not in result

    def test_marker_set_fast_path_never_runs_staleness(
            self, monkeypatch, tmp_path):
        """Perf contract pin: the marker-set fast path suppresses WITHOUT
        any CLAUDE.md read — _detect_stale_session_block must not be
        called at all."""
        import bootstrap_prompt_gate as gate_module

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)
        self._stale_project(monkeypatch, tmp_path)

        calls = []
        monkeypatch.setattr(
            gate_module, "_detect_stale_session_block",
            lambda input_data: calls.append(1) or None,
        )

        result = gate_module._check_bootstrap_needed(_make_input(
            session_id=self._ACTUAL_HEX))

        assert result is None, "marker set → suppress"
        assert calls == [], (
            "fast path must not invoke the staleness check (zero-read "
            "perf contract)"
        )

    def test_non_lead_path_never_runs_staleness(self, monkeypatch, tmp_path):
        """Plain frame (non-lead) → suppress without staleness check."""
        import bootstrap_prompt_gate as gate_module

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        self._stale_project(monkeypatch, tmp_path)

        calls = []
        monkeypatch.setattr(
            gate_module, "_detect_stale_session_block",
            lambda input_data: calls.append(1) or None,
        )

        result = gate_module._check_bootstrap_needed(_make_input(
            session_id=self._ACTUAL_HEX, agent_type=None))

        assert result is None
        assert calls == []

    @pytest.mark.parametrize("frame_kind", [
        "teammate-in-process",
        "teammate-captured-tmux",
    ])
    def test_teammate_frames_both_modes_never_run_staleness(
            self, monkeypatch, tmp_path, frame_kind):
        """Both-modes gate: the lead/non-lead split keys ONLY on the
        structural agent_type signal via is_lead — never a mode flag. A
        synthesized in-process teammate frame AND a real captured tmux
        teammate frame must both suppress without ever invoking the
        staleness check (the plain-frame sibling above covers the third
        non-lead shape)."""
        import bootstrap_prompt_gate as gate_module
        from fixtures.role_frames import (
            captured_teammate_sessionstart,
            teammate_frame,
        )

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        self._stale_project(monkeypatch, tmp_path)

        if frame_kind == "teammate-in-process":
            frame = teammate_frame(session_id=self._ACTUAL_HEX)
        else:
            frame = captured_teammate_sessionstart()  # real tmux capture

        calls = []
        monkeypatch.setattr(
            gate_module, "_detect_stale_session_block",
            lambda input_data: calls.append(1) or None,
        )

        result = gate_module._check_bootstrap_needed(frame)

        assert result is None, f"{frame_kind} must suppress (is_lead False)"
        assert calls == [], (
            f"{frame_kind} must never reach the staleness check"
        )


class TestSubprocessStalenessE2E:
    """Full-process E2E for the #943 incident chain: session_init crashed
    at SessionStart → pact-session-context.json ABSENT → the prompt gate's
    self-heal re-creates it in the SAME invocation → the lead+no-marker
    inject branch is reached → the staleness check reads the project
    CLAUDE.md and (on a recorded-vs-actual session mismatch) appends the
    advisory warning to the bootstrap instruction. In-process tests mock
    pieces of this chain; a fresh subprocess proves the chain end-to-end
    with real module loading, real env reads, and real disk I/O.

    Self-masker rule: bootstrap_prompt_gate exits 0 on EVERY path, so
    health is asserted on stdout content + on-disk effects; returncode is
    asserted only alongside content.
    """

    # Hex-only ids (the production _RESUME_LINE_RE matches [0-9a-f-]).
    _SID = "deadbeef-4242-4242-4242-deadbeef4242"
    _STALE_SID = "0badcafe-9999-8888-7777-666655554444"

    def _run_gate_subprocess(self, tmp_path, recorded_sid):
        """Scaffold: HOME under tmp_path, real project dir whose
        .claude/CLAUDE.md records ``recorded_sid``, context file ABSENT,
        lead UserPromptSubmit frame for ``_SID``. Returns
        (CompletedProcess, healed_context_path)."""
        import subprocess

        home = tmp_path
        project = home / "staleproj"
        claude_md = project / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text(
            "# Project\n\n## Current Session\n"
            f"- Resume: `claude --resume {recorded_sid}`\n"
            "- Team: `pact-old`\n",
            encoding="utf-8",
        )

        plugin_root = home / "plugin"
        plugin_root.mkdir(parents=True)

        # Session dir intentionally NOT created; context file ABSENT.
        ctx = (home / ".claude" / "pact-sessions" / "staleproj" /
               self._SID / "pact-session-context.json")

        hook_path = (
            Path(__file__).parent.parent / "hooks" /
            "bootstrap_prompt_gate.py"
        )
        assert hook_path.exists(), f"gate hook missing at {hook_path}"

        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": self._SID,
            "prompt": "first prompt after session_init crashed",
            "agent_type": "pact-orchestrator",
        })

        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)  # force the HOME/.claude fallback
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=10,
        )
        return result, ctx

    def test_heal_chain_with_stale_block_injects_instruction_and_warning(
            self, tmp_path):
        """Mismatch (CLAUDE.md records the PREVIOUS session) → ONE
        additionalContext string carrying the bootstrap instruction with
        the staleness warning APPENDED, both ids named; the context file
        is healed on disk with session_init-parity content."""
        result, ctx = self._run_gate_subprocess(
            tmp_path, recorded_sid=self._STALE_SID
        )

        # Content first (self-masker rule), rc alongside.
        out = json.loads(result.stdout.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        context = hso["additionalContext"]
        assert "PACT:bootstrap" in context
        assert "PACT_SESSION_DIR=" in context
        assert "stale session block" in context
        assert self._STALE_SID in context, "recorded (stale) id named"
        assert self._SID in context, "actual id named"
        assert context.index("PACT:bootstrap") < context.index(
            "stale session block"), "warning is APPENDED, not prepended"
        assert "suppressOutput" not in out
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )

        # Heal landed on disk (the chain's enabling step).
        assert ctx.exists(), "self-heal should re-create the context file"
        content = json.loads(ctx.read_text(encoding="utf-8"))
        assert content["team_name"] == "session-deadbeef"
        assert content["session_id"] == self._SID

    def test_heal_chain_with_matching_block_injects_instruction_only(
            self, tmp_path):
        """Match (healthy resume shape) → instruction WITHOUT the warning;
        the heal still lands (it is independent of staleness)."""
        result, ctx = self._run_gate_subprocess(
            tmp_path, recorded_sid=self._SID
        )

        out = json.loads(result.stdout.strip())
        context = out["hookSpecificOutput"]["additionalContext"]
        assert "PACT:bootstrap" in context
        assert "stale session block" not in context
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert ctx.exists()
