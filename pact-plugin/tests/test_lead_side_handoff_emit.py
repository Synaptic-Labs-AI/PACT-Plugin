"""
Fix A (#869) — lead-side agent_handoff emission at the lead's acceptance-commit
(task_lifecycle_gate.py block ③, _emit_lead_side_agent_handoff).

#869 gap: agent_handoff is TaskCompleted-keyed; a stage-ready task completes
mid-turn so it is already "completed" at the lead's Stop-sweep → swept over →
the TaskCompleted-keyed agent_handoff_emitter never fires → the HANDOFF never
lands in the lead's canonical journal. Fix A re-emits it from the lead's
TaskUpdate(status="completed") acceptance-commit, where the lead's process has
a populated context.

This file drives tlg.evaluate_lifecycle(payload) with tlg.append_event spied to
capture emitted events, exercising the b2 emit-eligibility discriminator (which
MIRRORS agent_handoff_emitter's b1 gates) + the is_lead topology gate. Every
SUPPRESS row carries a same-fixture POSITIVE CONTROL proving the suppression is
the intended discriminator firing, not an unrelated missing precondition.

is_lead reads input_data["agent_type"] DIRECTLY against
{"PACT:pact-orchestrator", "pact-orchestrator"} (pact_context.LEAD_AGENT_TYPES).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.emitter import VALID_HANDOFF  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"


@pytest.fixture
def emit_events(monkeypatch):
    """Spy on the gate's append_event so emitted agent_handoff events are
    captured instead of written to a journal."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tlg, "append_event", _spy)
    return events


def _payload(
    *,
    agent_type=LEAD,
    task_id="42",
    subject="devops: implement Fix A",
    owner="devops",
    handoff=VALID_HANDOFF,
    task_type=None,
):
    """Build a TaskUpdate(status=completed) PostToolUse payload. Post-state is
    supplied via tool_response.task (the gate's preferred source)."""
    metadata: dict = {}
    if handoff is not None:
        metadata["handoff"] = handoff
    if task_type is not None:
        metadata["type"] = task_type
    task = {"id": task_id, "subject": subject, "owner": owner, "metadata": metadata}
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {"task": task},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


# =============================================================================
# Positive: lead + work + handoff → emit
# =============================================================================
class TestLeadEmits:
    def test_lead_with_handoff_emits_one(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload())
        assert len(emit_events) == 1, "lead acceptance-commit on a work task with handoff must emit"

    def test_emitted_event_payload_shape(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(
            _payload(task_id="77", subject="devops: ship it", owner="devops")
        )
        assert len(emit_events) == 1
        ev = emit_events[0]
        assert ev["type"] == "agent_handoff"
        assert ev["agent"] == "devops"
        assert ev["task_id"] == "77"
        assert ev["task_subject"] == "devops: ship it"
        assert ev["handoff"] == VALID_HANDOFF

    @pytest.mark.parametrize("spelling", ["PACT:pact-orchestrator", "pact-orchestrator"])
    def test_both_qualified_lead_spellings_emit(
        self, tmp_path, monkeypatch, pact_context, emit_events, spelling
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(agent_type=spelling))
        assert len(emit_events) == 1, f"lead spelling {spelling!r} must emit"


# =============================================================================
# Topology gate (is_lead) — SUPPRESS rows + same-fixture positive controls
# =============================================================================
class TestIsLeadTopologyGate:
    def test_teammate_frame_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(agent_type="pact-devops-engineer"))
        assert len(emit_events) == 0, (
            "a teammate process (is_lead False) must NOT emit — its context "
            "has no canonical journal; only the lead's process emits"
        )

    def test_teammate_frame_positive_control_lead_same_fixture_emits(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """Positive control: the identical fixture under the LEAD frame DOES
        emit — proving the suppression above is the is_lead gate, not a
        missing handoff/owner."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(agent_type=LEAD))
        assert len(emit_events) == 1

    def test_empty_agent_type_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        """is_lead('') is False (empty agent_type fail-safe)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(agent_type=""))
        assert len(emit_events) == 0

    def test_missing_agent_type_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(agent_type=None))
        assert len(emit_events) == 0

    def test_is_lead_empty_string_predicate(self):
        """Direct predicate pin: is_lead('') and the non-lead spellings."""
        import shared.pact_context as ctx

        assert ctx.is_lead({"agent_type": ""}) is False
        assert ctx.is_lead({}) is False
        assert ctx.is_lead({"agent_type": "pact-devops-engineer"}) is False
        assert ctx.is_lead({"agent_type": "PACT:pact-orchestrator"}) is True
        assert ctx.is_lead({"agent_type": "pact-orchestrator"}) is True


# =============================================================================
# Emit-eligibility discriminator (mirrors b1) — SUPPRESS rows + positive controls
# =============================================================================
class TestEmitEligibilityMirrorsB1:
    @pytest.mark.parametrize("signal_type", ["blocker", "algedonic"])
    def test_signal_task_no_emit(
        self, tmp_path, monkeypatch, pact_context, emit_events, signal_type
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(task_type=signal_type))
        assert len(emit_events) == 0, (
            f"signal task ({signal_type}) must not emit a phantom agent_handoff"
        )

    def test_signal_task_positive_control(self, tmp_path, monkeypatch, pact_context, emit_events):
        """Same fixture WITHOUT the signal type → emits (proves the
        suppression is the signal-task gate, not something else)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(task_type=None))
        assert len(emit_events) == 1

    def test_teachback_subject_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(
            _payload(subject="test-engineer: TEACHBACK for #880 TEST", owner="test-engineer")
        )
        assert len(emit_events) == 0, "a teachback task is not a HANDOFF-bearing work task"

    def test_teachback_subject_positive_control(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """Same owner, NON-teachback subject → emits."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(
            _payload(subject="test-engineer: write the tests", owner="test-engineer")
        )
        assert len(emit_events) == 1

    def test_no_handoff_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(handoff=None))
        assert len(emit_events) == 0, "no metadata.handoff → nothing to preserve"

    def test_empty_handoff_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(handoff={}))
        assert len(emit_events) == 0, "empty (falsy) handoff → suppress"

    def test_no_handoff_positive_control(self, tmp_path, monkeypatch, pact_context, emit_events):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(handoff=VALID_HANDOFF))
        assert len(emit_events) == 1

    def test_owner_empty_no_emit(self, tmp_path, monkeypatch, pact_context, emit_events):
        """owner-empty → b2 early-returns no-emit. (This is the owner half of
        the b1/b2 occupant-alignment crux — see test_handoff_b1_b2_dedup.py.)"""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        tlg.evaluate_lifecycle(_payload(owner=""))
        assert len(emit_events) == 0


# =============================================================================
# Re-completion dedup (shared occupant marker)
# =============================================================================
class TestReCompletionDedup:
    def test_lead_recompletion_dedups_to_one(
        self, tmp_path, monkeypatch, pact_context, emit_events
    ):
        """The lead may TaskUpdate(completed) the same task more than once
        (e.g. a metadata correction). The shared occupant marker must collapse
        re-emits to exactly ONE journal event."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s1")
        p = _payload(task_id="99", subject="devops: dedup probe", owner="devops")
        tlg.evaluate_lifecycle(p)
        tlg.evaluate_lifecycle(p)
        tlg.evaluate_lifecycle(p)
        assert len(emit_events) == 1, (
            f"re-completion must dedup to 1 via the occupant marker; got {len(emit_events)}"
        )
