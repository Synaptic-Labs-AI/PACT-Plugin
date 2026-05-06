"""
Tests for bootstrap_gate.py — PreToolUse hook that blocks code-editing and
agent-dispatch tools until the bootstrap-complete marker exists.

Tests cover:

_check_tool_allowed() unit tests:
1. Marker exists → None for any tool (fast path)
2. No marker + blocked tool (Edit) → deny reason string
3. No marker + blocked tool (Write) → deny reason string
4. No marker + blocked tool (Task) → deny reason string
5. No marker + blocked tool (NotebookEdit) → deny reason string
6. No marker + allowed tool (Read) → None
7. No marker + allowed tool (Glob) → None
8. No marker + allowed tool (Grep) → None
9. No marker + allowed tool (Bash) → None (critical: bootstrap needs Bash)
10. No marker + allowed tool (WebFetch) → None
11. No marker + allowed tool (WebSearch) → None
12. No marker + allowed tool (AskUserQuestion) → None
13. No marker + allowed tool (ExitPlanMode) → None
14. No marker + MCP tool → None (mcp__ prefix match)
15. Non-PACT session (no session dir) → None
16. Teammate → None (passthrough)
17. Empty tool_name → None (not in blocked set)
18. Non-string tool_name → None (isinstance guard)

main() integration tests:
19. Blocked tool → exit 2, deny JSON with permissionDecision
20. Allowed tool → exit 0, suppressOutput
21. Marker exists → exit 0, suppressOutput
22. Non-PACT → exit 0, suppressOutput
23. Teammate → exit 0, suppressOutput

Fail-open (P0):
24. Malformed stdin → exit 0, suppressOutput
25. Empty stdin → exit 0, suppressOutput
26. Exception in _check_tool_allowed → exit 0, suppressOutput

Error/suppress mutual exclusivity (P0):
27. Error paths never emit systemMessage
28. Deny path emits permissionDecision, not suppressOutput
29. Allow paths emit suppressOutput, not hookSpecificOutput

Blocked tool set completeness (P2):
30. Exactly 4 blocked tools in the set
31. Bash is NOT in blocked set (circular dependency guard)

Deny reason content (P2):
32. Deny reason mentions Skill("PACT:bootstrap")
33. Deny reason mentions available tools (Bash, Read, Glob, Grep)
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

# Session identity constants
_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"


# =============================================================================
# Helpers
# =============================================================================


def _make_input(tool_name="Edit", session_id=_SESSION_ID):
    """Build a minimal PreToolUse hook input dict."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {},
    }


def _run_main(input_data, capsys):
    """Run bootstrap_gate.main(), return (exit_code, stdout_json)."""
    from bootstrap_gate import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    return exc_info.value.code, json.loads(captured.out.strip())


def _setup_pact_session(monkeypatch, tmp_path, with_marker=False):
    """Set up a PACT session context with session dir under tmp_path.

    Monkeypatches Path.home to tmp_path so get_session_dir() returns a
    path under tmp_path. Returns the session_dir path.
    """
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": "",
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": "",
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_marker:
        (session_dir / BOOTSTRAP_MARKER_NAME).touch()

    return session_dir


# =============================================================================
# _check_tool_allowed — unit tests
# =============================================================================


class TestCheckToolAllowed:
    """Tests for _check_tool_allowed() decision logic."""

    # --- Marker exists: fast path ---

    @pytest.mark.parametrize("tool_name", ["Edit", "Write", "Task", "NotebookEdit", "Read", "Bash"])
    def test_marker_exists_allows_any_tool(self, monkeypatch, tmp_path, tool_name):
        """Marker exists → None for any tool (including normally-blocked ones)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        result = _check_tool_allowed(_make_input(tool_name))
        assert result is None

    # --- No marker: blocked tools ---

    @pytest.mark.parametrize("tool_name", ["Edit", "Write", "Task", "NotebookEdit"])
    def test_blocked_tools_return_deny_reason(self, monkeypatch, tmp_path, tool_name):
        """No marker + blocked tool → deny reason string."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_tool_allowed(_make_input(tool_name))
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    # --- No marker: allowed tools ---

    @pytest.mark.parametrize("tool_name", [
        "Read", "Glob", "Grep", "Bash",
        "WebFetch", "WebSearch",
        "AskUserQuestion", "ExitPlanMode",
    ])
    def test_allowed_tools_return_none(self, monkeypatch, tmp_path, tool_name):
        """No marker + allowed tool → None (pass through)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_tool_allowed(_make_input(tool_name))
        assert result is None

    def test_bash_explicitly_allowed(self, monkeypatch, tmp_path):
        """Bash MUST be allowed — blocking it creates circular dependency."""
        from bootstrap_gate import _check_tool_allowed, _BLOCKED_TOOLS

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        assert "Bash" not in _BLOCKED_TOOLS
        result = _check_tool_allowed(_make_input("Bash"))
        assert result is None

    # --- MCP tools ---

    @pytest.mark.parametrize("tool_name", [
        "mcp__computer-use__screenshot",
        "mcp__claude-in-chrome__navigate",
        "mcp__exa__web_search_exa",
    ])
    def test_mcp_tools_always_allowed(self, monkeypatch, tmp_path, tool_name):
        """MCP tools (mcp__ prefix) → None regardless of marker."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_tool_allowed(_make_input(tool_name))
        assert result is None

    # --- Non-PACT and teammate passthrough ---

    def test_non_pact_session_allows_all(self, monkeypatch):
        """Non-PACT session (no session dir) → None for blocked tools."""
        from bootstrap_gate import _check_tool_allowed
        import shared.pact_context as ctx_module

        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        result = _check_tool_allowed(_make_input("Edit"))
        assert result is None

    def test_teammate_allows_all(self, monkeypatch, tmp_path):
        """Teammate → None even for blocked tools."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input("Edit")
        input_data["agent_name"] = "backend-coder"

        result = _check_tool_allowed(input_data)
        assert result is None

    def test_teammate_via_agent_id(self, monkeypatch, tmp_path):
        """Teammate via agent_id format → None."""
        from bootstrap_gate import _check_tool_allowed
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        # Override context to have a team_name
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": "pact-test1234",
            "session_id": _SESSION_ID,
            "project_dir": _PROJECT_DIR,
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        ctx_module._cache = None

        input_data = _make_input("Write")
        input_data["agent_id"] = "backend-coder@pact-test1234"

        result = _check_tool_allowed(input_data)
        assert result is None

    # --- Edge cases ---

    def test_empty_tool_name(self, monkeypatch, tmp_path):
        """Empty string tool_name → None (not in blocked set)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_tool_allowed(_make_input(""))
        assert result is None

    def test_unknown_tool_name_allowed(self, monkeypatch, tmp_path):
        """Unknown tool name → None (only explicit block list denies)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_tool_allowed(_make_input("SomeNewTool"))
        assert result is None

    def test_non_string_tool_name(self, monkeypatch, tmp_path):
        """Non-string tool_name (e.g. int) → None (isinstance guard on mcp check)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input("Edit")
        input_data["tool_name"] = 42  # non-string

        result = _check_tool_allowed(input_data)
        assert result is None  # int not in frozenset, isinstance guard prevents startswith


# =============================================================================
# main() — integration tests
# =============================================================================


class TestMainEntryPoint:
    """Tests for main() stdin/stdout/exit behavior."""

    def test_blocked_tool_exits_2(self, monkeypatch, tmp_path, capsys):
        """Blocked tool → exit 2 (PreToolUse deny convention)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        exit_code, output = _run_main(_make_input("Edit"), capsys)
        assert exit_code == 2

    def test_blocked_tool_outputs_deny_json(self, monkeypatch, tmp_path, capsys):
        """Blocked tool → deny JSON with permissionDecision."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Write"), capsys)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "permissionDecisionReason" in hso

    def test_allowed_tool_exits_0(self, monkeypatch, tmp_path, capsys):
        """Allowed tool → exit 0."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        exit_code, output = _run_main(_make_input("Read"), capsys)
        assert exit_code == 0
        assert output == _SUPPRESS_EXPECTED

    def test_marker_exists_exits_0(self, monkeypatch, tmp_path, capsys):
        """Marker exists → exit 0 (fast path)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        exit_code, output = _run_main(_make_input("Edit"), capsys)
        assert exit_code == 0
        assert output == _SUPPRESS_EXPECTED

    def test_non_pact_exits_0(self, monkeypatch, capsys):
        """Non-PACT session → exit 0."""
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "_context_path", None)
        monkeypatch.setattr(ctx_module, "_cache", None)

        exit_code, output = _run_main(_make_input("Edit"), capsys)
        assert exit_code == 0
        assert output == _SUPPRESS_EXPECTED

    def test_teammate_exits_0(self, monkeypatch, tmp_path, capsys):
        """Teammate → exit 0."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input("Edit")
        input_data["agent_name"] = "backend-coder"

        exit_code, output = _run_main(input_data, capsys)
        assert exit_code == 0
        assert output == _SUPPRESS_EXPECTED


# =============================================================================
# Fail-open — P0 priority
# =============================================================================


class TestFailOpen:
    """P0: Every exception path must fail-open (exit 0, suppressOutput)."""

    def test_malformed_stdin_json(self, capsys):
        """Invalid JSON on stdin → fail-open."""
        from bootstrap_gate import main

        with patch("sys.stdin", io.StringIO("not valid json {")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_empty_stdin(self, capsys):
        """Empty stdin → fail-open."""
        from bootstrap_gate import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_exception_in_check_tool_allowed(self, capsys):
        """RuntimeError in _check_tool_allowed → fail-open."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_oserror_in_marker_check_treats_marker_absent(self, monkeypatch, tmp_path, capsys):
        """OSError when checking marker → marker treated as absent → blocked
        tool denied (gate stays armed). Behavior change from pre-S2 fix:
        previously `Path.exists()` raises propagated to the outer except
        and fell open with suppressOutput. Now `is_marker_set` catches
        OSError internally and returns False — the conservative choice
        per S2 trust-boundary rationale ('don't claim the marker is set
        when we can't verify it'). The OUTER fail-open contract (any
        raisable path → suppressOutput) still holds for genuine
        programmer errors above the marker-check layer."""
        from bootstrap_gate import main

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        with patch("os.lstat", side_effect=OSError("disk error")):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input("Edit")))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        # Edit is a blocked tool + marker absent → exit 2 (deny).
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


# =============================================================================
# Error/suppress mutual exclusivity — P0 priority
# =============================================================================


class TestErrorSuppressMutualExclusivity:
    """P0: These hooks use suppressOutput for fail-open, never systemMessage.
    Deny path uses hookSpecificOutput, never suppressOutput."""

    def test_malformed_stdin_no_system_message(self, capsys):
        """Malformed stdin → suppressOutput, not systemMessage."""
        from bootstrap_gate import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed

    def test_exception_no_system_message(self, capsys):
        """Exception → suppressOutput, not systemMessage."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit):
                    main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed

    def test_deny_path_no_suppress_output(self, monkeypatch, tmp_path, capsys):
        """Deny path → hookSpecificOutput, NOT suppressOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Edit"), capsys)
        assert "hookSpecificOutput" in output
        assert "suppressOutput" not in output

    def test_allow_path_no_hook_specific_output(self, monkeypatch, tmp_path, capsys):
        """Allow path → suppressOutput, NOT hookSpecificOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Read"), capsys)
        assert "suppressOutput" in output
        assert "hookSpecificOutput" not in output


# =============================================================================
# Blocked tool set completeness — P2 priority
# =============================================================================


class TestBlockedToolSet:
    """P2: Verify the blocked tool set is correct and complete."""

    def test_blocked_set_exact_cardinality(self):
        """Exactly 4 tools in the blocked set."""
        from bootstrap_gate import _BLOCKED_TOOLS

        assert len(_BLOCKED_TOOLS) == 4

    def test_blocked_set_exact_members(self):
        """Blocked set contains exactly Edit, Write, Task, NotebookEdit.

        The agent-dispatch tool name is `Task` (the canonical platform
        tool). Cross-evidence: hooks.json PreToolUse team_guard +
        PostToolUse auditor_reminder both use matcher='Task' and fire
        correctly in production.
        """
        from bootstrap_gate import _BLOCKED_TOOLS

        assert _BLOCKED_TOOLS == frozenset({"Edit", "Write", "Task", "NotebookEdit"})

    def test_bash_not_blocked(self):
        """Bash must NOT be in blocked set (circular dependency)."""
        from bootstrap_gate import _BLOCKED_TOOLS

        assert "Bash" not in _BLOCKED_TOOLS

    def test_read_not_blocked(self):
        """Read must NOT be blocked (exploration tool)."""
        from bootstrap_gate import _BLOCKED_TOOLS

        assert "Read" not in _BLOCKED_TOOLS


# =============================================================================
# Deny reason content — P2 priority
# =============================================================================


class TestDenyReasonContent:
    """P2: Verify deny reason includes actionable guidance."""

    def test_deny_reason_mentions_bootstrap_skill(self, monkeypatch, tmp_path):
        """Deny reason should tell the LLM to invoke bootstrap."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        reason = _check_tool_allowed(_make_input("Edit"))
        assert reason is not None
        assert 'Skill("PACT:bootstrap")' in reason

    def test_deny_reason_mentions_available_tools(self, monkeypatch, tmp_path):
        """Deny reason should mention tools that ARE available."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        reason = _check_tool_allowed(_make_input("Edit"))
        assert reason is not None
        assert "Bash" in reason
        assert "Read" in reason
        assert "Glob" in reason
        assert "Grep" in reason


# =============================================================================
# Marker lifecycle — P3 priority
# =============================================================================


class TestMarkerLifecycle:
    """P3: Gate transitions based on marker presence."""

    def test_gate_transitions_deny_to_allow(self, monkeypatch, tmp_path, capsys):
        """Before marker: deny Edit. After marker: allow Edit."""
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        # Before marker — Edit denied
        exit_code_before, output_before = _run_main(_make_input("Edit"), capsys)
        assert exit_code_before == 2
        assert "permissionDecision" in output_before.get("hookSpecificOutput", {})

        # Create marker
        (session_dir / BOOTSTRAP_MARKER_NAME).touch()

        # Reset cache for second call
        ctx_module._cache = None

        # After marker — Edit allowed
        exit_code_after, output_after = _run_main(_make_input("Edit"), capsys)
        assert exit_code_after == 0
        assert output_after == _SUPPRESS_EXPECTED

    def test_repeated_deny_is_consistent(self, monkeypatch, tmp_path, capsys):
        """Multiple blocked calls without marker all produce deny."""
        import shared.pact_context as ctx_module

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        for tool in ["Edit", "Write", "Task"]:
            ctx_module._cache = None

            exit_code, output = _run_main(_make_input(tool), capsys)
            assert exit_code == 2
            assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


# =============================================================================
# Cross-module marker name consistency — P2 priority
# =============================================================================


class TestMarkerNameConsistency:
    """P2: All bootstrap gate files must use the same marker name."""

    def test_shared_constant_value(self):
        """BOOTSTRAP_MARKER_NAME is the expected string."""
        assert BOOTSTRAP_MARKER_NAME == "bootstrap-complete"

    def test_bootstrap_md_references_same_marker(self):
        """bootstrap.md touch command must reference the shared marker name."""
        bootstrap_md = (
            Path(__file__).parent.parent / "commands" / "bootstrap.md"
        )
        content = bootstrap_md.read_text(encoding="utf-8")
        assert f"touch \"<path>/{BOOTSTRAP_MARKER_NAME}\"" in content


# =============================================================================
# is_marker_set — public helper (Arch-M1 + S2 + S4 defense)
# =============================================================================


class TestIsMarkerSet:
    """Public predicate `is_marker_set(session_dir)` — does a real marker
    exist? Defends S2 (symlink-planted bypass) + S4 (ancestor symlink).
    Plan §High-Risk-TDD-Specs Q4 names this as a 7-method TDD target.
    """

    def test_returns_false_when_session_dir_none(self):
        from bootstrap_gate import is_marker_set

        assert is_marker_set(None) is False

    def test_returns_false_when_session_dir_empty(self):
        from bootstrap_gate import is_marker_set

        assert is_marker_set(Path("")) is False

    def test_returns_false_when_marker_absent(self, tmp_path):
        from bootstrap_gate import is_marker_set

        assert is_marker_set(tmp_path) is False

    def test_returns_true_when_marker_present_as_regular_file(self, tmp_path):
        from bootstrap_gate import is_marker_set

        (tmp_path / BOOTSTRAP_MARKER_NAME).touch()
        assert is_marker_set(tmp_path) is True

    def test_returns_false_when_marker_is_symlink(self, tmp_path):
        """S2 attack chain: planted symlink at the marker path pointing
        at ANY existing file falsely satisfies `Path.exists()` (which
        follows symlinks). The defense uses `os.lstat() + S_ISREG`
        which checks the leaf without following the link.

        Reproducer for the bypass-without-defense:
            ln -s /etc/hostname <session_dir>/bootstrap-complete
            → Path.exists() returns True → gate would allow → BYPASS

        With defense:
            os.lstat() returns the symlink's own stat → S_ISLNK, not
            S_ISREG → returns False → gate stays armed.
        """
        from bootstrap_gate import is_marker_set

        # Plant a real file outside the session dir.
        target = tmp_path / "decoy_target"
        target.touch()
        # Plant the marker as a symlink to the decoy.
        marker = tmp_path / BOOTSTRAP_MARKER_NAME
        marker.symlink_to(target)
        assert marker.exists() is True  # Path.exists follows symlinks
        assert is_marker_set(tmp_path) is False  # but is_marker_set rejects

    def test_returns_false_when_marker_is_directory(self, tmp_path):
        """S2 corollary: a directory at the marker path is also rejected
        (S_ISREG False)."""
        from bootstrap_gate import is_marker_set

        (tmp_path / BOOTSTRAP_MARKER_NAME).mkdir()
        assert is_marker_set(tmp_path) is False

    def test_returns_false_when_ancestor_is_symlink(self, tmp_path):
        """S4 attack chain: planted symlink at any ancestor of the
        session_dir (e.g., ~/.claude itself being a symlink to attacker-
        controlled /tmp/evil/.claude) lets the attacker plant a regular
        file marker satisfying the leaf-only check.

        Reproducer:
            ln -s /tmp/evil ~/.claude
            mkdir -p /tmp/evil/pact-sessions/{slug}/{session_id}
            touch /tmp/evil/pact-sessions/{slug}/{session_id}/bootstrap-complete
            → leaf is_symlink() returns False → leaf-only gate would allow

        Defense: Path.resolve(strict=False) follows ALL ancestor symlinks
        in the path; if the resolved path differs from the absolute input
        path, an ancestor was a symlink → reject.
        """
        from bootstrap_gate import is_marker_set

        # Real session_dir target.
        real_dir = tmp_path / "real_session_dir"
        real_dir.mkdir()
        (real_dir / BOOTSTRAP_MARKER_NAME).touch()
        # Symlink the parent directory to the real one.
        link_dir = tmp_path / "linked_session_dir"
        link_dir.symlink_to(real_dir)
        # The marker IS a real file (via the symlink path) but the path
        # has a symlink ancestor → defense rejects.
        assert (link_dir / BOOTSTRAP_MARKER_NAME).exists() is True
        assert is_marker_set(link_dir) is False
        # Sanity: the real path (no ancestor symlink) IS accepted.
        assert is_marker_set(real_dir) is True
