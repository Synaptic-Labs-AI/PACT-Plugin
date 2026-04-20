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
        # After #7-follow-up (Y2 wiring), a minimally-shaped approved
        # dict fails full content-schema validation and the gate upgrades
        # the reason to invalid_submit. To exercise the unaddressed_items
        # path proper, provide a fully schema-valid approved with
        # non-empty unaddressed. required_scope_items MUST match the
        # addressed items (case-insensitive membership check).
        submit = {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to the session_token handling path."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with the existing session_token flow",
                "consequence": "if wrong the session_token validation may silently accept expired tokens",
            },
            "least_confident_item": {
                "item": "exact semantics of the session_token expiry check across timezones",
                "current_plan": "mirror the approach from auth.py:42 which handles UTC offsets",
                "failure_mode": "timezone drift could let stale session_tokens slip past the gate",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }
        approved = {
            "scanned_candidate": {
                "candidate": "the middleware might instead be mis-routing the session_token lookup",
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": "confirm",
                "grounding": "dispatch §Scope line 17 session_token",
            },
            "response_to_least_confident": {
                "verdict": "correct",
                "grounding": "see architecture §Token-Validation line 42",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {
                "addressed": ["scope_a"],
                "unaddressed": ["scope_b", "scope_c"],
            },
        }
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "7",
            "first_failing_reason": "unaddressed_items",
            "first_failing_metadata": {
                "variety": {"total": 11, "novelty": 3, "scope": 3,
                             "uncertainty": 3, "risk": 2},
                "required_scope_items": ["scope_a", "scope_b", "scope_c"],
                "teachback_submit": submit,
                "teachback_approved": approved,
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert ctx["reason_code"] == "unaddressed_items"
        assert "scope_b, scope_c" in reason

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

class TestStateTransitionEmission:
    """Y1 follow-up — teachback_state_transition de-dupe emit."""

    def test_state_from_reason_mapping(self):
        from teachback_gate import _state_from_reason

        assert _state_from_reason("missing_submit") == "teachback_pending"
        assert _state_from_reason("invalid_submit") == "teachback_pending"
        assert _state_from_reason("awaiting_approval") == "teachback_under_review"
        assert _state_from_reason("unaddressed_items") == "teachback_correcting"
        assert _state_from_reason("corrections_pending") == "teachback_correcting"
        assert _state_from_reason("unknown_code") == "teachback_pending"

    def test_trigger_vocabulary(self):
        from teachback_gate import _trigger_for_transition

        assert _trigger_for_transition("", "teachback_under_review") == "teammate_submit"
        assert _trigger_for_transition(
            "teachback_pending", "teachback_under_review"
        ) == "teammate_submit"
        assert _trigger_for_transition("teachback_under_review", "active") == "lead_approve"
        assert _trigger_for_transition(
            "teachback_correcting", "active"
        ) == "lead_approve"
        assert _trigger_for_transition(
            "teachback_under_review", "teachback_correcting"
        ) == "lead_correct"
        assert _trigger_for_transition(
            "teachback_correcting", "teachback_under_review"
        ) == "teammate_revise"
        assert _trigger_for_transition("", "") == "unknown"

    def test_emit_on_first_observation(self, monkeypatch):
        """First transition for a task emits with no from_state."""
        import teachback_gate

        emitted = []
        monkeypatch.setattr(
            teachback_gate, "read_events", lambda _type: []
        )
        monkeypatch.setattr(
            teachback_gate, "append_event",
            lambda ev: emitted.append(ev) or True,
        )
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

        teachback_gate._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="teachback_under_review",
        )
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["type"] == "teachback_state_transition"
        assert ev["task_id"] == "17"
        assert ev["to_state"] == "teachback_under_review"
        assert "from_state" not in ev  # first transition has no from_state
        assert ev["trigger"] == "teammate_submit"

    def test_dedupe_same_state_no_emit(self, monkeypatch):
        import teachback_gate

        prior = [
            {"type": "teachback_state_transition", "task_id": "17",
             "to_state": "teachback_under_review"},
        ]
        emitted = []
        monkeypatch.setattr(
            teachback_gate, "read_events", lambda _type: prior
        )
        monkeypatch.setattr(
            teachback_gate, "append_event",
            lambda ev: emitted.append(ev) or True,
        )
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

        teachback_gate._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="teachback_under_review",
        )
        assert emitted == []  # no duplicate emission

    def test_emit_on_state_change(self, monkeypatch):
        import teachback_gate

        prior = [
            {"type": "teachback_state_transition", "task_id": "17",
             "to_state": "teachback_under_review"},
        ]
        emitted = []
        monkeypatch.setattr(
            teachback_gate, "read_events", lambda _type: prior
        )
        monkeypatch.setattr(
            teachback_gate, "append_event",
            lambda ev: emitted.append(ev) or True,
        )
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

        teachback_gate._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="active",
        )
        assert len(emitted) == 1
        ev = emitted[0]
        assert ev["to_state"] == "active"
        assert ev["from_state"] == "teachback_under_review"
        assert ev["trigger"] == "lead_approve"

    def test_dedupe_task_scoped(self, monkeypatch):
        """Transitions for other tasks don't block emission for this task."""
        import teachback_gate

        prior = [
            {"type": "teachback_state_transition", "task_id": "99",
             "to_state": "teachback_under_review"},  # different task
        ]
        emitted = []
        monkeypatch.setattr(
            teachback_gate, "read_events", lambda _type: prior
        )
        monkeypatch.setattr(
            teachback_gate, "append_event",
            lambda ev: emitted.append(ev) or True,
        )
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

        teachback_gate._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="teachback_under_review",
        )
        # Emit fires for task 17 because task 99's prior is irrelevant
        assert len(emitted) == 1

    def test_read_events_failure_falls_open(self, monkeypatch):
        """If read_events raises, emission proceeds (treated as no prior)."""
        import teachback_gate

        def boom(_type):
            raise RuntimeError("journal read exploded")

        emitted = []
        monkeypatch.setattr(teachback_gate, "read_events", boom)
        monkeypatch.setattr(
            teachback_gate, "append_event",
            lambda ev: emitted.append(ev) or True,
        )
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

        # Should not raise
        teachback_gate._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="teachback_pending",
        )
        # Behaves as empty prior → emits once
        assert len(emitted) == 1

    def test_append_event_failure_swallowed(self, monkeypatch):
        """If append_event raises, caller does not see the exception."""
        import teachback_gate

        def boom(_event):
            raise RuntimeError("append exploded")

        monkeypatch.setattr(teachback_gate, "read_events", lambda _type: [])
        monkeypatch.setattr(teachback_gate, "append_event", boom)
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

        # Should not raise
        teachback_gate._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="teachback_pending",
        )


class TestInvalidSubmitErrorSurfacing:
    """Y3 follow-up — invalid_submit deny reason carries real per-field error."""

    def _setup(self, monkeypatch, scan_result):
        import teachback_gate
        monkeypatch.setattr(
            teachback_gate, "resolve_agent_name",
            lambda *a, **kw: "backend-coder-1",
        )
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(
            teachback_gate, "scan_teachback_state",
            lambda *a, **kw: scan_result,
        )
        # Silence state-transition journal writes
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda ev: True)
        monkeypatch.setattr(
            teachback_gate, "make_event",
            lambda _type, **kw: {"type": _type, **kw},
        )

    def test_structurally_valid_semantically_invalid_upgrades_to_invalid_submit(
        self, monkeypatch
    ):
        """Scanner says awaiting_approval; validator finds schema errors;
        gate upgrades reason to invalid_submit."""
        # Submit has all required fields structurally, but first_action.action
        # fails the citation regex.
        submit = {
            "understanding": "x" * 120,  # passes min 100, mostly empty content
            "most_likely_wrong": {
                "assumption": "the middleware integrates cleanly with existing session_token flow",
                "consequence": "if wrong the auth middleware may drop valid session_tokens silently",
            },
            "least_confident_item": {
                "item": "the exact semantics of session_token expiry checks across zones",
                "current_plan": "mirror auth.py:42 which handles the UTC offset correctly",
                "failure_mode": "timezone drift may allow stale tokens to pass",
            },
            "first_action": {
                "action": "this is not a valid citation at all",
                "expected_signal": "tests pass reliably after the change",
            },
        }
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "17",
            "first_failing_reason": "awaiting_approval",
            "first_failing_metadata": {
                "variety": {"total": 11},
                "required_scope_items": ["session_token handling"],
                "teachback_submit": submit,
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert ctx["reason_code"] == "invalid_submit"
        # Deny reason should surface the specific first_action.action failure
        assert "first_action.action" in reason
        assert "citation shape" in reason.lower()

    def test_valid_submit_remains_awaiting_approval(self, monkeypatch):
        """If content-schema validation passes, reason stays awaiting_approval."""
        submit = {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with session_token flow",
                "consequence": "if wrong the session_token validation may silently accept expired tokens",
            },
            "least_confident_item": {
                "item": "exact semantics of the session_token expiry check across zones",
                "current_plan": "mirror the approach from auth.py:42 which handles offsets",
                "failure_mode": "timezone drift could let stale session_tokens slip past",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "17",
            "first_failing_reason": "awaiting_approval",
            "first_failing_metadata": {
                "variety": {"total": 11},
                "required_scope_items": ["auth middleware", "session_token handling"],
                "teachback_submit": submit,
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert ctx["reason_code"] == "awaiting_approval"
        # Awaiting-approval template mentions teachback_approved + teachback_corrections
        assert "teachback_approved" in reason


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
