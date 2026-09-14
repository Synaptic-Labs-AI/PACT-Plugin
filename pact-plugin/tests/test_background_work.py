"""Behaviour pins for shared/background_work.py.

Location: pact-plugin/tests/test_background_work.py
Summary: pins the registry's record schema, the R5 task_ids list, the Layer 3
         two-threshold clock, the identity bind (including the validated
         agent_type route and the collisions it must refuse), and the
         acknowledgment discharge.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

THESE PIN BEHAVIOUR, NOT FRAME FIELDS. Identity may arrive by `agent_name`,
by an `@`-bearing `agent_id`, or by a validated `agent_type`, and no test
here asserts WHICH field carried it, because that is a property of the
harness rather than of this code. What is pinned is the OUTCOME: a frame
carrying an identity by any accepted route produces a record; a frame
carrying none produces nothing.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared.background_work import (
    LEAD_STALE_MINUTES,
    LEAD_UNIDLED_STALE_MINUTES,
    _sanitize_record,
    agent_type_names_a_member,
    bind_launcher_identity,
    classify_wait,
    discharge_acknowledged_for_owner,
    effective_since,
    is_shell_backgrounded_bash,
    lead_stale,
    load_records_for_discharge,
    matching_outstanding,
    record_task_ids,
    outstanding_unflagged,
    unflagged_fire,
    wait_covers_record,
)

TEAM = "probe-team"
HEX16 = "0123456789abcdef"
T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _record(**over):
    base = {
        "agent_name": "probe-coder",
        "session_id": "sid",
        "task_ids": ["13"],
        "registered_at": _iso(T0),
    }
    base.update(over)
    return base


def _task(task_id="13", status="in_progress", wait=None, **over):
    task = {"id": task_id, "status": status, "owner": "probe-coder"}
    if wait is not None:
        task["metadata"] = {"intentional_wait": wait}
    task.update(over)
    return task


def _wait(since: datetime, reason="awaiting_blocker_resolution"):
    return {"reason": reason, "expected_resolver": "lead", "since": _iso(since)}


# ---------------------------------------------------------------- schema (R5)


class TestRecordSchema:
    def test_task_ids_list_is_accepted(self):
        assert _sanitize_record(_record())["task_ids"] == ["13"]

    def test_two_task_ids_survive(self):
        assert _sanitize_record(_record(task_ids=["13", "14"]))["task_ids"] == [
            "13",
            "14",
        ]

    def test_scalar_task_id_is_REJECTED_not_coerced(self):
        """A scalar is a malformed write, not an older schema.

        Records are ephemeral team state with a 24h TTL and are never read
        across a version boundary, so coercing a scalar into a one-element
        list would silently reinterpret a record nobody wrote deliberately.
        """
        raw = _record()
        del raw["task_ids"]
        raw["task_id"] = "13"
        assert _sanitize_record(raw) is None

    def test_string_task_ids_is_rejected(self):
        assert _sanitize_record(_record(task_ids="13")) is None

    @pytest.mark.parametrize("bad", [[], [""], ["13", 14], [None], [["13"]]])
    def test_malformed_task_ids_rejected(self, bad):
        assert _sanitize_record(_record(task_ids=bad)) is None

    def test_command_is_truncated_to_240(self):
        out = _sanitize_record(_record(command="x" * 500))
        assert len(out["command"]) == 240

    def test_record_task_ids_is_total(self):
        assert record_task_ids(None) == []
        assert record_task_ids({}) == []
        assert record_task_ids({"task_ids": "nope"}) == []


class TestMatchingByList:
    def test_matches_any_listed_id(self):
        rec = _sanitize_record(_record(task_ids=["13", "14"]))
        assert matching_outstanding(_task("14"), records=[rec]) == rec

    def test_unlisted_id_does_not_match(self):
        rec = _sanitize_record(_record(task_ids=["13"]))
        assert matching_outstanding(_task("99"), records=[rec]) is None


# -------------------------------------------------- Layer 3's two-clock rule


class TestEffectiveSince:
    def test_idled_at_present_uses_the_short_window(self):
        rec = _record(idled_at=_iso(T0))
        since, minutes = effective_since(_sanitize_record(rec))
        assert minutes == LEAD_STALE_MINUTES
        assert since == T0

    def test_idled_at_absent_falls_back_to_registered_at(self):
        """Without this, a missed TeammateIdle disables Layer 3 entirely.

        `stamp_idled_at` is the only writer of `idled_at`, so keying the lead
        surface on that field alone made Layer 3 a consumer of Layer 2 rather
        than a backstop for it.
        """
        since, minutes = effective_since(_sanitize_record(_record()))
        assert minutes == LEAD_UNIDLED_STALE_MINUTES
        assert since == T0

    def test_registered_at_arm_needs_the_longer_window(self):
        rec = _sanitize_record(_record())
        mid = T0 + timedelta(minutes=LEAD_STALE_MINUTES + 1)
        assert lead_stale(rec, now=mid) is False, (
            "registered_at is not evidence of idling; the short window must "
            "not apply to it"
        )
        late = T0 + timedelta(minutes=LEAD_UNIDLED_STALE_MINUTES + 1)
        assert lead_stale(rec, now=late) is True

    def test_idled_at_arm_fires_on_the_short_window(self):
        rec = _sanitize_record(_record(idled_at=_iso(T0)))
        assert lead_stale(rec, now=T0 + timedelta(minutes=LEAD_STALE_MINUTES + 1))

    def test_thresholds_are_distinct(self):
        assert LEAD_UNIDLED_STALE_MINUTES > LEAD_STALE_MINUTES


# ------------------------------------------------------------ the fire predicate


class TestUnflaggedFire:
    def test_fires_when_in_progress_recorded_and_unflagged(self):
        rec = _sanitize_record(_record())
        fire, klass, got = unflagged_fire(_task(), records=[rec])
        assert fire is True and klass == "missing" and got == rec

    def test_does_not_fire_without_a_record(self):
        assert unflagged_fire(_task(), records=[])[0] is False

    def test_does_not_fire_when_the_wait_is_valid(self):
        rec = _sanitize_record(_record())
        assert unflagged_fire(_task(wait=_wait(T0)), records=[rec])[0] is False

    def test_does_not_fire_once_the_task_leaves_in_progress(self):
        rec = _sanitize_record(_record())
        assert unflagged_fire(_task(status="completed"), records=[rec])[0] is False

    @pytest.mark.parametrize(
        "wait,expected",
        [(None, "missing"), ({}, "malformed"), ({"reason": "x"}, "malformed")],
    )
    def test_wait_classes(self, wait, expected):
        task = _task()
        task["metadata"] = {"intentional_wait": wait} if wait is not None else {}
        assert classify_wait(task) == expected

    def test_a_flag_on_EITHER_held_task_silences_the_advisory(self):
        """R5's silencing half, and it points at LESS firing deliberately.

        A teammate holding two tasks who flagged on either has flagged. Under
        the old exactly-one-task rule that teammate got no record at all, so
        this strictly adds coverage without adding a false-positive route.
        """
        rec = _sanitize_record(_record(task_ids=["13", "14"]))
        tasks = [_task("13"), _task("14", wait=_wait(T0))]
        assert unflagged_fire(_task("13"), records=[rec], tasks=tasks)[0] is False

    def test_neither_task_flagged_still_fires(self):
        rec = _sanitize_record(_record(task_ids=["13", "14"]))
        tasks = [_task("13"), _task("14")]
        assert unflagged_fire(_task("13"), records=[rec], tasks=tasks)[0] is True


# ------------------------------------------- the acknowledgment discharge (P1a)


class TestWaitCoversRecord:
    """The discharge predicate. Pure over (task, record) — no session needed."""

    def test_wait_after_the_launch_acknowledges_it(self):
        rec = _sanitize_record(_record())
        assert wait_covers_record(_task(wait=_wait(T0 + timedelta(minutes=1))), rec)

    def test_wait_exactly_at_the_launch_acknowledges_it(self):
        rec = _sanitize_record(_record())
        assert wait_covers_record(_task(wait=_wait(T0)), rec)

    def test_wait_BEFORE_the_launch_does_not_acknowledge_it(self):
        """The comparison is load-bearing, not decoration.

        A wait flagged for an earlier job must not acquit a job launched
        afterwards. Dropping the comparison turns a precise discharge into a
        blanket amnesty.
        """
        rec = _sanitize_record(_record(registered_at=_iso(T0 + timedelta(minutes=5))))
        assert wait_covers_record(_task(wait=_wait(T0)), rec) is False

    def test_no_wait_acknowledges_nothing(self):
        assert wait_covers_record(_task(), _sanitize_record(_record())) is False

    def test_malformed_wait_acknowledges_nothing(self):
        task = _task()
        task["metadata"] = {"intentional_wait": {"reason": "x"}}
        assert wait_covers_record(task, _sanitize_record(_record())) is False

    def test_naive_since_acknowledges_nothing(self):
        """validate_wait rejects tz-naive, so this can never acquit a record."""
        task = _task(wait={"reason": "r", "expected_resolver": "lead",
                           "since": "2026-09-11T12:00:00"})
        assert wait_covers_record(task, _sanitize_record(_record())) is False


class TestDischargeSequences:
    """The sequences that decide whether Layers 2 and 3 may ship.

    Each is a full sequence rather than a point check, because the defect
    being pinned only exists ACROSS steps: a record that outlives its own
    acknowledgment, and is then cited by a later advisory.

    WHAT IS BEING PREVENTED, AT ITS ACTUAL SIZE: a MISATTRIBUTED advisory,
    not one that fires at a compliant teammate. The threshold counter is
    cleared on every tick where the fire predicate is false, so a teammate
    that flags never accumulates toward it. When the advisory does fire the
    teammate is genuinely idling unflagged — the advice is right and only the
    CITED CAUSE is stale. Sequence 1 pins that the discharge happens; it does
    not pin a false alarm that was never reachable.
    """

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "teams" / TEAM).mkdir(parents=True)

    def _seed(self, *records: dict):
        """Seed the registry in ONE clock-free write.

        🔴 `save_records` WRITES DIRECTLY AND NEVER READS, so no TTL prune can
        touch the fixture on the way in. `append_record` READS FIRST — through
        `_parse_records_text`, which drops any record older than
        RECORD_TTL_SECONDS against the REAL clock — so two successive appends
        of a fixed-date record silently lose the first one once the fixture
        ages past 24h. The append still returns True, so the loss is invisible
        at seed time and surfaces later as a count that is short by one.

        Seeding is not the subject of any arm in this class, so it must not be
        able to fail. Where `append_record` IS the subject — the concurrent
        two-writer arm — it stays, and its fixture is made time-independent a
        different way.
        """
        from shared.background_work import save_records

        assert save_records(
            [_record(**over) for over in (records or ({},))], team_name=TEAM
        ) is True

    def test_1_flag_then_idle_then_clear_then_idle_DOES_NOT_FIRE(self):
        """The whole point. A teammate that did everything right stays silent."""
        self._seed()
        flagged = _task(wait=_wait(T0 + timedelta(minutes=1)))
        assert discharge_acknowledged_for_owner([flagged], "probe-coder", team_name=TEAM, now=T0) == 1
        assert load_records_for_discharge(TEAM, now=T0) == []
        # later it clears the wait and keeps working the same task
        assert unflagged_fire(_task(), team_name=TEAM, now=T0)[0] is False

    def test_2_never_flag_then_idle_FIRES(self):
        """The target case must be untouched by the discharge."""
        self._seed()
        assert discharge_acknowledged_for_owner([_task()], "probe-coder", team_name=TEAM, now=T0) == 0
        assert unflagged_fire(_task(), team_name=TEAM, now=T0)[0] is True, (
            "a teammate that never flagged a wait must still draw the advisory: "
            "a discharge with nothing to acknowledge leaves the record firing"
        )

    def test_3_a_wait_discharges_job1_ONLY_and_job2_fires_once_the_wait_clears(
        self,
    ):
        """RENAMED. It previously read `..._FIRES_FOR_JOB2_ONLY`, which
        asserted more than the arm measures: the fire check below runs on a
        task with NO wait and without a `tasks` list, so at that point the
        job-1 flag is already cleared and the R5 silencing gate is not
        evaluated at all. What this arm actually pins — and it is the
        load-bearing half — is that `since >= registered_at` discharges job 1
        and spares job 2. See the sibling class below for what happens while
        the wait is still open, which is the opposite of what the name implied.
        """
        self._seed({"registered_at": _iso(T0)},
                   {"registered_at": _iso(T0 + timedelta(minutes=10))})
        flagged = _task(wait=_wait(T0 + timedelta(minutes=1)))
        assert discharge_acknowledged_for_owner([flagged], "probe-coder", team_name=TEAM, now=T0) == 1, (
            "exactly one record — job 1 — may be discharged by a wait flagged "
            "before job 2 was launched"
        )
        left = load_records_for_discharge(TEAM, now=T0)
        assert len(left) == 1
        assert left[0]["registered_at"] == _iso(T0 + timedelta(minutes=10))
        # the wait has since been cleared: no valid wait on the task, no tasks list
        assert unflagged_fire(_task(), team_name=TEAM, now=T0)[0] is True, (
            "job 2 was launched after the wait was flagged, so the discharge "
            "spared it; with the wait cleared, job 2 must fire"
        )


    def test_4_RESIDUAL_flag_and_clear_within_one_turn_keeps_the_record(self):
        """Documented residue, not a bug to fix.

        Discharge runs on TeammateIdle. A teammate that flags and clears
        inside one turn never idles between the two, so nothing observes the
        flag and the record survives. Pinned so the limit is visible rather
        than discovered.
        """
        self._seed()
        # no idle occurs, so discharge_acknowledged_for_owner is never called
        assert unflagged_fire(_task(), team_name=TEAM, now=T0)[0] is True, (
            "with no idle between setting and clearing the flag, nothing observed "
            "the flag, so the record must still fire"
        )
        assert len(load_records_for_discharge(TEAM, now=T0)) == 1, (
            "a flag set and cleared inside one turn must leave the record in place: "
            "discharge runs only on TeammateIdle, and no idle occurred"
        )


class TestSuppressionIsTemporaryNotPermanent:
    """What an OPEN wait does to a job launched after it.

    THE DESIGN DOCUMENT SAYS THIS FIRES. IT DOES NOT, AND THE CODE IS RIGHT.
    §6.2's discharge table reads "job 2's registered_at > the wait's since,
    test fails, job 2 not discharged -> fires". Only the first clause is true.
    The record does survive the discharge — and then nothing surfaces it,
    because the task carries a valid wait.

    THE SUPPRESSION IS OVER-DETERMINED, AND SAYING WHICH GATE DOES IT WOULD BE
    WRONG. `unflagged_fire` consults `any_listed_task_flagged` first and
    `classify_wait` second, and on this input EITHER ALONE suffices: MEASURED,
    disabling `any_listed_task_flagged` entirely leaves the job-2 arms below
    green, because `classify_wait` refuses the same frame. So these arms pin
    an OUTCOME with two independent causes, and no arm here should be read as
    evidence about which gate is load-bearing. The R5 silencing gate is
    isolated by `test_a_flag_on_EITHER_held_task_silences_the_advisory`, which
    puts the flag on a DIFFERENT task than the one in hand — the only shape
    where the two gates disagree.

    WHAT THE JOB-2 ARMS UNIQUELY PIN is the discharge's `since` scoping:
    job 2's record SURVIVES an acknowledgment aimed at job 1, and fires once
    the wait closes. MEASURED — the job-2 arms die when `since >= registered`
    is widened to a blanket amnesty and when it is narrowed to never
    discharge. Suppression is temporary; discharge is permanent.

    AND FIRING WOULD BE WORSE THAN A MISSED TICK. The advisory's own text says
    "no flagged wait" — literally false at a teammate holding one open. A
    discredited alarm never recovers, which is the standing posture and what
    decides this.

    Pinned as a pair — suppressed while open, fires once cleared — because
    either arm alone reads as the other's bug.
    """

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "teams" / TEAM).mkdir(parents=True)

    def _two_jobs(self):
        """Two launches, seeded in ONE clock-free write. See the sibling
        class's `_seed` for why this is not two `append_record` calls: the
        second append RE-READS the registry and the TTL prune drops the first
        record once the fixture ages past 24h, so the arm would be measuring
        one job while its name and its assertions claim two."""
        from shared.background_work import save_records

        assert save_records(
            [_record(registered_at=_iso(T0)),
             _record(registered_at=_iso(T0 + timedelta(minutes=10)))],
            team_name=TEAM,
        ) is True
        flagged = _task(wait=_wait(T0 + timedelta(minutes=1)))
        assert discharge_acknowledged_for_owner([flagged], "probe-coder", team_name=TEAM, now=T0) == 1
        return flagged

    def test_job2_is_SILENT_while_the_job1_wait_is_still_open(self):
        flagged = self._two_jobs()
        fire, wait_class, record = unflagged_fire(
            flagged, team_name=TEAM, now=T0, tasks=[flagged]
        )
        assert fire is False, (
            "an advisory here would tell a teammate it has no flagged wait "
            "while it holds one open"
        )
        assert wait_class is None
        assert record is not None, "and the record must SURVIVE, not be discharged"
        assert record["registered_at"] == _iso(T0 + timedelta(minutes=10))

    def test_BOTH_gates_independently_refuse_so_NEITHER_reads_AS_DEAD(self):
        """States the over-determination, so nobody has to re-derive it.

        `unflagged_fire` refuses THIS frame twice over: `any_listed_task_flagged`
        returns True and `classify_wait` returns None, and either alone
        suffices. Nothing else asserts that, because every other arm checks the
        combined outcome, which cannot distinguish one gate from two.

        WHAT THIS ARM IS *NOT*. It is not closing a coverage gap. Both gates
        were ALREADY pinned before it existed — MEASURED at primary +
        adversarial scope, with this arm removed: disabling
        `any_listed_task_flagged` kills 5 arms
        (`test_a_flag_on_EITHER_held_task_silences_the_advisory`,
        `..._suppresses_the_surface`, `test_a_FLAGGED_task_is_NOT_surfaced`,
        `test_the_discharge_read_stays_RAW_so_it_can_still_see_a_flag`,
        `test_the_two_alarms_are_EXCLUSIVE_on_one_task`) and disabling
        `classify_wait`'s validation kills 14. Neither gate can be deleted on a
        green suite.

        That correction matters more than the arm. The belief that neither gate
        was pinned came from a TRUE but NARROW measurement — disabling one gate
        left the two suppression arms in this class green — generalised to the
        suite without re-measuring. The arms that pin each gate use a fixture
        where the two gates DISAGREE (flag on a different task than the one in
        hand); this class uses one where they agree, so of course it could not
        see them.

        What is left for this arm to do is name the property: these two agree
        here and disagree elsewhere, so neither is redundant, and a reader who
        mutates one in THIS shape and sees nothing should not conclude it is
        dead.
        """
        from shared.background_work import any_listed_task_flagged

        flagged = self._two_jobs()
        record = matching_outstanding(
            flagged, records=load_records_for_discharge(TEAM, now=T0)
        )
        assert record is not None

        assert any_listed_task_flagged(record, [flagged]) is True, (
            "gate 1 (R5 silencing) no longer refuses this frame"
        )
        assert classify_wait(flagged) is None, (
            "gate 2 (valid-wait) no longer refuses this frame"
        )
        # and gate 2 alone still suppresses, with no task list for gate 1 to read
        assert unflagged_fire(flagged, team_name=TEAM, now=T0, tasks=None)[0] is False

    def test_job2_FIRES_as_soon_as_that_wait_is_cleared(self):
        """The other half. Without it the arm above reads as a swallowed alarm."""
        self._two_jobs()
        cleared = _task()
        fire, wait_class, record = unflagged_fire(
            cleared, team_name=TEAM, now=T0, tasks=[cleared]
        )
        assert fire is True and wait_class == "missing"
        assert record["registered_at"] == _iso(T0 + timedelta(minutes=10)), (
            "and it fires for JOB 2 — job 1 stays discharged"
        )


# ------------------------------------------------ the Layer 3 read path (gates)


class TestOutstandingUnflagged:
    """The gated selector every surface-read path must go through.

    WHY THIS CLASS EXISTS. Layer 2 reached records through `unflagged_fire`,
    which applies the task-status and flagged-wait gates. Layer 3's lead-side
    selector read `_load_records` directly and applied NEITHER, so it surfaced
    records for completed tasks and for correctly-flagged waits — while the
    lead-facing text asserts "outstanding launches and no flagged wait".
    MEASURED on one 40-minute-old record: `lead_stale` True (surfaced) against
    `unflagged_fire` False on both gates (refused). One gated consumer, one
    ungated.

    The earlier coverage pinned the gates only on the path that already had
    them — coverage that looks like coverage and is not.
    """

    def test_a_completed_task_is_NOT_surfaced(self):
        rec = _sanitize_record(_record())
        tasks = [_task(status="completed")]
        assert outstanding_unflagged(tasks, records=[rec]) == []

    def test_a_FLAGGED_task_is_NOT_surfaced(self):
        """The clause the lead-facing text asserts and the old path never checked."""
        rec = _sanitize_record(_record())
        tasks = [_task(wait=_wait(T0 + timedelta(minutes=1)))]
        assert outstanding_unflagged(tasks, records=[rec]) == []

    def test_an_in_progress_unflagged_task_IS_surfaced(self):
        """The positive, so the negatives above are not vacuous."""
        rec = _sanitize_record(_record())
        assert outstanding_unflagged([_task()], records=[rec]) == [rec]

    def test_a_flag_on_EITHER_held_task_suppresses_the_surface(self):
        rec = _sanitize_record(_record(task_ids=["13", "14"]))
        tasks = [_task("13"), _task("14", wait=_wait(T0))]
        assert outstanding_unflagged(tasks, records=[rec]) == []

    def test_an_unknown_task_id_is_NOT_surfaced(self):
        """A record whose tasks are absent from the store cannot be judged."""
        rec = _sanitize_record(_record(task_ids=["99"]))
        assert outstanding_unflagged([_task("13")], records=[rec]) == []

    def test_a_non_list_task_set_surfaces_NOTHING(self):
        """An unevaluable gate must never read as a passed gate.

        Falling back to the ungated record list here is precisely the defect
        this selector exists to remove.
        """
        rec = _sanitize_record(_record())
        assert outstanding_unflagged(None, records=[rec]) == []

    def test_the_discharge_read_stays_RAW_so_it_can_still_see_a_flag(self):
        """Gate B must NOT move into any read the discharge uses.

        `discharge_acknowledged_for_owner` retires a record BY observing that a valid
        wait covers it. If the loader pre-filtered flagged records away, the
        discharge would never see one and the fix would die silently — green,
        because every test of the gates would still pass.
        """
        rec = _sanitize_record(_record())
        flagged = _task(wait=_wait(T0 + timedelta(minutes=1)))
        assert outstanding_unflagged([flagged], records=[rec]) == []
        assert wait_covers_record(flagged, rec) is True, (
            "the discharge must still be able to see the flagged record that "
            "the surface selector correctly hides"
        )


# ------------------------------------------------------------------ identity


class TestBindLauncherIdentity:
    @pytest.fixture(autouse=True)
    def _team_config(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        team_dir = tmp_path / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps(
                {
                    "leadSessionId": "lead-sid",
                    "members": [
                        {"name": "probe-coder", "agentId": f"probe-coder@{TEAM}",
                         "agentType": "pact-backend-coder"},
                        {"name": "preparer", "agentId": f"preparer@{TEAM}",
                         "agentType": "pact-preparer"},
                    ],
                }
            )
        )
        tasks = tmp_path / "tasks" / TEAM
        tasks.mkdir(parents=True)
        (tasks / "13.json").write_text(
            json.dumps({"id": "13", "status": "in_progress", "owner": "probe-coder"})
        )

    def _frame(self, **over):
        frame = {
            "session_id": "sid",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi", "run_in_background": True},
        }
        frame.update(over)
        return frame

    def test_step2_route_with_NO_agent_name_key(self):
        """Exercises the `@`-bearing agent_id route on its own.

        Every fixture that supplies `agent_name` passes through Step 1, so
        without this arm the Step-2 route would ship entirely unexecuted —
        and nobody has been able to separate the two on live data.
        """
        frame = self._frame(agent_id=f"probe-coder@{TEAM}")
        assert "agent_name" not in frame
        bound = bind_launcher_identity(frame, TEAM)
        assert bound is not None and bound[0] == "probe-coder"

    def test_step1_route(self):
        bound = bind_launcher_identity(self._frame(agent_name="probe-coder"), TEAM)
        assert bound is not None and bound[0] == "probe-coder"

    def test_validated_agent_type_route(self):
        """The measured in-process shape: agent_type carries the NAME."""
        frame = self._frame(agent_id="0123456789abcdef", agent_type="probe-coder")
        assert "agent_name" not in frame and "@" not in frame["agent_id"]
        bound = bind_launcher_identity(frame, TEAM)
        assert bound is not None and bound[0] == "probe-coder"

    def test_task_ids_is_a_list_on_the_bound_result(self):
        bound = bind_launcher_identity(self._frame(agent_name="probe-coder"), TEAM)
        assert bound[2] == ["13"]

    def test_the_raw_agent_type_does_NOT_leak_into_the_step1_route(self):
        """Steps 1/2 must still win, and must NOT record the raw agent_type.

        The membership route records the RAW `agent_type` because that is the
        value validation passed. If that raw value reached the `agent_name`
        or `@`-bearing `agent_id` branches, the mis-bind would MOVE rather
        than close — and a membership-route test cannot see it, because that
        route is the one behaving correctly.

        Here `agent_name` says `probe-coder` while `agent_type` says
        `preparer` — a different LIVE member. Step 1 must win, so the bind
        must report `probe-coder` and `probe-coder`'s task, not `preparer`.
        """
        frame = self._frame(agent_name="probe-coder", agent_type="preparer")
        bound = bind_launcher_identity(frame, TEAM)
        assert bound is not None
        assert bound[0] == "probe-coder", (
            f"Step 1 lost to the agent_type route: bound {bound[0]!r}"
        )
        assert bound[2] == ["13"], (
            "bound the wrong member's tasks — the raw agent_type leaked past "
            "the membership route"
        )

    def test_the_raw_agent_type_does_NOT_leak_into_the_step2_route(self):
        """Same claim for the `@`-bearing `agent_id` branch."""
        frame = self._frame(
            agent_id=f"probe-coder@{TEAM}", agent_type="preparer"
        )
        assert "agent_name" not in frame
        bound = bind_launcher_identity(frame, TEAM)
        assert bound is not None
        assert bound[0] == "probe-coder"
        assert bound[2] == ["13"]

    def test_no_identity_writes_nothing(self):
        frame = self._frame(agent_id="0123456789abcdef", agent_type="pact-backend-coder")
        assert bind_launcher_identity(frame, TEAM) is None

    def test_a_member_owning_no_task_at_all_writes_nothing(self):
        """A member holding only a completed task is a consultant and is
        recorded on that anchor. Only a member owning no task at all binds
        nothing."""
        frame = self._frame(agent_name="preparer")
        assert bind_launcher_identity(frame, TEAM) is None

    # --- the exposure we accepted rather than eliminated -------------------

    def test_prefixed_type_naming_a_live_member_is_REFUSED(self):
        """`Agent(subagent_type="pact-preparer")` must not bind to `preparer`.

        A non-teammate spawn of a PACT agent type is ordinary usage. Matching
        the RAW value is what closes this: an in-process teammate's frame
        carries the bare name, a type carries the `pact-` prefix, so the
        prefix is itself the discriminator. Never strip before matching.
        """
        assert agent_type_names_a_member("pact-preparer", TEAM) is False
        frame = self._frame(agent_id="0123456789abcdef", agent_type="pact-preparer")
        assert bind_launcher_identity(frame, TEAM) is None

    def test_prefixed_collision_is_refused_BY_THE_RAW_MATCH_not_the_deny_set(self):
        """Isolates the raw-match rule from the deny set that shadows it.

        `pact-preparer` is ALSO an agents/*.md stem, so the deny set refuses
        it before the name comparison ever runs — which means the previous
        arm passes whether or not the match is raw. MEASURED: a mutant that
        strips the prefix before comparing SURVIVED that arm.

        `probe-coder` is a member whose `pact-`-prefixed spelling is NOT a
        shipped agent file, so the deny set does not fire and only the raw
        comparison can refuse it. This arm is the one that separates the two
        mechanisms, and it is the reason the rule is "match raw, never strip".
        """
        from shared.background_work import _known_agent_types

        assert "pact-probe-coder" not in _known_agent_types(), (
            "this arm requires a member whose prefixed spelling is NOT a "
            "shipped agent stem, or the deny set shadows what it measures"
        )
        assert agent_type_names_a_member("probe-coder", TEAM) is True
        assert agent_type_names_a_member("pact-probe-coder", TEAM) is False
        frame = self._frame(agent_id="0123456789abcdef", agent_type="pact-probe-coder")
        assert bind_launcher_identity(frame, TEAM) is None

    @pytest.mark.parametrize(
        "platform_type", ["general-purpose", "Explore", "Plan", "statusline-setup"]
    )
    def test_platform_types_are_refused(self, platform_type):
        assert agent_type_names_a_member(platform_type, TEAM) is False

    def test_deny_set_control_member_named_after_a_real_agent_type(self, tmp_path):
        """The residual, made visible rather than claimed away.

        A member NAMED after a shipped agent type is refused by the deny set.
        An UNKNOWN FUTURE platform type colliding with a member name is NOT
        covered, and fails toward mis-bind rather than silence.
        """
        import json

        cfg = tmp_path / "teams" / TEAM / "config.json"
        cfg.write_text(
            json.dumps(
                {"members": [{"name": "pact-architect", "agentType": "pact-architect"}]}
            )
        )
        assert agent_type_names_a_member("pact-architect", TEAM) is False


class TestTheAgentIdShapeDecidesBeforeTheTypeChecks:
    """`agent_type_names_a_member(..., agent_id=)` reads the frame's id shape first.

    An in-process teammate's frame carries "a" + agent_type + "-" + 16 lowercase
    hex, and an Agent-tool subagent's carries "a" + 16 lowercase hex. A
    subagent-shaped id is never a member. A teammate-shaped id goes to
    membership without the deny set. Any other id, or none, keeps the
    type-based checks, which the arms above pin.
    """

    MEMBERS = ("pact-backend-coder", "general-purpose", "claude", "probe-work-coder")

    @pytest.fixture(autouse=True)
    def _team_config(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        team_dir = tmp_path / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps(
            {"leadSessionId": "lead-sid", "members": [{"name": n} for n in self.MEMBERS]}
        ))
        tasks = tmp_path / "tasks" / TEAM
        tasks.mkdir(parents=True)
        (tasks / "13.json").write_text(
            json.dumps({"id": "13", "status": "in_progress", "owner": "claude"})
        )
        self.root = tmp_path

    def test_a_teammate_shaped_id_admits_a_member_named_after_a_shipped_stem(self):
        """REVERT PROOF. Without the id the deny set refuses this member on its
        own frame, so it never gets its peer block or its launch record."""
        from shared.background_work import _known_agent_types

        assert "pact-backend-coder" in _known_agent_types(), "the arm needs a shipped stem"
        assert agent_type_names_a_member(
            "pact-backend-coder", TEAM, agent_id=f"apact-backend-coder-{HEX16}"
        ) is True
        assert agent_type_names_a_member("pact-backend-coder", TEAM) is False

    def test_a_teammate_shaped_id_admits_a_member_named_after_a_platform_type(self):
        assert agent_type_names_a_member(
            "general-purpose", TEAM, agent_id=f"ageneral-purpose-{HEX16}"
        ) is True
        assert agent_type_names_a_member("general-purpose", TEAM) is False

    def test_a_subagent_shaped_id_is_never_a_member(self):
        """REVERT PROOF. An Agent-tool subagent whose type equals a member's
        name must not be read as that member."""
        assert agent_type_names_a_member("claude", TEAM, agent_id=f"a{HEX16}") is False
        assert agent_type_names_a_member("claude", TEAM) is True

    @pytest.mark.parametrize(
        "agent_type, agent_id, expected",
        [
            ("pact-backend-coder", f"apact-backend-coder-{HEX16[:15]}", False),
            ("pact-backend-coder", f"apact-backend-coder-{HEX16.upper()}", False),
            ("pact-backend-coder", f"aother-{HEX16}", False),
            ("pact-backend-coder", f"apact-backend-coder-{HEX16}0", False),
            ("pact-backend-coder", f"apact-backend-coder-{HEX16}\n", False),
            ("claude", f"a{HEX16}0", True),
            ("claude", f"a{HEX16.upper()}", True),
            ("claude", f"a{HEX16}\n", True),
        ],
        ids=["teammate-15-hex", "teammate-uppercase", "teammate-name-mismatch",
             "teammate-17-hex", "teammate-trailing-newline", "subagent-17-hex",
             "subagent-uppercase", "subagent-trailing-newline"],
    )
    def test_only_the_exact_shapes_are_recognized(self, agent_type, agent_id, expected):
        """Anything short of an exact shape falls through to the type-based checks."""
        assert agent_type_names_a_member(agent_type, TEAM, agent_id=agent_id) is expected

    def test_a_teammate_shaped_id_for_a_non_member_is_refused(self):
        assert agent_type_names_a_member("stranger", TEAM, agent_id=f"astranger-{HEX16}") is False

    def test_a_teammate_shaped_id_with_an_unsafe_team_is_refused(self):
        """`teams/../config.json` exists and names the member, so only the
        team-name check refuses it."""
        import json
        from shared.pact_context import _iter_members

        (self.root / "config.json").write_text(json.dumps({"members": [{"name": "claude"}]}))
        assert [m["name"] for m in _iter_members("..")] == ["claude"], "control: the path resolves"
        assert agent_type_names_a_member("claude", "..", agent_id=f"aclaude-{HEX16}") is False

    @pytest.mark.parametrize(
        "team, config_under_root",
        [(".", "teams/config.json"), ("..", "config.json"), ("a/b", "teams/a/b/config.json"),
         ("", None)],
        ids=["dot", "dotdot", "slash", "empty"],
    )
    def test_the_type_checks_refuse_an_unsafe_team(self, team, config_under_root):
        """REVERT PROOF for the non-empty names. Each resolves to a real config
        naming the member, so only the team-name check refuses it. An empty
        team reads no config at all, so that case is a guard, not a proof."""
        import json
        from shared.pact_context import _iter_members

        if config_under_root:
            path = self.root / config_under_root
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"members": [{"name": "claude"}]}))
            assert [m["name"] for m in _iter_members(team)] == ["claude"], "control: the path resolves"
        assert agent_type_names_a_member("claude", team) is False

    @pytest.mark.parametrize("agent_id", [{"id": "x"}, 12345, [f"a{HEX16}"]])
    def test_a_non_string_id_keeps_the_type_checks(self, agent_id):
        assert agent_type_names_a_member("claude", TEAM, agent_id=agent_id) is True
        assert agent_type_names_a_member("pact-backend-coder", TEAM, agent_id=agent_id) is False

    def test_every_caller_passes_the_frames_agent_id(self):
        """Each call site in the hooks tree hands the predicate the frame's id."""
        hooks = Path(__file__).resolve().parents[1] / "hooks"
        sites = []
        for path in sorted(hooks.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "attr", getattr(node.func, "id", None))
                if name == "agent_type_names_a_member":
                    passed = next((k.value for k in node.keywords if k.arg == "agent_id"), None)
                    sites.append((path.relative_to(hooks).as_posix(),
                                  ast.unparse(passed) if passed is not None else None))
        assert sorted(p for p, _ in sites) == [
            "peer_inject.py", "shared/background_work.py",
            "shared/background_work.py", "shared/turn_end_gate.py",
        ], sites
        assert all(v == "input_data.get('agent_id')" for _, v in sites), sites

    def test_the_launch_name_refuses_a_subagent_named_like_a_member(self):
        """REVERT PROOF. `teammate_launch_name` step 2 took the member's name
        from a subagent's frame, so the launch advisory and Layer 1 treated the
        subagent as that teammate."""
        from shared.background_work import teammate_launch_name

        frame = {"session_id": "sid", "tool_name": "Bash", "agent_type": "claude"}
        assert teammate_launch_name(dict(frame, agent_id=f"a{HEX16}"), TEAM) == ""
        assert teammate_launch_name(dict(frame, agent_id=f"aclaude-{HEX16}"), TEAM) == "claude"

    def test_the_launch_binding_refuses_a_subagent_named_like_a_member(self):
        frame = {"session_id": "sid", "tool_name": "Bash", "agent_type": "claude",
                 "tool_input": {"command": "sleep 5", "run_in_background": True}}
        assert bind_launcher_identity(dict(frame, agent_id=f"a{HEX16}"), TEAM) is None
        bound = bind_launcher_identity(dict(frame, agent_id=f"aclaude-{HEX16}"), TEAM)
        assert bound is not None and bound[0] == "claude" and bound[2] == ["13"], bound

    def test_the_turn_end_identity_refuses_a_subagent_named_like_a_member(self):
        """REVERT PROOF. The turn-end gate read a subagent ending its turn as
        the member its type names."""
        from shared import turn_end_gate

        frame = {"session_id": "sid", "hook_event_name": "SubagentStop", "agent_type": "claude"}
        assert turn_end_gate.teammate_identity(dict(frame, agent_id=f"a{HEX16}"), TEAM) == ""
        assert turn_end_gate.teammate_identity(
            dict(frame, agent_id=f"aclaude-{HEX16}"), TEAM
        ) == "claude"

    def test_captured_frames(self):
        """The captured teammate frame's `agent_id` is a synthetic value in
        neither shape, so it stays True through the type checks; a copy rebuilt
        to the measured teammate shape is True through membership. The captured
        general-purpose subagent frame carries the subagent shape."""
        from fixtures.role_frames import (
            captured_posttooluse_teammate_inprocess_bash_background,
            captured_pretooluse_teammate_inprocess_subagent,
        )

        mate = captured_posttooluse_teammate_inprocess_bash_background()
        name = mate["agent_type"]
        assert name == "probe-work-coder"
        assert agent_type_names_a_member(name, TEAM, agent_id=mate["agent_id"]) is True
        assert agent_type_names_a_member(name, TEAM, agent_id=f"a{name}-{HEX16}") is True
        sub = captured_pretooluse_teammate_inprocess_subagent()
        assert agent_type_names_a_member(
            sub["agent_type"], TEAM, agent_id=sub["agent_id"]
        ) is False


class TestShellBackgroundedLaunchPopulation:
    """`is_shell_backgrounded_bash` — the population the detector ADDS.

    🔴 WHAT THIS CLASS DOES NOT SHOW, STATED FIRST BECAUSE IT IS THE TRAP.
    Every arm here tests the PREDICATE IN ISOLATION. All of them pass whether
    or not `record_background_launch` ever calls it, so none of them can tell
    you the widening is wired in. That is not a defect in these arms; it is
    what a predicate test is. The wiring is pinned by a POSITIVE-RECORD arm
    through the real seam, in test_track_files_background_integration.py, and
    the general rule is worth stating once: only an arm asserting that a
    record WAS written can detect a wiring break, because an unwired gate
    fails CLOSED and a silent refusal is observationally identical to a
    correct one. Every negative-asserting arm below is structurally incapable
    of catching it.

    THE POPULATION IS EXACTLY "a command ENDING in a bare `&`", and nothing
    here may be read as broader. A mid-line background, a subshell and a
    disowned job all miss, and they are pinned as such below rather than left
    to a docstring — the prose describing this feature has already been wrong
    twice in the overclaiming direction.
    """

    @staticmethod
    def _frame(command, tool_name="Bash"):
        return {"tool_name": tool_name, "tool_input": {"command": command}}

    # MEASURED against the predicate before being written down, not predicted.
    LAUNCHES = [
        "nohup ./gate.sh &",
        "sleep 720 &",
        "python3 -m pytest -q > out.log 2>&1 &",
        "./run.sh --flag &   ",          # trailing whitespace, rstrip'd
        "nohup bash -c 'a; b' &",
    ]
    NOT_BACKGROUNDING = [
        "a && b",                        # conjunction
        "make -j4 &&",                   # ENDS in `&&` — the exclusion's own case
        "echo x 2>&1",                   # fd redirect
        "echo 'a & b'",                  # quoted
        "cmd & wait",                    # backgrounds, then waits: not a launch
        "echo done \\&",                 # escaped literal
        "echo plain",                    # no ampersand at all
        "cat <<'EOF'\nbody &\nEOF",      # `&` inside a heredoc body
    ]
    DOCUMENTED_MISSES = [
        "nohup ./gate.sh & echo started",
        "( ./gate.sh & )",
        "./gate.sh & sleep 1",
        "setsid ./gate.sh & disown",
    ]

    @pytest.mark.parametrize("command", LAUNCHES)
    def test_a_command_ENDING_in_a_bare_ampersand_is_a_launch(self, command):
        """ARM 1. The shapes the widening exists to catch.

        MUTANT that reddens this arm: drop the `.rstrip()` — the trailing-
        whitespace case then stops matching, which is the realistic edit
        because the strip looks redundant until you have a command built by
        string concatenation.
        """
        assert is_shell_backgrounded_bash(self._frame(command)) is True, command

    @pytest.mark.parametrize("command", NOT_BACKGROUNDING)
    def test_an_ampersand_that_does_not_background_is_NOT_a_launch(self, command):
        """ARM 2. The over-fire direction.

        An over-fire here costs one spurious registry row, which is visible
        and dischargeable — so this is the CHEAP direction and the arm exists
        to keep it cheap rather than to prevent a catastrophe. The expensive
        direction is a miss, which is silent.

        MUTANT that reddens this arm: drop the `&&` / `\\&` exclusions, and
        `a && b` and `echo done \\&` start recording.
        """
        assert is_shell_backgrounded_bash(self._frame(command)) is False, command

    @pytest.mark.parametrize("command", DOCUMENTED_MISSES)
    def test_the_DOCUMENTED_LIMITS_are_still_missed(self, command):
        """ARM 7. A pin on a KNOWN GAP, so widening the predicate forces the
        docstring to move with it.

        🔴 THIS ARM ASSERTS A LIMITATION, NOT A REQUIREMENT. Each command
        below genuinely backgrounds work that this hook will not see. They are
        listed verbatim in `is_shell_backgrounded_bash`'s docstring as measured
        misses, and this arm is what stops that list going quietly stale: if a
        future change starts catching one of these, this arm reddens and
        whoever widened the predicate must update the docstring in the same
        commit rather than leaving prose that understates coverage.

        IF YOU HAVE DELIBERATELY WIDENED THE PREDICATE, DELETE THE CASE YOU
        NOW CATCH — its reddening is the improvement landing, not a
        regression. Do not restore the old behaviour to keep this green.

        MUTANT that reddens this arm: change `endswith("&")` to `"&" in
        command`, which is the obvious "surely we should catch mid-line too"
        edit; every command listed then matches.
        """
        assert is_shell_backgrounded_bash(self._frame(command)) is False, (
            "%r is a DOCUMENTED miss. If the predicate now catches it, that is "
            "a widening — delete this case and update the docstring's measured-"
            "misses list in the same commit." % (command,)
        )

    def test_a_NON_Bash_frame_ending_in_an_ampersand_is_refused(self):
        """ARM 3. The tool gate, which no command-text case can cover.

        Without it a Write or Edit frame whose payload happens to end in `&`
        would be recorded as a launch.

        MUTANT that reddens this arm: drop the `tool_name != "Bash"` check.
        """
        assert is_shell_backgrounded_bash(
            self._frame("sleep 1 &", tool_name="Write")) is False

    @pytest.mark.parametrize("frame", [
        None, {}, {"tool_name": "Bash"},
        {"tool_name": "Bash", "tool_input": {"command": None}},
        {"tool_name": "Bash", "tool_input": "not-a-dict"},
    ])
    def test_a_MALFORMED_frame_returns_False_rather_than_raising(self, frame):
        """ARM 4. Fail-open, at the predicate.

        The host calls this for its side effect only and must not be disturbed
        by anything here, so a malformed frame returns False rather than
        propagating. `assert is False` rather than `assert not` on purpose: a
        raise would fail the arm, but so would a None return, and those are
        different defects.

        MUTANT that reddens this arm: drop the isinstance guards and a None
        frame raises AttributeError instead of returning False.
        """
        assert is_shell_backgrounded_bash(frame) is False


MODULE_SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "shared" / "background_work.py"


def _module_functions():
    """Top-level functions of background_work.py, as AST nodes."""
    tree = ast.parse(MODULE_SOURCE.read_text(encoding="utf-8"))
    return [n for n in tree.body if isinstance(n, ast.FunctionDef)]


def _params(fn) -> list:
    return [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]


def _references(fn, name: str) -> bool:
    """True if `name` is READ as a value anywhere in the body.

    A keyword argument that merely CARRIES the name does not count. Counting it
    let `f(now=None)` pass as a use: that call names the parameter while
    discarding the clock it holds, which is exactly the decorative parameter
    this guard exists to catch. Forwarding the clock, `f(now=now)`, still
    counts, because the keyword's value is itself a read of `now`.
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            return True
    return False


class TestTheClockIsNotDecorative:
    """Structural guard: a `now=` parameter must mean something.

    🔴 WHY THIS EXISTS, and it is a guard against a well-intentioned edit
    rather than against a bug. An earlier dispatch on this module asked for
    the clock to be threaded through its write paths, and that instruction was
    too wide: some of these functions have no clock to thread. A `now=` on a
    function that never consults it PROMISES DETERMINISM WHERE THERE IS
    NOTHING TO DETERMINE — and a test written against that promise passes for
    no reason at all, which is strictly worse than no test, because it reports
    coverage of a behaviour that does not exist.

    The reader this stops is someone tidying up: they see `append_record(...,
    now=None)` beside `save_records(...)` without one, read it as an
    oversight, and "finish the job".
    """

    def test_a_now_parameter_is_never_DECORATIVE(self):
        """THE GENERAL INVARIANT, and it is derived rather than listed.

        Every function taking `now=` must actually REFERENCE it — in
        arithmetic, in a comparison, or by forwarding it onward. This is
        deliberately NOT 'must read a clock': `_record_expired` reads no clock
        and is correct, because it does the subtraction itself, and a
        reads-a-clock rule would have false-fired on it. Nor is it 'must
        forward': the same function forwards nothing.

        A LIST OF FUNCTION NAMES WOULD HAVE GONE STALE ALREADY. The set of
        clock-threaded functions on this module changed once during this
        branch, so this arm computes its own population from the source every
        run and cannot describe a module that has moved.
        """
        offenders = [
            fn.name for fn in _module_functions()
            if "now" in _params(fn) and not _references(fn, "now")
        ]
        assert offenders == [], (
            "these functions accept `now=` and never use it: %s. A clock "
            "parameter that is ignored advertises determinism the function "
            "does not provide, and any test written against it passes without "
            "exercising anything. Either use it or remove it." % (offenders,)
        )

    # MEASURED FROM SOURCE, NOT COPIED FROM A LIST. Every state-file write in
    # the module goes through `state_file.locked_update` (or its `write_text`),
    # the one open path that writes; `state_file.read_text` only reads. The
    # clockless writers are the functions in THIS module that reach that path
    # with no `now=` and no clock read. Writers that DO take a clock
    # (`_atomic_update_records`, `append_record`, `discharge_acknowledged_for_owner`,
    # `stamp_idled_at`, `record_background_launch`) are outside this list on
    # purpose. Listed as a DECISION rather than as an inventory: if the write
    # path is restructured, re-derive it.
    CLOCKLESS_WRITERS = (
        "save_records",
        "update_unflagged_idle_counts",
        "save_unflagged_idle_counts",
    )

    def test_the_deliberately_clockless_writers_take_no_now(self):
        """The writers in CLOCKLESS_WRITERS persist bytes and consult no clock, ON PURPOSE.

        They are pure I/O: given the content, they write it. None of them
        prunes, compares, stamps or expires anything, so there is no moment at
        which 'what time is it' could change what they do.

        IF YOU ARE HERE BECAUSE THIS WENT RED, the question to answer first is
        NOT 'how do I thread the clock' but 'what in this function now depends
        on the time'. If the honest answer is nothing, the `now=` should come
        back out. If something genuinely does, delete the name from the tuple
        above in the same commit and say what changed — the tuple is a record
        of a decision, and moving it is a decision too.
        """
        by_name = {fn.name: fn for fn in _module_functions()}
        missing = [n for n in self.CLOCKLESS_WRITERS if n not in by_name]
        assert missing == [], (
            "named clockless writers no longer exist in the module: %s. They "
            "were renamed or removed, so this guard is now pointing at "
            "nothing — re-derive the set rather than deleting the arm."
            % (missing,)
        )
        acquired = [n for n in self.CLOCKLESS_WRITERS if "now" in _params(by_name[n])]
        assert acquired == [], (
            "these writers acquired a `now=` parameter: %s. They read no clock "
            "and decide nothing from the time, so the parameter cannot change "
            "their behaviour — it only promises a determinism they do not "
            "have. If one genuinely became time-dependent, remove it from "
            "CLOCKLESS_WRITERS in the same commit and say what changed."
            % (acquired,)
        )

    def test_CONTROL_the_guard_can_see_a_now_parameter_at_all(self):
        """Non-vacuity. The guard arms above assert an EMPTY list, and an empty
        list is what a broken parser returns too — a typo in the module path,
        an `ast` walk that finds no FunctionDef, or a `_params` that never
        sees a keyword-only argument would all report a clean pass.

        So: the module must contain functions that DO take `now=`, and
        `_references` must return True for them. If this reddens, the guard
        arms above are measuring nothing and their green means nothing.
        """
        with_now = [fn for fn in _module_functions() if "now" in _params(fn)]
        assert len(with_now) >= 5, (
            "found %d functions taking `now=` in %s — the parser is not seeing "
            "the module and the guards above are vacuous"
            % (len(with_now), MODULE_SOURCE.name)
        )
        # DELIBERATELY NOT re-asserting the invariant here. A control that
        # fails for the SAME reason as the arm it controls is not an
        # independent control — it inflates the kill set and hides which
        # property actually broke.
        #
        # Instead the HELPER is checked against synthetic functions whose
        # answers are known, which is independent of whatever the module
        # currently looks like: if `_references` ever stops discriminating,
        # the guards above go quietly green and this is the only arm that
        # says so.
        probe = ast.parse(
            "def uses(now):\n    return now\n"
            "def ignores(now):\n    return 1\n"
            "def forwards(now):\n    return f(now=now)\n"
            "def forwards_none(now):\n    return f(now=None)\n"
        ).body
        assert _references(probe[0], "now") is True, (
            "_references cannot see a parameter that IS used — the guards "
            "above would report a clean pass on a module full of offenders")
        assert _references(probe[1], "now") is False, (
            "_references reports a use where there is none — the guards "
            "above are then unfalsifiable")
        assert _references(probe[2], "now") is True, (
            "_references no longer counts forwarding the clock as a use, so "
            "the guard would flag every function that threads `now` onward")
        assert _references(probe[3], "now") is False, (
            "_references counts `f(now=None)` as a use: a call that names the "
            "parameter while discarding the clock passes the guard")


class TestTheLaunchPathThreadsOneClock:
    """`record_background_launch(now=)` stamps AND prunes on the clock it is
    given, never on the real one.

    The launch writes a new row and, in the same locked write, prunes rows
    older than the 24-hour TTL. If one step reads the injected clock and the
    other reads the real one, the launch stamps at one time and prunes at
    another, which no real clock can produce. The structural guard above
    cannot see that: dropping `now=` from one call leaves the function still
    reading `now` elsewhere.

    THE EXISTING ROW IS STAMPED IN THE YEAR 2000. Correct code never reads the
    real clock here, so that date cannot age out. Any real clock is more than
    24 hours later, so a step that falls back to the real clock prunes the row,
    and the arm sees it.
    """

    LAUNCHER = "clock-coder"
    SESSION_ID = "clock-arm-session"
    PROJECT_DIR = "/clock-arm/project"
    CLOCK_TEAM = "session-clockarm"
    OLD = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    NOW = OLD + timedelta(hours=1)

    @pytest.fixture
    def rows_after_launch(self, tmp_path, monkeypatch):
        """Seed one row at OLD, launch at NOW, return the rows as written."""
        import json

        import shared.pact_context as pact_context
        from shared import background_work as bw
        from shared.pact_context import project_slug

        config = tmp_path / ".claude"
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", self.PROJECT_DIR)

        def write(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

        write(config / "teams" / self.CLOCK_TEAM / "config.json", {
            "leadSessionId": self.SESSION_ID,
            "members": [{"name": self.LAUNCHER,
                         "agentId": f"{self.LAUNCHER}@{self.CLOCK_TEAM}",
                         "agentType": "pact-backend-coder",
                         "backendType": "in-process"}],
        })
        write(config / "pact-sessions" / project_slug(self.PROJECT_DIR)
              / self.SESSION_ID / "pact-session-context.json", {
            "session_id": self.SESSION_ID,
            "project_dir": self.PROJECT_DIR,
            "team_name": self.CLOCK_TEAM,
        })
        write(config / "tasks" / self.CLOCK_TEAM / "13.json",
              {"id": "13", "status": "in_progress", "owner": self.LAUNCHER})
        assert bw.save_records([{
            "agent_name": self.LAUNCHER,
            "session_id": self.SESSION_ID,
            "task_ids": ["13"],
            "registered_at": self.OLD.isoformat(),
        }], team_name=self.CLOCK_TEAM) is True

        frame = {
            "hook_event_name": "PostToolUse",
            "session_id": self.SESSION_ID,
            "tool_name": "Bash",
            "agent_type": self.LAUNCHER,
            "agent_id": "0123456789abcdef",
            "tool_input": {"command": "sleep 5", "run_in_background": True},
        }
        pact_context.init(frame)
        assert bw.record_background_launch(frame, now=self.NOW) is True
        registry = config / "teams" / self.CLOCK_TEAM / "background_work.json"
        return json.loads(registry.read_text(encoding="utf-8")).get("records", [])

    def test_the_launch_PRUNES_on_the_injected_clock(self, rows_after_launch):
        stamps = sorted(r.get("registered_at") for r in rows_after_launch)
        assert len(rows_after_launch) == 2, (
            "the launch pruned a row stamped one hour before the injected "
            "clock, so its prune ran on the real clock instead. Rows left: %r"
            % (stamps,)
        )

    def test_the_new_row_is_STAMPED_on_the_injected_clock(self, rows_after_launch):
        new = [r.get("registered_at") for r in rows_after_launch
               if r.get("registered_at") != self.OLD.isoformat()]
        assert new == [self.NOW.isoformat(timespec="seconds")], (
            "the new row was stamped %r, not the injected clock %r, so the "
            "stamp ran on the real clock" % (new, self.NOW.isoformat(timespec="seconds"))
        )



class TestOneDischargePassPerIdle:
    """An idle retires every acknowledged record in ONE registry update."""

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def test_an_idle_discharges_every_owned_task_in_one_registry_write(self, monkeypatch):
        """30 owned tasks, each with a covering wait and its own record: one write drops all 30."""
        from shared import background_work as bw
        from shared import state_file

        import teammate_idle

        owner = "probe-coder"
        tasks = [
            _task(task_id=str(i), wait=_wait(T0 + timedelta(minutes=1)))
            for i in range(30)
        ]
        assert bw.save_records(
            [_record(task_ids=[str(i)], registered_at=_iso(T0)) for i in range(30)],
            team_name=TEAM,
        ) is True
        registry = bw.registry_path(TEAM)
        registry_writes = []
        real_locked_update = state_file.locked_update

        def counting_locked_update(path, apply, root, *args, **kwargs):
            if Path(path) == registry:
                registry_writes.append(path)
            return real_locked_update(path, apply, root, *args, **kwargs)

        monkeypatch.setattr(state_file, "locked_update", counting_locked_update)
        teammate_idle.check_unflagged_background(tasks, owner, TEAM, now=T0)

        assert len(registry_writes) == 1, (
            f"the idle made {len(registry_writes)} registry updates for 30 owned "
            "tasks; the discharge must be one pass"
        )
        assert load_records_for_discharge(TEAM, now=T0) == []

    def test_one_pass_drops_only_covered_records(self):
        """Guard arm: a wait anchored before its record's launch does not discharge it."""
        from shared import background_work as bw

        covered = _task(task_id="A", wait=_wait(T0 + timedelta(minutes=1)))
        not_covered = _task(task_id="B", wait=_wait(T0 - timedelta(minutes=1)))
        assert bw.save_records(
            [_record(task_ids=["A"], registered_at=_iso(T0)),
             _record(task_ids=["B"], registered_at=_iso(T0))],
            team_name=TEAM,
        ) is True
        assert discharge_acknowledged_for_owner(
            [covered, not_covered], "probe-coder", team_name=TEAM, now=T0
        ) == 1
        assert [r["task_ids"] for r in load_records_for_discharge(TEAM, now=T0)] == [["B"]]


# ---------------------------------------------------------- frame route


class TestFrameTeamAndNameReadsATeammateIdleFrame:
    """The frame route: a TeammateIdle frame's own `team_name` and
    `teammate_name`, trusted only when that name is a member of that team."""

    TEAM = "session-frameroute"
    MEMBER = "frame-coder"

    @pytest.fixture
    def config(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        team_dir = tmp_path / "teams" / self.TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "leadSessionId": "frame-lead-session",
            "members": [{"name": self.MEMBER, "agentId": f"{self.MEMBER}@{self.TEAM}",
                         "agentType": "pact-backend-coder"}],
        }))
        return tmp_path

    def _frame(self, team=None, name=None, session_id="frame-idle-session"):
        return {"hook_event_name": "TeammateIdle", "session_id": session_id,
                "team_name": team if team is not None else self.TEAM,
                "teammate_name": name if name is not None else self.MEMBER}

    def test_frame_team_and_name_resolves_a_teammate_idle_frame_without_context(self, config):
        """REVERT PROOF. No context and no registry entry, so only the frame route
        can resolve it."""
        from shared.background_work import frame_team_and_name

        assert frame_team_and_name(self._frame()) == (self.TEAM, self.MEMBER)

    def test_frame_route_rejects_an_unsafe_team_name(self, config):
        """GUARD. A traversal team name resolves nothing, even with a member name."""
        from shared.background_work import frame_team_and_name

        assert frame_team_and_name(self._frame(team="../" + self.TEAM)) == ("", "")

    def test_frame_route_rejects_a_non_member_name(self, config):
        """GUARD. A non-member name falls through to the registry route, which
        names the real member for this session."""
        import json

        from shared.background_work import frame_team_and_name

        registry = config / "pact-sessions" / ".teammate-registry.jsonl"
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({
            "session_id": "frame-idle-session", "value": f"{self.MEMBER}@{self.TEAM}",
        }) + "\n")
        assert frame_team_and_name(self._frame(name="stranger")) == (self.TEAM, self.MEMBER)

    def test_context_route_stays_first(self, config, pact_context):
        """GUARD. A session with a PACT context resolves its context team, whatever
        team the frame names."""
        from shared.background_work import frame_team_and_name

        pact_context(team_name="context-team", session_id="context-session")
        assert frame_team_and_name(self._frame()) == ("context-team", "")
