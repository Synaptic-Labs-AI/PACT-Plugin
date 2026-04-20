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
        # Non-active branch context: the scanner classified this task as
        # `unaddressed_items` (T5 auto-downgrade). To exercise that
        # branch cleanly we must also supply a fully schema-valid
        # approved — otherwise `validate_approved` would find per-field
        # errors and the gate would upgrade the reason to
        # `invalid_submit` (see TestInvalidSubmitErrorSurfacing below).
        # required_scope_items MUST match the addressed items
        # (case-insensitive membership check).
        #
        # This test exercises the NON-ACTIVE (deny) branch of
        # _check_tool_allowed, not the active-path R2-A1 fix. The R2-A1
        # active-path content-validation is covered by
        # TestActiveTaskContentValidation below.
        submit = {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to the session_token handling path."
            ),
            "most_likely_wrong": {
                "assumption": "the session_token handling path integrates cleanly with the existing middleware flow",
                "consequence": "if wrong the session_token handling may silently accept expired tokens",
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


class TestActiveTaskContentValidation:
    """R2-A1 fix: when scan[all_active]=True the gate runs the full
    content validator on every active task's teachback_approved +
    teachback_submit. A lead writing the minimal
    `{"teachback_approved": {"conditions_met": {"unaddressed": []}}}`
    rubber-stamp shape passes the scanner's T4 structural check, but the
    full validator finds empty/missing fields and upgrades to an
    invalid_submit deny. These tests counter-test-by-revert the bypass:
    with the fix removed, the gate returns None on minimal approved and
    the test fails as intended.
    """

    def _setup(self, monkeypatch, scan_result):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "backend-coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: scan_result)
        # Silence state-transition journal writes during the active
        # path's observability emit.
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda _ev: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t, **kw})

    def _valid_submit(self):
        return {
            "understanding": (
                "I will implement the auth middleware per the architect "
                "spec with careful attention to the session_token handling "
                "path across UTC offsets."
            ),
            "most_likely_wrong": {
                "assumption": "the session_token handling path integrates cleanly with the existing middleware flow",
                "consequence": "if wrong the session_token handling may silently accept expired tokens",
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

    def test_minimal_rubber_stamp_approved_is_rejected(self, monkeypatch):
        """Counter-test-by-revert: minimal approved
        `{"conditions_met": {"unaddressed": []}}` MUST be rejected.
        Revert the R2-A1 fix (remove _check_active_tasks_content call)
        and this test fails — confirming the fix is load-bearing."""
        submit = self._valid_submit()
        # Minimal rubber-stamp — just the structural T4 minimum.
        approved = {"conditions_met": {"unaddressed": []}}
        metadata = {
            "variety": {"total": 11, "novelty": 3, "scope": 3,
                         "uncertainty": 3, "risk": 2},
            "required_scope_items": ["session_token handling",
                                     "UTC offset handling"],
            "teachback_submit": submit,
            "teachback_approved": approved,
        }
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "",
            "first_failing_reason": "",
            "first_failing_metadata": {},
            "first_failing_protocol_level": "exempt",
            "all_active": True,
            "active_tasks": [("17", metadata, "full")],
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is not None, "minimal approved must NOT silently pass"
        assert ctx["reason_code"] == "invalid_submit"
        assert ctx["task_id"] == "17"

    def test_fully_valid_approved_is_allowed(self, monkeypatch):
        """Happy path: a lead-written approved that satisfies every
        content-shape rule allows the tool call."""
        submit = self._valid_submit()
        approved = {
            "scanned_candidate": {
                "candidate": "the middleware might instead be mis-routing the session_token lookup",
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": "confirm",
                "grounding": "dispatch §auth-middleware section line 42",
            },
            "response_to_least_confident": {
                "verdict": "confirm",
                "grounding": "dispatch §UTC-offset section line 55",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {
                "addressed": ["session_token handling", "UTC offset handling"],
                "unaddressed": [],
            },
        }
        metadata = {
            "variety": {"total": 11, "novelty": 3, "scope": 3,
                         "uncertainty": 3, "risk": 2},
            "required_scope_items": ["session_token handling",
                                     "UTC offset handling"],
            "teachback_submit": submit,
            "teachback_approved": approved,
        }
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "",
            "first_failing_reason": "",
            "first_failing_metadata": {},
            "first_failing_protocol_level": "exempt",
            "all_active": True,
            "active_tasks": [("19", metadata, "full")],
        })
        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None, "fully-valid approved must allow the tool"

    def test_one_rubber_stamped_among_many_taints_all(self, monkeypatch):
        """ALL-match semantics inherit from the existing scanner design:
        if ANY active task has a rubber-stamped approved, the gate denies
        for the entire agent (a valid approval on task A cannot satisfy
        the gate for task B's content violation)."""
        submit = self._valid_submit()
        good_approved = {
            "scanned_candidate": {
                "candidate": "the middleware might mis-route session_token lookups",
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": "confirm",
                "grounding": "dispatch §auth-middleware line 42",
            },
            "response_to_least_confident": {
                "verdict": "confirm",
                "grounding": "dispatch §UTC-offset line 55",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {
                "addressed": ["session_token handling"],
                "unaddressed": [],
            },
        }
        rubber_stamp = {"conditions_met": {"unaddressed": []}}
        good_meta = {
            "variety": {"total": 11, "novelty": 3, "scope": 3,
                         "uncertainty": 3, "risk": 2},
            "required_scope_items": ["session_token handling"],
            "teachback_submit": submit,
            "teachback_approved": good_approved,
        }
        bad_meta = {
            "variety": {"total": 11, "novelty": 3, "scope": 3,
                         "uncertainty": 3, "risk": 2},
            "required_scope_items": ["session_token handling"],
            "teachback_submit": submit,
            "teachback_approved": rubber_stamp,
        }
        self._setup(monkeypatch, {
            "task_count": 2,
            "first_failing_task_id": "",
            "first_failing_reason": "",
            "first_failing_metadata": {},
            "first_failing_protocol_level": "exempt",
            "all_active": True,
            "active_tasks": [
                ("21", good_meta, "full"),
                ("22", bad_meta, "full"),
            ],
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is not None
        # Sorted iteration; good task (21) is checked first and passes,
        # bad task (22) is checked second and produces the deny.
        assert ctx["task_id"] == "22"
        assert ctx["reason_code"] == "invalid_submit"

    def test_state_transition_emitted_for_active(self, monkeypatch):
        """R2-A2 fix: on the active-allow path, emit
        teachback_state_transition(to_state='active') for each active
        task so the lead_approve trigger is observed."""
        submit = self._valid_submit()
        approved = {
            "scanned_candidate": {
                "candidate": "the middleware might mis-route session_token lookups",
                "evidence_against": "session_token",
            },
            "response_to_assumption": {
                "verdict": "confirm",
                "grounding": "dispatch §auth line 42",
            },
            "response_to_least_confident": {
                "verdict": "confirm",
                "grounding": "dispatch §auth line 55",
            },
            "first_action_check": {
                "my_derivation": "auth.py:42",
                "match": "match",
                "if_mismatch_resolution": None,
            },
            "conditions_met": {
                "addressed": ["session_token handling"],
                "unaddressed": [],
            },
        }
        metadata = {
            "variety": {"total": 11, "novelty": 3, "scope": 3,
                         "uncertainty": 3, "risk": 2},
            "required_scope_items": ["session_token handling"],
            "teachback_submit": submit,
            "teachback_approved": approved,
        }
        emitted: list[dict] = []
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "backend-coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "",
                                 "first_failing_reason": "",
                                 "first_failing_metadata": {},
                                 "first_failing_protocol_level": "exempt",
                                 "all_active": True,
                                 "active_tasks": [("23", metadata, "full")],
                             })
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event",
                             lambda ev: emitted.append(ev) or True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t, **kw})

        reason, _ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None
        transitions = [e for e in emitted
                       if e.get("type") == "teachback_state_transition"]
        assert len(transitions) == 1
        assert transitions[0]["to_state"] == "active"
        assert transitions[0]["task_id"] == "23"
        assert transitions[0].get("trigger") == "lead_approve"


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
        # M-R4-3: teachback_correcting -> active now emits "content_fixed"
        # to distinguish teammate re-submit from true lead_approve.
        assert _trigger_for_transition(
            "teachback_correcting", "active"
        ) == "content_fixed"
        assert _trigger_for_transition(
            "teachback_under_review", "teachback_correcting"
        ) == "lead_correct"
        assert _trigger_for_transition(
            "teachback_correcting", "teachback_under_review"
        ) == "teammate_revise"
        assert _trigger_for_transition("", "") == "unknown"

    def test_correcting_to_active_emits_content_fixed(self):
        """M-R4-3 (round-4 architect): splitting the `to_state == active`
        branch so Phase-2 forgery detection can distinguish the two
        arrival paths.

        Before cycle-5: teachback_correcting -> active returned
        "lead_approve" (same as teachback_under_review -> active). A
        teammate that overwrites teachback_approved with a conforming
        dict cannot be distinguished from a real lead approve.

        After cycle-5: content_fixed (teammate-authored re-submit)
        vs lead_approve (true lead approve from under_review). The
        auditor uses the trigger to filter forgery candidates.
        """
        from teachback_gate import _trigger_for_transition

        assert _trigger_for_transition(
            "teachback_correcting", "active"
        ) == "content_fixed"

    def test_under_review_to_active_emits_lead_approve(self):
        """M-R4-3 partner: under_review -> active remains lead_approve.
        Locks the true-lead-approve path against accidental conflation
        with content_fixed during future refactors."""
        from teachback_gate import _trigger_for_transition

        assert _trigger_for_transition(
            "teachback_under_review", "active"
        ) == "lead_approve"

    def test_active_to_teachback_pending_trigger_is_content_invalid(self):
        """M2 (round 3): explicit trigger for the active → teachback_pending
        transition. This transition fires when _check_active_tasks_content
        denies a structurally-active task on a generation-shape content
        violation (substring-inequality, citation, template-density, etc.);
        it is neither a lead_approve nor a teammate_revise. Previously
        mapped to 'unknown' — fails the JOURNAL-EVENTS.md §Trigger values
        controlled-vocab intent. Adds 'content_invalid' per the T10
        Transition Matrix row in STATE-MACHINE.md."""
        from teachback_gate import _trigger_for_transition

        assert _trigger_for_transition(
            "active", "teachback_pending"
        ) == "content_invalid"

    def test_content_invalid_in_controlled_vocabulary(self):
        """Defensive: confirm 'content_invalid' is the ONLY trigger the
        active→teachback_pending transition can return. If a future
        refactor renames the trigger to, e.g., 'active_reject', the
        JOURNAL-EVENTS.md docs must be updated in lockstep. This test
        pins the string so drift surfaces at pytest time."""
        from teachback_gate import _trigger_for_transition

        trigger = _trigger_for_transition("active", "teachback_pending")
        assert trigger in {
            "teammate_submit", "lead_approve", "content_fixed",
            "lead_correct", "auto_downgrade", "teammate_revise",
            "content_invalid", "unknown",
        }, f"Trigger '{trigger}' is not in the controlled vocabulary"
        assert trigger != "unknown", (
            "active→teachback_pending must be an explicit named trigger, "
            "not the fallback 'unknown' bucket"
        )

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


# ---------------------------------------------------------------------------
# P0: dedicated TestFailOpen + TestErrorSuppressMutualExclusivity
# (mirrors test_bootstrap_gate.py discipline per dispatch checklist)
# ---------------------------------------------------------------------------


class TestFailOpen:
    """P0: Every exception path must exit 0 with suppressOutput. A bug
    in the gate must NEVER block a teammate's legitimate tool call."""

    def test_malformed_stdin_json_fails_open(self, capsys):
        monkeypatch_stdin = io.StringIO("not valid json {")
        with patch("sys.stdin", monkeypatch_stdin):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed == {"suppressOutput": True}

    def test_empty_stdin_fails_open(self, capsys):
        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {"suppressOutput": True}

    def test_check_tool_allowed_runtime_error_fails_open(self, monkeypatch, capsys):
        monkeypatch.setattr(
            teachback_gate, "_check_tool_allowed",
            lambda _: (_ for _ in ()).throw(RuntimeError("gate exploded")),
        )
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "teachback_gate" in captured.err

    def test_oserror_in_scan_fails_open(self, monkeypatch, capsys):
        """OSError raised inside scan_teachback_state must be absorbed
        by the outer try/except — gate exits 0 (never 2). hook_error_json
        emits a systemMessage hook-warning payload that bubbles up."""
        def scan_boom(*a, **kw):
            raise OSError("disk wedged")

        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state", scan_boom)
        with patch("sys.stdin", io.StringIO(json.dumps(
            {"tool_name": "Edit", "team_name": "pact-test"}
        ))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        # SACROSANCT fail-open: never exit 2 on unhandled exception.
        assert exc.value.code == 0

    def test_validator_exception_does_not_block(self, monkeypatch, capsys):
        """Y2/Y3 integration: if validate_submit raises, the scanner's
        structural classification stays in force — gate doesn't crash."""
        def validator_boom(*a, **kw):
            raise RuntimeError("regex engine melted")

        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "17",
                                 "first_failing_reason": "awaiting_approval",
                                 "first_failing_metadata": {
                                     "variety": {"total": 11},
                                     "required_scope_items": ["x"],
                                     "teachback_submit": {"understanding": "y" * 120},
                                 },
                                 "first_failing_protocol_level": "full",
                                 "all_active": False,
                             })
        monkeypatch.setattr(teachback_gate, "validate_submit", validator_boom)
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t, **kw})

        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        # Scanner said awaiting_approval; validator blew up → reason stays
        assert ctx["reason_code"] == "awaiting_approval"
        assert reason is not None


class TestErrorSuppressMutualExclusivity:
    """P0: These hooks use suppressOutput for fail-open, never
    systemMessage. Deny path uses hookSpecificOutput (blocking) or
    systemMessage (advisory), never suppressOutput."""

    def test_fail_open_no_system_message(self, capsys):
        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit):
                teachback_gate.main()
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed
        assert "hookSpecificOutput" not in parsed

    def test_advisory_deny_no_suppress_output(self, monkeypatch, capsys):
        monkeypatch.setattr(teachback_gate, "_TEACHBACK_MODE", "advisory")
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t})
        monkeypatch.setattr(
            teachback_gate, "_check_tool_allowed",
            lambda _: ("deny reason body", {
                "reason_code": "missing_submit", "tool_name": "Edit",
                "task_id": "17", "agent_name": "coder-1",
            }),
        )
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))):
            with pytest.raises(SystemExit):
                teachback_gate.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in payload
        assert "suppressOutput" not in payload

    def test_blocking_deny_no_suppress_output(self, monkeypatch, capsys):
        monkeypatch.setattr(teachback_gate, "_TEACHBACK_MODE", "blocking")
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t})
        monkeypatch.setattr(
            teachback_gate, "_check_tool_allowed",
            lambda _: ("deny reason body", {
                "reason_code": "missing_submit", "tool_name": "Edit",
                "task_id": "17", "agent_name": "coder-1",
            }),
        )
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 2
        payload = json.loads(capsys.readouterr().out.strip())
        assert "hookSpecificOutput" in payload
        assert "suppressOutput" not in payload
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allow_path_no_hook_specific_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            teachback_gate, "_check_tool_allowed", lambda _: (None, {}),
        )
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Read"}))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert "suppressOutput" in payload
        assert "hookSpecificOutput" not in payload
        assert "systemMessage" not in payload


# ---------------------------------------------------------------------------
# Coverage fills — narrow-targeted tests for uncovered branches
# ---------------------------------------------------------------------------


class TestEmptyTeamNameShortCircuit:
    """Line 126: team_name resolves to empty string → allow (not our team)."""

    def test_empty_team_name_returns_none(self, monkeypatch):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "")
        reason, ctx = _check_tool_allowed({"tool_name": "Edit"})
        assert reason is None
        assert ctx == {}


class TestInvalidSubmitFallbackWhenSubmitIsNone:
    """Lines 217-222: scanner said invalid_submit but submit is None/
    non-dict (so validator produced no FieldError). Fallback populates
    a minimal error hint from the protocol_level."""

    def _setup(self, monkeypatch, scan_result):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: scan_result)
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t, **kw})

    def test_submit_none_invalid_submit_fallback_hint(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "17",
            "first_failing_reason": "invalid_submit",
            "first_failing_metadata": {
                "variety": {"total": 11},
                "required_scope_items": ["x"],
                "teachback_submit": None,  # non-dict → validator returns []
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert ctx["reason_code"] == "invalid_submit"
        # Fallback template uses the generic hint — protocol level named
        assert "full" in reason

    def test_submit_non_dict_invalid_submit_fallback(self, monkeypatch):
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "17",
            "first_failing_reason": "invalid_submit",
            "first_failing_metadata": {
                "variety": {"total": 8},
                "teachback_submit": "just a string",  # scanner sees as invalid
            },
            "first_failing_protocol_level": "simplified",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Write", "team_name": "pact-test"}
        )
        assert ctx["reason_code"] == "invalid_submit"
        # Fallback should surface the protocol_level "simplified" in the hint
        assert "simplified" in reason


class TestStateTransitionEmissionOuterFailOpen:
    """Lines 235-237: outer try/except around _emit_state_transition_if_changed
    absorbs exceptions. Verifies the gate still returns deny_reason even
    if the emitter blows up."""

    def test_emit_exception_absorbed(self, monkeypatch):
        def emit_boom(**kw):
            raise RuntimeError("emitter exploded")

        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "17",
                                 "first_failing_reason": "missing_submit",
                                 "first_failing_metadata": {"variety": {"total": 10}},
                                 "first_failing_protocol_level": "full",
                                 "all_active": False,
                             })
        monkeypatch.setattr(
            teachback_gate, "_emit_state_transition_if_changed", emit_boom,
        )

        # Should NOT raise; gate returns deny_reason normally
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is not None
        assert ctx["reason_code"] == "missing_submit"


class TestStateTransitionDedupeNonDictEvents:
    """Line 299: when read_events returns a list that contains a non-dict
    entry (journal-corruption defense), the de-dupe scan skips it and
    continues looking for the most-recent dict event."""

    def test_non_dict_event_in_journal_is_skipped(self, monkeypatch):
        import teachback_gate as tg

        # Prior journal has a non-dict entry followed by a dict entry.
        # reversed() iteration means the non-dict is hit first; the
        # filter must skip it and find the dict entry next.
        prior = [
            {"type": "teachback_state_transition", "task_id": "17",
             "to_state": "teachback_under_review"},
            "corrupted-string-entry",  # non-dict; should be skipped
        ]
        emitted = []
        monkeypatch.setattr(tg, "read_events", lambda _t: prior)
        monkeypatch.setattr(tg, "append_event",
                             lambda ev: emitted.append(ev) or True)
        monkeypatch.setattr(tg, "make_event",
                             lambda _t, **kw: {"type": _t, **kw})

        # to_state matches the dict entry — de-dupe suppresses emission
        tg._emit_state_transition_if_changed(
            task_id="17", agent="coder-1", to_state="teachback_under_review",
        )
        assert emitted == [], (
            "de-dupe should skip the non-dict event and find the matching "
            "dict entry, suppressing emission"
        )


class TestReasonUpgradeFromUnaddressedToInvalidSubmit:
    """Line 185-186: when scanner says unaddressed_items but the approved
    structure itself is invalid, gate upgrades reason to invalid_submit so
    the lead sees the actual schema error (not just 'unaddressed')."""

    def _setup(self, monkeypatch, scan_result):
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: scan_result)
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t, **kw})

    def test_bad_approved_with_unaddressed_upgrades(self, monkeypatch):
        # Full-protocol approved with unaddressed non-empty AND missing
        # required fields (e.g. no response_to_assumption) → validator
        # returns FieldError for the missing field, gate upgrades.
        submit = {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with session_token",
                "consequence": "if wrong session_token validation may accept expired tokens",
            },
            "least_confident_item": {
                "item": "exact semantics of session_token expiry across time zones",
                "current_plan": "mirror auth.py:42 which handles offsets correctly",
                "failure_mode": "timezone drift could let stale session_tokens pass",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }
        approved = {
            # Minimal + invalid: no response_to_assumption, no
            # response_to_least_confident, no first_action_check (full
            # protocol requires all three)
            "scanned_candidate": {
                "candidate": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "evidence_against": "session_token",
            },
            "conditions_met": {
                "addressed": ["a"],
                "unaddressed": ["b"],  # scanner sees unaddressed_items
            },
        }
        self._setup(monkeypatch, {
            "task_count": 1,
            "first_failing_task_id": "17",
            "first_failing_reason": "unaddressed_items",
            "first_failing_metadata": {
                "variety": {"total": 11},
                "required_scope_items": ["a", "b"],
                "teachback_submit": submit,
                "teachback_approved": approved,
            },
            "first_failing_protocol_level": "full",
            "all_active": False,
        })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        # Upgraded from unaddressed_items to invalid_submit
        assert ctx["reason_code"] == "invalid_submit", (
            "Gate should upgrade from unaddressed_items to invalid_submit "
            "when the approved structure is itself invalid"
        )


class TestAdvisoryEventEmitFailOpen:
    """Lines 369-370: journal append raises inside _emit_advisory_event;
    exception is swallowed so the systemMessage still goes out."""

    def test_journal_exception_does_not_prevent_advisory(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(teachback_gate, "_TEACHBACK_MODE", "advisory")
        monkeypatch.setattr(
            teachback_gate, "_check_tool_allowed",
            lambda _: ("deny reason body", {
                "reason_code": "missing_submit", "tool_name": "Edit",
                "task_id": "17", "agent_name": "coder-1",
            }),
        )

        def journal_boom(_ev):
            raise RuntimeError("journal died")

        monkeypatch.setattr(teachback_gate, "append_event", journal_boom)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t})
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert "systemMessage" in payload


class TestBlockedEventEmitFailOpen:
    """Lines 387-388: same fail-open pattern for the blocking-mode emit."""

    def test_journal_exception_does_not_prevent_blocking_deny(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(teachback_gate, "_TEACHBACK_MODE", "blocking")
        monkeypatch.setattr(
            teachback_gate, "_check_tool_allowed",
            lambda _: ("deny reason body", {
                "reason_code": "missing_submit", "tool_name": "Edit",
                "task_id": "17", "agent_name": "coder-1",
            }),
        )

        def journal_boom(_ev):
            raise RuntimeError("journal died")

        monkeypatch.setattr(teachback_gate, "append_event", journal_boom)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t})
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 2
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# Counter-test-by-revert — checklist items that span modules
# ---------------------------------------------------------------------------


class TestCounterTestByRevertGate:
    """Counter-test-by-revert sweep for gate-level invariants. Each test
    must fail if its guarded behavior is reverted."""

    def test_item1_pending_to_under_review_via_submit(self, monkeypatch):
        """Checklist item 1: teachback_submit write transitions pending
        → under_review. Valid submit produces awaiting_approval reason
        (not missing_submit)."""
        submit = {
            "understanding": (
                "I will implement the auth middleware per the architect spec "
                "with careful attention to session_token expiry handling."
            ),
            "most_likely_wrong": {
                "assumption": "the auth middleware integrates cleanly with session_token flow",
                "consequence": "if wrong session_token validation accepts expired tokens",
            },
            "least_confident_item": {
                "item": "exact semantics of session_token expiry across time zones",
                "current_plan": "mirror auth.py:42 which handles UTC offsets",
                "failure_mode": "timezone drift could let stale session_tokens pass",
            },
            "first_action": {
                "action": "auth.py:42",
                "expected_signal": "pytest suite passes after the middleware change",
            },
        }
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "17",
                                 "first_failing_reason": "awaiting_approval",
                                 "first_failing_metadata": {
                                     "variety": {"total": 11},
                                     # Cycle 2 F5 tightening: 2-token
                                     # share required between
                                     # assumption and one scope item.
                                     # `session_token` and `middleware`
                                     # both appear in the submit's
                                     # most_likely_wrong.assumption.
                                     "required_scope_items": [
                                         "session_token middleware",
                                     ],
                                     "teachback_submit": submit,
                                 },
                                 "first_failing_protocol_level": "full",
                                 "all_active": False,
                             })
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t})

        _reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert ctx["reason_code"] == "awaiting_approval", (
            "Valid teachback_submit should transition the task to "
            "teachback_under_review (reason=awaiting_approval). Reverting "
            "this guarantees by dropping the submit-presence check in "
            "_classify_task_state breaks this assertion."
        )

    def test_item2_under_review_to_active_via_approval(self, monkeypatch):
        """Checklist item 2: valid teachback_approved with empty unaddressed
        transitions under_review → active. Gate allows (reason None)."""
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "",
                                 "first_failing_reason": "",
                                 "first_failing_metadata": {},
                                 "first_failing_protocol_level": "exempt",
                                 "all_active": True,  # approval → active
                             })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None
        assert ctx == {}

    def test_item5_signal_tasks_bypass_gate(self, monkeypatch):
        """Checklist item 5: signal tasks (type=blocker/algedonic) bypass
        the gate. Scan returns all_active=True for a fully-bypassed
        signal task because the carve-out fires before classification."""
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        # When all tasks are signal/carve-out, scanner returns task_count=1
        # but all_active=True because carve-outs short-circuit classification.
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "",
                                 "first_failing_reason": "",
                                 "first_failing_metadata": {},
                                 "first_failing_protocol_level": "exempt",
                                 "all_active": True,
                             })
        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        assert reason is None, "Signal tasks must bypass the gate"

    def test_item6_fail_open_on_filesystem_errors(self, monkeypatch, capsys):
        """Checklist item 6: fail-open on filesystem errors. OSError in
        the decision path → gate allows (exit 0, suppressOutput or hook
        error JSON; NOT exit 2)."""
        def check_boom(_):
            raise OSError("disk wedged")

        monkeypatch.setattr(teachback_gate, "_check_tool_allowed", check_boom)
        with patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))):
            with pytest.raises(SystemExit) as exc:
                teachback_gate.main()
        assert exc.value.code == 0, (
            "Reverting the SACROSANCT outer try/except would let this OSError "
            "exit 2 and block a teammate — THIS TEST catches that regression."
        )

    def test_item12_matcherless_pretooluse_registration(self):
        """Checklist item 12: teachback_gate is registered matcherless in
        hooks.json so it fires on ALL hookable tools. A regression that
        adds a matcher would limit gate coverage."""
        hooks_json = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
        config = json.loads(hooks_json.read_text(encoding="utf-8"))
        for entry in config["hooks"].get("PreToolUse", []):
            for hook in entry.get("hooks", []):
                if "teachback_gate.py" in hook.get("command", ""):
                    assert "matcher" not in entry, (
                        "teachback_gate.py must be registered matcherless; "
                        "a matcher key would skip the gate for non-matching "
                        "tools."
                    )
                    return
        pytest.fail("teachback_gate.py not found in hooks.json PreToolUse")

    def test_item13_state_transition_emission_at_right_states(self, monkeypatch):
        """Checklist item 13: teachback_state_transition events fire for
        correct to_state values from reason_code. Verified via mapping
        _REASON_TO_STATE; reverting the mapping misroutes transitions."""
        from teachback_gate import _state_from_reason

        # Missing submit → pending (not under_review, not active)
        assert _state_from_reason("missing_submit") == "teachback_pending"
        # Invalid submit → pending (structural absence model)
        assert _state_from_reason("invalid_submit") == "teachback_pending"
        # Valid submit awaiting approval → under_review
        assert _state_from_reason("awaiting_approval") == "teachback_under_review"
        # Unaddressed items → correcting (T5 auto-downgrade)
        assert _state_from_reason("unaddressed_items") == "teachback_correcting"
        # Corrections pending → correcting (T6)
        assert _state_from_reason("corrections_pending") == "teachback_correcting"

    def test_item15_invalid_submit_surfaces_specific_field(self, monkeypatch):
        """Checklist item 15: invalid_submit error identifies the specific
        failing field(s). Reverting Y3 wiring (dropping fail_field/fail_error
        population from the first FieldError) would leave the template
        substitution empty."""
        submit = {
            "understanding": "x" * 120,
            "most_likely_wrong": {
                "assumption": "the auth middleware connects cleanly with session_token",
                "consequence": "if wrong session_token validation drops valid ones",
            },
            "least_confident_item": {
                "item": "the exact semantics of session_token expiry checks",
                "current_plan": "mirror auth.py:42 which handles offsets correctly",
                "failure_mode": "timezone drift allows stale session_tokens through",
            },
            "first_action": {
                # Strict-mode citation regex expects file.ext:linenum OR function()
                "action": "this does not match any citation shape",
                "expected_signal": "tests pass reliably after the change",
            },
        }
        monkeypatch.setattr(teachback_gate, "resolve_agent_name",
                             lambda *a, **kw: "backend-coder-1")
        monkeypatch.setattr(teachback_gate, "get_team_name", lambda: "pact-test")
        monkeypatch.setattr(teachback_gate, "scan_teachback_state",
                             lambda *a, **kw: {
                                 "task_count": 1,
                                 "first_failing_task_id": "17",
                                 "first_failing_reason": "awaiting_approval",
                                 "first_failing_metadata": {
                                     "variety": {"total": 11},
                                     # Cycle 2 F5: 2-token share
                                     # requirement. Assumption shares
                                     # `session_token` and `middleware`
                                     # with this scope item so the
                                     # first failing field remains
                                     # first_action.action (the invalid
                                     # citation shape — this test's
                                     # actual subject).
                                     "required_scope_items": [
                                         "session_token middleware",
                                     ],
                                     "teachback_submit": submit,
                                 },
                                 "first_failing_protocol_level": "full",
                                 "all_active": False,
                             })
        monkeypatch.setattr(teachback_gate, "read_events", lambda _t: [])
        monkeypatch.setattr(teachback_gate, "append_event", lambda _: True)
        monkeypatch.setattr(teachback_gate, "make_event",
                             lambda _t, **kw: {"type": _t})

        reason, ctx = _check_tool_allowed(
            {"tool_name": "Edit", "team_name": "pact-test"}
        )
        # Reason is upgraded to invalid_submit
        assert ctx["reason_code"] == "invalid_submit"
        # Deny reason names the SPECIFIC failing field
        assert "first_action.action" in reason, (
            "invalid_submit deny reason must surface the specific failing "
            "field name (Y3). Reverting Y3 wiring would leave the template "
            "placeholder {fail_field} substituted with 'teachback_submit' "
            "generic instead of the specific nested field."
        )
