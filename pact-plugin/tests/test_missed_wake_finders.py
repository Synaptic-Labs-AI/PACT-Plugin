"""Behavioural pins for the lead-side finders and their ENTRY POINT.

Location: pact-plugin/tests/test_missed_wake_finders.py
Summary: pins `find_mutual_waits`, `find_unanchored_waits`,
         `build_unanchored_surface`, and that every finder in FINDERS is
         reached from `run_surface` — the registered entry point — rather
         than only from a test calling them directly.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

🔴 WHY THE ENTRY-POINT ARMS EXIST, and it is the reason this file is not just
finder arms. Five non-vacuity arms were run against `find_unanchored_waits`
and all five called it DIRECTLY. **Every one would have passed with the caller
broken** — and the caller really was broken at one point, by a use-before-
assignment inside a bare `except Exception: pass`, which would have made the
alarm silently never fire while every finder arm stayed green. Testing a finder
is not testing that anything calls it.

STRUCTURAL REACHABILITY IS NOT ENOUGH ON ITS OWN AND IS PAIRED HERE
DELIBERATELY. An AST reachability check was measured earlier to PASS against
code that raises: the call is textually present, so the walk finds it, while
execution never arrives. Structure catches *nothing calls it*; behaviour
catches *the call is never reached*. Neither subsumes the other, so both are
below.

PROOF STANDARD FOR THIS FILE: revert-to-pre-fix mutation, never a total break.
Measured on a sibling surface — destroying a function killed nine pre-existing
arms while reverting it to the previous behaviour killed zero of 124.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import missed_wake_scan as mw
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified

SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "missed_wake_scan.py"
ENTRY_POINT = "run_surface"
FINDERS = ("find_stale_missed_wakes", "find_mutual_waits", "find_unanchored_waits")

AGED_MIN = 90     # past the 30-minute staleness threshold
FRESH_MIN = 2     # well inside it


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _task(task_id, owner, resolver="peer", since=AGED_MIN, anchor=None,
          status="in_progress") -> dict:
    wait = {"reason": "awaiting_blocker_resolution",
            "expected_resolver": resolver, "since": _ago(since)}
    if anchor is not None:
        wait["covers_since"] = _ago(anchor)
    return {"id": task_id, "owner": owner, "subject": "s", "status": status,
            "metadata": {"intentional_wait": wait}}


class TestMutualWaitsNeedsTwoDistinctPeersAndAge:
    """Two or more agents each waiting on a peer, none of them working.

    WHAT IT DETECTS IS A CANDIDATE, NOT A PROVEN CYCLE. `expected_resolver`
    records the KIND of resolver and never which one, so the task store cannot
    say X waits on Y and Y waits on X. Two agents independently waiting on a
    third looks identical from outside. Both are worth a look; only one is a
    deadlock, and the surface must not claim otherwise.
    """

    def test_two_distinct_peers_both_aged_are_DETECTED(self):
        assert mw.find_mutual_waits(
            [_task("1", "alice"), _task("2", "bob")]) != []

    def test_a_RESTAMPED_PAIR_is_still_detected(self):
        """🔴 THE CASE THIS ARC ACTUALLY HIT, and the reason the age gate reads
        the ANCHOR rather than `since`.

        Both waits were re-stamped two minutes ago and both anchors are ninety
        minutes old. Under a `since`-based gate this pair is ZERO stale and is
        never detected — and re-stamping is BOTH what makes a mutual wait
        dangerous and what makes it look fresh. A `since` gate therefore shows
        exactly the population that is fine and hides the one that is not.
        """
        pair = [_task("1", "alice", since=FRESH_MIN, anchor=AGED_MIN),
                _task("2", "bob", since=FRESH_MIN, anchor=AGED_MIN)]
        assert mw.find_mutual_waits(pair) != [], (
            "a re-stamped pair must still age via its ANCHOR; gating on "
            "`since` inverts the signal and hides every dangerous case"
        )

    def test_ONE_owner_holding_two_waits_is_not_a_cycle(self):
        """Needs two DISTINCT owners. One agent with two waits is idle, not
        deadlocked, and surfacing it would train the alarm to be ignored."""
        assert mw.find_mutual_waits(
            [_task("1", "alice"), _task("2", "alice")]) == []

    def test_a_peer_waiting_alongside_a_LEAD_waiter_is_not_a_cycle(self):
        assert mw.find_mutual_waits(
            [_task("1", "alice"), _task("2", "bob", resolver="lead")]) == []

    def test_two_peers_both_FRESH_are_not_detected(self):
        """The age gate, from the other side — without this the aged arm could
        pass on an implementation that detects any two peers at all."""
        assert mw.find_mutual_waits(
            [_task("1", "alice", since=FRESH_MIN),
             _task("2", "bob", since=FRESH_MIN)]) == []

    def test_a_NON_STRING_owner_is_not_counted_and_does_not_raise(self):
        """An owner that is not a string is not an agent. It must not count
        toward the two distinct owners, and it must not raise: this finder runs
        outside any try in `run_surface`, where one raise drops every surface."""
        odd = _task("1", "alice")
        odd["owner"] = ["x"]
        try:
            found = mw.find_mutual_waits([odd, _task("2", "carol")])
        except Exception as exc:
            pytest.fail("a non-string owner made find_mutual_waits raise %r" % (exc,))
        assert found == [], (
            "only one real owner is waiting, so this is not a mutual wait: %r" % (found,)
        )

    def test_CHARACTERIZATION_a_user_resolver_cycle_is_INVISIBLE(self):
        """🔴 THIS ARM PINS A KNOWN LIMITATION, NOT A REQUIREMENT.

        `user` and `external` are not task-store entities, so a cycle running
        through the human cannot be REPRESENTED here — it is not that such
        cycles are rare, it is that this instrument has no way to see them. An
        empty result must never be read as "no deadlock".

        IF YOU HAVE TAUGHT THE DETECTOR TO SEE USER CYCLES, DELETE THIS ARM.
        Its reddening is the fix landing, not a regression you caused. It is
        pinned rather than left in prose because a documented blind spot with
        no arm is what this codebase has repeatedly watched evaporate — the
        `covers_since` contract lives in a skill file and nothing enforces it.
        """
        both_user = [_task("1", "alice", resolver="user"),
                     _task("2", "bob", resolver="user")]
        assert mw.find_mutual_waits(both_user) == [], (
            "KNOWN LIMITATION, pinned deliberately: `expected_resolver` records "
            "the KIND of resolver, so user/external cycles cannot be "
            "represented. If this now returns a detection, the instrument has "
            "been extended — DELETE THIS ARM rather than restoring the old "
            "behaviour. Its failure is the improvement landing."
        )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A real team-scoped registry on disk, returning a seeder.

    Not a mock of the loader: the finder reaches the registry through the same
    team-path resolution production uses, so a seam regression reddens here.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    team = "finder-team"
    (tmp_path / "teams" / team).mkdir(parents=True)
    path = tmp_path / "teams" / team / "background_work.json"

    def seed(launched_minutes_ago):
        if launched_minutes_ago is None:
            path.write_text(json.dumps({"records": []}))
            return team
        path.write_text(json.dumps({"records": [{
            "agent_name": "alice", "session_id": "s", "task_ids": ["7"],
            "registered_at": _ago(launched_minutes_ago)}]}))
        return team

    return seed


class TestUnanchoredWaitsGateOnACoveredRecord:
    """The surface fires only where the missing anchor has actually DECIDED
    something — i.e. where a record exists that the fallback covers.

    An unanchored wait still works; what is missing is the field pinning
    WHICH launches it covers. Agents write that field on every SET, so its
    absence means the wait predates the field, came from an older instruction
    or a template that omits it, or lost the field on a re-SET. With no covered
    record there is nothing for the absence to have affected and nothing to
    tell the lead.
    """

    def test_an_unanchored_wait_covering_a_record_FIRES(self, registry):
        team = registry(100)
        assert mw.find_unanchored_waits([_task("7", "alice")], team) != []

    def test_an_ANCHORED_wait_is_silent(self, registry):
        team = registry(100)
        assert mw.find_unanchored_waits(
            [_task("7", "alice", anchor=AGED_MIN)], team) == []

    def test_an_unanchored_wait_covering_NOTHING_is_silent(self, registry):
        """The gate is a COVERED record, not mere absence of an anchor. The
        launch here is more recent than the wait, so the fallback does not
        reach it and the absence has decided nothing."""
        team = registry(10)
        assert mw.find_unanchored_waits([_task("7", "alice")], team) == []

    def test_a_FRESH_TEAM_with_no_records_is_silent(self, registry):
        """Silence here is the GENERAL RULE with an empty record set, not a
        special case — and an integration arm depends on it.

        🔴 ITS SILENCE IS OVER-DETERMINED, AND SAYING SO IS THE POINT.
        MEASURED: two independent mechanisms produce it — the `if not records`
        early return, and the covered-record gate downstream (with no records,
        nothing is covered). Reverting EITHER alone leaves this arm GREEN;
        only the COMBINED revert reddens it. So this arm does not pin either
        mechanism on its own and must not be read as doing so — the sibling
        arm above is what pins the covered-record gate specifically.

        What it does pin is the OUTCOME a fresh team must see, which is worth
        having because an integration arm depends on that silence and because
        both mechanisms would have to go for it to break. An earlier draft of
        this docstring claimed it reddens when absence-of-anchor becomes
        sufficient; measured, it does not, and the claim is corrected rather
        than removed so the next reader knows the difference was checked.
        """
        team = registry(None)
        assert mw.find_unanchored_waits([_task("7", "alice")], team) == []

    def test_the_surface_renders_and_names_the_FALLBACK_ANCHOR(self, registry):
        team = registry(100)
        rows = mw.find_unanchored_waits([_task("7", "alice")], team)
        surface = mw.build_unanchored_surface(rows)
        assert surface is not None and "FALLBACK ANCHOR" in surface

    def test_no_rows_render_NOTHING(self, registry):
        """Paired with the arm above so neither passes on a builder that always
        returns a string."""
        assert mw.build_unanchored_surface([]) is None


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    """Module-scoped, and a plain function rather than a class instance method:
    a class-scoped fixture defined as a method is deprecated and would add a
    third PytestRemovedIn10Warning to the suite's standing two."""
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


class TestBothFindersAreReachedFromTheRegisteredEntryPoint:
    """STRUCTURE AND BEHAVIOUR, PAIRED — neither is sufficient alone."""

    @pytest.mark.parametrize("finder", FINDERS)
    def test_STRUCTURAL_each_finder_is_called_from_run_surface(self, tree, finder):
        """Catches a finder being ORPHANED — no caller at all.

        Does NOT catch the call being unreachable at runtime: an AST walk finds
        a call that a raise above it never arrives at. The behavioural arm
        below is what covers that, and removing either leaves a real hole.
        """
        entry = next(n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == ENTRY_POINT)
        called = {n.func.id for n in ast.walk(entry)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert finder in called, (
            f"{finder} is no longer called from {ENTRY_POINT}; a finder nothing "
            "reaches is an alarm that cannot fire, however green its own arms are"
        )

    def test_BEHAVIOURAL_run_surface_actually_produces_the_unanchored_text(
        self, registry, monkeypatch
    ):
        """The arm that a raise inside `run_surface` would redden and the
        structural one would not.

        Drives the registered entry point with a lead frame and asserts the
        composed output, so a use-before-assignment swallowed by the bare
        `except` — which really happened — cannot present as correct silence.
        """
        team = registry(100)
        task = _task("7", "alice")
        monkeypatch.setattr(mw, "get_task_list", lambda: [task])
        monkeypatch.setattr(mw, "read_events", lambda et: [])
        monkeypatch.setattr(mw, "append_event", lambda e: True)
        monkeypatch.setattr(mw, "get_journal_path", lambda: "/tmp/fake.jsonl")
        from shared import pact_context
        monkeypatch.setattr(pact_context, "get_team_name", lambda: team)

        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None and "FALLBACK ANCHOR" in out, (
            "the unanchored alarm did not reach the lead from the registered "
            "entry point. A finder arm cannot see this: the finder works and "
            "nothing calls it, or the call is never reached."
        )


class TestTheMutualWaitSurfaceReachesTheLead:
    """The mutual-wait alarm, driven through the registered entry point.

    The structural arm sees the finder CALLED. It cannot see the finder's
    result dropped before it reaches the output, and it cannot see a raise
    that stops the call being reached. Only the composed text shows either.
    """

    @pytest.fixture
    def surface(self, tmp_path, monkeypatch):
        """Run `run_surface` as a lead on a given task list.

        No team name, so the registry-backed alarms stay out of the output and
        nothing reads a config root outside tmp_path. The journal is stubbed so
        no alarm can write anywhere.
        """
        from shared import pact_context

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(pact_context, "get_team_name", lambda: None)
        monkeypatch.setattr(mw, "read_events", lambda et: [])
        monkeypatch.setattr(mw, "append_event", lambda e: True)
        monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))

        def run(tasks):
            monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
            return mw.run_surface(captured_lead_userpromptsubmit_qualified()) or ""

        return run

    def test_an_aged_peer_pair_reaches_the_lead_as_a_MUTUAL_WAIT(self, surface):
        out = surface([_task("1", "alice"), _task("2", "bob")])
        assert "POSSIBLE MUTUAL WAIT" in out, (
            "two distinct agents each idling on a peer past the threshold did "
            "not reach the lead. A finder can be called and its result still "
            "dropped, and only the output shows that: %r" % (out,)
        )
        assert "- Task #1 (alice" in out and "- Task #2 (bob" in out, out

    def test_a_FRESH_peer_pair_does_not_reach_the_lead(self, surface):
        """Paired with the arm above, so neither passes on an entry point that
        prints the header for any two peers. Both anchors are fresh: a fresh
        `since` over an old `covers_since` still reads stale, by design."""
        out = surface([_task("1", "alice", since=FRESH_MIN, anchor=FRESH_MIN),
                       _task("2", "bob", since=FRESH_MIN, anchor=FRESH_MIN)])
        assert "POSSIBLE MUTUAL WAIT" not in out, out

    def test_a_task_with_NON_DICT_metadata_does_not_blank_the_surface(self, surface):
        """The finder runs outside any try in `run_surface`, so a malformed task
        that raises there drops every lead surface, not only this one."""
        malformed = {"id": "3", "status": "in_progress", "owner": "carol",
                     "metadata": ["not", "a", "dict"]}
        try:
            out = surface([_task("1", "alice"), _task("2", "bob"), malformed])
        except Exception as exc:
            pytest.fail("a task whose metadata is not a dict made run_surface "
                        "raise %r, which drops every lead surface" % (exc,))
        assert "POSSIBLE MUTUAL WAIT" in out, (
            "one task whose metadata is not a dict blanked the mutual-wait "
            "surface, so a single malformed task hid the lead's alarm for a "
            "real aged peer pair: %r" % (out,)
        )
