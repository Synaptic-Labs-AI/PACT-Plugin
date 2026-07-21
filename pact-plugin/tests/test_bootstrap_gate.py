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
    "until bootstrap completes. Bash, Read, Glob, Grep are available. "
    "If bootstrap cannot complete because the task-management tools are "
    "unavailable, see "
    "https://github.com/Synaptic-Labs-AI/PACT-Plugin#enabling-agent-teams"
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
    suppress/deny paths; the degraded warn path (#942, defer/ask) is the
    single deliberate systemMessage emitter (see TestDegradedMode).
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

    Mirrors _setup_pact_session but adds the team-config sidecar that the
    members[] readers consume via shared.pact_context._iter_members — the gate
    carve-out's _secretary_in_members JOIN witness (#1023) and the marker
    writer's _team_has_secretary DISPATCH witness alike. The monkeypatched
    Path.home means the team config lands under tmp_path.

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

    Four conjunctive bindings (tool_name, subagent_type, name,
    NOT _secretary_in_members). (#979: binding-4 — the tool_input.team_name
    equality — was dropped; the Agent(team_name=) arg is platform-ignored.
    #1023: binding 5 now reads the gate-local members[]-only JOIN witness
    _secretary_in_members, NOT bootstrap_marker_writer._team_has_secretary —
    the latter's inbox DISPATCH fallback is created pre-spawn and re-deadlocked
    the carve-out.) Each test below mentally reverts ONE binding and confirms
    the carve-out closes (predicate returns False → caller returns
    _DENY_REASON).
    """

    # --- Positive case: all bindings match → allow ---

    def test_secretary_spawn_allowed_when_members_lacks_secretary(
        self, monkeypatch, tmp_path,
    ):
        """All bindings match + no secretary in members[] → allow."""
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

    def test_secretary_spawn_ignores_caller_team_name(self, monkeypatch, tmp_path):
        """#979: binding-4 DROPPED — the carve-out no longer compares the
        LLM-controlled tool_input.team_name. The Agent(team_name=) arg is
        platform-ignored, so a spawn passing a DIFFERENT team_name still rides
        the carve-out (the secretary-presence check resolves via the SSOT
        get_team_name(), not the spawn arg). Bindings 2/3 (subagent_type + name
        literals) and 5 (one-shot, gated on the real team dir) keep it tight.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="other-team",
        ))
        assert result is None

    # --- One-shot closure: secretary already in members[] → deny ---

    def test_carve_out_closes_after_secretary_in_members(
        self, monkeypatch, tmp_path,
    ):
        """_secretary_in_members(team_name) == True → predicate False → deny.

        The one-shot semantic (CLI): once the canonical spawn has landed and
        the secretary entry is in members[], the JOIN witness returns True so
        the carve-out cannot fire a second time in the same session. The marker
        writer's UserPromptSubmit hook handles the next turn from there.
        (#1023 D-record: under config-less Desktop members[] is structurally
        empty, so this self-closure does not apply there — marker-presence is
        the durable one-shot instead.)
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

    def test_witness_read_error_fires_carve_out_allow(
        self, monkeypatch, tmp_path,
    ):
        """A members[] read error in the JOIN witness → carve-out FIRES (allow).

        #1023: the join witness is the gate-local _secretary_in_members, which
        reads pact_context._iter_members and wraps its body in a broad
        ``except Exception: return False``. A read error therefore makes the
        witness return False → binding 5 (`not False`) is True → the carve-out
        fires → the canonical secretary spawn is ALLOWED (result is None).

        This is the deliberate SAFE fail direction (architect D-record): a
        witness-read error only ever PERMITS the canonical secretary spawn —
        bindings 1/2/3 (exact Agent + pact-secretary + secretary literals)
        still exclude every non-secretary tool — and it specifically avoids
        the re-deadlock that the pre-#1023 typed-except-DENY direction caused
        on the Path.home() RuntimeError seam.

        We monkeypatch pact_context._iter_members (the witness's data source)
        to RAISE, exercising _secretary_in_members's broad-except arm
        end-to-end — NOT stubbing _secretary_in_members itself, which would
        bypass the very except clause this test must prove.
        """
        from bootstrap_gate import _check_tool_allowed
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _boom(team_name, teams_dir=None):
            raise OSError("simulated disk error")

        monkeypatch.setattr(ctx_module, "_iter_members", _boom)

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None


# =============================================================================
# Premature-inbox carve-out regression (#1023)
# =============================================================================


def _create_secretary_inbox(tmp_path, team_name="t1"):
    """Reproduce the platform's PRE-SPAWN secretary-inbox write.

    The bootstrap choreography is::

        Step 1  TaskCreate(secretary briefing)
        Step 2  TaskUpdate(taskId, owner="secretary")   # platform delivers a
                                                         # task_assignment ->
                                                         # CREATES inboxes/secretary.json
        Step 3  Agent(name="secretary", ...)            # the carve-out must fire HERE

    So by Step 3 ``teams/<team>/inboxes/secretary.json`` ALREADY exists, written
    by the Step-2 ``TaskUpdate(owner)`` task-assignment delivery — BEFORE the
    ``Agent(secretary)`` spawn the carve-out exists to permit. This helper writes
    that file FAITHFULLY: the SAME path the marker writer's inbox witness reads
    (``get_claude_config_dir()/teams/<team>/inboxes/secretary.json`` ==
    ``Path.home()/.claude/...`` under the fixture's Path.home monkeypatch), the
    SAME filename, and a ``task_assignment``-shaped body matching the issue's
    ground-truth evidence (an assigned task with ``assignedBy="team-lead"``).

    This is NOT a synthetic pre-placed file divorced from the choreography — it
    is the exact artifact ``TaskUpdate(owner="secretary")`` produces, present at
    gate-eval time. The synthetic shortcut (an empty touch, or a file written in
    a shape the platform never produces) is precisely what let #1021 slip through
    #1019's verification, so the regression test must use the real shape.

    Returns the inbox file path.
    """
    inbox_dir = tmp_path / ".claude" / "teams" / team_name / "inboxes"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_path = inbox_dir / "secretary.json"
    # task_assignment-shaped body (issue #1023 evidence: a task_assignment for the
    # secretary task, assignedBy team-lead). The marker writer's inbox witness
    # only checks .is_file() — it never parses the body — but a faithful body
    # keeps the fixture honest about what the platform actually writes.
    inbox_path.write_text(json.dumps([
        {
            "type": "task_assignment",
            "taskId": "1",
            "subject": "secretary briefing",
            "assignedBy": "team-lead",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    ]), encoding="utf-8")
    return inbox_path


def _setup_carveout_team_with_lead_session(
    monkeypatch, tmp_path, *, team_name="t1", lead_session_id,
    frame_session_id, members=None, seed_team_dir_name=None,
):
    """Both-modes carve-out fixture: like _setup_pact_session_with_team but the
    on-disk config.json carries a ``leadSessionId`` field and the context's
    ``session_id`` is set independently, so get_team_name's identity-match
    (_resolve_aligned_team_name) sees a genuine in-process (frame==lead) vs tmux
    (frame!=lead) topology.

    ``team_name`` is the PERSISTED context team_name (use "" to exercise the
    empty-SSOT fail-closed short-circuit). ``seed_team_dir_name`` is the dir name
    the team config/inbox are seeded under (defaults to ``team_name``); pass it
    explicitly when team_name is "" so the team dir still exists on disk but the
    SSOT is empty.

    Resets _aligned_cache (NOT reset by init(), which is a no-op once
    _context_path is pre-set) so each leg / loop iteration resolves freshly.
    """
    import shared.pact_context as ctx_module

    dir_name = seed_team_dir_name if seed_team_dir_name is not None else team_name

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": team_name,
        "session_id": frame_session_id,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    if dir_name:
        teams_dir = tmp_path / ".claude" / "teams" / dir_name
        teams_dir.mkdir(parents=True, exist_ok=True)
        (teams_dir / "config.json").write_text(json.dumps({
            "name": dir_name,
            "leadSessionId": lead_session_id,
            "members": members if members is not None else [],
        }), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None)
    return session_dir


def _setup_carveout_session_config_less(monkeypatch, tmp_path, *, team_name="t1"):
    """Config-less Desktop carve-out fixture: a PACT session context whose
    team_name resolves, but with NO config.json on disk for the team (the
    config-less Desktop/SDK substrate). members[] is therefore structurally
    empty (no roster file to read), so the members-only join witness returns
    False and the carve-out fires.

    The team dir exists (so get_team_name's resolution lands on it) but holds
    NO config.json — only the inbox the caller seeds via _create_secretary_inbox.
    """
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": team_name,
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    # Team dir exists but deliberately has NO config.json (config-less Desktop).
    teams_dir = tmp_path / ".claude" / "teams" / team_name
    teams_dir.mkdir(parents=True, exist_ok=True)
    assert not (teams_dir / "config.json").exists()

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None)
    return session_dir


class TestPrematureInboxCarveOutRegression:
    """#1023: the canonical secretary spawn must STILL be allowed when the
    secretary's inbox file already exists (written pre-spawn by
    ``TaskUpdate(owner="secretary")``) but the secretary is NOT yet in
    ``members[]``.

    ROOT CAUSE this guards: #1021 widened the SHARED
    ``bootstrap_marker_writer._team_has_secretary`` predicate with a config-less
    inbox-witness fallback (``members[]`` roster OR
    ``teams/<team>/inboxes/secretary.json`` exists). That predicate had a SECOND
    consumer — ``bootstrap_gate`` binding 5 — never re-examined by #1021. Because
    the platform creates the inbox at bootstrap Step 2 (``TaskUpdate(owner)``)
    BEFORE Step 3 (``Agent(secretary)``), the inbox arm returned True
    prematurely, ``not True`` made binding 5 False, the carve-out did NOT fire,
    and the gate DENIED the very spawn the carve-out exists to permit — a
    bootstrap deadlock on the first turn of every fresh CLI session under 4.4.39.

    The fix decouples by consumer: binding 5 now reads the gate-local
    ``members[]``-only JOIN witness ``_secretary_in_members`` (dispatch-witness
    via the inbox != join-witness via members[]), leaving
    ``_team_has_secretary``'s inbox DISPATCH fallback for the marker writer.

    WHY THE EXISTING CARVE-OUT TESTS DID NOT CATCH THIS: every carve-out test
    seeds the team via ``_setup_pact_session_with_team``, which writes
    ``config.json`` + ``members[]`` but NEVER creates an ``inboxes/`` dir — so
    binding 5 was only ever exercised against the ``members[]`` arm, and the
    cross-product (gate carve-out × premature inbox witness) — the exact
    regression path — was untested. These tests add the inbox witness via the
    REAL ``TaskUpdate(owner)`` write shape (``_create_secretary_inbox``) and
    assert the carve-out STILL fires.

    NON-VACUITY — VERIFICATION MATRIX (counter-test-by-revert, SOURCE-ONLY)
    ----------------------------------------------------------------------
    Binding 5 is the load-bearing source. The pre-fix shape
    (``return not _team_has_secretary(expected_team)``) reads the inbox arm, so
    a premature inbox flips it to a DENY; the post-fix shape
    (``return not _secretary_in_members(expected_team)``) reads members[] only,
    so a premature inbox is ignored and the carve-out fires.

    Measure via SOURCE-ONLY revert of the gate alone (the fix commit bundles
    these new tests with the source — a whole-commit ``git revert`` would mask
    the cardinality)::

        git checkout <fix>^ -- pact-plugin/hooks/bootstrap_gate.py
        python -m pytest tests/test_bootstrap_gate.py::TestPrematureInboxCarveOutRegression \
                         tests/test_bootstrap_gate.py::TestSecretaryInMembersUnit -q
        git checkout <fix>  -- pact-plugin/hooks/bootstrap_gate.py
        git diff --quiet -- pact-plugin/hooks/bootstrap_gate.py   # exits 0

    EMPIRICAL RED set (MEASURED against the pre-fix gate, not assumed) over
    {TestPrematureInboxCarveOutRegression + TestSecretaryInMembersUnit} =
    {7 failed, 2 passed}, decomposing as:

      BEHAVIORAL RED (4) — coupled to the inbox-arm regression, the load-bearing
      non-vacuity proof. Each asserts ``result is None`` over a present premature
      inbox, and each flips to ``_DENY_REASON`` under the pre-fix inbox-reading
      binding 5 (``not _team_has_secretary``):
        - R1 test_premature_inbox_does_not_defeat_carve_out      (THE headline)
        - R2 test_premature_inbox_carve_out_fires_in_process
        - R3 test_premature_inbox_carve_out_fires_tmux
        - C2 test_carve_out_fires_config_less_desktop_no_config

      NET-NEW-SYMBOL RED (3) — the U1 unit cells fail by ``ImportError`` because
      ``_secretary_in_members`` does not exist in the pre-fix gate (it is the
      fix's net-new symbol). This is a WEAKER non-vacuity form than a behavioral
      RED (an ImportError proves the symbol is new, not that the assertion is
      coupled to the bug); the U1 cells' OWN per-test ``# COUNTER-TEST`` notes
      describe the finer behavioral revert (typed-tuple swap / members-vs-inbox)
      that would flip them RED in isolation:
        - U1 test_true_when_secretary_in_members
        - U1 test_false_when_members_empty
        - U1 test_false_on_iter_members_raising

      GREEN (2) — FIX-INDEPENDENT by construction (they assert a DENY, not an
      allow over a present inbox), so they correctly stay GREEN under the revert;
      documented so a verifier does not expect them in the RED set:
        - E1 test_empty_ssot_fails_closed_even_with_premature_inbox_both_modes
        - CONTAINMENT test_non_canonical_input_still_blocked_with_pending_witness_error

    The existing ``test_carve_out_closes_after_secretary_in_members`` (one-shot
    closure, in TestCanonicalSecretarySpawnCarveOut) is likewise fix-independent.
    """

    # --- R1: THE headline regression (premature inbox must NOT defeat carve-out) ---

    def test_premature_inbox_does_not_defeat_carve_out(self, monkeypatch, tmp_path):
        """members=[] + a pre-spawn task-assignment-shaped inbox present →
        Agent(secretary) ALLOWED (result is None).

        Exercises the REAL inbox-witness seam UNMOCKED: the inbox file is on
        disk at the path the predicate reads, and neither _team_has_secretary
        nor _secretary_in_members is stubbed. Keyed on the OBSERVABLE outcome
        (result is None vs _DENY_REASON), not the internal helper name, so it
        stays valid across helper-shape changes.

        # COUNTER-TEST (source-only revert of bootstrap_gate.py): the pre-fix
        # binding 5 (`not _team_has_secretary`) reads the inbox arm → the
        # premature inbox makes it True → `not True` → carve-out denies → this
        # assertion (result is None) goes RED. THE headline non-vacuity cell.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        _create_secretary_inbox(tmp_path, team_name="t1")  # real pre-spawn write

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    # --- R2 / R3: both-modes legs (standing merge gate) ---

    def test_premature_inbox_carve_out_fires_in_process(self, monkeypatch, tmp_path):
        """Both-modes leg — IN-PROCESS topology (frame session_id ==
        config.leadSessionId). The carve-out resolves the team via get_team_name
        (the SSOT context), so the identity-match in _resolve_aligned_team_name
        succeeds; the premature inbox is still present and the carve-out STILL
        fires (result is None).

        The carve-out is topology-agnostic by design (it never branches on the
        in-process/tmux mode flag — DUAL-MODE PERMANENT CONTRACT item 2), so both
        legs assert the SAME outcome; they are provided as the standing merge-gate
        formality, with the leadSessionId seeded so the two topologies are
        genuinely distinct (not a duplicated assertion).

        # COUNTER-TEST (source-only revert): same as R1 — the premature inbox
        # flips the pre-fix inbox-reading binding 5 to a DENY → RED.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_carveout_team_with_lead_session(
            monkeypatch, tmp_path, team_name="t1",
            lead_session_id=_SESSION_ID, frame_session_id=_SESSION_ID,
        )
        _create_secretary_inbox(tmp_path, team_name="t1")

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    def test_premature_inbox_carve_out_fires_tmux(self, monkeypatch, tmp_path):
        """Both-modes leg — TMUX topology (frame session_id !=
        config.leadSessionId). The running frame's own session id differs from
        the lead's, so _resolve_aligned_team_name finds no identity match and
        falls back to the persisted ctx team_name — which still resolves the same
        team dir, so get_team_name returns the same team and the carve-out STILL
        fires over the premature inbox (result is None).

        # COUNTER-TEST (source-only revert): same as R1 → RED.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_carveout_team_with_lead_session(
            monkeypatch, tmp_path, team_name="t1",
            lead_session_id="a-different-lead-session-id",
            frame_session_id=_SESSION_ID,
        )
        _create_secretary_inbox(tmp_path, team_name="t1")

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    # --- C2: config-less Desktop always-fire ---

    def test_carve_out_fires_config_less_desktop_no_config(self, monkeypatch, tmp_path):
        """Config-less Desktop: NO config.json at all + a premature inbox →
        carve-out FIRES (result is None). Under config-less Desktop members[] is
        STRUCTURALLY empty (there is no config.json to read members from), so the
        members-only join witness returns False → `not False` → the carve-out
        fires. This is the agreed always-fire behavior (architect + security
        firm): a witness-read over a missing config returns False, which only
        ever PERMITS the canonical secretary spawn (bindings 1/2/3 still exclude
        every non-secretary tool), and the is_marker_set fast-path closes the
        one-shot window once the marker lands.

        # COUNTER-TEST (source-only revert): the pre-fix binding 5 reads
        # _team_has_secretary, whose inbox fallback fires on the present
        # config-less inbox → True → `not True` → DENY → this assertion
        # (result is None) goes RED. Couples to the inbox-arm regression exactly
        # like R1.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_carveout_session_config_less(monkeypatch, tmp_path, team_name="t1")
        _create_secretary_inbox(tmp_path, team_name="t1")  # inbox witness, NO config.json

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    # --- E1: empty-SSOT fail-closed preserved, both modes ---

    def test_empty_ssot_fails_closed_even_with_premature_inbox_both_modes(
        self, monkeypatch, tmp_path,
    ):
        """Empty-SSOT fail-closed guard PRESERVED — in BOTH topologies — even
        with a premature inbox present. When the persisted SSOT team_name is
        empty, get_team_name short-circuits to "" (the deliberate fail-closed
        "team unknown → refuse" gate), the carve-out's `if not expected_team:
        return False` guard runs AHEAD of the witness, binding 5 returns False →
        the carve-out does NOT fire → the canonical secretary spawn is DENIED.
        The members-only witness change must not collide with this short-circuit.

        # COUNTER-TEST: this cell is FIX-INDEPENDENT — it asserts a DENY (not an
        # allow over a present inbox), so it stays GREEN under the source-only
        # revert. It pins that the members-only witness did not weaken the
        # empty-SSOT gate (a hypothetical regression where the witness recovered
        # a team from an empty SSOT would flip this to allow → RED).
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        for frame_sid, lead_sid in (
            (_SESSION_ID, _SESSION_ID),          # in-process (== leadSessionId)
            (_SESSION_ID, "other-lead-sid"),     # tmux (!= leadSessionId)
        ):
            _setup_carveout_team_with_lead_session(
                monkeypatch, tmp_path, team_name="",   # EMPTY SSOT
                lead_session_id=lead_sid, frame_session_id=frame_sid,
                seed_team_dir_name="t1",
            )
            # A premature inbox exists under the real team dir, but the empty SSOT
            # means get_team_name never resolves to it.
            _create_secretary_inbox(tmp_path, team_name="t1")

            result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
            assert result == _DENY_REASON, (
                f"empty SSOT must fail-closed even with a premature inbox "
                f"(frame_sid={frame_sid!r}, lead_sid={lead_sid!r})"
            )

    # --- CONTAINMENT (security-suggested): non-canonical input still blocked ---

    def test_non_canonical_input_still_blocked_with_pending_witness_error(
        self, monkeypatch, tmp_path,
    ):
        """CONTAINMENT: a NON-canonical tool call is STILL blocked even when a
        witness-read error is pending — proving bindings 1/2/3 (and the
        empty-SSOT guard) run BEFORE the join witness, so the broad-except
        error→ALLOW inversion is CONTAINED to the canonical secretary spawn and
        cannot leak into a blanket allow.

        The fix's _secretary_in_members returns False on a read error (the safe
        direction — it makes the carve-out FIRE). If that error→False were
        reached for a NON-canonical input, it would wrongly ALLOW it. This test
        proves it is not: bindings 1/2/3 short-circuit the predicate to False
        for any non-secretary tool BEFORE _secretary_in_members is ever called,
        so a pending witness error is irrelevant and the deny stands.

        Covers two non-canonical shapes while the witness data source is rigged
        to raise: (a) a plain Edit (fails binding 1 tool_name != Agent), and
        (b) an Agent with the wrong subagent_type/name (fails bindings 2/3).
        Both must DENY.

        # COUNTER-TEST: this cell is FIX-INDEPENDENT (asserts DENY, not an allow
        # over a present inbox) → GREEN under the source-only revert. It pins the
        # binding-ordering containment invariant, not the inbox-arm regression.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        # Rig the witness data source to RAISE — a pending witness error.
        def _boom(team_name, teams_dir=None):
            raise OSError("simulated witness read error")

        monkeypatch.setattr(ctx_module, "_iter_members", _boom)

        # (a) plain Edit — fails binding 1 (tool_name != Agent) → DENY,
        # the witness is never consulted.
        assert _check_tool_allowed(_make_input("Edit")) == _DENY_REASON

        # (b) Agent with wrong subagent_type → fails binding 2 → DENY.
        assert _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": "pact-architect"},
        )) == _DENY_REASON

        # (c) Agent with wrong name → fails binding 3 → DENY.
        assert _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": "secretari"},
        )) == _DENY_REASON


class TestSecretaryInMembersUnit:
    """U1: direct unit tests of the gate-local _secretary_in_members JOIN witness
    (#1023). The carve-out integration tests above key on the observable gate
    outcome; these pin the helper's own contract directly so a future refactor of
    the helper is caught at the unit level (closes the shared-helper-direct-unit-
    test gap)."""

    def test_true_when_secretary_in_members(self, monkeypatch, tmp_path):
        """members[] contains a 'secretary' name entry → True (join witness)."""
        from bootstrap_gate import _secretary_in_members

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1",
            members=[{"id": "sec-1", "name": "secretary", "type": "pact-secretary"}],
        )
        assert _secretary_in_members("t1") is True

    def test_false_when_members_empty(self, monkeypatch, tmp_path):
        """Empty members[] → False (the fresh-team precondition the carve-out
        handles). A premature inbox is IGNORED — this is a members-ONLY witness,
        the whole point of the #1023 decoupling.

        # COUNTER-TEST (source-only revert): the pre-fix carve-out called
        # _team_has_secretary (members[] OR inbox), so the equivalent member-only
        # query did not exist as a gate-local symbol; the import of
        # _secretary_in_members itself is the post-fix artifact. We additionally
        # seed a premature inbox to make explicit that the members-only witness
        # ignores it (it would return True if it read the inbox).
        """
        from bootstrap_gate import _secretary_in_members

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        _create_secretary_inbox(tmp_path, team_name="t1")  # ignored by a members-only witness
        assert _secretary_in_members("t1") is False

    def test_false_on_iter_members_raising(self, monkeypatch, tmp_path):
        """A raising _iter_members → broad-except returns False (totality /
        never-raises). This is the load-bearing safe direction: a False return
        makes binding 5's `not False` fire the carve-out (architect D-record:
        the Path.home() RuntimeError seam must not propagate to main()'s
        degraded-DENY and re-deadlock the spawn).

        # COUNTER-TEST: replacing the broad `except Exception` with the caller's
        # typed tuple (OSError, ValueError, KeyError, TypeError, AttributeError)
        # would let a RuntimeError (the Path.home seam) PROPAGATE → this call
        # would raise instead of returning False → RED. Pins the broad-except.
        """
        from bootstrap_gate import _secretary_in_members
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _boom(team_name, teams_dir=None):
            raise RuntimeError("unresolvable HOME seam")

        monkeypatch.setattr(ctx_module, "_iter_members", _boom)
        # Must NOT raise; must return False.
        assert _secretary_in_members("t1") is False


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
    - "degraded-warn-import": #942 warn-without-granting (defer/ask),
      import stage (direct _emit_degraded_warning invocation)
    - "degraded-warn-runtime": #942 warn-without-granting (defer/ask),
      runtime stage (gate-logic exception + allowlisted tool through main())

    All five MUST carry the audit anchor — parametrizing pins the
    invariant so no future emit path can be added without it. Mirrors
    bootstrap_marker_writer's test_every_emit_shape_carries_hook_event_name
    so all three bootstrap-related hooks share one parity contract.
    """

    @pytest.mark.parametrize("shape", [
        "deny-load-failure",
        "deny-runtime",
        "suppress",
        "degraded-warn-import",
        "degraded-warn-runtime",
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
        elif shape == "degraded-warn-import":
            from bootstrap_gate import _emit_degraded_warning
            with pytest.raises(SystemExit):
                _emit_degraded_warning("module imports", RuntimeError("x"), "Read")
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "degraded-warn-runtime":
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


def _break_shared_syntax(scaffold):
    """Canonical vector: shared/ exists but its __init__.py is syntax-broken."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "this is not valid python (", encoding="utf-8"
    )


def _break_shared_absent(scaffold):
    """shared/ package absent entirely (deleted / never installed)."""
    # Deliberately create nothing — `import shared.pact_context` raises
    # ModuleNotFoundError at module load.


def _break_shared_missing_transitive(scaffold):
    """shared/__init__.py parses fine but its body fails a from-import of a
    name that does not exist (the missing-transitive-dependency shape) —
    raises ImportError (NOT its ModuleNotFoundError subclass)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "from json import name_that_does_not_exist_in_json\n",
        encoding="utf-8",
    )


# Breakage-vector table for the degraded-import scaffold: vector name →
# (scaffold-breaker, exception type name the warning must carry). The
# production degraded region is `except BaseException`, so the defer/deny
# split must be IDENTICAL no matter HOW shared/ broke; the distinct
# exception type names make each vector's diagnosability assertable. The
# 2026-06-11 incident vector (py3.9 TypeError from annotation evaluation)
# is exercised separately by test_degraded_path_runs_on_python39_floor on
# a real 3.9 interpreter.
_BREAKAGE_VECTORS = {
    "syntax-broken-init": (_break_shared_syntax, "SyntaxError"),
    "shared-package-absent": (_break_shared_absent, "ModuleNotFoundError"),
    "missing-transitive-import": (
        _break_shared_missing_transitive, "ImportError",
    ),
}


def _run_degraded_subprocess(tmp_path, stdin_text, interpreter=None,
                             vector="syntax-broken-init"):
    """Run bootstrap_gate.py as a subprocess inside a scaffold whose
    `shared` package is deliberately broken per ``vector`` (a
    _BREAKAGE_VECTORS key; default = the canonical syntax-broken
    __init__.py), forcing the import-stage degraded path. Returns the
    CompletedProcess.

    ``interpreter`` defaults to the dev interpreter (sys.executable); the
    py3.9-floor test passes a discovered 3.9 binary to exercise the
    stdlib-only degraded region on the production system interpreter
    (GUI-launched macOS sessions run hooks on /usr/bin/python3 = 3.9.x).
    """
    import subprocess

    hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir(parents=True)
    (scaffold / "bootstrap_gate.py").write_text(
        hook_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _BREAKAGE_VECTORS[vector][0](scaffold)
    return subprocess.run(
        [interpreter or sys.executable, str(scaffold / "bootstrap_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
    )


def _find_python39():
    """Best-effort discovery of a Python 3.9 interpreter for the floor
    exercise: an explicit ``python3.9`` on PATH, else the macOS system
    ``/usr/bin/python3`` when it reports 3.9.x (the actual interpreter
    GUI-launched sessions run hooks on). Returns None when unavailable —
    callers skip; the static AST floor guard
    (test_py39_annotation_compat.py) remains the unconditional gate.
    """
    import shutil
    import subprocess

    candidates = [shutil.which("python3.9"), "/usr/bin/python3"]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if (probe.stdout + probe.stderr).strip().startswith("Python 3.9"):
            return candidate
    return None


# Canonical degraded-allowlist literal — deliberately independent of
# bootstrap_gate._READ_ONLY_TOOLS (same convention as
# _CANONICAL_DENY_REASON_LITERAL above): the parametrized subprocess matrix
# anchors on this literal so a member silently dropped from (or added to)
# the production set cannot shrink the matrix unnoticed. The set-equality +
# cardinality pin in TestDegradedMode is the two-site edit surface: any
# intentional allowlist change must update BOTH the production frozenset
# AND this literal, forcing the matrix to follow.
_DEGRADED_ALLOWLIST_LITERAL = (
    "AskUserQuestion",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "Read",
    "Skill",
    "TaskGet",
    "TaskList",
    "ToolSearch",
    "WebFetch",
    "WebSearch",
)

# Deny matrix: representatives of every non-member class — blocked mutating
# tools, the deliberate healthy/degraded asymmetry (Bash, mcp__*), task-
# mutation tools excluded from the read-only views, and an unknown/future
# name proving deny-by-default needs no enumeration.
_DEGRADED_DENY_MATRIX = (
    "Write",
    "Agent",
    "NotebookEdit",
    "Bash",
    "mcp__computer-use__key",
    "TaskCreate",
    "TaskUpdate",
    "SomeFutureTool",
)


class TestDegradedMode:
    """#942 degraded-mode handler: while the gate cannot evaluate (import
    or runtime failure), verified read-only tools are routed onward WITH a
    warning at exit 0 — permissionDecision "defer" (normal permission
    flow) for local tools, "ask" (explicit user approval) for outbound
    WebFetch/WebSearch, NEVER "allow" — so degraded mode is a
    permission-layer subset by construction. Everything else keeps the
    unchanged fail-closed deny at exit 2. Malformed/unverifiable stdin in
    the degraded path is fail-CLOSED (deny) — the opposite of the healthy
    path's input-side fail-open, because in degraded mode this module IS
    the broken layer.
    """

    # --- structural invariant pins (master safety property) -------------
    # MEMBERSHIP-CHANGE INVARIANT GUARD: the three tests below re-derive
    # the degraded⊆healthy safety property from the PRODUCTION sets
    # (_READ_ONLY_TOOLS, _DEGRADED_ASK_TOOLS, _BLOCKED_TOOLS) on every
    # run. They exist to catch FUTURE membership edits: any tool added to
    # the degraded allowlist that the healthy gate would deny — or any
    # ask-tool that is not an allowlist member — fails here before it can
    # ship. Do not weaken these to literal snapshots.

    def test_allowlist_disjoint_from_blocked_tools(self):
        """Membership invariant, part 1: no allowlist member is ever in
        the blocked set, and every ask-escalation tool is itself an
        allowlist member (ask is a refinement of membership, not a
        side-channel). Re-derived from production sets — guards future
        membership edits."""
        from bootstrap_gate import (
            _BLOCKED_TOOLS,
            _DEGRADED_ASK_TOOLS,
            _READ_ONLY_TOOLS,
        )

        assert _READ_ONLY_TOOLS & _BLOCKED_TOOLS == frozenset()
        assert _DEGRADED_ASK_TOOLS <= _READ_ONLY_TOOLS, (
            "_DEGRADED_ASK_TOOLS must be a subset of _READ_ONLY_TOOLS — an "
            "ask-tool outside the allowlist would never reach the ask arm "
            "(denied first), masking a dead or drifted entry"
        )

    def test_allowlist_excludes_bash_and_mcp(self):
        """Membership invariant, part 2 (deliberate strictness asymmetry):
        Bash and MCP tools are allowed on the healthy pre-marker path but
        must NOT be degraded-recognized. Re-derived from the production
        set — guards future membership edits."""
        from bootstrap_gate import _READ_ONLY_TOOLS

        assert "Bash" not in _READ_ONLY_TOOLS
        assert not any(t.startswith("mcp__") for t in _READ_ONLY_TOOLS)

    def test_every_allowlist_member_allowed_on_healthy_gated_branch(
        self, monkeypatch, tmp_path
    ):
        """Membership invariant, part 3 (degraded ⊆ healthy, empirical):
        on the strictest healthy branch (lead, no marker), every allowlist
        member is allowed by the REAL production gate. A future membership
        edit that adds a healthy-denied tool fails here — degraded mode
        must never route a tool the healthy gate denies onward to the
        permission flow."""
        from bootstrap_gate import _READ_ONLY_TOOLS, _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        for tool in sorted(_READ_ONLY_TOOLS):
            assert _check_tool_allowed(_make_input(tool)) is None, (
                f"allowlist member {tool!r} must be allowed on the healthy "
                f"lead+no-marker branch — degraded mode may never route "
                f"onward something the healthy gate denies"
            )

    # --- M1: bounded error interpolation ---------------------------------

    def test_degraded_warning_bounds_error_text(self, capsys):
        """Exception text interpolated into the context-bound warning is
        sanitized (control chars stripped) and truncated with an explicit
        marker; the stderr diagnostic channel keeps the full text."""
        from bootstrap_gate import _ERROR_TEXT_MAX, _emit_degraded_warning

        payload = "X" * 1000 + "\x07\x1b[31m\ninjected"
        with pytest.raises(SystemExit) as exc_info:
            _emit_degraded_warning("runtime", RuntimeError(payload), "Read")

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out.strip())
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        assert "...[truncated]" in reason
        # The embedded exception rendering is bounded: the 1000-char payload
        # must not appear in full in any context-bound field.
        assert "X" * (_ERROR_TEXT_MAX + 1) not in reason
        assert "X" * (_ERROR_TEXT_MAX + 1) not in out["hookSpecificOutput"]["additionalContext"]
        # Control / escape characters never reach the context-bound warning.
        assert "\x07" not in reason and "\x1b" not in reason
        # Full text still goes to stderr (debug channel).
        assert "X" * 999 in captured.err

    # --- runtime stage (in-process, symmetric with import stage) ---

    def test_runtime_exception_with_readonly_tool_defers_with_warning(self, capsys):
        """Gate-logic exception + local allowlisted tool → exit 0, JSON with
        permissionDecision="defer" (normal permission flow — never "allow"),
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
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "runtime" in hso[field]
            assert "RuntimeError" in hso[field]
            assert "DEGRADED" in hso[field]
        assert "systemMessage" in out

    def test_runtime_exception_with_outbound_tool_asks(self, capsys):
        """Gate-logic exception + outbound tool (WebFetch) → exit 0,
        permissionDecision="ask": network traffic under a broken gate
        escalates to explicit user approval rather than deferring."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "sys.stdin",
                io.StringIO(json.dumps(_make_input(tool_name="WebFetch"))),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out.strip())
        hso = out["hookSpecificOutput"]
        assert hso["permissionDecision"] == "ask"
        assert "DEGRADED" in hso["permissionDecisionReason"]
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

    def test_subprocess_broken_import_readonly_tool_defers(self, tmp_path):
        """Broken `shared` import + tool_name=Read → defer-with-warning,
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
        assert hso["permissionDecision"] == "defer"
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

    # --- comprehensive subprocess matrix (TEST phase) ---

    def test_allowlist_literal_matches_production_set(self):
        """Two-site edit pin: the parametrization literal and the production
        frozenset must stay in lockstep, at the architecturally-settled
        cardinality of 11. A drop OR an addition on either side fails here,
        so the subprocess matrix below can never silently under-cover the
        live allowlist (per-member parametrization, not per-container)."""
        from bootstrap_gate import _READ_ONLY_TOOLS

        assert len(_DEGRADED_ALLOWLIST_LITERAL) == 11
        assert len(set(_DEGRADED_ALLOWLIST_LITERAL)) == 11, (
            "literal must not contain duplicates — each matrix row must be "
            "a distinct member"
        )
        assert set(_DEGRADED_ALLOWLIST_LITERAL) == set(_READ_ONLY_TOOLS), (
            "allowlist literal drifted from bootstrap_gate._READ_ONLY_TOOLS "
            "— update BOTH sites (intentional change) or revert the "
            "production edit (accidental)"
        )

    @pytest.mark.parametrize("tool", _DEGRADED_ALLOWLIST_LITERAL)
    def test_subprocess_broken_import_full_allowlist_warns(self, tmp_path, tool):
        """T1 full matrix: EVERY allowlist member gets warn-without-granting
        from a real broken-import process — permissionDecision "defer" for
        local tools, "ask" for outbound WebFetch/WebSearch, NEVER "allow" —
        rc 0 (the emit contract: stdout JSON is only honored on exit 0),
        full emit shape pinned key-by-key, warning carries stage + exception
        type + the tool name, systemMessage present, stderr diagnostic
        non-empty. Expected decision is re-derived from the production
        _DEGRADED_ASK_TOOLS set so a membership edit moves this pin with it."""
        from bootstrap_gate import _DEGRADED_ASK_TOOLS

        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name=tool))
        )

        # Content asserts first; rc is asserted WITH content, never alone.
        out = json.loads(result.stdout.strip().splitlines()[0])
        assert set(out.keys()) == {"hookSpecificOutput", "systemMessage"}, (
            f"degraded-warn emit shape drifted for {tool!r}: {out.keys()!r}"
        )
        hso = out["hookSpecificOutput"]
        assert set(hso.keys()) == {
            "hookEventName",
            "permissionDecision",
            "permissionDecisionReason",
            "additionalContext",
        }
        assert hso["hookEventName"] == "PreToolUse"
        expected_decision = "ask" if tool in _DEGRADED_ASK_TOOLS else "defer"
        assert hso["permissionDecision"] == expected_decision, (
            f"{tool!r} must {expected_decision} under a degraded gate — and "
            f"never 'allow' (degraded mode is a permission-layer subset by "
            f"construction)"
        )
        assert hso["permissionDecision"] != "allow"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field], "stage must be named"
            assert "SyntaxError" in hso[field], (
                "exception type from the broken shared/__init__.py must be "
                "named so the warning is diagnosable"
            )
            assert f"'{tool}'" in hso[field], "allowed tool must be named"
        assert hso["permissionDecisionReason"] == hso["additionalContext"], (
            "both fields carry the SAME warning (docs ambiguity hedge)"
        )
        assert "degraded" in out["systemMessage"]
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert result.stderr.strip(), "stderr diagnostic line expected"

    @pytest.mark.parametrize("tool", _DEGRADED_DENY_MATRIX)
    def test_subprocess_broken_import_full_deny_matrix_denies(self, tmp_path, tool):
        """T2 full matrix: every non-member class — mutating tools, the
        Bash/mcp__ healthy-vs-degraded asymmetry, task-mutation tools, and
        an unknown/future name — takes the byte-shape of today's
        _emit_load_failure_deny at rc 2 with non-empty stderr. Deny is the
        default: nothing here requires enumerating 'the hookable set'."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name=tool))
        )

        out = json.loads(result.stdout.strip().splitlines()[0])
        # Byte-shape pin of the unchanged deny emitter: exactly one
        # top-level key, exactly three hookSpecificOutput keys.
        assert set(out.keys()) == {"hookSpecificOutput"}, (
            f"deny emit shape drifted for {tool!r}: {out.keys()!r}"
        )
        hso = out["hookSpecificOutput"]
        assert set(hso.keys()) == {
            "hookEventName",
            "permissionDecision",
            "permissionDecisionReason",
        }
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports failure" in hso["permissionDecisionReason"]
        assert "blocking for safety" in hso["permissionDecisionReason"]
        assert "systemMessage" not in out, (
            "error/suppress-style exclusivity: the deny arm never carries "
            "the degraded-warn banner"
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert result.stderr.strip()

    @pytest.mark.parametrize("label,stdin_text", [
        ("missing-tool-name", json.dumps({
            k: v for k, v in _make_input().items() if k != "tool_name"
        })),
        ("null-tool-name", json.dumps(dict(_make_input(), tool_name=None))),
        ("int-tool-name", json.dumps(dict(_make_input(), tool_name=123))),
        ("list-tool-name", json.dumps(dict(_make_input(), tool_name=["Read"]))),
        ("empty-string-tool-name", json.dumps(dict(_make_input(), tool_name=""))),
        ("empty-stdin", ""),
        ("non-dict-frame-list", json.dumps([1, 2, 3])),
        ("non-dict-frame-bare-string", json.dumps("Read")),
    ])
    def test_subprocess_broken_import_unverifiable_stdin_denies(
        self, tmp_path, label, stdin_text
    ):
        """T3 full matrix: every unverifiable-stdin class — absent, null,
        non-string, empty tool_name; empty stdin; valid-JSON non-dict
        frames (including a bare string "Read", which must NOT be read as
        a tool name) — is fail-CLOSED deny at rc 2. The degraded path
        inverts the healthy input-side fail-open because in degraded mode
        this module IS the broken layer."""
        result = _run_degraded_subprocess(tmp_path, stdin_text)

        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny", (
            f"stdin class {label!r} must deny fail-closed"
        )
        assert "module imports failure" in hso["permissionDecisionReason"]
        assert "systemMessage" not in out
        assert result.returncode == 2, (
            f"{label}: stderr={result.stderr!r} stdout={result.stdout!r}"
        )

    # --- breakage-vector agnosticism (beyond the canonical SyntaxError) ---

    @pytest.mark.parametrize("vector", sorted(_BREAKAGE_VECTORS))
    def test_subprocess_breakage_vectors_readonly_defers(self, tmp_path, vector):
        """The degraded region catches BaseException, so the warn arm must
        behave IDENTICALLY no matter HOW shared/ broke: syntax-broken
        __init__.py (SyntaxError), package absent (ModuleNotFoundError),
        or a failing from-import inside an otherwise-parseable __init__.py
        (ImportError — the missing-transitive-dependency shape). Read
        defers with the vector's exception type named in the warning
        (diagnosability), rc 0 alongside content."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name="Read")), vector=vector
        )

        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer", (
            f"vector {vector!r} must take the same defer arm as the "
            f"canonical syntax vector"
        )
        expected_exc = _BREAKAGE_VECTORS[vector][1]
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field], "stage must be named"
            assert expected_exc in hso[field], (
                f"vector {vector!r} must name its exception type "
                f"({expected_exc}) so the warning is diagnosable"
            )
        assert "systemMessage" in out
        assert result.returncode == 0, (
            f"{vector}: stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert result.stderr.strip(), "stderr diagnostic line expected"

    @pytest.mark.parametrize("vector", sorted(_BREAKAGE_VECTORS))
    def test_subprocess_breakage_vectors_mutating_denies(self, tmp_path, vector):
        """Deny-arm twin of the vector matrix: Edit takes the unchanged
        fail-closed deny at rc 2 under EVERY breakage vector, with the
        vector's exception type named in the deny reason."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), vector=vector
        )

        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny", (
            f"vector {vector!r} must keep the fail-closed deny arm"
        )
        assert "module imports failure" in hso["permissionDecisionReason"]
        assert _BREAKAGE_VECTORS[vector][1] in hso["permissionDecisionReason"]
        assert "systemMessage" not in out
        assert result.returncode == 2, (
            f"{vector}: stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert result.stderr.strip()

    # --- py3.9 floor exercise (conditional on an available interpreter) ---

    def test_degraded_path_runs_on_python39_floor(self, tmp_path):
        """The degraded region is stdlib-only and must execute on the
        production system interpreter (GUI-launched macOS sessions run
        hooks on /usr/bin/python3 = 3.9.x). Exercise the three behavior
        classes — warn(defer), deny, fail-closed-stdin — under a REAL 3.9
        interpreter when one is discoverable; the static AST floor guard
        (test_py39_annotation_compat.py R0–R3) remains the unconditional
        merge gate when none is."""
        py39 = _find_python39()
        if py39 is None:
            pytest.skip(
                "no Python 3.9 interpreter discoverable (python3.9 on PATH "
                "or /usr/bin/python3 reporting 3.9.x); static floor guard "
                "test_py39_annotation_compat.py covers the syntax floor"
            )

        warn = _run_degraded_subprocess(
            tmp_path / "warn", json.dumps(_make_input(tool_name="Read")),
            interpreter=py39,
        )
        out = json.loads(warn.stdout.strip().splitlines()[0])
        assert out["hookSpecificOutput"]["permissionDecision"] == "defer"
        assert "DEGRADED" in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert warn.returncode == 0, (
            f"stderr={warn.stderr!r} stdout={warn.stdout!r}"
        )

        deny = _run_degraded_subprocess(
            tmp_path / "deny", json.dumps(_make_input(tool_name="Edit")),
            interpreter=py39,
        )
        out = json.loads(deny.stdout.strip().splitlines()[0])
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert deny.returncode == 2

        malformed = _run_degraded_subprocess(
            tmp_path / "malformed", "not valid json {", interpreter=py39,
        )
        out = json.loads(malformed.stdout.strip().splitlines()[0])
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert malformed.returncode == 2


# =============================================================================
# Hostile-__str__ crash path — every error-text render must fall back
# =============================================================================


def _break_shared_hostile_str(scaffold):
    """shared/__init__.py raises an exception whose own __str__ raises —
    the hostile-renderer shape: rendering an exception message runs
    arbitrary exception-class code, so every interpolation of the caught
    error on the crash path must fall back instead of letting the render
    raise. Deliberately NOT in _BREAKAGE_VECTORS: the matrix asserts the
    exception type is named in the DENY reason, which the raise-proof
    constant fallback (no type prefix) intentionally does not satisfy."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class HostileStrError(Exception):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile __str__')\n"
        "raise HostileStrError()\n",
        encoding="utf-8",
    )


class TestHostileStrCrashPath:

    def _run(self, tmp_path, stdin_text):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _break_shared_hostile_str(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    def test_mutating_deny_json_intact_under_hostile_str(self, tmp_path):
        """Hostile __str__ must not suppress the deny: an unguarded render
        raising before the deny print would exit nonzero-non-2 — a
        non-blocking PreToolUse error, so the tool call would PROCEED
        (fail-open). The deny JSON must print with the raise-proof
        constant in the reason and the exit-2 blocking path intact."""
        result = self._run(tmp_path, json.dumps(_make_input(tool_name="Edit")))
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        reason = hso["permissionDecisionReason"]
        assert "module imports" in reason
        assert "HostileStrError: <exception str() raised>" in reason, (
            f"deny reason must name the hostile exception type via the bounded "
            f"renderer (parity with the degraded path): {reason!r}"
        )
        # Guarded stderr full-text line: placeholder, exit-2 preserved.
        assert "Hook load error (bootstrap_gate / module imports)" in (
            result.stderr
        )
        assert "<exception str() raised>" in result.stderr

    def test_readonly_defer_intact_under_hostile_str(self, tmp_path):
        """Degraded warn arm under hostile __str__: the bounded renderer
        falls back to the type-prefixed placeholder inside the warning
        (diagnosability preserved — the type name still appears), the
        defer decision and exit 0 stay intact, and the guarded stderr
        line carries the placeholder instead of voiding the decision
        with a nonzero exit."""
        result = self._run(tmp_path, json.dumps(_make_input(tool_name="Read")))
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            assert "HostileStrError: <exception str() raised>" in hso[field], (
                f"warning must carry the type-prefixed placeholder: "
                f"{hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )
        assert "<exception str() raised>" in result.stderr


# =============================================================================
# Cycle-2 regression: hostile-metaclass __name__ on the crash path.
#
# A metaclass can make type(error).__name__ a property that RAISES. The
# degraded warn path (_emit_degraded_warning) renders the error inline via
# _bounded_error_text with NO constant fallback around the call — so before
# 7155516d the helper's own fallback re-accessed type(error).__name__ and
# re-raised, the warn path exited 1 (a PreToolUse non-blocking error that
# SUPPRESSED the warning), and the degraded gate silently failed open. The
# fix captures the type name once → literal "exception". These pins lock
# the restored exit-0 defer; counter-test: source-revert 7155516d → the
# defer leg regresses to exit 1.
#
# HAZARD: the breakage modules define the hostile metaclass entirely in the
# subprocess scaffold text — no in-process class whose __name__ pytest
# could bomb during collection.
# =============================================================================


def _break_shared_hostile_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass makes
    __name__ a raising property (normal __str__). Rendering the caught
    error's type name runs metaclass code that raises."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _HostileNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        raise RuntimeError('hostile __name__')\n"
        "class NameBomb(Exception, metaclass=_HostileNameMeta):\n"
        "    pass\n"
        "raise NameBomb('boom')\n",
        encoding="utf-8",
    )


def _break_shared_hostile_name_and_str(scaffold):
    """Both hostile: metaclass __name__ raises AND __str__ raises — both
    helper fallbacks fire (type name → 'exception', message → marker)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _HostileNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        raise RuntimeError('hostile __name__')\n"
        "class BothBomb(Exception, metaclass=_HostileNameMeta):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile __str__')\n"
        "raise BothBomb('boom')\n",
        encoding="utf-8",
    )


class TestHostileNameCrashPath:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker",
        [_break_shared_hostile_name, _break_shared_hostile_name_and_str],
        ids=["hostile-name", "hostile-name-and-str"],
    )
    def test_readonly_defer_intact_under_hostile_name(self, tmp_path, breaker):
        """The regression pin: a read-only tool under a hostile-__name__
        (or both-hostile) module crash must take the defer arm at exit 0,
        decision JSON intact, with the "exception" type-name fallback in
        the warning. Pre-7155516d this exited 1 with the warning
        suppressed."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "hostile __name__ must not regress the degraded path to a "
            f"nonzero exit (the suppressed-warning bug): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            # Type-name fallback fired: the literal "exception", never a
            # real class name (the metaclass made the name unrenderable).
            assert "failure — exception:" in hso[field], (
                f"warning must carry the captured-name fallback: "
                f"{hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )

    @pytest.mark.parametrize(
        "breaker",
        [_break_shared_hostile_name, _break_shared_hostile_name_and_str],
        ids=["hostile-name", "hostile-name-and-str"],
    )
    def test_mutating_deny_intact_under_hostile_name(self, tmp_path, breaker):
        """Deny-arm twin: a mutating tool under hostile __name__ still
        denies at exit 2 (blocking). The deny render now routes through
        _bounded_error_text (item 1: collapse the distinct render site into
        the shared helper for M1-consistency), which is total — so under
        hostile __name__ the reason carries the helper's "exception:"
        type-name fallback (parity with the degraded arm), not the old
        call-site "<error text unavailable>" constant. Deny still exits 2."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        expected_render = (
            "exception: boom"
            if breaker is _break_shared_hostile_name
            else "exception: <exception str() raised>"
        )
        assert expected_render in hso["permissionDecisionReason"], (
            f"deny reason must carry the bounded-helper fallback (hostile "
            f"__name__ → 'exception:' type-name fallback), parity with the "
            f"degraded arm: {hso['permissionDecisionReason']!r}"
        )


# =============================================================================
# Cycle-3 regression: __name__ that RETURNS (not raises) a hostile non-str.
#
# The cycle-2 fix (7155516d) guards a metaclass __name__ that RAISES. A
# metaclass __name__ can instead RETURN a non-str object whose own
# __format__/__str__ raises — that poisoned value defeats BOTH f-string
# branches of _bounded_error_text (the except-branch fallback re-interpolates
# the same type_name), so before b6e9125a the degraded warn path raised out of
# the renderer and exited 1: a PreToolUse non-blocking error that SUPPRESSED
# the warning, silently failing open. b6e9125a coerces a non-str type_name to
# the literal "exception" (isinstance guard). These pins lock the restored
# exit-0 defer / exit-2 deny under the returns-hostile shape; counter-test:
# source-revert b6e9125a → the defer leg regresses to exit 1.
#
# HAZARD: the hostile metaclass lives entirely in the subprocess scaffold
# text — no in-process class whose __name__ pytest could bomb on collection.
# =============================================================================


def _break_shared_hostile_format_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a non-str object whose __format__ (and __str__) raise. Rendering the
    caught error's type name interpolates that poisoned value → raises unless
    the renderer coerces it to a str first (b6e9125a)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _FormatBomb:\n"
        "    def __format__(self, spec):\n"
        "        raise RuntimeError('hostile __format__')\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile __str__')\n"
        "class _HostileFormatNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return _FormatBomb()\n"
        "class FormatNameBomb(Exception, metaclass=_HostileFormatNameMeta):\n"
        "    pass\n"
        "raise FormatNameBomb('boom')\n",
        encoding="utf-8",
    )


def _break_shared_hostile_int_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a non-str int. The int interpolates without raising, but the type name is
    nonsense ('42: ...'); b6e9125a coerces it to 'exception' so the degraded
    warning carries the same diagnosable fallback as the raising case."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _IntNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return 42\n"
        "class IntNameBomb(Exception, metaclass=_IntNameMeta):\n"
        "    pass\n"
        "raise IntNameBomb('boom')\n",
        encoding="utf-8",
    )


class TestHostileNameReturnCrashPath:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker",
        [_break_shared_hostile_format_name, _break_shared_hostile_int_name],
        ids=["name-returns-format-bomb", "name-returns-int"],
    )
    def test_readonly_defer_intact_under_hostile_name_return(
        self, tmp_path, breaker,
    ):
        """The cycle-3 regression pin: a read-only tool under a module crash
        whose __name__ RETURNS a hostile non-str must take the defer arm at
        exit 0, decision JSON intact, with the "exception" type-name fallback
        in the warning. Pre-b6e9125a the format-bomb variant exited 1 with the
        warning suppressed (the renderer raised out of both f-string
        branches)."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "hostile __name__ RETURN must not regress the degraded path to a "
            f"nonzero exit (the suppressed-warning fail-open): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            # Coerced fallback fired: the literal "exception", never a raw
            # non-str type name ("42:") or a render crash.
            assert "failure — exception:" in hso[field], (
                f"warning must carry the coerced-name fallback: {hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )

    @pytest.mark.parametrize(
        "breaker",
        [_break_shared_hostile_format_name, _break_shared_hostile_int_name],
        ids=["name-returns-format-bomb", "name-returns-int"],
    )
    def test_mutating_deny_intact_under_hostile_name_return(
        self, tmp_path, breaker,
    ):
        """Deny-arm twin: a mutating tool under a __name__-returns-hostile
        module crash still denies at exit 2 (blocking). The deny render has
        its own guard (constant fallback on the format-bomb; the int renders
        cleanly), so the blocking exit-2 path stays intact either way."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports" in hso["permissionDecisionReason"]


# =============================================================================
# BaseException-breadth regression (symmetric with the lifecycle-gate pin): a
# module-level sys.exit() / KeyboardInterrupt AT IMPORT is a BaseException that
# is NOT an Exception subclass. The import-gauntlet guard (`except
# BaseException`) is deliberately broad so such a vector is caught and routed
# to the degraded decision (read-only → defer exit 0; mutating → deny exit 2).
# None of the _BREAKAGE_VECTORS exercises this (all raise Exception
# subclasses), so narrowing the guard to `except Exception` would silently
# fail open: SystemExit/KeyboardInterrupt escapes → exit 1, which for
# PreToolUse is a non-blocking error → the tool PROCEEDS (mutating) or the
# defer decision is lost (read-only). These pins lock the breadth.
#
# Counter-test: narrow the gauntlet guard to `except Exception` → the crash
# escapes, no decision JSON prints, exit 1 → both arms below fail.
# =============================================================================


def _break_shared_sys_exit(scaffold):
    """shared/__init__.py calls sys.exit(1) at import → SystemExit (a
    BaseException, NOT an Exception) propagates out of the gauntlet import."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "import sys\nsys.exit(1)\n", encoding="utf-8"
    )


def _break_shared_keyboard_interrupt(scaffold):
    """shared/__init__.py raises KeyboardInterrupt at import → a BaseException
    that `except Exception` would NOT catch."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "raise KeyboardInterrupt('simulated at import')\n", encoding="utf-8"
    )


class TestBaseExceptionBreadthAtImport:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker,expected_exc",
        [
            (_break_shared_sys_exit, "SystemExit"),
            (_break_shared_keyboard_interrupt, "KeyboardInterrupt"),
        ],
        ids=["import-sys-exit", "import-keyboard-interrupt"],
    )
    def test_readonly_defer_intact_under_non_exception_baseexception(
        self, tmp_path, breaker, expected_exc,
    ):
        """A read-only tool under a module-level sys.exit()/KeyboardInterrupt
        at import takes the defer arm at exit 0 (the gauntlet's `except
        BaseException` catches the non-Exception BaseException). Narrowing to
        `except Exception` re-masks: the crash escapes → exit 1, defer lost."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "a non-Exception BaseException at import must be caught by the "
            f"gauntlet's BaseException breadth (defer exit 0): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        assert "DEGRADED" in hso["permissionDecisionReason"]
        assert "module imports" in hso["permissionDecisionReason"]
        assert expected_exc in hso["permissionDecisionReason"], (
            f"degraded warning must name the BaseException type "
            f"({expected_exc}): {hso['permissionDecisionReason']!r}"
        )

    @pytest.mark.parametrize(
        "breaker",
        [_break_shared_sys_exit, _break_shared_keyboard_interrupt],
        ids=["import-sys-exit", "import-keyboard-interrupt"],
    )
    def test_mutating_deny_intact_under_non_exception_baseexception(
        self, tmp_path, breaker,
    ):
        """A mutating tool under the same non-Exception BaseException at import
        still denies at exit 2 (blocking). Narrowing the gauntlet to `except
        Exception` lets the crash escape → exit 1, a non-blocking PreToolUse
        error → the mutating tool would PROCEED (fail-open)."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports" in hso["permissionDecisionReason"]


# =============================================================================
# Cycle-4 regression: __name__ that returns a str SUBCLASS whose formatting
# hooks raise. isinstance(str_subclass, str) is True, so the cycle-3 isinstance
# guard waved it through and _bounded_error_text raised out of both f-string
# branches (str.__format__'s empty-spec path delegates to the overridden
# __str__). cede8629 uses an EXACT-type guard (type(...) is str) that rejects
# str subclasses too, collapsing the prefix to "exception". These pins lock the
# restored exit-0 defer / exit-2 deny; counter-test: revert cede8629 (restore
# isinstance) → the defer leg raises out of the renderer → exit 1 (fail-open).
#
# HAZARD: the hostile str subclass lives entirely in the subprocess scaffold
# text — no in-process class whose __name__ pytest could bomb on collection.
# =============================================================================


def _break_shared_str_subclass_format_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a str SUBCLASS instance whose __format__ raises."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _FmtStr(str):\n"
        "    def __format__(self, spec):\n"
        "        raise RuntimeError('hostile str-subclass __format__')\n"
        "class _FmtStrNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return _FmtStr('HostileFmtName')\n"
        "class FmtStrNameBomb(Exception, metaclass=_FmtStrNameMeta):\n"
        "    pass\n"
        "raise FmtStrNameBomb('boom')\n",
        encoding="utf-8",
    )


def _break_shared_str_subclass_str_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a str SUBCLASS instance whose __str__ raises (str.__format__'s empty-spec
    delegation to __str__ makes the f-string interpolation raise)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _StrStr(str):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile str-subclass __str__')\n"
        "class _StrStrNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return _StrStr('HostileStrName')\n"
        "class StrStrNameBomb(Exception, metaclass=_StrStrNameMeta):\n"
        "    pass\n"
        "raise StrStrNameBomb('boom')\n",
        encoding="utf-8",
    )


class TestStrSubclassNameReturnCrashPath:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker",
        [
            _break_shared_str_subclass_format_name,
            _break_shared_str_subclass_str_name,
        ],
        ids=["name-returns-str-subclass-format-bomb",
             "name-returns-str-subclass-str-bomb"],
    )
    def test_readonly_defer_intact_under_str_subclass_name(
        self, tmp_path, breaker,
    ):
        """The cycle-4 regression pin: a read-only tool under a module crash
        whose __name__ RETURNS a hostile str SUBCLASS must take the defer arm
        at exit 0, decision JSON intact, with the "exception" fallback in the
        warning. Pre-cede8629 (isinstance guard) the str subclass passed the
        guard and the renderer raised → exit 1, warning suppressed
        (fail-open)."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "a str-subclass __name__ must not regress the degraded path to a "
            f"nonzero exit (the suppressed-warning fail-open): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            assert "failure — exception:" in hso[field], (
                f"warning must carry the exact-type-coerced fallback: "
                f"{hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )

    @pytest.mark.parametrize(
        "breaker",
        [
            _break_shared_str_subclass_format_name,
            _break_shared_str_subclass_str_name,
        ],
        ids=["name-returns-str-subclass-format-bomb",
             "name-returns-str-subclass-str-bomb"],
    )
    def test_mutating_deny_intact_under_str_subclass_name(
        self, tmp_path, breaker,
    ):
        """Deny-arm twin: a mutating tool under a str-subclass-__name__ module
        crash still denies at exit 2 (blocking). The deny render routes
        through the total _bounded_error_text (item 1), so a str-subclass
        __name__ that bombs its own render is coerced to the "exception"
        type-name fallback rather than raising — the blocking exit-2 path
        stays intact."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports" in hso["permissionDecisionReason"]


# =============================================================================
# Marker verification × CLAUDE_PLUGIN_ROOT env fallback — healthy path
# =============================================================================


class TestMarkerVerifyEnvFallback:
    """is_marker_set derives plugin_root via pact_context.get_plugin_root(),
    which falls back to the CLAUDE_PLUGIN_ROOT env var when the context-file
    value is empty or the file is missing. These tests pin that the
    fallback participates in MARKER VERIFICATION on the healthy path — the
    realistic shape being a context file healed (or written) while
    CLAUDE_PLUGIN_ROOT was absent, leaving plugin_root='' on disk while the
    env var is exported into every subsequent hook process.

    Three arms: (1) env rescues verification — a validly-signed marker
    verifies with the context plugin_root unavailable; (2) both sources
    absent fails closed (the conftest autouse scrub guarantees the env
    baseline); (3) a WRONG env root fails the SIGNATURE — proving the env
    value participates in the HMAC input, not merely the non-empty check.
    """

    def _scaffold(self, monkeypatch, tmp_path, context_state):
        """Session dir + plugin root (plugin.json v9.9.9) + validly-signed
        marker; the context file is per ``context_state``: 'absent' (never
        written) or 'empty-plugin-root' (present, plugin_root='')."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
        session_dir.mkdir(parents=True)

        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": "9.9.9"}), encoding="utf-8"
        )
        _write_f24_marker(session_dir, plugin_root)

        context_file = session_dir / "pact-session-context.json"
        if context_state == "empty-plugin-root":
            context_file.write_text(json.dumps({
                "team_name": "",
                "session_id": _SESSION_ID,
                "project_dir": _PROJECT_DIR,
                "plugin_root": "",
                "started_at": "2026-01-01T00:00:00Z",
            }), encoding="utf-8")
        # 'absent': deliberately not written.

        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        monkeypatch.setattr(ctx_module, "_cache", None)
        return session_dir, plugin_root

    @pytest.mark.parametrize("context_state", ["absent", "empty-plugin-root"])
    def test_env_fallback_rescues_marker_verification(
            self, monkeypatch, tmp_path, context_state):
        """Context plugin_root unavailable + CLAUDE_PLUGIN_ROOT exported →
        the validly-signed marker verifies via the env-derived root."""
        from bootstrap_gate import is_marker_set

        session_dir, plugin_root = self._scaffold(
            monkeypatch, tmp_path, context_state)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        assert is_marker_set(session_dir) is True

    @pytest.mark.parametrize("context_state", ["absent", "empty-plugin-root"])
    def test_both_sources_absent_fails_closed(
            self, monkeypatch, tmp_path, context_state):
        """Same scaffold, env NOT set (conftest scrub guarantees the unset
        baseline) → plugin_root resolves '' → marker verification fails
        closed, single-variable counterpart of the rescue arm above."""
        from bootstrap_gate import is_marker_set

        session_dir, _ = self._scaffold(monkeypatch, tmp_path, context_state)

        assert is_marker_set(session_dir) is False

    def test_wrong_env_root_fails_signature(self, monkeypatch, tmp_path):
        """Env pointing at a DIFFERENT root (own valid plugin.json, SAME
        version, so only the root path differs in the HMAC input) → the
        signature mismatch rejects the marker — the env value participates
        in verification, it does not merely satisfy the non-empty check."""
        from bootstrap_gate import is_marker_set

        session_dir, _ = self._scaffold(monkeypatch, tmp_path, "absent")
        other_root = tmp_path / "other-plugin"
        (other_root / ".claude-plugin").mkdir(parents=True)
        (other_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": "9.9.9"}), encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(other_root))

        assert is_marker_set(session_dir) is False

    def test_empty_plugin_root_context_marker_fast_path_via_env(
            self, monkeypatch, tmp_path):
        """Integration through the real gate: context file PRESENT with
        plugin_root='' (the heal-without-env shape) + env exported → the
        marker fast path allows a normally-blocked tool; with the env
        absent the SAME scaffold denies — the discriminating pair pins
        that the env fallback is what carries the fast path."""
        from bootstrap_gate import _check_tool_allowed

        session_dir, plugin_root = self._scaffold(
            monkeypatch, tmp_path, "empty-plugin-root")

        # Without the env var (scrubbed baseline): marker unverifiable →
        # lead+no-marker branch → Edit denied.
        assert _check_tool_allowed(_make_input("Edit")) is not None

        # With the env var: marker verifies → fast path allows Edit.
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
        assert _check_tool_allowed(_make_input("Edit")) is None


# =============================================================================
# Import discipline — #789 reciprocal-cycle defense
# =============================================================================


class TestImportDiscipline:
    """Structural pin (#1023): bootstrap_gate.py MUST NOT import
    bootstrap_marker_writer in ANY scope — module-load OR function-local.

    Pre-#1023 the carve-out's binding 5 called
    bootstrap_marker_writer._team_has_secretary via a LOCAL import (inside
    _is_canonical_secretary_spawn) to break a reciprocal cycle:
    bootstrap_marker_writer imports is_marker_set from THIS module at its
    own top level, so a reciprocal top-level import here would deadlock
    module load. #1023 decoupled the carve-out onto a gate-local
    members[]-only JOIN witness (_secretary_in_members, reading
    shared.pact_context._iter_members), so the gate no longer references
    bootstrap_marker_writer AT ALL. That is a STRICTLY STRONGER invariant
    than the old local-import-only rule: with zero gate→marker_writer edges
    the cycle cannot exist in any form. This pin enforces the stronger
    invariant so a future refactor can't re-introduce ANY gate→marker_writer
    import (local OR module-scope) and re-open the cycle / re-couple the
    carve-out to the DISPATCH witness that caused the #1023 deadlock.
    """

    def test_no_bootstrap_marker_writer_import_in_any_scope(self):
        """No reference to ``bootstrap_marker_writer`` ANYWHERE in
        bootstrap_gate.py — not an Import / ImportFrom statement (module or
        function scope), nor a dynamic ``__import__`` /
        ``importlib.import_module`` call. The whole AST is walked (no
        function/class boundary stop) because, post-#1023, even a LOCAL
        import is forbidden: the carve-out's JOIN witness reads
        pact_context._iter_members directly and never crosses into
        bootstrap_marker_writer.

        AST-based (not a source grep) so it catches every import idiom,
        including a dynamic ``_bmw = __import__('bootstrap_marker_writer')``
        that a string-prefix grep would miss.
        """
        import ast

        gate_path = (
            Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        )
        source = gate_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(gate_path))

        target = "bootstrap_marker_writer"

        for node in ast.walk(tree):
            # `import bootstrap_marker_writer` / `... as bm`
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == target or alias.name.endswith(f".{target}"):
                        pytest.fail(
                            f"bootstrap_gate.py has `import {alias.name}` at "
                            f"line {node.lineno}. Post-#1023 the gate must NOT "
                            f"import bootstrap_marker_writer in ANY scope — the "
                            f"carve-out reads pact_context._iter_members "
                            f"directly via the gate-local _secretary_in_members "
                            f"JOIN witness."
                        )
            # `from bootstrap_marker_writer import ...` / relative form
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                leaf = module.split(".")[-1] if module else ""
                if module == target or leaf == target:
                    pytest.fail(
                        f"bootstrap_gate.py has `from {module} import ...` at "
                        f"line {node.lineno}. Post-#1023 the gate must NOT "
                        f"import bootstrap_marker_writer in ANY scope — the "
                        f"carve-out reads pact_context._iter_members directly "
                        f"via the gate-local _secretary_in_members JOIN witness."
                    )
            # `__import__('bootstrap_marker_writer')` /
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
                            f"bootstrap_gate.py has "
                            f"`{call_name}({first_arg.value!r})` at line "
                            f"{node.lineno}. Post-#1023 the gate must NOT "
                            f"import bootstrap_marker_writer in ANY scope."
                        )


class TestCanonicalSecretaryConstantPin:
    """Structural pin: the canonical-secretary `name` literal MUST match
    byte-for-byte across the carve-out's 3-way mirror surface
    (bootstrap_gate.py `_SECRETARY_NAME`, bootstrap_marker_writer.py
    `_SECRETARY_NAME`, and the canonical `name="secretary"` literal in
    commands/bootstrap.md Step 2).

    Drift between any pair silently breaks the carve-out's binding (5):
    `NOT _secretary_in_members(team_name)` (#1023). The gate's JOIN witness
    compares `member.get("name")` to the gate's `_SECRETARY_NAME`; if that
    literal diverged from the one the orchestrator-emitted spawn actually
    writes into members[] (mirrored from marker_writer's `_SECRETARY_NAME` and
    the canonical `name="secretary"` in bootstrap.md), `_secretary_in_members`
    would return False forever — the carve-out would stay open and re-fire on
    every subsequent canonical spawn, re-introducing the brittleness BE-F1
    flagged in PR #790 review. (The 3-way mirror still binds all three
    surfaces; #1023 only moved the gate-side consumer from _team_has_secretary
    to _secretary_in_members, both reading the same _SECRETARY_NAME constant.)
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
            f"binding-5 semantic will break in production if these diverge — "
            f"_secretary_in_members returns False forever, carve-out stays open."
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
        ["subagent_type", "name"],
    )
    def test_tool_input_missing_required_key_denies(
        self, monkeypatch, tmp_path, missing_key,
    ):
        """tool_input missing one of (subagent_type, name) → predicate
        returns False → deny. (#979: team_name dropped from the binding set —
        the Agent(team_name=) arg is platform-ignored.)

        `.get(missing_key)` returns None, which compares unequal to the
        expected literal value. Mental revert: replacing
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
        ],
    )
    def test_wrong_value_type_on_binding_denies(
        self, monkeypatch, tmp_path, binding, wrong_type_value,
    ):
        """Wrong value TYPE on a binding (int/None/list/dict where str
        is expected) → != comparison against the string constant → deny.
        (#979: team_name removed from the binding set — its type is no
        longer checked since the arg is platform-ignored.)

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

    # --- Witness exception totality (#1023) --------------------------------
    # Post-#1023 the JOIN witness _secretary_in_members wraps its body in a
    # BROAD `except Exception: return False`, so EVERY exception type raised
    # while reading members[] is absorbed at the witness → binding 5
    # (`not False`) → carve-out FIRES (allow). This replaces the pre-#1023
    # typed-except design, where the 5 "listed" types denied and "unlisted"
    # types (RuntimeError etc.) propagated to main()'s load-failure deny. The
    # broad except is LOAD-BEARING: a Path.home() RuntimeError seam in the
    # members[] read would otherwise escape and re-deadlock the spawn.

    @pytest.mark.parametrize(
        "exc_type",
        # Spans BOTH the old "listed" set AND the old "unlisted" set — all are
        # now uniformly caught by the witness's broad except.
        [OSError, ValueError, KeyError, TypeError, AttributeError,
         RuntimeError, MemoryError, NotImplementedError, AssertionError],
        ids=lambda e: e.__name__,
    )
    def test_witness_exception_caught_and_carve_out_fires(
        self, monkeypatch, tmp_path, exc_type,
    ):
        """ANY exception raised while reading members[] is CAUGHT by
        _secretary_in_members's broad except → witness False → binding 5
        (`not False`) → carve-out fires → ALLOW (result is None).

        We monkeypatch pact_context._iter_members (the witness's data source)
        to raise, exercising the witness's broad-except arm end-to-end — not
        stubbing the witness, which would bypass the except being proven. The
        broad except is uniform: there is no longer a "listed vs unlisted"
        split, and no exception propagates to main()'s load-failure deny path.
        """
        from bootstrap_gate import _check_tool_allowed
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _raiser(team_name, teams_dir=None):
            raise exc_type("simulated")

        monkeypatch.setattr(ctx_module, "_iter_members", _raiser)
        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    def test_witness_never_propagates_out_of_predicate(
        self, monkeypatch, tmp_path,
    ):
        """_is_canonical_secretary_spawn NEVER raises on a witness read
        error — totality (#989). The witness's broad except absorbs the
        exception and returns False, so the predicate returns True (carve-out
        fires) without propagating. Pinned via a direct predicate call (no
        pytest.raises) to prove non-propagation at the predicate boundary.

        Mental revert: narrowing _secretary_in_members' except to a typed
        tuple would let a Path.home() RuntimeError (absent from the tuple)
        escape here, propagate to main(), and re-deadlock the secretary spawn
        — the exact #1023 failure mode the broad except prevents.
        """
        from bootstrap_gate import _is_canonical_secretary_spawn
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _raiser(team_name, teams_dir=None):
            raise RuntimeError("Path.home() seam — unresolvable HOME")

        monkeypatch.setattr(ctx_module, "_iter_members", _raiser)

        # No pytest.raises: the predicate must NOT raise. The witness swallows
        # the RuntimeError → False → binding 5 (`not False`) → True.
        input_data = _canonical_secretary_input(team_name="t1")
        assert _is_canonical_secretary_spawn(input_data) is True

    def test_witness_error_does_not_route_to_main_load_failure_deny(
        self, monkeypatch, tmp_path, capsys,
    ):
        """End-to-end (#1023): a witness read error on the canonical-secretary
        path does NOT reach main()'s fail-closed deny. Pre-#1023 an unlisted
        exception from the predicate propagated through _check_tool_allowed to
        main()'s _emit_load_failure_deny (exit 2). Post-#1023 the witness's
        broad except swallows it → carve-out fires → _check_tool_allowed
        returns None → main() suppresses at exit 0 (ALLOW). The carve-out path
        is now total, so it never triggers the load-failure deny.

        The main()-level fail-closed safety net for GENUINE non-carve-out
        runtime bugs is UNCHANGED and still covered (see
        TestFailClosedGateLogic.test_runtime_exception_with_mutating_tool_still_denies,
        which patches _check_tool_allowed itself to raise and asserts exit 2) —
        only the carve-out-path expectation inverts here.
        """
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        def _raiser(team_name, teams_dir=None):
            raise RuntimeError("genuine bug in the members[] read")

        monkeypatch.setattr(ctx_module, "_iter_members", _raiser)

        exit_code, output = _run_main(
            _canonical_secretary_input(team_name="t1"), capsys
        )
        # Carve-out fired → allow → suppressOutput at exit 0, NOT the exit-2
        # load-failure deny the pre-#1023 typed-except design produced.
        assert exit_code == 0
        assert output.get("suppressOutput") is True
        assert "permissionDecision" not in output.get("hookSpecificOutput", {})

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

    def test_get_team_name_returning_dict_handled_safely(
        self, monkeypatch, tmp_path,
    ):
        """get_team_name returning a non-string (dict) is handled WITHOUT
        raising. The dict is truthy so it passes the empty-SSOT guard, then
        _secretary_in_members(dict) → _iter_members(dict) raises TypeError
        (cannot build a path from a dict) → the witness's broad except → False
        → binding 5 (`not False`) → carve-out fires → ALLOW (result is None).

        #1023: confirms no crash on a non-string truthy team value. The
        outcome is ALLOW (not deny) under the new SAFE fail direction — the
        carve-out only ever permits the canonical secretary spawn (bindings
        1/2/3 still exclude every non-secretary tool), and the predicate never
        raises (totality).
        """
        from bootstrap_gate import _check_tool_allowed
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        monkeypatch.setattr(
            ctx_module, "get_team_name", lambda: {"team": "t1"},
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

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
            # (#979: "wrong_team" scenario removed — binding-4 dropped, so a
            # mismatched team_name no longer denies the carve-out.)
            ("missing_subagent_type", None, "missing_subagent_type"),
            # (#1023: the three *_in_team_has_secretary exception scenarios were
            # removed — a witness-read error now FIRES the carve-out (ALLOW),
            # not deny, so they are no longer deny-path failure modes. Witness
            # exception totality is covered by
            # test_witness_exception_caught_and_carve_out_fires.)
        ],
    )
    def test_deny_reason_is_byte_identical_across_failure_modes(
        self, monkeypatch, tmp_path, scenario, overrides, exc_setup,
    ):
        """Across every DENY failure mode (wrong binding, missing key), the
        user-visible permissionDecisionReason is BYTE-IDENTICAL to the
        canonical deny-reason literal pinned independently in
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

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        if exc_setup == "missing_subagent_type":
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

    def test_witness_read_error_leaks_no_exception_detail(
        self, monkeypatch, tmp_path,
    ):
        """When the members[] read raises with a sensitive-looking message,
        NO formatted exception text reaches the user (#1023 security pin).

        Post-#1023 the JOIN witness's broad except swallows the exception and
        returns False → carve-out fires → ALLOW (result is None). Because the
        carve-out path emits NO user-visible string at all on a witness error,
        the no-leak guarantee holds trivially AND more strongly than the
        pre-#1023 deny path (which had to scrub the deny reason): there is
        simply no string in which the sensitive token could appear. We assert
        the witness error neither raises nor surfaces ANY string carrying the
        sensitive content.
        """
        from bootstrap_gate import _check_tool_allowed
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        sensitive = "secret-token-deadbeef /Users/victim/.ssh/id_rsa"

        def _raiser(team_name, teams_dir=None):
            raise OSError(sensitive)

        monkeypatch.setattr(ctx_module, "_iter_members", _raiser)

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        # Carve-out fires (allow) → no deny string emitted → no leak surface.
        assert result is None
        # Defensive: if a future change ever returns a string here, it must not
        # carry the sensitive exception text.
        if result is not None:
            assert sensitive not in result
            assert "deadbeef" not in result
            assert "/Users/" not in result
