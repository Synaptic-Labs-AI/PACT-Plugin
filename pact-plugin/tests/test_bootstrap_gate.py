"""
Tests for bootstrap_gate.py — PreToolUse hook that blocks code-editing and
agent-dispatch tools until the bootstrap-complete marker exists.

Tests cover:

_check_tool_allowed() unit tests:
1. Marker exists (properly stamped with valid content fingerprint) → None for any tool (fast path)
2. No marker + blocked tool (Edit) → deny reason string
3. No marker + blocked tool (Write) → deny reason string
4. No marker + blocked tool (Agent) → deny reason string
5. No marker + blocked tool (NotebookEdit) → deny reason string
6. No marker + allowed tool (Read) → None
7-13. No marker + allowed tools (Glob, Grep, Bash, WebFetch, WebSearch,
       AskUserQuestion, ExitPlanMode) → None
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

Fail-OPEN preserved for input-side failures (P0):
24. Malformed stdin → exit 0, suppressOutput
25. Empty stdin → exit 0, suppressOutput

Fail-CLOSED for gate-logic exceptions (P0):
26. Exception in _check_tool_allowed → exit 2, deny JSON with hookEventName

Error/suppress mutual exclusivity (P0):
27. Input-side fail-open paths emit suppressOutput, never systemMessage
28. Deny path (block + runtime fail-closed) emits permissionDecision, not suppressOutput
29. Allow paths emit suppressOutput, not hookSpecificOutput

Blocked tool set completeness (P2 — post-#662):
30. Exactly 4 blocked tools in the set
31. Members are exactly {Edit, Write, Agent, NotebookEdit}
32. Bash is NOT in blocked set (circular dependency guard)
33. Read is NOT in blocked set (exploration tool)

Deny reason content (P2):
34. Deny reason mentions Skill("PACT:bootstrap")
35. Deny reason mentions available tools (Bash, Read, Glob, Grep)

is_marker_set() — public helper:
36. None / empty session_dir → False
37. Marker absent → False
38. Marker symlink (S2) → False
39. Marker is a directory (S2 corollary) → False
40. Ancestor symlink (S4) → False
41. Properly-stamped marker → True
42. Empty file (legacy `touch` form) → False
43. Marker with wrong sid → False
44. Marker with wrong version → False
45. Malformed JSON marker → False
46. Marker with wrong signature → False
47. Marker with oversized content → False
48. Marker with missing plugin context → False
"""

import hashlib
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


def _write_f24_marker(session_dir: Path, plugin_root: Path,
                      plugin_version: str = "9.9.9",
                      marker_version: int = 1,
                      sid: str | None = None,
                      sig: str | None = None) -> Path:
    """Write a properly-stamped marker. Override fields to forge invalid
    variants for negative tests.
    """
    from bootstrap_gate import MARKER_SCHEMA_VERSION

    real_sid = sid if sid is not None else session_dir.name
    real_sig_input = (
        f"{real_sid}|{str(plugin_root).rstrip('/')}|{plugin_version}|{marker_version}"
    )
    real_sig = sig if sig is not None else hashlib.sha256(
        real_sig_input.encode("utf-8")
    ).hexdigest()
    payload = {"v": marker_version, "sid": real_sid, "sig": real_sig}
    marker = session_dir / BOOTSTRAP_MARKER_NAME
    marker.write_text(json.dumps(payload), encoding="utf-8")
    # Sanity: caller using default args should produce a valid stamp
    # for the current MARKER_SCHEMA_VERSION constant.
    if marker_version == 1:
        assert MARKER_SCHEMA_VERSION == 1
    return marker


def _setup_pact_session(monkeypatch, tmp_path, with_marker=False,
                        plugin_version="9.9.9"):
    """Set up a PACT session context with session dir under tmp_path.

    Monkeypatches Path.home to tmp_path so get_session_dir() returns a
    path under tmp_path. Returns the session_dir path.

    When ``with_marker=True`` writes a properly-stamped marker (post-#662);
    callers that want to test legacy or invalid markers should pass
    ``with_marker=False`` and use ``_write_f24_marker`` directly with override
    fields.
    """
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
        _write_f24_marker(session_dir, plugin_root, plugin_version=plugin_version)

    return session_dir


# =============================================================================
# _check_tool_allowed — unit tests
# =============================================================================


class TestCheckToolAllowed:
    """Tests for _check_tool_allowed() decision logic."""

    # --- Marker exists: fast path ---

    @pytest.mark.parametrize("tool_name", ["Edit", "Write", "Agent", "NotebookEdit", "Read", "Bash"])
    def test_marker_exists_allows_any_tool(self, monkeypatch, tmp_path, tool_name):
        """Marker exists → None for any tool (including normally-blocked ones)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        result = _check_tool_allowed(_make_input(tool_name))
        assert result is None

    # --- No marker: blocked tools ---

    @pytest.mark.parametrize("tool_name", ["Edit", "Write", "Agent", "NotebookEdit"])
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
        plugin_root = tmp_path / "plugin"
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": "pact-test1234",
            "session_id": _SESSION_ID,
            "project_dir": _PROJECT_DIR,
            "plugin_root": str(plugin_root),
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
        """Marker exists (properly stamped) → exit 0 (fast path)."""
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
# Fail-open (input-side) — P0 priority
# =============================================================================


class TestInputSideFailOpen:
    """P0: Malformed/empty stdin remains fail-OPEN — input-side failures
    are the harness's domain (cannot evaluate without input).
    """

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


# =============================================================================
# Fail-closed (gate-logic exception) — P0 priority
# =============================================================================


class TestFailClosedGateLogic:
    """Runtime fail-closed (#662, post-#658 defect class): runtime exception in
    ``_check_tool_allowed`` must DENY (not fail-OPEN). Pre-#662 this path
    was fail-OPEN — that was the same defect class as #658.
    """

    def test_exception_in_check_tool_allowed_emits_deny(self, capsys):
        """RuntimeError in _check_tool_allowed → exit 2 with structured deny."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "RuntimeError" in hso["permissionDecisionReason"]


# =============================================================================
# Error/suppress mutual exclusivity — P0 priority
# =============================================================================


class TestErrorSuppressMutualExclusivity:
    """P0: input-side fail-open uses suppressOutput; deny path (block + runtime fail-closed
    fail-closed) uses hookSpecificOutput. systemMessage is never emitted.
    """

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

    def test_gate_logic_exception_no_system_message(self, capsys):
        """Runtime fail-closed → hookSpecificOutput, not systemMessage."""
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
        assert "hookSpecificOutput" in parsed
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
# Blocked tool set completeness — P2 priority (post-#662)
# =============================================================================


class TestBlockedToolSet:
    """P2: Verify the blocked tool set is correct and complete (post-#662)."""

    def test_blocked_set_exact_cardinality(self):
        """Exactly 4 tools in the blocked set."""
        from bootstrap_gate import _BLOCKED_TOOLS

        assert len(_BLOCKED_TOOLS) == 4

    def test_blocked_set_exact_members(self):
        """Blocked set contains exactly Edit, Write, Agent, NotebookEdit (#662).

        The agent-dispatch tool name is `Agent` — the canonical Claude Code
        platform name (verified against code.claude.com docs as of 2026-05-06).
        Earlier `Task` literal (commit 4c286c1f) was the wrong rename
        direction; #662 reverts it.
        """
        from bootstrap_gate import _BLOCKED_TOOLS

        assert _BLOCKED_TOOLS == frozenset({"Edit", "Write", "Agent", "NotebookEdit"})

    def test_blocked_set_does_not_contain_task(self):
        """Regression-prevention: Task is NOT in the blocked set (#662).

        Pre-#662, commit 4c286c1f wrongly renamed Agent→Task here.
        This test fails-closed if anyone reverses the rename direction.
        """
        from bootstrap_gate import _BLOCKED_TOOLS

        assert "Task" not in _BLOCKED_TOOLS

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
        """Before marker: deny Edit. After marker stamp: allow Edit."""
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"

        # Before marker — Edit denied
        exit_code_before, output_before = _run_main(_make_input("Edit"), capsys)
        assert exit_code_before == 2
        assert "permissionDecision" in output_before.get("hookSpecificOutput", {})

        # Write a properly-stamped marker
        _write_f24_marker(session_dir, plugin_root, plugin_version="9.9.9")

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

        for tool in ["Edit", "Write", "Agent"]:
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
        """bootstrap.md marker producer must reference the shared marker name."""
        bootstrap_md = (
            Path(__file__).parent.parent / "commands" / "bootstrap.md"
        )
        content = bootstrap_md.read_text(encoding="utf-8")
        # The marker producer (#662) writes the marker via python3, but the
        # marker file path still embeds BOOTSTRAP_MARKER_NAME literally.
        assert BOOTSTRAP_MARKER_NAME in content


# =============================================================================
# is_marker_set — public helper (leaf-symlink + ancestor-symlink + content-fingerprint defenses)
# =============================================================================


class TestIsMarkerSet:
    """Public predicate `is_marker_set(session_dir)` — does a properly-stamped
    properly-stamped marker exist? Defends symlink-planted bypass
    (leaf and ancestor) and Bash-touch bypass (via SHA256 content fingerprint).
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

    def test_returns_true_when_marker_properly_stamped(
        self, monkeypatch, tmp_path
    ):
        """Only properly-stamped markers satisfy the gate."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=True)
        assert is_marker_set(session_dir) is True

    def test_returns_false_when_marker_is_empty_file_legacy_touch(
        self, monkeypatch, tmp_path
    ):
        """Bash-touch bypass closure (#662): legacy `touch bootstrap-complete`
        (empty file) MUST NOT satisfy the gate.
        """
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        (session_dir / BOOTSTRAP_MARKER_NAME).touch()
        assert is_marker_set(session_dir) is False

    def test_returns_false_when_marker_is_symlink(self, monkeypatch, tmp_path):
        """S2 attack chain — symlink at the marker path is rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        target = tmp_path / "decoy_target"
        target.touch()
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        marker.symlink_to(target)
        assert marker.exists() is True  # Path.exists follows symlinks
        assert is_marker_set(session_dir) is False  # but is_marker_set rejects

    def test_returns_false_when_marker_is_directory(
        self, monkeypatch, tmp_path
    ):
        """S2 corollary: a directory at the marker path is rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        (session_dir / BOOTSTRAP_MARKER_NAME).mkdir()
        assert is_marker_set(session_dir) is False

    def test_returns_false_when_ancestor_is_symlink(self, tmp_path):
        """S4 attack chain: symlinked ancestor is rejected."""
        from bootstrap_gate import is_marker_set

        real_dir = tmp_path / "real_session_dir"
        real_dir.mkdir()
        (real_dir / BOOTSTRAP_MARKER_NAME).touch()
        link_dir = tmp_path / "linked_session_dir"
        link_dir.symlink_to(real_dir)
        assert (link_dir / BOOTSTRAP_MARKER_NAME).exists() is True
        # Both paths fail content-fingerprint validation (empty content
        # is not a valid stamp), but
        # the ancestor-symlink check fires FIRST and ensures the bypass
        # would be rejected even if the content fingerprint were satisfied.
        assert is_marker_set(link_dir) is False
        assert is_marker_set(real_dir) is False  # content fingerprint fails on empty file

    def test_rejects_wrong_sid(self, monkeypatch, tmp_path):
        """Marker with mismatched sid (not session_dir.name) rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        _write_f24_marker(
            session_dir, plugin_root, plugin_version="9.9.9", sid="wrong-session"
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_wrong_version(self, monkeypatch, tmp_path):
        """Marker with v != MARKER_SCHEMA_VERSION rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        _write_f24_marker(
            session_dir, plugin_root, plugin_version="9.9.9", marker_version=99
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_malformed_json(self, monkeypatch, tmp_path):
        """Non-JSON marker content rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            "not json at all", encoding="utf-8"
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_extra_keys(self, monkeypatch, tmp_path):
        """Marker with keys beyond {v, sid, sig} rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        marker.write_text(
            json.dumps({
                "v": 1, "sid": session_dir.name, "sig": "deadbeef",
                "extra": "snuck in",
            }),
            encoding="utf-8",
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_wrong_signature(self, monkeypatch, tmp_path):
        """Marker with a non-matching SHA256 signature rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        _write_f24_marker(
            session_dir, plugin_root, plugin_version="9.9.9", sig="0" * 64
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_oversized_content(self, monkeypatch, tmp_path):
        """Marker file > 256 bytes rejected (pathological-read defense)."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        # Write a JSON object that's syntactically correct but huge.
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        big_payload = {"v": 1, "sid": session_dir.name,
                       "sig": "x" * 1024}
        marker.write_text(json.dumps(big_payload), encoding="utf-8")
        assert is_marker_set(session_dir) is False

    def test_rejects_when_plugin_root_missing(self, monkeypatch, tmp_path):
        """Cannot compute expected signature without plugin context."""
        from bootstrap_gate import is_marker_set
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        # Stamp the marker with what WOULD be a valid sig, then break the
        # plugin context.
        _write_f24_marker(session_dir, plugin_root, plugin_version="9.9.9")
        # Remove plugin_root from context (simulate older session_init or
        # a corrupted context file).
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": "",
            "session_id": _SESSION_ID,
            "project_dir": _PROJECT_DIR,
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        ctx_module._cache = None
        assert is_marker_set(session_dir) is False
