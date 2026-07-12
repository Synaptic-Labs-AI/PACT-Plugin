"""
Location: pact-plugin/tests/test_snapshot_seams_gate.py
Summary: Seam tests for the task_metadata_snapshot emission points inside
         task_lifecycle_gate.py — the lead-completion seam (additive-after
         the lead-side agent_handoff emit) and the post-completion backstop
         seam (the any-metadata generalization of the handoff backstop).
         Covers the both-modes matrix (lead vs teammate frames — the
         is_lead runtime structural signal), signal-task INCLUSION with the
         unconditional agent_handoff-suppression leg, eligibility
         (handoff-only → no snapshot), ownerless emission, incoming-metadata
         merge, and content-key dedup/supersession across re-fires.
Used by: pytest (CODE-phase verification for the gate seams; edge/matrix
         depth is TEST phase work).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.task_metadata_snapshot as tms  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.emitter import VALID_HANDOFF  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"

SIBLINGS = {
    "teachback_submit": {"understanding": "u", "first_action": "f"},
    "variety": {"total": 10},
}


@pytest.fixture
def snapshot_events(monkeypatch):
    """Spy on the SUBSTRATE's append_event binding — the gate seams route
    snapshot writes through shared.task_metadata_snapshot, not through the
    gate module's own append_event."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tms, "append_event", _spy)
    return events


@pytest.fixture
def handoff_events(monkeypatch):
    """Spy on the GATE's append_event binding (agent_handoff and advisory
    journal writes) — the unconditional-leg oracle for signal suppression."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tlg, "append_event", _spy)
    return events


def _completion_payload(
    *,
    agent_type=LEAD,
    task_id="42",
    subject="devops: implement the thing",
    owner="devops",
    metadata=None,
    incoming_metadata=None,
):
    """A TaskUpdate(status=completed) PostToolUse frame with post-state via
    tool_response.task (the gate's preferred source)."""
    task = {
        "id": task_id,
        "subject": subject,
        "owner": owner,
        "metadata": metadata if metadata is not None else dict(SIBLINGS),
    }
    tool_input = {"taskId": task_id, "status": "completed"}
    if incoming_metadata is not None:
        tool_input["metadata"] = incoming_metadata
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": tool_input,
        "tool_response": {"task": task},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def _seed_task(tmp_path, team, task_id, **fields):
    """Write ~/.claude/tasks/{team}/{id}.json under the test-scoped HOME so
    the gate's read_task_json resolves the on-disk post-state."""
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _metadata_only_payload(task_id, metadata, agent_type=LEAD):
    """A metadata-only TaskUpdate (no status key → lands in the write-time
    block) — the post-completion backstop's fire surface."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "metadata": metadata},
        "tool_response": {},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def _snapshots(events):
    return [e for e in events if e.get("type") == "task_metadata_snapshot"]


# =============================================================================
# Seam A — lead completion
# =============================================================================
class TestSeamALeadCompletion:
    def test_lead_completion_emits_one_snapshot(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_completion_payload())
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        event = snaps[0]
        assert event["task_id"] == "42"
        assert event["subject"] == "devops: implement the thing"
        assert event["owner"] == "devops"
        assert event["metadata"] == SIBLINGS
        assert "handoff" not in event["metadata"]
        assert isinstance(event["occupant"], str) and event["occupant"]

    def test_handoff_key_excluded_but_siblings_mirrored(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        metadata = {**SIBLINGS, "handoff": VALID_HANDOFF}
        tlg.evaluate_lifecycle(_completion_payload(metadata=metadata))
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert snaps[0]["metadata"] == SIBLINGS

    def test_handoff_only_metadata_no_snapshot(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """Post-exclude payload is empty → ineligible → clean no-emit
        (agent_handoff already covers handoff)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(
            _completion_payload(metadata={"handoff": VALID_HANDOFF})
        )
        assert _snapshots(snapshot_events) == []

    def test_teammate_frame_no_snapshot(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """Both-modes matrix, teammate leg: a non-lead frame must not emit —
        its context has no canonical journal (the tmux-mode teammate
        completion is covered by the TaskCompleted seam instead)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_completion_payload(agent_type=TEAMMATE))
        assert _snapshots(snapshot_events) == []

    def test_teammate_frame_positive_control(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """The identical fixture under the LEAD frame emits — the
        suppression above is the is_lead gate, nothing else."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_completion_payload(agent_type=LEAD))
        assert len(_snapshots(snapshot_events)) == 1

    def test_signal_task_snapshot_emits_handoff_stays_suppressed(
        self,
        tmp_path,
        monkeypatch,
        pact_context,
        snapshot_events,
        handoff_events,
    ):
        """Signal-task INCLUSION with the unconditional leg: a blocker task
        DOES snapshot (task_type mirrored) while the agent_handoff emit for
        the same frame stays suppressed (that event family's reader-purity
        basis is untouched)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        metadata = {
            "type": "blocker",
            "blocker_context": {"halted_on": "auth bypass"},
            "handoff": VALID_HANDOFF,
        }
        tlg.evaluate_lifecycle(_completion_payload(metadata=metadata))
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1, "signal tasks DO snapshot"
        assert snaps[0]["task_type"] == "blocker"
        assert snaps[0]["metadata"]["type"] == "blocker"
        assert "handoff" not in snaps[0]["metadata"]
        # Unconditional leg: no agent_handoff event for the signal task.
        assert [
            e for e in handoff_events if e.get("type") == "agent_handoff"
        ] == []

    def test_teachback_task_snapshots(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """No teachback gate on the snapshot: teachback_submit siblings are
        enumerated load-bearing keys."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(
            _completion_payload(
                subject="devops: TEACHBACK for thing CODE",
                metadata={"teachback_submit": {"understanding": "u"}},
            )
        )
        assert len(_snapshots(snapshot_events)) == 1

    def test_ownerless_task_snapshots_without_owner_field(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_completion_payload(owner=""))
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert "owner" not in snaps[0]

    def test_incoming_metadata_merged_over_disk_view(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """The completing frame's tool_input.metadata is shallow-merged over
        the post-state view — correct whether or not the platform's write
        landed before the hook fired."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(
            _completion_payload(
                metadata={"variety": {"total": 10}},
                incoming_metadata={"r2_verification": {"verified": True}},
            )
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert snaps[0]["metadata"] == {
            "variety": {"total": 10},
            "r2_verification": {"verified": True},
        }

    def test_unchanged_recompletion_dedups_changed_supersedes(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """Content-key dedup: identical payload re-fires collapse to one
        event; a changed payload emits a superseding second event."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        payload = _completion_payload(task_id="77")
        tlg.evaluate_lifecycle(payload)
        tlg.evaluate_lifecycle(payload)
        assert len(_snapshots(snapshot_events)) == 1
        changed = _completion_payload(
            task_id="77",
            metadata={**SIBLINGS, "r2_verification": {"verified": True}},
        )
        tlg.evaluate_lifecycle(changed)
        assert len(_snapshots(snapshot_events)) == 2


# =============================================================================
# Seam B — post-completion backstop
# =============================================================================
class TestSeamBPostCompletionBackstop:
    def test_late_metadata_write_on_completed_task_snapshots(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """A metadata-only TaskUpdate landing on an already-completed task
        (the write-after-completion class) emits a snapshot with the merged
        disk+incoming payload."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path,
            TEAM,
            "55",
            subject="devops: late write probe",
            owner="devops",
            status="completed",
            metadata={"variety": {"total": 8}},
        )
        tlg.evaluate_lifecycle(
            _metadata_only_payload(
                "55", {"r2_verification": {"verified": True}}
            )
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert snaps[0]["task_id"] == "55"
        assert snaps[0]["metadata"] == {
            "variety": {"total": 8},
            "r2_verification": {"verified": True},
        }

    def test_open_task_metadata_write_no_backstop_fire(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """The backstop keys on disk status == completed — a metadata write
        on an OPEN task is normal mid-task traffic, not a missed mirror
        (its completion-time snapshot will cover it)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path,
            TEAM,
            "56",
            subject="devops: open task",
            owner="devops",
            status="in_progress",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _metadata_only_payload("56", {"scope_contract": {"files": []}})
        )
        assert _snapshots(snapshot_events) == []

    def test_teammate_frame_no_backstop_fire(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path,
            TEAM,
            "57",
            subject="devops: teammate frame probe",
            owner="devops",
            status="completed",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _metadata_only_payload(
                "57", {"note": "late"}, agent_type=TEAMMATE
            )
        )
        assert _snapshots(snapshot_events) == []

    def test_unchanged_backstop_refire_dedups_against_seam_a(
        self, tmp_path, monkeypatch, pact_context, snapshot_events
    ):
        """Cross-seam dedup: seam A emitted at completion; a later
        metadata-only re-write of the SAME content no-ops on the shared
        content-keyed marker."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        # Seam A fire at completion.
        tlg.evaluate_lifecycle(
            _completion_payload(
                task_id="58",
                subject="devops: cross-seam dedup",
                metadata={"variety": {"total": 9}},
            )
        )
        assert len(_snapshots(snapshot_events)) == 1
        # Later metadata-only write carrying the SAME resolved content.
        _seed_task(
            tmp_path,
            TEAM,
            "58",
            subject="devops: cross-seam dedup",
            owner="devops",
            status="completed",
            metadata={"variety": {"total": 9}},
        )
        tlg.evaluate_lifecycle(
            _metadata_only_payload("58", {"variety": {"total": 9}})
        )
        assert len(_snapshots(snapshot_events)) == 1, (
            "identical content must dedup across seams via the shared "
            "content-keyed marker"
        )

    def test_handoff_backstop_and_snapshot_backstop_both_fire_sibling(
        self, tmp_path, monkeypatch, pact_context, snapshot_events,
        handoff_events,
    ):
        """The two backstops are SIBLING blocks: a late write that sets
        handoff PLUS a sibling key triggers both — the handoff backstop
        re-emits agent_handoff, the snapshot backstop mirrors the sibling
        (handoff still excluded from the snapshot payload)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path,
            TEAM,
            "59",
            subject="devops: dual backstop",
            owner="devops",
            status="completed",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _metadata_only_payload(
                "59",
                {
                    "handoff": VALID_HANDOFF,
                    "r2_verification": {"verified": True},
                },
            )
        )
        handoffs = [
            e for e in handoff_events if e.get("type") == "agent_handoff"
        ]
        snaps = _snapshots(snapshot_events)
        assert len(handoffs) == 1
        assert len(snaps) == 1
        assert snaps[0]["metadata"] == {"r2_verification": {"verified": True}}
