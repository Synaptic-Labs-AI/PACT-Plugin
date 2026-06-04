"""
Comprehensive coverage for task_lifecycle_gate.py — #662 PostToolUse hook.

Sibling to test_task_lifecycle_gate_smoke.py (the 6 minimum-viable cases).
This file expands every rule landed in the gate.

Rule coverage:
  - teachback_addblocks_missing — TaskCreate TEACHBACK without
    addBlocks=[<work_task_id>] → advisory
  - work_addblockedby_missing — TaskCreate pact-* non-TEACHBACK
    without addBlockedBy → advisory
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
# teachback_addblocks_missing — TaskUpdate-wiring-time rule
#
# The rule fires at the canonical Step-3 wiring TaskUpdate (lead sets owner
# on a teachback Task) when the update lands without paired
# addBlocks=[<work_task_id>] AND the on-disk task_a record does not already
# carry blocks (benign late-wiring guard). Re-times the historical
# TaskCreate-time check, which was structurally unsatisfiable because the
# work-task id did not exist yet at TaskCreate(A) time.
#
# Fixture discipline: the wiring-boundary check calls
# read_task_json(task_id, team_name) at the top of the write-time branch
# (fires on EVERY non-completed TaskUpdate, not only those carrying
# teachback_submit metadata). Tests must seed ~/.claude/tasks/{team}/{id}.json
# under tmp_path via monkeypatch.setattr(Path, "home", lambda: tmp_path) so
# that the disk read resolves the teachback subject + blocks fields.
# =============================================================================


def _seed_task_a(tmp_path: Path, team_name: str, task_id: str, **fields) -> None:
    """Write a task .json under the test-scoped HOME so the lifted
    `read_task_json(task_id, team_name)` call inside the write-time
    TaskUpdate branch resolves to a real on-disk record.
    """
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_silent_when_owner_wiring_carries_addblocks(tmp_path, monkeypatch, pact_context):
    """Canonical Step-3 wiring TaskUpdate pairs owner + addBlocks in one
    update — no advisory at the wiring boundary."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="preparer: TEACHBACK for foo",
        status="pending",
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            "addBlocks": ["42"],
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent (owner+addBlocks paired in wiring update), got: {advisories}"
    )


def test_fires_on_wiring_update_without_addblocks(tmp_path, monkeypatch, pact_context):
    """Positive: the canonical owner-wiring TaskUpdate lands without paired
    addBlocks AND task_a has no pre-existing blocks → advisory fires.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="preparer: TEACHBACK for foo",
        status="pending",
        blocks=[],
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            # no addBlocks
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected teachback_addblocks_missing on owner-only wiring, got: {advisories}"
    )


def test_silent_when_task_a_already_has_blocks(tmp_path, monkeypatch, pact_context):
    """Benign late-wiring guard: an earlier TaskUpdate already wired
    blocks; a later owner-only update is not a violation."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="preparer: TEACHBACK for foo",
        status="pending",
        blocks=["42"],
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            # no addBlocks — but task_a.blocks already populated
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent (task_a.blocks already wired), got: {advisories}"
    )


def test_silent_when_subject_is_not_teachback(tmp_path, monkeypatch, pact_context):
    """Subject gate: a work-task subject (non-teachback) carrying owner
    without addBlocks does not trip the teachback-specific rule."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "B",
        subject="preparer: implement feature X",
        status="pending",
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "B",
            "owner": "pact-preparer",
            # no addBlocks — but subject is not a teachback subject
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent on non-teachback subject, got: {advisories}"
    )


def test_silent_when_owner_not_provided_on_teachback_subject(tmp_path, monkeypatch, pact_context):
    """Clause-2 coverage: the rule requires `tool_input.get('owner')` as
    one of the four AND clauses. A TaskUpdate that touches a teachback
    subject WITHOUT setting owner (e.g., a status-change update or an
    addBlocks-only update) must NOT trip the rule — the rule fires
    specifically on the canonical Step-3 owner-wiring update, not on
    arbitrary mutations to teachback-subject tasks.

    Phantom-green protection: if a future refactor drops the owner check
    (simplifying to `is_teachback AND NOT addBlocks AND NOT blocks_on_disk`),
    this test catches the regression."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="preparer: TEACHBACK for foo",
        status="pending",
        blocks=[],
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "addBlocks": ["42"],
            # owner deliberately omitted — exercises the clause-2 short-circuit
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent on owner-less TaskUpdate, got: {advisories}"
    )


def test_silent_when_status_completed_on_teachback_subject(tmp_path, monkeypatch, pact_context):
    """Defense-in-depth: the write-time TaskUpdate branch carrying the
    teachback_addblocks_missing rule is gated by `status != 'completed'`.
    A completion-time TaskUpdate (status=completed) must NOT trip the
    wiring-boundary rule — completion-time rules (teachback_submit checks,
    paired-send window, etc.) handle that branch in the sibling
    `if tool_name == 'TaskUpdate' and tool_input.get('status') == 'completed':`
    block. A regression that moves the new rule outside the status guard
    would fire on every completion of a teachback-subject task."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="preparer: TEACHBACK for foo",
        status="pending",
        blocks=[],
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            "status": "completed",
            # no addBlocks — but the status-completed guard suppresses
        },
        "tool_response": {
            "task": {
                "id": "A",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "pact-preparer",
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent on status=completed TaskUpdate, got: {advisories}"
    )


def test_silent_when_team_name_empty(tmp_path, monkeypatch, pact_context):
    """Defense-in-depth: empty team_name short-circuits the
    `read_task_json(task_id, team_name) if team_name else {}` disk read
    at the top of the write-time branch. With task_a = {}, subject = ""
    → `_is_teachback_subject("")` is False → clause 1 fails → rule silent.
    A regression dropping the `if team_name` guard would route through
    read_task_json with an empty team and potentially misbehave depending
    on the function's empty-team-name semantics."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            # owner set, no addBlocks — would fire if subject resolved
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected silent on empty team_name, got: {advisories}"
    )


def test_silent_when_gate_writeback_recursion_marker_present(tmp_path, monkeypatch, pact_context):
    """Defense-in-depth: the recursion guard at evaluate_lifecycle's top
    short-circuits when `tool_input.metadata.gate_writeback is True`. This
    is the gate's own writeback re-entry path; the new wiring-boundary
    rule must never fire on it (and no other rule fires either — the
    guard returns [] immediately). A regression that moves the recursion
    guard below the new rule would emit a spurious advisory on every
    gate-writeback re-entry."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="preparer: TEACHBACK for foo",
        status="pending",
        blocks=[],
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            "metadata": {"gate_writeback": True},
            # no addBlocks — would otherwise fire, but recursion guard preempts
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert advisories == [], (
        f"expected empty advisory list (recursion guard short-circuits), "
        f"got: {advisories}"
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
    secretary exemption. A stray teachback-subject task whose owner-wiring
    TaskUpdate lands without paired addBlocks still triggers the advisory —
    the agentType exemption applies only to work_addblockedby_missing."""
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
    _seed_task_a(
        tmp_path,
        "test-team",
        "A",
        subject="secretary: TEACHBACK for some task",
        status="pending",
        blocks=[],
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
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
# Shared inbox-seeding fixture — used by the teachback/variety tests below to
# seed a team-lead inbox message in isolation. (The gate no longer reads the
# inbox; the seed is now an inert convenience for tests that want a realistic
# team-inbox on disk.)
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


# =============================================================================
# Helpers for per-dispatch variety / teachback_submit tests
# =============================================================================


def _well_formed_variety_ack(value="yes", concern=""):
    """Return a well-formed variety_acknowledgment dict per D10."""
    if value == "yes":
        return {"rationale_articulates_this_dispatch": "yes"}
    return {
        "rationale_articulates_this_dispatch": value,
        "concern": concern or "novelty_rationale appears copied from feature",
    }


def _well_formed_teachback_submit(**overrides):
    """Return a well-formed 5-field teachback_submit payload."""
    payload = {
        "understanding": "x",
        "most_likely_wrong": "x",
        "least_confident_item": "x",
        "first_action": "x",
        "variety_acknowledgment": _well_formed_variety_ack("yes"),
    }
    payload.update(overrides)
    return payload


def _well_formed_variety(**overrides):
    """Return a well-formed metadata.variety dict per D11."""
    payload = {
        "novelty": 2,
        "novelty_rationale": "novelty rationale text",
        "scope": 2,
        "scope_rationale": "scope rationale text",
        "uncertainty": 2,
        "uncertainty_rationale": "uncertainty rationale text",
        "risk": 2,
        "risk_rationale": "risk rationale text",
        "total": 8,
    }
    payload.update(overrides)
    return payload


# =============================================================================
# teachback_submit_missing (R1) — Teachback completion without teachback_submit
# =============================================================================


def test_advisory_when_teachback_completed_without_teachback_submit(
    tmp_path, monkeypatch, pact_context,
):
    """Teachback subject task completed with empty metadata.teachback_submit
    → R1 fires. Disjoint from R2 (no schema check on missing payload).
    Seeds a recent team-lead inbox message so the test exercises R1 against a
    realistic on-disk team inbox (the gate does not read it; the seed is inert)."""
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
    assert any(rule == "teachback_submit_missing" for rule, _ in advisories), (
        f"expected teachback_submit_missing, got: {advisories}"
    )
    # Disjoint with R2
    assert not any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


def test_silent_when_teachback_completed_with_well_formed_submit(
    tmp_path, monkeypatch, pact_context,
):
    """Well-formed 5-field teachback_submit → R1 silent, R2 silent."""
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
                "metadata": {
                    "teachback_submit": _well_formed_teachback_submit(),
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "teachback_submit_missing" for rule, _ in advisories
    )
    assert not any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


def test_silent_on_non_teachback_subject_without_teachback_submit(
    tmp_path, monkeypatch, pact_context,
):
    """Non-teachback subject completed without teachback_submit → R1 silent.
    R1 is gated on _is_teachback_subject; non-teachback completions never
    fire R1 (handoff_missing covers work-task completions)."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "implement foo",
                "owner": "pact-backend-coder",
                "metadata": {"handoff": {
                    "produced": "x",
                    "decisions": "x",
                    "reasoning_chain": "x",
                    "uncertainty": "x",
                    "integration": "x",
                    "open_questions": "x",
                }},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "teachback_submit_missing" for rule, _ in advisories
    )


def test_silent_on_in_progress_update_without_teachback_submit(
    pact_context,
):
    """TaskUpdate with status='in_progress' on teachback subject lacking
    teachback_submit → R1 silent (R1 fires only at completion)."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "in_progress"},
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
    assert not any(
        rule == "teachback_submit_missing" for rule, _ in advisories
    )


# =============================================================================
# teachback_submit_schema_invalid (R2) — disjoint from R1 (present-but-malformed)
# =============================================================================


@pytest.mark.parametrize(
    "missing_field",
    [
        "understanding",
        "most_likely_wrong",
        "least_confident_item",
        "first_action",
        "variety_acknowledgment",
    ],
)
def test_advisory_when_teachback_submit_missing_required_field(
    missing_field, tmp_path, monkeypatch, pact_context,
):
    """Present teachback_submit missing one required field → R2 fires.
    Covers the architect2 Task #25 deviation case (canonical-field omission)
    AND the D10 variety_acknowledgment-missing case."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit()
    tb.pop(missing_field)
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    ), f"expected R2 for missing {missing_field}, got: {advisories}"
    # Disjoint with R1
    assert not any(
        rule == "teachback_submit_missing" for rule, _ in advisories
    )


def test_advisory_when_teachback_submit_is_non_dict(
    tmp_path, monkeypatch, pact_context,
):
    """teachback_submit is a string → R2 fires (type mismatch)."""
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
                "metadata": {"teachback_submit": "hello"},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


def test_advisory_when_teachback_submit_field_is_empty_string(
    tmp_path, monkeypatch, pact_context,
):
    """Required field present but empty string → R2 fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit(most_likely_wrong="")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


def test_advisory_when_teachback_uses_non_canonical_label(
    tmp_path, monkeypatch, pact_context,
):
    """Canonical-label deviation (`questions_or_concerns` instead of
    `least_confident_item`) → R2 fires. The empirical case that motivated
    R2 in the first place."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit()
    tb.pop("least_confident_item")
    tb["questions_or_concerns"] = "..."
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


@pytest.mark.parametrize(
    "ack_shape,description",
    [
        ("string-not-dict", "non-dict variety_acknowledgment"),
        ({"rationale_articulates_this_dispatch": "maybe"}, "invalid enum value"),
        ({"rationale_articulates_this_dispatch": "no"}, "missing concern for no"),
        ({"rationale_articulates_this_dispatch": "no", "concern": ""}, "empty concern for no"),
    ],
)
def test_advisory_when_variety_ack_is_malformed(
    ack_shape, description, tmp_path, monkeypatch, pact_context,
):
    """Malformed variety_acknowledgment → R2 fires (D10 schema check via
    sub-validator). Covers non-dict, invalid enum, missing concern, and
    empty concern."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit(variety_acknowledgment=ack_shape)
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    ), f"expected R2 for {description}, got: {advisories}"


def test_silent_when_variety_ack_is_well_formed_with_concern(
    tmp_path, monkeypatch, pact_context,
):
    """variety_acknowledgment with value='no' and non-empty concern is
    well-formed → R2 silent. This is the teammate-flag-orchestrator case."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment=_well_formed_variety_ack(
            "no", concern="novelty_rationale appears copied from feature"
        )
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


def test_silent_when_extra_reasoning_reconstruction_present(
    tmp_path, monkeypatch, pact_context,
):
    """Extra fields beyond the 5 canonical (e.g., reasoning_reconstruction)
    are permitted → R2 silent. The validator checks required-presence, not
    schema exclusivity."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit(
        reasoning_reconstruction={
            "decision_attribution": "x",
            "assumption_trace": "x",
            "contingency_clause": "x",
        }
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


# =============================================================================
# variety_missing_on_dispatch_task (R4) — D11-refined: absent + malformed paths
# =============================================================================


def test_advisory_when_pact_work_task_created_without_variety(
    pact_context,
):
    """TaskCreate pact-* work task without metadata.variety → R4 advisory.
    Mirrors work_addblockedby_missing trigger shape (same discriminators)
    but checks the variety field instead of addBlockedBy."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            # no metadata.variety
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


@pytest.mark.parametrize(
    "missing_rationale",
    [
        "novelty_rationale",
        "scope_rationale",
        "uncertainty_rationale",
        "risk_rationale",
    ],
)
def test_advisory_when_variety_missing_per_dimension_rationale(
    missing_rationale, pact_context,
):
    """D11 schema: missing any per-dimension rationale → R4 fires (same
    rule, malformed-path message). Covers all four rationale fields."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety()
    variety.pop(missing_rationale)
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {"variety": variety},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    ), f"expected R4 for missing {missing_rationale}, got: {advisories}"


def test_advisory_when_variety_rationale_is_empty_string(
    pact_context,
):
    """D11: empty-string per-dimension rationale → R4 fires."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety(scope_rationale="")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {"variety": variety},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_silent_when_variety_well_formed(pact_context):
    """Full D11 schema (4 dim scores + 4 rationales + total) → R4 silent."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {"variety": _well_formed_variety()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_silent_r4_on_teachback_subject_without_variety(pact_context):
    """Teachback subject TaskCreate without variety → R4 silent. Teachback
    tasks are not work tasks; variety stamping convention applies to
    Task B (work tasks) only."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "backend-coder: TEACHBACK for foo",
            "owner": "pact-backend-coder",
            "addBlocks": ["42"],
            # no metadata.variety
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_silent_r4_on_teachback_exempt_secretary(
    tmp_path, monkeypatch, pact_context,
):
    """Teachback-exempt agentType (pact-secretary) → R4 silent. Secretary
    dispatches are single-task, not subject to variety stamping."""
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
            "subject": "secretary: harvest HANDOFFs",
            "owner": "pact-secretary",
            # no metadata.variety
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_silent_r4_on_non_pact_owner(pact_context):
    """Non-pact-* owner → R4 silent (R4 is gated on the same pact-* prefix
    as work_addblockedby_missing)."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "custom-name",
            # no metadata.variety
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_silent_r4_does_not_detect_cargo_cult_rationales(pact_context):
    """All 4 rationales identical / cargo-culted → R4 SILENT. R4 only
    enforces presence + non-empty; cargo-cult detection is D10's teammate
    adversarial review (variety_acknowledgment) + wrap-up retrospective
    signal aggregation. Pins the contract: hooks do NOT do heuristic
    cargo-cult detection."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety(
        novelty_rationale="matches feature complexity",
        scope_rationale="matches feature complexity",
        uncertainty_rationale="matches feature complexity",
        risk_rationale="matches feature complexity",
    )
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {"variety": variety},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_advisory_r4_on_d4_legacy_single_rationale(pact_context):
    """Legacy D4 single-rationale schema (just `rationale`) without the 4
    per-dimension rationales → R4 fires (missing D11 fields). Demonstrates
    R4 catches the legacy-shape migration path."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {"variety": {
                "novelty": 2,
                "scope": 2,
                "uncertainty": 2,
                "risk": 2,
                "total": 8,
                "rationale": "legacy single-rationale schema",
            }},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


# =============================================================================
# R3 + R5 write-time branch — TaskUpdate writing teachback_submit metadata
# =============================================================================


def _setup_blocks_pair(
    tmp_path, monkeypatch, team_name, task_a_id, task_b_id,
    variety_total=12, include_variety=True, variety=None,
):
    """Seed two task JSONs in ~/.claude/tasks/{team_name}/: Task A
    (teachback subject) blocks Task B (work task with variety.total).
    Used by R3 + R5 branch tests."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_dir = tmp_path / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True)
    task_a = {
        "id": task_a_id,
        "subject": "preparer: TEACHBACK for foo",
        "owner": "preparer",
        "blocks": [task_b_id],
        "metadata": {},
    }
    (tasks_dir / f"{task_a_id}.json").write_text(
        json.dumps(task_a), encoding="utf-8"
    )
    if include_variety:
        if variety is None:
            variety = _well_formed_variety(total=variety_total)
        task_b = {
            "id": task_b_id,
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "metadata": {"variety": variety},
        }
    else:
        task_b = {
            "id": task_b_id,
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "metadata": {},
        }
    (tasks_dir / f"{task_b_id}.json").write_text(
        json.dumps(task_b), encoding="utf-8"
    )


# ---------- R3 cases ----------


def test_r3_advisory_at_required_band_without_reasoning_reconstruction(
    tmp_path, monkeypatch, pact_context,
):
    """Task B.variety.total=12 → REQUIRED band; teachback_submit lacks
    reasoning_reconstruction → R3 fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=12,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    ), f"expected R3, got: {advisories}"


def test_r3_advisory_at_required_band_with_malformed_reasoning(
    tmp_path, monkeypatch, pact_context,
):
    """REQUIRED band; reasoning_reconstruction is a string instead of
    dict → R3 fires (malformed treated as missing per architect3 §4.1)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=12,
    )
    tb = _well_formed_teachback_submit(reasoning_reconstruction="not a dict")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )


def test_r3_silent_at_required_band_with_well_formed_reasoning(
    tmp_path, monkeypatch, pact_context,
):
    """REQUIRED band; well-formed reasoning_reconstruction triangle → R3
    silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=12,
    )
    tb = _well_formed_teachback_submit(reasoning_reconstruction={
        "decision_attribution": "x",
        "assumption_trace": "x",
        "contingency_clause": "x",
    })
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )


def test_r3_silent_at_recommended_band(
    tmp_path, monkeypatch, pact_context,
):
    """Task B.variety.total=8 → recommended band; no reasoning_reconstruction
    required → R3 silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )


def test_r3_silent_at_skipped_band(
    tmp_path, monkeypatch, pact_context,
):
    """Task B.variety.total=5 → skipped band; R3 silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=5,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )


def test_r3_band_unresolvable_when_blocks_missing(
    tmp_path, monkeypatch, pact_context,
):
    """Task A has no blocks → band_unresolvable advisory (fail-open per
    architect3 carry-forward #2)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    tasks_dir.mkdir(parents=True)
    task_a = {
        "id": "1",
        "subject": "preparer: TEACHBACK for foo",
        "owner": "preparer",
        "blocks": [],  # empty
        "metadata": {},
    }
    (tasks_dir / "1.json").write_text(json.dumps(task_a), encoding="utf-8")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_band_unresolvable"
        for rule, _ in advisories
    )
    # R3 itself silent in unresolvable path
    assert not any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )


def test_r3_band_unresolvable_when_task_b_missing(
    tmp_path, monkeypatch, pact_context,
):
    """Task A.blocks=['999'] but no 999.json on disk → band_unresolvable."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    tasks_dir.mkdir(parents=True)
    task_a = {
        "id": "1",
        "subject": "preparer: TEACHBACK for foo",
        "owner": "preparer",
        "blocks": ["999"],
        "metadata": {},
    }
    (tasks_dir / "1.json").write_text(json.dumps(task_a), encoding="utf-8")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_band_unresolvable"
        for rule, _ in advisories
    )


def test_r3_band_unresolvable_when_variety_absent_on_task_b(
    tmp_path, monkeypatch, pact_context,
):
    """Task B exists but lacks metadata.variety → band_unresolvable."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", include_variety=False,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_band_unresolvable"
        for rule, _ in advisories
    )


def test_r3_band_unresolvable_when_variety_total_non_int(
    tmp_path, monkeypatch, pact_context,
):
    """Task B.variety.total is a string → band_unresolvable."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety()
    variety["total"] = "twelve"
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2",
        variety=variety,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_band_unresolvable"
        for rule, _ in advisories
    )


# ---------- R5 cases ----------


def test_r5_advisory_when_variety_ack_missing(
    tmp_path, monkeypatch, pact_context,
):
    """teachback_submit lacks variety_acknowledgment → R5 fires.
    Presence-only check at write-time; full schema validation happens at
    completion-time via R2 (two-surface asymmetry)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit()
    tb.pop("variety_acknowledgment")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_acknowledgment_missing" for rule, _ in advisories
    )


def test_r5_silent_when_variety_ack_present(
    tmp_path, monkeypatch, pact_context,
):
    """variety_acknowledgment present (with value 'yes') → R5 silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_acknowledgment_missing" for rule, _ in advisories
    )


def test_r5_silent_when_variety_ack_present_with_concern(
    tmp_path, monkeypatch, pact_context,
):
    """variety_acknowledgment with value='no' + concern → R5 silent (R5
    only checks presence; R2 catches schema defects at completion)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment=_well_formed_variety_ack(
            "no", concern="novelty_rationale appears cargo-culted"
        )
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_acknowledgment_missing" for rule, _ in advisories
    )


def test_r5_silent_on_non_teachback_subject(
    tmp_path, monkeypatch, pact_context,
):
    """TaskUpdate writing teachback_submit on NON-teachback subject → R5
    silent (subject-pattern gate filters)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    tasks_dir.mkdir(parents=True)
    task = {
        "id": "1",
        "subject": "implement foo",  # NOT a teachback subject
        "owner": "pact-backend-coder",
        "blocks": [],
        "metadata": {},
    }
    (tasks_dir / "1.json").write_text(json.dumps(task), encoding="utf-8")
    tb = _well_formed_teachback_submit()
    tb.pop("variety_acknowledgment")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_acknowledgment_missing" for rule, _ in advisories
    )


def test_r3_r5_silent_when_status_completed(
    tmp_path, monkeypatch, pact_context,
):
    """R3 + R5 are gated on status != 'completed' (two-surface asymmetry:
    R1/R2 cover completion-time; R3/R5 cover write-time only). When a
    teammate atypically bundles teachback_submit +
    status=completed in one TaskUpdate, R3/R5 stay silent and R1/R2 (with
    schema validator) cover the same defect class."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=12,
    )
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=30,
    )
    tb = _well_formed_teachback_submit()
    tb.pop("variety_acknowledgment")  # would normally fire R5
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "status": "completed",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": {"teachback_submit": tb},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    # R3 + R5 silent (write-time-only branch skipped on completion)
    assert not any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )
    assert not any(
        rule == "variety_acknowledgment_missing" for rule, _ in advisories
    )
    # R2 catches the missing variety_acknowledgment at completion-time
    assert any(
        rule == "teachback_submit_schema_invalid" for rule, _ in advisories
    )


# =============================================================================
# Pure-function tests: schema validators (D10 + D11)
# =============================================================================


class TestValidateVarietyAcknowledgment:
    """Pure-function tests for _validate_variety_acknowledgment (D10)."""

    def test_yes_without_concern_is_valid(self):
        assert tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "yes"}
        ) is None

    def test_yes_with_empty_concern_is_valid(self):
        assert tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "yes", "concern": ""}
        ) is None

    def test_no_with_concern_is_valid(self):
        assert tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "no", "concern": "x"}
        ) is None

    def test_concern_value_with_concern_is_valid(self):
        assert tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "concern", "concern": "x"}
        ) is None

    def test_non_dict_rejected(self):
        result = tlg._validate_variety_acknowledgment("yes")
        assert result is not None
        assert "must be object" in result

    def test_invalid_enum_rejected(self):
        result = tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "maybe"}
        )
        assert result is not None
        assert "maybe" in result

    def test_no_without_concern_rejected(self):
        result = tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "no"}
        )
        assert result is not None
        assert "concern" in result

    def test_concern_value_with_empty_concern_rejected(self):
        result = tlg._validate_variety_acknowledgment(
            {"rationale_articulates_this_dispatch": "concern", "concern": ""}
        )
        assert result is not None
        assert "concern" in result


class TestValidateVarietySchema:
    """Pure-function tests for _validate_variety_schema (D11)."""

    def test_full_d11_schema_valid(self):
        assert tlg._validate_variety_schema(_well_formed_variety()) is None

    @pytest.mark.parametrize(
        "missing_field",
        [
            "novelty_rationale",
            "scope_rationale",
            "uncertainty_rationale",
            "risk_rationale",
        ],
    )
    def test_missing_rationale_rejected(self, missing_field):
        variety = _well_formed_variety()
        variety.pop(missing_field)
        result = tlg._validate_variety_schema(variety)
        assert result is not None
        assert missing_field in result

    def test_empty_rationale_rejected(self):
        result = tlg._validate_variety_schema(
            _well_formed_variety(novelty_rationale="")
        )
        assert result is not None
        assert "novelty_rationale" in result

    def test_non_string_rationale_rejected(self):
        result = tlg._validate_variety_schema(
            _well_formed_variety(scope_rationale=42)
        )
        assert result is not None
        assert "scope_rationale" in result

    def test_non_dict_rejected(self):
        result = tlg._validate_variety_schema("not a dict")
        assert result is not None
        assert "must be object" in result

    def test_extra_legacy_rationale_field_permitted(self):
        """Legacy D4 `rationale` field present alongside D11 4-tuple → valid.
        D11 validates required-presence, not exclusivity."""
        variety = _well_formed_variety(rationale="legacy field still there")
        assert tlg._validate_variety_schema(variety) is None


class TestValidateTeachbackSubmitSchema:
    """Pure-function tests for _validate_teachback_submit_schema (D10)."""

    def test_full_5_field_payload_valid(self):
        assert tlg._validate_teachback_submit_schema(
            _well_formed_teachback_submit()
        ) is None

    def test_non_dict_rejected(self):
        result = tlg._validate_teachback_submit_schema("hello")
        assert result is not None
        assert "must be object" in result

    @pytest.mark.parametrize(
        "missing_field",
        [
            "understanding",
            "most_likely_wrong",
            "least_confident_item",
            "first_action",
            "variety_acknowledgment",
        ],
    )
    def test_missing_required_field_rejected(self, missing_field):
        tb = _well_formed_teachback_submit()
        tb.pop(missing_field)
        result = tlg._validate_teachback_submit_schema(tb)
        assert result is not None
        assert missing_field in result

    def test_empty_string_field_rejected(self):
        result = tlg._validate_teachback_submit_schema(
            _well_formed_teachback_submit(understanding="")
        )
        assert result is not None
        assert "understanding" in result

    def test_malformed_ack_rejected(self):
        result = tlg._validate_teachback_submit_schema(
            _well_formed_teachback_submit(
                variety_acknowledgment={"rationale_articulates_this_dispatch": "maybe"}
            )
        )
        assert result is not None
        assert "variety_acknowledgment" in result

    def test_extra_reasoning_reconstruction_field_permitted(self):
        result = tlg._validate_teachback_submit_schema(
            _well_formed_teachback_submit(
                reasoning_reconstruction={
                    "decision_attribution": "x",
                    "assumption_trace": "x",
                    "contingency_clause": "x",
                }
            )
        )
        assert result is None


# =============================================================================
# variety_acknowledgment_schema_invalid_at_write_time — D10 schema check
# forwarded from R2 (completion-time) to write-time
# =============================================================================


def test_variety_ack_schema_invalid_at_write_time_fires_on_string(
    tmp_path, monkeypatch, pact_context,
):
    """variety_acknowledgment as a free-text STRING instead of OBJECT →
    fires at write-time (not just completion-time)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment="I think the scoring is fine",
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_acknowledgment_schema_invalid_at_write_time"
        for rule, _ in advisories
    ), f"expected write-time schema advisory, got: {advisories}"


def test_variety_ack_schema_invalid_at_write_time_fires_on_invalid_enum(
    tmp_path, monkeypatch, pact_context,
):
    """variety_acknowledgment with invalid enum value → fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment={"rationale_articulates_this_dispatch": "maybe"},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "variety_acknowledgment_schema_invalid_at_write_time"
        for rule, _ in advisories
    )


def test_variety_ack_schema_silent_at_write_time_on_well_formed(
    tmp_path, monkeypatch, pact_context,
):
    """Well-formed variety_acknowledgment → write-time schema advisory silent.
    Disjoint with R5 (which fires on absent, not present-but-malformed)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "variety_acknowledgment_schema_invalid_at_write_time"
        for rule, _ in advisories
    )


def test_variety_ack_schema_silent_when_field_absent(
    tmp_path, monkeypatch, pact_context,
):
    """variety_acknowledgment absent → R5 fires for presence; the
    schema-at-write-time rule does NOT fire (disjoint trigger)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit()
    tb.pop("variety_acknowledgment")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    rules = {rule for rule, _ in advisories}
    assert "variety_acknowledgment_missing" in rules
    assert "variety_acknowledgment_schema_invalid_at_write_time" not in rules


# =============================================================================
# reasoning_reconstruction_in_handoff — cross-slot mistake (wrong slot)
# =============================================================================


def test_reasoning_reconstruction_in_handoff_fires_when_nested_in_handoff(
    tmp_path, monkeypatch, pact_context,
):
    """reasoning_reconstruction placed inside metadata.handoff → fires.
    Cross-slot: belongs on teachback_submit, not handoff."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {
                "handoff": {
                    "produced": "x",
                    "decisions": "x",
                    "reasoning_chain": "x",
                    "uncertainty": "x",
                    "integration": "x",
                    "open_questions": "x",
                    "reasoning_reconstruction": {
                        "decision_attribution": "x",
                        "assumption_trace": "x",
                        "contingency_clause": "x",
                    },
                },
            },
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_in_handoff"
        for rule, _ in advisories
    ), f"expected cross-slot advisory, got: {advisories}"


def test_reasoning_reconstruction_in_handoff_silent_when_only_on_teachback(
    tmp_path, monkeypatch, pact_context,
):
    """reasoning_reconstruction placed on teachback_submit (correct slot)
    AND handoff has reasoning_chain but no reasoning_reconstruction →
    cross-slot advisory silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(reasoning_reconstruction={
        "decision_attribution": "x",
        "assumption_trace": "x",
        "contingency_clause": "x",
    })
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_in_handoff"
        for rule, _ in advisories
    )


def test_reasoning_reconstruction_in_handoff_silent_when_no_handoff(
    pact_context,
):
    """No handoff at all → cross-slot advisory silent."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_in_handoff"
        for rule, _ in advisories
    )


@pytest.mark.parametrize(
    "rr_shape,shape_label",
    [
        (
            {
                "what-I-learned": "x",
                "falsification-attempts": "y",
                "most-likely-wrong-prediction": "z",
            },
            "wrong-key-names",
        ),
        (
            {
                "decision_attribution": "x",
                "assumption_trace": "",
                "contingency_clause": "x",
            },
            "empty-sub-key-value",
        ),
    ],
)
def test_reasoning_reconstruction_in_handoff_fires_regardless_of_inner_shape(
    rr_shape, shape_label, pact_context,
):
    """R7 fires whenever reasoning_reconstruction appears in metadata.handoff,
    INDEPENDENT of the inner sub-key shape. The cross-slot rule is a pure
    slot-location detector; sub-key validity is R9's surface, not R7's.

    Matches R9's parametrized shape-probe coverage (wrong-key-names +
    empty-sub-key-value) so the two write-time advisory surfaces are
    symmetric in shape-probe breadth — proving slot detection is
    disjoint from sub-key validation across both surfaces."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {
                "handoff": {
                    "produced": "x",
                    "decisions": "x",
                    "reasoning_chain": "x",
                    "uncertainty": "x",
                    "integration": "x",
                    "open_questions": "x",
                    "reasoning_reconstruction": rr_shape,
                },
            },
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_in_handoff"
        for rule, _ in advisories
    ), f"expected R7 to fire for {shape_label}, got: {advisories}"


# =============================================================================
# reasoning_reconstruction_subkeys_invalid — wrong 3 sub-key names / shapes
# =============================================================================


def test_reasoning_reconstruction_subkeys_invalid_fires_on_wrong_names(
    tmp_path, monkeypatch, pact_context,
):
    """Non-canonical sub-key names (e.g. what-I-learned) → fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(reasoning_reconstruction={
        "what-I-learned": "x",
        "falsification-attempts": "y",
        "most-likely-wrong-prediction": "z",
    })
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_subkeys_invalid"
        for rule, _ in advisories
    ), f"expected subkeys advisory, got: {advisories}"


def test_reasoning_reconstruction_subkeys_invalid_fires_on_empty_subkey(
    tmp_path, monkeypatch, pact_context,
):
    """Canonical keys but empty sub-key value → fires."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(reasoning_reconstruction={
        "decision_attribution": "x",
        "assumption_trace": "",
        "contingency_clause": "x",
    })
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_subkeys_invalid"
        for rule, _ in advisories
    )


def test_reasoning_reconstruction_subkeys_silent_on_well_formed(
    tmp_path, monkeypatch, pact_context,
):
    """Well-formed 3-sub-key triangle → silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(reasoning_reconstruction={
        "decision_attribution": "x",
        "assumption_trace": "x",
        "contingency_clause": "x",
    })
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_subkeys_invalid"
        for rule, _ in advisories
    )


def test_reasoning_reconstruction_subkeys_silent_when_field_absent(
    tmp_path, monkeypatch, pact_context,
):
    """reasoning_reconstruction not provided → subkeys rule silent (R3
    handles the band-required case independently)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_subkeys_invalid"
        for rule, _ in advisories
    )


# =============================================================================
# intentional_wait_nested_in_teachback_submit — cross-key mistake
# =============================================================================


def test_intentional_wait_nested_in_teachback_submit_fires_when_nested(
    tmp_path, monkeypatch, pact_context,
):
    """intentional_wait placed INSIDE teachback_submit → fires.
    Cross-key: must be sibling top-level metadata key per Step 3."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit()
    tb["intentional_wait"] = {
        "reason": "awaiting_lead_completion",
        "expected_resolver": "lead",
        "since": "2026-05-26T19:45:40+00:00",
    }
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "intentional_wait_nested_in_teachback_submit"
        for rule, _ in advisories
    ), f"expected cross-key advisory, got: {advisories}"


def test_intentional_wait_silent_when_top_level_sibling(
    tmp_path, monkeypatch, pact_context,
):
    """intentional_wait as a sibling top-level metadata key (canonical Step 3
    placement) → cross-key advisory silent."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {
                "teachback_submit": _well_formed_teachback_submit(),
                "intentional_wait": {
                    "reason": "awaiting_lead_completion",
                    "expected_resolver": "lead",
                    "since": "2026-05-26T19:45:40+00:00",
                },
            },
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "intentional_wait_nested_in_teachback_submit"
        for rule, _ in advisories
    )


# =============================================================================
# Multi-rule concurrent emission — a single TaskUpdate that violates several
# canonical-schema rules at once must emit ALL applicable advisories, not
# stop after the first match. Pinning this property guards against future
# short-circuit refactors that would silently degrade the teaching surface
# (a teammate with 3 mistakes should learn about all 3, not just one).
# =============================================================================


def test_multi_rule_concurrent_emission_three_rules_fire_together(
    tmp_path, monkeypatch, pact_context,
):
    """One TaskUpdate carrying THREE distinct violations must emit THREE
    advisories on the same evaluation pass:
      - variety_acknowledgment as STRING → variety_acknowledgment_schema_invalid_at_write_time
      - reasoning_reconstruction with wrong sub-key names → reasoning_reconstruction_subkeys_invalid
      - intentional_wait nested inside teachback_submit → intentional_wait_nested_in_teachback_submit
    """
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment="I think the scoring is fine",
        reasoning_reconstruction={
            "what-I-learned": "x",
            "falsification-attempts": "y",
            "most-likely-wrong-prediction": "z",
        },
    )
    tb["intentional_wait"] = {
        "reason": "awaiting_lead_completion",
        "expected_resolver": "lead",
        "since": "2026-05-26T20:00:00+00:00",
    }
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": tb},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    rules = {rule for rule, _ in advisories}
    assert "variety_acknowledgment_schema_invalid_at_write_time" in rules
    assert "reasoning_reconstruction_subkeys_invalid" in rules
    assert "intentional_wait_nested_in_teachback_submit" in rules


def test_multi_rule_concurrent_emission_cross_slot_with_teachback_violation(
    tmp_path, monkeypatch, pact_context,
):
    """A TaskUpdate writing BOTH teachback_submit (with a wrong-shape ack)
    AND handoff (with reasoning_reconstruction in the wrong slot) must
    emit BOTH advisories — the cross-slot rule (R8) and the teachback-
    scoped rule (R7) are independent code paths and both must fire."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment={"rationale_articulates_this_dispatch": "maybe"},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {
                "teachback_submit": tb,
                "handoff": {
                    "produced": "x", "decisions": "x", "reasoning_chain": "x",
                    "uncertainty": "x", "integration": "x", "open_questions": "x",
                    "reasoning_reconstruction": {
                        "decision_attribution": "x",
                        "assumption_trace": "x",
                        "contingency_clause": "x",
                    },
                },
            },
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    rules = {rule for rule, _ in advisories}
    assert "variety_acknowledgment_schema_invalid_at_write_time" in rules
    assert "reasoning_reconstruction_in_handoff" in rules


def test_multi_rule_concurrent_emission_all_four_advisory_rules_fire_together(
    tmp_path, monkeypatch, pact_context,
):
    """One TaskUpdate carrying FOUR distinct violations across BOTH the
    teachback_submit slot AND the handoff slot must emit FOUR advisories
    on the same evaluation pass — the 4-way superset of the 3-rule and
    2-rule cross-slot fixtures above. Pins the structural property that
    R7 + R8 + R9 + R10 emit at FOUR independent code paths with no
    short-circuit dispatcher consolidation: a teammate making all 4
    mistakes at once learns about all 4, not just the first match.

    Violations:
      - teachback_submit.variety_acknowledgment as STRING
        → variety_acknowledgment_schema_invalid_at_write_time (R7)
      - handoff.reasoning_reconstruction nested in wrong slot
        → reasoning_reconstruction_in_handoff (R8)
      - teachback_submit.reasoning_reconstruction with wrong sub-key names
        → reasoning_reconstruction_subkeys_invalid (R9)
      - teachback_submit.intentional_wait nested instead of sibling
        → intentional_wait_nested_in_teachback_submit (R10)
    """
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=8,
    )
    tb = _well_formed_teachback_submit(
        variety_acknowledgment="I think the scoring is fine",
        reasoning_reconstruction={
            "what-I-learned": "x",
            "falsification-attempts": "y",
            "most-likely-wrong-prediction": "z",
        },
    )
    tb["intentional_wait"] = {
        "reason": "awaiting_lead_completion",
        "expected_resolver": "lead",
        "since": "2026-05-26T20:00:00+00:00",
    }
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {
                "teachback_submit": tb,
                "handoff": {
                    "produced": "x", "decisions": "x", "reasoning_chain": "x",
                    "uncertainty": "x", "integration": "x", "open_questions": "x",
                    "reasoning_reconstruction": {
                        "decision_attribution": "x",
                        "assumption_trace": "x",
                        "contingency_clause": "x",
                    },
                },
            },
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    rules = {rule for rule, _ in advisories}
    assert "variety_acknowledgment_schema_invalid_at_write_time" in rules
    assert "reasoning_reconstruction_in_handoff" in rules
    assert "reasoning_reconstruction_subkeys_invalid" in rules
    assert "intentional_wait_nested_in_teachback_submit" in rules


# =============================================================================
# 10|11 variety-band boundary disambiguation — pin the exact cut so that
# future variety_scorer threshold changes (e.g. PLAN_MODE_MIN export) are
# caught here rather than silently re-routing recommended/required cases.
# =============================================================================


def test_r3_boundary_variety_total_10_recommended_band_silent_on_missing_rr(
    tmp_path, monkeypatch, pact_context,
):
    """variety_total=10 → ROUTE_ORCHESTRATE (recommended, not required).
    Missing reasoning_reconstruction must NOT fire R3
    (reasoning_reconstruction_missing_at_required_band). 10 is the TOP of
    the recommended band per the threshold-band table at
    pact-teachback/SKILL.md and the SSOT at pact-ct-teachback.md."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=10,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    )


def test_r3_boundary_variety_total_11_required_band_fires_on_missing_rr(
    tmp_path, monkeypatch, pact_context,
):
    """variety_total=11 → ROUTE_PLAN_MODE (required). Missing
    reasoning_reconstruction must fire R3
    (reasoning_reconstruction_missing_at_required_band). 11 is the BOTTOM
    of the required band — the 10|11 cut is the canonical threshold."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety_total=11,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(
        rule == "reasoning_reconstruction_missing_at_required_band"
        for rule, _ in advisories
    ), f"expected R3 to fire at variety=11, got: {advisories}"
