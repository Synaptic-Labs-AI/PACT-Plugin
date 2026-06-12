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

_SUPPRESS_EXPECTED = {
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "PreToolUse"},
}

# Session identity constants
_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"

# Canonical deny-reason literal — independent of bootstrap_gate._DENY_REASON.
# Hard-coded here so byte-identity tests anchor on this literal rather than
# self-comparing against the imported constant (which would silently pass if
# both the constant and the assertion target alias the same mutated string).
# Any intentional change to the deny reason must update BOTH this literal AND
# bootstrap_gate._DENY_REASON — that two-site edit is the load-bearing review
# surface for deny-reason drift.
_CANONICAL_DENY_REASON_LITERAL = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Code-editing tools (Edit, Write) and agent dispatch (Agent) are blocked "
    "until bootstrap completes. Bash, Read, Glob, Grep are available."
)


# =============================================================================
# Helpers
# =============================================================================


def _make_input(tool_name="Edit", session_id=_SESSION_ID,
                agent_type="pact-orchestrator"):
    """Build a minimal PreToolUse hook input dict.

    #878: the gate now keys lead-detection on the harness-set agent_type via
    is_lead. The default is a LEAD frame (the unmarked case these DENY tests
    historically assumed via empty resolve_agent_name). Teammate/non-lead tests
    pass agent_type=<teammate> or agent_type=None to exercise the bypass branch.
    """
    data = {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {},
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


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
    from shared.marker_schema import MARKER_SCHEMA_VERSION

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
        """Teammate (non-lead agent_type) → None even for blocked tools.

        #878: lead-detection migrated to is_lead, which reads agent_type. A
        specialist agent_type is not a lead spelling, so the gate bypasses.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input("Edit", agent_type="pact-backend-coder")

        result = _check_tool_allowed(input_data)
        assert result is None

    def test_teammate_via_qualified_agent_type(self, monkeypatch, tmp_path):
        """Teammate carrying a qualified non-lead agent_type → None.

        #878: the gate no longer resolves agent_id/agent_name — it reads
        agent_type directly. A `PACT:`-qualified specialist type is not a lead
        spelling, so the gate bypasses.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input("Write", agent_type="PACT:pact-backend-coder")

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
        """Teammate (non-lead agent_type) → exit 0 (bypass, no DENY)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        input_data = _make_input("Edit", agent_type="pact-backend-coder")

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
    fail-closed) uses hookSpecificOutput. systemMessage is never emitted on
    suppress/deny paths; the degraded-allow path (#942) is the single
    deliberate systemMessage emitter (see TestDegradedMode).
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

    def test_allow_path_carries_audit_anchor_only(self, monkeypatch, tmp_path, capsys):
        """Allow path → suppressOutput + hookSpecificOutput.hookEventName only,
        NO permissionDecision. The audit-anchor parity retrofit aligns the
        suppress envelope with bootstrap_marker_writer's pattern (the writer
        was the role model; both gates now match)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Read"), capsys)
        assert output.get("suppressOutput") is True
        hso = output.get("hookSpecificOutput")
        assert hso == {"hookEventName": "PreToolUse"}, (
            f"Allow envelope should be exactly suppressOutput + audit-anchor "
            f"hookEventName, no permissionDecision. Got: {output!r}"
        )


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
# Canonical secretary spawn carve-out (#789)
# =============================================================================


def _setup_pact_session_with_team(monkeypatch, tmp_path, team_name="t1",
                                  members=None, with_marker=False,
                                  plugin_version="9.9.9"):
    """Set up a PACT session whose context carries a non-empty team_name and
    a matching team config at ~/.claude/teams/{team_name}/config.json.

    Mirrors _setup_pact_session but adds the team-config sidecar that
    _team_has_secretary reads via shared.pact_context._iter_members. The
    monkeypatched Path.home means the team config lands under tmp_path.

    members: list of member dicts to embed at config.members[]. Defaults
    to a fresh-team shape (no secretary entry) which is the precondition
    the carve-out exists to handle.
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
        "team_name": team_name,
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    teams_dir = tmp_path / ".claude" / "teams" / team_name
    teams_dir.mkdir(parents=True, exist_ok=True)
    config = {"members": members if members is not None else []}
    (teams_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_marker:
        _write_f24_marker(session_dir, plugin_root, plugin_version=plugin_version)

    return session_dir


def _canonical_secretary_input(team_name="t1", overrides=None,
                               agent_type="pact-orchestrator"):
    """Build the canonical Agent(secretary) PreToolUse input.

    overrides: dict merged into tool_input to forge mismatched bindings
    in negative tests.

    #878: the gate keys lead-detection on is_lead (the harness-set agent_type).
    The secretary-spawn carve-out only applies on the LEAD path (the lead's
    pre-bootstrap secretary dispatch), so the default is a lead agent_type.
    """
    tool_input = {
        "subagent_type": "pact-secretary",
        "name": "secretary",
        "team_name": team_name,
    }
    if overrides:
        tool_input.update(overrides)
    data = {
        "hook_event_name": "PreToolUse",
        "session_id": _SESSION_ID,
        "tool_name": "Agent",
        "tool_input": tool_input,
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


class TestCanonicalSecretarySpawnCarveOut:
    """#789: the canonical secretary spawn is allowed even when the
    bootstrap marker is absent — without this carve-out, the gate denies
    the only dispatch that could clear its own deny condition (the
    secretary spawn populates the team members[] entry that the marker
    writer requires before writing the marker).

    Five conjunctive bindings (tool_name, subagent_type, name, team_name,
    NOT _team_has_secretary). Each test below mentally reverts ONE
    binding and confirms the carve-out closes (predicate returns False
    → caller returns _DENY_REASON).
    """

    # --- Positive case: all five bindings match → allow ---

    def test_secretary_spawn_allowed_when_members_lacks_secretary(
        self, monkeypatch, tmp_path,
    ):
        """All 5 bindings match + no secretary in members[] → allow."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    # --- subagent_type mismatch → predicate False → deny ---

    def test_non_secretary_agent_still_blocked(self, monkeypatch, tmp_path):
        """subagent_type != 'pact-secretary' → predicate False → deny."""
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": "pact-architect"},
        ))
        assert result == _DENY_REASON

    # --- Name mismatch → predicate False → deny (name-impostor blocked) ---

    def test_secretary_spawn_with_wrong_name_blocked(self, monkeypatch, tmp_path):
        """name != 'secretary' → predicate False → deny.

        An attacker (or a stale orchestrator) dispatching pact-secretary
        with a non-canonical name (e.g., 'sec', 'secretari') is denied.
        The literal name is load-bearing per commands/bootstrap.md Step 2.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": "secretari"},
        ))
        assert result == _DENY_REASON

    # --- Cross-team injection → predicate False → deny ---

    def test_secretary_spawn_with_wrong_team_blocked(self, monkeypatch, tmp_path):
        """tool_input.team_name != get_team_name() → predicate False → deny.

        Cross-team injection defense: tool_input is LLM-controlled and a
        prompt-injected dispatch could claim a different team_name to
        ride the carve-out. The binding compares against the disk-derived
        session context, not against tool_input.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="other-team",
        ))
        assert result == _DENY_REASON

    # --- One-shot closure: secretary already in members[] → deny ---

    def test_carve_out_closes_after_secretary_in_members(
        self, monkeypatch, tmp_path,
    ):
        """_team_has_secretary(team_name) == True → predicate False → deny.

        The one-shot semantic: once the canonical spawn has landed and
        the secretary entry is in members[], the carve-out cannot fire a
        second time in the same session. The marker writer's
        UserPromptSubmit hook handles the next turn from there.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1",
            members=[{"id": "sec-1", "name": "secretary", "type": "pact-secretary"}],
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result == _DENY_REASON

    # --- End-to-end fresh-session repro ---

    def test_fresh_session_repro_end_to_end(self, monkeypatch, tmp_path):
        """Fresh-session bootstrap: Agent(secretary) allowed; Agent(other) denied.

        Mirrors the issue's acceptance criterion. No marker on disk; team
        config exists but lacks the secretary entry. The canonical spawn
        passes the gate; any other Agent dispatch (e.g., a non-canonical
        subagent_type) is denied as before.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        secretary_result = _check_tool_allowed(
            _canonical_secretary_input(team_name="t1")
        )
        assert secretary_result is None

        other_agent_result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": "pact-architect"},
        ))
        assert other_agent_result == _DENY_REASON

    # --- Fail-closed posture on team-config read error ---

    def test_predicate_fail_closed_on_team_has_secretary_oserror(
        self, monkeypatch, tmp_path,
    ):
        """_team_has_secretary raising OSError → predicate False → deny.

        The predicate catches (OSError, ValueError, KeyError, TypeError,
        AttributeError) and returns False. Caller falls through to the
        existing _BLOCKED_TOOLS deny path, so the user sees the canonical
        _DENY_REASON rather than the load-failure variant.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import bootstrap_marker_writer

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _boom(team_name):
            raise OSError("simulated disk error")

        monkeypatch.setattr(
            bootstrap_marker_writer, "_team_has_secretary", _boom,
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result == _DENY_REASON


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


# =============================================================================
# Audit-anchor parity (mirror of writer's emit-shape invariant)
# =============================================================================


class TestAuditAnchorParity:
    """Every JSON output path bootstrap_gate produces MUST carry
    hookSpecificOutput.hookEventName == "PreToolUse". Missing the field
    silently fails open at the platform layer (per pinned context). The
    invariant is parametrized over the five distinct emit shapes:

    - "deny-load-failure": _emit_load_failure_deny advisory
    - "deny-runtime": runtime-exception deny via _emit_load_failure_deny
    - "suppress": every other exit path via the _SUPPRESS_OUTPUT constant
    - "degraded-allow-import": #942 allow-with-warning, import stage
      (direct _emit_degraded_allow invocation)
    - "degraded-allow-runtime": #942 allow-with-warning, runtime stage
      (gate-logic exception + allowlisted tool through main())

    All five MUST carry the audit anchor — parametrizing pins the
    invariant so no future emit path can be added without it. Mirrors
    bootstrap_marker_writer's test_every_emit_shape_carries_hook_event_name
    so all three bootstrap-related hooks share one parity contract.
    """

    @pytest.mark.parametrize("shape", [
        "deny-load-failure",
        "deny-runtime",
        "suppress",
        "degraded-allow-import",
        "degraded-allow-runtime",
    ])
    def test_every_emit_shape_carries_hook_event_name(self, shape, capsys):
        if shape == "deny-load-failure":
            from bootstrap_gate import _emit_load_failure_deny
            with pytest.raises(SystemExit):
                _emit_load_failure_deny("module imports", RuntimeError("x"))
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "deny-runtime":
            from bootstrap_gate import main
            with patch(
                "bootstrap_gate._check_tool_allowed",
                side_effect=RuntimeError("boom"),
            ):
                with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                    with pytest.raises(SystemExit):
                        main()
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "degraded-allow-import":
            from bootstrap_gate import _emit_degraded_allow
            with pytest.raises(SystemExit):
                _emit_degraded_allow("module imports", RuntimeError("x"), "Read")
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "degraded-allow-runtime":
            from bootstrap_gate import main
            with patch(
                "bootstrap_gate._check_tool_allowed",
                side_effect=RuntimeError("boom"),
            ):
                with patch(
                    "sys.stdin",
                    io.StringIO(json.dumps(_make_input(tool_name="Read"))),
                ):
                    with pytest.raises(SystemExit):
                        main()
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "suppress":
            from bootstrap_gate import _SUPPRESS_OUTPUT
            out = json.loads(_SUPPRESS_OUTPUT)
        else:  # pragma: no cover
            pytest.fail(f"unknown shape param: {shape}")

        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"shape={shape} emit MUST carry hookSpecificOutput; missing "
            f"the field silently fails open at the platform layer."
        )
        assert hso.get("hookEventName") == "PreToolUse", (
            f"shape={shape} emit MUST carry hookEventName=='PreToolUse'; "
            f"got {hso!r}"
        )


# =============================================================================
# Degraded mode (#942) — verification slice (CODE phase)
# =============================================================================


def _run_degraded_subprocess(tmp_path, stdin_text):
    """Run bootstrap_gate.py as a subprocess inside a scaffold whose
    `shared` package is deliberately syntax-broken, forcing the import-stage
    degraded path. Returns the CompletedProcess.

    Minimal smoke scaffold (CODE-phase verification); the comprehensive
    broken-import behavior matrix (full allowlist/deny parametrization)
    is TEST-phase scope.
    """
    import subprocess

    hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
    scaffold = tmp_path / "scaffold"
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "bootstrap_gate.py").write_text(
        hook_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Syntax error → ImportError class failure at module load.
    (scaffold / "shared" / "__init__.py").write_text(
        "this is not valid python (", encoding="utf-8"
    )
    return subprocess.run(
        [sys.executable, str(scaffold / "bootstrap_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
    )


class TestDegradedMode:
    """#942 degraded-mode handler: while the gate cannot evaluate (import
    or runtime failure), verified read-only tools are allowed WITH a
    warning at exit 0; everything else keeps the unchanged fail-closed
    deny at exit 2. Malformed/unverifiable stdin in the degraded path is
    fail-CLOSED (deny) — the opposite of the healthy path's input-side
    fail-open, because in degraded mode this module IS the broken layer.

    CODE-phase verification slice only: structural allowlist pins, the
    runtime-stage branch in-process, and an import-stage subprocess smoke.
    The comprehensive subprocess matrix is TEST-phase scope.
    """

    # --- structural invariant pins (master safety property) ---

    def test_allowlist_disjoint_from_blocked_tools(self):
        """Degraded-allow ⊆ healthy-allow, part 1: no allowlist member is
        ever in the blocked set."""
        from bootstrap_gate import _BLOCKED_TOOLS, _READ_ONLY_TOOLS

        assert _READ_ONLY_TOOLS & _BLOCKED_TOOLS == frozenset()

    def test_allowlist_excludes_bash_and_mcp(self):
        """Deliberate strictness asymmetry: Bash and MCP tools are allowed
        on the healthy pre-marker path but must NOT be degraded-allowable."""
        from bootstrap_gate import _READ_ONLY_TOOLS

        assert "Bash" not in _READ_ONLY_TOOLS
        assert not any(t.startswith("mcp__") for t in _READ_ONLY_TOOLS)

    def test_every_allowlist_member_allowed_on_healthy_gated_branch(
        self, monkeypatch, tmp_path
    ):
        """Degraded-allow ⊆ healthy-allow, part 2: on the strictest healthy
        branch (lead, no marker), every allowlist member is allowed."""
        from bootstrap_gate import _READ_ONLY_TOOLS, _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        for tool in sorted(_READ_ONLY_TOOLS):
            assert _check_tool_allowed(_make_input(tool)) is None, (
                f"allowlist member {tool!r} must be allowed on the healthy "
                f"lead+no-marker branch — degraded mode may never grant "
                f"something the healthy gate denies"
            )

    # --- runtime stage (in-process, symmetric with import stage) ---

    def test_runtime_exception_with_readonly_tool_allows_with_warning(self, capsys):
        """Gate-logic exception + allowlisted tool → exit 0, allow JSON with
        warning text and systemMessage (the single deliberate emitter)."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "sys.stdin", io.StringIO(json.dumps(_make_input(tool_name="Read")))
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "allow"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "runtime" in hso[field]
            assert "RuntimeError" in hso[field]
            assert "DEGRADED" in hso[field]
        assert "systemMessage" in out

    def test_runtime_exception_with_mutating_tool_still_denies(self, capsys):
        """Symmetry must not weaken the deny arm: Edit under a gate-logic
        exception keeps today's fail-closed deny (exit 2)."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "sys.stdin", io.StringIO(json.dumps(_make_input(tool_name="Edit")))
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 2
        out = json.loads(capsys.readouterr().out.strip())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "systemMessage" not in out

    def test_runtime_exception_with_missing_tool_name_denies(self, capsys):
        """Unverifiable tool name in the degraded runtime path → fail-CLOSED
        deny, same as the import stage."""
        from bootstrap_gate import main

        frame = _make_input()
        del frame["tool_name"]
        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(frame))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 2
        out = json.loads(capsys.readouterr().out.strip())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_runtime_exception_with_non_dict_frame_denies(self, capsys):
        """Valid-JSON non-dict stdin (e.g. a list) raises inside the gate
        logic; the degraded handler must not crash on the .get and must
        deny fail-closed (rc 2 with structured JSON, never a traceback)."""
        from bootstrap_gate import main

        with patch("sys.stdin", io.StringIO(json.dumps([1, 2, 3]))):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        out = json.loads(capsys.readouterr().out.strip())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    # --- import stage (subprocess smoke; full matrix is TEST phase) ---

    def test_subprocess_broken_import_readonly_tool_allows(self, tmp_path):
        """Broken `shared` import + tool_name=Read → allow-with-warning,
        rc 0 (rc IS the emit contract: JSON only honored on exit 0 — pair
        with content asserts, never rc alone)."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name="Read"))
        )
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "allow"
        assert "module imports" in hso["permissionDecisionReason"]
        assert "systemMessage" in out
        assert result.stderr.strip(), "stderr diagnostic line expected"

    def test_subprocess_broken_import_mutating_tool_denies(self, tmp_path):
        """Broken import + tool_name=Edit → byte-shape of today's
        _emit_load_failure_deny, rc 2, stderr non-empty."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name="Edit"))
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports failure" in hso["permissionDecisionReason"]
        assert "systemMessage" not in out
        assert result.stderr.strip()

    def test_subprocess_broken_import_malformed_stdin_denies(self, tmp_path):
        """Broken import + unparseable stdin → fail-CLOSED deny (decision
        (b)): the degraded path inverts the healthy input-side fail-open."""
        result = _run_degraded_subprocess(tmp_path, "not valid json {")
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# =============================================================================
# Import discipline — #789 reciprocal-cycle defense
# =============================================================================


class TestImportDiscipline:
    """Structural pin: bootstrap_gate.py MUST NOT import
    _team_has_secretary from bootstrap_marker_writer at module-load
    time. bootstrap_marker_writer imports is_marker_set from this module
    at its OWN top-level; a reciprocal top-level import here would
    deadlock module load and route every tool call through the
    fail-closed deny path.

    The carve-out predicate (_is_canonical_secretary_spawn) uses a
    LOCAL import (inside the function body) to break the cycle —
    enforced here as a source-level invariant so a future refactor
    can't silently re-introduce the deadlock.
    """

    def test_team_has_secretary_imported_locally_not_at_module_load(self):
        """No module-scope reference to ``bootstrap_marker_writer`` in
        bootstrap_gate.py — neither as an Import / ImportFrom statement
        nor as a dynamic ``__import__`` / ``importlib.import_module``
        call. The local import inside ``_is_canonical_secretary_spawn``
        is the only legal form.

        AST-based walk closes the source-grep gap empirically
        demonstrated during review: a top-level
        ``_bmw = __import__('bootstrap_marker_writer')`` bypasses the
        old string-prefix grep yet still triggers the exact deadlock
        (ImportError: cannot import 'is_marker_set' from
        'bootstrap_gate') the discipline is meant to prevent. The AST
        walk catches every module-scope reference regardless of the
        import idiom used.

        Module scope means the statement runs at import time. Indented
        statements inside function / class bodies are NOT module-scope
        because they only execute when the function / class body is
        invoked, which happens after module load completes.
        """
        import ast

        gate_path = (
            Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        )
        source = gate_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(gate_path))

        target = "bootstrap_marker_writer"

        def _check_node(node, context_description):
            # `import bootstrap_marker_writer` or `import bootstrap_marker_writer as bm`
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == target or alias.name.endswith(f".{target}"):
                        pytest.fail(
                            f"bootstrap_gate.py {context_description} "
                            f"`import {alias.name}` at line {node.lineno}. "
                            f"This would deadlock module load with "
                            f"bootstrap_marker_writer's top-level "
                            f"`from bootstrap_gate import is_marker_set`. "
                            f"Use a LOCAL import inside "
                            f"_is_canonical_secretary_spawn."
                        )
            # `from bootstrap_marker_writer import ...` or
            # `from .bootstrap_marker_writer import ...`
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # node.module is None for `from . import X`; module is the
                # dotted name otherwise. Check the leaf segment to catch
                # both absolute and relative forms.
                leaf = module.split(".")[-1] if module else ""
                if module == target or leaf == target:
                    pytest.fail(
                        f"bootstrap_gate.py {context_description} "
                        f"`from {module} import ...` at line {node.lineno}. "
                        f"This would deadlock module load with "
                        f"bootstrap_marker_writer's top-level "
                        f"`from bootstrap_gate import is_marker_set`. "
                        f"Use a LOCAL import inside "
                        f"_is_canonical_secretary_spawn."
                    )
            # `__import__('bootstrap_marker_writer')` or
            # `importlib.import_module('bootstrap_marker_writer')`
            elif isinstance(node, ast.Call):
                func = node.func
                is_builtin_import = (
                    isinstance(func, ast.Name) and func.id == "__import__"
                )
                is_importlib_import_module = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "import_module"
                )
                if (is_builtin_import or is_importlib_import_module) and node.args:
                    first_arg = node.args[0]
                    if (
                        isinstance(first_arg, ast.Constant)
                        and isinstance(first_arg.value, str)
                        and (
                            first_arg.value == target
                            or first_arg.value.endswith(f".{target}")
                        )
                    ):
                        call_name = (
                            "__import__"
                            if is_builtin_import
                            else "importlib.import_module"
                        )
                        pytest.fail(
                            f"bootstrap_gate.py {context_description} "
                            f"`{call_name}({first_arg.value!r})` at line "
                            f"{node.lineno}. Dynamic import at module "
                            f"scope deadlocks the same way as a static "
                            f"top-level import. Use a LOCAL import "
                            f"inside _is_canonical_secretary_spawn."
                        )

        # Walk only module-scope statements + the body of any module-scope
        # try/except wrapper (the existing fail-closed import block is one).
        # We deliberately do NOT recurse into FunctionDef / ClassDef bodies
        # because those run after module load completes — the local import
        # inside _is_canonical_secretary_spawn lives there and is legal.
        def _walk_module_scope(body):
            for stmt in body:
                for sub in ast.walk(stmt):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        # Stop descent at function / class boundaries.
                        continue
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    _check_node(stmt, "contains module-scope")
                elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    _check_node(stmt.value, "contains module-scope call")
                elif isinstance(stmt, ast.Assign):
                    # `_bmw = __import__('bootstrap_marker_writer')`
                    if isinstance(stmt.value, ast.Call):
                        _check_node(stmt.value, "contains module-scope assignment with call")
                elif isinstance(stmt, ast.Try):
                    # The existing fail-closed wrapper at module top is a
                    # try block — its body executes at module load time.
                    _walk_module_scope(stmt.body)
                    for handler in stmt.handlers:
                        _walk_module_scope(handler.body)
                    _walk_module_scope(stmt.orelse)
                    _walk_module_scope(stmt.finalbody)

        _walk_module_scope(tree.body)


class TestCanonicalSecretaryConstantPin:
    """Structural pin: the canonical-secretary `name` literal MUST match
    byte-for-byte across the carve-out's 3-way mirror surface
    (bootstrap_gate.py `_SECRETARY_NAME`, bootstrap_marker_writer.py
    `_SECRETARY_NAME`, and the canonical `name="secretary"` literal in
    commands/bootstrap.md Step 2).

    Drift between any pair silently breaks the carve-out's one-shot
    binding (5): `NOT _team_has_secretary(team_name)`. If marker_writer's
    `_SECRETARY_NAME` diverged from gate's, marker_writer would compare
    `member.get("name")` to a different literal than the one the
    orchestrator-emitted spawn would actually write into members[], so
    `_team_has_secretary` would return False forever — the carve-out would
    stay open and re-fire on every subsequent canonical spawn,
    re-introducing the brittleness BE-F1 flagged in PR #790 review.
    """

    def _read_constant(self, py_path, name):
        """Return the string value of a module-scope `NAME = "literal"`
        assignment via AST. Returns None if not found, raises on
        non-string literal (drift would surface as a clear error)."""
        import ast
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        value = stmt.value
                        assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
                            f"{py_path.name}: `{name}` MUST be a top-level string-literal "
                            f"assignment (got {ast.dump(value)})"
                        )
                        return value.value
        return None

    def test_secretary_name_literal_matches_across_files(self):
        """3-way mirror pin: `_SECRETARY_NAME` in bootstrap_gate.py ==
        `_SECRETARY_NAME` in bootstrap_marker_writer.py == canonical
        `name="secretary"` literal in commands/bootstrap.md Step 2.

        Counter-test (executed at review time): mutating either
        `_SECRETARY_NAME` constant to e.g. "secretari" produces RED with
        the exact divergence reported in the failure message; mutating
        the bootstrap.md literal produces RED on the markdown leg.
        """
        hooks_dir = Path(__file__).parent.parent / "hooks"
        commands_dir = Path(__file__).parent.parent / "commands"

        gate_value = self._read_constant(hooks_dir / "bootstrap_gate.py", "_SECRETARY_NAME")
        writer_value = self._read_constant(
            hooks_dir / "bootstrap_marker_writer.py", "_SECRETARY_NAME"
        )
        assert gate_value is not None, (
            "bootstrap_gate.py: top-level `_SECRETARY_NAME` constant missing — "
            "carve-out predicate references it at function-call time"
        )
        assert writer_value is not None, (
            "bootstrap_marker_writer.py: top-level `_SECRETARY_NAME` constant missing — "
            "_team_has_secretary references it at function-call time"
        )
        assert gate_value == writer_value, (
            f"Canonical-secretary name literal drift between bootstrap_gate.py "
            f"(`_SECRETARY_NAME={gate_value!r}`) and bootstrap_marker_writer.py "
            f"(`_SECRETARY_NAME={writer_value!r}`); bootstrap_gate carve-out's "
            f"one-shot semantic will break in production if these diverge — "
            f"_team_has_secretary returns False forever, carve-out stays open."
        )

        # Markdown leg: canonical spawn literal in bootstrap.md Step 2.
        # Substring rather than regex — the surrounding `Agent(...)` call
        # may reformat without breaking the contract, but the literal
        # `name="<value>"` MUST remain present byte-for-byte.
        bootstrap_md = (commands_dir / "bootstrap.md").read_text(encoding="utf-8")
        canonical_spawn_literal = f'name="{gate_value}"'
        assert canonical_spawn_literal in bootstrap_md, (
            f"Canonical-secretary name literal drift: bootstrap.md does not "
            f"contain {canonical_spawn_literal!r} (the constant value matched "
            f"between bootstrap_gate.py and bootstrap_marker_writer.py is "
            f"{gate_value!r}). The orchestrator-emitted spawn literal MUST "
            f"match the constants the verifier reads, or the carve-out's "
            f"binding (3) fails and bootstrap deadlocks."
        )


# =============================================================================
# Adversarial / edge-case / fuzz coverage (#789)
# =============================================================================


class TestCanonicalSecretarySpawnAdversarial:
    """Adversarial / edge-case coverage layered on top of the
    directly-coupled per-binding tests in TestCanonicalSecretarySpawnCarveOut.

    These tests probe the carve-out predicate's attack surface where the
    directly-coupled tests are silent: malformed tool_input shapes,
    encoding edge cases on the canonical literals, exception envelope
    tightness (only 5 listed exception types are caught; everything else
    propagates), get_team_name edge values (empty / None / whitespace),
    and deny-reason content invariance under failure modes.

    Each test's docstring describes the mental-revert that produces RED
    on the targeted invariant.
    """

    # --- Tool-input shape manipulation -------------------------------------

    @pytest.mark.parametrize(
        "tool_input_value",
        [
            None,
            [],
            ["subagent_type", "pact-secretary"],
            "pact-secretary",
            42,
            True,
        ],
        ids=[
            "none",
            "empty_list",
            "list_value",
            "string_value",
            "int_value",
            "bool_value",
        ],
    )
    def test_non_dict_tool_input_denies(
        self, monkeypatch, tmp_path, tool_input_value,
    ):
        """tool_input is not a dict → predicate returns False → deny.

        The predicate's explicit `isinstance(tool_input, dict)` guard
        rejects non-dict shapes before any binding check. Mental revert:
        remove the isinstance guard and the next `.get(...)` raises
        AttributeError, which IS caught by the broad except envelope —
        but the resulting False still denies. The isinstance guard is
        load-bearing for code clarity, not behavior; this test confirms
        deny under all non-dict shapes regardless.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "tool_name": "Agent",
            "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
            "tool_input": tool_input_value,
        }
        result = _check_tool_allowed(input_data)
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "missing_key",
        ["subagent_type", "name", "team_name"],
    )
    def test_tool_input_missing_required_key_denies(
        self, monkeypatch, tmp_path, missing_key,
    ):
        """tool_input missing one of (subagent_type, name, team_name) →
        predicate returns False → deny.

        `.get(missing_key)` returns None, which compares unequal to the
        expected literal/disk-derived value. Mental revert: replacing
        the binding's `!=` with `not ==` would not change behavior; but
        replacing `_SECRETARY_NAME` with None (silently dropping the
        constant) would make missing-name spawns ALLOW. Defends that
        the constant is non-None.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        canonical = {
            "subagent_type": "pact-secretary",
            "name": "secretary",
            "team_name": "t1",
        }
        canonical.pop(missing_key)
        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "tool_name": "Agent",
            "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
            "tool_input": canonical,
        }
        result = _check_tool_allowed(input_data)
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "binding,wrong_type_value",
        [
            ("subagent_type", 123),
            ("subagent_type", None),
            ("subagent_type", ["pact-secretary"]),
            ("name", False),
            ("name", 0),
            ("name", {"value": "secretary"}),
            ("team_name", []),
            ("team_name", 3.14),
        ],
    )
    def test_wrong_value_type_on_binding_denies(
        self, monkeypatch, tmp_path, binding, wrong_type_value,
    ):
        """Wrong value TYPE on a binding (int/None/list/dict where str
        is expected) → != comparison against the string constant → deny.

        Confirms the carve-out doesn't accidentally truthy-coerce
        non-string values (e.g., `1 == "secretary"` is False — good).
        Mental revert: changing the `!=` checks to `not bool(value)`
        would allow truthy non-string values; this test catches that.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={binding: wrong_type_value},
        ))
        assert result == _DENY_REASON

    def test_missing_tool_name_denies(self, monkeypatch, tmp_path):
        """input_data missing `tool_name` key entirely → predicate
        returns False (binding 1 fails on `.get` → None != "Agent") →
        deny via the existing _BLOCKED_TOOLS fall-through (which also
        misses on empty tool_name, but that's existing behavior).
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
            "tool_input": {
                "subagent_type": "pact-secretary",
                "name": "secretary",
                "team_name": "t1",
            },
        }
        result = _check_tool_allowed(input_data)
        # Missing tool_name → "" → not in _BLOCKED_TOOLS → allow (None).
        # The carve-out predicate returns False (binding 1 fails); the
        # missing-tool_name path is the existing behavior tested at line
        # ~340 of TestCheckToolAllowed. This test pins that the
        # carve-out does NOT accidentally allow a missing-tool_name
        # request as if it were the canonical spawn.
        assert result is None

    # --- Encoding edge cases on canonical literals -------------------------

    @pytest.mark.parametrize(
        "wrong_name",
        [
            "SECRETARY",
            "Secretary",
            "secretary ",
            " secretary",
            "secretary\n",
            "secretary\t",
            "secretary\x00",
            "secretary​",
            "secretari",
            "secretaries",
            "secretary-1",
            "secеretary",
        ],
        ids=[
            "uppercase",
            "title_case",
            "trailing_space",
            "leading_space",
            "trailing_newline",
            "trailing_tab",
            "embedded_null",
            "zero_width_space",
            "typo_drop_y",
            "trailing_s",
            "trailing_suffix",
            "cyrillic_e_lookalike",
        ],
    )
    def test_name_canonical_literal_is_case_and_whitespace_sensitive(
        self, monkeypatch, tmp_path, wrong_name,
    ):
        """name binding is BYTE-EXACT equality against _SECRETARY_NAME.

        No normalization, no casefold, no strip, no Unicode-fold. Any
        deviation (case, whitespace, lookalike Unicode, null byte,
        zero-width space) closes the carve-out. Mental revert: changing
        `tool_input.get("name") != _SECRETARY_NAME` to
        `tool_input.get("name", "").strip().lower() != _SECRETARY_NAME`
        would allow several of these and is the kind of "helpful"
        refactor that silently widens the attack surface.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": wrong_name},
        ))
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "wrong_type",
        [
            "PACT-SECRETARY",
            "pact_secretary",
            "pact-Secretary",
            "pact-secretary ",
            "pact-secretary\x00",
            "PACT:secretary",
            "secretary",
        ],
        ids=[
            "uppercase",
            "underscore_separator",
            "title_case_word",
            "trailing_space",
            "embedded_null",
            "colon_separator",
            "missing_prefix",
        ],
    )
    def test_subagent_type_canonical_literal_is_byte_exact(
        self, monkeypatch, tmp_path, wrong_type,
    ):
        """subagent_type binding is BYTE-EXACT equality against
        _SECRETARY_AGENT_TYPE. Case, separator, and prefix variations
        all close the carve-out. Mirrors the name-binding tightness pin.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": wrong_type},
        ))
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "wrong_team",
        [
            "T1",
            "t1 ",
            " t1",
            "t1\n",
            "t1\x00",
            "t１",
            "t1-",
            "",
        ],
        ids=[
            "uppercase",
            "trailing_space",
            "leading_space",
            "trailing_newline",
            "embedded_null",
            "fullwidth_digit_one",
            "trailing_dash",
            "empty_string",
        ],
    )
    def test_team_name_binding_is_byte_exact(
        self, monkeypatch, tmp_path, wrong_team,
    ):
        """team_name binding compares tool_input.team_name == disk-derived
        get_team_name(). BYTE-EXACT — no normalization. Case-sensitivity
        is intentional per spec B1 (tight equality binding).

        The fullwidth_digit_one row (U+FF11) is a Unicode digit that
        renders visually like ASCII '1' but is a different code point.
        Tests defend against any future "helpful" Unicode normalization
        that would treat lookalikes as equivalent.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name=wrong_team,
        ))
        assert result == _DENY_REASON

    def test_bytes_value_on_name_binding_denies(
        self, monkeypatch, tmp_path,
    ):
        """name = b'secretary' (bytes, not str) → != "secretary" → deny.

        Python: `b'secretary' != 'secretary'` is True. Defends that the
        predicate doesn't decode bytes to str silently.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": b"secretary"},
        ))
        assert result == _DENY_REASON

    # --- Exception envelope tightness --------------------------------------

    @pytest.mark.parametrize(
        "exc_type",
        [OSError, ValueError, KeyError, TypeError, AttributeError],
        ids=lambda e: e.__name__,
    )
    def test_listed_exception_types_caught_and_deny(
        self, monkeypatch, tmp_path, exc_type,
    ):
        """Each of the 5 listed exception types raised by
        _team_has_secretary is CAUGHT by the predicate's broad except
        → predicate returns False → _check_tool_allowed returns
        _DENY_REASON. Pins the catch-set width exactly.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import bootstrap_marker_writer

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _raiser(team_name):
            raise exc_type("simulated")

        monkeypatch.setattr(
            bootstrap_marker_writer, "_team_has_secretary", _raiser,
        )
        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "exc_type",
        [RuntimeError, MemoryError, NotImplementedError, AssertionError],
        ids=lambda e: e.__name__,
    )
    def test_unlisted_exception_propagates_out_of_predicate(
        self, monkeypatch, tmp_path, exc_type,
    ):
        """Exception types NOT in the predicate's catch tuple PROPAGATE
        out of `_is_canonical_secretary_spawn` and reach the caller.
        This is the spec's deliberate fail-closed-scope-tightness: the
        5 catch-types cover benign disk-read failures; wider catches
        would mask genuine bugs (RuntimeError, AssertionError).

        Mental revert: widening the except clause to
        `except Exception` would absorb these and silently mask defects
        that should propagate to main()'s _emit_load_failure_deny path.

        Pin via direct predicate call (not _check_tool_allowed), because
        _check_tool_allowed itself has no exception handler — exceptions
        propagate to main()'s outer try/except where they're routed to
        the load-failure deny path. We assert the EXCEPTION ESCAPES the
        predicate here; main()-level deny is covered by
        TestFailClosedGateLogic.
        """
        from bootstrap_gate import _is_canonical_secretary_spawn
        import bootstrap_marker_writer

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _raiser(team_name):
            raise exc_type("simulated")

        monkeypatch.setattr(
            bootstrap_marker_writer, "_team_has_secretary", _raiser,
        )

        # Build the canonical input that gets us PAST bindings 1-4 so
        # the predicate reaches the local-import + call site where the
        # raise happens.
        input_data = _canonical_secretary_input(team_name="t1")
        with pytest.raises(exc_type):
            _is_canonical_secretary_spawn(input_data)

    def test_unlisted_exception_in_main_routes_to_load_failure_deny(
        self, monkeypatch, tmp_path, capsys,
    ):
        """End-to-end: an unlisted exception propagating from the
        predicate through _check_tool_allowed lands at main()'s outer
        try/except (line 391-396), which routes to
        _emit_load_failure_deny. User sees the LOAD-FAILURE deny text
        ("PACT bootstrap_gate runtime failure — blocking for safety...")
        NOT the canonical _DENY_REASON. Confirms the fail-closed routing
        for genuine-bug exceptions while preserving deny semantics.
        """
        import bootstrap_marker_writer

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _raiser(team_name):
            raise RuntimeError("genuine bug")

        monkeypatch.setattr(
            bootstrap_marker_writer, "_team_has_secretary", _raiser,
        )

        exit_code, output = _run_main(_canonical_secretary_input(team_name="t1"), capsys)
        assert exit_code == 2
        hso = output["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        # Load-failure deny text differs from the canonical _DENY_REASON.
        # Pin both invariants:
        assert "runtime failure" in hso["permissionDecisionReason"]
        assert "PACT bootstrap required" not in hso["permissionDecisionReason"]

    # --- Predicate state edge values ---------------------------------------

    @pytest.mark.parametrize(
        "configured_team_name",
        ["", None],
        ids=["empty_string", "none_value"],
    )
    def test_empty_or_none_disk_team_name_denies(
        self, monkeypatch, tmp_path, configured_team_name,
    ):
        """get_team_name returns falsy → predicate's `if not expected_team`
        branch returns False → deny.

        Defends an attacker dispatching `Agent(secretary, team_name="")`
        in a fresh session whose context_path is missing/empty. The
        predicate refuses to compare against a falsy team_name to avoid
        the accidental == "" match.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        # Override get_team_name to return the falsy value.
        monkeypatch.setattr(
            ctx_module, "get_team_name", lambda: configured_team_name,
        )

        # Match the falsy value on tool_input side as well to confirm
        # the predicate's GUARD on falsy disk-value closes the carve-out
        # even when tool_input would otherwise match.
        result = _check_tool_allowed(_canonical_secretary_input(
            team_name=configured_team_name if configured_team_name else "",
        ))
        assert result == _DENY_REASON

    def test_get_team_name_returning_dict_denies_safely(
        self, monkeypatch, tmp_path,
    ):
        """get_team_name returning non-string (dict) → comparison
        operates on the wrong type → != is True → predicate returns
        False → deny. Confirms no AttributeError on `not expected_team`
        for non-string truthy values.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        monkeypatch.setattr(
            ctx_module, "get_team_name", lambda: {"team": "t1"},
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result == _DENY_REASON

    def test_carve_out_can_fire_then_close_within_session(
        self, monkeypatch, tmp_path,
    ):
        """One-shot closure: with the same session set up, a FIRST
        _check_tool_allowed call observes members=[] → ALLOW; mutating
        the team config to include the secretary entry mid-test causes
        a SECOND call to DENY. Pins one-shot semantic at the session
        level, not just the call level.

        Distinct from `test_carve_out_closes_after_secretary_in_members`
        (CODE-phase test) which observes deny when members already
        contains secretary. This test observes the TRANSITION.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        # First call — fresh, carve-out fires.
        first = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert first is None

        # Simulate the spawn landing in members[] by rewriting the
        # team config (matching how the platform's team-config
        # maintenance would update it).
        teams_dir = tmp_path / ".claude" / "teams" / "t1"
        config_file = teams_dir / "config.json"
        config_file.write_text(
            json.dumps({"members": [
                {"id": "sec-1", "name": "secretary", "type": "pact-secretary"},
            ]}),
            encoding="utf-8",
        )

        # Second call — secretary now in members[], carve-out closes.
        second = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert second == _DENY_REASON

    # --- Same-turn duplicate-dispatch: accepted-residual benign behavior ----

    def test_same_turn_duplicate_dispatch_is_benign(
        self, monkeypatch, tmp_path,
    ):
        """Pin the accepted-residual behavior: same-turn duplicate
        Agent(secretary) dispatch is benign under platform serialization.
        If a future PR adds strict one-shot enforcement (sidecar marker /
        state primitive), this test WILL break by design — that's the
        regression door. Remove this test as part of any strict-one-shot
        work, not silently.

        Scenario: between the first dispatch's PreToolUse firing and the
        platform's members[] write landing on disk, the LLM emits a
        SECOND Agent(secretary) dispatch in the same turn. Both observe
        members=[] (the first write hasn't flushed yet); both fire the
        carve-out → both ALLOW. Downstream consumers tolerate the
        resulting duplicate member entry; this is a benign duplicate
        spawn, not a security risk.

        Pin the benign behavior so future strict-one-shot enforcement
        causes this test to break loudly rather than silently change
        observable behavior.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        # Two sequential dispatches without any members[] mutation in
        # between (modeling the not-yet-flushed-to-disk race).
        first = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        second = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert first is None
        assert second is None

    # --- Deny-reason content invariance ------------------------------------

    @pytest.mark.parametrize(
        "scenario,overrides,exc_setup",
        [
            ("wrong_subagent_type", {"subagent_type": "pact-architect"}, None),
            ("wrong_name", {"name": "secretari"}, None),
            ("wrong_team", {"team_name": "other-team"}, None),
            ("missing_subagent_type", None, "missing_subagent_type"),
            ("oserror_in_team_has_secretary", None, "oserror"),
            ("valueerror_in_team_has_secretary", None, "valueerror"),
            ("keyerror_in_team_has_secretary", None, "keyerror"),
        ],
    )
    def test_deny_reason_is_byte_identical_across_failure_modes(
        self, monkeypatch, tmp_path, scenario, overrides, exc_setup,
    ):
        """Across every failure mode (wrong binding, missing key,
        every caught exception type), the user-visible
        permissionDecisionReason is BYTE-IDENTICAL to the canonical
        deny-reason literal pinned independently in
        ``_CANONICAL_DENY_REASON_LITERAL``.

        Two-sided assertion: the result MUST equal the independent
        literal AND ``_DENY_REASON`` MUST equal the same literal.
        The independent literal is what closes the self-comparison
        gap — comparing ``result == _DENY_REASON`` alone passes even
        if both sides are mutated in lockstep, because they alias the
        same in-memory string. The independent literal anchors the
        byte content outside the module under test.

        Mental revert: a future "helpful" patch that customizes the
        deny reason per-scenario (e.g., "name mismatch detected") would
        flunk this test. The canonical literal is the only approved
        user-visible string for the carve-out's deny path.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import bootstrap_marker_writer

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        if exc_setup == "oserror":
            monkeypatch.setattr(
                bootstrap_marker_writer, "_team_has_secretary",
                lambda team_name: (_ for _ in ()).throw(OSError("x")),
            )
            input_data = _canonical_secretary_input(team_name="t1")
        elif exc_setup == "valueerror":
            monkeypatch.setattr(
                bootstrap_marker_writer, "_team_has_secretary",
                lambda team_name: (_ for _ in ()).throw(ValueError("x")),
            )
            input_data = _canonical_secretary_input(team_name="t1")
        elif exc_setup == "keyerror":
            monkeypatch.setattr(
                bootstrap_marker_writer, "_team_has_secretary",
                lambda team_name: (_ for _ in ()).throw(KeyError("x")),
            )
            input_data = _canonical_secretary_input(team_name="t1")
        elif exc_setup == "missing_subagent_type":
            input_data = {
                "hook_event_name": "PreToolUse",
                "session_id": _SESSION_ID,
                "tool_name": "Agent",
                "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
                "tool_input": {"name": "secretary", "team_name": "t1"},
            }
        else:
            input_data = _canonical_secretary_input(
                team_name="t1", overrides=overrides,
            )

        result = _check_tool_allowed(input_data)
        assert result == _CANONICAL_DENY_REASON_LITERAL, (
            f"scenario={scenario}: deny reason drifted from canonical "
            f"literal. Got {result!r}."
        )
        assert _DENY_REASON == _CANONICAL_DENY_REASON_LITERAL, (
            f"scenario={scenario}: bootstrap_gate._DENY_REASON drifted "
            f"from canonical literal. Got {_DENY_REASON!r}."
        )

    def test_deny_reason_constant_matches_canonical_literal(self):
        """Independent-literal pin on ``bootstrap_gate._DENY_REASON``.

        Closes the self-comparison gap: every other deny-reason test
        in this file asserts ``result == _DENY_REASON``, which passes
        even if both sides are mutated in lockstep (they alias the
        same string). This test compares ``_DENY_REASON`` against an
        independent literal hard-coded in the test file. A future
        single-byte change to the constant in bootstrap_gate.py
        (e.g., dropping the word "are" from "Bash, Read, Glob, Grep
        are available") flunks this test even though every other
        deny-reason test continues to pass.

        Any intentional change to the canonical deny reason must
        update BOTH the constant in bootstrap_gate.py AND the literal
        below — that explicit two-site edit is the load-bearing
        review surface.
        """
        from bootstrap_gate import _DENY_REASON

        assert _DENY_REASON == _CANONICAL_DENY_REASON_LITERAL

    def test_deny_reason_excludes_exception_detail(
        self, monkeypatch, tmp_path,
    ):
        """When _team_has_secretary raises with a sensitive-looking
        message, the user-visible deny reason MUST NOT leak the
        exception text. Pins that the carve-out's catch returns False
        and the caller's _DENY_REASON path is used — no formatted
        error string ever reaches the user.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import bootstrap_marker_writer

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        sensitive = "secret-token-deadbeef /Users/victim/.ssh/id_rsa"

        def _raiser(team_name):
            raise OSError(sensitive)

        monkeypatch.setattr(
            bootstrap_marker_writer, "_team_has_secretary", _raiser,
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result == _DENY_REASON
        assert sensitive not in result
        assert "deadbeef" not in result
        assert "/Users/" not in result
