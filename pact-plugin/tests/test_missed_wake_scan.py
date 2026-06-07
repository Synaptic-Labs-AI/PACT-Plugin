"""
Location: pact-plugin/tests/test_missed_wake_scan.py
Summary: BEHAVIORAL coverage for the #903 deferred lead-side missed-wake alarm
         (missed_wake_scan.py). The symmetric FP/TP RE-ARM matrix — the #897
         cry-wolf killer — plus is_lead both-modes gating. The structural layer
         (hooks.json registration, journal-schema completeness, livelock
         invariant) is covered by devops's test_hooks_json /
         test_dogfood_livelock_invariant / test_session_journal samples; this
         file does NOT re-assert those — it builds the behavioral layer on top.
Used by: the pact-plugin test suite (standing both-modes merge gate).

WHY THIS SHAPE
--------------
The retired completion_no_paired_send detector was ~100% false-positive by
construction (a synchronous wake-read races the async inbox write). A working
replacement must therefore prove LOW false-positive — so the load-bearing half
of this matrix is the must-NOT-fire rows, each paired with a POSITIVE CONTROL
proving the alarm CAN fire (a silent row must not be silent for the wrong
reason — the count_active_tasks fixture-completeness lesson).

DETERMINISM (never wall-clock): the alarm keys on wait_stale() over the
intentional_wait `since`. We construct `since` at a fixed offset from real now
(STALE = now-60min, FRESH = now-5min, FUTURE = now+60min) so the 30-min
threshold is crossed/not-crossed deterministically with a 30-min margin that no
test-execution jitter can perturb. The real wait_stale() runs end-to-end (we do
NOT monkeypatch the staleness logic — its _now injection is unit-tested
separately in test_intentional_wait.py).

The REAL O_EXCL marker mechanism (already_emitted / .missed_wake_emitted/) runs
against a tmp HOME, so marker assertions are against the actual kernel
test-and-set, not a mock.
"""
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import missed_wake_scan as mw  # noqa: E402
from fixtures.role_frames import (  # noqa: E402
    captured_lead_sessionstart_qualified,
    captured_lead_sessionstart_unqualified,
    captured_plain_sessionstart,
    captured_teammate_sessionstart,
)

TEAM = "pact-cd39eedb"
LEAD_SESSION = "cd39eedb-2715-4dce-9898-a99284b77554"
LEAD_AGENT_TYPE = "PACT:pact-orchestrator"
TEAMMATE_AGENT_TYPE = "pact-test-engineer"


# --- precondition construction (deterministic, real wait_stale) -------------
def _since(minutes_ago: int) -> str:
    """ISO-8601 UTC `since` at a fixed offset from real now. Positive
    minutes_ago = past (older); negative = future-dated (clock-skew)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


STALE = 60       # 60 min in the past → past the 30-min threshold → stale
FRESH = 5        # 5 min in the past → below threshold → not stale
FUTURE = -60     # 60 min in the FUTURE → negative age → conservatively NOT stale


def _wait(reason="awaiting_lead_completion", minutes_ago=STALE, **over):
    w = {
        "reason": reason,
        "expected_resolver": "lead",
        "since": _since(minutes_ago),
    }
    w.update(over)
    return w


def _task(task_id="42", owner="test-engineer", subject="do the thing",
          status="in_progress", wait="__default__"):
    meta = {}
    if wait == "__default__":
        wait = _wait()
    if wait is not None:
        meta["intentional_wait"] = wait
    return {"id": task_id, "owner": owner, "subject": subject,
            "status": status, "metadata": meta}


def _markers(home: Path, team=TEAM) -> list:
    d = home / ".claude" / "teams" / team / ".missed_wake_emitted"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


@pytest.fixture
def home(tmp_path, monkeypatch):
    """tmp HOME so the REAL O_EXCL marker mechanism writes under an isolated
    tree. Returns the tmp home path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def spy(monkeypatch):
    """Spy missed_wake_scan.append_event → capture emitted events; return True so
    emit_missed_wake treats the write as successful (no compensating-unclaim)."""
    events = []
    monkeypatch.setattr(mw, "append_event", lambda e: events.append(e) or True)
    return events


# ===========================================================================
# 1. find_stale_missed_wakes — the pure FP/TP filter (each FP row + a control)
# ===========================================================================
class TestFindStaleFilter:
    """The filter qualifies a task iff: in_progress AND a WELL-FORMED
    intentional_wait with reason==awaiting_lead_completion AND wait_stale().
    Each must-NOT-qualify row is paired with a positive control."""

    def test_TP_genuinely_stale_qualifies(self):
        assert mw.find_stale_missed_wakes([_task()]) != []

    def test_FP_not_in_progress(self):
        # FP: a completed/other-status task never alarms…
        assert mw.find_stale_missed_wakes([_task(status="completed")]) == []
        assert mw.find_stale_missed_wakes([_task(status="pending")]) == []
        # …positive control: same task in_progress qualifies.
        assert mw.find_stale_missed_wakes([_task(status="in_progress")]) != []

    def test_FP_wait_freshly_set_below_threshold(self):
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=FRESH))]) == []
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=STALE))]) != []

    def test_FP_future_dated_since_clock_skew(self):
        # Forward clock-skew / tampering → negative age → conservatively NOT
        # stale (wait_stale's documented behavior). Must not cry wolf.
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=FUTURE))]) == []
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=STALE))]) != []

    def test_FP_resolver_already_acted_no_wait(self):
        # Resolver acted → intentional_wait removed. No wait → no alarm.
        assert mw.find_stale_missed_wakes([_task(wait=None)]) == []
        assert mw.find_stale_missed_wakes([_task()]) != []

    def test_FP_reason_not_awaiting_lead_completion(self):
        # A DIFFERENT (legitimate) wait reason — e.g. awaiting_lead_commit — is
        # NOT the missed-wake gap and must not alarm, even when stale.
        assert mw.find_stale_missed_wakes(
            [_task(wait=_wait(reason="awaiting_lead_commit", minutes_ago=STALE))]
        ) == []
        assert mw.find_stale_missed_wakes(
            [_task(wait=_wait(reason="awaiting_lead_completion", minutes_ago=STALE))]
        ) != []

    def test_FP_malformed_wait_validate_gate_first(self):
        # validate_wait gates BEFORE wait_stale (which would treat a malformed
        # flag as stale → fail-loud). So a malformed wait must NOT produce a
        # missed_wake with a bad since. Two malformed shapes:
        tznaive = _wait(minutes_ago=STALE)
        tznaive["since"] = datetime.now().replace(tzinfo=None).isoformat()  # tz-naive
        assert mw.find_stale_missed_wakes([_task(wait=tznaive)]) == []
        no_since = _wait(minutes_ago=STALE)
        del no_since["since"]
        assert mw.find_stale_missed_wakes([_task(wait=no_since)]) == []
        # positive control: the well-formed equivalent qualifies.
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=STALE))]) != []

    def test_non_dict_and_missing_metadata_safe(self):
        # Robustness: never raises on degenerate task shapes.
        assert mw.find_stale_missed_wakes(["nope", 7, None, {}]) == []
        assert mw.find_stale_missed_wakes([{"status": "in_progress"}]) == []


# ===========================================================================
# 2. emit_missed_wake — emit-once, marker, writability precondition, unclaim
# ===========================================================================
class TestEmitAndDedup:
    def test_emits_once_and_claims_marker(self, home, spy, pact_context):
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        task = _task()
        mw.emit_missed_wake(TEAM, task)
        assert len(spy) == 1, "a genuinely-stale task emits exactly one missed_wake"
        assert spy[0]["type"] == "missed_wake"
        assert spy[0]["task_id"] == "42" and spy[0]["agent"] == "test-engineer"
        assert spy[0]["reason"] == "awaiting_lead_completion"
        assert len(_markers(home)) == 1, "the re-armable marker is claimed"

    def test_second_emit_same_since_dedups_no_nag(self, home, spy, pact_context):
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        task = _task()
        mw.emit_missed_wake(TEAM, task)
        mw.emit_missed_wake(TEAM, task)  # same (task_id, since) → suppressed
        assert len(spy) == 1, "repeated Stop fires within one (task,since) emit ONCE (no nag)"

    def test_writability_precondition_unwritable_defers(self, home, spy):
        # No pact_context persisted → get_journal_path() == "" → emit must DEFER
        # BEFORE claiming the marker (the #917 claim-without-write poison class).
        assert mw.get_journal_path() == "", "model check: unpersisted context → unwritable"
        mw.emit_missed_wake(TEAM, _task())
        assert spy == [], "an unwritable fire emits nothing"
        assert _markers(home) == [], "and claims NO marker (cannot poison a writable retry)"

    def test_compensating_unclaim_on_append_failure(self, home, monkeypatch, pact_context):
        # append_event fails (returns False) AFTER the marker is claimed → the
        # marker must be UNCLAIMED so a later Stop retries (the lead may never
        # re-SET a forgotten wait, so a since-keyed re-arm can't recover alone).
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        monkeypatch.setattr(mw, "append_event", lambda e: False)
        mw.emit_missed_wake(TEAM, _task())
        assert _markers(home) == [], "write-failure must compensating-unclaim the marker"
        # retry now succeeds → re-emits (proves the unclaim restored re-armability)
        events = []
        monkeypatch.setattr(mw, "append_event", lambda e: events.append(e) or True)
        mw.emit_missed_wake(TEAM, _task())
        assert len(events) == 1, "after unclaim, a retry re-emits"

    def test_missing_required_field_skips(self, home, spy, pact_context):
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        mw.emit_missed_wake(TEAM, _task(owner=""))      # no agent
        mw.emit_missed_wake(TEAM, _task(task_id=""))    # no task_id
        assert spy == [], "a task missing a load-bearing field emits nothing"


# ===========================================================================
# 3. RE-ARM — the 2-phase carrier-agnostic guard (fire-once marker would fail)
# ===========================================================================
class TestReArm:
    def test_two_phase_rearm_fires_again_on_fresh_since(self, home, spy, pact_context):
        """Phase A: after the lead acts, the wait is re-SET with a FRESH since
        (< threshold) → SILENT. Phase B: that wait re-ages (a NEW since, now
        stale) → FIRES AGAIN. A fire-once O_EXCL marker keyed on task_id alone
        would regress Phase B to silence — this row is the executable guard that
        the namespace is RE-ARMABLE (keyed on (task_id, hash(since)))."""
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)

        # Cycle 1: stale → fires once.
        stale1 = _wait(minutes_ago=STALE)
        t1 = _task(wait=stale1)
        assert mw.find_stale_missed_wakes([t1]) != []
        mw.emit_missed_wake(TEAM, t1)
        assert len(spy) == 1

        # Phase A: lead acts → wait re-set FRESH (different `since`). Not stale
        # → filtered out → no emit.
        fresh = _wait(minutes_ago=FRESH)
        assert fresh["since"] != stale1["since"]
        t2 = _task(wait=fresh)
        assert mw.find_stale_missed_wakes([t2]) == [], "Phase A: fresh re-set is silent"

        # Phase B: that wait re-ages → a NEW stale `since` (distinct from cycle 1)
        # → re-arms → FIRES AGAIN.
        stale2 = _wait(minutes_ago=STALE + 1)  # distinct since value
        assert stale2["since"] != stale1["since"]
        t3 = _task(wait=stale2)
        assert mw.find_stale_missed_wakes([t3]) != []
        mw.emit_missed_wake(TEAM, t3)
        assert len(spy) == 2, "Phase B: a re-aged fresh wait RE-FIRES (re-arm)"
        assert len(_markers(home)) == 2, "distinct (task,since) markers — re-armable namespace"

    def test_same_since_never_rearms(self, home, spy, pact_context):
        # Guard the other direction: the SAME since must dedup forever (no nag),
        # however many Stop fires occur.
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        task = _task()
        for _ in range(5):
            mw.emit_missed_wake(TEAM, task)
        assert len(spy) == 1, "same (task,since) dedups across many fires"


# ===========================================================================
# 4. run_scan + is_lead BOTH-MODES gating (standing merge gate)
# ===========================================================================
def _frame(agent_type):
    f = {"hook_event_name": "Stop"}
    if agent_type is not None:
        f["agent_type"] = agent_type
    return f


class TestRunScanBothModes:
    """run_scan is is_lead-gated. The both-modes axis: the lead's Stop fires
    lead-side in BOTH topologies (session_id==leadSessionId in-process AND a
    lead process under tmux); a teammate/plain frame no-ops in both."""

    @pytest.mark.parametrize("same_session", [True, False], ids=["in_process", "tmux"])
    def test_lead_frame_emits_both_modes(self, home, spy, monkeypatch, pact_context, same_session):
        # in_process: this process's session == leadSessionId. tmux: a separate
        # lead process — still is_lead by agent_type, journal still writable (its
        # OWN session). Either way the lead's scan emits.
        sid = LEAD_SESSION if same_session else "lead-proc-" + LEAD_SESSION
        pact_context(team_name=TEAM, session_id=sid)
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        mw.run_scan(_frame(LEAD_AGENT_TYPE))
        assert len(spy) == 1, "lead frame → scan emits in both topologies"

    @pytest.mark.parametrize("same_session", [True, False], ids=["in_process", "tmux"])
    def test_teammate_frame_noops_both_modes(self, home, spy, monkeypatch, pact_context, same_session):
        sid = LEAD_SESSION if same_session else "teammate-" + LEAD_SESSION
        pact_context(team_name=TEAM, session_id=sid)
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        mw.run_scan(_frame(TEAMMATE_AGENT_TYPE))
        assert spy == [], "teammate frame → run_scan no-ops (is_lead fail-safe) in both modes"

    @pytest.mark.parametrize("same_session", [True, False], ids=["in_process", "tmux"])
    def test_plain_frame_noops_both_modes(self, home, spy, monkeypatch, pact_context, same_session):
        sid = LEAD_SESSION if same_session else "plain-" + LEAD_SESSION
        pact_context(team_name=TEAM, session_id=sid)
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        mw.run_scan(_frame(None))  # agent_type absent → plain/non-PACT
        assert spy == [], "plain frame (agent_type absent) → no-op in both modes"

    def test_lead_scan_emits_only_the_stale_awaiting_task(self, home, spy, monkeypatch, pact_context):
        # Integration: among a mixed task list, ONLY the stale
        # awaiting_lead_completion in_progress task alarms.
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        tasks = [
            _task(task_id="1", wait=_wait(minutes_ago=STALE)),                       # YES
            _task(task_id="2", wait=_wait(minutes_ago=FRESH)),                       # fresh → no
            _task(task_id="3", status="completed"),                                  # done → no
            _task(task_id="4", wait=_wait(reason="awaiting_lead_commit", minutes_ago=STALE)),  # wrong reason → no
            _task(task_id="5", wait=None),                                           # no wait → no
        ]
        monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
        mw.run_scan(_frame(LEAD_AGENT_TYPE))
        assert [e["task_id"] for e in spy] == ["1"], "only the genuinely-stale awaiting task alarms"


class TestIsLeadCarrierFrames:
    """Auditor TEST-FOCUS (a): is_lead classifies the carrier frames correctly,
    in both modes. is_lead reads ONLY agent_type — the universal discriminator
    present on EVERY event in both topologies (HOOK_STDIN_DISCRIMINATORS.md; its
    purity is pinned by test_is_lead.py). So it is event-AGNOSTIC: a Stop-shaped
    frame is classified solely by its agent_type, identically to a SessionStart
    frame.

    Two DISTINCT claims, two test treatments:
      - is_lead GATING (this process is/ isn't lead): agent_type-only, topology-
        invariant → faithfully exercised by a SYNTHESIZED Stop frame. NOT
        deferred (a real frame adds nothing the agent_type doesn't carry).
      - PLATFORM behavior ('bare Stop FIRES lead-process in both topologies and
        carries agent_type'): a real-capture / live claim — preparer-CONFIRMED
        via the live probe, NOT a unit test. THIS is what dual-mode
        Consequence 3 governs (the real-capture prerequisite), not the gating.
    SessionStart rows use REAL captures; the Stop gating row uses a synthesized
    frame; a real captured Stop frame is an OPTIONAL completeness item below."""

    def test_sessionstart_carrier_islead_real_frames(self):
        assert mw.is_lead(captured_lead_sessionstart_qualified()) is True
        assert mw.is_lead(captured_lead_sessionstart_unqualified()) is True
        assert mw.is_lead(captured_teammate_sessionstart()) is False
        assert mw.is_lead(captured_plain_sessionstart()) is False

    def test_stop_carrier_islead_gating_synthesized_frame(self):
        # is_lead is agent_type-only + event-agnostic → a SYNTHESIZED Stop frame
        # faithfully exercises the GATING. This is NOT a Consequence-3 platform
        # claim (that bare Stop fires + carries agent_type is preparer-confirmed
        # via the live probe); it is the role-classification the run_scan gate
        # relies on, asserted on the carrier's own event shape.
        assert mw.is_lead(_frame(LEAD_AGENT_TYPE)) is True
        assert mw.is_lead({"hook_event_name": "Stop", "agent_type": "pact-orchestrator"}) is True
        assert mw.is_lead(_frame(TEAMMATE_AGENT_TYPE)) is False
        assert mw.is_lead(_frame(None)) is False  # plain / non-PACT (agent_type absent)

    @pytest.mark.skip(reason="OPTIONAL completeness only — bind to a REAL captured Stop frame "
                             "once devops/preparer land one. The is_lead GATING is already "
                             "covered (synthesized Stop above + run_scan's synthesized-Stop "
                             "both-modes tests + test_is_lead's purity pin); the 'bare Stop fires "
                             "lead-process in both topologies + carries agent_type' PLATFORM claim "
                             "is preparer-CONFIRMED via the live probe (dual-mode Consequence 3 "
                             "governs that platform claim, NOT this agent_type-only gating). "
                             "Un-skip: assert is_lead(captured_lead_stop) is True / "
                             "is_lead(captured_teammate_stop) is False.")
    def test_stop_carrier_islead_real_frame_completeness(self):
        raise AssertionError("placeholder: bind to captured_lead_stop / captured_teammate_stop")
