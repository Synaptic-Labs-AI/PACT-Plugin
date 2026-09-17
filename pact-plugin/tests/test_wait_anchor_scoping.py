"""Behavioural pins for the wait SCOPING ANCHOR — `covers_since`.

Location: pact-plugin/tests/test_wait_anchor_scoping.py
Summary: pins `wait_scope_anchor`, `wait_anchor_class` and the anchor path
         through `wait_covers_record`. Three surfaces that shipped with ZERO
         test references anywhere under tests/.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

WHY A NEW FILE. `test_background_work.py` reads as though it covers
`wait_covers_record` — the symbol appears there twice. MEASURED: `covers_since`
appears ZERO times in that file, so every existing arm drives that function
through a wait with NO anchor and exercises only the fallback. **The anchor
comparison, which is the entire fix, was untested while its function read as
covered.** A census count above zero is not coverage of a path; it sent a
reader past this surface rather than to it.

THE DEFECT BEING PINNED. `since` is the freshness clock and agents are
INSTRUCTED to re-SET it so a long wait does not read as stale. Scoping coverage
on `since` therefore let every re-stamp widen a wait FORWARD over launches it
had never acknowledged — a rolling amnesty that annulled the
`anchor >= registered_at` comparison. `covers_since` is a separate field that
every SET carries forward unchanged, so the clock moves and the scope does not.

NON-COVERAGE, STATED. These arms pin the PREDICATE. They do not pin that any
agent actually writes `covers_since` — that contract lives in
`skills/pact-agent-teams/SKILL.md` and nothing enforces it, which is a real gap
and is not this file's subject. They also do not pin the lead-side surfacing of
a missing anchor; that is `find_unanchored_waits`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.background_work import (
    ANCHOR_CLASS_ABSENT,
    ANCHOR_CLASS_MALFORMED,
    WAIT_ANCHOR_KEY,
    wait_anchor_class,
    wait_covers_record,
    wait_scope_anchor,
)

# Three ordered instants. The whole fix is a comparison between two of them, so
# they are named rather than offset inline: ANCHOR_AT < LAUNCH_AT < RESTAMP_AT.
ANCHOR_AT = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
LAUNCH_AT = ANCHOR_AT + timedelta(minutes=10)
RESTAMP_AT = ANCHOR_AT + timedelta(minutes=20)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _task(**wait_fields) -> dict:
    """A task carrying a VALID wait plus whatever anchor fields are given.

    `reason` and `expected_resolver` are present because `validate_wait` gates
    first — an invalid wait returns False from coverage for a different reason
    and would make every arm here pass vacuously.
    """
    wait = {"reason": "awaiting_blocker_resolution", "expected_resolver": "peer"}
    wait.update(wait_fields)
    return {"id": "7", "status": "in_progress", "owner": "alice",
            "metadata": {"intentional_wait": wait}}


def _record(registered: datetime) -> dict:
    return {"agent_name": "alice", "session_id": "s", "task_ids": ["7"],
            "registered_at": _iso(registered)}


class TestTheAnchorScopesCoverageAndTheClockDoesNot:
    """The defect, its control, and the boundary.

    NON-VACUITY: arm 1 is the defect itself. On the pre-fix implementation —
    which compared against `since` — this exact input returned covered=True,
    measured by the implementing agent as its control at the time. That is what
    makes this arm a pin rather than a restatement.
    """

    def test_a_RESTAMPED_wait_does_NOT_cover_a_launch_made_after_its_anchor(self):
        """🔴 THE DEFECT. Anchor at ANCHOR_AT, clock re-stamped to RESTAMP_AT,
        launch at LAUNCH_AT in between.

        Pre-fix this returned True, because the comparison read the re-stamped
        clock. The launch was never acknowledged by the wait that now appears
        to cover it — a rolling amnesty granted by following an instruction the
        framework itself gives (re-SET `since` so a long wait is not stale).
        """
        task = _task(covers_since=_iso(ANCHOR_AT), since=_iso(RESTAMP_AT))
        assert wait_covers_record(task, _record(LAUNCH_AT)) is False, (
            "a re-stamped clock must not widen coverage: the anchor is "
            "ANCHOR_AT and the launch is LAUNCH_AT, which is later, so this "
            "wait never acknowledged it"
        )

    def test_an_anchor_AFTER_the_launch_DOES_cover_it(self):
        """The positive control. Without it the arm above could pass on an
        implementation that never covers anything."""
        task = _task(covers_since=_iso(RESTAMP_AT), since=_iso(RESTAMP_AT))
        assert wait_covers_record(task, _record(LAUNCH_AT)) is True

    def test_the_comparison_is_inclusive_at_the_boundary(self):
        """`anchor >= registered_at`, so an anchor set at the launch instant
        covers it. Distinguishes `>=` from `>`, which no arm above can."""
        task = _task(covers_since=_iso(LAUNCH_AT), since=_iso(RESTAMP_AT))
        assert wait_covers_record(task, _record(LAUNCH_AT)) is True


class TestAnAbsentAnchorSTILLCoversDeliberately:
    """🔴 THE HIGHEST-VALUE PIN IN THIS FILE, and it is not the defect arm.

    A wait with no `covers_since` falls back to `since` and behaves EXACTLY as
    it did before the field existed. That is deliberate and it is the behaviour
    a future reader is most likely to "fix" into failing closed, because an
    absent field feeding a security-shaped comparison looks like an oversight.

    WHY FAILING CLOSED WOULD BE WRONG. The anchor is agent-written. Every wait
    raised before the field shipped has none, and so does every wait written
    under the earlier instruction or from a template that omits it. Refusing to
    cover an unanchored wait would make the discharge mechanism stop retiring
    those records at once, silently, in the safe-looking direction.

    AND AN UNANCHORED WAIT THAT WAS NEVER RE-STAMPED IS HARMLESS BY
    CONSTRUCTION: its `since` still equals its true anchor, so the fallback
    gives the right answer. The harm begins at the re-stamp, which is why the
    lead-side surface reports the missing anchor rather than this returning a
    quiet verdict on it.
    """

    def test_an_unanchored_wait_covers_an_earlier_launch(self):
        """The fallback, asserted. Changing this to fail closed reddens here."""
        task = _task(since=_iso(RESTAMP_AT))
        assert WAIT_ANCHOR_KEY not in task["metadata"]["intentional_wait"]
        assert wait_covers_record(task, _record(LAUNCH_AT)) is True, (
            "THE FALLBACK IS DELIBERATE. An unanchored wait must behave as it "
            "did before `covers_since` existed. If you have made absence fail "
            "closed, you have stopped the discharge retiring records for every "
            "wait raised before this field shipped — which at transition is all "
            "of them — silently and in the safe-looking direction. The missing "
            "anchor is SURFACED by find_unanchored_waits; it is not resolved here."
        )

    def test_the_fallback_still_APPLIES_the_comparison(self):
        """Absence is not a blanket amnesty.

        Distinguishes "falls back to `since`" from "covers everything when
        unanchored" — two implementations the arm above cannot separate.
        """
        task = _task(since=_iso(ANCHOR_AT))
        assert wait_covers_record(task, _record(LAUNCH_AT)) is False

    def test_a_never_restamped_unanchored_wait_covers_its_own_launch_instant(self):
        """The never-re-stamped case: `since` equals the true anchor, so the
        fallback is exactly right and the wait is harmless by construction."""
        task = _task(since=_iso(LAUNCH_AT))
        assert wait_covers_record(task, _record(LAUNCH_AT)) is True


class TestAbsentAndMalformedStayTwoDistinctClasses:
    """Three states, not two — the recurring defect of this arc is collapsing
    `absent` and `uncheckable` into one null.

    They have different causes and different remedies. ABSENT means the wait
    predates the field, was written under the earlier instruction or from a
    template that omits it, or lost the field on a re-SET. MALFORMED means an
    agent wrote something unparseable and has a bug worth naming. Both fall back
    to `since` and both are surfaced, so neither is collapsed into a pass or a
    fail — but a reader told only "no anchor" cannot tell which they have.

    NON-COVERAGE: these pin the CLASS the predicate reports. They do not pin
    what the lead-facing surface says about each class.
    """

    def test_a_missing_anchor_reports_ABSENT(self):
        assert wait_anchor_class(_task(since=_iso(RESTAMP_AT))) == ANCHOR_CLASS_ABSENT

    def test_an_UNPARSEABLE_anchor_reports_MALFORMED_not_absent(self):
        task = _task(covers_since="not-a-timestamp", since=_iso(RESTAMP_AT))
        assert wait_anchor_class(task) == ANCHOR_CLASS_MALFORMED, (
            "an unparseable anchor is an agent BUG and must not be reported as "
            "a missing field; collapsing the two hides the bug behind the "
            "legitimate transitional case"
        )

    def test_the_two_classes_are_not_the_same_value(self):
        """Guards the collapse directly: if someone sets both constants to one
        value the arms above still pass individually."""
        assert ANCHOR_CLASS_ABSENT != ANCHOR_CLASS_MALFORMED

    def test_a_VALID_anchor_reports_NO_class(self):
        """None means "nothing to report", and it is the third state."""
        task = _task(covers_since=_iso(ANCHOR_AT), since=_iso(RESTAMP_AT))
        assert wait_anchor_class(task) is None

    def test_a_malformed_anchor_still_FALLS_BACK_rather_than_refusing(self):
        """The class is reported AND coverage still works — a bug in the field
        must not break the mechanism that field scopes."""
        task = _task(covers_since="not-a-timestamp", since=_iso(RESTAMP_AT))
        assert wait_covers_record(task, _record(LAUNCH_AT)) is True


class TestWaitScopeAnchorReturnsBothHalves:
    """The predicate underneath the surfaces above, pinned directly.

    It returns (anchor, class) and the callers use the halves separately —
    `wait_covers_record` takes the anchor and ignores the class,
    `wait_anchor_class` takes the class and ignores the anchor. An arm on either
    caller alone cannot see a swap of the two.
    """

    @pytest.mark.parametrize("bad", [None, "", 7, [], "not-a-dict"])
    def test_a_non_dict_wait_yields_no_anchor_and_ABSENT(self, bad):
        assert wait_scope_anchor(bad) == (None, ANCHOR_CLASS_ABSENT)

    def test_a_valid_anchor_returns_THAT_instant_and_no_class(self):
        anchor, cls = wait_scope_anchor(
            {"since": _iso(RESTAMP_AT), WAIT_ANCHOR_KEY: _iso(ANCHOR_AT)})
        assert (anchor, cls) == (ANCHOR_AT, None)

    def test_an_absent_anchor_returns_SINCE_and_ABSENT(self):
        anchor, cls = wait_scope_anchor({"since": _iso(RESTAMP_AT)})
        assert (anchor, cls) == (RESTAMP_AT, ANCHOR_CLASS_ABSENT)

    def test_a_malformed_anchor_returns_SINCE_and_MALFORMED(self):
        anchor, cls = wait_scope_anchor(
            {"since": _iso(RESTAMP_AT), WAIT_ANCHOR_KEY: "garbage"})
        assert (anchor, cls) == (RESTAMP_AT, ANCHOR_CLASS_MALFORMED)

    def test_an_unusable_SINCE_with_no_anchor_yields_None(self):
        """Both halves unusable is the one case with nothing to fall back to."""
        anchor, cls = wait_scope_anchor({"since": "garbage"})
        assert (anchor, cls) == (None, ANCHOR_CLASS_ABSENT)
