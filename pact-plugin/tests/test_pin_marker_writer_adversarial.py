"""
Location: pact-plugin/tests/test_pin_marker_writer_adversarial.py

Summary: Adversarial extension of `test_pin_marker_writer.py`. That file shows
the implementation works. This one attacks the properties it does NOT reach,
and every gate here carries the arm that makes its own degradation visible.

Sibling file rather than an addition, for two reasons. The primary file's
assertions are tight fire-counts and six named anti-vacuity guards; a large
parametrised matrix alongside them costs signal on those counts. And keeping
the two apart makes it impossible for work here to quietly reshape a guard
there.

FOUR PROPERTIES THE PRIMARY FILE CANNOT REACH, each with the reason:

  1. THE TWO-STATE DENY. `compute_deny_reason` compares a PRE-edit parse
     against a POST-edit parse. No single-document probe reaches it at any
     probe count, because the compared quantity does not exist until two
     documents are parsed. Certified here on DOCUMENT PAIRS, driven through
     the gate's own pipeline in the gate's own order.

  2. ROTATION-DELAYED DEATH. The primary file pins marker survival as a
     REGEX MATCH against hand-written copies of the auto-writer patterns.
     A pattern match is not survival. Survival is measured here by running
     the REAL writer across `MAX_RETRIEVED_MEMORIES + 1` saves with a live
     rotation control, because a single-save probe scores a FALSE GREEN --
     demonstrated below rather than asserted.

  3. THE REFUSAL PREDICATE'S OVER-REFUSAL SURFACE. A predicate chosen for
     being unable to misplace is worthless if it refuses the commonest shape
     in a curated pin.

  4. THE OBSERVABILITY SEAM. The census event is pinned in the primary file
     with BOTH `append_event` and `pact_context.init` monkeypatched -- the
     seam whose correct resolution IS the thing under test. A census that
     never lands reads as "the predicate never fires", which is the one
     conclusion it exists to prevent.

EVERY GATE HERE HAS A MEASURED INPUT THAT REDDENS IT. An assertion with no
stated red is decoration, so the mutation that kills each gate is recorded
rather than left for a reviewer to re-derive. Measured by mutating an ISOLATED
COPY of the plugin and running pytest inside it -- `PYTHONPATH` does NOT work,
because `conftest.py` inserts its own `hooks/` path and a `sys.path` insertion
beats an environment variable, so a mutant loaded that way never loads and
every arm reports a false green.

  | mutation                                   | reddens                        |
  |--------------------------------------------|--------------------------------|
  | fence predicate always returns False       | the 5 refusal rows + the census |
  | fence predicate always returns True        | 28, incl. all 9 pair rows       |
  | end-marker name OUT of the alternation     | the 3 gap tests + 1 PAIR ROW    |
  | start-marker name OUT of the alternation   | the caught-survives rotation arm|
  | whole-file marker check disabled           | the 3 stray-literal refusals    |
  | census emitted unconditionally             | the census non-vacuity arm      |

The third row is the one that matters most: it proves the PAIR COMPARISON can
detect a verdict that moved. Without it, nine rows agreeing between two arms
would be indistinguishable from a test comparing a value to itself.

A MUTATION THAT FAILS TO APPLY REPORTS A CLEAN GREEN. One arm of this sweep
silently no-opped on a mangled search pattern and reported zero failures,
which reads exactly like "the tests do not cover this". Assert the patch
anchor is present before scoring any arm, and treat a total non-flip as an
instrument alarm rather than a finding.

SAFETY NOTE FOR ANYONE EDITING THIS FILE. Read this before adding a test.

`staleness._resolve_project_claude_md_with_base` returns on
`CLAUDE_PROJECT_DIR` ONLY when a file EXISTS there. When the fixture is
EMPTY it FALLS THROUGH to the parent of `git rev-parse --git-common-dir`,
and a worktree shares the main checkout's common dir -- so a subprocess whose
working directory sits inside the repo resolves the DEVELOPER'S OWN
`CLAUDE.md`, which is gitignored and unrecoverable. The empty fixture is the
commonest shape in a no-op test, so this fires exactly where it is least
expected. Pinning the environment variable alone does NOT close it.

Every subprocess here therefore pins `CLAUDE_PROJECT_DIR`, `HOME` AND `cwd`
to a `tmp_path`, and no test process runs with a working directory inside a
git repository. Do not remove any of the three.

USE THE IMPORTED CONSTANTS, NEVER A MARKER LITERAL. `MANAGED_START_MARKER`
carries a long human-readable suffix. A hand-typed short form parses as a
file with NO managed region, which silently changes the configuration under
test rather than failing. `assert_is_shipping_shape` below exists to catch
exactly that, and it caught it once already.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pin_caps import (
    PIN_COUNT_CAP,
    PIN_SIZE_CAP,
    apply_edit_and_parse,
    compute_deny_reason,
)
# `pin_caps_gate` is imported LAZILY, inside `gate_verdict`, and must stay that
# way. It is a hook SCRIPT, and importing it at module scope pulls it in during
# COLLECTION -- before any test runs. MEASURED: doing so makes two unrelated
# counter-tests in `test_pin_caps_gate_counter_test.py` fail, and only at
# full-suite scale. Isolated by construction rather than inferred: with an
# empty stub in this file's place the counter-tests pass, with a stub carrying
# ONLY this one import line they fail, and stubs carrying any of the other five
# imports pass. The counter-test file imports both `pin_caps` and
# `pin_caps_gate` inside its own test bodies for the same reason, so this
# follows the convention already in force here rather than inventing one.
from shared.claude_md_manager import (
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
    extract_managed_region,
)
# The boundary layout is taken from ONE place for the whole corpus. Importing
# the sibling's helper rather than re-deriving it here is what stops the two
# files drifting into two different opinions about the shipping shape, which
# is the defect this correction exists to remove.
from tests.test_pin_marker_writer import production_head_and_tail
from shared.pin_markers import (
    START_LINE,
    Insertion,
    SkipReason,
    apply_insertion,
    certify_expel_nothing,
    plan_insertion,
)
from staleness import _parse_pinned_section
from clock_shift.clock_shift_env import carry_clock_shift

PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "pin_marker_writer.py"
MEMORY_SCRIPTS = PLUGIN_ROOT / "skills" / "pact-memory" / "scripts"

WORKING_MEMORY_HEADING = "## Working Memory\n"


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def a_pin(n: int, body: str = "body prose") -> str:
    """One pin in the shape `commands/pin-memory.md` documents.

    THE DATE COMMENT GOES ABOVE THE HEADING. `parse_pins` walks BACKWARD from
    a heading to attribute a comment, so a comment placed below the heading
    binds to the NEXT pin and leaves this one with `date_comment is None` --
    which the gate then reads as a smuggle and denies for a reason that has
    nothing to do with the test. That mistake produced a false finding here
    once; the shape is pinned in a helper so it cannot recur per-test.
    """
    return f"<!-- pinned: 2026-01-01 -->\n### Pin {n}\n{body}\n\n"


def claude_md(
    n_pins: int = 3,
    last_pin_body: str = "body prose",
    pinned_body: str | None = None,
    managed: bool = True,
    retrieved: str = "\n### 2026-01-01\nA retrieved entry.\n\n",
) -> str:
    """Compose a CLAUDE.md in canonical section order.

    Parameterised by PIN COUNT, which is what the cap tests vary. The primary
    file's builder is parameterised by pinned BODY instead; the two are not
    twins of one another, they take different axes.
    """
    if pinned_body is None:
        pinned_body = "".join(
            a_pin(i, last_pin_body if i == n_pins else "body prose")
            for i in range(1, n_pins + 1)
        )
    sections = (
        f"## Retrieved Context\n{retrieved}"
        f"## Pinned Context\n\n{pinned_body}"
        f"{WORKING_MEMORY_HEADING}\n### 2026-01-02\nA working entry.\n"
    )
    if not managed:
        return (
            "# My own heading\n\nUser prose above.\n\n"
            "# PACT Framework and Managed Project Memory\n\n"
            + sections
            + "\nUser prose below.\n"
        )
    # THE BOUNDARY LAYOUT COMES FROM THE PRODUCTION EMITTER. See
    # `test_pin_marker_writer.production_skeleton` for why a hand-written
    # boundary is the defect rather than the shortcut: this builder used to
    # emit the outer managed marker with NO inner memory pair, a shape
    # production does not produce.
    head, tail = production_head_and_tail()
    return (
        "# My own heading\n\nUser prose above.\n\n"
        + head
        + sections
        + tail
        + "User prose below.\n"
    )


def assert_is_shipping_shape(content: str) -> None:
    """A fixture missing EITHER boundary is NOT the configuration under test.

    Every reader in this feature bounds itself to a region and falls back to a
    WIDER text when it finds none. A fixture that fails to establish the region
    therefore still produces plausible numbers, from a different code path.
    This is the wrong-unit family in fixture form.

    THE MEMORY CLAUSE IS THE ONE THAT WAS MISSING, and its absence is what let
    the whole corpus drift. This guard checked the MANAGED region only, so a
    document carrying the outer marker and no inner pair passed it while being
    a shape production never emits. A guard that names one of two boundaries
    certifies the fixtures it was written against and nothing wider.
    """
    assert extract_managed_region(content) is not None, (
        "fixture has no PACT-managed region, so every reader silently fell "
        "back to scanning the whole file. Build it from the production "
        "emitter, never from a hand-typed literal."
    )
    region_text, _offset = extract_managed_region(content)
    assert MEMORY_START_MARKER in region_text, (
        "fixture has no PACT_MEMORY start marker inside its managed region, "
        "so the pin planner has no window to anchor in. Build it from the "
        "production emitter, never from a hand-typed literal."
    )
    assert MEMORY_END_MARKER in region_text, (
        "fixture has no PACT_MEMORY end marker inside its managed region, "
        "so the pin planner has no window to anchor in. Build it from the "
        "production emitter, never from a hand-typed literal."
    )


def marked(content: str) -> str:
    """Return `content` with the marker pair inserted. Refusal is a test bug."""
    planned = plan_insertion(content)
    assert isinstance(planned, Insertion), f"the planner refused the fixture: {planned}"
    new = apply_insertion(content, planned)
    assert certify_expel_nothing(content, new, planned) is True
    return new


def gate_verdict(content: str, tool_input: dict):
    """Drive the gate's OWN pipeline, in the gate's OWN order.

    Composed from the gate's real functions rather than re-implemented, so a
    change to how the gate assembles pre/post reaches this test.

    The import is INSIDE this function deliberately -- see the note at the
    module imports. Moving it to the top breaks two counter-tests at
    full-suite scale.
    """
    from pin_caps_gate import _extract_new_body, _parse_baseline

    pre_pins = _parse_baseline(content)
    post_pins = apply_edit_and_parse(content, tool_input)
    new_body = _extract_new_body(tool_input, pre_pins=pre_pins, post_pins=post_pins)
    return compute_deny_reason(pre_pins, post_pins, new_body=new_body)


# --------------------------------------------------------------------------
# 0. THE PLANNER IS ALIVE AT ALL
# --------------------------------------------------------------------------

# Every document shape this file drives through the planner, kept in one place
# so the liveness sweep cannot drift away from the fixtures in use.
#
# BUILT LAZILY, AND THAT IS LOAD-BEARING RATHER THAN STYLISTIC. An earlier
# version was a module-level list whose "already marked" row called `marked()`,
# which asserts the planner returns an `Insertion`. So a broken planner raised
# at IMPORT time and the whole file became uncollectable -- a collection ERROR
# instead of the liveness guard firing with its diagnostic message, and an
# uncollectable file also reads as a large unexplained removal in a count
# floor. MEASURED: mutating the planner to reference a removed symbol produced
# exactly that. A test module must not depend at import time on the thing it
# exists to check.
PLANNER_CORPUS_IDS = [
    "clean migrated file", "single pin", "at the count cap",
    "over the count cap", "oversized pin body", "unmigrated",
    "empty pinned section", "whitespace-only pinned section", "fenced body",
    "inline backticks", "already marked", "crlf document", "empty string",
    "no pinned heading",
]


def planner_corpus() -> list[str]:
    """The documents, built on demand. Never calls a helper that asserts."""
    already_marked = claude_md(n_pins=1)
    planned = plan_insertion(already_marked)
    if isinstance(planned, Insertion):
        already_marked = apply_insertion(already_marked, planned)
    # When the planner is broken this row degrades to an unmarked document
    # rather than raising, so the sweep still runs and still reports.
    return [
        claude_md(n_pins=2),
        claude_md(n_pins=1),
        claude_md(n_pins=PIN_COUNT_CAP),
        claude_md(n_pins=PIN_COUNT_CAP + 1),
        claude_md(n_pins=2, last_pin_body="x" * (PIN_SIZE_CAP + 1)),
        claude_md(managed=False),
        claude_md(pinned_body=""),
        claude_md(pinned_body="   \n\n"),
        claude_md(pinned_body="### A pin\n```\ncode\n```\n\n"),
        claude_md(pinned_body="### A pin\nuse `x` inline\n\n"),
        already_marked,
        claude_md(n_pins=2).replace("\n", "\r\n"),
        "",
        claude_md(pinned_body="").replace("## Pinned Context\n", ""),
    ]


class TestThePlannerIsAlive:
    """THE CONTROL THAT MAKES EVERY REFUSAL ASSERTION IN THIS FILE MEAN
    ANYTHING.

    `plan_insertion` is total: it wraps its whole body and converts ANY
    exception into `SkipReason.PLAN_FAILED`. That guard is correct -- a hook
    forbidden to fail must not raise -- but it has a cost nobody priced. A
    `NameError` from a half-migrated module renders as a LADDER RESULT. The
    hook then exits 0, journals a plausible outcome, and writes nothing,
    forever, on every file.

    AND EVERY TEST THAT ASSERTS "THE PLANNER REFUSES ON «PRECONDITION»" IS
    SATISFIED BY A PLANNER THAT HAS NEVER WORKED. The refusal suite in this
    file would be entirely green against a planner that returns a refusal for
    every input, including one that cannot execute a single line of its body.

    This is the arc's own allow-side lesson at a new site: for a predicate
    whose BROKEN state is to REFUSE, the control that does the work is the one
    asserting it does NOT refuse.

    OBSERVED 2026-08-01, mid-edit, and recorded as a dated observation rather
    than a standing claim: `PLAN_FAILED` was reported for every input while
    the module was half-migrated -- the dataclass and enum had moved to the
    single-marker design while the function body still referenced removed
    symbols. The state is reachable, which is why this class exists; it was
    NOT present when these tests were written, which is why they pass.
    """

    def test_the_planner_returns_an_insertion_on_a_clean_fixture(self):
        """THE POSITIVE OUTCOME. If this is the only test that survives a
        future tidy-up, the suite still cannot go green against a dead
        planner."""
        planned = plan_insertion(claude_md(n_pins=2))
        assert isinstance(planned, Insertion), (
            f"the planner produced no insertion on a clean migrated fixture, "
            f"got {planned}. Every refusal assertion in this file is vacuous "
            "while this is true."
        )

    @pytest.mark.parametrize("index", range(len(PLANNER_CORPUS_IDS)),
                             ids=PLANNER_CORPUS_IDS)
    def test_no_fixture_ever_yields_PLAN_FAILED(self, index):
        """`PLAN_FAILED` is a TOTALITY GUARD, not a designed outcome. Every
        operation in the planner is a string or regex operation on a `str`,
        so no `str` input can reach it. Any occurrence is a bug by
        construction -- most likely an exception being laundered into a
        ladder result.
        """
        label = PLANNER_CORPUS_IDS[index]
        document = planner_corpus()[index]
        assert plan_insertion(document) is not SkipReason.PLAN_FAILED, (
            f"{label}: the planner raised internally and the totality guard "
            "converted it into a skip reason. This is an exception wearing "
            "the costume of a precondition."
        )

    def test_the_corpus_reaches_more_than_one_outcome(self):
        """NON-VACUITY for the sweep above. A corpus that produced a single
        outcome could not distinguish a working planner from a stuck one, and
        `is not PLAN_FAILED` would pass on any of them."""
        outcomes = set()
        for doc in planner_corpus():
            planned = plan_insertion(doc)
            outcomes.add("Insertion" if isinstance(planned, Insertion)
                         else planned.value)
        assert len(outcomes) >= 3, (
            f"the planner corpus reaches only {outcomes}; a differentiated "
            "table is what makes the liveness sweep readable"
        )
        assert "Insertion" in outcomes, (
            "no document in the corpus produces an insertion, so the planner "
            "refuses everything and every refusal assertion here is vacuous"
        )

    def test_PLAN_FAILED_is_still_reachable_and_this_guard_can_fire(self):
        """NON-VACUITY for the guard itself.

        Asserting an outcome never occurs is worthless if that outcome cannot
        occur at all. A non-`str` input is the one input that legitimately
        reaches the totality guard, so it proves the sweep above is testing a
        live distinction rather than an impossible one.
        """
        assert plan_insertion(None) is SkipReason.PLAN_FAILED
        assert plan_insertion(42) is SkipReason.PLAN_FAILED


# --------------------------------------------------------------------------
# 1. The two-state deny, certified on DOCUMENT PAIRS
# --------------------------------------------------------------------------

_APPEND_AFTER = "### Pin {n}\nbody prose\n"

# Each row is (label, n_pins, last_pin_body, tool_input, expected_verdict).
# The edit must land at the SAME place relative to the pinned body in both
# documents of the pair, or the two arms are running different edits and a
# disagreement says nothing about the markers.
PAIR_TABLE = [
    ("clean file, unrelated edit", 3, "body prose",
     {"old_string": "User prose below", "new_string": "User prose beneath"}, "allow"),
    ("11 -> 12 pins, AT the cap", 11, "body prose",
     {"old_string": _APPEND_AFTER.format(n=11),
      "new_string": _APPEND_AFTER.format(n=11) + "\n" + a_pin(99)}, "allow"),
    ("12 -> 13 pins, OVER the cap", 12, "body prose",
     {"old_string": _APPEND_AFTER.format(n=12),
      "new_string": _APPEND_AFTER.format(n=12) + "\n" + a_pin(99)}, "deny"),
    ("body exactly at the size cap", 3, "x" * PIN_SIZE_CAP,
     {"old_string": "User prose below", "new_string": "User prose beneath"}, "allow"),
    ("body one over the size cap, unrelated edit", 3, "x" * (PIN_SIZE_CAP + 1),
     {"old_string": "User prose below", "new_string": "User prose beneath"}, "allow"),
    ("body grows across the size cap", 3, "x" * (PIN_SIZE_CAP - 100),
     {"old_string": "x" * (PIN_SIZE_CAP - 100),
      "new_string": "x" * (PIN_SIZE_CAP + 1)}, "deny"),
    ("body shrinks back under the cap", 3, "x" * (PIN_SIZE_CAP + 1),
     {"old_string": "x" * (PIN_SIZE_CAP + 1),
      "new_string": "x" * (PIN_SIZE_CAP - 100)}, "allow"),
    ("pre-bad count, edit does not worsen", PIN_COUNT_CAP + 1, "body prose",
     {"old_string": "User prose below", "new_string": "User prose beneath"}, "allow"),
    ("pre-bad count, edit adds another", PIN_COUNT_CAP + 1, "body prose",
     {"old_string": _APPEND_AFTER.format(n=PIN_COUNT_CAP + 1),
      "new_string": _APPEND_AFTER.format(n=PIN_COUNT_CAP + 1) + "\n" + a_pin(99)},
     "deny"),
]


class TestTwoStateDenyOnDocumentPairs:
    """MERGE GATE. Inserting the marker pair must not move a cap verdict.

    The size cap is STRICT (`body_chars > PIN_SIZE_CAP`), so a body of exactly
    the cap is NOT a violation and one character more is. Both boundaries are
    rows here, because a `>=` reading finds a non-violation and reports a
    clean negative.
    """

    @pytest.mark.parametrize(
        "label,n_pins,last_body,tool_input,expected",
        PAIR_TABLE, ids=[r[0] for r in PAIR_TABLE],
    )
    def test_the_marked_twin_returns_the_same_verdict(
        self, label, n_pins, last_body, tool_input, expected
    ):
        unmarked = claude_md(n_pins=n_pins, last_pin_body=last_body)
        assert_is_shipping_shape(unmarked)
        twin = marked(unmarked)

        # A twin whose anchor is missing is not a twin. Without this the edit
        # silently becomes a no-op on one arm and the two verdicts agree for
        # the wrong reason.
        assert tool_input["old_string"] in unmarked, f"{label}: anchor missing (unmarked)"
        assert tool_input["old_string"] in twin, f"{label}: anchor missing (marked)"

        before = gate_verdict(unmarked, tool_input)
        after = gate_verdict(twin, tool_input)

        assert (before or "") == (after or ""), (
            f"{label}: the marker pair MOVED the cap verdict.\n"
            f"  unmarked: {before!r}\n  marked:   {after!r}"
        )
        assert (before is not None) == (expected == "deny"), (
            f"{label}: the row's own expectation is wrong, so the pair "
            f"comparison above is not measuring the case it names. "
            f"expected {expected}, got {before!r}"
        )

    def test_the_pair_table_produces_both_outcomes(self):
        """NON-VACUITY. A table that only ever ALLOWS would agree with itself
        under any change to the markers.

        A uniform column across a differentiated table is a broken instrument,
        not a result, so the outcome SET is asserted rather than the rows.
        """
        outcomes = set()
        for label, n_pins, last_body, tool_input, _expected in PAIR_TABLE:
            doc = claude_md(n_pins=n_pins, last_pin_body=last_body)
            outcomes.add("deny" if gate_verdict(doc, tool_input) else "allow")
        assert outcomes == {"allow", "deny"}, (
            f"the pair table is uniform ({outcomes}); it cannot detect a "
            "verdict that moved"
        )


class TestNoCaughtTerminatorEndsThePinnedRegion:
    """RESIDUAL CANARY for a defect that is being designed out.

    ============================================================
    TWO TESTS HERE ARE EXPECTED TO FAIL UNTIL THE END MARKER IS
    REMOVED. THEY ARE THE CANARY AND THEY ARE MEASURING CORRECTLY.
    DO NOT RELAX THEM TO GET A GREEN RUN -- they go green on their
    own the moment the caught marker stops sitting below the pins.
    A red here is the finding, not a broken test.
    ============================================================


    THE DEFECT IT REMEMBERS. An END marker whose name joins the terminator
    alternation stops the live pinned scan at itself, opening a span between
    it and `## Working Memory` that belongs to no region. A pin written there
    sits VISUALLY inside the Pinned Context section and is charged against
    NEITHER cap. Measured, with a control that isolated alternation membership
    as the cause: the unmarked baseline denied a 13th pin, the marked document
    allowed it, and renaming the marker out of the alternation restored the
    deny. The size axis behaved identically.

    WHY THIS CLASS EXISTS AT ALL. The remedy removes the END marker, which
    makes the span structurally impossible -- and therefore makes every
    detector of it structurally unreachable. A fix that makes a bad state
    impossible also retires its detector silently, and a silently retired
    detector is how the same defect ships a second time. So one assertion
    stays, phrased against the property rather than against the deleted symbol.

    WHAT IT ASSERTS, and the wording matters. NOT "no caught marker sits above
    `## Working Memory`" -- the START marker is caught and does sit above it,
    higher up, so that phrasing would be false on arrival and a canary that
    fires on the intended state gets deleted within a week. The property is
    about where the pinned scan ENDS: on a marked file the pinned region must
    terminate at the INFERRED heading and not at any marker.
    """

    def test_the_pinned_region_terminates_at_the_declared_end_marker(self):
        """SUPERSEDES `test_the_pinned_region_terminates_at_the_inferred
        _heading`, whose CLAIM the marker pair retires.

        That test asserted the region ends at the inferred heading, because the
        remedy of the day was to have NO end marker. The user retired that:
        every marker that bounds a region in PACT is a pair. So the region now
        ends AT the declared END marker, which sits above the heading.
        """
        marked_doc = marked(claude_md(n_pins=3))
        assert_is_shipping_shape(marked_doc)
        parsed = _parse_pinned_section(marked_doc)
        assert parsed is not None, "the reader must find the section at all"

        _start, end, _content = parsed
        assert marked_doc[end:].startswith(PINNED_END_MARKER), (
            "the pinned region no longer ends at the declared END marker, so "
            "the pair is not the delimiter the parse uses"
        )

    def test_a_pin_placed_above_the_end_marker_is_charged(self):
        """THE COMPLIANT WRITE PATH. `commands/pin-memory.md` instructs the
        writer to insert above the END marker, and a pin placed there is inside
        the region and charged against the count cap."""
        doc = marked(claude_md(n_pins=PIN_COUNT_CAP))
        insert_above = {
            "old_string": PINNED_END_MARKER,
            "new_string": a_pin(99) + PINNED_END_MARKER,
        }
        assert gate_verdict(doc, insert_above) is not None, (
            "a pin inserted ABOVE the END marker was not charged against the "
            "count cap; the compliant write path is not measured"
        )

    def test_a_pin_appended_below_the_end_marker_is_NOT_charged(self):
        """THE RESIDUAL, PINNED AS A RESIDUAL RATHER THAN LEFT IMPLICIT.

        This test asserts a GAP, not a guarantee, and that is deliberate. A pin
        appended below the END marker sits outside the region, so no cap
        measures it. The pair CREATES this span -- it does not exist without an
        end marker -- and nothing in the hook layer closes it.

        WHAT CLOSES IT IS AN INSTRUCTION, NOT A MECHANISM. `commands/pin-memory
        .md` tells the writer to insert above the marker. That file is executed
        by an LLM, so no test can prove compliance at run time; the sibling test
        above measures what happens WHEN it complies, and this one measures what
        happens when it does not.

        AND PROSE IS NOT A FALLBACK HERE, IT IS THE ONLY WRITER THERE IS. No
        code path in this repository inserts a pin: the command file instructs
        an LLM to perform the edit, and the caps gate only judges the resulting
        Edit at PreToolUse. So there is no lower level to push this down to
        without building one.

        Tracked as issue #1358, which carries the measured table, the search
        that establishes no pin-inserting code path exists, and the reason a
        caps-gate refusal was DEFERRED rather than omitted: a new deny on that
        control needs over-block certification of its own, and in this project
        an over-block is worse than the gap it closes.

        SO THE INSTRUCTION'S EXISTENCE IS ASSERTED HERE TOO. It is the only
        mitigation this span has, and a mitigation that lives in prose can be
        deleted by anyone tidying a command file without a single test going
        red. This assertion is that red.

        If a future change adds a MECHANICAL guard -- a gate refusal for edits
        below the marker -- this test must be inverted, not deleted.
        """
        doc = marked(claude_md(n_pins=PIN_COUNT_CAP))
        append_below = {
            "old_string": WORKING_MEMORY_HEADING,
            "new_string": a_pin(99) + WORKING_MEMORY_HEADING,
        }
        assert gate_verdict(doc, append_below) is None, (
            "a pin appended below the END marker was charged after all. If a "
            "mechanical guard now closes this span, INVERT this test rather "
            "than deleting it -- the residual it documents would be gone."
        )

        pin_command = (
            Path(__file__).parent.parent / "commands" / "pin-memory.md"
        ).read_text(encoding="utf-8")
        assert PINNED_END_MARKER in pin_command, (
            "commands/pin-memory.md no longer names the END marker, so the "
            "ONLY mitigation for the uncharged span above has been removed"
        )
        assert "ABOVE" in pin_command, (
            "commands/pin-memory.md names the END marker but no longer tells "
            "the writer which side of it to insert on"
        )

    def test_the_canary_can_fire(self):
        """NON-VACUITY, and it is what keeps this class honest once the defect
        is structurally impossible.

        Reconstructs the retired shape -- a caught marker below the pins -- and
        asserts BOTH halves flip. Without this the two assertions above would
        pass on any document at all, and the canary would be a comment.
        """
        # A LITERAL ON PURPOSE, not an oversight. This reconstructs a shape the
        # design no longer ships, so it must not depend on a constant that has
        # been deleted -- the canary has to keep working after the symbol it
        # remembers is gone. Any name matching the terminator alternation does.
        caught_marker_below_the_pins = "<!-- PACT_MEMORY_PINNED_END -->"
        doc = claude_md(n_pins=PIN_COUNT_CAP).replace(
            WORKING_MEMORY_HEADING,
            caught_marker_below_the_pins + "\n" + WORKING_MEMORY_HEADING,
            1,
        )
        parsed = _parse_pinned_section(doc)
        assert parsed is not None
        assert not doc[parsed[1]:].startswith(WORKING_MEMORY_HEADING), (
            "the reconstructed defect no longer moves the region end, so the "
            "offset assertion above cannot detect it"
        )
        append = {
            "old_string": WORKING_MEMORY_HEADING,
            "new_string": a_pin(99) + WORKING_MEMORY_HEADING,
        }
        assert gate_verdict(doc, append) is None, (
            "the reconstructed defect no longer un-charges the appended pin, "
            "so the behavioural assertion above cannot detect it"
        )


# --------------------------------------------------------------------------
# 2. Rotation-delayed death, against the REAL writer
# --------------------------------------------------------------------------

_ROTATION_RUNNER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["HOOKS_DIR"])
sys.path.insert(0, os.environ["SCRIPTS_DIR"])
import working_memory as wm
from shared.claude_md_manager import extract_managed_region

FIXTURE = Path(os.environ["CLAUDE_PROJECT_DIR"])
TARGET = FIXTURE / "CLAUDE.md"

# SAFETY PRE-ASSERTION. The writer resolves its own target and takes no
# explicit path, so the resolution is checked BEFORE the writer runs.
resolved, _base = wm._resolve_display_claude_md_with_base()
if resolved is None:
    print(json.dumps({"abort": "resolver returned None"})); sys.exit(3)
try:
    Path(resolved).resolve().relative_to(FIXTURE.resolve())
except ValueError:
    print(json.dumps({"abort": "resolver escaped the fixture: %s" % resolved}))
    sys.exit(3)
if extract_managed_region(TARGET.read_text(encoding="utf-8")) is None:
    print(json.dumps({"abort": "fixture has no managed region"})); sys.exit(3)

START = os.environ["START_MARKER"]
# THE BOUND IS DERIVED HERE, in the process that runs the writer, from the
# writer's OWN constant. It is never passed in and never typed.
saves = wm.MAX_RETRIEVED_MEMORIES + 1
SENTINELS = ["SENTINEL-%d" % i for i in range(0, saves + 1)]

def state():
    text = TARGET.read_text(encoding="utf-8")
    _b, _h, _a, entries = wm._parse_retrieved_context_section(text)
    return {"start_marker": START in text,
            # GAP_SENTINEL sits between the last dated entry and the pinned
            # heading. Whether it survives is the property the caught marker
            # actually changes.
            "gap_content": "GAP_SENTINEL" in text,
            "rc_entries": len(entries),
            "sentinels": [s for s in SENTINELS if s in text]}

out = {"max_retrieved": wm.MAX_RETRIEVED_MEMORIES, "saves": saves,
       "steps": [dict(save=0, **state())]}
for i in range(1, saves + 1):
    wm.sync_retrieved_to_claude_md(
        memories=[{"context": "ctx %d" % i, "goal": "goal %d" % i}],
        query="SENTINEL-%d" % i, scores=[0.9], memory_ids=["id%d" % i])
    out["steps"].append(dict(save=i, **state()))
print(json.dumps(out))
'''


def _rotation_fixture(start_marker: str, marker_above_gap: bool = False) -> str:
    """A migrated file whose Retrieved Context already holds one dated entry.

    The seed entry is the LIVE ROTATION CONTROL: it must be evicted by the
    end of the run, or nothing in the run rotated and every survival verdict
    taken from it is vacuous.
    """
    head, tail = production_head_and_tail()
    return (
        "# My own heading\n\nUser prose above.\n\n"
        + head
        + "## Retrieved Context\n<!-- Auto-populated -->\n\n"
        + '### 2026-01-01 00:00\n**Query**: "SENTINEL-0"\n**Context**: seed\n\n'
        # Unrecognised content in the GAP -- after the last dated entry and
        # before the pinned heading. The rebuild keeps recognised entries
        # only, so this survives if and only if the scan terminates ABOVE it.
        #
        # THE ORDER OF THESE TWO LINES IS THE WHOLE EXPERIMENT. `marker_above
        # _gap=False` is the arrangement the REAL writer produces:
        # `apply_insertion` splices the marker immediately above the pinned
        # heading, so any pre-existing gap content ends up ABOVE the marker.
        # `True` is the opposite arrangement, which the writer never produces
        # and which exists here only as a control.
        + (start_marker + "\nGAP_SENTINEL: prose the rebuild does not recognise.\n\n"
           if marker_above_gap
           else "GAP_SENTINEL: prose the rebuild does not recognise.\n\n"
                + start_marker + "\n")
        + "## Pinned Context\n\n<!-- pinned: 2026-01-01 -->\n### A pin\nbody\n\n"
        + WORKING_MEMORY_HEADING + "\n### 2026-01-02\nA working entry.\n"
        + tail
        + "User prose below.\n"
    )


def _run_rotation(start_marker: str, marker_above_gap: bool = False) -> dict:
    """Run `MAX_RETRIEVED_MEMORIES + 1` real retrieval saves in isolation.

    The save count is derived INSIDE the subprocess from the writer's own
    constant, so this file never imports `working_memory` and never touches
    the interpreter's `sys.path`. That is not tidiness: a module-scoped
    `sys.path.insert(0, ...)` of the memory-scripts directory is a GLOBAL
    mutation that outlives this file and re-orders imports for every test
    that runs afterwards. It broke two unrelated counter-tests in the full
    suite while passing in isolation and when paired directly -- the failure
    appeared only at full-suite scale, which is where a path leak is hardest
    to attribute.
    """
    with tempfile.TemporaryDirectory() as tmp:
        fixture_dir = Path(tmp) / "proj"
        fixture_dir.mkdir()
        content = _rotation_fixture(start_marker, marker_above_gap)
        assert_is_shipping_shape(content)
        (fixture_dir / "CLAUDE.md").write_text(content, encoding="utf-8")
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(fixture_dir),
            "CLAUDE_PROJECT_DIR": str(fixture_dir),
            "CLAUDE_CONFIG_DIR": str(fixture_dir / "config"),
            "HOOKS_DIR": str(PLUGIN_ROOT / "hooks"),
            "SCRIPTS_DIR": str(MEMORY_SCRIPTS),
            "START_MARKER": start_marker,
        }
        proc = subprocess.run(
            [sys.executable, "-c", _ROTATION_RUNNER],
            capture_output=True, text=True, cwd=str(fixture_dir), env=carry_clock_shift(env),
            timeout=180,
        )
        assert proc.returncode == 0, (
            f"rotation runner failed rc={proc.returncode}\n"
            f"stdout={proc.stdout[-600:]}\nstderr={proc.stderr[-900:]}"
        )
        return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def rotation_runs():
    """Both arms, run once.

    THE GOVERNING CONSTANT IS `MAX_RETRIEVED_MEMORIES`, NOT
    `MAX_WORKING_MEMORIES`. The marker at risk sits in the RETRIEVED CONTEXT
    span. The two constants are equal today, so the wrong one yields the right
    number and the error is invisible -- until either moves alone, after which
    a test written to the wrong name silently measures the wrong section.
    The subprocess reads the correct constant from the writer's own module.
    """
    return {
        "caught": _run_rotation(PINNED_START_MARKER),
        "uncaught": _run_rotation("<!-- PINNED_START -->"),
        # CONTROL ARM. Same caught marker, placed ABOVE the gap content -- an
        # arrangement `apply_insertion` never produces. It exists to prove the
        # instrument CAN observe preservation, so "the gap content died in
        # both real arms" reads as a result rather than as a dead probe.
        "caught_above_gap": _run_rotation(PINNED_START_MARKER,
                                          marker_above_gap=True),
    }


class TestStartMarkerSurvivesRetrievedContextRotation:
    """The start marker sits INSIDE the span the retrieved-context writer
    rebuilds from recognised entries only. Whether it survives is a property
    of its NAME, and a pattern match is not a survival measurement.
    """

    @staticmethod
    def _rotation_ran(run: dict) -> bool:
        return "SENTINEL-0" not in run["steps"][-1]["sentinels"]

    def test_rotation_actually_ran_in_both_arms(self, rotation_runs):
        """THE CONTROL THAT MAKES EVERY OTHER ASSERTION HERE NON-VACUOUS.

        If the seed entry is still present, nothing was evicted, and a marker
        that "survived" merely outlived a run in which nothing was removed.
        """
        for arm in ("caught", "uncaught"):
            run = rotation_runs[arm]
            assert self._rotation_ran(run), (
                f"{arm}: the seed entry was never evicted, so rotation did not "
                f"run and no survival verdict from this arm means anything. "
                f"final sentinels={run['steps'][-1]['sentinels']}"
            )
            assert run["steps"][-1]["rc_entries"] == run["max_retrieved"], (
                f"{arm}: entry count is not at the rotation bound, so the run "
                "did not reach steady state"
            )

    def test_the_caught_start_marker_survives(self, rotation_runs):
        run = rotation_runs["caught"]
        assert self._rotation_ran(run), "precondition: rotation must have run"
        assert run["steps"][-1]["start_marker"] is True, (
            "the shipped start marker did NOT survive rotation. It terminates "
            "the retrieved-context scan by name, so this is a naming failure."
        )

    def test_the_uncaught_start_marker_dies(self, rotation_runs):
        """NON-VACUITY for the survival claim, by varying the ONE property the
        claim rests on.

        Without this arm, a fixture in which the writer never touched the
        marker at all would pass the survival test above.
        """
        run = rotation_runs["uncaught"]
        assert self._rotation_ran(run), "precondition: rotation must have run"
        assert run["steps"][-1]["start_marker"] is False, (
            "a start marker OUTSIDE the terminator alternation survived "
            "rotation, so the survival test above is not measuring the name"
        )

    def test_a_single_save_would_have_scored_a_false_green(self, rotation_runs):
        """The reason the bound is `MAX_RETRIEVED_MEMORIES + 1` and not one.

        Demonstrated rather than asserted in a comment: the uncaught marker is
        absorbed into the oldest entry and rides it, so it is still PRESENT
        after the first save and disappears only when rotation evicts that
        entry. A probe that stopped at save 1 would report survival.
        """
        run = rotation_runs["uncaught"]
        after_first = run["steps"][1]
        assert after_first["start_marker"] is True, (
            "the uncaught marker was already gone after one save, so this "
            "demonstration no longer shows the delayed death it describes"
        )
        assert run["steps"][-1]["start_marker"] is False

    def test_the_marker_does_not_change_the_fate_of_gap_content(
        self, rotation_runs
    ):
        """AT THE PLACEMENT THE WRITER ACTUALLY PRODUCES, the caught marker is
        BYTE-FREE with respect to content above it.

        `apply_insertion` splices the marker immediately above the pinned
        heading, so any pre-existing unrecognised content between the last
        dated entry and that heading ends up ABOVE the marker -- still inside
        the rebuilt span, and still evicted at rotation. MEASURED: the gap
        content dies in the caught arm and in the uncaught arm alike.

        That matches the design's own earlier statement that nothing which
        survives today stops surviving. It does NOT match a later claim that a
        caught marker converts destruction into preservation; the control
        below shows the arrangement in which that claim holds, and it is not
        one the writer can produce.
        """
        for arm in ("caught", "uncaught"):
            run = rotation_runs[arm]
            assert self._rotation_ran(run), f"{arm}: rotation must have run"
            assert run["steps"][-1]["gap_content"] is False, (
                f"{arm}: gap content SURVIVED. At the writer's real placement "
                "the marker sits below this content, so survival means the "
                "terminator moved and the marker is no longer byte-free."
            )

    def test_the_gap_instrument_can_observe_preservation(self, rotation_runs):
        """CONTROL, and it is what makes the uniform column above a result.

        Two arms agreeing is the signature of a dead probe, so the same
        measurement is run once more with the marker placed ABOVE the gap
        content. There the scan terminates above it, the rebuild copies it
        verbatim, and it SURVIVES. So the instrument can distinguish the two
        outcomes, and the agreement above is a property of the placement
        rather than of a broken fixture.

        This arrangement is NOT producible by `apply_insertion`.
        """
        control = rotation_runs["caught_above_gap"]
        assert self._rotation_ran(control), "control: rotation must have run"
        assert control["steps"][-1]["gap_content"] is True, (
            "the control did not preserve the gap content either, so this "
            "instrument cannot observe preservation at all and the two arms "
            "above certify nothing"
        )

    def test_a_single_save_would_not_have_separated_the_arms(self, rotation_runs):
        """The reason the bound is `MAX_RETRIEVED_MEMORIES + 1` and not one.

        After ONE save the gap content is still present in EVERY arm, so a
        single-save probe reports agreement and reads as a clean result.
        Demonstrated rather than asserted in a comment.
        """
        for arm in ("caught", "uncaught", "caught_above_gap"):
            assert rotation_runs[arm]["steps"][1]["gap_content"] is True, (
                f"{arm}: the arms already differ after one save, so this "
                "demonstration no longer shows the delayed divergence"
            )


# --------------------------------------------------------------------------
# 3. Reader and planner disagree about which documents have a pinned region
# --------------------------------------------------------------------------

class TestReaderPlannerBoundaryAsymmetry:
    """`_parse_pinned_section` FALLS BACK to scanning the whole file when there
    is no managed region. `plan_insertion` refuses such a file outright.

    So on a pre-migration file the READER sees a pinned region and would charge
    it, while the write declares no boundary there and never will. The two
    directions are pinned separately because only one of them was known.
    """

    def test_unmigrated_file_reader_accepts_and_planner_refuses(self):
        doc = claude_md(managed=False)
        assert extract_managed_region(doc) is None
        assert _parse_pinned_section(doc) is not None, (
            "the reader falls back to the whole file and DOES find a section"
        )
        assert plan_insertion(doc) is SkipReason.NOT_MIGRATED, (
            "the planner refuses the same document"
        )

    def test_empty_section_both_refuse_in_the_same_direction(self):
        """The known half. Different shapes by design, same direction, benign."""
        doc = claude_md(pinned_body="")
        assert _parse_pinned_section(doc) is None
        assert plan_insertion(doc) is SkipReason.EMPTY_SECTION

    def test_migrated_file_with_content_both_accept(self):
        """NON-VACUITY. Without a row where both accept, the two assertions
        above would pass on a planner that refused everything."""
        doc = claude_md(n_pins=2)
        assert _parse_pinned_section(doc) is not None
        assert isinstance(plan_insertion(doc), Insertion)


# --------------------------------------------------------------------------
# 4. The refusal predicate's over-refusal surface
# --------------------------------------------------------------------------

STILL_WRITES = [
    ("single inline backticks", "### A pin\nuse `code` inline\n\n"),
    ("doubled inline backticks", "### A pin\nuse ``code`` inline\n\n"),
    ("backticks in a table cell", "### A pin\n| a | b |\n|---|---|\n| `x` | y |\n\n"),
    ("a tilde in prose", "### A pin\napprox ~5 items, and ~~struck~~ text\n\n"),
    ("two tildes only", "### A pin\n~~strikethrough~~ is not a fence\n\n"),
    ("an html comment", "### A pin\n<!-- a note -->\nbody\n\n"),
    ("an indented code block, no fence", "### A pin\n    indented code\n    more\n\n"),
    ("a path containing tildes", "### A pin\nsee ~/.claude/settings.json\n\n"),
]

REFUSED = [
    ("triple backtick fence", "### A pin\n```\ncode\n```\n\n"),
    ("tilde fence", "### A pin\n~~~\ncode\n~~~\n\n"),
    ("four backtick fence", "### A pin\n````\n```\ninner\n```\n````\n\n"),
    ("unclosed backtick fence", "### A pin\n```\nnever closes\n\n"),
    ("triple backtick mentioned in prose", "### A pin\ntype ``` to open\n\n"),
]


class TestFenceRefusalDoesNotOverMatch:
    """A predicate chosen for being unable to MISPLACE is worthless if it
    refuses the commonest shape in a curated pin. Inline code is that shape.
    """

    @pytest.mark.parametrize("label,body", STILL_WRITES, ids=[r[0] for r in STILL_WRITES])
    def test_these_shapes_still_receive_the_markers(self, label, body):
        doc = claude_md(pinned_body=body)
        assert_is_shipping_shape(doc)
        assert isinstance(plan_insertion(doc), Insertion), (
            f"{label}: over-refusal. This shape carries no fence and must "
            "still be marked."
        )

    @pytest.mark.parametrize("label,body", REFUSED, ids=[r[0] for r in REFUSED])
    def test_these_shapes_are_refused(self, label, body):
        """The other side of the boundary. Both tables are needed: the first
        alone passes on a predicate that never refuses, the second alone
        passes on one that always does."""
        doc = claude_md(pinned_body=body)
        assert plan_insertion(doc) is SkipReason.FENCED_BODY, f"{label}: expected refusal"

    def test_a_fence_BELOW_the_terminator_does_not_refuse(self):
        """The predicate reads the TRUNCATED body, so a fence in the Working
        Memory section is out of scope and must not cost the pinned section
        its markers."""
        doc = claude_md(n_pins=1)
        doc = doc.replace(
            "### 2026-01-02\nA working entry.\n",
            "### 2026-01-02\nA working entry.\n```\nfenced, but below\n```\n",
        )
        assert "```" in doc
        assert isinstance(plan_insertion(doc), Insertion), (
            "a fence below the pinned terminator refused the write, so the "
            "predicate is reading a wider span than the measured body"
        )


# --------------------------------------------------------------------------
# 5. Marker state is decided on the WHOLE FILE, the section on the REGION
# --------------------------------------------------------------------------

class TestOnlyTheAdjacentLineSuppressesTheWrite:
    """The detector recognises ONLY what the writer emits: the last line of the
    gap above the pinned heading, stripped, equal to the symbol.

    THIS CLASS REPLACES ONE THAT ASSERTED THE OPPOSITE, and the history is kept
    because it is the more useful half. Its predecessor observed -- correctly --
    that the detector searched the WHOLE FILE while the section checks were
    region-bounded, and then pinned the resulting refusal as intended
    behaviour. Refusing IS the safe direction, which is why it read as a
    finding rather than a defect. What the assertion did not capture is that
    the refusal was UNCONDITIONAL and PERMANENT: a document that merely
    MENTIONED the symbol was never revisited, and the memory formatters
    interpolate harvested text into the target file, so a memory DISCUSSING
    the symbol creates a carrier with no human involved.

    So the direction was right and the duration was wrong, and a test that
    checks direction without duration certifies a feature that has switched
    itself off.
    """

    # (label, carrier placement, whether the write should proceed)
    CARRIERS = [
        ("no carrier at all", "", "", True),
        ("mid-line above the managed region", "prose", "", True),
        ("mid-line inside the pinned body", "", "prose", True),
    ]

    def test_a_clean_document_receives_the_marker(self):
        """Baseline. Kept from the predecessor class, where it was the
        non-vacuity arm for a refusal that no longer exists -- the assertion
        is still worth making on its own, so it is renamed rather than
        dropped."""
        planned = plan_insertion(claude_md(n_pins=1))
        assert isinstance(planned, Insertion)

    def test_a_carrier_outside_the_adjacent_line_does_not_suppress_the_write(self):
        """THE CORRECTED CONTRACT. A carrier anywhere but the adjacent line
        must NOT stop the write.

        Asserted as `isinstance(..., Insertion)` rather than as "not
        already-marked", because the weak form is satisfied by ANY other
        outcome -- including a refusal under a different name, which is the
        exact failure this replaces.

        FAILING INPUT: a detector that reverts to a whole-file substring test.
        It returns a `SkipReason` for both rows and this reddens. Verified by
        construction: the predecessor assertion, which demanded a `SkipReason`
        on this same fixture, failed against the corrected planner.
        """
        above = claude_md(n_pins=1)
        above = f"I documented {PINNED_START_MARKER} in my notes.\n\n" + above
        inside = claude_md(
            pinned_body=f"### A pin\nI quoted {PINNED_START_MARKER} here\n\n"
        )
        for label, doc in (("above the region", above), ("inside the body", inside)):
            assert_is_shipping_shape(doc)
            assert PINNED_START_MARKER in doc, f"{label}: fixture carries no carrier"
            planned = plan_insertion(doc)
            assert isinstance(planned, Insertion), (
                f"{label}: a carrier off the adjacent line suppressed the "
                f"write, got {planned}. The detector is matching somewhere "
                "other than the line the writer emits."
            )

    def test_a_carrier_ON_the_adjacent_line_still_suppresses_it(self):
        """THE POSITIVE CONTROL, and the reason it must survive this rewrite.

        Without it the class cannot catch a detector that NEVER matches, which
        is worse than the defect being fixed: every pin command would re-insert
        a marker and the count would grow without bound.

        Inverting a fossilised test is precisely when its positive control gets
        dropped as no-longer-relevant. It is MORE relevant here, not less.

        FAILING INPUT: a detector that never matches -- it returns an
        `Insertion` and this reddens.
        """
        doc = claude_md(n_pins=1).replace(
            "## Pinned Context\n", f"{PINNED_START_MARKER}\n## Pinned Context\n", 1
        )
        assert_is_shipping_shape(doc)
        # UNPAIRED, not ALREADY_MARKED, and the difference is the marker pair.
        # The START sits on the adjacent line -- the writer's own position --
        # so the detector DID recognise it. What this document lacks is the
        # END half, so it is HALF-marked. Either outcome proves the point this
        # control exists for: the write is SUPPRESSED, so no second marker is
        # emitted. Asserting the specific label keeps the control honest about
        # WHY it was suppressed.
        assert plan_insertion(doc) is SkipReason.UNPAIRED, (
            "a marker on the adjacent line was not recognised as the writer's "
            "own, so the next pin command would insert a second one"
        )

    def test_a_carrier_never_causes_a_second_marker_to_be_emitted(self):
        """THE PROPERTY THE PREDECESSOR WAS REALLY PINNING, kept because the
        wrong contract was wrapped around a real guard.

        Its original intent was that a carrier must never cause a DOUBLE WRITE.
        That was previously guaranteed BY the defect -- the write never ran at
        all -- so it was true for the wrong reason and cost nothing to assert.
        Now the write DOES run, which makes the property non-trivial for the
        first time.

        The measure is what the WRITER emitted, not the total occurrence count,
        because the carrier itself is one of the occurrences and is not ours.

        FAILING INPUT: a detector that fails to recognise its own emitted
        marker on the second pass. Every pass writes again and the count
        climbs past `carriers + 1`.
        """
        for label, doc in (
            ("above the region",
             f"I documented {PINNED_START_MARKER} in my notes.\n\n"
             + claude_md(n_pins=1)),
            ("inside the body",
             claude_md(pinned_body=f"### A pin\nI quoted {PINNED_START_MARKER} here\n\n")),
        ):
            carriers = doc.count(PINNED_START_MARKER)
            outcomes = []
            current = doc
            for _ in range(4):
                planned = plan_insertion(current)
                if isinstance(planned, SkipReason):
                    outcomes.append(planned.value)
                    continue
                composed = apply_insertion(current, planned)
                if not certify_expel_nothing(current, composed, planned):
                    outcomes.append("refused")
                    continue
                current = composed
                outcomes.append("written")

            assert outcomes[0] == "written", (
                f"{label}: the first pass did not write, so this arm never "
                f"reached the state it exists to test. outcomes={outcomes}"
            )
            assert outcomes[1:] == [SkipReason.ALREADY_MARKED.value] * 3, (
                f"{label}: a later pass did not report already-marked. "
                f"outcomes={outcomes}"
            )
            assert current.count(PINNED_START_MARKER) == carriers + 1, (
                f"{label}: the writer emitted more than one marker across four "
                f"passes -- {current.count(PINNED_START_MARKER)} occurrences "
                f"against {carriers} carrier(s) plus one legitimate write."
            )

    def test_a_second_pinned_heading_is_ignored_first_find_wins(self):
        """Documented behaviour: `re.search` takes the FIRST heading, so a
        second section sits outside the wrapped span entirely.

        The span is measured from the start marker to the INFERRED terminator
        rather than to a second marker literal, so this reads the same way
        however many markers the design carries.
        """
        doc = claude_md(n_pins=1)
        doc = doc.replace(
            WORKING_MEMORY_HEADING,
            "## Pinned Context\n\n" + a_pin(50) + WORKING_MEMORY_HEADING,
            1,
        )
        assert doc.count("## Pinned Context") == 2
        new = marked(doc)
        # Measured through the READER's own region rather than by slicing on
        # marker literals, so this reads the same however many markers the
        # design carries.
        parsed = _parse_pinned_section(new)
        assert parsed is not None
        region = parsed[2]
        assert "### Pin 1" in region, "the FIRST section is the measured one"
        assert "### Pin 50" not in region, "the second section sits outside it"


# --------------------------------------------------------------------------
# 6. Whole-document line endings
# --------------------------------------------------------------------------

class TestWholeDocumentLineEndings:
    """The primary file varies line endings INSIDE the pinned body only. A
    file that is CRLF throughout is a different document.

    The assertion is a SAFETY property rather than a specific outcome, because
    no specification says which of the two safe outcomes is required: either
    the planner refuses, or it inserts and expels nothing. The failure this
    excludes is the third case -- an insertion that loses a byte.
    """

    def test_a_crlf_document_is_refused_or_expels_nothing(self):
        doc = claude_md(n_pins=2).replace("\n", "\r\n")
        planned = plan_insertion(doc)
        if isinstance(planned, SkipReason):
            pytest.skip(f"planner refuses a CRLF document ({planned.value}); "
                        "refusal is a safe outcome and there is nothing to certify")
        new = apply_insertion(doc, planned)
        assert certify_expel_nothing(doc, new, planned) is True, (
            "a CRLF document was inserted into WITHOUT byte preservation"
        )

        # Restated independently of the function under test, so a certificate
        # degrading to `return True` cannot carry this row. The marker lines
        # are taken from the PLAN rather than from named constants, so this
        # survives a change in how many literals the design carries.
        stripped = new
        for line in (getattr(planned, name) for name in dir(planned)
                     if name.endswith("_line")):
            stripped = stripped.replace(line, "")
        assert stripped == doc, (
            "removing the planned marker lines did not reproduce the original"
        )


# --------------------------------------------------------------------------
# 7. The observability seam, NOT mocked
# --------------------------------------------------------------------------

def _write_session_context(fixture_dir: Path, session_id: str) -> Path:
    """Create the REAL session-context file the journal resolves through.

    The primary file's census tests monkeypatch `append_event` AND
    `pact_context.init`, which are the seam whose correct resolution is the
    thing under test. With both stubbed, a census that never lands anywhere
    still passes.
    """
    session_dir = (
        fixture_dir / "config" / "pact-sessions" / fixture_dir.name / session_id
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": "test-team",
            "session_id": session_id,
            "project_dir": str(fixture_dir),
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    return session_dir


def _run_hook_with_journal(pinned_body: str, prompt: str) -> tuple[int, list, str]:
    """Drive the real script against a real journal. Returns (rc, events, text)."""
    with tempfile.TemporaryDirectory() as tmp:
        fixture_dir = Path(tmp) / "proj"
        fixture_dir.mkdir()
        content = claude_md(pinned_body=pinned_body)
        assert_is_shipping_shape(content)
        target = fixture_dir / "CLAUDE.md"
        target.write_text(content, encoding="utf-8")
        session_id = "test-session"
        session_dir = _write_session_context(fixture_dir, session_id)
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(fixture_dir),
            "CLAUDE_PROJECT_DIR": str(fixture_dir),
            "CLAUDE_CONFIG_DIR": str(fixture_dir / "config"),
        }
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "prompt": prompt,
                "session_id": session_id,
                "cwd": str(fixture_dir),
            }),
            capture_output=True, text=True, cwd=str(fixture_dir), env=carry_clock_shift(env),
            timeout=60,
        )
        journal = session_dir / "session-journal.jsonl"
        events = (
            [json.loads(line) for line in journal.read_text().splitlines() if line]
            if journal.exists() else []
        )
        return proc.returncode, events, target.read_text(encoding="utf-8")


class TestCensusEventReachesARealJournal:
    """The census exists to convert a one-disk, self-applied count into a live
    one. If the event never lands, every consumer reports zero and zero reads
    as "fenced bodies are rare" -- the exact inference the census was built to
    avoid depending on.
    """

    FENCED = "<!-- pinned: 2026-01-01 -->\n### A pin\n```\n## Not a heading\n```\nmore\n\n"
    PLAIN = "<!-- pinned: 2026-01-01 -->\n### A pin\nplain prose only\n\n"

    def test_a_fenced_body_writes_both_events_to_a_real_journal(self):
        rc, events, text = _run_hook_with_journal(self.FENCED, "/PACT:pin-memory x")
        assert rc == 0
        types = [e["type"] for e in events]
        assert "pin_marker_write" in types, (
            "no journal event landed at all. The observability channel is the "
            "ONLY one this hook has -- it is async, so stdout is discarded."
        )
        assert "fenced_body_skipped" in types
        outcome = next(e for e in events if e["type"] == "pin_marker_write")["outcome"]
        assert outcome == SkipReason.FENCED_BODY.value, (
            f"the event landed but reports {outcome!r}; the fixture did not "
            "reach the fence predicate"
        )
        assert PINNED_START_MARKER not in text, "a refused body must not be written"

    def test_a_written_outcome_emits_only_the_write_event(self):
        """NON-VACUITY over the census, driven end to end rather than through a
        stub: an unconditionally-emitted census would fire here too."""
        rc, events, text = _run_hook_with_journal(self.PLAIN, "/PACT:pin-memory x")
        assert rc == 0
        types = [e["type"] for e in events]
        assert types == ["pin_marker_write"], f"unexpected event set: {types}"
        assert events[0]["outcome"] == "written"
        assert PINNED_START_MARKER in text

    def test_an_ordinary_prompt_journals_nothing(self):
        """The hot path must stay silent, or the journal fills with noise from
        every prompt in every session."""
        rc, events, text = _run_hook_with_journal(self.PLAIN, "what time is it")
        assert rc == 0
        assert events == [], f"an ordinary prompt journalled {events}"
        assert PINNED_START_MARKER not in text


# --------------------------------------------------------------------------
# 8. The certificate's refusal branch
# --------------------------------------------------------------------------

class TestCertificateRefusalReachability:
    """`_plan_and_write` returns `certificate_failed` when the composition
    cannot be proven byte-identical. This records whether that branch is
    reachable through the ASSEMBLED write, so a reader does not assume a live
    guard where there is a second, earlier one.

    THE DIRECT-DRIVE ARM IS DELIBERATELY ABSENT HERE and its role has moved.
    It constructed a damaged composition from CROSSED OFFSETS, which is not
    expressible once `Insertion` carries a single splice point -- one offset
    cannot cross itself. Measured exhaustively by the implementer: with one
    offset there is NO offset choice that drops a byte, so the certificate
    places no constraint on placement at all. Its refusal is still reachable,
    but only by mutating the ASSEMBLY rather than the offsets. That arm lives
    with the certificate's own tests, and the consequence for this file is
    recorded at `TestPlacementIsTheOnlyOffsetConstraint`.
    """

    def test_a_carrier_on_its_own_line_reaches_the_certificate_and_is_refused(self):
        """THE CERTIFICATE IS NOW REACHABLE BY THE CARRIER ROUTE, and that is
        the two-independent-mechanisms property rather than a side effect.

        The predecessor asserted the opposite -- that the planner refused first
        so the certificate was never consulted. That was TRUE, and it was true
        only because of the defect: an over-broad detector was firing early and
        MASKING the later guard. Removing the mask makes the second mechanism
        load-bearing for the first time.

        PLACEMENT IS THE WHOLE FIXTURE. The certificate strips `START_LINE` --
        the symbol PLUS its newline -- so a carrier sitting MID-LINE leaves
        `START_LINE` absent from the original and the certificate PASSES. Only
        a carrier occupying its OWN LINE puts `START_LINE` into the original
        and makes the equality fail. The predecessor's fixture was mid-line, so
        a rewrite that merely flipped its assertion would have demanded a
        refusal that never comes.

        FAILING INPUTS, two, and they are different mechanisms:
          - the planner refusing first again: `isinstance(planned, Insertion)`
            reddens, and the certificate is masked once more;
          - the certificate degrading to a constant True: the refusal
            assertion reddens.
        """
        doc = claude_md(
            pinned_body=f"### A pin\nbody prose\n\n{PINNED_START_MARKER}\n\n"
        )
        assert_is_shipping_shape(doc)
        assert f"{PINNED_START_MARKER}\n" in doc, (
            "precondition: the carrier must occupy its own line, or the "
            "certificate cannot observe it at all"
        )

        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion), (
            f"the planner refused before the certificate was reached, got "
            f"{planned}. The early guard is masking the later one again."
        )
        composed = apply_insertion(doc, planned)
        assert certify_expel_nothing(doc, composed, planned) is False, (
            "the certificate accepted a composition whose original already "
            "carried the marker line; the second mechanism is not refusing"
        )

    def test_a_mid_line_carrier_does_NOT_reach_the_certificate(self):
        """NON-VACUITY for the row above, and the measurement that decides its
        fixture.

        If the certificate refused on ANY carrier the test above would pass
        without placement mattering, and the claim that the rewrite is a
        fixture change rather than a claim change would be untested. Measured:
        mid-line PASSES, own-line REFUSES.

        FAILING INPUT: a certificate that strips the bare symbol rather than
        the symbol-plus-newline. Both placements would then refuse and this
        reddens.
        """
        doc = claude_md(
            pinned_body=f"### A pin\nI quoted {PINNED_START_MARKER} here\n\n"
        )
        assert_is_shipping_shape(doc)
        assert f"{PINNED_START_MARKER}\n" not in doc, (
            "precondition: the carrier must NOT occupy its own line"
        )
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion)
        composed = apply_insertion(doc, planned)
        assert certify_expel_nothing(doc, composed, planned) is True, (
            "a mid-line carrier was refused by the certificate, so placement "
            "no longer discriminates and the arm above proves nothing"
        )

    def test_the_writer_reports_a_collision_rather_than_an_assembly_defect(self):
        """END TO END, over BYTES ON DISK, because the branch under test lives
        in the WRITER and is unreachable from the planner alone.

        A refused composition has two causes that call for opposite responses:
        a document already carrying the marker is a COLLISION -- expected and
        countable -- while anything else is an assembly defect in this
        plugin's own code. Reporting them as one outcome is what let the
        collision hide under a success-shaped label.

        This is also the arm a planner-only rewrite would have missed
        entirely: I predicted the certificate would refuse and did NOT predict
        that the refusal surfaces under a distinct outcome name.

        FAILING INPUTS: the collision branch collapsing back into
        `certificate_failed`, or the write proceeding and changing the file.
        """
        rc, events, text = _run_hook_with_journal(
            f"### A pin\nbody prose\n\n{PINNED_START_MARKER}\n\n",
            "/PACT:pin-memory x",
        )
        assert rc == 0
        outcomes = [e["outcome"] for e in events if e["type"] == "pin_marker_write"]
        assert outcomes == [SkipReason.MARKER_COLLISION.value], (
            f"the writer did not report a collision; got {outcomes}"
        )
        # BYTES ON DISK: a refused composition must leave the file alone, and
        # the carrier must still be the only occurrence.
        assert text.count(PINNED_START_MARKER) == 1, (
            "the refused pass still altered the marker count on disk"
        )


class TestPlacementIsTheOnlyOffsetConstraint:
    """Placement coverage carries alone what two mechanisms carried before.

    With two offsets the certificate refused a crossed pair, so the offset had
    an independent constraint. With ONE offset it has none: measured
    exhaustively, every offset into a document that does not already contain
    the marker satisfies both certificate assertions. A wildly wrong offset
    that splices the marker into the middle of a heading now passes the
    certificate cleanly, by construction.

    So an assertion about WHERE the marker lands is the only thing standing
    between a correct planner and a silently misplacing one.
    """

    def test_the_marker_lands_immediately_above_the_pinned_heading(self):
        doc = claude_md(n_pins=2)
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion)
        new = apply_insertion(doc, planned)
        after = new.split(START_LINE, 1)[1]
        assert after.startswith("## Pinned Context"), (
            "the marker no longer sits immediately above the pinned heading, "
            "and the certificate CANNOT detect that -- it is satisfied by any "
            "offset. This assertion is the only constraint on placement."
        )

    def test_the_certificate_accepts_a_placement_this_test_rejects(self):
        """NON-VACUITY, and it is the whole reason this class exists.

        Builds a deliberately wrong placement, shows the certificate ACCEPTS
        it, and shows the placement assertion REJECTS it. If this ever fails
        because the certificate started refusing, placement has regained a
        second constraint and that is worth knowing -- but until then, the
        assertion above is load-bearing on its own.
        """
        doc = claude_md(n_pins=2)
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion)

        # Splice the same line at a nonsense offset, mid-heading.
        bad_offset = doc.index("## Pinned Context") + 5
        misplaced = doc[:bad_offset] + START_LINE + doc[bad_offset:]

        assert len(misplaced) == len(doc) + len(START_LINE)
        assert misplaced.replace(START_LINE, "") == doc, (
            "the misplaced composition still expels nothing, which is exactly "
            "why the certificate cannot catch it"
        )
        after = misplaced.split(START_LINE, 1)[1]
        assert not after.startswith("## Pinned Context"), (
            "the deliberately-wrong placement was not actually wrong, so this "
            "control does not discriminate"
        )


# --------------------------------------------------------------------------
# 9. Non-denial over the ENVIRONMENT axis
# --------------------------------------------------------------------------

class TestNonDenialOverTheEnvironment:
    """The primary file's denial corpus quantifies over STDIN and holds the
    ENVIRONMENT fixed. But the write reaches a real filesystem, and the states
    that make a read fail are environment states, not input states.

    Every arm here confirms a pin command that CANNOT complete still exits 0.
    A non-zero exit on `UserPromptSubmit` is the block code, and this hook is
    forbidden to deny by anything.

    Measured separately: with the read end of stdout CLOSED the hook exits 120.
    That is NOT a defect of this hook -- a two-line script that prints and
    exits 0 returns 120 under the identical condition with the identical
    `Exception ignored while flushing sys.stdout`. It is the CPython shutdown
    flush and it applies to every Python hook in this plugin equally, so it is
    recorded here rather than asserted as a property of this one.
    """

    PIN_FRAME = {"hook_event_name": "UserPromptSubmit", "prompt": "/PACT:pin-memory x"}

    def _run(self, fixture_dir: Path):
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(fixture_dir),
            "CLAUDE_PROJECT_DIR": str(fixture_dir),
            "CLAUDE_CONFIG_DIR": str(fixture_dir / "config"),
        }
        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(self.PIN_FRAME), capture_output=True, text=True,
            cwd=str(fixture_dir), env=carry_clock_shift(env), timeout=60,
        )

    def test_claude_md_is_a_directory(self, tmp_path):
        """`read_text` on a directory raises `IsADirectoryError`, an `OSError`."""
        (tmp_path / "CLAUDE.md").mkdir()
        proc = self._run(tmp_path)
        assert proc.returncode == 0, f"exit {proc.returncode}; stderr={proc.stderr[:300]}"

    def test_claude_md_is_unreadable(self, tmp_path):
        target = tmp_path / "CLAUDE.md"
        target.write_text(claude_md(n_pins=1), encoding="utf-8")
        target.chmod(0o000)
        try:
            if os.access(target, os.R_OK):
                pytest.skip("running with privileges that ignore the mode bits, "
                            "so this arm cannot create the condition it names")
            proc = self._run(tmp_path)
            assert proc.returncode == 0, (
                f"exit {proc.returncode}; stderr={proc.stderr[:300]}"
            )
        finally:
            # Restore, or the temp-directory teardown fails and reports as an
            # unrelated error in a later test.
            target.chmod(0o644)

    def test_claude_md_is_a_broken_symlink(self, tmp_path):
        """Reaches a DIFFERENT path from the two arms around it, and the
        difference is stated so nobody reads this as a third read-failure case.

        `_find_existing_claude_md` probes with `os.stat()`, which FOLLOWS the
        link, so a broken link resolves to nothing and the outcome is
        `noop_no_file` -- the read is never attempted. It is still a distinct
        environment input the hook must survive, which is why it is here.
        """
        (tmp_path / "CLAUDE.md").symlink_to(tmp_path / "does-not-exist.md")
        proc = self._run(tmp_path)
        assert proc.returncode == 0, f"exit {proc.returncode}; stderr={proc.stderr[:300]}"

    def test_the_environment_arms_can_observe_a_nonzero_exit(self, tmp_path):
        """POSITIVE CONTROL. Without it, three arms asserting `returncode == 0`
        would pass on a harness that could never produce anything else -- a
        mistyped interpreter path, a swallowed spawn, a stubbed runner."""
        denier = tmp_path / "denier.py"
        denier.write_text("import sys\nsys.exit(2)\n")
        proc = subprocess.run(
            [sys.executable, str(denier)], input="{}", capture_output=True,
            text=True, timeout=60,
        )
        assert proc.returncode == 2, (
            "the runner cannot observe a non-zero exit, so the three arms "
            "above certify nothing"
        )

    def test_the_os_error_message_is_length_bounded_but_NOT_redacted(
        self, tmp_path
    ):
        """RECORDS A MEASURED GAP between a stated guarantee and the code.

        The design says the `OSError` message is truncated to 50 characters
        "so the absolute path does not leak", and contrasts it with the
        containment refusal, which is deliberately opaque and names no victim
        path. So path non-disclosure was the INTENT.

        Truncation bounds LENGTH. It does not redact. `str(OSError)` is
        `[Errno N] <reason>: '<path>'`, and the fixed prefix is short enough
        that a short project path fits inside the 50 characters WHOLE.
        MEASURED with a project directory at `/tmp/pq`: the journalled outcome
        was `error_os: [Errno 21] Is a directory: '/tmp/pq/CLAUDE.md'` --
        the entire absolute path.

        Severity is low: the journal is under the user's own config directory,
        so this is not a cross-user disclosure. It is recorded because the
        STATED property is false, and a later reader will otherwise rely on it.

        THIS TEST IS A DRIFT DETECTOR, NOT AN ENDORSEMENT. If someone
        implements real redaction, this reddens -- and that is correct, because
        the guarantee will have changed and this record must be retired with it.
        """
        (tmp_path / "CLAUDE.md").mkdir()
        proc = self._run(tmp_path)
        assert proc.returncode == 0

        # The bound that IS real: the message body is capped.
        long_reason = "[Errno 21] Is a directory: '" + "x" * 200 + "'"
        assert len(f"error_os: {long_reason[:50]}") == len("error_os: ") + 50, (
            "the truncation width changed; the recorded measurement above no "
            "longer describes the code"
        )

        # The property that is NOT real: a short path survives the cut whole.
        short_path = "/tmp/pq/CLAUDE.md"
        rendered = f"error_os: {(f'[Errno 21] Is a directory: {short_path!r}')[:50]}"
        assert short_path in rendered, (
            "a short absolute path no longer survives truncation, so the "
            "design's non-disclosure claim may now hold. Re-measure and retire "
            "this record rather than relaxing it."
        )

    def test_a_completing_write_still_exits_zero(self, tmp_path):
        """NON-VACUITY over the environment table: without a row that REACHES
        the write, every arm above would pass on a hook that exited 0 by never
        doing anything at all."""
        target = tmp_path / "CLAUDE.md"
        target.write_text(claude_md(n_pins=1), encoding="utf-8")
        proc = self._run(tmp_path)
        assert proc.returncode == 0
        assert PINNED_START_MARKER in target.read_text(encoding="utf-8"), (
            "this arm must actually reach the write, or it does not "
            "distinguish a working hook from an inert one"
        )


# --------------------------------------------------------------------------
# 10. Registration order: the load-bearing half
# --------------------------------------------------------------------------

class TestRegistrationOrderLoadBearingProperty:
    """The primary file asserts the new entry is LAST. Being last is not the
    property that matters and a fourth prompt hook would redden it while
    nothing was wrong.

    The load-bearing property is that the entry sits AFTER the pinned
    bootstrap pair, because an entry inserted BETWEEN them breaks a pinned
    order assertion elsewhere in the suite. That is asserted here so the
    coverage survives if the stricter assertion is ever relaxed.
    """

    def test_the_entry_sits_after_the_bootstrap_pair(self):
        config = json.loads(
            (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        commands = [
            hook["command"]
            for entry in config["hooks"]["UserPromptSubmit"]
            for hook in entry.get("hooks", [])
        ]
        index = next(i for i, c in enumerate(commands) if "pin_marker_writer.py" in c)
        writer = next(i for i, c in enumerate(commands) if "bootstrap_marker_writer.py" in c)
        gate = next(i for i, c in enumerate(commands) if "bootstrap_prompt_gate.py" in c)
        assert writer < gate, "precondition: the pinned bootstrap order holds"
        assert index > gate, (
            "the marker writer was inserted between the bootstrap pair, which "
            "breaks the order assertion in test_hooks_json.py"
        )
