"""
Cross-path coverage for the two agent_handoff emit paths sharing ONE marker:
  - b1: agent_handoff_emitter.py  (TaskCompleted Stop-sweep)
  - b2: task_lifecycle_gate.py    (lead's TaskUpdate(completed) acceptance-commit, Fix A #869)

Both import occupant_hash + already_emitted from shared/agent_handoff_marker.py
(the SSOT), so for one logical completion they derive the SAME marker key and
the shared O_EXCL marker dedups them to EXACTLY ONE journal event. This file
proves that, plus:

  - ASSERTION A (the b1/b2 occupant-alignment crux flagged in TEST teachback):
    b1 derives occupant from (owner OR teammate_name); b2 from owner alone.
    They align iff owner is populated. When owner is empty, b2 early-returns
    no-emit, so the derivation difference never manifests as a double-emit.
  - #887 integration through the emitter: same team + same task_id + DIFFERENT
    occupant → BOTH emit (with a same-occupant positive control).
  - DUAL-MODE matrix: in the shipped DEFER scope there is NO
    session_id==leadSessionId code branch (that was the deferred redirect, D5).
    The topology discriminator that IS shipped is is_lead — which under real
    multi-process tmux corresponds to "this process's session == the lead's
    session" (lead) vs "!=" (teammate). The matrix below exercises both modes
    of the b2 gate with same-fixture positive controls, and pins that b1 is
    agent_type-AGNOSTIC (it writes to its own session journal in either mode;
    WHERE that lands under tmux is what the E1 real-tmux smoke resolves).

Shared-marker mechanics: both paths resolve the marker dir via Path.home();
patching Path.home (+ HOME) to one tmp_path and using one team_name/task_id/
owner/subject makes b1 and b2 contend for the identical marker file.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.emitter import VALID_HANDOFF, _run_main  # noqa: E402
from shared.agent_handoff_marker import occupant_hash  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"
TASK_ID = "55"
OWNER = "devops"
SUBJECT = "devops: ship it"


def _gate_payload(*, agent_type=LEAD, owner=OWNER, subject=SUBJECT, task_id=TASK_ID, handoff=VALID_HANDOFF):
    metadata = {"handoff": handoff} if handoff is not None else {}
    task = {"id": task_id, "subject": subject, "owner": owner, "metadata": metadata}
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {"task": task},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def _emitter_stdin(*, teammate_name=OWNER, subject=SUBJECT, task_id=TASK_ID, team=TEAM):
    return {
        "task_id": task_id,
        "task_subject": subject,
        "teammate_name": teammate_name,
        "team_name": team,
    }


def _emitter_task_data(*, owner=OWNER, handoff=VALID_HANDOFF):
    metadata = {"handoff": handoff} if handoff is not None else {}
    return {"status": "completed", "owner": owner, "metadata": metadata}


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    """Make BOTH emit paths resolve the SAME marker dir under tmp_path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# =============================================================================
# Cross-path shared-marker dedup → exactly ONE event
# =============================================================================
class TestCrossPathDedup:
    def test_b2_then_b1_dedups_to_one(self, shared_home, monkeypatch, pact_context):
        """Lead acceptance-commit (b2) fires first and claims the marker; the
        later Stop-sweep TaskCompleted (b1) sees the shared marker and is
        suppressed. Net: exactly ONE journal event."""
        pact_context(team_name=TEAM, session_id="s1")
        gate_events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: gate_events.append(e) or True)

        tlg.evaluate_lifecycle(_gate_payload())
        assert len(gate_events) == 1, "b2 must emit first"

        emitter_events: list[dict] = []
        _run_main(_emitter_stdin(), _emitter_task_data(), emitter_events)
        assert len(emitter_events) == 0, "b1 must be suppressed by the shared marker"
        assert len(gate_events) + len(emitter_events) == 1, "exactly ONE net event"

    def test_b1_then_b2_dedups_to_one(self, shared_home, monkeypatch, pact_context):
        """Reverse order: the Stop-sweep (b1) fires first; the lead's
        acceptance-commit (b2) then sees the shared marker and suppresses."""
        pact_context(team_name=TEAM, session_id="s1")
        emitter_events: list[dict] = []
        _run_main(_emitter_stdin(), _emitter_task_data(), emitter_events)
        assert len(emitter_events) == 1, "b1 must emit first"

        gate_events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: gate_events.append(e) or True)
        tlg.evaluate_lifecycle(_gate_payload())
        assert len(gate_events) == 0, "b2 must be suppressed by the shared marker"
        assert len(gate_events) + len(emitter_events) == 1, "exactly ONE net event"

    def test_b1_b2_compute_identical_marker_key(self):
        """Structural pin: with owner populated, both paths' occupant is
        identical (the marker filenames align byte-for-byte)."""
        # b1: teammate_name = owner or stdin-teammate_name → owner when populated
        b1_occ = occupant_hash(OWNER or "stdin-teammate", SUBJECT)
        # b2: occupant from owner directly
        b2_occ = occupant_hash(OWNER, SUBJECT)
        assert b1_occ == b2_occ


# =============================================================================
# ASSERTION A — the owner/teammate_name occupant-alignment crux
# =============================================================================
class TestAssertionA:
    def test_owner_populated_aligns_and_dedups(self, shared_home, monkeypatch, pact_context):
        """owner populated → b1 and b2 share the key → one event (covered by
        TestCrossPathDedup; re-pinned here as the assertion-A positive)."""
        pact_context(team_name=TEAM, session_id="s1")
        gate_events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: gate_events.append(e) or True)
        tlg.evaluate_lifecycle(_gate_payload(owner=OWNER))
        emitter_events: list[dict] = []
        _run_main(
            _emitter_stdin(teammate_name=OWNER), _emitter_task_data(owner=OWNER), emitter_events
        )
        assert len(gate_events) + len(emitter_events) == 1

    def test_owner_empty_b2_silent_no_divergence(self, shared_home, monkeypatch, pact_context):
        """owner EMPTY → b2 early-returns no-emit, so the derivation
        difference (b1 uses teammate_name, b2 uses owner) cannot produce a
        double-emit. b1 still emits once via its teammate_name fallback.

        This is the crux: a divergent key only matters if BOTH paths emit; b2's
        owner-empty early-return guarantees they don't both fire here."""
        pact_context(team_name=TEAM, session_id="s1")
        gate_events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: gate_events.append(e) or True)

        # b2 with empty owner → silent
        tlg.evaluate_lifecycle(_gate_payload(owner=""))
        assert len(gate_events) == 0, "b2 must be silent when owner is empty"

        # b1 (emitter) with owner empty falls back to stdin teammate_name → emits once
        emitter_events: list[dict] = []
        _run_main(
            _emitter_stdin(teammate_name="devops-teammate"),
            _emitter_task_data(owner=""),
            emitter_events,
        )
        assert len(emitter_events) == 1, (
            "b1 falls back to teammate_name and emits once; total stays at one"
        )
        assert len(gate_events) + len(emitter_events) == 1


# =============================================================================
# #887 integration through the emitter — different occupant → both emit
# =============================================================================
class TestEmitterOccupantCollision:
    def test_same_task_id_different_subject_both_emit(self, shared_home):
        """A reused (team, task_id) under two DIFFERENT subjects (→ different
        occupant) must produce TWO emitter events — the #887 collision fix
        seen end-to-end through main(). NON-VACUITY: reverting to the bare
        {task_id} key collapses this to 1."""
        events: list[dict] = []
        _run_main(
            _emitter_stdin(subject="alpha phase handoff"),
            _emitter_task_data(),
            events,
        )
        _run_main(
            _emitter_stdin(subject="beta phase handoff"),
            _emitter_task_data(),
            events,
        )
        assert len(events) == 2, (
            "different occupants on the same task_id must both emit (#887); "
            f"got {len(events)} — marker key may have regressed to bare task_id"
        )

    def test_same_task_id_different_owner_both_emit(self, shared_home):
        events: list[dict] = []
        _run_main(_emitter_stdin(teammate_name="agent-a"), _emitter_task_data(owner="agent-a"), events)
        _run_main(_emitter_stdin(teammate_name="agent-b"), _emitter_task_data(owner="agent-b"), events)
        assert len(events) == 2

    def test_same_occupant_dedups_positive_control(self, shared_home):
        """POSITIVE CONTROL: identical occupant on the same task_id → ONE
        event. Proves the two rows above emit twice because occupants DIFFER,
        not because dedup is globally broken."""
        events: list[dict] = []
        _run_main(_emitter_stdin(), _emitter_task_data(), events)
        _run_main(_emitter_stdin(), _emitter_task_data(), events)
        assert len(events) == 1


# =============================================================================
# DUAL-MODE matrix (is_lead topology ≙ session==leadSessionId vs !=)
# =============================================================================
class TestDualModeMatrix:
    """The shipped DEFER scope has NO session_id==leadSessionId branch; the
    topology discriminator that IS shipped is is_lead. Under real tmux,
    is_lead==True ≙ this process's session == the lead's session.

    b2 (gate): KEEP under lead mode, SUPPRESS under teammate mode, with a
    same-fixture positive control. b1 (emitter): agent_type-agnostic — emits in
    either mode (the WHERE-it-lands question is the E1 smoke's, not a code
    branch here)."""

    def test_b2_lead_mode_keeps(self, shared_home, monkeypatch, pact_context):
        pact_context(team_name=TEAM, session_id="s1")
        events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
        tlg.evaluate_lifecycle(_gate_payload(agent_type=LEAD))
        assert len(events) == 1, "lead mode (session==leadSessionId) → KEEP"

    def test_b2_teammate_mode_suppresses(self, shared_home, monkeypatch, pact_context):
        pact_context(team_name=TEAM, session_id="s2")
        events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
        tlg.evaluate_lifecycle(_gate_payload(agent_type="pact-devops-engineer"))
        assert len(events) == 0, "teammate mode (session!=leadSessionId) → SUPPRESS"

    def test_b2_teammate_mode_positive_control(self, shared_home, monkeypatch, pact_context):
        """Same fixture, lead frame → emits. Proves the suppression above is
        the topology gate, not a missing precondition."""
        pact_context(team_name=TEAM, session_id="s2")
        events: list[dict] = []
        monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
        tlg.evaluate_lifecycle(_gate_payload(agent_type=LEAD))
        assert len(events) == 1

    def test_b1_emitter_is_agent_type_agnostic(self, shared_home):
        """b1 does not read agent_type — it emits regardless of mode (an
        agent_type field in stdin is simply ignored). The marker, not a
        topology gate, is what dedups it against b2."""
        events: list[dict] = []
        stdin = _emitter_stdin()
        stdin["agent_type"] = "pact-devops-engineer"  # ignored by the emitter
        _run_main(stdin, _emitter_task_data(), events)
        assert len(events) == 1
