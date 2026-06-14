"""#955 Component C — hook-side emits of the two GC-immune journal events.

dispatch_variety + teachback_ack mirror per-dispatch variety and the teammate's
variety_acknowledgment into the GC-immune journal so wrap-up Q5/Q6 survive the
teams/tasks reaper (the task store goes false-empty after GC).

Both emit from the existing PostToolUse task_lifecycle_gate, is_lead-gated:
  - dispatch_variety: on the TaskCreate of a Task-B carrying metadata.variety.
    The new Task-B id comes from tool_response.task.id (the create-result
    post-state), falling back to tool_input.taskId. Keyed on metadata.variety
    PRESENCE, NOT owner — per orchestrate.md the TaskCreate(B) sets variety but
    leaves owner empty (owner is wired by a SEPARATE later TaskUpdate).
  - teachback_ack: on the lead's TaskUpdate(A, completed) accepting a teachback;
    reads variety_acknowledgment off the DISK Task-A (the accept TaskUpdate
    carries only status).

Both-modes matrix (M9-M11): lead frame emits; teammate frame self-drops (#877).
Drives tlg.evaluate_lifecycle with tlg.append_event spied to capture events.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402

LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"
VARIETY = {"novelty": 3, "scope": 3, "uncertainty": 3, "risk": 3, "total": 12}

# The canonical 5 keys the dispatch_variety emit must project to (§5.1: the 4
# dimensions + total — the *_rationale strings are NOT mirrored).
CANONICAL_VARIETY_KEYS = {"novelty", "scope", "uncertainty", "risk", "total"}

# A realistic on-disk stamp as the orchestrator writes it: the 5 canonical keys
# PLUS the four per-dimension rationale strings. The emit must DROP the
# rationales (they are calibration noise in the GC-immune journal; wrap-up Q5
# reads only .total). Feeding this proves the projection actually trims.
VARIETY_WITH_RATIONALES = {
    "novelty": 3, "novelty_rationale": "new-but-conventional",
    "scope": 3, "scope_rationale": "bounded diff",
    "uncertainty": 3, "uncertainty_rationale": "careful cold read",
    "risk": 3, "risk_rationale": "high blast radius",
    "total": 12,
}


@pytest.fixture
def emit_events(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
    return events


def _typed(events, event_type):
    return [e for e in events if e.get("type") == event_type]


# =============================================================================
# M9 — dispatch_variety on TaskCreate(Task-B, metadata.variety)
# =============================================================================
class TestM9DispatchVariety:
    def test_lead_taskcreate_with_variety_emits(self, emit_events):
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {"subject": "devops: implement", "metadata": {"variety": VARIETY}},
            "tool_response": {"task": {"id": "99"}},
        })
        dv = _typed(emit_events, "dispatch_variety")
        assert len(dv) == 1
        assert dv[0]["task_id"] == "99"
        assert dv[0]["variety"] == VARIETY
        # The projection is canonical: exactly the 5 §5.1 keys, no extras.
        assert set(dv[0]["variety"].keys()) == CANONICAL_VARIETY_KEYS

    def test_emit_projects_to_canonical_keys_dropping_rationales(self, emit_events):
        """§5.1 trim: a stamp carrying *_rationale strings emits ONLY the 4
        dimensions + total — the rationale keys are NOT mirrored to the journal.
        NON-VACUITY: the input dict explicitly contains four *_rationale keys;
        without the projection the emitted variety would carry all 9 keys."""
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {
                "subject": "devops: implement",
                "metadata": {"variety": VARIETY_WITH_RATIONALES},
            },
            "tool_response": {"task": {"id": "99"}},
        })
        dv = _typed(emit_events, "dispatch_variety")
        assert len(dv) == 1
        emitted = dv[0]["variety"]
        # Exactly the 5 canonical keys — no *_rationale leaked through.
        assert set(emitted.keys()) == CANONICAL_VARIETY_KEYS
        assert not any(k.endswith("_rationale") for k in emitted)
        # Values for the canonical keys are preserved verbatim.
        assert emitted == {
            "novelty": 3, "scope": 3, "uncertainty": 3, "risk": 3, "total": 12,
        }

    def test_emit_tolerates_partial_stamp(self, emit_events):
        """A stamp missing some dimensions emits only the present canonical keys
        (the projection's `if k in` guard) — never KeyErrors, never invents."""
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {
                "subject": "devops: implement",
                # total + a subset of dims, plus a stray rationale to drop.
                "metadata": {"variety": {"total": 7, "novelty": 2,
                                         "novelty_rationale": "x"}},
            },
            "tool_response": {"task": {"id": "99"}},
        })
        dv = _typed(emit_events, "dispatch_variety")
        assert len(dv) == 1
        assert dv[0]["variety"] == {"total": 7, "novelty": 2}

    def test_id_falls_back_to_tool_input_taskid(self, emit_events):
        """If the create-result omits task.id, fall back to tool_input.taskId."""
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {
                "taskId": "77", "subject": "devops: implement",
                "metadata": {"variety": VARIETY},
            },
            "tool_response": {},  # no task.id
        })
        dv = _typed(emit_events, "dispatch_variety")
        assert len(dv) == 1 and dv[0]["task_id"] == "77"

    def test_teammate_frame_no_emit(self, emit_events):
        """M9 dual-mode: teammate frame self-drops (no canonical journal)."""
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": TEAMMATE,
            "tool_input": {"subject": "devops: implement", "metadata": {"variety": VARIETY}},
            "tool_response": {"task": {"id": "99"}},
        })
        assert _typed(emit_events, "dispatch_variety") == []

    def test_no_id_anywhere_skips_emit(self, emit_events):
        """No resolvable id → skip (best-effort; coverage degrades, gate intact)."""
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {"subject": "devops: implement", "metadata": {"variety": VARIETY}},
            "tool_response": {},
        })
        assert _typed(emit_events, "dispatch_variety") == []


# =============================================================================
# M11 — TaskCreate with NO metadata.variety → no dispatch_variety emit
# =============================================================================
class TestM11NoVarietyNoEmit:
    def test_taskcreate_without_variety_no_emit(self, emit_events):
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {"subject": "devops: implement", "metadata": {}},
            "tool_response": {"task": {"id": "99"}},
        })
        assert _typed(emit_events, "dispatch_variety") == []

    def test_taskcreate_empty_variety_no_emit(self, emit_events):
        tlg.evaluate_lifecycle({
            "tool_name": "TaskCreate",
            "agent_type": LEAD,
            "tool_input": {"subject": "devops: implement", "metadata": {"variety": {}}},
            "tool_response": {"task": {"id": "99"}},
        })
        assert _typed(emit_events, "dispatch_variety") == []


# =============================================================================
# M10 — teachback_ack on TaskUpdate(A, completed) with variety_acknowledgment
# =============================================================================
class TestM10TeachbackAck:
    def _ack_payload(self, *, agent_type=LEAD, flag="yes", concern=None):
        ack = {"rationale_articulates_this_dispatch": flag}
        if concern is not None:
            ack["concern"] = concern
        return {
            "tool_name": "TaskUpdate",
            "agent_type": agent_type,
            "tool_input": {"taskId": "41", "status": "completed"},
            "tool_response": {"task": {
                "id": "41",
                "subject": "devops: TEACHBACK for the thing",
                "owner": "devops",
                "metadata": {"teachback_submit": {"variety_acknowledgment": ack}},
            }},
        }

    def test_lead_completion_emits_ack(self, emit_events):
        tlg.evaluate_lifecycle(self._ack_payload(flag="yes"))
        ta = _typed(emit_events, "teachback_ack")
        assert len(ta) == 1
        assert ta[0]["task_id"] == "41"
        assert ta[0]["rationale_articulates_this_dispatch"] == "yes"
        assert "concern" not in ta[0], "a 'yes' ack omits the optional concern"

    def test_ack_with_concern_carries_optional_field(self, emit_events):
        tlg.evaluate_lifecycle(
            self._ack_payload(flag="concern", concern="risk rationale understated")
        )
        ta = _typed(emit_events, "teachback_ack")
        assert len(ta) == 1
        assert ta[0]["rationale_articulates_this_dispatch"] == "concern"
        assert ta[0]["concern"] == "risk rationale understated"

    def test_teammate_frame_no_emit(self, emit_events):
        """M10 dual-mode: teammate frame self-drops."""
        tlg.evaluate_lifecycle(self._ack_payload(agent_type=TEAMMATE))
        assert _typed(emit_events, "teachback_ack") == []

    def test_no_ack_no_emit(self, emit_events):
        """A teachback completion without variety_acknowledgment → no emit
        (the existing variety_acknowledgment_missing advisory covers the gap)."""
        payload = {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "41", "status": "completed"},
            "tool_response": {"task": {
                "id": "41",
                "subject": "devops: TEACHBACK for the thing",
                "owner": "devops",
                "metadata": {"teachback_submit": {"understanding": "x"}},
            }},
        }
        tlg.evaluate_lifecycle(payload)
        assert _typed(emit_events, "teachback_ack") == []

    def test_non_teachback_completion_no_ack(self, emit_events):
        """A WORK-task completion (non-teachback subject) emits no teachback_ack
        even if it somehow carried an ack-shaped field."""
        payload = {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "42", "status": "completed"},
            "tool_response": {"task": {
                "id": "42",
                "subject": "devops: implement the thing",  # work, not teachback
                "owner": "devops",
                "metadata": {"teachback_submit": {
                    "variety_acknowledgment": {"rationale_articulates_this_dispatch": "yes"}
                }},
            }},
        }
        tlg.evaluate_lifecycle(payload)
        assert _typed(emit_events, "teachback_ack") == []
