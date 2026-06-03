"""
#880 remediation (PR #898 review cycle 1) — closes two convergent test gaps:

1. ELIGIBILITY-PARITY (devops Future-minor + architect drift-path): b1 (the
   agent_handoff_emitter) and b2 (task_lifecycle_gate._emit_lead_side_agent_handoff)
   are supposed to fire on the SAME emit-eligibility set. This regression-pins
   that mirrored eligibility — the "#887 divergence shape one level up" — by
   DRIVING THE ACTUAL CODE PATHS over the (owner, handoff, signal, teachback)
   matrix and asserting b1 and b2 agree on emit-vs-suppress. A future edit to
   one path's bypass gates that desyncs the other fails this test.

   NOTE — one DOCUMENTED, BENIGN asymmetry the parity scan surfaces: b2 carries
   an explicit `_is_teachback_subject` exclusion that b1 LACKS. b1 instead
   relies on its handoff-presence gate to suppress teachback tasks (which carry
   metadata.teachback_submit, NOT metadata.handoff). So they CONVERGE on every
   realistic input; they diverge only on the unreachable cell
   (teachback-subject AND handoff-present AND owner AND not-signal), where b1
   would emit and b2 suppresses. That cell is pinned explicitly below so a
   future change in EITHER direction is a deliberate, test-visible decision.

2. FAIL-OPEN (test-engineer M3): _emit_lead_side_agent_handoff wraps its body in
   `except Exception: pass`. Pin that a raising append_event does NOT propagate
   out of evaluate_lifecycle (the gate's advisory eval + exit-0 contract stay
   intact). Non-vacuous: removing the except makes the exception propagate and
   this test fails.

Test-only; no source dependency on devops's concurrent .lower() centralization
(team names here are already lowercase, so .lower() is a no-op either way).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.emitter import VALID_HANDOFF, _run_main  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"


def _subject(teachback: bool) -> str:
    # Matches _TEACHBACK_SUBJECT_PATTERN ^[a-z0-9-]+: TEACHBACK for
    return "agent: TEACHBACK for the mission" if teachback else "agent: do the work"


def _metadata(handoff: bool, signal: bool) -> dict:
    md: dict = {}
    if handoff:
        md["handoff"] = VALID_HANDOFF
    if signal:
        md["type"] = "blocker"  # is_signal_task reads metadata.type
    return md


def _b1_emits(tmp_path, owner: str, handoff: bool, signal: bool, teachback: bool) -> bool:
    """Run the ACTUAL emitter (b1) and report whether it emitted. Fresh marker
    via the dedicated task_id 'e-b1' under tmp_path's HOME."""
    calls: list[dict] = []
    # b1's actor = owner OR stdin teammate_name; mirror owner into teammate_name
    # so the 'owner' dimension drives b1 the same way it drives b2.
    _run_main(
        stdin_payload={
            "task_id": "e-b1",
            "task_subject": _subject(teachback),
            "teammate_name": owner,
            "team_name": TEAM,
        },
        task_data={
            "status": "completed",
            "owner": owner,
            "metadata": _metadata(handoff, signal),
        },
        append_calls=calls,
    )
    return len(calls) == 1


def _b2_emits(monkeypatch, owner: str, handoff: bool, signal: bool, teachback: bool) -> bool:
    """Run the ACTUAL gate emit path (b2) and report whether it emitted. Fresh
    marker via task_id 'e-b2'. is_lead gate satisfied via agent_type=LEAD."""
    events: list[dict] = []
    monkeypatch.setattr(tlg, "append_event", lambda e: events.append(e) or True)
    task = {
        "id": "e-b2",
        "subject": _subject(teachback),
        "owner": owner,
        "metadata": _metadata(handoff, signal),
    }
    tlg.evaluate_lifecycle({
        "agent_type": LEAD,
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "e-b2", "status": "completed"},
        "tool_response": {"task": task},
    })
    return len(events) == 1


# Full (owner, handoff, signal, teachback) matrix — 16 cells.
_MATRIX = [
    (owner, handoff, signal, teachback)
    for owner in ("devops", "")
    for handoff in (True, False)
    for signal in (True, False)
    for teachback in (True, False)
]


class TestEligibilityParity:
    """b1 and b2 must agree on emit-vs-suppress across the eligibility matrix,
    with ONE documented benign asymmetry (teachback-subject + handoff-present).
    Driven off the actual code paths (relational assertion), so any desync of
    the mirrored gates fails this test."""

    @pytest.mark.parametrize("owner,handoff,signal,teachback", _MATRIX)
    def test_b1_b2_eligibility_agree(
        self, tmp_path, monkeypatch, pact_context, owner, handoff, signal, teachback
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s")

        b1 = _b1_emits(tmp_path, owner, handoff, signal, teachback)
        b2 = _b2_emits(monkeypatch, owner, handoff, signal, teachback)

        # The ONLY licensed divergence: b2 has an explicit teachback-subject
        # exclusion that b1 lacks (b1 relies on handoff-absence for realistic
        # teachback tasks). It manifests only when a teachback-subject task ALSO
        # carries a handoff AND has an owner AND is not a signal task — an
        # unreachable shape in practice (teachback tasks carry teachback_submit).
        divergence_cell = bool(owner) and handoff and (not signal) and teachback

        if divergence_cell:
            assert b1 is True and b2 is False, (
                "documented asymmetry changed: b2 has a teachback-subject "
                "exclusion that b1 lacks. If a path changed, update deliberately. "
                f"got b1={b1}, b2={b2}"
            )
        else:
            assert b1 == b2, (
                f"b1/b2 eligibility DESYNCED at "
                f"(owner={owner!r}, handoff={handoff}, signal={signal}, "
                f"teachback={teachback}): b1={b1}, b2={b2}. The mirrored "
                "emit-eligibility (the #887 divergence shape one level up) "
                "has drifted."
            )

    def test_fully_eligible_both_emit_positive_control(self, tmp_path, monkeypatch, pact_context):
        """Vacuity guard: the all-eligible cell (owner + handoff + not-signal +
        not-teachback) makes BOTH paths emit — so the agreement above isn't
        'both always suppress'."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s")
        assert _b1_emits(tmp_path, "devops", True, False, False) is True
        assert _b2_emits(monkeypatch, "devops", True, False, False) is True

    def test_documented_teachback_asymmetry_explicit(self, tmp_path, monkeypatch, pact_context):
        """Pin the one asymmetry on its own so it is unmistakable: a
        teachback-subject task that (unrealistically) carries a handoff →
        b1 EMITS (no teachback check), b2 SUPPRESSES (explicit teachback
        exclusion). Benign because teachback tasks carry teachback_submit, not
        handoff — but the mechanisms differ, and that is pinned here."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s")
        assert _b1_emits(tmp_path, "devops", True, False, True) is True
        assert _b2_emits(monkeypatch, "devops", True, False, True) is False


class TestGateEmitFailOpen:
    """M3: _emit_lead_side_agent_handoff's `except Exception: pass` must fail
    open — a raising append_event MUST NOT propagate out of evaluate_lifecycle.
    Non-vacuous: removing the except makes evaluate_lifecycle raise and this
    test fails."""

    def test_append_event_raise_does_not_propagate(self, tmp_path, monkeypatch, pact_context):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s")

        def _boom(_event):
            raise RuntimeError("simulated journal-write failure")

        monkeypatch.setattr(tlg, "append_event", _boom)

        task = {
            "id": "fo",
            "subject": "devops: do the work",
            "owner": "devops",
            "metadata": {"handoff": VALID_HANDOFF},
        }
        payload = {
            "agent_type": LEAD,  # is_lead True → reaches the emit (which raises)
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "fo", "status": "completed"},
            "tool_response": {"task": task},
        }

        # Must return normally (a list of advisories), NOT raise. If the
        # except: pass is removed, RuntimeError propagates and this fails.
        advisories = tlg.evaluate_lifecycle(payload)
        assert isinstance(advisories, list), (
            "evaluate_lifecycle must return its advisory list even when the "
            "lead-side emit's append_event raises — the fail-open except must "
            "absorb it (exit-0 / advisory-eval contract intact)."
        )

    def test_fail_open_does_not_suppress_unrelated_advisories(self, tmp_path, monkeypatch, pact_context):
        """The emit failure must not swallow the gate's normal advisory
        evaluation. A teammate self-completion (no is_lead → emit not reached,
        but an unrelated advisory path) still evaluates — sanity that the emit
        try/except is scoped to the emit, not the whole gate."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="s")

        def _boom(_event):
            raise RuntimeError("simulated journal-write failure")

        monkeypatch.setattr(tlg, "append_event", _boom)
        # Lead frame, work task with handoff → emit path is hit and raises;
        # evaluate still completes and returns a list.
        task = {"id": "fo2", "subject": "devops: work", "owner": "devops",
                "metadata": {"handoff": VALID_HANDOFF}}
        result = tlg.evaluate_lifecycle({
            "agent_type": LEAD, "tool_name": "TaskUpdate",
            "tool_input": {"taskId": "fo2", "status": "completed"},
            "tool_response": {"task": task},
        })
        assert isinstance(result, list)
