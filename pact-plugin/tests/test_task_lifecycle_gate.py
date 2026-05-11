"""
Comprehensive coverage for task_lifecycle_gate.py — #662 PostToolUse hook.

Sibling to test_task_lifecycle_gate_smoke.py (the 6 minimum-viable cases).
This file expands every rule landed in the gate.

Rule coverage:
  - teachback_addblocks_missing — TaskCreate TEACHBACK without
    addBlocks=[<work_task_id>] → advisory
  - work_addblockedby_missing — TaskCreate pact-* non-TEACHBACK
    without addBlockedBy → advisory
  - completion_no_paired_send — TaskUpdate(completed) teachback
    Task without paired SendMessage → advisory
        Boundary tested at 119s (silent) and 121s (advisory).
  - handoff_missing — TaskUpdate(completed) pact-* work Task
    without metadata.handoff → advisory
  - self_completion — Teammate self-completes Task → advisory +
    completion_disputed writeback
        Carve-outs: secretary self-complete (team-config agentType in
        SELF_COMPLETE_EXEMPT_AGENT_TYPES, resolved via the
        is_self_complete_exempt predicate), signal task
        (metadata.completion_type=signal — also via the predicate),
        recursion-marker skip.
        Sketch-A: actor unresolvable → CURRENT skip behavior; encoded with
        explicit deviation-documenting test referencing architect §5.3.
  - handoff_schema_invalid — TaskUpdate(completed) with malformed
    metadata.handoff → advisory (disjoint from handoff_missing —
    handoff present but schema-incomplete).
  - module-load failure → advisory + hookEventName=PostToolUse + exit 0
  Anti-sprawl — single evaluate_lifecycle composition.

Disciplines applied:
  - PR #660 R2: never pop shared.* from sys.modules in this test process.
  - Rule names describe behavior, not provenance — per
    `feedback_no_planning_artifact_test_names`.
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
# teachback_addblocks_missing — TEACHBACK Task without addBlocks=[<work_task_id>]
# =============================================================================


def test_silent_when_teachback_carries_addblocks(pact_context):
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
        f"expected silent (teachback_addblocks_missing satisfied), got: {advisories}"
    )


# =============================================================================
# work_addblockedby_missing — pact-* non-TEACHBACK Task without addBlockedBy=[<teachback_id>]
# =============================================================================


def test_silent_when_work_task_carries_addblockedby(pact_context):
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


def test_silent_when_owner_is_not_pact_specialist(pact_context):
    """Non-pact-* owner doesn't trigger work_addblockedby_missing even without addBlockedBy."""
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


def test_silent_when_owner_is_teachback_exempt_secretary(tmp_path, monkeypatch, pact_context):
    """Secretary owner with agentType in TEACHBACK_EXEMPT_AGENT_TYPES → no
    work_addblockedby_missing advisory even without addBlockedBy. Resolution
    via team-config agentType lookup (mirrors the self-completion carve-out
    fixture pattern at test_silent_when_secretary_self_completes)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "pact-secretary", "agentType": "pact-secretary"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "secretary: session briefing + HANDOFF readiness",
            "owner": "pact-secretary",
            # no addBlockedBy — single-task dispatch shape
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "work_addblockedby_missing" for rule, _ in advisories)


def test_advisory_when_pact_owner_is_not_teachback_exempt(tmp_path, monkeypatch, pact_context):
    """A pact-* owner whose team-config agentType is NOT in
    TEACHBACK_EXEMPT_AGENT_TYPES still fires work_addblockedby_missing —
    regression protection for the unchanged majority path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "pact-backend-coder", "agentType": "pact-backend-coder"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement feature X",
            "owner": "pact-backend-coder",
            # no addBlockedBy
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "work_addblockedby_missing" for rule, _ in advisories)


def test_silent_when_teachback_exempt_resolves_via_team_config_agent_type(
    tmp_path, monkeypatch, pact_context
):
    """Spawn-name independence: owner='session-secretary' (non-canonical
    spawn name) with team-config agentType='pact-secretary' still reaches
    the exemption. The carve-out keys on agentType, not owner-name match."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "pact-session-secretary", "agentType": "pact-secretary"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "session-secretary: harvest HANDOFFs",
            "owner": "pact-session-secretary",
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "work_addblockedby_missing" for rule, _ in advisories)


def test_teachback_addblocks_missing_still_fires_for_stray_secretary_teachback(
    tmp_path, monkeypatch, pact_context
):
    """Defensive: the teachback_addblocks_missing rule is independent of the
    new exemption. A stray teachback-subject task for the secretary still
    triggers the addBlocks advisory — exemption applies only to the
    work_addblockedby_missing rule."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "pact-secretary", "agentType": "pact-secretary"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "secretary: TEACHBACK for some task",
            "owner": "pact-secretary",
            # no addBlocks
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "teachback_addblocks_missing" for rule, _ in advisories)


def test_advisory_when_pact_owner_not_in_members_fails_closed(tmp_path, monkeypatch, pact_context):
    """Fail-closed: owner doesn't match any member.name in team config →
    is_teachback_exempt returns False → advisory still fires. The pinned
    property is fail-closed-on-member-miss; the spoof-defense framing is
    one motivation, not the property itself."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "other-agent", "agentType": "pact-secretary"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "spoof: pact-secretary work",
            "owner": "pact-secretary",  # owner doesn't match any member.name
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "work_addblockedby_missing" for rule, _ in advisories)


# =============================================================================
# completion_no_paired_send — teachback completion without paired SendMessage (120s window)
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


def test_silent_when_paired_sendmessage_within_window(
    tmp_path, monkeypatch, pact_context
):
    """Paired SendMessage 30s ago (well within 120s) → no completion_no_paired_send advisory."""
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


def test_silent_at_119s_boundary(tmp_path, monkeypatch, pact_context):
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


def test_advisory_at_121s_boundary(tmp_path, monkeypatch, pact_context):
    """121s ago is outside the 120s window → completion_no_paired_send fires."""
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
        f"expected completion_no_paired_send outside window, got: {advisories}"
    )


def test_advisory_when_inbox_empty(tmp_path, monkeypatch, pact_context):
    """No paired SendMessage at all → completion_no_paired_send fires."""
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
# handoff_missing — pact-* work-Task completed with empty metadata.handoff
# =============================================================================


def test_silent_when_handoff_well_formed(pact_context):
    """Valid handoff schema → no handoff_missing and no handoff_schema_invalid."""
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


@pytest.mark.parametrize(
    "metadata_shape",
    [
        {},  # absent metadata.handoff
        {"handoff": {}},  # empty-dict metadata.handoff
        {"handoff": None},  # explicit-null metadata.handoff
    ],
    ids=["absent", "empty_dict", "null"],
)
def test_advisory_when_pact_work_task_completes_without_handoff(
    metadata_shape, pact_context,
):
    """A pact-* work Task that transitions to status=completed without a
    metadata.handoff payload fires the handoff_missing advisory. Covers
    three variants of "no handoff": absent key, empty dict, explicit
    null. The schema-invalid rule must NOT also fire — handoff_missing
    and handoff_schema_invalid are disjoint per the gate contract.
    """
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "42", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "42",
                "subject": "implement foo",
                "owner": "pact-backend-coder",
                "metadata": metadata_shape,
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "handoff_missing" for rule, _ in advisories), (
        f"expected handoff_missing advisory for {metadata_shape}, "
        f"got: {advisories}"
    )
    assert not any(rule == "handoff_schema_invalid" for rule, _ in advisories), (
        "handoff_missing and handoff_schema_invalid must be disjoint; "
        f"both fired for {metadata_shape}"
    )


# =============================================================================
# handoff_schema_invalid — handoff present but schema malformed (disjoint from handoff_missing)
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
def test_advisory_for_each_missing_required_field(missing_field, pact_context):
    """Handoff present but missing one required field → handoff_schema_invalid. Disjoint
    from handoff_missing — handoff_missing fires only on missing/empty handoff payload entirely.
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
        f"expected handoff_schema_invalid advisory for missing {missing_field}, got: {advisories}"
    )
    # handoff_missing must NOT also fire — disjoint per impl / lead clarification.
    assert not any(rule == "handoff_missing" for rule, _ in advisories)


def test_advisory_when_handoff_is_non_dict(pact_context):
    """metadata.handoff is a string instead of a dict → handoff_schema_invalid advisory."""
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
# self_completion carve-outs — secretary, signal task, recursion marker, unresolvable actor
# =============================================================================


def test_silent_when_secretary_self_completes(tmp_path, monkeypatch, pact_context):
    """Secretary's team-config agentType is in SELF_COMPLETE_EXEMPT_AGENT_TYPES
    → no advisory. Spawn name is `session-secretary` (production shape);
    the carve-out resolves via team config, not owner-name match."""
    # Wire pact_context to read tmp_path-rooted team config.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "session-secretary@test-team",
        "tool_input": {"taskId": "5", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "5",
                "subject": "save institutional memory",
                "owner": "session-secretary",
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


def test_advisory_when_owner_named_secretary_without_agenttype(
    tmp_path, monkeypatch, pact_context
):
    """Trust-boundary defense: a teammate spoofing owner='secretary'
    without the team config recording the privileged agentType DOES
    trigger the self-completion advisory. Pre-#682 this test would have
    been silent (owner-name carve-out); post-#682 the carve-out keys on
    team-config agentType, so the spoof is caught."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    team_dir = tmp_path / ".claude" / "teams" / "test-team"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "members": [
                {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
            ],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "secretary@test-team",
        "tool_input": {"taskId": "9", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "9",
                "subject": "spoof attempt",
                "owner": "secretary",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "self_completion" for rule, _ in advisories)


def test_silent_when_signal_task_self_completes(pact_context):
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


def test_skips_when_actor_unresolvable(
    pact_context,
):
    """Documents an intentional deviation from architect §5.3: that
    spec says when ``trustworthy_actor_name`` returns None (no
    agent_id, or no ``@`` in agent_id), the gate should still emit a
    self-completion advisory.

    The CURRENT implementation (task_lifecycle_gate.py condition
    ``actor is not None``) skips the advisory in that case.

    This test encodes the CURRENT skip behavior so a future change
    surfaces the deviation deliberately. Resolution tracked in a
    follow-up issue (filed at stage-ready). DO NOT 'fix' the gate to
    satisfy this test — fix the test only if the architect §5.3
    reconciliation lands.
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
    # Architect §5.3 would expect a self_completion advisory; current impl skips. Assert SKIP.
    assert not any(rule == "self_completion" for rule, _ in advisories), (
        "If self_completion fired here, the gate has been changed to match architect "
        "§5.3 (advisory-emit on unresolvable actor). Confirm the change "
        "was intentional and update this test + close the follow-up issue."
    )


# =============================================================================
# self_completion — lead-driven completion is silent (actor != owner)
# =============================================================================


def test_silent_when_lead_completes_teammates_task(pact_context):
    """team-lead@test-team completing a teammate's task → not self_completion."""
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
# module-load advisory contract (smoke covers the full helper invoke)
# =============================================================================


def test_runtime_advisory_carries_post_tool_use_event_name(capsys):
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


# =============================================================================
# Defensive fallback: `tool_response or tool_output or {}`
# =============================================================================
#
# evaluate_lifecycle reads the post-state task via:
#     tool_response = input_data.get("tool_response") or input_data.get("tool_output") or {}
#
# The `or tool_output` fallback covers (a) legacy/captured-from-production
# fixtures whose envelope predates the canonical `tool_response` rename,
# and (b) any future platform envelope rename. The 4-line comment in
# task_lifecycle_gate.py documents the intent; this test enforces it.
#
# If a future "cleanup" PR removes the `or tool_output` branch, the
# test_legacy_envelope_extracts_via_fallback case fails — which is the
# enforcement-mechanism prose alone cannot provide.


def test_legacy_envelope_extracts_via_fallback():
    """Legacy `tool_output` envelope (no `tool_response`) → handoff_missing fires.

    Constructs a payload with NO `tool_response` field and the task data
    under `tool_output` (the pre-rename envelope shape). evaluate_lifecycle
    must extract the task via the `or tool_output` fallback; the
    handoff_missing rule then fires because the work task carries no
    metadata.handoff.

    A regression that strips the `or tool_output` branch causes
    tool_response to resolve to {}, task.get("subject") to be empty, and
    handoff_missing to NOT fire (the rule is gated on subject + owner
    being a pact-* work task) — which would leak past this assertion.
    """
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        # Legacy envelope shape — pre-rename. NO `tool_response` key.
        "tool_output": {
            "task": {
                "id": "1",
                "subject": "pact-backend-coder: implement foo",
                "owner": "pact-backend-coder",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "handoff_missing" for rule, _ in advisories), (
        f"expected handoff_missing via tool_output fallback, got: {advisories}"
    )


def test_canonical_tool_response_takes_precedence_over_legacy():
    """Both `tool_response` and `tool_output` present → canonical wins.

    The `or` short-circuits: when `tool_response` is a truthy dict, the
    fallback is never consulted. This pins the precedence so a refactor
    that swapped the operands (e.g., to `tool_output or tool_response`)
    would silently make legacy data shadow canonical data — caught here
    by injecting DIFFERENT subjects in each envelope and asserting the
    advisory reflects the canonical one.
    """
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        # Canonical envelope: pact-* work task, no handoff → handoff_missing fires.
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "pact-backend-coder: implement canonical",
                "owner": "pact-backend-coder",
                "metadata": {},
            }
        },
        # Legacy envelope: non-pact owner → handoff_missing would NOT fire if read.
        "tool_output": {
            "task": {
                "id": "1",
                "subject": "user: random task",
                "owner": "user",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    # Canonical was read → backend-coder pact-* work task → handoff_missing fires.
    assert any(rule == "handoff_missing" for rule, _ in advisories), (
        "canonical tool_response must take precedence over legacy tool_output"
    )
