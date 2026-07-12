"""
Location: pact-plugin/tests/test_per_write_snapshot_seam.py
Summary: Seam tests for the per-write task_metadata_snapshot mirror inside
         task_lifecycle_gate.py — the open-task TaskUpdate leg that fires
         when a metadata delta carries a targeted key (PER_WRITE_MIRROR_KEYS)
         in a canonical journal frame. Covers the both-modes frame matrix
         (lead / in-process teammate / tmux teammate / tmux lead — the
         is_lead + session_id-vs-leadSessionId structural signals), the
         byte-identical default for untargeted traffic, unchanged-rewrite
         dedup, cross-seam dedup against the lead-completion seam, and
         delete-only (None-valued targeted key) semantics. The tmux teammate
         row asserts WHERE events land (no journal file, no marker claim),
         not merely that nothing raises.
Used by: pytest (CODE-phase verification for the per-write seam; adversarial
         payload edges and live-mode depth are TEST phase work).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.task_metadata_snapshot as tms  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"

LEAD_SID = "lead-session-0001"
TMUX_SID = "teammate-session-0002"

SCOPE = {"files": ["a.py"], "boundaries": "backend only"}


@pytest.fixture
def snapshot_events(monkeypatch):
    """Spy on the SUBSTRATE's append_event binding — the per-write leg routes
    snapshot writes through shared.task_metadata_snapshot. Marker claims stay
    REAL (under the test HOME), so dedup rows exercise the actual O_EXCL
    content-key path."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tms, "append_event", _spy)
    return events


def _snapshots(events):
    return [e for e in events if e.get("type") == "task_metadata_snapshot"]


def _seed_task(tmp_path, team, task_id, **fields):
    """Write ~/.claude/tasks/{team}/{id}.json under the test-scoped HOME so
    the gate's read_task_json resolves the on-disk state."""
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _seed_team_config(tmp_path, team=TEAM, lead_session_id=LEAD_SID):
    """Write ~/.claude/teams/{team}/config.json carrying leadSessionId —
    the topology leg's compare source."""
    team_dir = tmp_path / ".claude" / "teams" / team
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"leadSessionId": lead_session_id}), encoding="utf-8"
    )


def _open_write_payload(task_id, metadata, agent_type=LEAD, session_id=None):
    """A metadata-only TaskUpdate (no status key → lands in the write-time
    block) — the per-write mirror's fire surface."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "metadata": metadata},
        "tool_response": {},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def _completion_payload(task_id, subject, owner, metadata):
    """A lead TaskUpdate(status=completed) frame with post-state via
    tool_response.task — drives the lead-completion seam for the
    cross-seam dedup row."""
    return {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {
            "task": {
                "id": task_id,
                "subject": subject,
                "owner": owner,
                "metadata": metadata,
            }
        },
        "agent_type": LEAD,
    }


def _journal_files(tmp_path):
    """Every journal file under the test HOME — the WHERE-it-lands oracle."""
    return list(tmp_path.rglob("*.jsonl"))


def _marker_files(tmp_path):
    """Every claimed snapshot content-hash marker under the test HOME."""
    teams_root = tmp_path / ".claude" / "teams"
    if not teams_root.exists():
        return []
    return [
        p
        for p in teams_root.rglob("*")
        if p.is_file() and p.parent.name == tms.SNAPSHOT_MARKER_NAMESPACE
    ]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Test-scoped HOME: tasks dir, teams dir (config + markers), and the
    journal resolution all land under tmp_path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


# =============================================================================
# Frame matrix — rows keyed by (agent_type, session_id vs leadSessionId)
# =============================================================================
class TestPerWriteFrameMatrix:
    def test_lead_frame_open_task_targeted_key_emits(
        self, home, pact_context, snapshot_events
    ):
        """Lead frame (in-process), open task + targeted key → 1 snapshot
        carrying the disk∪delta overlay."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "60",
            subject="scope: atomize sub-scope",
            owner="team-lead",
            status="in_progress",
            metadata={"note": "existing"},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "60", {"scope_contract": SCOPE}, session_id=LEAD_SID
            )
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        event = snaps[0]
        assert event["task_id"] == "60"
        assert event["subject"] == "scope: atomize sub-scope"
        assert event["owner"] == "team-lead"
        assert event["metadata"] == {
            "note": "existing",
            "scope_contract": SCOPE,
        }

    def test_in_process_teammate_frame_emits(
        self, home, pact_context, snapshot_events
    ):
        """Teammate frame, session_id == leadSessionId (in-process): the
        topology leg admits the frame — positive control proving the gate
        is NOT is_lead-only (the teammate-written teachback_submit leg
        would otherwise be dead by construction)."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_team_config(home)
        _seed_task(
            home,
            TEAM,
            "61",
            subject="backend-coder: TEACHBACK gate",
            owner="backend-coder",
            status="in_progress",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "61",
                {"teachback_submit": {"understanding": "u"}},
                agent_type=TEAMMATE,
                session_id=LEAD_SID,
            )
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert snaps[0]["metadata"] == {
            "teachback_submit": {"understanding": "u"}
        }

    def test_tmux_teammate_frame_no_event_anywhere_no_marker(
        self, home, pact_context
    ):
        """Teammate frame, session_id != leadSessionId (tmux): skip by
        predicate. NO spy here on purpose — this row asserts WHERE writes
        land: ZERO journal files anywhere under the test HOME and ZERO
        content-hash markers claimed. A silo'd emit or a poisoned shared
        marker (suppressing a later canonical emit) is the hazard the
        frame gate exists to exclude."""
        pact_context(team_name=TEAM, session_id=TMUX_SID)
        _seed_team_config(home)  # leadSessionId=LEAD_SID != TMUX_SID
        _seed_task(
            home,
            TEAM,
            "62",
            subject="backend-coder: TEACHBACK gate",
            owner="backend-coder",
            status="in_progress",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "62",
                {"teachback_submit": {"understanding": "u"}},
                agent_type=TEAMMATE,
                session_id=TMUX_SID,
            )
        )
        assert _journal_files(home) == []
        assert _marker_files(home) == []

    def test_tmux_lead_frame_emits(self, home, pact_context, snapshot_events):
        """Lead frame with a session_id that does NOT match the seeded
        leadSessionId: the is_lead leg is independent of the topology
        compare, so the lead keeps full both-modes coverage."""
        pact_context(team_name=TEAM, session_id="lead-tmux-other")
        _seed_team_config(home, lead_session_id="a-different-lead")
        _seed_task(
            home,
            TEAM,
            "63",
            subject="scope: atomize sub-scope",
            owner="team-lead",
            status="in_progress",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "63",
                {"worktree_path": "/tmp/wt"},
                session_id="lead-tmux-other",
            )
        )
        assert len(_snapshots(snapshot_events)) == 1


# =============================================================================
# Fire-predicate semantics
# =============================================================================
class TestPerWriteFirePredicate:
    def test_non_targeted_key_no_emit(
        self, home, pact_context, snapshot_events
    ):
        """Byte-identical default: untargeted traffic never reaches the
        leg — an open-task write of a non-registry key emits nothing."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "64",
            subject="devops: open task",
            owner="devops",
            status="in_progress",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "64", {"note": "mid-task"}, session_id=LEAD_SID
            )
        )
        assert _snapshots(snapshot_events) == []

    def test_unchanged_targeted_rewrite_dedups(
        self, home, pact_context, snapshot_events
    ):
        """An identical targeted rewrite no-ops on the REAL content-hash
        marker: two fires, one event, one marker."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "65",
            subject="scope: atomize sub-scope",
            owner="team-lead",
            status="in_progress",
            metadata={},
        )
        payload = _open_write_payload(
            "65", {"scope_contract": SCOPE}, session_id=LEAD_SID
        )
        tlg.evaluate_lifecycle(payload)
        tlg.evaluate_lifecycle(payload)
        assert len(_snapshots(snapshot_events)) == 1
        assert len(_marker_files(home)) == 1

    def test_cross_seam_dedup_with_completion(
        self, home, pact_context, snapshot_events
    ):
        """Whole-payload mirroring makes the per-write emit and a later
        acceptance completion with identical final content collapse into
        ONE event (shared content-key marker across seams); changed
        content emits a superseding second event."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "66",
            subject="scope: atomize sub-scope",
            owner="team-lead",
            status="in_progress",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "66", {"scope_contract": SCOPE}, session_id=LEAD_SID
            )
        )
        assert len(_snapshots(snapshot_events)) == 1
        # Acceptance completion with the IDENTICAL final content → dedup.
        tlg.evaluate_lifecycle(
            _completion_payload(
                "66",
                "scope: atomize sub-scope",
                "team-lead",
                {"scope_contract": SCOPE},
            )
        )
        assert len(_snapshots(snapshot_events)) == 1
        # Changed content at completion → superseding second event.
        tlg.evaluate_lifecycle(
            _completion_payload(
                "66",
                "scope: atomize sub-scope",
                "team-lead",
                {"scope_contract": {**SCOPE, "boundaries": "widened"}},
            )
        )
        assert len(_snapshots(snapshot_events)) == 2

    def test_delete_only_targeted_write_mirrors_post_delete_state(
        self, home, pact_context, snapshot_events
    ):
        """A None-valued targeted key is the platform DELETE op and COUNTS
        as a fire: the overlay drops the None and mirrors the post-delete
        disk state (the platform applied the delete before PostToolUse)."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "67",
            subject="scope: atomize sub-scope",
            owner="team-lead",
            status="in_progress",
            # Post-delete disk state: scope_contract already removed by the
            # platform; a sibling survives.
            metadata={"worktree_path": "/tmp/wt"},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "67", {"scope_contract": None}, session_id=LEAD_SID
            )
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert snaps[0]["metadata"] == {"worktree_path": "/tmp/wt"}
        assert "scope_contract" not in snaps[0]["metadata"]

    def test_completed_disk_status_routes_to_backstop_not_this_leg(
        self, home, pact_context, snapshot_events
    ):
        """Disjointness with the post-completion backstop on the same
        task_a.status read: a targeted write on an ALREADY-completed task
        is the backstop's surface, and the two legs together emit exactly
        ONE event (they would dedup on content anyway; the status split
        means only one leg fires at all)."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "68",
            subject="scope: atomize sub-scope",
            owner="team-lead",
            status="completed",
            metadata={},
        )
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "68", {"scope_contract": SCOPE}, session_id=LEAD_SID
            )
        )
        assert len(_snapshots(snapshot_events)) == 1

    def test_missing_task_file_treated_open_delta_only_payload(
        self, home, pact_context, snapshot_events
    ):
        """Fail-toward-mirroring: a missing/drained task file at fire time
        is treated as open and mirrors the delta-only payload (mirror what
        the write shows us) — over-firing dedups, under-firing loses the
        exact write the drain hazard targets."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        tlg.evaluate_lifecycle(
            _open_write_payload(
                "69", {"scope_contract": SCOPE}, session_id=LEAD_SID
            )
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        assert snaps[0]["metadata"] == {"scope_contract": SCOPE}


# =============================================================================
# TaskCreate leg
# =============================================================================
class TestPerWriteTaskCreateLeg:
    def _create_payload(self, task_id, metadata, subject="scope: sub-scope"):
        """A TaskCreate PostToolUse frame: the platform-assigned id exists
        only in the create response (tool_response.task.id)."""
        return {
            "tool_name": "TaskCreate",
            "tool_input": {"subject": subject, "metadata": metadata},
            "tool_response": {"task": {"id": task_id}},
            "agent_type": LEAD,
            "session_id": LEAD_SID,
        }

    def test_create_with_targeted_key_emits_keyed_to_response_id(
        self, home, pact_context, snapshot_events
    ):
        """TaskCreate carrying a targeted key → 1 snapshot keyed to
        tool_response.task.id, with subject/owner resolved from the
        just-created on-disk file (platform write lands before
        PostToolUse) overlaid with the incoming delta."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        _seed_task(
            home,
            TEAM,
            "70",
            subject="scope: sub-scope",
            owner="team-lead",
            status="pending",
            metadata={"scope_contract": SCOPE},
        )
        tlg.evaluate_lifecycle(
            self._create_payload("70", {"scope_contract": SCOPE})
        )
        snaps = _snapshots(snapshot_events)
        assert len(snaps) == 1
        event = snaps[0]
        assert event["task_id"] == "70"
        assert event["subject"] == "scope: sub-scope"
        assert event["metadata"] == {"scope_contract": SCOPE}

    def test_create_without_targeted_key_no_emit(
        self, home, pact_context, snapshot_events
    ):
        """Byte-identical default on the create surface: an untargeted
        initial metadata emits nothing."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        tlg.evaluate_lifecycle(
            self._create_payload("71", {"note": "plain create"})
        )
        assert _snapshots(snapshot_events) == []

    def test_create_with_unresolvable_id_skips(
        self, home, pact_context, snapshot_events
    ):
        """Missing id → skip (coverage degrades by one write; the gate is
        never broken) — the dispatch_variety posture."""
        pact_context(team_name=TEAM, session_id=LEAD_SID)
        payload = {
            "tool_name": "TaskCreate",
            "tool_input": {
                "subject": "scope: sub-scope",
                "metadata": {"scope_contract": SCOPE},
            },
            "tool_response": {},
            "agent_type": LEAD,
            "session_id": LEAD_SID,
        }
        tlg.evaluate_lifecycle(payload)
        assert _snapshots(snapshot_events) == []

    def test_tmux_teammate_create_no_event_no_marker(
        self, home, pact_context
    ):
        """The create leg carries its own frame gate: a tmux teammate frame
        (session_id != leadSessionId) writes nothing anywhere and claims no
        marker."""
        pact_context(team_name=TEAM, session_id=TMUX_SID)
        _seed_team_config(home)
        _seed_task(
            home,
            TEAM,
            "72",
            subject="scope: sub-scope",
            owner="team-lead",
            status="pending",
            metadata={},
        )
        payload = {
            "tool_name": "TaskCreate",
            "tool_input": {
                "subject": "scope: sub-scope",
                "metadata": {"scope_contract": SCOPE},
            },
            "tool_response": {"task": {"id": "72"}},
            "agent_type": TEAMMATE,
            "session_id": TMUX_SID,
        }
        tlg.evaluate_lifecycle(payload)
        assert _journal_files(home) == []
        assert _marker_files(home) == []
