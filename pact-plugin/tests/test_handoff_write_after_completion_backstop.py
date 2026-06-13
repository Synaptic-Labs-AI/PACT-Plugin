"""#956 — write-time backstop for the agent_handoff write-after-completion race.

THE RACE: agent_handoff has two pre-existing emit paths — b1 (TaskCompleted-keyed,
agent_handoff_emitter.py) and b2 (lead-side at the completing TaskUpdate,
task_lifecycle_gate._emit_lead_side_agent_handoff). BOTH gate on metadata.handoff
PRESENCE at completion time. So when a task is completed BEFORE its handoff is
written, and the handoff is then SET via a later metadata-only TaskUpdate on the
already-completed task, NEITHER path re-fires — the event is silently lost.

THE BACKSTOP: the gate's ④ block (`TaskUpdate && status != "completed"`) observes
that later metadata-only write and re-calls the b2 emitter. The shared O_EXCL
occupant marker makes the re-fire idempotent (dedups against any prior b1/b2 fire).

D4 PRECONDITION (the canary): the backstop only works if a metadata-only TaskUpdate
setting handoff actually REACHES the ④ block at runtime. The architect source-
confirmed this; `test_d4_metadata_only_taskupdate_reaches_block` is the runtime
proof (uses an EXISTING ④-block rule, independent of the backstop, so it stays a
true reachability canary even if the backstop logic regresses).

Driving pattern mirrors test_lead_side_handoff_emit.py: spy on tlg.append_event to
capture emitted events; seed a real on-disk task.json so the gate's read_task_json
resolves the completed-task post-state; key is_lead on agent_type (the only tmux-safe
discriminator).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"
HANDOFF = {"decisions": ["x"], "produced": ["f.py"]}


@pytest.fixture
def emit_events(monkeypatch):
    """Capture events the gate would append, instead of writing a journal."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tlg, "append_event", _spy)
    return events


def _seed_task(tmp_path, team, task_id, **fields):
    """Write ~/.claude/tasks/{team}/{id}.json under the test-scoped HOME so the
    gate's read_task_json resolves the on-disk post-state."""
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _metadata_only_handoff_update(task_id, handoff=HANDOFF, agent_type=LEAD):
    """A metadata-only TaskUpdate that SETS handoff — NO status key (so
    status != "completed"), landing in the ④ block. tool_response empty so the
    gate falls back to the on-disk task record."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "metadata": {"handoff": handoff}},
        "tool_response": {},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


# =============================================================================
# D4 precondition — runtime reachability of the ④ block by a metadata-only update
# =============================================================================
class TestD4Reachability:
    def test_d4_metadata_only_taskupdate_reaches_block(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """CANARY: a metadata-only TaskUpdate setting metadata.handoff reaches
        the ④ block. Proven via an EXISTING ④-block rule
        (reasoning_reconstruction_in_handoff at the wrong-slot check) so this
        stays a reachability proof independent of the backstop — if the ④ block
        ever stops observing metadata-only updates, this fails FIRST."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        payload = {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {
                "taskId": "1",
                "metadata": {
                    "handoff": {
                        "decisions": ["x"],
                        # rr nested in handoff = the ④-block wrong-slot rule
                        "reasoning_reconstruction": {"a": "b"},
                    }
                },
            },
            "tool_response": {},
        }
        advisories = tlg.evaluate_lifecycle(payload)
        rules = [rule for rule, _ in advisories]
        assert "reasoning_reconstruction_in_handoff" in rules, (
            "the ④ block must observe a metadata-only TaskUpdate; if this fails, "
            "the backstop's host block is unreachable — STOP and escalate (D4)"
        )


# =============================================================================
# M7 — the backstop fires on the write-after-completion race
# =============================================================================
class TestBackstopFires:
    def test_backstop_fires_on_metadata_only_taskupdate_after_completion(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """The core #956 fix: handoff SET via a metadata-only TaskUpdate on an
        ALREADY-completed task re-emits agent_handoff via the backstop."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},  # completed, NO handoff yet
        )
        tlg.evaluate_lifecycle(_metadata_only_handoff_update("42"))
        assert len(emit_events) == 1, "backstop must re-emit the lost agent_handoff"
        ev = emit_events[0]
        assert ev["type"] == "agent_handoff"
        assert ev["agent"] == "devops"
        assert ev["task_id"] == "42"
        assert ev["task_subject"] == "devops: CODE the thing"
        assert ev["handoff"] == HANDOFF

    def test_backstop_both_modes_teammate_frame_no_emit(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """M7 dual-mode variant: the identical race under a TEAMMATE frame
        (is_lead False) does NOT emit — a teammate process has no canonical
        journal and self-drops (#877). is_lead is the only tmux-safe
        discriminator."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={},
        )
        tlg.evaluate_lifecycle(
            _metadata_only_handoff_update("42", agent_type=TEAMMATE)
        )
        assert len(emit_events) == 0, "teammate frame must self-drop the backstop emit"


# =============================================================================
# Backstop scoping — only the RACE (handoff on an already-completed task)
# =============================================================================
class TestBackstopScoping:
    def test_no_emit_when_task_not_yet_completed(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """If the task is NOT yet completed, b2 will fire normally at the later
        completion — no backstop needed. Scoping to status==completed keeps the
        intent precise (a handoff-then-complete two-step is not the race)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="pending", metadata={},
        )
        tlg.evaluate_lifecycle(_metadata_only_handoff_update("42"))
        assert len(emit_events) == 0, "non-completed task → no backstop (b2 covers it later)"

    def test_no_emit_for_teachback_subject(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """A teachback Task-A is not HANDOFF-bearing; the emitter's own
        eligibility (not-teachback) suppresses even if a handoff is somehow set."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path, TEAM, "A",
            subject="devops: TEACHBACK for the thing", owner="devops",
            status="completed", metadata={},
        )
        tlg.evaluate_lifecycle(_metadata_only_handoff_update("A"))
        assert len(emit_events) == 0, "teachback subject is not a HANDOFF-bearing task"

    def test_no_emit_when_no_owner(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """No owner → not a teammate work task; the emitter early-returns."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="",
            status="completed", metadata={},
        )
        tlg.evaluate_lifecycle(_metadata_only_handoff_update("42"))
        assert len(emit_events) == 0


# =============================================================================
# M8 — idempotent dedup against a prior b2 fire (shared O_EXCL occupant marker)
# =============================================================================
class TestBackstopDedup:
    def test_backstop_dedups_against_b2(self, tmp_path, monkeypatch, pact_context):
        """Complete a task WITH handoff present (b2 fires) → then a metadata-only
        handoff-set TaskUpdate on the now-completed task (the backstop path) →
        still exactly ONE agent_handoff event.

        The shared O_EXCL occupant marker (claimed by the FIRST emit) runs on
        the REAL filesystem; append_event is spied only to COUNT emits. Both
        Path.home and HOME point at one tmp_path so b2 and the backstop contend
        for the identical marker file (mirrors test_handoff_b1_b2_dedup's
        shared_home idiom). NON-VACUITY: without the marker, the count is 2."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id="s1")

        # One spy list shared across BOTH emit attempts.
        events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)

        # b2 fire: lead completes a work task carrying a populated handoff. This
        # claims the occupant marker on disk.
        b2_payload = {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "42", "status": "completed"},
            "tool_response": {
                "task": {
                    "id": "42",
                    "subject": "devops: CODE the thing",
                    "owner": "devops",
                    "metadata": {"handoff": HANDOFF},
                }
            },
        }
        tlg.evaluate_lifecycle(b2_payload)
        assert len(events) == 1, "b2 must emit first (claims the marker)"

        # Backstop path: a later metadata-only handoff-set TaskUpdate on the
        # (now seeded as) completed task. The shared marker is already claimed →
        # already_emitted() short-circuits → no second emit.
        _seed_task(
            tmp_path, TEAM, "42",
            subject="devops: CODE the thing", owner="devops",
            status="completed", metadata={"handoff": HANDOFF},
        )
        tlg.evaluate_lifecycle(_metadata_only_handoff_update("42"))
        assert len(events) == 1, (
            f"b2 + backstop must dedup to exactly ONE agent_handoff; got {len(events)}"
        )
