"""Tests for pact-plugin/hooks/teachback_gate.py (#401 Commit #7).

Covers: _BLOCKED_TOOLS set, _check_tool_allowed decision flow across
carve-outs + state inferences, main() stdin handling + fail-open, Phase 1
advisory mode exit 0 + systemMessage, Phase 2 blocking mode exit 2 +
hookSpecificOutput, hooks.json matcherless registration invariant,
journal event emission.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
_SHARED_DIR = _HOOKS_DIR / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

import teachback_gate  # noqa: E402
from teachback_gate import (  # noqa: E402
    _BLOCKED_TOOLS,
    _check_tool_allowed,
)


# ---------------------------------------------------------------------------
# Constants + invariants
# ---------------------------------------------------------------------------

class TestBlockedToolsInvariants:
    def test_matches_bootstrap_gate_set(self):
        """_BLOCKED_TOOLS must equal bootstrap_gate._BLOCKED_TOOLS verbatim
        (TERMINOLOGY-LOCK.md §Blocked-tool set)."""
        from bootstrap_gate import _BLOCKED_TOOLS as BOOT

        assert _BLOCKED_TOOLS == BOOT

    def test_bash_not_blocked(self):
        """Bash is explicitly NOT blocked — same reasoning as bootstrap_gate
        (Bash is the recovery tool of last resort)."""
        assert "Bash" not in _BLOCKED_TOOLS

    def test_default_phase_is_advisory(self):
        """Phase 1 default: advisory. Flip to blocking in Commit #14b."""
        from shared import TEACHBACK_MODE_ADVISORY

        assert teachback_gate._TEACHBACK_MODE == TEACHBACK_MODE_ADVISORY


# ---------------------------------------------------------------------------
# _check_tool_allowed — decision branches
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_scan():
    """Stub for shared.teachback_scan.scan_teachback_state."""
    return MagicMock()


@pytest.fixture
def fake_resolve():
    """Stub for pact_context.resolve_agent_name."""
    return MagicMock()


class TestCheckToolAllowedFastPaths:
    def test_mcp_tool_always_allowed(self):
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "mcp__foo__bar", "team_name": "pact-test"}
        )
        assert reason is None

    def test_non_blocked_tool_allowed(self):
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Read", "team_name": "pact-test"}
        )
        assert reason is None

    def test_unknown_tool_allowed(self):
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "SomeFutureToolThatDoesntExist"}
        )
        assert reason is None


class TestCheckToolAllowedAgentContext:
    def test_no_agent_name_allowed(self, monkeypatch):
        # Orchestrator / non-PACT context — resolve_agent_name returns ""
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "")
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None

    def test_exempt_agent_allowed(self, monkeypatch):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "secretary")
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None

    def test_auditor_allowed(self, monkeypatch):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "pact-auditor")
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None


class TestCheckToolAllowedScanBranches:
    def _setup(self, monkeypatch, scan_result):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "backend-coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: scan_result)

    def test_no_in_progress_tasks_allowed(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 0,
            "first_failing_task_id": "",
            "first_failing_reason": "",
            "first_failing_metadata": {},
            "first_failing_protocol_level": "exempt",
            "all_active": True,
        })
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None

    def test_all_active_allowed(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 3,
            "first_failing_task_id": "",
            "first_failing_reason": "",
            "first_failing_metadata": {},
            "first_failing_protocol_level": "full",
            "all_active": True,
        })
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None

    def test_failing_task_produces_deny_reason(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "17",
            "first_failing_reason": "missing_submit",
            "first_failing_metadata": {"variety": {"total": 10}},
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is not None
        assert 'TaskUpdate(taskId="17"' in reason
        assert ctx["reason_code"] == "missing_submit"
        assert ctx["task_id"] == "17"
        assert ctx["agent_name"] == "backend-coder-1"
        assert ctx["tool_name"] == "Edit"

    def test_simplified_protocol_uses_simplified_template(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "5",
            "first_failing_reason": "missing_submit",
            "first_failing_metadata": {"variety": {"total": 8}},
            "first_failing_protocol_level": "simplified",
            "all_active": False,
        })
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is not None
        # simplified template excludes most_likely_wrong / least_confident_item
        assert "most_likely_wrong" not in reason
        assert "least_confident_item" not in reason

    def test_unaddressed_items_populates_context(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "7",
            "first_failing_reason": "unaddressed_items",
            "first_failing_metadata": {
                "variety": {"total": 11},
                "teachback_approved": {
                    "conditions_met": {"unaddressed": ["scope_a", "scope_b"]}
                },
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert "scope_a, scope_b" in reason

    def test_corrections_populates_context(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "9",
            "first_failing_reason": "corrections_pending",
            "first_failing_metadata": {
                "variety": {"total": 11},
                "teachback_corrections": {
                    "issues": ["first_action missing citation"],
                    "request_revisions_on": ["first_action"],
                },
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert "missing citation" in reason
        assert "first_action" in reason


# ---------------------------------------------------------------------------
# main() — stdin + exit code flow
# ---------------------------------------------------------------------------

def _run_main(monkeypatch, capsys, stdin_payload, *, check_result=None):
    if isinstance(stdin_payload, (dict, list)):
        raw = json.dumps(stdin_payload)
    else:
        raw = stdin_payload
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    if check_result is not None:
        monkeypatch.setattr(teachback_gate, "_check_tool_allowed",
                             lambda _: check_result)
    with pytest.raises(SystemExit) as exc:
        teachback_gate.main()
    captured = capsys.readouterr()
    return exc.value.code, captured.out, captured.err


class TestMainStdinFailOpen:
    def test_malformed_stdin_fails_open(self, monkeypatch, capsys):
        code, out, _err = _run_main(monkeypatch, capsys, "{{not-json}")
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_empty_stdin_fails_open(self, monkeypatch, capsys):
        code, out, _err = _run_main(monkeypatch, capsys, "")
        assert code == 0


class TestMainAdvisoryMode:
    def test_allow_emits_suppress(self, monkeypatch, capsys):
        code, out, _err = _run_main(
            monkeypatch, capsys, {"tool_name": "Read"},
            check_result=(None, {}),
        )
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_deny_in_advisory_exits_0_with_system_message(self, monkeypatch, capsys):
        # Ensure advisory mode is active
        monkeypatch.setattr(teachback_gate, "_TEACHBACK_MODE", "advisory")
        # Stub journal append to avoid disk writes
        monkeypatch.setattr(teachback_gate, "append_event", lambda *a, **kw: None)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda *a, **kw: {"type": "fake"})

        code, out, _err = _run_main(
            monkeypatch, capsys, {"tool_name": "Edit"},
            check_result=(
                "Send a teachback before Edit. ...",
                {"reason_code": "missing_submit",
                 "tool_name": "Edit",
                 "task_id": "17",
                 "agent_name": "backend-coder-1"},
            ),
        )
        assert code == 0
        payload = json.loads(out.strip())
        assert "systemMessage" in payload
        assert "Send a teachback" in payload["systemMessage"]


class TestMainBlockingMode:
    def test_deny_in_blocking_exits_2_with_hookspecific(self, monkeypatch, capsys):
        monkeypatch.setattr(teachback_gate, "_TEACHBACK_MODE", "blocking")
        monkeypatch.setattr(teachback_gate, "append_event", lambda *a, **kw: None)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda *a, **kw: {"type": "fake"})

        code, out, _err = _run_main(
            monkeypatch, capsys, {"tool_name": "Edit"},
            check_result=(
                "Send a teachback before Edit. ...",
                {"reason_code": "missing_submit",
                 "tool_name": "Edit",
                 "task_id": "17",
                 "agent_name": "backend-coder-1"},
            ),
        )
        assert code == 2
        payload = json.loads(out.strip())
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Send a teachback" in payload["hookSpecificOutput"][
            "permissionDecisionReason"
        ]


class TestMainInternalExceptionFailOpen:
    def test_exception_in_check_fails_open(self, monkeypatch, capsys):
        def boom(_):
            raise RuntimeError("gate exploded")

        monkeypatch.setattr(teachback_gate, "_check_tool_allowed", boom)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"tool_name": "Edit"}
        )))
        with pytest.raises(SystemExit) as exc:
            teachback_gate.main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "teachback_gate" in captured.err


# ---------------------------------------------------------------------------
# hooks.json invariants
# ---------------------------------------------------------------------------

class TestHooksJsonRegistration:
    def test_teachback_gate_is_registered(self):
        hooks_json = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
        config = json.loads(hooks_json.read_text(encoding="utf-8"))

        found = False
        for entry in config["hooks"].get("PreToolUse", []):
            for hook in entry.get("hooks", []):
                if "teachback_gate.py" in hook.get("command", ""):
                    found = True
                    # matcherless: entry must not have a matcher key
                    assert "matcher" not in entry, (
                        "teachback_gate.py must be registered matcherless — "
                        "it must fire for ALL hookable tools to enforce the gate"
                    )
        assert found, "teachback_gate.py must be registered in PreToolUse"

    def test_bootstrap_precedes_teachback(self):
        hooks_json = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
        config = json.loads(hooks_json.read_text(encoding="utf-8"))

        bootstrap_idx = None
        teachback_idx = None
        for i, entry in enumerate(config["hooks"].get("PreToolUse", [])):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if "bootstrap_gate.py" in cmd and bootstrap_idx is None:
                    bootstrap_idx = i
                if "teachback_gate.py" in cmd and teachback_idx is None:
                    teachback_idx = i

        assert bootstrap_idx is not None
        assert teachback_idx is not None
        assert bootstrap_idx < teachback_idx, (
            "bootstrap_gate must fire BEFORE teachback_gate. Bootstrap is the "
            "gate-of-gates; teachback is meaningless until bootstrap completes."
        )
