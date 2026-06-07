"""
Location: pact-plugin/tests/test_missed_wake_scan.py
Summary: BEHAVIORAL coverage for the #903 NO-MARKER missed-wake SURFACER
         (missed_wake_scan.py, post-B1 remediation). The mechanism surfaces a
         stale awaiting_lead_completion wait as actionable additionalContext on
         the lead's UserPromptSubmit / SessionStart (is_lead-gated), persisting
         every turn while stale and auto-clearing on resolve; a once-per-
         (task_id,since) forensic `missed_wake` journal event is written via a
         JOURNAL-READ dedup (no filesystem marker). The structural layer
         (hooks.json registration, journal-schema) lives in devops's
         test_hooks_json / test_dogfood_livelock_invariant / test_session_journal
         samples; this file builds the behavioral layer on top.
Used by: the pact-plugin test suite (standing both-modes merge gate).

DESIGN (architect B1 remediation, lead-confirmed): the prior Stop + O_EXCL
.missed_wake_emitted marker machinery is DELETED. Two distinct dedup semantics
the suite must pin:
  • SURFACE = PERSISTENT-while-stale. run_surface returns the additionalContext
    on EVERY firing turn while the wait is stale; there is NO surface dedup —
    current-stale-state is the only "dedup" and it AUTO-CLEARS when the wait
    resolves (find_stale → [] → run_surface → None → suppressOutput). A 2nd fire
    while still stale surfaces AGAIN.
  • FORENSIC emit = once-per-(task_id,since), deduped by _emitted_keys() reading
    read_events("missed_wake"). Cross-fire dedup works because the 1st fire's
    event is in the journal on the 2nd fire's read. Re-arms on a new `since`.

DETERMINISM (never wall-clock): find_stale keys on wait_stale() over the
intentional_wait `since` — constructed at a fixed offset from real now (STALE =
now-60min, FRESH = now-5min, FUTURE = now+60min) so the 30-min threshold is
crossed deterministically; build_surface / _age_minutes take an injectable
`now`. The journal-read dedup is driven by monkeypatching read_events /
append_event (module-global on missed_wake_scan), per devops's confirmed levers.
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
    captured_lead_userpromptsubmit_qualified,
    captured_plain_sessionstart,
    captured_plain_userpromptsubmit,
    captured_teammate_sessionstart,
)

LEAD_AGENT_TYPE = "PACT:pact-orchestrator"
TEAMMATE_AGENT_TYPE = "pact-test-engineer"
FIXED_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


# --- precondition construction (deterministic) ------------------------------
def _since(minutes_ago: int) -> str:
    """ISO-8601 UTC `since` at a fixed offset from real now. Positive = past
    (older); negative = future-dated (clock-skew)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _since_of(now: datetime, minutes_ago: int) -> str:
    """`since` at a fixed offset from an INJECTED now (for build_surface age)."""
    return (now - timedelta(minutes=minutes_ago)).isoformat()


STALE = 60       # 60 min past → past the 30-min threshold → stale
FRESH = 5        # 5 min past → below threshold → not stale
FUTURE = -60     # 60 min future → negative age → conservatively NOT stale


def _wait(reason="awaiting_lead_completion", minutes_ago=STALE, since=None, **over):
    w = {
        "reason": reason,
        "expected_resolver": "lead",
        "since": since if since is not None else _since(minutes_ago),
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


@pytest.fixture
def journal(monkeypatch):
    """Deterministic journal-read dedup harness (devops's confirmed levers):
    seed `read_events('missed_wake')` and spy `append_event`. Returns a dict
    with `seed` (list the dedup reads) and `emitted` (captured append events).
    Patches get_journal_path truthy so emit_forensic passes its writability
    precondition without a real session dir."""
    state = {"seed": [], "emitted": []}
    monkeypatch.setattr(mw, "read_events", lambda et: list(state["seed"]) if et == "missed_wake" else [])
    monkeypatch.setattr(mw, "append_event", lambda e: state["emitted"].append(e) or True)
    monkeypatch.setattr(mw, "get_journal_path", lambda: "/tmp/fake-journal.jsonl")
    return state


# ===========================================================================
# 1. find_stale_missed_wakes — the pure FP/TP filter (UNCHANGED by the refactor)
# ===========================================================================
class TestFindStaleFilter:
    """The single scan feeding both surface + forensic paths. Qualifies a task
    iff: in_progress AND a WELL-FORMED awaiting_lead_completion wait AND
    wait_stale(). Each must-NOT-qualify row is paired with a positive control."""

    def test_TP_genuinely_stale_qualifies(self):
        assert mw.find_stale_missed_wakes([_task()]) != []

    def test_FP_not_in_progress(self):
        assert mw.find_stale_missed_wakes([_task(status="completed")]) == []
        assert mw.find_stale_missed_wakes([_task(status="pending")]) == []
        assert mw.find_stale_missed_wakes([_task(status="in_progress")]) != []

    def test_FP_wait_freshly_set_below_threshold(self):
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=FRESH))]) == []
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=STALE))]) != []

    def test_FP_future_dated_since_clock_skew(self):
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=FUTURE))]) == []
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=STALE))]) != []

    def test_FP_resolver_already_acted_no_wait(self):
        assert mw.find_stale_missed_wakes([_task(wait=None)]) == []
        assert mw.find_stale_missed_wakes([_task()]) != []

    def test_FP_reason_not_awaiting_lead_completion(self):
        assert mw.find_stale_missed_wakes(
            [_task(wait=_wait(reason="awaiting_lead_commit", minutes_ago=STALE))]) == []
        assert mw.find_stale_missed_wakes(
            [_task(wait=_wait(reason="awaiting_lead_completion", minutes_ago=STALE))]) != []

    def test_FP_malformed_wait_validate_gate_first(self):
        tznaive = _wait(minutes_ago=STALE)
        tznaive["since"] = datetime.now().replace(tzinfo=None).isoformat()
        assert mw.find_stale_missed_wakes([_task(wait=tznaive)]) == []
        no_since = _wait(minutes_ago=STALE)
        del no_since["since"]
        assert mw.find_stale_missed_wakes([_task(wait=no_since)]) == []
        assert mw.find_stale_missed_wakes([_task(wait=_wait(minutes_ago=STALE))]) != []

    def test_non_dict_and_missing_metadata_safe(self):
        assert mw.find_stale_missed_wakes(["nope", 7, None, {}]) == []
        assert mw.find_stale_missed_wakes([{"status": "in_progress"}]) == []


# ===========================================================================
# 2. build_surface — pure, now-injectable additionalContext
# ===========================================================================
class TestBuildSurface:
    def test_none_when_empty(self):
        assert mw.build_surface([]) is None
        assert mw.build_surface([], now=FIXED_NOW) is None

    def test_one_line_per_task_with_actionable_text(self):
        stale = [_task(task_id="7", owner="architect", subject="design X",
                       wait=_wait(since=_since_of(FIXED_NOW, 45)))]
        out = mw.build_surface(stale, now=FIXED_NOW)
        assert out is not None
        assert "missed-wake" in out.lower()
        assert "wake-SendMessage" in out, "must name the corrective action"
        assert "#7" in out and "architect" in out and "design X" in out
        assert "~45min" in out, "age computed from the injected now (deterministic)"
        assert "awaiting_lead_completion" in out

    def test_multiple_tasks_one_line_each(self):
        stale = [
            _task(task_id="1", owner="a", subject="s1", wait=_wait(since=_since_of(FIXED_NOW, 40))),
            _task(task_id="2", owner="b", subject="s2", wait=_wait(since=_since_of(FIXED_NOW, 90))),
        ]
        out = mw.build_surface(stale, now=FIXED_NOW)
        lines = [ln for ln in out.splitlines() if ln.startswith("- Task")]
        assert len(lines) == 2, "one actionable line per stranded task"
        assert "#1" in out and "#2" in out

    def test_unparseable_since_degrades_to_stale_label(self):
        stale = [_task(task_id="9", owner="x", subject="y", wait=_wait(since="not-a-date"))]
        out = mw.build_surface(stale, now=FIXED_NOW)
        assert out is not None and "stale" in out, "unparseable age → 'stale' label, never crashes"


# ===========================================================================
# 3. emit_forensic — once-per-(task,since) JOURNAL-READ dedup (no marker)
# ===========================================================================
class TestEmitForensicJournalDedup:
    def test_emits_once_for_fresh_stale_wait(self, journal):
        mw.emit_forensic([_task(task_id="42", owner="te", subject="s", wait=_wait(since="2026-06-07T11:00:00+00:00"))])
        assert len(journal["emitted"]) == 1
        ev = journal["emitted"][0]
        assert ev["type"] == "missed_wake" and ev["task_id"] == "42" and ev["agent"] == "te"
        assert ev["since"] == "2026-06-07T11:00:00+00:00" and ev["reason"] == "awaiting_lead_completion"

    def test_cross_fire_dedup_same_task_since_not_reemitted(self, journal):
        # The 1st fire's event is in the journal on the 2nd fire's read → suppressed.
        journal["seed"] = [{"task_id": "42", "since": "2026-06-07T11:00:00+00:00", "type": "missed_wake"}]
        mw.emit_forensic([_task(task_id="42", wait=_wait(since="2026-06-07T11:00:00+00:00"))])
        assert journal["emitted"] == [], "a (task,since) already in the journal is NOT re-emitted"

    def test_rearm_on_new_since(self, journal):
        # A re-SET wait gets a fresh `since` → new key → emits again even though
        # the OLD (task,since) is in the journal.
        journal["seed"] = [{"task_id": "42", "since": "2026-06-07T11:00:00+00:00", "type": "missed_wake"}]
        mw.emit_forensic([_task(task_id="42", wait=_wait(since="2026-06-07T11:40:00+00:00"))])
        assert len(journal["emitted"]) == 1, "a NEW since re-arms the forensic emit"
        assert journal["emitted"][0]["since"] == "2026-06-07T11:40:00+00:00"

    def test_multiple_stale_each_emitted_once(self, journal):
        # The surfacer-form equivalent of the (now-deleted) run_scan multi-stale
        # intent: N stale waits → N forensic events, each once.
        stale = [
            _task(task_id="1", owner="a", wait=_wait(since="2026-06-07T10:00:00+00:00")),
            _task(task_id="2", owner="b", wait=_wait(since="2026-06-07T10:30:00+00:00")),
        ]
        mw.emit_forensic(stale)
        assert sorted(e["task_id"] for e in journal["emitted"]) == ["1", "2"]

    def test_writability_precondition_unwritable_noop(self, monkeypatch):
        # get_journal_path()=="" → emit_forensic no-ops: NO read, NO write.
        reads = {"n": 0}
        monkeypatch.setattr(mw, "get_journal_path", lambda: "")
        monkeypatch.setattr(mw, "read_events", lambda et: reads.__setitem__("n", reads["n"] + 1) or [])
        emitted = []
        monkeypatch.setattr(mw, "append_event", lambda e: emitted.append(e) or True)
        mw.emit_forensic([_task()])
        assert emitted == [] and reads["n"] == 0, "unwritable context → clean no-op (no journal touch)"

    def test_empty_stale_is_noop(self, journal):
        mw.emit_forensic([])
        assert journal["emitted"] == []

    def test_missing_required_field_skipped(self, journal):
        mw.emit_forensic([_task(owner="", wait=_wait(since="2026-06-07T11:00:00+00:00"))])
        assert journal["emitted"] == [], "a stale task missing a load-bearing field emits nothing"


# ===========================================================================
# 4. run_surface — is_lead gate, surface text, AUTO-CLEAR, PERSISTENT-while-stale
# ===========================================================================
def _lead_frame():
    return captured_lead_userpromptsubmit_qualified()


class TestRunSurface:
    def test_lead_stale_surfaces_and_emits(self, journal, monkeypatch):
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        out = mw.run_surface(_lead_frame())
        assert out is not None and "missed-wake" in out.lower(), "lead + stale → surface text"
        assert len(journal["emitted"]) == 1, "and a forensic emit fires"

    def test_auto_clears_when_resolved(self, journal, monkeypatch):
        # Wait resolved (task no longer in_progress) → re-scan finds nothing →
        # run_surface returns None (suppressOutput). THE auto-clear.
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task(status="completed")])
        assert mw.run_surface(_lead_frame()) is None
        assert journal["emitted"] == [], "resolved → no surface, no emit"

    def test_persistent_while_stale_second_fire_surfaces_again(self, journal, monkeypatch):
        # PERSISTENT-while-stale: a 2nd fire with the SAME stale wait surfaces
        # AGAIN (no surface dedup). The forensic emit, however, is once-per-
        # (task,since): after fire 1 records it, fire 2's read sees it → no 2nd emit.
        # The since must be genuinely stale vs real now (run_surface re-checks
        # wait_stale), so construct it at the STALE offset, not a fixed literal.
        task = _task(wait=_wait(minutes_ago=STALE))
        monkeypatch.setattr(mw, "get_task_list", lambda: [task])

        out1 = mw.run_surface(_lead_frame())
        # simulate the journal now carrying fire-1's forensic event for fire 2's read
        journal["seed"] = list(journal["emitted"])
        out2 = mw.run_surface(_lead_frame())

        assert out1 is not None and out2 is not None, "surface RE-SHOWS every turn while stale"
        assert len(journal["emitted"]) == 1, "but the forensic emit is once-per-(task,since)"

    def test_no_tasks_returns_none(self, journal, monkeypatch):
        monkeypatch.setattr(mw, "get_task_list", lambda: None)
        assert mw.run_surface(_lead_frame()) is None
        monkeypatch.setattr(mw, "get_task_list", lambda: [])
        assert mw.run_surface(_lead_frame()) is None

    def test_multiple_stale_all_surfaced(self, journal, monkeypatch):
        tasks = [
            _task(task_id="1", owner="a", wait=_wait(minutes_ago=STALE)),
            _task(task_id="2", owner="b", wait=_wait(minutes_ago=STALE + 5)),
        ]
        monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
        out = mw.run_surface(_lead_frame())
        assert "#1" in out and "#2" in out, "all stale waits surfaced"
        assert sorted(e["task_id"] for e in journal["emitted"]) == ["1", "2"]


# ===========================================================================
# 5. is_lead BOTH-MODES on the REAL carrier frames + run_surface gating
# ===========================================================================
class TestIsLeadCarrierFramesBothModes:
    """Auditor TEST-FOCUS: is_lead both-modes for the SURFACER carrier frames
    (UserPromptSubmit + SessionStart — Stop is DROPPED). Both carriers have REAL
    captures, so the gating is asserted on real frames (no synthetic stdin; the
    deferred-Stop-frame row dissolved with the carrier change)."""

    def test_islead_on_real_userpromptsubmit_frames(self):
        assert mw.is_lead(captured_lead_userpromptsubmit_qualified()) is True
        assert mw.is_lead(captured_plain_userpromptsubmit()) is False  # agent_type absent

    def test_islead_on_real_sessionstart_frames(self):
        assert mw.is_lead(captured_lead_sessionstart_qualified()) is True
        assert mw.is_lead(captured_lead_sessionstart_unqualified()) is True
        assert mw.is_lead(captured_teammate_sessionstart()) is False
        assert mw.is_lead(captured_plain_sessionstart()) is False

    @pytest.mark.parametrize("session_id", ["lead-session", "alt-session"],
                             ids=["in_process", "tmux"])
    def test_run_surface_gating_both_modes(self, journal, monkeypatch, pact_context, session_id):
        # Lead carrier frame → surfaces; non-lead carrier frame → no-op (None),
        # in BOTH topologies. The lead's surface works regardless of journal
        # writability (surfacing doesn't touch the journal).
        pact_context(team_name="pact-test", session_id=session_id)
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        assert mw.run_surface(captured_lead_userpromptsubmit_qualified()) is not None
        assert mw.run_surface(captured_plain_userpromptsubmit()) is None       # plain → no-op
        assert mw.run_surface(captured_teammate_sessionstart()) is None        # teammate → no-op


# ===========================================================================
# 6. main() — additionalContext shape vs suppressOutput, exit-0 robustness
# ===========================================================================
def _run_main(frame, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(frame)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    with pytest.raises(SystemExit) as exc:
        mw.main()
    assert exc.value.code == 0, "exit-0 invariant on every path (livelock-safe)"
    return out.getvalue()


class TestMain:
    def test_surface_emits_hookspecificoutput_matching_event(self, journal, monkeypatch):
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        frame = captured_lead_userpromptsubmit_qualified()
        payload = json.loads(_run_main(frame, monkeypatch))
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == frame.get("hook_event_name", "UserPromptSubmit")
        assert "missed-wake" in hso["additionalContext"].lower()

    def test_no_stale_emits_suppressoutput(self, journal, monkeypatch):
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task(status="completed")])
        payload = json.loads(_run_main(captured_lead_userpromptsubmit_qualified(), monkeypatch))
        assert payload == {"suppressOutput": True}

    def test_teammate_frame_suppressoutput(self, journal, monkeypatch):
        monkeypatch.setattr(mw, "get_task_list", lambda: [_task()])
        payload = json.loads(_run_main(captured_teammate_sessionstart(), monkeypatch))
        assert payload == {"suppressOutput": True}, "non-lead frame → no surface"

    def test_bad_stdin_exits_zero_suppressoutput(self, monkeypatch):
        # JSONDecodeError + non-dict stdin both → suppressOutput, exit 0.
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        out = io.StringIO(); monkeypatch.setattr("sys.stdout", out)
        with pytest.raises(SystemExit) as e1:
            mw.main()
        assert e1.value.code == 0 and json.loads(out.getvalue()) == {"suppressOutput": True}

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(["not", "a", "dict"])))
        out2 = io.StringIO(); monkeypatch.setattr("sys.stdout", out2)
        with pytest.raises(SystemExit) as e2:
            mw.main()
        assert e2.value.code == 0 and json.loads(out2.getvalue()) == {"suppressOutput": True}
