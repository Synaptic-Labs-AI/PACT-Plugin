"""dispatch_site — the hook-side emit that carries the coverage DENOMINATOR.

One event per dispatched Task-B, emitted at the owner-WITNESSING TaskUpdate —
any write naming a pact-specialist `owner` on a non-teachback, non-exempt
task. The event's EXISTENCE is a dispatch site; the OPTIONAL `variety` within
it is the numerator. Both terms come from one stream, which is what makes
coverage > 1.0 structurally impossible rather than merely guarded.

MEMBERSHIP IS OWNER-WITNESSED, NEVER INFERRED. Live dispatch practice wires
owners in SPLIT writes (owner in one update, blockers at creation or in
another), so the population leg is the owner-BEARING shape, not the composite
owner+addBlockedBy the ordering gate keys on — a composite-keyed population is
dark by construction on every real arc. Teachback Task-A gates are excluded by
the SUBJECT carve-out alone (the named dependence: an owner-only write is
exactly the split wiring a gate receives); review-panel dispatches are EXCLUDED
because no owner write ever witnesses them (documented exclusion — their
dispatches stay visible via the review_dispatch stream).

WHY THE TaskUpdate BRANCH AND NOT BESIDE dispatch_variety: that emit lives in
the TaskCreate branch and keys on metadata.variety PRESENCE, because
TaskCreate(B) leaves owner empty — an owner-wiring predicate placed there would
return False forever and Q5 would stay dark with every test still green. These
tests pin the emit to the branch where an owner-bearing write is observable.

Each step of the evaluation order gets a negation test, because every one of
them fails SILENTLY: a wrong frame gate zeroes the population, a dropped
team_name precondition craters coverage while the number still renders, and a
missing dedup claim turns one dispatch into two sites. None of those produce an
exception or a red anywhere else.

Drives tlg.evaluate_lifecycle against a real on-disk team config, task store
and O_EXCL marker root, with tlg.append_event spied to capture events — EXCEPT
the seam-integration class at the bottom, which leaves the journal write real.
"""
import json
from pathlib import Path

import pytest

import task_lifecycle_gate as tlg  # noqa: E402
import shared.session_journal as sj  # noqa: E402

TEAM = "test-team"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"

MEMBERS = [
    {"name": "backend-coder", "agentType": "pact-backend-coder"},
    {"name": "auditor", "agentType": "pact-auditor"},
    {"name": "secretary", "agentType": "pact-secretary"},
    {"name": "explorer", "agentType": "general-purpose"},
]

# The 5 canonical keys the projection must keep. The *_rationale strings are
# calibration noise in a GC-immune journal and must be dropped.
CANONICAL = {"novelty", "scope", "uncertainty", "risk", "total"}

STAMP = {
    "novelty": 3, "novelty_rationale": "new shape",
    "scope": 2, "scope_rationale": "two files",
    "uncertainty": 2, "uncertainty_rationale": "closed spec",
    "risk": 4, "risk_rationale": "silent failure modes",
    "total": 11,
}


@pytest.fixture
def env(tmp_path, monkeypatch, pact_context):
    """Real team config + specialist registry + task store + marker root."""
    import shared.dispatch_helpers as dh
    import shared.pact_context as ctx

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    team_dir = tmp_path / ".claude" / "teams" / TEAM
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"leadSessionId": "s1", "members": MEMBERS}), encoding="utf-8"
    )

    plugin_root = tmp_path / "plugin"
    agents = plugin_root / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for m in MEMBERS:
        at = m["agentType"]
        if at.startswith("pact-"):
            (agents / f"{at}.md").write_text(f"---\nname: {at}\n---\n", "utf-8")
    monkeypatch.setattr(ctx, "get_plugin_root", lambda: str(plugin_root))
    dh._specialist_registry.cache_clear()

    pact_context(team_name=TEAM, session_id="s1", project_dir=str(tmp_path))
    return tmp_path


@pytest.fixture
def events(monkeypatch):
    captured: list[dict] = []
    monkeypatch.setattr(sj, "append_event", lambda e: captured.append(e) or True)
    return captured


def seed(tmp_path, task_id="42", subject="backend-coder: implement the thing",
         owner="backend-coder", metadata=None):
    d = tmp_path / ".claude" / "tasks" / TEAM
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps({
        "id": task_id, "subject": subject, "owner": owner,
        "metadata": metadata if metadata is not None else {},
    }), encoding="utf-8")


def wiring(task_id="42", *, owner="backend-coder", blockers=("41",),
           agent_type=LEAD, metadata=None, session_id=None):
    ti = {"taskId": task_id, "status": "in_progress"}
    if owner is not None:
        ti["owner"] = owner
    if blockers:
        ti["addBlockedBy"] = list(blockers)
    if metadata is not None:
        ti["metadata"] = metadata
    payload = {"tool_name": "TaskUpdate", "tool_input": ti}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def sites(events):
    return [e for e in events if e.get("type") == "dispatch_site"]


class TestEmitsAtTheOwnerWitnessWrite:
    def test_owner_only_split_wiring_write_NOW_EMITS(self, env, events):
        """CARRIED FORWARD AND FLIPPED — this arm was
        ``test_partial_wiring_owner_only_does_not_emit`` (a pin of the OLD
        composite membership) before the owner-witnessed population change;
        it is flipped, never deleted, so the suite's record of the membership
        change is the flip itself.

        THE SHAPE IS THE LIVE ONE: real dispatch practice wires the work
        task's owner in a SPLIT write — owner set in one TaskUpdate, blockers
        at creation or in a separate update — which the composite shape
        (owner AND addBlockedBy in the same write) never matches. Under the
        composite, this exact write was the reason an arc that dispatched
        produced ZERO dispatch_site events with nothing suppressed. Under the
        owner-bearing leg it is the primary witness of a dispatch."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(blockers=()))
        s = sites(events)
        assert len(s) == 1, (
            "an owner-only write on a stamped specialist work task must "
            "witness a dispatch site"
        )
        assert s[0]["variety"]["total"] == 11

    def test_stamped_dispatch_emits_one_site_with_variety(self, env, events):
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring())
        s = sites(events)
        assert len(s) == 1
        assert s[0]["task_id"] == "42"
        assert set(s[0]["variety"]) == CANONICAL, "rationales must be dropped"
        assert s[0]["variety"]["total"] == 11

    def test_unstamped_dispatch_emits_a_site_with_NO_variety(self, env, events):
        """The coverage GAP. The event must still fire — it is the denominator
        — and `variety` must be ABSENT rather than empty or null, because
        absence is what the consumer reads as un-stamped."""
        seed(env, metadata={})
        tlg.evaluate_lifecycle(wiring())
        s = sites(events)
        assert len(s) == 1
        assert "variety" not in s[0]

    def test_incoming_stamp_in_the_same_write_is_not_missed(self, env, events):
        """If a lead stamps variety in the SAME update that wires the owner, a
        disk-only read would record an un-stamped site for a stamped dispatch
        — a false negative biasing coverage DOWN."""
        seed(env, metadata={})
        tlg.evaluate_lifecycle(wiring(metadata={"variety": STAMP}))
        s = sites(events)
        assert len(s) == 1 and s[0]["variety"]["total"] == 11

    def test_incoming_REPLACES_disk_rather_than_overlaying_it(self, env, events):
        """RE-POINTED, because the arm this replaces could not observe the
        model it was named for. It seeded a complete disk stamp, wrote
        `{"total": 16}` and asserted `total == 16` — which is true under the
        union AND under the replace, since both agree on a key the write
        names. Its truth value never depended on the question, so it reported
        green either way while its name asserted the retired vocabulary.

        The assertion is now on the WHOLE projected stamp, which is where the
        two models disagree: replace yields `{"total": 16}` alone, union
        yields it beside the four disk dimensions."""
        seed(env, metadata={"variety": {**STAMP, "total": 4}})
        tlg.evaluate_lifecycle(wiring(metadata={"variety": {"total": 16}}))
        assert sites(events)[0]["variety"] == {"total": 16}, (
            "the recorded sample kept disk dimensions the write dropped — "
            "a complete-looking stamp for a dispatch that post-write holds "
            "only a total"
        )

    def test_partial_restamp_records_the_POST_WRITE_stamp(
        self, env, events
    ):
        """THE SAMPLE MUST BE A STAMP THE TASK ACTUALLY ENDS UP WITH.

        `TaskUpdate` replaces `metadata.variety` wholesale, so a one-key
        re-stamp leaves the task holding ONLY that key. Recording the union
        instead would emit a complete-looking sample — old dimensions beside
        the new one, and a total inconsistent with them — for a dispatch that
        post-write resolves to nothing. The calibration delta is the metric
        this arc preserved; feeding it a value that never existed on disk is
        the failure mode that matters here, not a cosmetic divergence."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(metadata={"variety": {"risk": 1}}))
        v = sites(events)[0]["variety"]
        assert v == {"risk": 1}, (
            "the emit recorded a stamp the task does not hold post-write — "
            f"surviving disk keys mean this is still the union: {v!r}"
        )


def project(disk_variety, incoming_metadata=None):
    """Call `_dispatch_site_variety` DIRECTLY — no journal, no team config,
    no task store, no marker root. Just the (tool_input, task) pair it
    projects from, so an arm here survives a refactor of the emit caller.

    `disk_variety=None` seeds a task whose metadata carries no `variety`;
    `incoming_metadata=None` sends a write that names no metadata at all.
    """
    task = {"id": "42", "metadata": {}}
    if disk_variety is not None:
        task["metadata"]["variety"] = disk_variety
    tool_input = {"taskId": "42"}
    if incoming_metadata is not None:
        tool_input["metadata"] = incoming_metadata
    return tlg._dispatch_site_variety(tool_input, task)


class TestDispatchSiteVarietyProjectsThePostWriteStamp:
    """THE RECORDED SAMPLE MUST BE A STAMP THE TASK ACTUALLY ENDS UP WITH.

    Direct coverage of the projection, housed beside the end-to-end arms
    rather than replacing them: the E2E arms prove the emit REACHES this
    function with the right arguments, these prove the function ANSWERS
    correctly, and neither substitutes for the other.

    WHY THIS IS PINNED AT ALL, given the gate is the half that can refuse:
    a security review that measures `permissionDecision` sees nothing on
    this path, so its zero-delta here is silence rather than evidence. The
    failure it cannot see is a PHANTOM SAMPLE — old disk dimensions recorded
    beside a new one, with a total inconsistent with them, for a dispatch
    that post-write resolves to nothing. The calibration delta is the one
    metric this arc preserved, and a plausible wrong number reaching it is
    worse than an absent one, because absence is legible as the coverage gap
    and a phantom is not.

    THE THREE PARTIALNESS SHAPES ARE COVERED HERE TOO, and shape (a) is why
    the class exists: the emit's end-to-end rebuild pinned only the SUBSET
    shape, so a disjoint write — the one a containment reading excludes —
    was unpinned on this path as well as on the gate's.
    """

    def test_DISJOINT_partial_records_only_what_the_write_sent(self):
        """SHAPE (a). The write names `novelty`/`scope`, which the disk stamp
        LACKS, and drops the `total` it held. Post-write the task carries
        exactly what was sent.

        THE PHANTOM IS THE POINT: under the union this returns a `total` of
        11 sitting beside dimensions from a different write — a sample that
        existed on no version of this task, for a dispatch that post-write
        resolves to nothing. Discriminating: green here, red under the
        union."""
        got = project({"total": 11}, {"variety": {"novelty": 3, "scope": 2}})
        assert got == {"novelty": 3, "scope": 2}, (
            f"the projection kept a disk key the write dropped: {got!r}"
        )
        assert "total" not in got, (
            "a total the task does not hold post-write reached the "
            "calibration sample"
        )

    def test_SUBSET_partial_records_only_what_the_write_sent(self):
        """SHAPE (b). Every incoming key is already on disk, so only the
        DROPPED keys distinguish the models. Discriminating: green here, red
        under the union, which would return all five."""
        got = project(STAMP, {"variety": {"risk": 1}})
        assert got == {"risk": 1}, (
            f"surviving disk keys mean this is still the union: {got!r}"
        )

    def test_a_COMPLETE_stamp_in_the_write_survives_whole(self):
        """SHAPE (c) — the must-hold twin, and the non-vacuity control for
        the two arms above. A projection rebuilt to drop everything an
        incoming write names would satisfy both of those and be useless;
        only an arm that must still record a full sample separates them.

        Deliberately NOT discriminating against the union: with no disk
        stamp the two models agree, so a move here means the change
        over-reached rather than a model being told apart."""
        got = project(None, {"variety": STAMP})
        assert set(got) == CANONICAL and got["total"] == 11

    def test_disk_stands_when_the_write_names_no_variety(self):
        """A write that does not name `variety` leaves the disk stamp
        untouched — the ordinary wiring case, where the stamp landed at
        TaskCreate and this write only wires the owner."""
        assert project(STAMP, {"handoff": {"produced": ["f.py"]}})["total"] == 11
        assert project(STAMP)["total"] == 11

    def test_a_variety_DELETE_records_nothing(self):
        """`metadata={"variety": None}` is the platform's delete-the-key
        form. It NAMES `variety`, so post-write the stamp is absent and the
        caller renders no `variety` on the event — the un-stamped answer,
        which is the truthful one."""
        assert project(STAMP, {"variety": None}) == {}

    def test_rationale_strings_never_reach_the_sample(self):
        """The projection's own job, asserted separately from the write
        model so a regression in either is attributable. Rationales are
        calibration noise in a GC-immune journal."""
        got = project(STAMP)
        assert set(got) == CANONICAL
        assert not any(k.endswith("_rationale") for k in got)

    def test_a_non_canonical_key_is_dropped_even_though_it_RESOLVES(self):
        """THE PROJECTION AND THE GATE DELIBERATELY DIFFER HERE, and the
        difference is not a bug in either. `score` is a legal candidate for
        `resolve_variety_total`, so the enforcement gate reads a stamp of
        `{"score": 12}` as RESOLVED and stays silent — while this projection
        drops it and the event records no variety.

        The two answer different questions: the gate asks whether the
        dispatch was stamped at all, this asks for the canonical dimensions
        the calibration record is keyed on. Reds if someone 'aligns' them by
        widening the projection."""
        assert project({"score": 12}) == {}

    def test_no_stamp_on_either_side_is_an_EMPTY_dict(self):
        """Empty, not None: the caller renders an ABSENT `variety` key from
        this, which is what the consumer reads as un-stamped."""
        assert project(None) == {}
        assert project(None, {"variety": {}}) == {}


class TestEvaluationOrderNegations:
    """Every one of these fails silently in production. Each gets its own arm."""

    def test_non_canonical_frame_does_not_emit(self, env, events):
        """A tmux teammate frame (session_id != leadSessionId) writes a
        DIFFERENT journal; emitting there silos the event."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(
            wiring(agent_type=TEAMMATE, session_id="other-session")
        )
        assert sites(events) == []

    def test_class_3_lead_without_agent_type_STILL_emits(self, env, events):
        """THE ARM THAT MAKES THE FRAME-GATE CHOICE LOAD-BEARING.

        A lead launched without `--agent` carries NO agent_type, so is_lead is
        False — but its session_id IS the team's leadSessionId, so it writes
        the canonical journal and MUST emit. is_canonical_journal_frame
        survives this via its topology leg; is_lead does not.

        Without this arm the gate choice is untested: the tmux-teammate arm
        above is rejected by BOTH predicates, so swapping the gate to is_lead
        would leave the suite green while silently zeroing the entire
        population in exactly the frame the design named Class 3."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(agent_type=None, session_id="s1"))
        assert len(sites(events)) == 1, (
            "a no---agent lead writes the canonical journal and must emit"
        )

    def test_team_name_gate_holds_when_the_leg_that_subsumes_it_stops_blocking(
        self, env, events, monkeypatch
    ):
        """PINS A GUARD THAT IS OTHERWISE UNOBSERVABLE, by mutating what
        subsumes it.

        Step (2) — the team_name precondition — is strictly subsumed today by
        step (4)'s is_pact_specialist_owner, which also fails closed on an
        empty team. No ordinary input exists where step 2 rejects and step 4
        accepts, so simply deleting step 2 reddens nothing and a future reader
        sees a guard no test defends. Annotating that asks them to believe a
        comment; this makes the suite disagree with them instead.

        Forcing the SUBSUMING leg open isolates step 2 as the only thing left
        standing, so the arm reddens if step 2 is deleted AND it pins the
        subsumption assumption itself — if is_pact_specialist_owner ever stops
        failing closed, this keeps the guard honest rather than both going
        quiet together.

        The positive control matters: without it, an assertion of 'no emit'
        would pass even if the fixture could never emit at all."""
        seed(env, metadata={"variety": STAMP})
        monkeypatch.setattr(tlg, "is_pact_specialist_owner", lambda o, t: True)

        # POSITIVE CONTROL — subsuming leg forced open, team_name VALID:
        # the emit fires, proving this fixture can produce an event.
        tlg.evaluate_lifecycle(wiring())
        assert len(sites(events)) == 1, "control: fixture must be able to emit"

        # THE GUARDED CASE — team_name unresolvable. Only step (2) stands here.
        # Stub BOTH context surfaces the resolver seam reads: the persisted
        # name AND the aligned resolver (get_team_name memoizes per process, so
        # the control phase's aligned value would otherwise leak through the
        # aligned-first candidate and mask the flip this case exists to make).
        events.clear()
        monkeypatch.setattr(
            tlg.pact_context, "get_pact_context", lambda: {"team_name": ""}
        )
        monkeypatch.setattr(tlg.pact_context, "get_team_name", lambda: "")
        tlg.evaluate_lifecycle(wiring("43"))
        assert sites(events) == [], (
            "an unresolvable team_name must stop the emit even when the "
            "specialist leg no longer blocks it"
        )

    def test_partial_wiring_blockers_only_does_not_emit(self, env, events):
        """No owner named → no witness. This is the review-dispatch shape too:
        a write that never names owner cannot witness a dispatch site."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(owner=None))
        assert sites(events) == []

    def test_non_specialist_owner_does_not_emit(self, env, events):
        seed(env, owner="explorer", metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(owner="explorer"))
        assert sites(events) == []

    def test_owner_absent_from_team_config_does_not_emit(self, env, events):
        seed(env, owner="ghost", metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(owner="ghost"))
        assert sites(events) == []

    def test_teachback_task_a_does_not_emit(self, env, events):
        seed(env, subject="backend-coder: TEACHBACK for the thing",
             metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring())
        assert sites(events) == []

    def test_teachback_exempt_owner_does_not_emit(self, env, events):
        """The secretary is teachback-exempt: its task-system work is rote and
        skill-defined, so it is not a variety-eligible dispatch. Keying on
        is_teachback_exempt (NOT is_self_complete_exempt) is what makes this
        arm correct — the two answer different questions."""
        seed(env, owner="secretary", subject="secretary: harvest",
             metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(owner="secretary"))
        assert sites(events) == []


class TestDedup:
    def test_two_owner_bearing_writes_yield_ONE_site(self, env, events):
        """Without the O_EXCL claim a repeat wiring write doubles the
        denominator: a correctly-stamped dispatch reads coverage 0.5."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring())
        tlg.evaluate_lifecycle(wiring())
        assert len(sites(events)) == 1

    def test_same_task_DIFFERENT_owner_emits_ONE_site(self, env, events):
        """OWNER-INDEPENDENCE. Re-wiring the SAME Task-B to a DIFFERENT owner
        is the SAME dispatch site, so it must emit ONCE.

        WHY THE OPPOSITE OF THE agent_handoff SIBLING, and this is the half
        that keeps either from reading as a mistake. Both families share the
        same ``already_emitted`` primitive and differ ONLY in the third key
        argument, so the two policies sit one argument apart:

          * ``agent_handoff`` records an OCCURRENCE — who handed off what. A
            different occupant is a genuinely different handoff, so
            re-emission on a changed occupant is WANTED. It is occupant-keyed,
            and `test_handoff_b1_b2_dedup.py` pins that a different owner
            emits TWICE.
          * ``dispatch_site`` records a POSITION — one Task-B is one site, and
            the site IS the coverage denominator. A re-wired Task-B is the
            same position, so a second event FABRICATES a denominator entry.

        One counts occurrences, the other counts positions. Neither is a
        mistake, and **aligning them "for consistency" is the defect** — it
        does not take carelessness, only a maintainer who reads the pinned
        sibling and tidies this family to match.

        THE COST OF GETTING IT WRONG IS SILENT AND IN THE HARMFUL DIRECTION:
        the fabricated second site carries no variety, so a correctly-stamped
        dispatch reports 1 of 2 = 0.500 where the truth is 1 of 1 = 1.000 —
        a compliance gap invented out of a re-wire. Measured: an
        owner-dependent key applied to the claim and its unclaim twin passes
        the entire suite without this pin.

        Paired with the eligibility control directly below — read them
        together, because this assertion alone cannot tell you WHY there is
        one event."""
        seed(env, "42", metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring("42", owner="backend-coder"))
        tlg.evaluate_lifecycle(wiring("42", owner="auditor"))
        s = sites(events)
        assert len(s) == 1, (
            "a re-wired Task-B is the SAME dispatch site; a second event "
            "fabricates a denominator entry and drives coverage down"
        )
        assert "variety" in s[0], (
            "the surviving site must be the STAMPED first write — suppressing "
            "the first and keeping the un-stamped second would preserve the "
            "count while inverting the numerator"
        )

    def test_control_the_second_owner_IS_emit_eligible(self, env, events):
        """ELIGIBILITY CONTROL for the pin above. Do not delete.

        `len(sites) == 1` has a SECOND, CHEAPER SOURCE than dedup: it is also
        what you get if the second write was never eligible to emit at all —
        a non-specialist owner, a teachback-exempt owner, a teachback-shaped
        subject, a non-canonical frame, an unresolvable team. Every one of
        those yields one event for a reason that has nothing to do with the
        marker key, and an assertion whose truth has a cheaper source than the
        mechanism it names is decorative however true it reads.

        Worse, that decorative version would pass BOTH directions of the usual
        check — green at HEAD and red under an owner-dependent mutation —
        because the mutation changes the key and an ineligible write stays
        ineligible either way.

        This arm removes the ambiguity: the SAME owner-B wiring write, against
        a DIFFERENT task_id, emits. So owner-B is demonstrably eligible, and
        the single event above is dedup collapsing it — not the gate refusing
        it."""
        seed(env, "42", metadata={"variety": STAMP})
        seed(env, "43", metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring("42", owner="backend-coder"))
        tlg.evaluate_lifecycle(wiring("43", owner="auditor"))
        assert len(sites(events)) == 2, (
            "owner 'auditor' must be emit-eligible in this fixture, or "
            "the one-event assertion above proves nothing about dedup"
        )

    def test_distinct_tasks_are_not_deduped_against_each_other(self, env, events):
        seed(env, task_id="42", metadata={"variety": STAMP})
        seed(env, task_id="43", metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring("42"))
        tlg.evaluate_lifecycle(wiring("43"))
        assert {e["task_id"] for e in sites(events)} == {"42", "43"}

    def test_marker_key_constant_must_be_non_empty(self, env):
        """NON-VACUITY ON THE THIRD KEY COMPONENT — the pin that catches a
        future 'simplification' dropping the constant.

        already_emitted's degenerate guard FAIL-OPENS on an empty occupant: no
        marker is written, nothing raises, and dedup silently never fires while
        looking like it works. Every other dedup test would still pass. This
        arm asserts (a) the hazard is real, so the constant is load-bearing
        rather than decorative, and (b) the constant this module actually binds
        is not falsy.

        Asserted against the marker primitive directly, because the wrapper
        hard-binds the constant and therefore cannot express the broken case."""
        from shared.agent_handoff_marker import already_emitted

        # (a) the hazard: an empty occupant never suppresses.
        empty = [
            already_emitted(TEAM, "9001", "", namespace=".probe_ns")
            for _ in range(2)
        ]
        assert empty == [False, False], (
            "an empty occupant must fail open — if this ever starts deduping, "
            "the constant below is no longer load-bearing and this test should "
            "be re-derived rather than deleted"
        )

        # (b) what the module binds is not falsy, and does suppress.
        assert tlg._DISPATCH_SITE_MARKER_KEY, "marker key must be non-empty"
        real = [
            already_emitted(TEAM, "9002", tlg._DISPATCH_SITE_MARKER_KEY,
                            namespace=tlg.DISPATCH_SITE_MARKER_NAMESPACE)
            for _ in range(2)
        ]
        assert real == [False, True], "the bound constant must claim then dedup"

    def test_failed_write_rolls_the_claim_back(self, env, monkeypatch):
        """A claim whose write then failed must be released, or the site is
        suppressed forever with no journal entry to show for it."""
        seed(env, metadata={"variety": STAMP})
        monkeypatch.setattr(sj, "append_event", lambda e: False)
        tlg.evaluate_lifecycle(wiring())

        captured: list[dict] = []
        monkeypatch.setattr(sj, "append_event", lambda e: captured.append(e) or True)
        tlg.evaluate_lifecycle(wiring())
        assert len(sites(captured)) == 1, "rollback did not release the marker"


class TestAntiWidening:
    def test_signal_shaped_dispatch_STILL_emits(self, env, events):
        """TRIPWIRE. Signal-shaped dispatches are stamped 0-of-6 — a clean
        categorical zero that looks symmetric with the secretary's 0-of-51 and
        invites 'exempt them too'. Whether completion_type is a variety
        boundary is an UNRULED PROTOCOL QUESTION, and the current ruling is the
        opposite: auditor observation dispatches are genuine coverage gaps
        whose remedy is stamping, not exempting. This reddens if anyone adds a
        completion_type / metadata.type exemption to the emit."""
        seed(env, owner="auditor", subject="auditor: observe wave 2",
             metadata={"completion_type": "signal", "type": "blocker"})
        tlg.evaluate_lifecycle(wiring(owner="auditor"))
        s = sites(events)
        assert len(s) == 1, "a signal-shaped dispatch must COUNT as a site"
        assert "variety" not in s[0], "and it counts as an un-stamped one"


class TestLiveDispatchShapes:
    """Regression: the live dispatch shapes that left the stream DARK on an
    arc that dispatched (issue #1531 — 5 dispatch_variety, 0 dispatch_site,
    0 journal_emit_skipped; nothing was suppressed, the ladder simply never
    matched). Three shapes adjudicated from that arc's journal and task
    snapshots; the owner-only WORK-task arm is the flipped pin in
    TestEmitsAtTheOwnerWitnessWrite above."""

    def test_split_wiring_teachback_gate_does_not_emit(self, env, events):
        """A teachback Task-A gate receives the SAME owner-only split wiring
        the work task does — the subject carve-out is the SOLE discriminator.
        This arm pins that discriminator against the gate shape."""
        seed(env, subject="backend-coder: TEACHBACK for the thing",
             metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(blockers=()))
        assert sites(events) == []

    def test_gate_without_the_subject_marker_is_counted(self, env, events):
        """THE NAMED DEPENDENCE, MADE EXECUTABLE. Membership depends on the
        TEACHBACK subject convention: a Task-A gate whose subject does not
        carry the marker is indistinguishable from a work task at the
        owner-witnessing write and IS counted as a site. This arm documents
        the dependence by asserting it — if this ever reddens, someone added a
        beyond-subject discriminator and this docstring (plus the membership
        prose) must be updated, not just the assertion."""
        seed(env, subject="backend-coder: verify understanding for the thing",
             metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(blockers=()))
        s = sites(events)
        assert len(s) == 1, (
            "under subject-only discrimination a marker-less gate subject "
            "must be counted — the convention dependence is real"
        )

    def test_review_dispatch_shape_never_owner_wired_no_site(self, env, events):
        """Review-panel dispatches are EXCLUDED BY DOCUMENTED SEMANTICS: the
        peer-review path dispatches reviewers without ever writing owner on
        the task, so no owner write witnesses them. Whatever else lands on
        such a task — status flips, blocker writes — witnesses nothing. Their
        dispatches stay visible through the review_dispatch stream; wanting
        them in this population is remedied by canonical owner wiring at
        dispatch (practice-side), never by inferring ownership here."""
        seed(env, owner="", subject="review-coder: review PR #99 on the thing",
             metadata={"variety": STAMP})
        # The writes a never-owner-wired task receives: blockers-only and
        # bare status updates. Neither names an owner.
        tlg.evaluate_lifecycle(wiring(owner=None, blockers=("5",)))
        tlg.evaluate_lifecycle({"tool_name": "TaskUpdate",
                                "tool_input": {"taskId": "42",
                                               "status": "in_progress"},
                                "agent_type": LEAD})
        assert sites(events) == []


class TestRealJournalSeam:
    """NON-MOCKED SEAM INTEGRATION — the real session-journal write path.

    Every other class in this file spies ``sj.append_event`` to CAPTURE
    events; that mock is exactly the seam a regression hides behind, because
    an emit that never reaches a real journal still "fires" against the spy.
    These arms use the ``env`` fixture ONLY (real team config, real task
    store, real O_EXCL marker root, real context) and leave the journal write
    entirely real, then read the journal FILE bytes back from disk. A seam
    regression — the emit resolving the wrong session dir, or the write
    failing on the real path — turns these red while the spied arms stay
    green. Do NOT "tidy" these to use the events fixture.
    """

    def _journal_lines(self, env):
        path = sj.get_journal_path()
        p = Path(path)
        assert p.is_file(), f"no journal file at {path} — the write never landed"
        return [json.loads(line) for line in
                p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_live_shape_owner_wiring_lands_in_the_real_journal(self, env):
        """The acceptance shape: a live-shape owner-wiring dispatch (owner
        set in a split write) must produce a dispatch_site event IN THE REAL
        JOURNAL FILE, not merely in a spied capture list."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(blockers=()))
        site_events = [e for e in self._journal_lines(env)
                       if e.get("type") == "dispatch_site"]
        assert len(site_events) == 1
        assert site_events[0]["task_id"] == "42"
        assert site_events[0]["variety"]["total"] == 11

    def test_repeat_write_yields_ONE_site_in_the_real_journal(self, env):
        """Dedup through the REAL O_EXCL marker root and a REAL journal file:
        a second owner-bearing write appends nothing new."""
        seed(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(wiring(blockers=()))
        tlg.evaluate_lifecycle(wiring(blockers=()))
        site_events = [e for e in self._journal_lines(env)
                       if e.get("type") == "dispatch_site"]
        assert len(site_events) == 1
