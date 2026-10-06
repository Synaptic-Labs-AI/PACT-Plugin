"""The two-rule gate repair: the same-slice decision and the conjunction.

WHAT THE TWO RULES ARE.
  RULE 1, the same-slice decision. The two sides of one comparison are counted
  across ONE slice. A difference taken across two different slices is not a
  comparison, and that defect is the STRADDLE.
  RULE 2, the conjunction predicate. A heading that is date-led AND carries no
  `<!-- pinned: -->` marker is a memory entry rather than a pin.

🔴 THE TWO ARE COUPLED WHERE THE COUNT BOUND DECLINES, AND THE ARMS BELOW
MEASURE THE COUPLING. The whole-text fallback of Rule 1 KEEPS the memory
entries, and Rule 2 is what drops them. Where the two sides resolve a
pinned span, the count bound alone holds a memory write quiet. The ablation
class at the end is what shows each rule earns its place IN THE PRESENCE OF
the other, which a mutation of one rule alone cannot show.
"""
import re
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "hooks"

from shared.claude_md_manager import (  # noqa: E402
    MANAGED_START_MARKER,
    MANAGED_END_MARKER,
)

PIN_A = "<!-- pinned: 2026-08-01 -->\n### Merge guard purpose\nbody a\n"
PIN_B = "<!-- pinned: 2026-08-02 -->\n### Platform constraint\nbody b\n"
PIN_C = "<!-- pinned: 2026-08-03 -->\n### Version discipline\nbody c\n"
PIN_D = "<!-- pinned: 2026-08-04 -->\n### Count parameter\nbody d\n"
PIN_E = "<!-- pinned: 2026-08-05 -->\n### Fifth pin\nbody e\n"
# A pin the curator titled with a bare date. It CARRIES its marker, so the
# conjunction keeps it. A date-only rule would drop it, which is the mutation
# the E2 arm below kills.
PIN_DATE_TITLED = "<!-- pinned: 2026-08-06 -->\n### 2026-08-06\nbody f\n"
MEM_1 = "### 2026-08-11 19:11\n**Context**: one\n"
MEM_2 = "### 2026-08-11 20:02\n**Context**: two\n"
# Content OUTSIDE the managed region. Without a `### ` heading here the two
# counter branches agree BY CONSTRUCTION and the straddle cases cannot bite.
# That vacuity cost the design one whole run, so this file asserts it.
OUT_OF_REGION = "## User notes\n\n### My own heading\nprose the user wrote\n"
# The same out-of-region content with NO `### ` heading in it. The PAIR of
# fixtures is what lets a case move the WHOLE-TEXT count while it leaves the
# MANAGED-REGION count alone. One fixture cannot do that, because the two sides
# then carry the same outside content and the two slices agree by construction.
OUT_OF_REGION_PLAIN = "## User notes\n\nprose the user wrote\n"


def managed(pins, mems=(), outside=""):
    """A document that CARRIES the managed markers."""
    return (
        "# Project Memory\n\n"
        f"{outside}"
        f"{MANAGED_START_MARKER}\n"
        "## Pinned Context\n\n"
        f"{''.join(pins)}"
        "\n## Working Memory\n\n"
        f"{''.join(mems)}"
        f"{MANAGED_END_MARKER}\n"
    )


def unmanaged(pins, mems=(), outside=""):
    """The same content with NO managed markers, which is the other branch."""
    return (
        "# Project Memory\n\n"
        f"{outside}"
        "## Pinned Context\n\n"
        f"{''.join(pins)}"
        "\n## Working Memory\n\n"
        f"{''.join(mems)}"
    )


def pinned_document(body=""):
    """A document that CARRIES a `## Pinned Context` heading.

    THE SLICE THIS SELECTS IS THE PINNED SECTION, because `_counts_show_an_add`
    step 0 resolves that span on the two sides. So a count change INSIDE the
    section reaches the comparison and a change below `## Working Memory` does
    not. Use this shape for a claim about pins.
    """
    return (
        "# Project Memory\n\n"
        f"{MANAGED_START_MARKER}\n"
        "## Pinned Context\n\n"
        f"{body}"
        "\n## Working Memory\n\n"
        f"{MANAGED_END_MARKER}\n"
    )


def working_memory_document(body=""):
    """A document with a `## Working Memory` heading and NO pinned heading.

    🔴 THIS SHAPE IS WHAT MAKES RULE 2 MEASURABLE, AND THE OTHER SHAPE HIDES IT.
    Step 0 needs a `## Pinned Context` span on the two sides. With no such
    heading it DECLINES, the slice widens to the managed region, and the memory
    entries enter the count. `_is_memory_entry` is then the only thing that
    keeps the gate quiet, so removing it changes the verdict.

    MEASURED, one memory-entry add driven through the two shapes:
      pinned shape, shipped rule   counts 0 -> 0, quiet
      pinned shape, rule 2 ABLATED counts 0 -> 0, quiet    <- the arm says nothing
      this shape,   shipped rule   counts 0 -> 0, quiet
      this shape,   rule 2 ABLATED counts 0 -> 1, FIRES    <- the arm bites
    An arm for Rule 2 built on the pinned shape is quiet because of the SLICE
    BOUND rather than because of the rule, so it passes whatever Rule 2 does.

    THE SHAPE IS SHIPPED RATHER THAN CONTRIVED. A project file carries it
    before any pin is added, and a machine writer reaches it with no human in
    the route. It was confirmed against a live project file, which was READ for
    its shape and never used as a fixture.
    """
    return (
        "# Project Memory\n\n"
        f"{MANAGED_START_MARKER}\n"
        "## Working Memory\n\n"
        f"{body}"
        f"{MANAGED_END_MARKER}\n"
    )


def verdict_by_slice(gate, old, new, slice_name):
    """Count the two sides across ONE named slice and report the verdict.

    It reproduces what each branch of the selection would decide, so an arm can
    assert that the two branches DISAGREE on a fixture pair. An arm for a branch
    is vacuous while the two branches agree, and this is what measures that.
    """
    from shared.claude_md_manager import extract_managed_region

    if slice_name == "region":
        old_body = extract_managed_region(old)[0]
        new_body = extract_managed_region(new)[0]
    else:
        old_body, new_body = old, new
    return _count(gate, new_body) > _count(gate, old_body)


def _count(gate, text):
    """The gate's pin count over every row of `text` parsed on its own: the
    tests below count a chosen slice, which the gate itself never does."""
    from shared.claude_md_markers import parse

    doc = parse(text)
    return gate._count_pin_comments(doc, 0, len(doc.lines) - 1)


def fires(gate, old, new, tool="Edit", tmp_path=None, document=None):
    """Drive the shipped decision. True means the edit of a user is DENIED.

    🔴 THE EDIT BRANCH READS A FILE, SO EVERY EDIT CASE MUST SUPPLY THE
    DOCUMENT ITS FRAGMENT LIVES IN. The decision builds the post-edit document
    and compares it against the current one. THREE PATHS RETURN THE QUIET VALUE
    WITHOUT EVER COMPARING ANYTHING, and a quiet-asserting arm goes green on
    each of them:
      1. THE READ FAILS. A path that does not exist raises, the catch returns
         the quiet value, and the count never runs.
      2. THE ANCHOR IS ABSENT. `str.replace` is then a no-op, the two documents
         are equal, and the comparison is 0 against 0 whatever the gate does.
      3. THE COUNT SLICE EXCLUDES THE CHANGE. Content added below
         `## Working Memory` moves no count while a pinned span is resolved.
    Path 1 was live in this file and it VACATED 16 rows: they passed, and the
    comparison each one is named for never ran. A failure count is therefore a
    LOWER BOUND on the rows a change reached, and not a measure of it.

    THE TWO GUARDS BELOW CLOSE PATHS 1 AND 2 AT THE FIXTURE RATHER THAN AT THE
    VERDICT, which is where the defect is. Path 3 is closed per group, by the
    CHOICE OF DOCUMENT: see `pinned_document` and `working_memory_document`.
    """
    if tool == "Write":
        target = tmp_path / "CLAUDE.md"
        target.write_text(old, encoding="utf-8")
        return gate._is_add_shaped_edit({"content": new}, target, "Write")

    assert document is not None, (
        "the Edit branch reads a file, so this case must name the document its "
        "fragment lives in; a case with no document cannot reach the comparison"
    )
    occurrences = document.count(old)
    assert occurrences == 1, (
        f"THE FIXTURE CANNOT CARRY THIS ASSERTION.\n"
        f"  the old_string occurs {occurrences} times in the document, not 1\n"
        f"With no occurrence the edit is a NO-OP, the two documents are equal, "
        f"and the arm goes green whatever the gate decides. With more than one "
        f"the platform refuses the edit, so the gate judges a document that is "
        f"never produced."
    )
    assert document.replace(old, new, 1) != document, (
        "the payload leaves the document unchanged, so the comparison is the "
        "document against itself and this arm cannot separate any verdict"
    )

    target = tmp_path / "CLAUDE.md"
    target.write_text(document, encoding="utf-8")
    return gate._is_add_shaped_edit(
        {"old_string": old, "new_string": new}, target, "Edit"
    )


@pytest.fixture
def gate():
    import pin_staleness_gate
    return pin_staleness_gate


# =========================================================================
# The fixtures must be able to bite. This is the vacuity gate the design
# lost a run to.
# =========================================================================

class TestTheStraddleFixturesCanBite:
    def test_out_of_region_content_carries_a_pin_shaped_heading(self):
        """NON-VACUITY, AND IT IS THE ONE THE DESIGN LEARNED THE HARD WAY.

        The two counter branches differ ONLY in what they exclude. A document
        with nothing outside the managed region makes the whole-text count and
        the managed-region count agree by construction, so a straddle case
        passes for a reason that has nothing to do with the repair.
        """
        assert re.search(r"^### ", OUT_OF_REGION, re.M), (
            "the out-of-region fixture carries no `### ` heading, so the two "
            "slices agree by construction and each straddle arm below is "
            "vacuous"
        )

    def test_the_two_slices_disagree_on_the_straddle_fixture(self, gate):
        """The fixture separates the two branches. Measured, not assumed."""
        doc = managed([PIN_A], outside=OUT_OF_REGION)
        from shared.claude_md_manager import extract_managed_region
        whole = _count(gate, doc)
        region = _count(gate, extract_managed_region(doc)[0])
        assert whole != region, (
            f"whole-text count {whole} equals managed-region count {region}, "
            f"so this fixture cannot exhibit a straddle"
        )


# =========================================================================
# RULE 1, the same-slice decision.
# =========================================================================

class TestRule1TheDecisionOwnsTheSlice:
    def test_s1_a_straddle_that_moves_no_pin_stays_quiet(self, gate, tmp_path):
        """S1. The two sides reach different branches and NO pin moved.

        Before Rule 1 this OVER-BLOCKED: one side counted the managed region,
        the other counted the whole text, and the difference measured the
        slice rather than the pins.
        """
        old = managed([PIN_A, PIN_B], outside=OUT_OF_REGION)
        new = unmanaged([PIN_A, PIN_B], outside=OUT_OF_REGION)
        assert fires(gate, old, new, "Write", tmp_path) is False

    def test_s2_a_straddle_that_adds_a_pin_fires(self, gate, tmp_path):
        """S2. A TRUE add of four pins to five, across a straddle.

        🔴 THIS IS THE UNDER-BLOCK, and it is the direction this arc had not
        seen before. Today's code returns quiet on a real add. An arm that
        checks only that memory writes stopped over-blocking cannot see it.
        """
        old = unmanaged([PIN_A, PIN_B, PIN_C, PIN_D], outside=OUT_OF_REGION)
        new = managed(
            [PIN_A, PIN_B, PIN_C, PIN_D, PIN_E], outside=OUT_OF_REGION
        )
        assert fires(gate, old, new, "Write", tmp_path) is True

    def test_s3_control_out_of_region_content_and_no_straddle(
        self, gate, tmp_path
    ):
        """S3. Out-of-region content on the two sides and NO straddle.

        It separates a straddle repair from a repair that suppresses the gate.
        A change that makes the gate quiet everywhere passes S1 and fails S2,
        and this arm holds the quiet side honest.
        """
        old = managed([PIN_A, PIN_B], outside=OUT_OF_REGION)
        new = managed([PIN_A, PIN_B], mems=[MEM_1], outside=OUT_OF_REGION)
        assert fires(gate, old, new, "Write", tmp_path) is False


class TestRule1TheManagedBranchEarnsItsPlace:
    """THE BRANCH THAT TAKES THE MANAGED REGION, BOUNDED IN THE TWO DIRECTIONS.

    THE GAP THIS CLOSES WAS MEASURED RATHER THAN REASONED. The cases above
    either reach the whole-text fallback, or they reach the managed branch with
    the SAME out-of-region content on the two sides. In that second shape the
    slices count 2 against 2 inside the region and 3 against 3 across the whole
    text, so either slice returns the same verdict. The branch can then be made
    dead and 47 of 47 arms stay green.

    THE SEPARATING SHAPE IS THE ONE WHERE THE TWO SLICES DISAGREE. The managed
    region is a SUBSET of the whole text, so a change INSIDE the region moves
    the two counts together. Only a change OUTSIDE the region moves one count
    and leaves the other, so the out-of-region content must DIFFER across the
    two sides while the two sides are both managed.

    THE REMOVAL FAILS IN THE TWO DIRECTIONS AND ONE ARM BOUNDS ONE OF THEM.
    Without the branch, a user who adds a heading to their own notes is DENIED,
    and a user who adds a true pin while a heading leaves their notes is
    ALLOWED. An arm for the first direction alone leaves the second open, so
    there are two arms here and not one.
    """

    OUTSIDE_GAINS_A_HEADING = (
        (OUT_OF_REGION_PLAIN, OUT_OF_REGION),
        ([PIN_A, PIN_B], [PIN_A, PIN_B]),
    )
    A_PIN_LANDS_WHILE_THE_OUTSIDE_LOSES_ONE = (
        (OUT_OF_REGION, OUT_OF_REGION_PLAIN),
        ([PIN_A, PIN_B], [PIN_A, PIN_B, PIN_C]),
    )

    @staticmethod
    def _pair(case):
        (out_old, out_new), (pins_old, pins_new) = case
        return (
            managed(pins_old, outside=out_old),
            managed(pins_new, outside=out_new),
        )

    def test_a_heading_added_outside_the_managed_region_stays_quiet(
        self, gate, tmp_path
    ):
        """The managed region does not move. The user edits their own notes.

        A DENY here is an over-block on content the gate has no claim over,
        which is the cardinal direction for this repository.
        """
        old, new = self._pair(self.OUTSIDE_GAINS_A_HEADING)
        assert fires(gate, old, new, "Write", tmp_path) is False, (
            "a heading the user added OUTSIDE the managed region was read as a "
            "pin add, so the decision counted across the whole text while the "
            "two sides both carry the managed markers"
        )

    def test_a_pin_added_inside_fires_while_the_outside_loses_a_heading(
        self, gate, tmp_path
    ):
        """A TRUE pin add, with the whole-text count held level by the outside.

        THIS IS THE OTHER FAILURE DIRECTION OF THE SAME BRANCH. Across the whole
        text the counts are 3 against 3, so a decision that reached the whole
        text would go quiet on a real add. The arm above cannot see that.
        """
        old, new = self._pair(self.A_PIN_LANDS_WHILE_THE_OUTSIDE_LOSES_ONE)
        assert fires(gate, old, new, "Write", tmp_path) is True, (
            "a true pin add inside the managed region went unreported, because "
            "a heading left the user's own notes in the same edit and the two "
            "movements cancel in a whole-text count"
        )

    @pytest.mark.parametrize(
        "case_name",
        ["OUTSIDE_GAINS_A_HEADING", "A_PIN_LANDS_WHILE_THE_OUTSIDE_LOSES_ONE"],
    )
    def test_the_two_slices_disagree_on_each_fixture_pair(self, gate, case_name):
        """NON-VACUITY, AND THE TWO ARMS ABOVE ARE EVIDENCE ONLY WITH THIS.

        An arm for a branch says nothing while the branch it selects returns
        what the other branch returns. This measures the two verdicts and
        asserts they DIFFER, so a fixture that stops separating the branches
        reddens HERE rather than passing in silence up there.

        It also asserts the PRECONDITION: the two sides must carry the managed
        markers, or the pair never reaches the branch under test and the arms
        above pass from the fallback.
        """
        from shared.claude_md_manager import extract_managed_region

        old, new = self._pair(getattr(self, case_name))

        assert extract_managed_region(old) is not None, (
            "the OLD side carries no managed markers, so this pair takes the "
            "whole-text fallback and says nothing about the managed branch"
        )
        assert extract_managed_region(new) is not None, (
            "the NEW side carries no managed markers, so this pair takes the "
            "whole-text fallback and says nothing about the managed branch"
        )

        region = verdict_by_slice(gate, old, new, "region")
        whole = verdict_by_slice(gate, old, new, "whole")
        assert region != whole, (
            f"the managed-region slice and the whole-text slice AGREE on this "
            f"pair (both {region}), so either slice gives the same answer and "
            f"the arm built on it cannot separate the two branches"
        )


# =========================================================================
# RULE 2, the conjunction predicate.
# =========================================================================

class TestEachEditGroupRecordsWhatWasCompared:
    """🔴 A GREEN LINE IS NOT EVIDENCE. THIS CLASS IS THE EVIDENCE.

    An Edit arm can go green from at least three places that the suite output
    cannot separate: a failed read, a payload whose anchor the document lacks,
    and a count slice that excludes the change. A quiet arm that took any of
    those passes exactly like a quiet arm that ran the comparison and found no
    add.

    SO EACH GROUP RECORDS WHAT ITS DOCUMENT PUTS IN FRONT OF THE COUNT. These
    two arms assert the SHAPE OF THE COMPARISON rather than the verdict, so a
    document that stops reaching the count reddens HERE, with a message naming
    the cause, rather than passing in silence in the classes below.
    """

    def test_the_pinned_group_puts_its_change_inside_the_counted_span(self, gate):
        """The pinned document resolves a span, and the added pin is IN it."""
        from staleness import _parse_pinned_section

        document = pinned_document()
        old = "## Working Memory\n"
        new = f"{PIN_A}\n## Working Memory\n"
        simulated = document.replace(old, new, 1)
        assert simulated != document, "the payload is a no-op on this document"

        current_span = _parse_pinned_section(document, allow_empty_section=True)
        post_span = _parse_pinned_section(simulated, allow_empty_section=True)
        assert current_span is not None and post_span is not None, (
            "one side resolves no pinned section, so the decision does not take "
            "the span bound and the pinned group is counting a wider slice than "
            "its arms claim"
        )
        assert _count(gate, current_span[2]) == 0
        assert _count(gate, post_span[2]) == 1, (
            "the added pin is not inside the counted span, so an arm in the "
            "pinned group would be quiet for the span bound rather than for "
            "the verdict it names"
        )

    def test_the_working_memory_group_counts_the_entries_it_is_named_for(
        self, gate, monkeypatch
    ):
        """The other document declines the span, so the entries ARE counted.

        THIS IS THE SEPARATION RULE 2 NEEDS, RECORDED AS TWO COUNTS OF ONE
        SLICE. The same bytes count 0 with the conjunction and 1 without it. On
        the pinned document they count 0 either way, and an arm built there
        says nothing about the rule.
        """
        from staleness import _parse_pinned_section
        from shared.claude_md_manager import extract_managed_region

        document = working_memory_document()
        old = "## Working Memory\n\n"
        new = f"## Working Memory\n\n{MEM_1}"
        simulated = document.replace(old, new, 1)
        assert simulated != document, "the payload is a no-op on this document"

        assert _parse_pinned_section(document, allow_empty_section=True) is None, (
            "this document resolves a pinned section, so the count narrows to "
            "that span, the memory entry falls outside it, and the Rule 2 arms "
            "built on this document pass whatever Rule 2 does"
        )
        region = extract_managed_region(simulated)
        assert region is not None and MEM_1 in region[0], (
            "the memory entry is not inside the managed region, so it is not "
            "in the slice the count reads"
        )
        assert _count(gate, region[0]) == 0

        monkeypatch.setattr(gate, "_is_memory_entry", lambda pin: False)
        assert _count(gate, region[0]) == 1, (
            "the SAME slice counts the same with the conjunction removed, so "
            "the conjunction is not what holds this document quiet and the "
            "ablation below cannot measure it"
        )

class TestRule2TheConjunction:
    """🔴 THE TWO GROUPS BELOW TAKE DIFFERENT DOCUMENTS, AND THAT IS THE REPAIR.

    A claim about PINS needs a document whose pinned span the count reaches, so
    those cases take `pinned_document`. A claim about MEMORY ENTRIES needs a
    document where the entries are INSIDE the counted slice, and the pinned
    shape excludes them by its span bound, so those cases take
    `working_memory_document`.

    ONE DOCUMENT FOR EVERY CASE IS NOT A REPAIR, IT IS A BROADCAST. Measured:
    the pinned shape returns quiet for a memory-entry add whether Rule 2 is
    present or ablated, so a Rule 2 arm on that shape passes for the slice
    bound rather than for the rule.
    """

    def test_e0_adding_a_memory_entry_stays_quiet(self, gate, tmp_path):
        """E0. The measured defect: a memory write read as a pin add.

        THE DOCUMENT HAS NO PINNED HEADING ON PURPOSE. That is what puts the
        memory entry inside the counted slice, so this arm reddens when the
        date-led exclusion is removed. See `working_memory_document`.
        """
        assert fires(gate, "## Working Memory\n\n",
                     f"## Working Memory\n\n{MEM_1}",
                     tmp_path=tmp_path,
                     document=working_memory_document()) is False

    def test_e2_a_marked_pin_titled_a_bare_date_fires(self, gate, tmp_path):
        """E2. THE ARM THAT KILLS THE DATE-ONLY RULE.

        The heading is a bare date AND the entry carries its marker, so it is
        a pin. A predicate that read the heading alone would drop it and the
        gate would go quiet on a real add.
        """
        assert fires(gate, "## Working Memory\n",
                     f"{PIN_DATE_TITLED}\n## Working Memory\n",
                     tmp_path=tmp_path,
                     document=pinned_document()) is True

    def test_e4_archiving_a_pin_while_memory_lands_stays_quiet(
        self, gate, tmp_path
    ):
        """E4. The archive case, which is the escape from a full pin cap.

        An over-block here is the livelock the whole gate exists to prevent.

        THE DOCUMENT HAS NO PINNED HEADING, for the reason E0 gives: the two
        memory entries must be inside the counted slice, or the arm passes
        whether Rule 2 holds or not. Measured on this shape, the count runs
        1 against 0 with the rule and 1 against 2 without it.
        """
        assert fires(gate, PIN_A, f"{MEM_1}{MEM_2}",
                     tmp_path=tmp_path,
                     document=working_memory_document(body=PIN_A)) is False

    def test_e1_adding_a_real_pin_through_the_edit_path_fires(
        self, gate, tmp_path
    ):
        """E1. THE ARM THAT M2 MUST REDDEN.

        A suite that checks only that memory writes stopped over-blocking
        PASSES when the Edit path is replaced with a plain 0, because that
        stops the gate firing at all. This arm is what separates a repaired
        gate from a retired one.
        """
        assert fires(gate, "## Working Memory\n",
                     f"{PIN_A}\n## Working Memory\n",
                     tmp_path=tmp_path,
                     document=pinned_document()) is True


class TestRule2KnownResidual:
    def test_r2_an_edit_that_adds_a_missing_marker_fires(self, gate, tmp_path):
        """R2, A KNOWN AND ACCEPTED RESIDUAL, PINNED SO IT CANNOT MOVE UNSEEN.

        An edit that adds a missing marker to a date-titled pin moves NO pin,
        and this gate FIRES. That is a cardinal over-block.

        WHY IT SHIPS: its trigger is one population, a date-titled pin with no
        marker, and a user ruling declares that population empty. THE ARM DOES
        NOT ENDORSE THE BEHAVIOUR. It records the accepted state so a later
        change that closes R2, or that widens it, shows up as a diff here
        rather than passing in silence.
        """
        old = "### 2026-08-06\nbody f\n"
        new = PIN_DATE_TITLED
        assert fires(gate, old, new, tmp_path=tmp_path,
                     document=pinned_document(body=old)) is True


# =========================================================================
# THE HEADING CLASS REACHES THE VERDICT. THE POPULATION BELOW IS DERIVED.
#
# THE GUARDED THING is the verdict `count(new) > count(old)`. A heading adds 1
# to a count when it is a PIN and 0 when it is a MEMORY ENTRY. So the population
# is the cross product of TWO structural axes, and not a set of examples.
#   AXIS A, THE EDIT SHAPE: a heading is added, removed, renamed, or unchanged.
#   AXIS B, THE TITLE, whose dimensions are read off the heading a WRITER emits
#   by decomposition: the token count after the date, a time present or absent,
#   the whitespace run, and a date present or absent.
#
# WHICH COMBINATIONS MATTER WAS MEASURED AND NOT REASONED. For each combination
# the verdict was computed two times, once with the shipped classifier and once
# with a classifier that ALSO calls that title a memory entry. That second form
# stands for EVERY growth of the rule that reaches the title, so the result does
# not depend on a mutation somebody chose.
#
# EXACTLY TWO SHAPES CHANGE THE VERDICT, AND THE OTHER THREE DO NOT.
#   ADD, and the added title becomes a memory entry: the gate goes QUIET on a
#   true add. That is an under-block.
#   RENAME, and the OLD title becomes a memory entry: the old count FALLS and
#   the gate DENIES. That is an over-block, the cardinal direction.
#   REMOVE, RENAME where the NEW title moves, and an UNCHANGED heading were each
#   measured and NONE changes the verdict. DO NOT ADD ARMS FOR THEM. They would
#   pass for a reason that has nothing to do with the rule.
#
# THE BOUND ON THIS POPULATION, STATED SO A CLOSED AXIS IS NOT READ AS A CLOSED
# ALPHABET. `_is_memory_entry` is a CONJUNCTION of a date-led heading AND no
# marker, and the slice selection is a third input. Measured: the same heading
# counts 1 with a marker and 0 without one. So the two axes above close the
# HEADING axis of the verdict and NOT the verdict.
# =========================================================================

# The titles that can move class. Each one walks ONE dimension to a boundary
# value. The one-token entry is the boundary a blind pick reached when this
# population was three multi-word examples.
_WRITER_DERIVED_TITLES = (
    ("one token after the date", "### 2026-08-05 Draft"),
    ("two tokens after the date", "### 2026-08-05 Draft notes"),
    ("three tokens after the date", "### 2026-08-05 Merge guard purpose"),
    ("a time and one token", "### 2026-08-05 12:30 Draft"),
    ("a double whitespace run", "###  2026-08-05 Draft notes"),
    ("no date, one token", "### Draft"),
    ("no date, two tokens", "### Draft notes"),
)

# 🔴 THE SEPARATOR FAMILY, AND ITS SOURCE IS A HUMAN CORPUS RATHER THAN A
# WRITER. The set above reads its dimensions off the heading a WRITER emits.
# A writer emits ONE separator between the date and the words, a plain space,
# so a set derived that way cannot see a separator no writer produces. The gate
# reads a file a person also types, and a person types a colon after a date
# without a thought.
#
# THE SOURCE, WITH ITS COUNTING RULE BESIDE IT. The `### ` headings inside the
# four CLAUDE.md files this gate reads. MEASURED: 23 headings, of which 3 are
# date-led, and ALL THREE ARE WRITER OUTPUT. So that corpus holds NO
# human-typed date-led title and CANNOT answer the separator question directly.
# It answers the question it can: the punctuation a person types inside the 20
# curated pin titles. Counted there, one heading is one sample: hyphen 23,
# long dash 8, backtick 6, colon 5, period 4, plus 3, comma 2, underscore 2,
# semicolon 1, slash 1.
#
# THE CLOSURE RULE APPLIED TO THAT COUNT, so a later editor can check the set
# rather than add a line to it: take each punctuation mark the count observed,
# and drop the three the corpus shows only INSIDE a word or as markup, which
# are the backtick, the underscore and the plus. Seven remain, and they are the
# seven below.
#
# 🔴 THE SET IS WIDER THAN THE MUTATION THAT FOUND THE GAP, ON PURPOSE. The
# mutation that exposed this axis admitted a colon, a comma and a hyphen. A set
# cut to those three would be derived from the MUTANT, which is this defect one
# level along. The source is the corpus, so the semicolon, the period, the
# slash and the long dash are here too and no mutation to date reaches them.
#
# EACH ONE WAS DRIVEN THROUGH THE SHIPPED PREDICATE BEFORE IT ENTERED. All
# seven behave alike today: counted as a pin, not date-led, quiet on a rename,
# and firing on an add. A separator that behaved differently would make these
# arms red on a correct tree, so the family was measured rather than assumed.
_SEPARATOR_TITLES = tuple(
    (f"a {name} after the date", f"### 2026-08-05{char} Draft notes")
    for name, char in (
        ("colon", ":"),
        ("comma", ","),
        ("semicolon", ";"),
        ("period", "."),
        ("hyphen", "-"),
        ("slash", "/"),
        ("long dash", "—"),
    )
)

CLASS_MOVING_TITLES = _WRITER_DERIVED_TITLES + _SEPARATOR_TITLES
_TITLE_IDS = [name for name, _ in CLASS_MOVING_TITLES]
_TITLES = [title for _, title in CLASS_MOVING_TITLES]
REPLACEMENT_TITLE = "### Some other title"


class TestTheClassMovingPopulationCanMove:
    """NON-VACUITY ON THE POPULATION, AND THE TWO CLASSES BELOW NEED IT.

    Each arm below rests on a title that the shipped rule counts as a PIN today
    and that a grown rule can re-read as a memory entry. A title the shipped
    rule ALREADY calls a memory entry cannot move, so an arm built on it passes
    whatever the rule does. This measures the precondition rather than assume
    it.
    """

    @pytest.mark.parametrize("title", _TITLES, ids=_TITLE_IDS)
    def test_the_shipped_rule_counts_this_title_as_a_pin(self, gate, title):
        assert _count(gate, f"{title}\nbody\n") == 1, (
            f"the shipped rule does NOT count {title!r} as a pin, so it cannot "
            f"move class and each arm built on it says nothing about the rule"
        )


class TestRule2TheAddDirection:
    """A TRUE ADD MUST BE REPORTED, ACROSS THE WHOLE TITLE POPULATION.

    THIS IS THE SECOND SHAPE OF ONE MECHANISM and it was missing. A grown rule
    re-reads the ADDED title as a memory entry, the new count does not rise, and
    the gate says nothing where a pin arrived. That is an under-block, and an
    arm on the rename shape alone cannot see it, because a rename moves no count
    on the shipped tree.

    THE TWO CLASSES GUARD EACH OTHER, WHICH IS WHY NEITHER NEEDS A SEPARATE
    LIVENESS CONTROL. This class asserts the gate FIRES, so a gate that stopped
    firing at all reddens here. The rename class asserts the gate stays QUIET,
    so a gate that fires at everything reddens there.

    🔴 THE LIMIT OF THIS CLASS, STATED BECAUSE THE CONSEQUENCE IS NOT VISIBLE
    FROM A CASE. Each case starts from a document that holds ZERO pins, and a
    reader can see that much. WHAT A READER CANNOT SEE IS WHAT IT COSTS: this
    class holds the under-block direction at that ONE starting count and is
    unmeasured at each other one. A comparison that SCALES the old count rather
    than shifts it leaves each case here green, because zero multiplied by a
    number is zero, and the same comparison goes quiet on a user who holds
    three pins and adds a fourth. THAT CASE IS NOT COVERED HERE.
    """

    @pytest.mark.parametrize("title", _TITLES, ids=_TITLE_IDS)
    def test_an_added_pin_is_reported(self, gate, title, tmp_path):
        old = "## Working Memory\n"
        new = f"{title}\nbody\n## Working Memory\n"
        assert fires(gate, old, new, tmp_path=tmp_path,
                     document=pinned_document()) is True, (
            f"A TRUE PIN ADD WENT UNREPORTED.\n"
            f"  added title: {title!r}\n"
            f"\n"
            f"WHAT PRODUCES THIS. The date-led rule grew until it re-reads this "
            f"title as a memory entry. The title then adds nothing to the new "
            f"count, the counts agree, and the gate stays quiet while a pin "
            f"landed.\n"
            f"\n"
            f"THIS IS AN UNDER-BLOCK. The user passes the pin cap unseen. The "
            f"rule belongs to the gate. Do not repair it here."
        )


class TestRule2TheRenameDirection:
    """A FAITHFUL RENAME OF A PIN MUST NOT BE DENIED, MEASURED AT THE COUNT.

    WHY THIS SITS HERE AND NOT WITH THE WRITERS. The date-led rule is read on
    TWO surfaces. A PREDICATE reads one heading and answers whether it is a
    memory entry. A COMPARISON reads an OLD text and a NEW text and answers
    whether a pin arrived. A rule that grows fails differently on the two, and
    an arm on the predicate cannot reach the comparison, because the comparison
    needs two documents and a predicate arm has one heading. The predicate side
    is bounded beside the writers. THIS IS THE COMPARISON SIDE.

    THE SHAPE, AND IT IS AN ORDINARY EDIT RATHER THAN AN ADVERSARIAL ONE. A user
    renames a pin and drops the date from its title. No marker is present on
    either side, because that pin was never marked. The shipped rule refuses a
    date FOLLOWED BY A TITLE, so the old title and the new title are counted
    alike and the gate stays quiet.

    GROW THE RULE AT ITS TAIL AND THE OLD TITLE BECOMES A MEMORY ENTRY. The OLD
    count FALLS, the new count is then the greater, and the decision reports an
    add where the user removed a date. A faithful rename is DENIED. That is an
    over-block, which this repository treats as the cardinal direction, and it
    is invisible to an arm that reads one heading.

    🔴 THE QUIET ARMS ARE EVIDENCE ONLY BECAUSE OF THE CONTROL BELOW. An assert
    of False passes for many reasons, and a decision that returned False for
    everything would satisfy each quiet arm here forever. The control fires on a
    true add carrying the same date-prefixed title, so a retired decision shows
    up in this class rather than in another file. The ADD class above supplies
    the same guarantee across the whole population.

    🔴 THE CASES COME FROM `CLASS_MOVING_TITLES` ABOVE AND NOT FROM AN EXAMPLE.
    This class held three titles before, each of them MULTI-WORD, because they
    were parametrized around one pair handed over in a report. A blind pick
    then grew the rule so it admitted ONE token, and each of the three refused
    anyway, so the arm passed while the over-block was live. The population is
    now walked to the boundary of each dimension, and the one-token title is
    that boundary.
    """

    @pytest.mark.parametrize("old_title", _TITLES, ids=_TITLE_IDS)
    def test_a_rename_of_a_pin_stays_quiet(self, gate, old_title, tmp_path):
        new_title = REPLACEMENT_TITLE
        old = f"{old_title}\nbody\n"
        new = f"{new_title}\nbody\n"
        assert fires(gate, old, new, tmp_path=tmp_path,
                     document=pinned_document(body=old)) is False, (
            f"THE GATE DENIED A FAITHFUL RENAME.\n"
            f"  old title: {old_title!r}\n"
            f"  new title: {new_title!r}\n"
            f"No pin arrived. The user removed a date from a title.\n"
            f"\n"
            f"WHAT PRODUCES THIS. The date-led rule grew at its tail, so it now "
            f"accepts a date FOLLOWED BY A TITLE. The OLD title is then read as "
            f"a memory entry and drops out of the old count, the new count "
            f"becomes the greater, and the comparison reports an add.\n"
            f"\n"
            f"THIS IS AN OVER-BLOCK AND IT IS THE CARDINAL DIRECTION. The user "
            f"cannot edit Pinned Context and cannot see why. Do not repair it "
            f"here. The rule belongs to the gate, and the tail anchor is what "
            f"holds it."
        )

    def test_a_true_add_beside_a_date_prefixed_title_fires(self, gate, tmp_path):
        """POSITIVE CONTROL ON THE DECISION, IN THIS CLASS RATHER THAN ELSEWHERE.

        It carries the SAME date-prefixed title as the quiet arms and adds a
        second heading beside it. A decision that stopped firing at all passes
        each quiet arm above and fails here.
        """
        old = "### 2026-08-05 Draft notes\nbody\n"
        new = "### 2026-08-05 Draft notes\nbody\n### Second pin\nbody\n"
        assert fires(gate, old, new, tmp_path=tmp_path,
                     document=pinned_document(body=old)) is True, (
            "the decision went quiet on a true add, so the quiet arms above "
            "prove nothing: they pass for a decision that reports no add ever"
        )


# =========================================================================
# RULE 4, the tool name.
# =========================================================================

class TestRule4TheToolNameDecidesTheBranch:
    def test_an_edit_payload_carrying_a_content_key_takes_the_edit_branch(
        self, gate, tmp_path
    ):
        """Rule 4. The branch asks the TOOL rather than a payload key.

        An Edit payload that happens to carry a `content` key took the
        whole-document branch before this, which read the `content` value as
        the whole post-edit document rather than applying the Edit.

        🔴 THE PAYLOAD CHANGED AND THE CLAIM DID NOT. This case used to name a
        path that does not exist, because the Edit branch once compared the two
        FRAGMENTS and never opened a file. That contract is retired: the branch
        now reads the current document and simulates the edit, so an absent
        path sends the case out through the failed-read exit and the arm says
        nothing about which branch ran. The document below is what lets the
        assertion be carried, and the assertion itself is unchanged.

        THE TWO BRANCHES RETURN OPPOSITE VERDICTS ON THIS ONE PAYLOAD, WHICH IS
        WHAT MAKES THE ARM SEPARATE THEM. Measured:
          Edit branch, the strings applied to the document   counts 0 -> 1, FIRES
          Write branch, the `content` value as the document  counts 0 -> 0, quiet
        So a decision that reads the payload key rather than the tool goes
        quiet here, and this arm reddens.
        """
        document = pinned_document()
        target = tmp_path / "CLAUDE.md"
        target.write_text(document, encoding="utf-8")

        old_string = "## Working Memory\n"
        assert document.count(old_string) == 1, (
            "the anchor is not unique in the fixture document, so the edit is "
            "a no-op or the platform refuses it, and this arm would pass "
            "whatever branch the decision takes"
        )

        result = gate._is_add_shaped_edit(
            {
                "old_string": old_string,
                "new_string": f"{PIN_A}\n## Working Memory\n",
                "content": "decoy",
            },
            target,
            "Edit",
        )
        assert result is True, (
            "the decision took the Write branch on an Edit payload, so it read "
            "the `content` value as the whole post-edit document instead of "
            "applying the old and new strings the Edit supplied"
        )


# =========================================================================
# ABLATION. Does each rule earn its place IN THE PRESENCE OF the other?
# =========================================================================

class TestAblationEachRuleEarnsItsPlace:
    """AN ABLATION IS NOT A MUTATION AND THE DIFFERENCE DECIDED A DESIGN ITEM.

    A MUTATION tests a part ALONE: break it and see an arm redden.
    AN ABLATION removes a part while THE OTHERS STAY and asks whether any case
    changes. A part whose repair is present twice survives every mutation and
    changes nothing when ablated. That is how the write-path bounding was found
    redundant and dropped.

    THE TWO ARMS BELOW REMOVE ONE RULE WITH THE OTHER LEFT IN PLACE. Each one
    carries a POSITIVE CONTROL that the removal took effect, because a green
    ablation is ambiguous: it can mean the part earns nothing, or it can mean
    the patch missed its target.
    """

    def test_removing_rule_2_changes_a_named_case_while_rule_1_stays(
        self, gate, monkeypatch, tmp_path
    ):
        """Ablate the conjunction. Rule 1 stays. E0 must change verdict.

        This is the coupling, measured: the WIDER SLICE of Rule 1 keeps the
        memory entries, so with Rule 2 removed a plain memory write over-blocks
        again.

        🔴 THE DOCUMENT DECIDES WHETHER THIS ABLATION CAN BITE AT ALL, AND THE
        BOUND IS NARROWER THAN THE CLASS TITLE SUGGESTS. Step 0 of
        `_counts_show_an_add` narrows the count to the `## Pinned Context` span
        when the two sides resolve one, and a memory entry lands BELOW
        `## Working Memory`, outside that span. On a document that carries a
        pinned heading the count is 0 against 0 WHETHER RULE 2 IS PRESENT OR
        ABLATED, so the ablation returns the same verdict either way and
        reports that Rule 2 earns nothing. THAT WOULD BE A MEASUREMENT OF THE
        FIXTURE RATHER THAN OF THE RULE.
        So this case takes the document with NO pinned heading, where step 0
        declines and the entries enter the slice. Measured on that document:
        0 against 0 with the rule, and 0 against 1 without it.
        WHAT THIS BOUNDS: Rule 2 earns its place on documents where step 0
        DECLINES. Where a pinned span resolves, the span bound alone already
        holds the memory writes quiet.
        """
        document = working_memory_document()
        old, new = "## Working Memory\n\n", f"## Working Memory\n\n{MEM_1}"
        assert fires(gate, old, new, tmp_path=tmp_path,
                     document=document) is False

        monkeypatch.setattr(gate, "_is_memory_entry", lambda pin: False)

        # POSITIVE CONTROL: the ablation took effect. Without this a green
        # result can mean the patch never landed.
        assert _count(gate, MEM_1) == 1, (
            "the ablation did not reach the counter, so the result below "
            "says nothing about what Rule 2 contributes"
        )
        assert fires(gate, old, new, tmp_path=tmp_path,
                     document=document) is True, (
            "removing the conjunction changed NOTHING, so either Rule 2 earns "
            "nothing in the presence of Rule 1, or this arm is not measuring "
            "what it claims"
        )

    def test_removing_rule_1_changes_a_named_case_while_rule_2_stays(
        self, gate, monkeypatch, tmp_path
    ):
        """Ablate the same-slice selection. Rule 2 stays. S2 must change.

        The removal restores the per-side branch choice, which is the shipped
        behaviour before this repair.
        """
        old = unmanaged([PIN_A, PIN_B, PIN_C, PIN_D], outside=OUT_OF_REGION)
        new = managed(
            [PIN_A, PIN_B, PIN_C, PIN_D, PIN_E], outside=OUT_OF_REGION
        )
        assert fires(gate, old, new, "Write", tmp_path) is True

        def per_side(old_text, new_text):
            """Each side picks its own slice, which is the straddle."""
            from shared.claude_md_manager import extract_managed_region

            def one(text):
                got = extract_managed_region(text)
                return _count(gate, got[0] if got else text)

            return one(new_text) > one(old_text)

        monkeypatch.setattr(gate, "_counts_show_an_add", per_side)

        # POSITIVE CONTROL: the ablation reached the decision.
        assert gate._counts_show_an_add is per_side
        assert fires(gate, old, new, "Write", tmp_path) is False, (
            "removing the same-slice rule changed NOTHING on S2, so either it "
            "earns nothing, or this fixture cannot exhibit a straddle"
        )


class TestStep0TheCountBoundHoldsOnItsOwn:
    """STEP 0 ABLATED ALONE, WHICH IS THE WHOLE POINT OF THIS CLASS.

    THE ARM THAT NAMES RULE 1 DOES NOT HOLD STEP 0. The ablation arm in
    `TestAblationEachRuleEarnsItsPlace` replaces `_counts_show_an_add`
    WHOLESALE with a per-side function. A wholesale replacement cannot tell
    step 0 from the branch selection beneath it, so it reports on the pair and
    says nothing about either one. That is the structural reason step 0 was
    unarmed while an arm carrying Rule 1 in its name passed.

    WHAT STEP 0 IS. It asks each side for its `## Pinned Context` span. Where
    the two sides resolve one, that section is the slice for the two. So a
    change below `## Working Memory`, inside the managed region, is OUTSIDE
    the counted slice and the gate stays quiet.

    THE FAILURE DIRECTION, and it is the cardinal one. Remove step 0 and the
    slice widens to the managed region, the added heading enters the count,
    and a faithful memory write turns from quiet to DENY on the user's own
    file. The module docstring claims step 0 `cannot be worse on the cardinal
    axis, for any document, including shapes nobody has enumerated`. That
    claim is load-bearing and this class is what holds it.
    """

    # A heading a memory writer can put below `## Working Memory`. It carries
    # NO `<!-- pinned: -->` marker and it is NOT date-led, so the Rule 2
    # conjunction does not reach it. Step 0 is the only thing that keeps it
    # out of the count, which is what makes this fixture separate the two.
    ADDED_BELOW_WORKING_MEMORY = "### Some prose title\nbody\n"

    def _pair(self):
        """The two sides of one faithful memory write."""
        old = managed([PIN_A])
        new = managed([PIN_A], mems=[self.ADDED_BELOW_WORKING_MEMORY])
        return old, new

    def test_a_heading_below_working_memory_stays_quiet_while_the_bound_holds(
        self,
    ):
        """The shipped verdict, and its own positive control in one arm.

        THE SECOND HALF IS THE CONTROL AND IT IS NOT DECORATION. A lone
        assertion of `False` here is satisfied by any document where nothing
        changed at all, so it would pass for a fixture that cannot exhibit the
        defect. The control strips the `## Pinned Context` heading from the two
        sides, which makes step 0 DECLINE for a reason that has nothing to do
        with the code, and the same edit then reads as an ADD.

        So the pair says: this edit IS visible to the wider slice, and step 0
        is what keeps it out. One half alone says neither.
        """
        import pin_staleness_gate as gate

        old, new = self._pair()

        assert gate._counts_show_an_add(old, new) is False, (
            "A HEADING ADDED BELOW `## Working Memory` READ AS A PIN ADD. "
            "The gate now DENIES a faithful memory write on the user's own "
            "file, which is the cardinal over-block. Step 0 bounds the count "
            "to the `## Pinned Context` span, and it is gone or bypassed"
        )

        # POSITIVE CONTROL: with no pinned heading on either side, step 0
        # declines and the fallback counts the same edit as an add.
        old_wide = old.replace("## Pinned Context\n", "")
        new_wide = new.replace("## Pinned Context\n", "")
        assert gate._counts_show_an_add(old_wide, new_wide) is True, (
            "THE CONTROL HALF FAILED, so the quiet verdict above proves "
            "nothing. With step 0 unavailable this same edit must read as an "
            "ADD. If it does not, this fixture cannot exhibit the over-block "
            "and the arm above is passing for the wrong reason"
        )


class TestTheEmptySectionResolvesRatherThanDeclines:
    """`allow_empty_section=True`, held by the one document shape that moves.

    WHY THE PARAMETER IS THERE, in the words of its own site: without it `an
    empty section is indistinguishable from an absent one, this step declines,
    and the empty side falls back to a wider slice while the other side does
    not. That is the straddle again, in a new place.`

    THE SHAPE THAT SEPARATES, AND THE ONE THAT LOOKS RIGHT AND DOES NOT.
    MEASURED on this branch, with the parameter forced to each value:

      OLD empty section, NEW one pin, nothing else moves
          True with the parameter, True without it.   <- NO SEPARATION
      OLD empty section plus a pin down in Working Memory,
      NEW one pin in the section and that entry gone
          True with the parameter, False without it.  <- SEPARATES

    The first shape is the trap: it reads as the obvious test for an empty
    section and it holds nothing, because the wider fallback slice happens to
    give the same answer. The second forces the fallback to CANCEL the add
    against an unrelated removal, which is the straddle the parameter exists
    to stop, and the direction it fails in is a MISSED ADD.

    WHICH POPULATION THE ROW-TWO SHAPE COVERS. Its Working Memory item is a
    MARKED pin, and a marked entry is a pin to the counter, so its removal
    can cancel the add. THE ENTRIES THE WRITERS EMIT THERE ARE NOT THAT
    SHAPE: their heading is a bare date and time with NO marker, so the
    memory-entry exclusion drops them.

    AND THE EXCLUSION IS WHY THIS IS A LIMIT OF THE SHAPE RATHER THAN A
    DEFECT IN THE GATE. Where step 0 declines, that exclusion is what keeps
    the memory entries out of the wider slice, which is the PAIR recorded at
    `_is_memory_entry`. For the live shape that cell is True, so the
    parameter separates nothing THERE, and the exclusion is what holds that
    population.

    MEASURED, WITH THE COUNTING RULE ADJACENT TO THE NUMBER. Bound the region
    from the `## Working Memory` heading to the managed end marker. A heading
    is a `### ` line at column 0 inside that region. A marker is a
    `<!-- pinned: ... -->` comment inside the same region. Judge each entry
    with `_is_memory_entry` on the `parse_pins` output, the oracle of this
    guard, and not by a substring count, because the two disagree. Across the
    three managed files available at the time: 3 headings, 0 markers, 3
    memory entries, and 0 counted as pins. CONTROLS: a heading pattern that
    is not possible returned 0, the `### ` token returned non-zero, and
    `_is_memory_entry`
    returned True on a bare-date unmarked entry built for the check and
    False on the same entry with a marker. So the 0 is a measurement and not
    an instrument fault. THAT IS THREE FILES AT ONE MOMENT, and not a claim
    about all consumer files. RE-RUN IT.
    """

    def test_an_add_into_an_empty_section_is_seen_when_the_fallback_cancels_it(
        self,
    ):
        """OLD carries an empty pinned section. NEW adds one pin to it.

        The pin comment down in Working Memory on the OLD side is what makes
        the wider slice count 1 against 1 and report no add. Only the bounded
        slice, which needs the empty section to RESOLVE, sees 0 against 1.
        """
        import pin_staleness_gate as gate

        old = managed([], mems=[PIN_B])
        new = managed([PIN_A])

        assert gate._counts_show_an_add(old, new) is True, (
            "A PIN ADDED INTO AN EMPTY PINNED SECTION WAS NOT SEEN. "
            "`allow_empty_section=True` is what makes an empty section "
            "RESOLVE rather than decline. Without it the empty side falls "
            "back to a wider slice, an unrelated removal elsewhere cancels "
            "the add, and the gate stays quiet on a true add. That is the "
            "straddle, and it is a MISSED ADD"
        )

    def test_the_shape_that_cannot_separate_is_recorded_as_unusable(self):
        """A NEGATIVE RESULT, KEPT SO THE NEXT AUTHOR DOES NOT REACH FOR IT.

        This is the obvious empty-section fixture: OLD empty, NEW one pin, and
        nothing else moves. It gives True under the shipped parameter AND True
        with the parameter removed, so it cannot tell the two apart.

        The assertion below is deliberately about the SHIPPED verdict only. It
        exists to keep the fixture in the file with its measurement attached,
        so that a later reader who reaches for this shape finds the note rather
        than repeats the measurement.
        """
        import pin_staleness_gate as gate

        old = managed([])
        new = managed([PIN_A])

        assert gate._counts_show_an_add(old, new) is True, (
            "the plain empty-section add stopped reading as an add, which is "
            "a change this file did not predict. NOTE: this fixture is NOT a "
            "guard for `allow_empty_section`. It gives the same answer with "
            "and without the parameter. The arm above is the one that "
            "separates"
        )
