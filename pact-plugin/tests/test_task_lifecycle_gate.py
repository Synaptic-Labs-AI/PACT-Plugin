"""
Comprehensive coverage for task_lifecycle_gate.py — #662 PostToolUse hook.

Sibling to test_task_lifecycle_gate_smoke.py (the 6 minimum-viable cases).
This file expands every F-row landed in the gate.

F-row coverage (architect §6 + impl reality):
  F8  — TaskCreate TEACHBACK without addBlocks=[B_id]            → advisory
  F9  — TaskCreate pact-* non-TEACHBACK without addBlockedBy     → advisory
  F10 — TaskUpdate(completed) Task A without paired SendMessage  → advisory
        Boundary tested at 119s (silent) and 121s (advisory).
  F11 — TaskUpdate(completed) pact-* Task B without metadata.handoff → advisory
  F12 — Teammate self-completes Task → advisory + writeback
        Carve-outs: secretary self-complete (SELF_COMPLETE_EXEMPT_AGENTS),
        signal task (metadata.completion_type=signal — exempt via the
        is_self_complete_exempt predicate), recursion-marker skip.
        Sketch-A: actor unresolvable → CURRENT skip behavior; encoded with
        explicit deviation-documenting test referencing architect §5.3.
  F13 — TaskUpdate(completed) with malformed metadata.handoff → advisory
        (disjoint from F11 — handoff present but schema-incomplete).
  F21 — module-load failure → advisory + hookEventName=PostToolUse + exit 0
  Anti-sprawl — single evaluate_lifecycle composition.

Disciplines applied:
  - PR #660 R2: never pop shared.* from sys.modules in this test process.
  - F-row references = functional categories per architect §6, not
    provenance — kept per `feedback_no_planning_artifact_test_names`.
"""

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402


# =============================================================================
# Helpers
# =============================================================================


def _stdin(payload: dict) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def _capture_main(payload: dict, capsys) -> tuple[int, dict | None]:
    with patch.object(sys, "stdin", _stdin(payload)):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    raw_code = exc.value.code if exc.value.code is not None else 0
    code = int(raw_code) if isinstance(raw_code, int) else 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out) if out else None
    return code, parsed


# =============================================================================
# F8 — TEACHBACK Task without addBlocks=[B_id]
# =============================================================================


def test_f8_silent_when_teachback_carries_addblocks(pact_context):
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "preparer: TEACHBACK for foo",
            "owner": "pact-preparer",
            "addBlocks": ["42"],
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent (F8 satisfied), got: {advisories}"
    )


# =============================================================================
# F9 — pact-* non-TEACHBACK Task without addBlockedBy=[A_id]
# =============================================================================


def test_f9_silent_when_work_task_carries_addblockedby(pact_context):
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "work_addblockedby_missing" for rule, _ in advisories)


def test_f9_silent_when_owner_is_not_pact_specialist(pact_context):
    """Non-pact-* owner doesn't trigger F9 even without addBlockedBy."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "lead-only task",
            "owner": "team-lead",
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "work_addblockedby_missing" for rule, _ in advisories)


# =============================================================================
# F10 — Teachback completion without paired SendMessage (120s window)
# =============================================================================


def _setup_team_inbox(
    tmp_path: Path,
    monkeypatch,
    owner: str,
    team_name: str,
    paired_offset_seconds: float | None,
):
    """Seed ~/.claude/teams/{team_name}/inboxes/{owner}.json with one message
    from team-lead at `now - paired_offset_seconds`. None → empty inbox.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    inbox_dir = tmp_path / ".claude" / "teams" / team_name / "inboxes"
    inbox_dir.mkdir(parents=True)
    if paired_offset_seconds is not None:
        ts = datetime.now(timezone.utc).timestamp() - paired_offset_seconds
        ts_iso = (
            datetime.fromtimestamp(ts, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        messages = [
            {
                "from": "team-lead",
                "text": "completion ack",
                "timestamp": ts_iso,
            }
        ]
    else:
        messages = []
    (inbox_dir / f"{owner}.json").write_text(
        json.dumps(messages), encoding="utf-8"
    )


def test_f10_silent_when_paired_sendmessage_within_window(
    tmp_path, monkeypatch, pact_context
):
    """Paired SendMessage 30s ago (well within 120s) → no F10 advisory."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "completion_no_paired_send" for rule, _ in advisories), (
        f"expected silent within 120s window, got: {advisories}"
    )


def test_f10_silent_at_119s_boundary(tmp_path, monkeypatch, pact_context):
    """119s ago is still within the 120s window → silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=119,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "completion_no_paired_send" for rule, _ in advisories)


def test_f10_advisory_at_121s_boundary(tmp_path, monkeypatch, pact_context):
    """121s ago is outside the 120s window → F10 fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=121,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "completion_no_paired_send" for rule, _ in advisories), (
        f"expected F10 outside window, got: {advisories}"
    )


def test_f10_advisory_when_inbox_empty(tmp_path, monkeypatch, pact_context):
    """No paired SendMessage at all → F10 fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=None,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "completion_no_paired_send" for rule, _ in advisories)


# =============================================================================
# F11 — pact-* work-Task completed with empty metadata.handoff
# =============================================================================


def test_f11_silent_when_handoff_well_formed(pact_context):
    """Valid handoff schema → no F11 and no F13."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "42", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "42",
                "subject": "implement foo",
                "owner": "pact-backend-coder",
                "metadata": {
                    "handoff": {
                        "produced": "x",
                        "decisions": "x",
                        "reasoning_chain": "x",
                        "uncertainty": "x",
                        "integration": "x",
                        "open_questions": "x",
                    }
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "handoff_missing" for rule, _ in advisories)
    assert not any(rule == "handoff_schema_invalid" for rule, _ in advisories)


# =============================================================================
# F13 — handoff present but schema malformed (disjoint from F11)
# =============================================================================


@pytest.mark.parametrize(
    "missing_field",
    [
        "produced",
        "decisions",
        "reasoning_chain",
        "uncertainty",
        "integration",
        "open_questions",
    ],
)
def test_f13_advisory_for_each_missing_required_field(missing_field, pact_context):
    """Handoff present but missing one required field → F13. Disjoint
    from F11 — F11 fires only on missing/empty handoff payload entirely.
    """
    pact_context(team_name="test-team", session_id="test-session")
    full_handoff = {
        "produced": "x",
        "decisions": "x",
        "reasoning_chain": "x",
        "uncertainty": "x",
        "integration": "x",
        "open_questions": "x",
    }
    full_handoff.pop(missing_field)
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "42", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "42",
                "subject": "implement foo",
                "owner": "pact-backend-coder",
                "metadata": {"handoff": full_handoff},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "handoff_schema_invalid" for rule, _ in advisories), (
        f"expected F13 advisory for missing {missing_field}, got: {advisories}"
    )
    # F11 must NOT also fire — disjoint per impl §289 / lead clarification.
    assert not any(rule == "handoff_missing" for rule, _ in advisories)


def test_f13_advisory_when_handoff_is_non_dict(pact_context):
    """metadata.handoff is a string instead of a dict → F13 advisory."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "42", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "42",
                "subject": "implement foo",
                "owner": "pact-backend-coder",
                "metadata": {"handoff": "just a string"},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "handoff_schema_invalid" for rule, _ in advisories)


# =============================================================================
# F12 carve-outs — secretary, signal task, recursion marker, unresolvable actor
# =============================================================================


def test_f12_silent_when_secretary_self_completes(pact_context):
    """Secretary owner is in SELF_COMPLETE_EXEMPT_AGENTS → no advisory."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "secretary@test-team",
        "tool_input": {"taskId": "5", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "5",
                "subject": "save institutional memory",
                "owner": "secretary",
                "metadata": {
                    "handoff": {
                        "produced": "x",
                        "decisions": "x",
                        "reasoning_chain": "x",
                        "uncertainty": "x",
                        "integration": "x",
                        "open_questions": "x",
                    }
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "self_completion" for rule, _ in advisories)


def test_f12_silent_when_signal_task_self_completes(pact_context):
    """Signal task (metadata.completion_type='signal' AND
    metadata.type in {'blocker','algedonic'}) is exempted by
    is_self_complete_exempt(task) per shared.intentional_wait L201-L204.
    """
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "backend-coder-3@test-team",
        "tool_input": {"taskId": "6", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "6",
                "subject": "signal: ack",
                "owner": "backend-coder-3",
                "metadata": {
                    "completion_type": "signal",
                    "type": "blocker",
                    "handoff": {
                        "produced": "x",
                        "decisions": "x",
                        "reasoning_chain": "x",
                        "uncertainty": "x",
                        "integration": "x",
                        "open_questions": "x",
                    },
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "self_completion" for rule, _ in advisories)


def test_f12_skips_when_actor_unresolvable_documents_architect_5_3_deviation(
    pact_context,
):
    """Sketch-A deviation: architect §5.3 specifies that when
    trustworthy_actor_name returns None (no agent_id, or no '@' in
    agent_id), the gate should still emit an F12 advisory.

    The CURRENT implementation (task_lifecycle_gate.py L341 condition
    `actor is not None`) skips the advisory in that case.

    This test encodes the CURRENT skip behavior so a future change
    surfaces the deviation deliberately. Resolution tracked in a follow-up
    issue (filed at stage-ready). DO NOT 'fix' the gate to satisfy this
    test — fix the test only if the architect §5.3 reconciliation lands.
    """
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        # No agent_id at all → trustworthy_actor_name returns None.
        "tool_input": {"taskId": "7", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "7",
                "subject": "implement foo",
                "owner": "backend-coder-3",
                "metadata": {
                    "handoff": {
                        "produced": "x",
                        "decisions": "x",
                        "reasoning_chain": "x",
                        "uncertainty": "x",
                        "integration": "x",
                        "open_questions": "x",
                    }
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    # Architect §5.3 would expect F12; current impl skips. Assert SKIP.
    assert not any(rule == "self_completion" for rule, _ in advisories), (
        "If F12 fired here, the gate has been changed to match architect "
        "§5.3 (advisory-emit on unresolvable actor). Confirm the change "
        "was intentional and update this test + close the follow-up issue."
    )


# =============================================================================
# F12 — lead-driven completion is silent (actor != owner)
# =============================================================================


def test_f12_silent_when_lead_completes_teammates_task(pact_context):
    """team-lead@test-team completing a teammate's task → not F12."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "team-lead@test-team",
        "tool_input": {"taskId": "8", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "8",
                "subject": "implement foo",
                "owner": "backend-coder-3",
                "metadata": {
                    "handoff": {
                        "produced": "x",
                        "decisions": "x",
                        "reasoning_chain": "x",
                        "uncertainty": "x",
                        "integration": "x",
                        "open_questions": "x",
                    }
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "self_completion" for rule, _ in advisories)


# =============================================================================
# F21 — module-load advisory contract (smoke covers the full helper invoke)
# =============================================================================


def test_f21_runtime_advisory_carries_post_tool_use_event_name(capsys):
    """Direct invocation of _emit_load_failure_advisory under simulated
    runtime exception → exit 0 (PostToolUse cannot DENY) + hookEventName
    'PostToolUse' in the output. Mirrors smoke S6 with broader assertion.
    """
    err = RuntimeError("simulated runtime fail")
    with pytest.raises(SystemExit) as exc:
        tlg._emit_load_failure_advisory("runtime", err)
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out.strip())
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "additionalContext" in hso
    assert "runtime" in hso["additionalContext"]
    assert "RuntimeError" in hso["additionalContext"]


# =============================================================================
# Anti-sprawl invariant
# =============================================================================


def test_evaluate_lifecycle_is_single_composition_function():
    """Auditor §11 YELLOW: gate file is 429 LOC. Pin that the F-row rules
    compose in a single decision function rather than fragmenting.
    """
    import inspect

    public_evaluate_fns = [
        name
        for name, obj in inspect.getmembers(tlg, inspect.isfunction)
        if name.startswith("evaluate_") and not name.startswith("_")
    ]
    assert public_evaluate_fns == ["evaluate_lifecycle"], (
        f"expected single evaluate_lifecycle, got {public_evaluate_fns}"
    )
    forbidden_prefixes = (
        "_evaluate_f",
        "_f8_",
        "_f9_",
        "_f10_",
        "_f11_",
        "_f12_",
        "_f13_",
    )
    fn_names = [
        name for name, _ in inspect.getmembers(tlg, inspect.isfunction)
    ]
    sprawl = [
        n for n in fn_names if any(n.startswith(p) for p in forbidden_prefixes)
    ]
    assert not sprawl, f"per-F-row sprawl detected: {sprawl}"


# =============================================================================
# Defensive: malformed stdin / non-target tool / empty advisories path
# =============================================================================


def test_main_no_op_for_unrelated_tool(capsys):
    """matcher should already restrict, but defensive belt: tool_name='Read'
    → suppressOutput, exit 0.
    """
    code, out = _capture_main({"tool_name": "Read"}, capsys)
    assert code == 0
    assert out == {"suppressOutput": True}


def test_main_no_op_on_malformed_stdin(capsys):
    """Malformed JSON → fail-OPEN with suppressOutput."""
    with patch.object(sys, "stdin", io.StringIO("not json")):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {"suppressOutput": True}
