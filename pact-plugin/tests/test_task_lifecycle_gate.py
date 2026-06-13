"""
Comprehensive coverage for task_lifecycle_gate.py — #662 PostToolUse hook.

Sibling to test_task_lifecycle_gate_smoke.py (the 6 minimum-viable cases).
This file expands every rule landed in the gate.

Rule coverage:
  - teachback_addblocks_missing — TaskCreate TEACHBACK without
    addBlocks=[<work_task_id>] → advisory
  - work_addblockedby_missing — TaskCreate pact-* non-TEACHBACK
    without addBlockedBy → advisory
  - self_completion — Teammate self-completes Task → advisory +
    completion_disputed writeback
        Carve-outs: secretary self-complete (team-config agentType in
        SELF_COMPLETE_EXEMPT_AGENT_TYPES, resolved via the
        is_self_complete_exempt predicate), signal task
        (metadata.completion_type=signal — also via the predicate),
        recursion-marker skip.
        Sketch-A: actor unresolvable → CURRENT skip behavior; encoded with
        explicit deviation-documenting test referencing architect §5.3.
  - module-load failure → advisory + hookEventName=PostToolUse + exit 0
  (RETIRED: the handoff_missing / handoff_schema_invalid completion-time
   advisories — gated on a permanently-dormant is_work_task / owner.startswith
   ("pact-") guard that never matched bare teammate owner names — were
   retired along with their branch; the tests exercising them were removed.)
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
            "subject": "secretary: deliver session briefing",
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
# Retired paired-send detector (#897) — regression guard
#
# The per-completion paired-wake detector was removed: it read
# inboxes/{owner}.json to confirm a paired wake-SendMessage before a teachback
# completion, but that store is platform-written ASYNC on delivery (after the
# synchronous PostToolUse(TaskUpdate-completed) read), and SendMessage is
# unhookable, so the advisory was ~100%-false-positive by construction.
#
# The issue's originally-proposed non-vacuity test ("a teachback completion
# WITH a real paired wake must NOT emit the advisory") is UN-BUILDABLE: you
# cannot deterministically construct a teachback completion where a real paired
# wake is visible to the racing synchronous read — that race IS the bug. The
# buildable inverse is asserted here instead: the advisory NEVER fires, under
# ANY inbox state. Before the retire, the in-window state was silent while the
# empty/out-of-window states FIRED; now all four are uniformly silent, which
# proves the advisory is GONE, not merely silent on a happy path.
# =============================================================================


@pytest.mark.parametrize(
    "paired_offset_seconds",
    [
        30,    # paired wake well within the old 120s window (was silent)
        121,   # paired wake outside the old 120s window (FIRED before retire)
        None,  # empty inbox, no paired wake at all (FIRED before retire)
    ],
    ids=["paired_in_window", "paired_out_of_window", "inbox_empty"],
)
def test_no_paired_send_advisory_with_inbox_on_disk(
    paired_offset_seconds, tmp_path, monkeypatch, pact_context,
):
    """A teachback-subject TaskUpdate(status=completed) NEVER emits the
    retired `completion_no_paired_send` advisory, regardless of the on-disk
    team-inbox state. Covers the three states the deleted tests pinned —
    paired-in-window (was silent), paired-out-of-window and empty-inbox (both
    FIRED before retire). The seed exercises a realistic inbox; the gate no
    longer reads it, so all three states are uniformly silent post-retire."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_team_inbox(
        tmp_path, monkeypatch, owner="preparer", team_name="test-team",
        paired_offset_seconds=paired_offset_seconds,
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
    assert not any(
        rule == "completion_no_paired_send" for rule, _ in advisories
    ), f"retired advisory must never fire, got: {advisories}"


def test_no_paired_send_advisory_when_no_inbox_file(pact_context):
    """The fourth (production-faithful) inbox state: no inbox file on disk at
    all. This is the real shape the retired advisory could never observe — the
    teachback completion branch consumes `tool_response.task` directly and the
    gate performs NO inbox/Path.home read post-retire, so this test seeds
    nothing (no `_setup_team_inbox`, no home monkeypatch). The advisory must
    still never fire. Building this without monkeypatching home is deliberate:
    faking an empty home would be wrong-reason-green theater for a branch that
    never touches home."""
    pact_context(team_name="test-team", session_id="test-session")
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
    assert not any(
        rule == "completion_no_paired_send" for rule, _ in advisories
    ), f"retired advisory must never fire, got: {advisories}"


def test_paired_send_detector_symbols_stay_retired():
    """Anti-fossil liveness pin: the retired detector's symbols must NOT be
    re-introduced into the gate module. Goes RED loudly if someone "restores"
    the dead check (which cannot work — see the provenance NOTE at the removal
    site). Guards both the helper and its orphaned window constant."""
    assert not hasattr(tlg, "_has_paired_sendmessage"), (
        "_has_paired_sendmessage was retired in #897 (read-before-async-write "
        "race; ~100%-false-positive) — do not re-introduce it"
    )
    assert not hasattr(tlg, "PAIRED_SENDMESSAGE_WINDOW_S"), (
        "PAIRED_SENDMESSAGE_WINDOW_S was orphaned by the retire — its only "
        "consumers were the removed detector and its advisory message"
    )


def test_paired_send_advisory_absent_from_teachback_completion_output(pact_context):
    """Structural anti-fossil pin at the emission surface: a teachback
    completion that previously hit the empty-inbox FIRE path emits zero
    advisories whose rule-name is `completion_no_paired_send`. Complements the
    symbol-absence pin — even a re-introduction under a different helper name
    would be caught here as long as it reused the canonical rule-name."""
    pact_context(team_name="test-team", session_id="test-session")
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
    rule_names = [rule for rule, _ in advisories]
    assert "completion_no_paired_send" not in rule_names, (
        f"retired rule-name must not appear in gate output, got: {rule_names}"
    )


def test_retire_leaves_sibling_teachback_completion_advisories_intact(pact_context):
    """Regression-safety: removing the paired-send sub-check did not
    collaterally disable the sibling advisory that shares the same
    `if is_teachback and owner:` completion branch. A teachback completion
    with empty metadata must still fire `teachback_submit_missing` (R1).
    Focused confirmation only — the dedicated R1 test section covers this in
    depth; this asserts the retire was surgical."""
    pact_context(team_name="test-team", session_id="test-session")

    teachback_payload = {
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
    teachback_advisories = tlg.evaluate_lifecycle(teachback_payload)
    assert any(
        rule == "teachback_submit_missing" for rule, _ in teachback_advisories
    ), (
        "sibling R1 advisory must still fire after retire, got: "
        f"{teachback_advisories}"
    )


# Allowlist SSOT: the complete set of advisory rule-names a teachback-subject
# TaskUpdate(status=completed) may LEGITIMATELY emit. Derived from the gate's
# `if is_teachback and owner:` completion branch + the adjacent self-completion
# check:
#   - teachback_submit_missing            (teachback_submit absent/empty)
#   - teachback_submit_schema_invalid     (present but malformed)
#   - self_completion                     (actor == owner, not carve-out exempt)
# (the former handoff_missing / handoff_schema_invalid completion advisories
# were retired with their permanently-dormant is_work_task branch, so they
# cannot appear here either); the write-time rules
# (reasoning_reconstruction_in_handoff, teachback_addblocks_missing, the
# variety_acknowledgment / reasoning_reconstruction / intentional_wait
# write-time advisories) are gated on status != "completed"; agent_handoff is
# a journal side-effect (append_event), not a returned advisory, and is
# skipped for teachback subjects. A future maintainer who adds a NEW legitimate
# teachback-completion advisory MUST add its rule-name here — that deliberate
# update is the intended human gate, and is exactly what makes the pin below a
# name-agnostic guard rather than a brittle one.
_TEACHBACK_COMPLETION_ALLOWED_RULES = frozenset({
    "teachback_submit_missing",
    "teachback_submit_schema_invalid",
    "self_completion",
})


def _teachback_completion_metadata_for(shape: str) -> dict:
    """Build a teachback-completion `metadata` dict for the named shape. Called
    inside the test body (runtime), NOT at collection time, so the late-defined
    `_well_formed_teachback_submit` helper is resolvable. Shapes mirror what the
    gate's `if is_teachback and owner:` branch distinguishes: empty → R1,
    well_formed → nothing, missing_<field> → R2."""
    if shape == "empty":
        return {}
    if shape == "well_formed":
        return {"teachback_submit": _well_formed_teachback_submit()}
    assert shape.startswith("missing_"), shape
    field = shape[len("missing_"):]
    tb = _well_formed_teachback_submit()
    tb.pop(field)
    return {"teachback_submit": tb}


@pytest.mark.parametrize(
    "shape",
    [
        "empty",
        "well_formed",
        "missing_understanding",
        "missing_most_likely_wrong",
        "missing_least_confident_item",
        "missing_first_action",
        "missing_variety_acknowledgment",
    ],
)
def test_teachback_completion_emits_only_allowlisted_rule_names(shape, pact_context):
    """Name-agnostic anti-fossil pin: a teachback-subject completion emits ONLY
    rule-names from the known-good allowlist. This closes the one re-intro
    vector the symbol-absence + emission-surface pins miss — a paired-wake
    detector re-introduced under BOTH a renamed helper AND a renamed rule-name
    (so neither name-keyed pin fires) would still ADD an unexpected rule-name to
    a teachback completion's output, and this catches it regardless of the name
    chosen.

    Parametrized over every teachback-completion metadata shape the gate
    distinguishes (empty → R1, well_formed → nothing, each malformed variant →
    R2) so the allowlist's COMPLETENESS is validated against the full surface:
    if any legitimate shape emitted a rule-name outside the allowlist, this
    fails and surfaces it (which would mean the allowlist needs widening, not
    that a re-intro occurred)."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "preparer",
                "metadata": _teachback_completion_metadata_for(shape),
            }
        },
    }
    rule_names = {rule for rule, _ in tlg.evaluate_lifecycle(payload)}
    unexpected = rule_names - _TEACHBACK_COMPLETION_ALLOWED_RULES
    assert not unexpected, (
        "teachback completion emitted rule-name(s) outside the allowlist: "
        f"{unexpected} — a re-introduced detector under any name would surface "
        f"here. Full output: {rule_names}"
    )


def test_allowlist_pin_catches_a_foreign_rule_name():
    """Non-vacuity proof for the allowlist pin, read-only (no hook revert — the
    shared worktree forbids it). Simulates a re-introduced detector by injecting
    a synthetic foreign rule-name into a teachback completion's output, then
    applies the SAME allowlist predicate the pin above uses, and asserts it
    flags the foreign name. This proves the pin would catch a renamed re-intro
    on its face: if the allowlist were vacuous (e.g. it accidentally contained
    every name, or the subtraction logic were inverted) this would not fire."""
    # A re-introduced paired-wake detector would append an advisory tuple under
    # some new rule-name — model that with a representative synthetic name that
    # is NOT in the allowlist.
    simulated_reintroduced_output = {
        "teachback_submit_missing",          # a legitimate advisory that did fire
        "wake_not_paired_v2",                # the foreign, re-introduced detector
    }
    unexpected = simulated_reintroduced_output - _TEACHBACK_COMPLETION_ALLOWED_RULES
    assert unexpected == {"wake_not_paired_v2"}, (
        "allowlist predicate must flag a foreign re-introduced rule-name "
        f"regardless of its name; got unexpected={unexpected}"
    )


# =============================================================================
# RETIRED: the handoff_missing / handoff_schema_invalid completion-time
# advisories were gated on a permanently-dormant `is_work_task` (owner
# .startswith("pact-")) guard — real teammate owners are bare names, so the
# branch never fired in production. The branch and both advisories were
# retired; the tests that exercised them via synthetic pact-* owners were
# removed with it. Lead-side HANDOFF presence is still covered by the
# _emit_lead_side_agent_handoff path (see its own coverage below).
# =============================================================================


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
# Exception-safety (negative property) — hook level.
#
# The hook fires on every Task-tool use; no malformed variety shape may
# raise out of it. The resolver-leg of this property lives in
# test_teachback_schema.py; this is the full-hook leg: each malformed variety
# is run through a complete main() invocation in BOTH a TaskCreate envelope
# (write-time R4 path) and a TaskUpdate-to-completed-teachback envelope
# (read-time band path). The hook must exit 0 with valid JSON in every case.
# =============================================================================


# Malformed variety values that flow into the resolver via both surfaces. The
# hook receives its input as already-parsed JSON from stdin, so these values
# are restricted to JSON-representable shapes (a real hook can never receive a
# Python object() / NaN — those non-serializable inputs are covered at the
# pure-helper leg in test_teachback_schema.py, which has no stdin round-trip).
# The hook must absorb every one of these.
_MALFORMED_VARIETIES = [
    pytest.param(None, id="none"),
    pytest.param([], id="empty_list"),
    pytest.param("a string", id="string"),
    pytest.param(42, id="bare_int"),
    pytest.param(8.5, id="float"),
    pytest.param(True, id="bool"),
    pytest.param({"total": [1, 2, 3]}, id="total_list"),
    pytest.param({"total": {"k": "v"}}, id="total_dict"),
    pytest.param({"score": {"nested": "junk"}}, id="score_nested_dict"),
    pytest.param({"novelty": "two", "scope": 1,
                  "uncertainty": 1, "risk": 1}, id="dimension_wrong_type"),
    pytest.param({"total": "twelve", "score": "eight"}, id="all_string_candidates"),
    pytest.param({"deeply": {"nested": {"junk": [1, 2]}}}, id="unrelated_nested"),
]


def _assert_exits_zero_with_valid_json(payload, capsys):
    """Run main() on the payload; assert it exits 0 and emits parseable JSON.
    SystemExit(0) is the hook's normal termination; any OTHER exception is a
    failure of the never-raise contract."""
    code, out = _capture_main(payload, capsys)
    assert code == 0
    assert out is not None  # parseable JSON object (or None only if no output)


@pytest.mark.parametrize("variety", _MALFORMED_VARIETIES)
def test_hook_absorbs_malformed_variety_on_taskcreate(variety, capsys, pact_context):
    """Write-time envelope: a pact-* work-task TaskCreate carrying a
    malformed variety must not raise out of the hook."""
    pact_context(team_name="test-team", session_id="test-session")
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
    _assert_exits_zero_with_valid_json(payload, capsys)


@pytest.mark.parametrize("variety", _MALFORMED_VARIETIES)
def test_hook_absorbs_malformed_variety_on_teachback_submit(
    variety, capsys, tmp_path, monkeypatch, pact_context,
):
    """Read-time envelope: a teachback-submit TaskUpdate whose blocked Task B
    carries a malformed variety must not raise out of the hook (the band
    resolver consults resolve_variety_total on the on-disk shape)."""
    pact_context(team_name="test-team", session_id="test-session")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety=variety,
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    }
    _assert_exits_zero_with_valid_json(payload, capsys)


def test_hook_absorbs_malformed_metadata_envelope_on_taskcreate(
    capsys, pact_context,
):
    """The whole metadata object is malformed (a string, not a dict) → the
    hook tolerates it and exits 0. Exercises the metadata-not-dict guard
    above the variety lookup."""
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": "not-a-dict",
        },
        "tool_response": {},
    }
    _assert_exits_zero_with_valid_json(payload, capsys)


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
    """Legacy `tool_output` envelope (no `tool_response`) → task is extracted.

    Constructs a payload with NO `tool_response` field and the task data
    under `tool_output` (the pre-rename envelope shape). evaluate_lifecycle
    must extract the task via the `or tool_output` fallback; the
    teachback_submit_missing rule then fires because the extracted teachback
    task carries no metadata.teachback_submit — proving the subject/owner were
    read from the legacy envelope.

    A regression that strips the `or tool_output` branch causes
    tool_response to resolve to {}, task.get("subject") to be empty, and
    teachback_submit_missing to NOT fire (the rule is gated on subject being
    teachback-shaped + owner present) — which would leak past this assertion.
    """
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "completed"},
        # Legacy envelope shape — pre-rename. NO `tool_response` key.
        "tool_output": {
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
        f"expected teachback_submit_missing via tool_output fallback, got: {advisories}"
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
        # Canonical envelope: teachback task, no submit → teachback_submit_missing fires.
        "tool_response": {
            "task": {
                "id": "1",
                "subject": "preparer: TEACHBACK for canonical",
                "owner": "preparer",
                "metadata": {},
            }
        },
        # Legacy envelope: non-teachback → teachback_submit_missing would NOT fire if read.
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
    # Canonical was read → teachback task → teachback_submit_missing fires.
    assert any(rule == "teachback_submit_missing" for rule, _ in advisories), (
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
    fire R1."""
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
# variety_missing_on_dispatch_task (R4) — the third (NEW) message path:
# valid rationales present, but no resolvable variety total. Distinct from the
# absent-variety and malformed-rationale paths; same rule name, distinct text.
# =============================================================================


def _r4_create_payload(variety, metadata_extra=None):
    """A TaskCreate payload for a pact-* work task carrying the given variety
    dict. metadata_extra merges into the task metadata alongside `variety`
    (e.g. a top-level variety_score sibling)."""
    metadata = {"variety": variety}
    if metadata_extra:
        metadata.update(metadata_extra)
    return {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": metadata,
        },
        "tool_response": {},
    }


def _r4_message(advisories):
    """Return the variety_missing_on_dispatch_task message text, or None if
    the rule did not fire."""
    for rule, message in advisories:
        if rule == "variety_missing_on_dispatch_task":
            return message
    return None


def _rationales_only_variety(**overrides):
    """A variety dict with the four valid rationales but NO resolvable total
    candidate (no total, no score, no dimension scores). Forces the
    unresolvable-total path while staying rationale-complete. Overrides let a
    test add back a single candidate (e.g. an out-of-range total)."""
    variety = {
        "novelty_rationale": "x",
        "scope_rationale": "x",
        "uncertainty_rationale": "x",
        "risk_rationale": "x",
    }
    variety.update(overrides)
    return variety


def test_r4_unresolvable_total_path_fires_with_distinct_message(pact_context):
    """Valid rationales + no resolvable total (out-of-range `total`, no
    other candidate) → R4 fires on the NEW unresolvable-total path. The
    message names the no-resolvable-total condition and does NOT name the
    unrelated wiring rule that caused the original field misattribution."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _rationales_only_variety(total=99)
    advisories = tlg.evaluate_lifecycle(_r4_create_payload(variety))
    message = _r4_message(advisories)
    assert message is not None, "expected R4 to fire on unresolvable total"
    assert "no resolvable total" in message
    assert "teachback_addblocks_missing" not in message


def test_r4_unresolvable_total_message_distinct_from_rationale_path(pact_context):
    """The unresolvable-total message text differs from the malformed-
    rationale message text — the single rule emits path-specific detail."""
    pact_context(team_name="test-team", session_id="test-session")
    unresolvable = _r4_message(
        tlg.evaluate_lifecycle(_r4_create_payload(_rationales_only_variety(total=99)))
    )
    missing_rationale_variety = _well_formed_variety()
    missing_rationale_variety.pop("novelty_rationale")
    malformed = _r4_message(
        tlg.evaluate_lifecycle(_r4_create_payload(missing_rationale_variety))
    )
    assert unresolvable is not None and malformed is not None
    assert unresolvable != malformed
    assert "no resolvable total" in unresolvable
    assert "no resolvable total" not in malformed


def test_r4_unresolvable_total_message_distinct_from_absent_path(pact_context):
    """The unresolvable-total message differs from the absent-variety
    message — three distinct paths under one rule name."""
    pact_context(team_name="test-team", session_id="test-session")
    unresolvable = _r4_message(
        tlg.evaluate_lifecycle(_r4_create_payload(_rationales_only_variety(total=99)))
    )
    absent_payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
        },
        "tool_response": {},
    }
    absent = _r4_message(tlg.evaluate_lifecycle(absent_payload))
    assert unresolvable is not None and absent is not None
    assert unresolvable != absent


def test_r4_rationale_problem_reported_before_total_problem(pact_context):
    """A stamp missing BOTH a rationale AND a total surfaces the rationale
    problem first (return-the-first-problem contract). The unresolvable-total
    detail appears only once rationales are complete."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _rationales_only_variety(total=99)
    variety.pop("scope_rationale")  # now also rationale-incomplete
    message = _r4_message(tlg.evaluate_lifecycle(_r4_create_payload(variety)))
    assert message is not None
    assert "no resolvable total" not in message
    assert "scope_rationale" in message


def test_r4_silent_when_score_fallback_resolves(pact_context):
    """Valid rationales + a non-canonical `score` (no `total`) → R4 SILENT,
    because the shared resolver resolves the total via the score fallback.
    This is the write-time half of the consistency property for the exact
    reported field shape."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _rationales_only_variety(score=12)
    advisories = tlg.evaluate_lifecycle(_r4_create_payload(variety))
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_r4_silent_when_top_level_variety_score_resolves(pact_context):
    """Valid rationales + no in-dict candidate but a top-level
    metadata.variety_score → R4 SILENT. The validator forwards the
    surrounding metadata so the sibling candidate is reachable."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _rationales_only_variety()
    advisories = tlg.evaluate_lifecycle(
        _r4_create_payload(variety, metadata_extra={"variety_score": 8})
    )
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


def test_r4_silent_when_dimension_sum_resolves(pact_context):
    """Valid rationales + valid dimension scores (no explicit total) → R4
    SILENT via the dimension-sum fallback."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety()
    variety.pop("total")
    advisories = tlg.evaluate_lifecycle(_r4_create_payload(variety))
    assert not any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )


# =============================================================================
# Cross-rule consistency property (LOAD-BEARING) — the single test that pins
# this bug closed. For a representative set of variety shapes (all
# rationale-complete, so the rationale leg never confounds), the write-time
# rule must NOT fire on its total leg if and only if the shared resolver
# returns a non-None total. The two surfaces consult the same resolver, so
# they cannot disagree. Includes the original reported field shape.
# =============================================================================


# (variety, metadata_extra, description). Every variety is rationale-complete
# so the ONLY thing that can drive R4 is the total leg — isolating the
# property under test. The metadata_extra carries any top-level sibling.
_CONSISTENCY_SHAPES = [
    pytest.param(
        _rationales_only_variety(total=8), None,
        id="canonical_total_in_range",
    ),
    pytest.param(
        _rationales_only_variety(total=11), None,
        id="canonical_total_required_band",
    ),
    pytest.param(
        _rationales_only_variety(total=99), None,
        id="total_out_of_range_no_fallback",
    ),
    pytest.param(
        _rationales_only_variety(total="twelve"), None,
        id="total_non_numeric_string_no_fallback",
    ),
    pytest.param(
        _rationales_only_variety(total=True), None,
        id="total_bool_no_fallback",
    ),
    pytest.param(
        _rationales_only_variety(total=8.0), None,
        id="total_float_no_fallback",
    ),
    pytest.param(
        _rationales_only_variety(score=12), None,
        id="field_report_shape_score_only",
    ),
    pytest.param(
        _rationales_only_variety(score=99), None,
        id="score_out_of_range_no_other",
    ),
    pytest.param(
        _rationales_only_variety(total=99, score=8), None,
        id="junk_total_recovered_by_score",
    ),
    pytest.param(
        _rationales_only_variety(), {"variety_score": 8},
        id="top_level_variety_score_only",
    ),
    pytest.param(
        _rationales_only_variety(), {"variety_score": 99},
        id="top_level_variety_score_out_of_range",
    ),
    pytest.param(
        _well_formed_variety(),  # carries total=8 + valid dims
        None,
        id="full_well_formed",
    ),
    pytest.param(
        {**_well_formed_variety(), "total": "bad"},  # dims still 2/2/2/2
        None,
        id="junk_total_recovered_by_dimension_sum",
    ),
    pytest.param(
        _rationales_only_variety(), None,
        id="no_candidate_at_all",
    ),
]


@pytest.mark.parametrize("variety, metadata_extra", _CONSISTENCY_SHAPES)
def test_write_time_silence_iff_resolver_resolves(
    variety, metadata_extra, pact_context,
):
    """CONSISTENCY PROPERTY: variety_missing_on_dispatch_task does NOT fire
    (rationales held valid, so only the total leg is in play) if and only if
    resolve_variety_total returns a non-None total for the same (variety,
    metadata). No stamp-time-accepted shape may read as unresolvable later."""
    pact_context(team_name="test-team", session_id="test-session")
    metadata = {"variety": variety}
    if metadata_extra:
        metadata.update(metadata_extra)

    resolved = tlg.resolve_variety_total(variety, metadata)
    advisories = tlg.evaluate_lifecycle(_r4_create_payload(variety, metadata_extra))
    r4_fired = any(
        rule == "variety_missing_on_dispatch_task" for rule, _ in advisories
    )

    # iff: silent (not fired) ⟺ resolver resolved.
    assert (not r4_fired) == (resolved is not None), (
        f"consistency violated: r4_fired={r4_fired}, "
        f"resolved={resolved!r} for variety={variety!r} "
        f"metadata_extra={metadata_extra!r}"
    )


def test_field_report_shape_agrees_across_both_surfaces(
    tmp_path, monkeypatch, pact_context,
):
    """The exact reported false-positive shape — rationales + a non-canonical
    `score` int with a sibling top-level `variety_score` — must AGREE across
    both surfaces post-fix: write-time SILENT (TaskCreate) AND read-time
    RESOLVABLE to a band (no band_unresolvable advisory at teachback submit).
    This is the regression that the whole fix exists to close."""
    pact_context(team_name="test-team", session_id="test-session")
    field_variety = _rationales_only_variety(score=12)

    # Write-time surface: TaskCreate is silent.
    create_advisories = tlg.evaluate_lifecycle(
        _r4_create_payload(field_variety, metadata_extra={"variety_score": 12})
    )
    assert not any(
        rule == "variety_missing_on_dispatch_task"
        for rule, _ in create_advisories
    ), "write-time surface fired on a resolvable field shape"

    # Read-time surface: teachback submit resolves the band, no unresolvable.
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2",
        variety=field_variety,
    )
    # Add the top-level sibling onto the seeded Task B metadata.
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    task_b = json.loads((tasks_dir / "2.json").read_text(encoding="utf-8"))
    task_b["metadata"]["variety_score"] = 12
    (tasks_dir / "2.json").write_text(json.dumps(task_b), encoding="utf-8")

    submit_advisories = tlg.evaluate_lifecycle({
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    })
    assert not any(
        rule == "reasoning_reconstruction_band_unresolvable"
        for rule, _ in submit_advisories
    ), "read-time surface emitted unresolvable for a resolvable field shape"


# =============================================================================
# reasoning_reconstruction_band_unresolvable — rewritten advisory text.
# The message must enumerate the no-resolvable-total cause and must NOT name
# the unrelated wiring rule that caused the original field misattribution.
# Text is asserted on normalized substrings (the prod text is word-identical
# to the spec but reflowed as f-string continuations), never byte-equality.
# =============================================================================


def _band_unresolvable_message(advisories):
    for rule, message in advisories:
        if rule == "reasoning_reconstruction_band_unresolvable":
            return message
    return None


def test_band_unresolvable_message_names_no_resolvable_total(
    tmp_path, monkeypatch, pact_context,
):
    """When Task B variety is present but every resolver candidate is
    invalid, the rewritten advisory enumerates the no-resolvable-total cause
    and drops the misattributing cross-name."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety()
    variety["total"] = "twelve"
    for dim in ("novelty", "scope", "uncertainty", "risk"):
        variety.pop(dim, None)
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety=variety,
    )
    advisories = tlg.evaluate_lifecycle({
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    })
    message = _band_unresolvable_message(advisories)
    assert message is not None
    normalized = " ".join(message.split())
    assert "no resolvable total" in normalized
    assert "cannot resolve the variety band" in normalized
    assert "teachback_addblocks_missing" not in normalized


# =============================================================================
# Read-time band MAPPING through the fallback candidates — advisory surface.
#
# The unit-layer counterpart in test_per_dispatch_variety.py asserts the band
# STRING returned by _resolve_required_band_via_blocks for each fallback. These
# tests assert the complementary observable: a fallback-only stamp seeded on
# disk drives a full evaluate_lifecycle teachback-submit through to the
# user-visible required-band advisory (reasoning_reconstruction_missing_at_
# required_band) — and emits no band_unresolvable. Belt (advisory) + suspenders
# (unit band string) cover different change classes: a regression that left the
# band string correct but broke the advisory wiring, or vice versa, fails on
# exactly one layer. Each stamp omits the canonical total so only the named
# fallback can resolve, and lands at the required band (total >=
# TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN) so R3 is observable.
# =============================================================================


def _assert_required_band_advisory(advisories):
    """The read-time required-band advisory fired and no unresolvable
    advisory was emitted alongside it."""
    rules = [rule for rule, _ in advisories]
    assert "reasoning_reconstruction_missing_at_required_band" in rules, (
        f"expected the required-band advisory to fire, got: {rules}"
    )
    assert "reasoning_reconstruction_band_unresolvable" not in rules, (
        f"a resolvable fallback stamp must not emit unresolvable, got: {rules}"
    )


def test_r3_fires_at_read_time_via_score_fallback(
    tmp_path, monkeypatch, pact_context,
):
    """Task B carries a non-canonical `score` at the required band (no
    `total`) → read-time resolves via the score fallback and the required-
    band advisory fires. teachback_submit omits reasoning_reconstruction so
    R3 is the expected miss."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety(score=tlg.TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN)
    variety.pop("total")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety=variety,
    )
    advisories = tlg.evaluate_lifecycle({
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    })
    _assert_required_band_advisory(advisories)


def test_r3_fires_at_read_time_via_top_level_variety_score_fallback(
    tmp_path, monkeypatch, pact_context,
):
    """Task B has no in-dict total/score but a top-level
    metadata.variety_score at the required band → read-time resolves via the
    sibling candidate (the caller forwards Task B's full metadata) and the
    required-band advisory fires."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety()
    variety.pop("total")
    for dim in ("novelty", "scope", "uncertainty", "risk"):
        variety.pop(dim, None)
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety=variety,
    )
    # Add the top-level sibling onto the seeded Task B metadata.
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    task_b = json.loads((tasks_dir / "2.json").read_text(encoding="utf-8"))
    task_b["metadata"]["variety_score"] = (
        tlg.TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN
    )
    (tasks_dir / "2.json").write_text(json.dumps(task_b), encoding="utf-8")
    advisories = tlg.evaluate_lifecycle({
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    })
    _assert_required_band_advisory(advisories)


def test_r3_fires_at_read_time_via_dimension_sum_fallback(
    tmp_path, monkeypatch, pact_context,
):
    """Task B has no total/score/variety_score but four valid dimension
    scores summing into the required band (3+3+3+3=12) → read-time resolves
    via the dimension-sum fallback and the required-band advisory fires."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety(novelty=3, scope=3, uncertainty=3, risk=3)
    variety.pop("total")
    _setup_blocks_pair(
        tmp_path, monkeypatch, "test-team", "1", "2", variety=variety,
    )
    advisories = tlg.evaluate_lifecycle({
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "1",
            "metadata": {"teachback_submit": _well_formed_teachback_submit()},
        },
        "tool_response": {},
    })
    _assert_required_band_advisory(advisories)


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


def test_r3_band_unresolvable_when_no_candidate_resolves(
    tmp_path, monkeypatch, pact_context,
):
    """Task B.variety has a non-int total AND no recoverable fallback
    (no score, no top-level variety_score, dimensions stripped so the
    dimension-sum cannot fire) → every resolver candidate is invalid →
    band_unresolvable. A non-int total alone no longer yields unresolvable
    when a valid dimension-sum or score fallback exists."""
    pact_context(team_name="test-team", session_id="test-session")
    variety = _well_formed_variety()
    variety["total"] = "twelve"
    # Strip the four dimension scores so the dimension-sum fallback cannot
    # resolve; the rationales (not resolution candidates) stay.
    for dim in ("novelty", "scope", "uncertainty", "risk"):
        variety.pop(dim, None)
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
# variety_scorer threshold changes (e.g. a shift in PLAN_MODE_MIN) are
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


# =============================================================================
# #906 auditor-verdict overwrite-protection (codified mirror)
#
# Two structural branches keyed on is_lead (top-level agent_type), all in this
# one hook (no new matcher):
#   MIRROR  (non-lead audit_summary write) → durable audit_summary_authored
#   RECOVER (lead divergent overwrite)     → advisory + lead_close_note, with
#            the authored verdict preserved (NO read of the clobbered value).
# Disk reads/writes resolve under the monkeypatched HOME via _seed_task_a /
# read_task_json / _writeback_audit_recovery.
# =============================================================================


def _read_task_back(tmp_path: Path, team_name: str, task_id: str) -> dict:
    p = tmp_path / ".claude" / "tasks" / team_name / f"{task_id}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_906_mirror_snapshots_authored_verdict_on_non_lead_write(
    tmp_path, monkeypatch, pact_context
):
    """MIRROR: a non-lead TaskUpdate that writes metadata.audit_summary durably
    snapshots it to metadata.audit_summary_authored (silent — no advisory)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    verdict = {"signal": "RED", "findings": ["sql injection"], "scope": "backend"}
    _seed_task_a(
        tmp_path, "test-team", "1",
        subject="auditor: observe", owner="auditor",
        metadata={"completion_type": "signal", "audit_summary": verdict},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_type": "pact-auditor",  # non-lead
        "tool_input": {"taskId": "1", "metadata": {"audit_summary": verdict}},
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    back = _read_task_back(tmp_path, "test-team", "1")
    assert back["metadata"].get("audit_summary_authored") == verdict, (
        "MIRROR must durably snapshot the authored verdict to "
        "audit_summary_authored"
    )
    assert not any(r == "audit_summary_overwrite" for r, _ in advisories), (
        "MIRROR is silent — no overwrite advisory on the auditor's own write"
    )


def test_906_recover_preserves_authored_and_routes_lead_note(
    tmp_path, monkeypatch, pact_context
):
    """RECOVER: a lead TaskUpdate that overwrites a DIVERGENT authored verdict
    fires the advisory, preserves audit_summary_authored, and routes the lead's
    value to lead_close_note (the verdict is not lost)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    authored = {"signal": "RED", "findings": ["sql injection"], "scope": "backend"}
    lead_note = {"signal": "GREEN", "note": "closing — no signal observed"}
    _seed_task_a(
        tmp_path, "test-team", "1",
        subject="auditor: observe", owner="auditor",
        metadata={"audit_summary": authored, "audit_summary_authored": authored},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_type": "pact-orchestrator",  # lead
        "tool_input": {"taskId": "1", "metadata": {"audit_summary": lead_note}},
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(r == "audit_summary_overwrite" for r, _ in advisories), (
        f"expected audit_summary_overwrite advisory, got: {advisories}"
    )
    back = _read_task_back(tmp_path, "test-team", "1")
    assert back["metadata"].get("audit_summary_authored") == authored, (
        "authored verdict MUST remain preserved in audit_summary_authored"
    )
    assert back["metadata"].get("lead_close_note") == lead_note, (
        "lead's overwriting value MUST be routed to lead_close_note"
    )


def test_906_recover_flags_destructive_downgrade(
    tmp_path, monkeypatch, pact_context
):
    """RED->GREEN is a destructive downgrade — advisory text escalates."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    authored = {"signal": "RED", "findings": ["auth bypass"], "scope": "api"}
    lead_note = {"signal": "GREEN", "note": "closing"}
    _seed_task_a(
        tmp_path, "test-team", "1",
        subject="auditor: observe", owner="auditor",
        metadata={"audit_summary": authored, "audit_summary_authored": authored},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_type": "pact-orchestrator",
        "tool_input": {"taskId": "1", "metadata": {"audit_summary": lead_note}},
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    msg = next((m for r, m in advisories if r == "audit_summary_overwrite"), "")
    assert "DESTRUCTIVE DOWNGRADE" in msg, (
        f"RED->GREEN must escalate advisory severity, got: {msg!r}"
    )


def test_906_no_advisory_when_no_authored_mirror_exists(
    tmp_path, monkeypatch, pact_context
):
    """No false-positive: a lead writing audit_summary from scratch (no prior
    audit_summary_authored mirror) does NOT fire the overwrite advisory."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    lead_note = {"signal": "GREEN", "note": "lead-authored from scratch"}
    _seed_task_a(
        tmp_path, "test-team", "1",
        subject="auditor: observe", owner="auditor", metadata={},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_type": "pact-orchestrator",
        "tool_input": {"taskId": "1", "metadata": {"audit_summary": lead_note}},
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(r == "audit_summary_overwrite" for r, _ in advisories), (
        "no mirror exists → no overwrite (lead-authored-from-scratch is not a "
        f"false fire); got: {advisories}"
    )


def test_906_no_advisory_when_lead_reaffirms_authored_value(
    tmp_path, monkeypatch, pact_context
):
    """No fire when the lead's audit_summary EQUALS the authored mirror
    (re-affirmation, not an overwrite)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    authored = {"signal": "YELLOW", "findings": ["stale comment"], "scope": "x"}
    _seed_task_a(
        tmp_path, "test-team", "1",
        subject="auditor: observe", owner="auditor",
        metadata={"audit_summary": authored, "audit_summary_authored": authored},
    )
    payload = {
        "tool_name": "TaskUpdate",
        "agent_type": "pact-orchestrator",
        "tool_input": {"taskId": "1", "metadata": {"audit_summary": authored}},
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(r == "audit_summary_overwrite" for r, _ in advisories), (
        f"lead re-affirming the authored verdict is not an overwrite; got: {advisories}"
    )


# =============================================================================
# M2 (security #38) — NUL-byte taskId advisory-suppression DoS closure
#
# A NUL byte in tool_input.taskId reaches read_task_json's task_file.exists(),
# which raises ValueError('embedded null byte'). Before the fix that propagated
# to main()'s catch-all → _emit_load_failure_advisory → rule enforcement skipped
# for the turn. read_task_json now catches ValueError (degrade to {}), and the
# two writeback helpers sanitize via sanitize_path_component (strip C0/\x00).
# =============================================================================


def test_M2_value_error_in_task_read_does_not_suppress_gate(
    tmp_path, monkeypatch, pact_context
):
    """End-to-end: a task-file stat that raises ValueError (the NUL-byte
    exists() raise on a vulnerable Python) must NOT propagate out of
    evaluate_lifecycle — else it reaches main()'s catch-all and skips rule
    enforcement for the turn (advisory-suppression DoS). The fix in
    read_task_json degrades the read to {}.

    Determinism: exists()'s NUL behavior is Python-version-dependent (3.14
    returns False), so we FORCE the raise via a conditional exists() that raises
    ONLY for the NUL-containing task file — leaving the pact_context context-file
    read (no NUL) unaffected, so team_name still resolves and the #906 read path
    is genuinely reached. This is RED-on-revert: drop ValueError from
    read_task_json's except and evaluate_lifecycle propagates the ValueError.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    _real_exists = Path.exists

    def _conditional_exists(self):
        if "\x00" in str(self):
            raise ValueError("embedded null byte")
        return _real_exists(self)

    monkeypatch.setattr(Path, "exists", _conditional_exists)
    payload = {
        "tool_name": "TaskUpdate",
        "agent_type": "pact-orchestrator",
        "tool_input": {
            "taskId": "9\x00x",
            "metadata": {"audit_summary": {"signal": "GREEN"}},
        },
        "tool_response": {},
    }
    # Must return a list (no exception). Before the fix this propagated ValueError.
    advisories = tlg.evaluate_lifecycle(payload)
    assert isinstance(advisories, list)
    assert not any(r == "audit_summary_overwrite" for r, _ in advisories), (
        "NUL-byte taskId read degraded to {} → no authored mirror → no overwrite fire"
    )


# =============================================================================
# Crash-path health marker (#951) — runtime-stage legs, journal pin,
# error bounding. Import-stage subprocess matrix lives in
# test_task_lifecycle_gate_degraded.py.
# =============================================================================


def _raise_runtime_failure(_input_data):
    raise RuntimeError("simulated evaluate failure")


def test_advisory_output_carries_no_health_marker(
    capsys, monkeypatch, tmp_path, pact_context,
):
    """Healthy advisory shape is unchanged: a real rule firing through a
    full main() invocation emits hookEventName + additionalContext and
    NEITHER pactGateHealth NOR systemMessage — the marker decorates only
    crash paths, never healthy advisories. (The suppress-shape twin pins
    are byte-identity asserts elsewhere in this file and in the degraded
    sibling module.)"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            "addBlockedBy": ["41"],
            "metadata": {"variety": None},
        },
        "tool_response": {},
    }
    code, out = _capture_main(payload, capsys)
    assert code == 0
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "additionalContext" in hso
    assert "pactGateHealth" not in out
    assert "systemMessage" not in out


def test_runtime_failure_emits_health_marker_with_runtime_stage(
    capsys, monkeypatch, tmp_path,
):
    """evaluate_lifecycle raising inside main() → exit 0 with the full
    machine marker at stage "runtime", the systemMessage mirror, and the
    bounded error text naming the exception type. With no session context
    resolvable (CLAUDE_PROJECT_DIR unset, config dir sandboxed) the
    best-effort journal emit deterministically degrades to the
    append-returned-False stderr disposition — never a raise."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(tlg, "evaluate_lifecycle", _raise_runtime_failure)
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "in_progress"},
        "tool_response": {},
        "session_id": "health-marker-session",
    }
    with patch.object(sys, "stdin", _stdin(payload)):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())
    marker = out["pactGateHealth"]
    assert set(marker) == {"v", "hook", "status", "stage", "error"}
    assert marker["v"] == 1
    assert marker["hook"] == "task_lifecycle_gate"
    assert marker["status"] == "failed"
    assert marker["stage"] == "runtime"
    assert "RuntimeError" in marker["error"]
    assert "simulated evaluate failure" in marker["error"]
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert out["systemMessage"] == hso["additionalContext"]
    assert "RuntimeError" in hso["additionalContext"]
    assert "gate_health journal emit skipped" in captured.err, (
        "no-session journal degradation must surface on the debug channel "
        "(append_event returned False), never raise"
    )


def test_runtime_failure_writes_gate_health_journal_event(
    capsys, monkeypatch, tmp_path, pact_context,
):
    """The journal channel's ONE pin, on the path where it is contractually
    expected to work (imports fine, context initialized, context FILE on
    disk): a runtime failure appends exactly one gate_health event with
    the full field set to the session journal — and neither stderr
    disposition line fires.

    Sandbox: the context file lives in tmp_path (pact_context fixture)
    and CLAUDE_CONFIG_DIR points inside tmp_path, so get_session_dir
    resolves the journal under the sandbox, never the real ~/.claude."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    pact_context(team_name="test-team", session_id="test-session")
    monkeypatch.setattr(tlg, "evaluate_lifecycle", _raise_runtime_failure)
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "in_progress"},
        "tool_response": {},
        "session_id": "test-session",
    }
    with patch.object(sys, "stdin", _stdin(payload)):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()

    # The context file's project_dir is /test/project → slug "project";
    # session_id comes from the context file, not stdin.
    journal = (
        tmp_path / "cfg" / "pact-sessions" / "project" / "test-session"
        / "session-journal.jsonl"
    )
    assert journal.exists(), (
        f"journal not written; stderr={captured.err!r}"
    )
    events = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    health_events = [e for e in events if e.get("type") == "gate_health"]
    assert len(health_events) == 1, (
        f"exactly one gate_health event expected, got {len(health_events)}: "
        f"{health_events!r}"
    )
    event = health_events[0]
    assert set(event) == {
        "v", "type", "ts", "hook", "status", "stage", "error", "tool_name",
    }
    assert event["v"] == 1
    assert event["hook"] == "task_lifecycle_gate"
    assert event["status"] == "failed"
    assert event["stage"] == "runtime"
    assert "RuntimeError" in event["error"]
    assert event["tool_name"] == "TaskUpdate"
    assert event["ts"]

    # Journal-event error text and marker error text are the same bounded
    # rendering — one bounding discipline across every context-bound surface.
    out = json.loads(captured.out.strip())
    assert event["error"] == out["pactGateHealth"]["error"]

    assert "gate_health journal emit" not in captured.err, (
        "neither disposition line (skipped / unavailable) may fire on the "
        "working journal path"
    )


def test_init_failure_routes_through_runtime_advisory(
    capsys, monkeypatch, tmp_path,
):
    """pact_context.init raising inside main() → the runtime crash mask,
    NOT an unmasked traceback: exit 0 with the stage-"runtime" marker
    naming the init failure. Pins the guarded-init routing (previously
    the file's one unmasked crash path). The journal emit's own lazy
    init call hits the same patched raise inside its guard → the
    "unavailable" stderr disposition, never a propagated exception."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        tlg.pact_context,
        "init",
        lambda _input_data: (_ for _ in ()).throw(
            RuntimeError("simulated init failure")
        ),
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "1", "status": "in_progress"},
        "tool_response": {},
        "session_id": "init-failure-session",
    }
    with patch.object(sys, "stdin", _stdin(payload)):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())
    marker = out["pactGateHealth"]
    assert marker["stage"] == "runtime"
    assert marker["status"] == "failed"
    assert "RuntimeError" in marker["error"]
    assert "simulated init failure" in marker["error"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "gate_health journal emit unavailable" in captured.err, (
        "the journal emitter's lazy init hits the same raise and must "
        "degrade to the unavailable disposition inside its guard"
    )


def test_health_marker_error_text_is_bounded_and_sanitized(capsys):
    """Direct helper invoke with pathological exception text (600+ chars,
    embedded control characters): every context-bound surface (marker
    error, advisory, systemMessage) carries the SAME sanitized rendering
    truncated at the cap with an explicit marker — while the stderr
    diagnostic keeps the full raw text (debug-channel split).

    The control characters sit INSIDE the truncation window (sanitized-
    text indices 62-64, well under the 200-char cap), so the sanitize
    step itself — not truncation — is what removes them: with the
    sanitize substitution deleted from _bounded_error_text, the
    positional and printability asserts below fail. Hostile bytes placed
    past the cap would make every sanitization assert vacuously true."""
    noisy = "a" * 50 + "\x00\x07\x1b" + "b" * 600
    err = ValueError(noisy)
    with pytest.raises(SystemExit) as exc:
        tlg._emit_load_failure_advisory("runtime", err)
    assert exc.value.code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())

    error_field = out["pactGateHealth"]["error"]
    suffix = "...[truncated]"
    assert error_field.endswith(suffix)
    assert len(error_field) == 200 + len(suffix)
    assert error_field.startswith("ValueError: ")
    # Positional pin: "ValueError: " (12) + 50 a's puts the three control
    # characters at indices 62-64 — each must have become a space.
    assert error_field[61] == "a"
    assert error_field[62:65] == "   ", (
        f"control characters inside the cap window must be substituted "
        f"with spaces by sanitization: {error_field[55:70]!r}"
    )
    assert error_field[65] == "b"
    assert all(ch.isprintable() for ch in error_field), (
        f"control characters must be sanitized: {error_field!r}"
    )

    # Same bounded text on every context-bound surface.
    hso = out["hookSpecificOutput"]
    assert error_field in hso["additionalContext"]
    assert error_field in out["systemMessage"]
    assert "\x00" not in out["systemMessage"]

    # Debug channel keeps the full raw text — past the cap AND unsanitized
    # (the raw control bytes survive only on stderr).
    assert "b" * 600 in captured.err
    assert "\x00\x07\x1b" in captured.err


class _HostileStrError(Exception):
    """Exception whose __str__ raises — the hostile-renderer shape the
    crash-path floor must survive (rendering an exception message runs
    arbitrary exception-class code)."""

    def __str__(self):
        raise RuntimeError("hostile __str__")


def test_hostile_str_exception_still_emits_floor_marker(capsys):
    """An exception whose __str__ raises must not void the floor: the
    helper falls back to the type-name + placeholder rendering, the full
    marker still prints, exit stays 0, and the guarded stderr diagnostic
    carries the placeholder instead of propagating the renderer raise."""
    err = _HostileStrError()
    with pytest.raises(SystemExit) as exc:
        tlg._emit_load_failure_advisory("runtime", err)
    assert exc.value.code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())

    marker = out["pactGateHealth"]
    assert set(marker) == {"v", "hook", "status", "stage", "error"}
    assert marker["status"] == "failed"
    assert marker["stage"] == "runtime"
    assert marker["error"] == "_HostileStrError: <exception str() raised>"

    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert marker["error"] in hso["additionalContext"]
    assert out["systemMessage"] == hso["additionalContext"]

    # Guarded stderr full-text line: placeholder, never a propagated raise.
    assert "Hook load error (task_lifecycle_gate / runtime)" in captured.err
    assert "<exception str() raised>" in captured.err


def test_renderer_defect_falls_back_to_raise_proof_constant(
    capsys, monkeypatch,
):
    """Defense-in-depth call-site guard: if the (now-total) bounded
    renderer itself somehow raises, the floor still prints — with the
    raise-proof CONSTANT. type(error).__name__ is deliberately not the
    fallback: a hostile metaclass __name__ would re-invoke the same
    attribute access that just failed, at the one site that must never
    raise."""
    monkeypatch.setattr(
        tlg,
        "_bounded_error_text",
        lambda _err: (_ for _ in ()).throw(
            RuntimeError("simulated renderer defect")
        ),
    )
    with pytest.raises(SystemExit) as exc:
        tlg._emit_load_failure_advisory("runtime", ValueError("boom"))
    assert exc.value.code == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out.strip())
    assert out["pactGateHealth"]["error"] == "<error text unavailable>"
    assert "<error text unavailable>" in out["systemMessage"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_journal_event_sanitizes_hostile_tool_name(
    capsys, monkeypatch, tmp_path, pact_context,
):
    """tool_name is attacker-set stdin on the import-stage path — the
    journal event must carry the same sanitize+bound discipline as the
    error text: control characters become spaces and over-cap text is
    truncated with the explicit marker."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    pact_context(team_name="test-team", session_id="test-session")
    hostile = "Task\x00Update" + "x" * 300
    tlg._emit_gate_health_event(
        "module imports", "SomeError: detail", {"tool_name": hostile}
    )
    captured = capsys.readouterr()
    assert "gate_health journal emit" not in captured.err, (
        f"emit must succeed on the working path: {captured.err!r}"
    )
    journal = (
        tmp_path / "cfg" / "pact-sessions" / "project" / "test-session"
        / "session-journal.jsonl"
    )
    events = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(events) == 1
    recorded = events[0]["tool_name"]
    suffix = "...[truncated]"
    assert recorded.endswith(suffix)
    assert len(recorded) == 200 + len(suffix)
    assert recorded.startswith("Task Update"), (
        f"control char must become a space: {recorded[:15]!r}"
    )
    assert all(ch.isprintable() for ch in recorded)


def test_journal_event_renders_non_str_tool_name_as_placeholder(
    capsys, monkeypatch, tmp_path, pact_context,
):
    """A non-string tool_name (type-confused stdin) becomes a typed
    placeholder in the journal event rather than a raw non-str value."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    pact_context(team_name="test-team", session_id="test-session")
    tlg._emit_gate_health_event(
        "module imports", "SomeError: detail", {"tool_name": 42}
    )
    captured = capsys.readouterr()
    assert "gate_health journal emit" not in captured.err
    journal = (
        tmp_path / "cfg" / "pact-sessions" / "project" / "test-session"
        / "session-journal.jsonl"
    )
    events = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(events) == 1
    assert events[0]["tool_name"] == "<non-str int>"


# =============================================================================
# Cycle-2: _bounded_error_text total against a hostile-metaclass __name__.
# A metaclass can make type(error).__name__ a property that RAISES; the
# helper captures the type name once defensively (→ literal "exception")
# so neither the type-name nor the message render can escape it. Pins the
# residual review-security found: pre-fix the helper's own fallback
# re-accessed __name__ and re-raised, crashing the bootstrap degraded path
# (exit 1, suppressed warning). The bootstrap-path regression pin is the
# subprocess matrix in test_bootstrap_gate.py::TestHostileNameCrashPath;
# these are the in-process helper-totality + tlg-path pins.
#
# HAZARD: never reference these classes' __name__ in test ids, labels, or
# assertion reprs — the metaclass property bombs the access itself. Pass
# instances positionally and assert only on returned STRINGS.
# =============================================================================


class _HostileNameMeta(type):
    @property
    def __name__(cls):  # noqa: N805 — metaclass property over the class
        raise RuntimeError("hostile __name__")


class _NameBomb(Exception, metaclass=_HostileNameMeta):
    """Raising __name__, normal __str__."""


class _BothBomb(Exception, metaclass=_HostileNameMeta):
    """Raising __name__ AND raising __str__."""

    def __str__(self):
        raise RuntimeError("hostile __str__")


def test_bounded_error_text_total_against_hostile_name():
    """The helper returns a string for an exception whose metaclass
    __name__ raises — type name captured once → literal "exception", with
    the message render still guarded for the both-hostile case. Counter-
    test: revert 7155516d → the fallback re-accesses __name__ and the
    helper re-raises (RuntimeError escapes)."""
    # Assert on returned strings ONLY; never let pytest repr a hostile
    # instance (its default Exception repr accesses __name__).
    assert tlg._bounded_error_text(_NameBomb("msg")) == "exception: msg"
    assert tlg._bounded_error_text(_BothBomb("msg")) == (
        "exception: <exception str() raised>"
    )


def test_bounded_error_text_common_cases_unchanged_by_cycle2():
    """No string-ripple (shape (b)): the common-case renderings are
    unchanged — only the hostile-__name__ path degrades to "exception:".
    Guards against a cycle-2 refactor silently altering the normal text."""
    assert tlg._bounded_error_text(ValueError("plain")) == "ValueError: plain"
    # Hostile __str__ but renderable __name__ → real type name preserved.
    assert tlg._bounded_error_text(_HostileStrError("m")) == (
        "_HostileStrError: <exception str() raised>"
    )


def test_hostile_name_exception_emits_floor_marker_via_helper_totality(capsys):
    """End-to-end through the full crash path: a real hostile-__name__
    exception renders "exception: ..." in the marker error (the helper
    handled it), NOT the call-site "<error text unavailable>" constant —
    proving the helper-totality branch, not the defense-in-depth fallback,
    is what carries a genuine hostile-__name__ exception. Floor marker
    intact, exit 0."""
    with pytest.raises(SystemExit) as exc:
        tlg._emit_load_failure_advisory("runtime", _NameBomb("boom"))
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out.strip())
    marker = out["pactGateHealth"]
    assert marker["status"] == "failed"
    assert marker["stage"] == "runtime"
    assert marker["error"] == "exception: boom", (
        f"helper-totality path expected; got {marker['error']!r}"
    )
    # Distinguish from the call-site constant guard (that path only fires
    # if the whole helper raises — here the helper is total, so it must
    # NOT appear).
    assert marker["error"] != "<error text unavailable>"
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert out["systemMessage"] == out["hookSpecificOutput"]["additionalContext"]
