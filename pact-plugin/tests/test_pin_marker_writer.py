"""
Location: pact-plugin/tests/test_pin_marker_writer.py

Summary: Pins the declared pinned-region START marker, its pure planner, and the
hook that writes it. Two properties are merge gates and are proven
MECHANICALLY rather than by reading the code:

  1. EXPEL-NOTHING. Every insertion is certified on DOCUMENT PAIRS -- the whole
     file before against the whole file after -- because the cap this feature
     leads to is a two-state predicate that no single-document probe can reach.
  2. NON-DENIAL. Asserted over a corpus of INPUTS driven through the real
     script as a subprocess, never over the wording of any message. The claim
     is "no input makes this hook deny", so the quantifier has to be over
     inputs.

SAFETY NOTE FOR ANYONE EDITING THIS FILE. The hook resolves a real project
CLAUDE.md and writes to it. Every test here pins CLAUDE_PROJECT_DIR to a
tmp_path so the resolver cannot reach the developer's own file, which is
gitignored and unrecoverable. Do not remove that env pin from any subprocess
call.
"""

from __future__ import annotations

import ast
import json
import re
import functools
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PACT_BOUNDARY_PREFIXES,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
    ensure_project_memory_md,
)
from shared.pin_markers import (
    END_LINE,
    START_LINE,
    Insertion,
    SkipReason,
    apply_insertion,
    certify_expel_nothing,
    marker_line_present,
    plan_insertion,
)
from clock_shift.clock_shift_env import carry_clock_shift

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
HOOK_SCRIPT = HOOKS_DIR / "pin_marker_writer.py"
HOOKS_JSON = HOOKS_DIR / "hooks.json"


# --------------------------------------------------------------------------
# Document builders
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def production_skeleton() -> str:
    """The canonical managed document, TAKEN FROM THE PRODUCTION EMITTER.

    `ensure_project_memory_md` is the code that creates a project CLAUDE.md,
    so driving it is what makes this corpus agree with production about the
    SHAPE OF THE DOCUMENT. It is run against a temporary project dir and the
    file it wrote is read back.

    WHY THIS IS NOT A CONVENIENCE. The builders here previously spelled the
    boundary layout by hand, and they spelled it WRONG: they emitted the outer
    managed marker and no inner memory pair, which is a shape production never
    produces. Every fixture in this corpus therefore agreed with every other
    fixture and with nothing that ships. A hand-written corpus cannot detect
    that, because the thing it would have to compare against is the very
    assumption it encodes.

    THE HEAD IS THE LOAD-BEARING PART. Production emits the managed marker,
    the title, THEN the session block, THEN the memory marker, so the session
    block sits ABOVE the memory region. That ordering is the whole subject of
    the window this module's planner searches, and a hand-built head omits it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        previous = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        try:
            ensure_project_memory_md()
            return (Path(tmp) / ".claude" / "CLAUDE.md").read_text(
                encoding="utf-8"
            )
        finally:
            if previous is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = previous


def production_head_and_tail() -> tuple[str, str]:
    """Split the production skeleton at its MEMORY marker pair.

    Returns `(head, tail)` where `head` runs from the start of the document
    through the memory START marker line, and `tail` runs from the memory END
    marker line to the end. Section bodies go between them.

    So a caller varies what sits INSIDE the memory region and never restates
    the boundary layout that encloses it.
    """
    doc = production_skeleton()
    start = doc.index(MEMORY_START_MARKER) + len(MEMORY_START_MARKER)
    # Take the newline that terminates the marker line with the head, so the
    # caller's first body line begins a line of its own.
    head = doc[:start] + "\n"
    tail = doc[doc.index(MEMORY_END_MARKER):]
    return head, tail


def build_claude_md(
    pinned_body: str = "### A pin\nSome pinned prose.\n\n",
    retrieved: str = "\n### 2026-01-01\nA retrieved entry.\n\n",
    working: str = "\n### 2026-01-02\nA working entry.\n",
    user_prefix: str = "# My own heading\n\nUser prose above the block.\n\n",
    user_suffix: str = "\nUser prose below the block.\n",
    include_pinned_heading: bool = True,
    managed: bool = True,
) -> str:
    """Compose a CLAUDE.md in the canonical section order.

    Canonical order is Retrieved Context, Pinned Context, Working Memory, which
    is what puts a start marker above the pinned heading INSIDE the Retrieved
    Context span -- the reason both marker names must join the terminator
    alternation.

    THE BOUNDARY LAYOUT COMES FROM `production_head_and_tail`, NEVER FROM A
    LITERAL HERE. Only the section bodies are this function's own. Read
    `production_skeleton` for why a hand-written boundary is the defect rather
    than the shortcut.
    """
    pinned = (
        f"## Pinned Context\n\n{pinned_body}" if include_pinned_heading else ""
    )
    sections = (
        f"## Retrieved Context\n{retrieved}"
        f"{pinned}"
        f"## Working Memory\n{working}"
    )
    if not managed:
        # No managed region at all, so no memory region either. The title stays
        # because a pre-migration document still carries user headings.
        return (
            user_prefix
            + "# PACT Framework and Managed Project Memory\n\n"
            + sections
            + user_suffix
        )
    head, tail = production_head_and_tail()
    return user_prefix + head + sections + tail + user_suffix


# --------------------------------------------------------------------------
# The two literals
# --------------------------------------------------------------------------

class TestMarkerLiterals:
    """The names are load-bearing, so their properties are pinned, not assumed."""

    def test_the_marker_joins_every_terminator_alternation(self, monkeypatch):
        """The name must be MATCHED by all three scanners that infer a
        section end.

        An unmatched start marker does not terminate the Retrieved Context
        scan. It falls inside the span that writer rebuilds from recognised
        entries only, rides the last entry as a passenger, and is deleted when
        rotation evicts that entry. A matched one terminates the scan at the
        true end of the section and lands in the span the rebuild preserves.

        The alternations are rebuilt here from each module's OWN constants --
        `PACT_BOUNDARY_PREFIXES` for the hooks side, and `_PACT_BOUNDARY_ALT`
        WITH `_SESSION_BOUNDARY_ALT` for the skills side -- so this measures
        the real rule rather than a copy of it. THE SECOND SKILLS CONSTANT IS
        NOT OPTIONAL HERE: the two write-side scans embed the two names, so a
        rebuild from one of them is a PARTIAL copy that keeps passing while it
        stops measuring what ships.
        """
        # `syspath_prepend` is reverted by pytest when the test ends. A bare
        # `sys.path.insert(0, ...)` here would OUTLIVE this test and re-order
        # imports for everything after it in the session -- the shape that
        # produced a full-suite-only failure elsewhere in this suite.
        monkeypatch.syspath_prepend(
            str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")
        )
        from scripts.working_memory import _PACT_BOUNDARY_ALT, _SESSION_BOUNDARY_ALT

        hooks_alt = "|".join(PACT_BOUNDARY_PREFIXES)
        skills_alt = f"{_PACT_BOUNDARY_ALT}|{_SESSION_BOUNDARY_ALT}"
        alternations = {
            "staleness pinned scan": re.compile(
                rf'(?:#{{1,2}}\s|<!-- (?:{hooks_alt}))'
            ),
            "working memory scan": re.compile(
                rf'(#\s|##\s(?!Working Memory)|---|<!-- (?:{skills_alt}))'
            ),
            "retrieved context scan": re.compile(
                rf'(#\s|##\s(?!Retrieved Context)|---|<!-- (?:{skills_alt}))'
            ),
        }
        for marker in (PINNED_START_MARKER,):
            for name, pattern in alternations.items():
                assert pattern.match(marker), (
                    f"{marker} is NOT matched by the {name}. An unmatched "
                    "marker is deleted by section rotation."
                )

    def test_alternation_check_is_not_vacuous(self):
        """A name the alternation does NOT match must fail the same check.

        Without this, a pattern that matched everything would pass the test
        above while measuring nothing.
        """
        hooks_alt = "|".join(PACT_BOUNDARY_PREFIXES)
        pattern = re.compile(rf'(?:#{{1,2}}\s|<!-- (?:{hooks_alt}))')
        assert not pattern.match("<!-- PINNED_START -->")
        assert not pattern.match("<!-- PACT_PINNED_START -->")

    @pytest.mark.parametrize("existing", [
        MANAGED_START_MARKER, MANAGED_END_MARKER,
        MEMORY_START_MARKER, MEMORY_END_MARKER,
    ])
    def test_new_literals_contain_no_existing_marker_as_substring(self, existing):
        """Containment either way would be a live bug, not an aesthetic one.

        `extract_managed_region` uses first-find on the managed markers, so a
        literal containing one would mis-anchor or truncate the region. And
        session_resume runs an UNBOUNDED replace on the memory start marker, so
        a literal containing THAT would collect a session block on every
        SessionStart.
        """
        for new in (PINNED_START_MARKER,):
            assert existing not in new
            assert new not in existing

    def test_marker_line_is_the_marker_plus_one_newline(self):
        assert START_LINE == PINNED_START_MARKER + "\n"


# --------------------------------------------------------------------------
# MERGE GATE 1 -- EXPEL-NOTHING, on document pairs
# --------------------------------------------------------------------------

PIN_TABLE = [
    ("single pin", "### A pin\nbody\n\n"),
    ("two pins", "### One\nbody one\n\n### Two\nbody two\n\n"),
    ("pin with a stamp", "### A pin\n<!-- pinned: 2026-01-01 -->\nbody\n\n"),
    ("body ends in blank lines", "### A pin\nbody\n\n\n\n"),
    ("body with no trailing blank", "### A pin\nbody\n"),
    ("prose with no H3 at all", "Just prose, no entry heading.\n\n"),
    ("body containing an html comment", "### A pin\n<!-- a note -->\nbody\n\n"),
    # A FENCED BODY IS NOT IN THIS TABLE ON PURPOSE. Every row here must produce
    # an insertion, and a fenced body is now REFUSED outright. The refusal and
    # the shapes that forced it are pinned in TestFencedBodyRefusal below.
    ("unicode body", "### A pin\nnaive cafe resume\n\n"),
    ("very long body", "### A pin\n" + ("x" * 5000) + "\n\n"),
    ("body with windows line endings", "### A pin\r\nbody\r\n\r\n"),
    ("body with a tab", "### A pin\n\tindented\n\n"),
    ("body with an equals rule", "### A pin\nbody\n===\n\n"),
]


class TestExpelNothing:
    """The certificate, driven on whole documents rather than on lines."""

    @pytest.mark.parametrize("label,pinned_body", PIN_TABLE, ids=[r[0] for r in PIN_TABLE])
    def test_insertion_expels_nothing(self, label, pinned_body):
        old = build_claude_md(pinned_body=pinned_body)
        planned = plan_insertion(old)
        assert isinstance(planned, Insertion), (
            f"{label}: expected an insertion, got {planned}"
        )
        new = apply_insertion(old, planned)

        # The certificate itself.
        assert certify_expel_nothing(old, new, planned) is True

        # Restated independently of the function under test, so a certificate
        # that silently degraded to `return True` cannot carry this test.
        # BOTH lines are accounted: the writer emits the pair in ONE
        # composition, so a restatement that counts only the START would pass
        # a composition that dropped the END entirely.
        assert len(new) == len(old) + len(START_LINE) + len(END_LINE)
        assert new.replace(START_LINE, "").replace(END_LINE, "") == old

        # Every original character survives in its original order.
        assert old in new.replace(START_LINE, "").replace(END_LINE, "")

    @pytest.mark.parametrize("label,pinned_body", PIN_TABLE, ids=[r[0] for r in PIN_TABLE])
    def test_the_marker_lands_in_the_right_place(self, label, pinned_body):
        """Placement is pinned SEPARATELY, and it is now the ONLY thing
        constraining the offset at all.

        MEASURED: with a single splice point the certificate returns True at
        EVERY offset from 0 to len(old) -- a splice point cannot cross itself,
        so no offset can drop a byte. Before the end marker was removed the
        offset had two independent constraints, the certificate refusing
        crossed offsets and these assertions. Now it has one. If this test is
        weakened or deleted, nothing anywhere checks where the marker went.
        """
        old = build_claude_md(pinned_body=pinned_body)
        planned = plan_insertion(old)
        new = apply_insertion(old, planned)

        # The marker sits immediately ABOVE the pinned heading, on its own line.
        after_marker = new.split(START_LINE, 1)[1]
        assert after_marker.startswith("## Pinned Context"), (
            f"{label}: the line after the marker is not the pinned heading"
        )

        # It sits BELOW the retrieved-context section it terminates, so the
        # heading it declares is the pinned one and not some earlier section.
        before_marker = new.split(START_LINE, 1)[0]
        assert before_marker.endswith("\n"), (
            f"{label}: the marker does not begin its own line"
        )
        assert "## Retrieved Context" in before_marker

        # The whole pinned body still follows it, unmoved.
        assert pinned_body.strip()[:20] in after_marker

    def test_the_certificate_refuses_a_crossed_pair(self):
        """NON-VACUITY FOR OFFSETS, restored with the pair.

        SUPERSEDES `test_placement_assertions_can_actually_fail`, whose premise
        the pair INVERTED. That test asserted the certificate "accepts every
        offset" and that placement was therefore the sole offset constraint --
        true while there was one splice point, because a single splice cannot
        cross itself. Two splice points can. Crossing them makes the middle
        slice run backwards and emits the tail twice, so the length assertion
        catches it and the certificate refuses.

        Do not read the change as a relaxation. The old test proved a WEAKNESS
        was still present; this one proves the STRENGTH that replaced it.
        """
        old = build_claude_md()
        planned = plan_insertion(old)
        assert isinstance(planned, Insertion), "FIXTURE INVALID"

        crossed = Insertion(
            start_offset=planned.end_offset,
            end_offset=planned.start_offset,
            start_line=START_LINE,
            end_line=END_LINE,
        )
        damaged = apply_insertion(old, crossed)

        assert len(damaged) != len(old) + len(START_LINE) + len(END_LINE), (
            "a crossed pair did not duplicate bytes, so the length assertion "
            "has nothing to catch and this arm is vacuous"
        )
        assert certify_expel_nothing(old, damaged, crossed) is False, (
            "the certificate accepted a crossed pair; its offset power is gone "
            "again and placement is back to being the sole guard"
        )

        # CONTROL: the uncrossed plan on the same document is accepted, so the
        # refusal above is attributable to the crossing and not to the fixture.
        assert certify_expel_nothing(
            old, apply_insertion(old, planned), planned
        ) is True

    def test_the_certificate_refuses_a_mid_line_offset(self):
        """NON-VACUITY FOR THE LINE-START PROPERTY.

        A mid-line offset splits a user's line and strands a fragment. Every
        other clause in the certificate still passes on such a composition --
        it is byte-preserving, the arithmetic holds, and the unbounded replace
        reproduces the original. Only the line-start gate catches it.

        Counter-test protocol: delete either `is_line_start` clause from
        `certify_expel_nothing` and this test must fail.
        """
        old = build_claude_md()
        planned = plan_insertion(old)
        assert isinstance(planned, Insertion), "FIXTURE INVALID"

        midline = Insertion(
            start_offset=planned.start_offset + 3,
            end_offset=planned.end_offset,
            start_line=START_LINE,
            end_line=END_LINE,
        )
        damaged = apply_insertion(old, midline)

        # The clauses that are NOT the line-start gate all pass on this input.
        assert len(damaged) == len(old) + len(START_LINE) + len(END_LINE)
        assert damaged.replace(START_LINE, "").replace(END_LINE, "") == old

        assert certify_expel_nothing(old, damaged, midline) is False, (
            "a mid-line insertion offset was certified; the line-start "
            "property is unguarded again"
        )

    def test_certificate_refuses_a_composition_that_drops_bytes(self):
        """NON-VACUITY for the certificate. It must REFUSE a real defect, or
        its green means nothing.

        The defect it can still catch is a MIS-ASSEMBLED composition, not a bad
        offset. `apply_insertion` is now the only place a byte-losing bug can
        enter, so the mutation is applied there: this splice drops one byte.
        """
        old = build_claude_md()
        good = plan_insertion(old)
        assert isinstance(good, Insertion)

        # A byte-dropping splice -- the shape a careless edit to
        # `apply_insertion` would produce.
        o = good.start_offset
        damaged = old[:o] + START_LINE + old[o + 1:]

        # The damage is real: content was actually lost.
        assert len(damaged) != len(old) + len(START_LINE)
        assert certify_expel_nothing(old, damaged, good) is False

    @pytest.mark.parametrize("label,mutate", [
        ("drops a byte", lambda c, o: c[:o] + START_LINE + c[o + 1:]),
        ("duplicates a byte", lambda c, o: c[:o + 1] + START_LINE + c[o:]),
        ("inserts the marker twice", lambda c, o: c[:o] + START_LINE + START_LINE + c[o:]),
        ("omits the newline", lambda c, o: c[:o] + START_LINE.rstrip("\n") + c[o:]),
        ("reorders the tail", lambda c, o: c[:o] + START_LINE + c[o:][::-1]),
    ])
    def test_every_assembly_mutation_is_refused(self, label, mutate):
        """The certificate's real remaining scope, enumerated.

        Each of these is a way `apply_insertion` could be broken by a future
        edit. All must be refused; the correct assembly is the control below.
        """
        old = build_claude_md()
        good = plan_insertion(old)
        assert certify_expel_nothing(
            old, mutate(old, good.start_offset), good
        ) is False, f"{label} was NOT refused"

    def test_the_correct_assembly_is_accepted(self):
        """CONTROL for the mutation table above. Without it, a certificate that
        refused everything would pass every row while blocking every write."""
        old = build_claude_md()
        good = plan_insertion(old)
        assert certify_expel_nothing(old, apply_insertion(old, good), good) is True

    def test_certificate_refuses_when_the_file_already_quotes_a_marker(self):
        """Driven DIRECTLY, because in the assembled write the presence check
        in `plan_insertion` refuses this file earlier.

        The two mechanisms are independent on purpose and neither may be
        removed on the ground that the other covers it, so each is pinned on
        its own.
        """
        old = build_claude_md(
            pinned_body="### A pin\nI wrote " + START_LINE + "in my notes\n\n"
        )
        forced = Insertion(0, 0, START_LINE, END_LINE)
        new = apply_insertion(old, forced)
        assert certify_expel_nothing(old, new, forced) is False

    def test_certificate_never_raises(self):
        forced = Insertion(0, 0, START_LINE, END_LINE)
        assert certify_expel_nothing(None, None, forced) is False
        assert certify_expel_nothing(1, 2, forced) is False


# --------------------------------------------------------------------------
# The precondition ladder and ordered-pair idempotence
# --------------------------------------------------------------------------

class TestPreconditionLadder:

    def test_unmigrated_file_is_refused(self):
        doc = build_claude_md(managed=False)
        assert plan_insertion(doc) is SkipReason.NOT_MIGRATED

    def test_absent_pinned_section_is_a_noop(self):
        doc = build_claude_md(include_pinned_heading=False)
        assert plan_insertion(doc) is SkipReason.NO_SECTION

    @pytest.mark.parametrize("body", ["", "   \n\n", "\n", "\t\n \n"])
    def test_empty_pinned_section_is_a_noop(self, body):
        """An empty section reads as ABSENT to the only current reader of this
        region, so a pair around it would declare a boundary no consumer
        believes in. Such a heading is migration-emitted, so this skips a
        heading the plugin wrote, never a user's content.
        """
        doc = build_claude_md(pinned_body=body)
        assert plan_insertion(doc) is SkipReason.EMPTY_SECTION

    def test_plan_never_raises_on_a_non_string(self):
        assert plan_insertion(None) is SkipReason.PLAN_FAILED
        assert plan_insertion(42) is SkipReason.PLAN_FAILED

    def test_no_pinned_section_means_the_section_is_never_created(self):
        """The 'create the missing section' shape is the destructive one, so
        its absence is asserted rather than assumed."""
        doc = build_claude_md(include_pinned_heading=False)
        planned = plan_insertion(doc)
        assert isinstance(planned, SkipReason)
        assert "## Pinned Context" not in doc


class TestTheDetectorAcceptsOnlyWhatTheWriterEmits:
    """LOAD-BEARING. These fail before the detector is narrowed and must pass
    after. The predicate compares the last gap line above the pinned heading,
    STRIPPED, against the symbol.

    Every fixture here is COMPOSED from the imported symbol via f-strings, so
    the marker's value is never typed as a literal anywhere in this file. That
    is not a workaround for the hygiene rule -- it satisfies it exactly, and
    weakening the corpus to avoid the apparent conflict would remove the very
    fixtures that prove the fix.
    """

    def _marked(self, **kw):
        old = build_claude_md(**kw)
        planned = plan_insertion(old)
        # AMENDMENT 1: fixture validity, asserted BEFORE any result is read. A
        # SkipReason here means the arm measured nothing while looking clean.
        assert isinstance(planned, Insertion), (
            f"FIXTURE INVALID: plan_insertion returned {planned!r}"
        )
        return apply_insertion(old, planned)

    def test_the_writers_own_output_is_recognised(self):
        assert plan_insertion(self._marked()) is SkipReason.ALREADY_MARKED

    @pytest.mark.parametrize("label,drift", [
        ("trailing spaces", lambda m: f"{m}   \n"),
        ("trailing tab", lambda m: f"{m}\t\n"),
        ("leading indent", lambda m: f"  {m}\n"),
        ("leading and trailing", lambda m: f"  {m}  \n"),
    ])
    def test_whitespace_drift_is_tolerated(self, label, drift):
        """STRIPPED, not byte-exact. A whitespace-padded marker line IS a
        marker line by any reading, so treating it as absent would itself be an
        asymmetry with the writer -- the same defect class one layer in.
        """
        marked = self._marked()
        drifted = marked.replace(START_LINE, drift(PINNED_START_MARKER))
        assert plan_insertion(drifted) is SkipReason.ALREADY_MARKED, (
            f"{label}: drift made the detector stop recognising its own marker"
        )

    @pytest.mark.parametrize("label,shape", [
        ("prefixed on the same line", lambda m: f"text {m}\n"),
        ("suffixed on the same line", lambda m: f"{m} trailing text\n"),
    ])
    def test_a_marker_sharing_its_line_is_NOT_recognised(self, label, shape):
        """`.strip()` subsumes an `alone on the line` requirement without a
        second clause: a line with other content strips to itself."""
        marked = self._marked()
        shared = marked.replace(START_LINE, shape(PINNED_START_MARKER))
        assert plan_insertion(shared) is not SkipReason.ALREADY_MARKED

    def test_a_carrier_in_the_gap_no_longer_blocks_the_write(self):
        """THE DEFECT ITSELF. A document merely MENTIONING the marker above the
        heading used to read as already migrated and was refused permanently.
        """
        doc = build_claude_md(
            retrieved=f"\n### 2026-01-01\n**Context**: one\n{PINNED_START_MARKER}\nthree\n\n"
        )
        assert isinstance(plan_insertion(doc), Insertion), (
            "a gap carrier still blocks the write; the defect is not fixed"
        )

    def test_a_mention_inside_the_pinned_body_does_not_block_the_write(self):
        doc = build_claude_md(
            pinned_body=f"### A pin\n{PINNED_START_MARKER}\nmore\n\n"
        )
        assert isinstance(plan_insertion(doc), Insertion)


class TestAdjacencySurvivesBothMachineWriters:
    """The machine path is why this is not a documentation problem: the
    pact-memory formatters interpolate free-text fields into CLAUDE.md through
    bare f-strings, so a harvested memory DISCUSSING the marker creates a
    carrier with no human involved.

    TWO WRITERS, ON OPPOSITE SIDES OF THE PINNED HEADING, AND THEY READ
    DIFFERENT FIELD SETS. Pairing a writer with a field it does not read is a
    silent no-op: the carrier is never placed and the arm returns a clean
    negative.

      - Retrieved Context, ABOVE the heading: reads query / score / context /
        goal / memory_id. It does NOT read lessons_learned.
      - Working Memory, BELOW the heading: reads the seven content fields
        including lessons_learned.

    A MULTI-LINE value is required. Each field renders as `**Field**: {value}`,
    so a single-line value puts the marker MID-LINE where it cannot reach the
    failure, and only a multi-line value gets it to line start.
    """

    def _project(self, tmp_path, monkeypatch):
        monkeypatch.syspath_prepend(
            str(Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts")
        )
        old = build_claude_md()
        planned = plan_insertion(old)
        assert isinstance(planned, Insertion), "FIXTURE INVALID"
        marked = apply_insertion(old, planned)
        target = tmp_path / "CLAUDE.md"
        target.write_text(marked, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        return target

    def _adjacent(self, text):
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _PINNED_HEADING, _is_already_marked
        region = extract_managed_region(text)
        assert region is not None
        heading = _PINNED_HEADING.search(region[0])
        assert heading is not None
        return _is_already_marked(region[0], heading.start())

    def test_the_above_heading_writer_cannot_break_adjacency(self, tmp_path, monkeypatch):
        from scripts import working_memory as wm
        target = self._project(tmp_path, monkeypatch)
        before = target.read_text(encoding="utf-8")

        # MULTI-LINE, in `goal`: a field this writer actually reads AND does
        # not truncate. `context` is also read but is CUT AT 197 CHARACTERS, so
        # a carrier placed beyond that offset is silently dropped and the arm
        # returns a clean negative. That truncation is a FIXTURE TRAP, not a
        # mitigation -- `goal` here and every field of the entry writer are
        # untruncated, so the machine path is not bounded by it.
        carrier = f"line one\n{PINNED_START_MARKER}\nline three"
        wm.sync_retrieved_to_claude_md(
            memories=[{"context": "c", "goal": carrier}],
            query="q", scores=[0.9], memory_ids=["mid"])

        after = target.read_text(encoding="utf-8")
        assert after != before, "the writer did not run; this arm measured nothing"
        assert after.count(PINNED_START_MARKER) > 1, (
            "the carrier was never placed, so adjacency held trivially"
        )
        assert self._adjacent(after) is True

    def test_the_below_heading_writer_cannot_break_adjacency(self, tmp_path, monkeypatch):
        from scripts import working_memory as wm
        target = self._project(tmp_path, monkeypatch)
        before = target.read_text(encoding="utf-8")

        wm.sync_to_claude_md(
            memory={"context": "c", "goal": "g",
                    "lessons_learned": f"first\n{PINNED_START_MARKER}\nthird"},
            target=target)

        after = target.read_text(encoding="utf-8")
        assert after != before, "the writer did not run; this arm measured nothing"
        assert after.count(PINNED_START_MARKER) > 1, (
            "the carrier was never placed, so adjacency held trivially"
        )
        assert self._adjacent(after) is True

    def test_the_join_cannot_FUSE_the_marker_onto_another_line(self, tmp_path, monkeypatch):
        """AMENDMENT 4. Adjacency rests on TWO structural properties of the
        rebuild, not one, and this is the second: `section_text` ends with a
        newline, so joining it to an `after_section` that begins at the marker
        cannot produce `...text<marker>` on a single line.

        THE INTUITION HERE IS INVERTED, AND THE PREDICATE MUST NOT BE ALLOWED
        TO STAND IN FOR THIS TEST. If the join ever fused, the STRIPPED
        comparison would CORRECTLY REFUSE the fused line -- `text marker`
        strips to itself, which is not the marker -- so the detector would
        report not-marked AND RE-INSERT. The comparison being right is exactly
        what makes fusion dangerous; it is not what protects against it. The
        protection is structural, so it needs a structural assertion.

        (It is also a third reason not to use `endswith`: that variant would
        silently ACCEPT a fused line, reporting marked while the marker is not
        where the writer puts it.)
        """
        from scripts import working_memory as wm
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _PINNED_HEADING
        target = self._project(tmp_path, monkeypatch)
        wm.sync_retrieved_to_claude_md(
            memories=[{"context": "c", "goal": f"one\n{PINNED_START_MARKER}\nthree"}],
            query="q", scores=[0.9], memory_ids=["mid"])

        region, _ = extract_managed_region(target.read_text(encoding="utf-8"))
        heading = _PINNED_HEADING.search(region)
        marker_line = region[:heading.start()].splitlines()[-1]
        assert marker_line == marker_line.strip() == PINNED_START_MARKER, (
            "the marker line has fused with adjacent content; the detector "
            "will stop recognising it and re-insert"
        )

    def test_the_structural_trailing_blank_is_present(self, tmp_path, monkeypatch):
        """THE SOLE REASON a machine-placed carrier cannot reach adjacency, and
        nothing else tests it. The Retrieved Context rebuild always emits a
        trailing blank line before the next section; if a future change drops
        it, a carrier could occupy the adjacent line and adjacency would
        degrade SILENTLY with no other failing test.
        """
        from scripts import working_memory as wm
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _PINNED_HEADING
        target = self._project(tmp_path, monkeypatch)
        wm.sync_retrieved_to_claude_md(
            memories=[{"context": "c", "goal": f"one\n{PINNED_START_MARKER}\nthree"}],
            query="q", scores=[0.9], memory_ids=["mid"])

        region, _ = extract_managed_region(target.read_text(encoding="utf-8"))
        heading = _PINNED_HEADING.search(region)
        gap_lines = region[:heading.start()].splitlines()
        # The line immediately above the marker line must be blank -- that is
        # the structural separator the rebuild emits.
        assert gap_lines[-1].strip() == PINNED_START_MARKER
        assert gap_lines[-2].strip() == "", (
            "the structural trailing blank is gone; a machine-placed carrier "
            "can now reach the adjacent line"
        )


class TestCardinalRegression:
    """THE LIKELIEST WAY THIS FIX GOES WRONG, and it is strictly worse than the
    defect being fixed: if the detector stops recognising the plugin's own
    marker, every pin command re-inserts one.

    TWO ROUTES REACH IT AND ONLY ONE IS BOUNDED. They look identical from
    outside, which is why both are tested by name rather than one standing in
    for the other.
    """

    def test_four_passes_yield_exactly_one_marker(self):
        """The gate. Catches BOTH routes, because even the bounded one violates
        `exactly one`."""
        text = build_claude_md()
        assert isinstance(plan_insertion(text), Insertion), "FIXTURE INVALID"
        for _ in range(4):
            planned = plan_insertion(text)
            if isinstance(planned, Insertion):
                text = apply_insertion(text, planned)
        assert text.count(PINNED_START_MARKER) == 1, (
            f"cardinal regression: {text.count(PINNED_START_MARKER)} markers "
            "after four passes"
        )

    def test_whitespace_drift_does_not_accumulate_under_the_shipped_predicate(self):
        """ROUTE 1, bounded even when it fails. Under a BYTE-EXACT predicate a
        drifted marker line is unrecognised, one clean marker is re-inserted,
        and the next pass matches THAT -- so the count reaches 2 and STOPS.
        Under the shipped STRIPPED predicate it never reaches 2 at all.
        """
        text = build_claude_md()
        planned = plan_insertion(text)
        assert isinstance(planned, Insertion), "FIXTURE INVALID"
        text = apply_insertion(text, planned).replace(
            START_LINE, f"{PINNED_START_MARKER}   \n"
        )
        for _ in range(10):
            p = plan_insertion(text)
            if isinstance(p, Insertion):
                text = apply_insertion(text, p)
        assert text.count(PINNED_START_MARKER) == 1

    def test_a_broken_gap_computation_grows_without_bound(self):
        """ROUTE 2, UNBOUNDED, and the reason route 1 must not stand in for it.
        A detector looking at the wrong place never matches the marker it just
        emitted, so nothing halts. Simulated by a predicate that always reports
        `not marked` -- the shape any wrong-region or off-by-one bug takes.

        This test does NOT exercise shipped code. It exists to show that the
        four-passes gate above is measuring something real: if growth were
        impossible, that gate would be vacuous.
        """
        text = build_claude_md()
        assert isinstance(plan_insertion(text), Insertion), "FIXTURE INVALID"
        for _ in range(4):
            planned = plan_insertion(text)
            if isinstance(planned, Insertion):
                text = apply_insertion(text, planned)
            else:
                # A broken detector would not have returned a SkipReason here.
                text = apply_insertion(
                    text,
                    Insertion(
                        text.index("## Pinned Context"),
                        text.index("## Pinned Context"),
                        START_LINE,
                        END_LINE,
                    ),
                )
        assert text.count(PINNED_START_MARKER) > 1, (
            "the unbounded route could not be reproduced, so the four-passes "
            "gate may be vacuous"
        )


class TestFencedBodyRefusal:
    """A pinned body containing ANY fence marker is refused.

    THE PREDICATE IS A BARE SUBSTRING TEST AND THAT IS THE POINT. The
    terminator scan has no fence awareness, and the guarantee it relies on --
    that the managed region holds only plugin-generated content -- is FALSE for
    the pinned section, where pins are user-authored. So on a fenced body the
    offset it returns cannot be trusted.

    The intuitive repair is a backtick tracker that finds the first terminator
    OUTSIDE a fence, plus a refusal gate for ambiguous cases. It was measured
    and it FAILS, and the two failures below are why this suite pins them by
    name. Crucially the refusal gate does not save that design either, because
    the gate asks the SAME tracker whether the landing line is inside a fence:
    on these shapes the tracker believes it is not, so the gate stays silent
    exactly where it is needed. A guard that consults the mechanism it guards
    cannot catch that mechanism failing.
    """

    FENCED_SHAPES = [
        ("backtick fence with a heading inside",
         "### A pin\n```\n## Not a heading\n```\nmore\n\n"),
        ("balanced fence with a heading-shaped line",
         "### A pin\n```\n# install deps\n```\nmore\n\n"),
        ("unclosed fence",
         "### A pin\n```\ncode that never closes\n\n"),
        ("four backticks wrapping three",
         "### A pin\n````\n```\n## Inner\n```\n````\nmore\n\n"),
        ("tilde fence containing a heading-shaped line",
         "### A pin\n~~~\n## Not a heading\n~~~\nmore\n\n"),
        ("balanced fence with no heading inside",
         "### A pin\n```\nplain code\n```\nmore\n\n"),
        ("inline triple backtick in prose",
         "### A pin\nuse ``` to open a block\n\n"),
    ]

    @pytest.mark.parametrize(
        "label,body", FENCED_SHAPES, ids=[r[0] for r in FENCED_SHAPES]
    )
    def test_every_fenced_shape_is_refused(self, label, body):
        doc = build_claude_md(pinned_body=body)
        assert plan_insertion(doc) is SkipReason.FENCED_BODY, (
            f"{label}: a fenced body must be refused, never placed"
        )

    def test_an_unfenced_body_is_still_inserted(self):
        """NON-VACUITY. A predicate that refused everything would pass every
        assertion above while shipping a write that never runs.
        """
        doc = build_claude_md(pinned_body="### A pin\nplain prose only\n\n")
        assert isinstance(plan_insertion(doc), Insertion)

    def test_the_refusal_is_what_prevents_the_misplacement(self):
        """Shows the defect the refusal avoids, so a later reader can see what
        is at stake before narrowing the predicate.

        The fence-blind scan really does stop on the heading-shaped line INSIDE
        the user's fence. Were the body not refused, the end marker would be
        placed there, splitting the user's code block.
        """
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _PINNED_HEADING, _PINNED_TERMINATOR
        from staleness import _find_terminator_offset

        doc = build_claude_md(
            pinned_body="### A pin\n```\n## Not a heading\n```\nmore\n\n"
        )
        region_text, _start = extract_managed_region(doc)
        heading = _PINNED_HEADING.search(region_text)
        end = _find_terminator_offset(
            region_text, heading.end(), _PINNED_TERMINATOR
        )
        assert region_text[end:].startswith("## Not a heading")

    def test_no_reader_was_taught_about_fences(self):
        """PR A changes NO reader. Repairing `_find_terminator_offset` would be
        an extent-contract change: on a currently-truncated pinned region a
        more complete reader RAISES the observed pin count, which can cross a
        count threshold and produce an over-block introduced BY the repair.

        The check is for a fence DELIMITER LITERAL, not for the word "fence".
        `staleness.py` already explains in prose why it does no fence tracking,
        so the word is present and is not evidence of logic. A real fence
        handler cannot be written without one of these literals, which makes
        their absence the load-bearing signal.
        """
        source = (HOOKS_DIR / "staleness.py").read_text(encoding="utf-8")
        for delimiter in ("```", "~~~"):
            assert delimiter not in source, (
                f"staleness.py contains the fence delimiter {delimiter!r}: a "
                "reader was taught about fences, which is an extent-contract "
                "change and does not belong in this PR"
            )


TERMINATOR_CORPUS = [
    ("h2 heading", "## Working Memory\n\n### 2026-01-02\nentry\n"),
    ("h1 heading", "# A top-level heading\n\nprose\n"),
    ("memory boundary", "<!-- PACT_MEMORY_END -->\n\nafter\n"),
    ("routing boundary", "<!-- PACT_ROUTING_START -->\n\nafter\n"),
    ("no terminator at all", ""),
    ("h3 is not a terminator", "### Not a terminator\nprose\n"),
    ("horizontal rule is not one", "---\nprose\n"),
    ("indented h2 is not one", "  ## Indented\nprose\n"),
    ("h2 with no space is not one", "##NoSpace\nprose\n"),
]


class TestTerminatorParityWithTheReader:
    """THE TWIN GUARD.

    `pin_markers` compiles the same terminator SHAPE that
    `staleness._parse_pinned_section` compiles inline. Two definitions of
    "where does a section end" is precisely the drift these markers exist to
    close, so reintroducing it unguarded would be self-defeating.

    The guard pins BEHAVIOUR, not pattern source. A source comparison fires on
    a semantically identical rewrite, and a guard that reddens on harmless
    edits gets weakened or deleted -- which is how the real coverage dies. So
    this drives BOTH implementations over a corpus of whole documents and
    compares the offset each one lands on. The reader is exercised through its
    own real function rather than through a copy of its regex.

    The property is the one that actually matters: THE BOUNDARY THIS WRITE
    DECLARES MUST BE THE BOUNDARY THE CURRENT READER INFERS. If those ever
    disagree, the markers stop describing the region they wrap.
    """

    @pytest.mark.parametrize(
        "label,tail", TERMINATOR_CORPUS, ids=[r[0] for r in TERMINATOR_CORPUS]
    )
    def test_the_planners_body_extent_equals_the_readers(self, label, tail):
        """RE-ANCHORED after the END marker was removed, and the re-anchoring
        is the point.

        This guard used to compare the offset the write DECLARED against the
        offset the reader INFERRED. There is no declared end any more, so that
        comparison has no left-hand side. The twin-drift RISK did not go away
        with it: this planner still compiles its own terminator, and still uses
        it to decide whether the body is empty and whether it is fenced. What
        changed is the CONSEQUENCE of drift, not its possibility.

        So the property moves to the extent both implementations measure. If
        the two terminators ever disagree, the planner and the live reader see
        different pinned bodies, and this reddens.
        """
        from staleness import _parse_pinned_section
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _PINNED_HEADING, _PINNED_TERMINATOR
        from staleness import _find_terminator_offset

        doc = (
            "# User heading\n\n"
            + MANAGED_START_MARKER + "\n"
            + "## Pinned Context\n\n### A pin\nbody prose\n\n"
            + tail
            + MANAGED_END_MARKER + "\ntrailing\n"
        )
        parsed = _parse_pinned_section(doc)
        assert parsed is not None, f"{label}: the reader found no pinned section"

        region_text, _start = extract_managed_region(doc)
        heading = _PINNED_HEADING.search(region_text)
        planner_body = region_text[
            heading.end():
            _find_terminator_offset(region_text, heading.end(), _PINNED_TERMINATOR)
        ]
        assert planner_body == parsed[2], (
            f"{label}: the planner measures a different pinned body than the "
            "reader infers. The two terminator definitions have drifted."
        )

    def test_the_parity_check_can_actually_fail(self, monkeypatch):
        """NON-VACUITY, by mutating the twin rather than the caller.

        Without this arm a parity test that compared a value to itself would
        pass forever while guarding nothing.
        """
        import shared.pin_markers as pin_markers
        from staleness import _parse_pinned_section, _find_terminator_offset
        from shared.claude_md_manager import extract_managed_region

        doc = (
            "# User heading\n\n"
            + MANAGED_START_MARKER + "\n"
            + "## Pinned Context\n\n### A pin\nbody prose\n\n"
            + "## Working Memory\n\nentry\n"
            + MANAGED_END_MARKER + "\ntrailing\n"
        )

        def planner_body():
            region_text, _s = extract_managed_region(doc)
            h = pin_markers._PINNED_HEADING.search(region_text)
            return region_text[
                h.end():
                _find_terminator_offset(
                    region_text, h.end(), pin_markers._PINNED_TERMINATOR
                )
            ]

        # Sanity: they agree before the mutation.
        assert planner_body() == _parse_pinned_section(doc)[2]

        # Mutate the planner's terminator so it no longer stops on an H2.
        monkeypatch.setattr(
            pin_markers, "_PINNED_TERMINATOR",
            re.compile(r'(?:<!-- (?:PACT_MEMORY_|PACT_MANAGED_|PACT_ROUTING_))'),
        )
        assert planner_body() != _parse_pinned_section(doc)[2], (
            "the mutated planner still agreed with the reader, so the parity "
            "assertion cannot detect drift"
        )


class TestIdempotenceOnASingleMarker:
    """Idempotence for ONE marker is a PRESENCE check and nothing more.

    The state space is exactly two -- the marker is in the file or it is not.
    There is no ordering to verify and no unpaired case to name, because
    ordering needs two things to order. The former `inverted_pair` and
    `unpaired` outcomes described states only a pair could occupy and were
    DELETED rather than left unreachable behind a comment.
    """

    def test_second_pass_is_a_noop(self):
        old = build_claude_md()
        once = apply_insertion(old, plan_insertion(old))
        assert plan_insertion(once) is SkipReason.ALREADY_MARKED

    def test_third_pass_is_still_a_noop(self):
        old = build_claude_md()
        once = apply_insertion(old, plan_insertion(old))
        assert plan_insertion(once) is SkipReason.ALREADY_MARKED
        assert plan_insertion(once) is SkipReason.ALREADY_MARKED

    def test_repeated_application_never_doubles_the_marker(self):
        old = build_claude_md()
        text = old
        for _ in range(4):
            planned = plan_insertion(text)
            if isinstance(planned, Insertion):
                text = apply_insertion(text, planned)
        assert text.count(PINNED_START_MARKER) == 1

    def test_a_lone_marker_is_half_marked_and_says_so(self):
        """SUPERSEDES `test_a_lone_marker_is_the_intended_state_not_an_error`,
        whose CLAIM this change retires.

        That test asserted a START with no END is the intended shipped state,
        and asserted `UNPAIRED` and `INVERTED_PAIR` do not exist. Every marker
        that bounds a region in PACT is a pair, so a lone marker is now a
        HALF-MARKED document -- still not an error, still not repaired here,
        but no longer the finished article.

        THE LABEL IS THE POINT. `already_marked` means "nothing to do". Filing
        a half-marked document under it hides a distinct condition beneath a
        success-shaped outcome, which is the exact shape that once hid a marker
        collision inside a completed-migration count.
        """
        old = build_claude_md()
        planned = plan_insertion(old)
        half = (old[:planned.start_offset] + START_LINE
                + old[planned.start_offset:])
        assert half.count(PINNED_START_MARKER) == 1
        assert PINNED_END_MARKER not in half, "FIXTURE INVALID: not half-marked"

        outcome = plan_insertion(half)
        assert outcome is SkipReason.UNPAIRED, (
            f"a half-marked document reported {outcome!r}; if this is "
            f"ALREADY_MARKED the distinct state is hidden under a success label"
        )
        assert outcome is not SkipReason.PLAN_FAILED

        # And the writer does NOT complete the pair: it emits both lines in one
        # composition or none. Adding the missing half would be a repair.
        assert not isinstance(outcome, Insertion)

        # The COMPLETE pair is what reports already-marked.
        both = apply_insertion(old, planned)
        assert plan_insertion(both) is SkipReason.ALREADY_MARKED

    def test_only_the_writer_emitted_POSITION_counts_as_marked(self):
        """SUPERSEDES an earlier version of this test that asserted the marker
        was detected "wherever it sits". That assertion pinned the DEFECT: a
        whole-file substring search treats a document merely MENTIONING the
        marker as already migrated, and refuses it permanently.

        The contract is now positional, and it is the PLANNER'S contract. Only
        the shape `apply_insertion` emits counts as already-migrated, so a copy
        anywhere else no longer suppresses the plan. Whether the WRITE then
        happens is a separate stage with its own answer -- see the comment on
        the loop below.
        """
        old = build_claude_md()
        planned = plan_insertion(old)
        assert isinstance(planned, Insertion), "FIXTURE INVALID"
        marked = apply_insertion(old, planned)

        # The writer's own position: marked.
        assert plan_insertion(marked) is SkipReason.ALREADY_MARKED

        # Every other position: the PLANNER no longer refuses them as
        # already-migrated, so a write is PLANNED. The WRITER may still refuse
        # a planned write -- each stray copy here sits on its own line followed
        # by a newline, so it trips the certificate and reports a collision.
        # THIS ASSERTS THE PLANNER'S CONTRACT, NOT THE WRITER'S OUTCOME, and an
        # earlier version of this comment claimed the write proceeds, which is
        # false at the writer level. That is a single-stage claim about a
        # two-stage pipeline -- the same shape as the design-document claim
        # this change set corrects, committed inside the correction itself.
        #
        # Asserted as `isinstance(..., Insertion)` and deliberately NOT as
        # `is not ALREADY_MARKED` -- "not the old wrong answer" is satisfied by
        # ANY refusal, including a wrong one, and is not the same claim as "the
        # right answer". An earlier draft of this test used the weak form.
        for label, placed in [
            ("prepended to the file", f"{PINNED_START_MARKER}\n" + old),
            ("above another heading",
             old.replace("## Working Memory", f"{PINNED_START_MARKER}\n## Working Memory")),
            ("appended to the file", old + f"{PINNED_START_MARKER}\n"),
        ]:
            assert isinstance(plan_insertion(placed), Insertion), (
                f"{label}: a stray copy did not leave the document writable "
                f"(got {plan_insertion(placed)!r})"
            )


# --------------------------------------------------------------------------
# MERGE GATE 2 -- non-denial, quantified over INPUTS
# --------------------------------------------------------------------------

def run_hook(frame, tmp_path, timeout=30):
    """Drive the REAL script as a subprocess and return (rc, stdout, stderr).

    CLAUDE_PROJECT_DIR is pinned to tmp_path so the resolver cannot reach the
    developer's own CLAUDE.md. Do not remove that.
    """
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(frame) if not isinstance(frame, str) else frame,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=carry_clock_shift(env),
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


# Every one of these is an INPUT the hook may be handed. The assertion below
# is quantified over this set, not over any message's wording.
DENIAL_CORPUS = [
    ("empty stdin", ""),
    ("not json", "this is not json at all"),
    ("json but not an object", "[1, 2, 3]"),
    ("json null", "null"),
    ("json string", '"a bare string"'),
    ("empty object", {}),
    ("no prompt key", {"hook_event_name": "UserPromptSubmit"}),
    ("prompt is not a string", {"hook_event_name": "UserPromptSubmit", "prompt": 42}),
    ("prompt is null", {"hook_event_name": "UserPromptSubmit", "prompt": None}),
    ("ordinary prompt", {"hook_event_name": "UserPromptSubmit", "prompt": "hello there"}),
    ("near-miss command", {"hook_event_name": "UserPromptSubmit",
                           "prompt": "/PACT:pin-memory-something"}),
    ("real pin command", {"hook_event_name": "UserPromptSubmit",
                          "prompt": "/PACT:pin-memory some context"}),
    ("real prune command", {"hook_event_name": "UserPromptSubmit",
                            "prompt": "/PACT:prune-memory"}),
    ("tool_input not a dict", {"hook_event_name": "PostToolUse", "tool_input": "nope"}),
    ("skill not a string", {"hook_event_name": "PostToolUse",
                            "tool_input": {"skill": []}}),
    ("skill route", {"hook_event_name": "PostToolUse",
                     "tool_input": {"skill": "PACT:pin-memory"}}),
    ("unrelated skill", {"hook_event_name": "PostToolUse",
                         "tool_input": {"skill": "some-other-skill"}}),
    ("hostile event name", {"hook_event_name": {"nested": "dict"}, "prompt": "hi"}),
    ("huge prompt", {"hook_event_name": "UserPromptSubmit", "prompt": "x" * 100000}),
    ("prompt with null bytes", {"hook_event_name": "UserPromptSubmit",
                                "prompt": "/PACT:pin-memory \x00\x01"}),
]

# Tokens that would constitute a block decision on either channel.
DENIAL_TOKENS = (
    '"decision"', '"block"', '"deny"', '"permissionDecision"',
    '"continue": false', '"continue":false', '"stopReason"',
)


class TestCannotDeny:
    """Merge gate 2. The claim is 'no input makes this hook deny', so the
    quantifier is over inputs.
    """

    @pytest.mark.parametrize("label,frame", DENIAL_CORPUS, ids=[r[0] for r in DENIAL_CORPUS])
    def test_exit_code_is_zero_for_every_input(self, label, frame, tmp_path):
        """Exit 2 is the block code on both registered channels, and any
        non-zero exit reaching the block path is indistinguishable from a
        deliberate refusal. So the assertion is exit code EQUALS zero, not
        merely 'not 2'.
        """
        rc, out, err = run_hook(frame, tmp_path)
        assert rc == 0, (
            f"{label}: exit {rc}. A non-zero exit on UserPromptSubmit blocks "
            f"the user's prompt. stderr={err[:400]}"
        )

    @pytest.mark.parametrize("label,frame", DENIAL_CORPUS, ids=[r[0] for r in DENIAL_CORPUS])
    def test_no_block_decision_is_emitted_for_any_input(self, label, frame, tmp_path):
        rc, out, err = run_hook(frame, tmp_path)
        for token in DENIAL_TOKENS:
            assert token not in out, (
                f"{label}: stdout carries {token}, which is a block decision"
            )

    @pytest.mark.parametrize("label,frame", DENIAL_CORPUS, ids=[r[0] for r in DENIAL_CORPUS])
    def test_output_is_a_valid_suppress_envelope(self, label, frame, tmp_path):
        """Every emit path must carry hookEventName. A missing or unknown one
        is a SILENT schema rejection at the platform layer.
        """
        rc, out, err = run_hook(frame, tmp_path)
        assert out.strip(), f"{label}: no output at all"
        payload = json.loads(out)
        assert payload["suppressOutput"] is True
        assert payload["hookSpecificOutput"]["hookEventName"]

    def test_the_denial_corpus_can_observe_a_denial(self, tmp_path):
        """POSITIVE CONTROL for the two assertions above.

        A script that DOES deny must fail both of them. Without this, an
        assertion that could never fire would carry the merge gate.
        """
        denier = tmp_path / "denier.py"
        denier.write_text(
            "import sys\n"
            'print(\'{"decision": "block"}\')\n'
            "sys.exit(2)\n"
        )
        proc = subprocess.run(
            [sys.executable, str(denier)], input="{}", capture_output=True,
            text=True, timeout=30,
        )
        assert proc.returncode == 2
        assert any(token in proc.stdout for token in DENIAL_TOKENS)

    def test_the_echoed_event_name_follows_the_firing_event(self, tmp_path):
        """The two registrations fire under different event names, so a
        hard-coded value would be a silent rejection on one of them."""
        for event in ("UserPromptSubmit", "PostToolUse"):
            rc, out, err = run_hook(
                {"hook_event_name": event, "prompt": "hello"}, tmp_path
            )
            assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == event


# --------------------------------------------------------------------------
# The hot path: nothing from the plugin is imported before the command test
# --------------------------------------------------------------------------

class TestAgentRouteSkillNameShape:
    """The shape a `Skill` invocation actually delivers.

    MEASURED 2026-08-01 in an isolated frame on this platform build: a PLUGIN
    skill arrives PLUGIN-QUALIFIED (`PACT:bootstrap` was observed) while a
    PROJECT-level skill arrives bare (`probeskill`). Both pin commands are
    plugin skills, so they arrive qualified.

    This is pinned as a test rather than left in a comment because it closed an
    open uncertainty by measurement: a bare-name widening had been refused on
    judgement, and the measurement showed the widening would have been both
    unnecessary AND actively wrong, since it would have matched another
    plugin's same-named skill. An uncertainty closed by measurement should not
    be able to silently re-open.
    """

    @pytest.mark.parametrize("skill_value", [
        "PACT:pin-memory", "PACT:prune-memory",
        "/PACT:pin-memory", "/PACT:prune-memory",
    ])
    def test_qualified_names_are_accepted(self, skill_value, tmp_path):
        original = build_claude_md()
        (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
        rc, out, err = run_hook(
            {"hook_event_name": "PostToolUse",
             "tool_input": {"skill": skill_value}},
            tmp_path,
        )
        assert rc == 0
        written = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert PINNED_START_MARKER in written, (
            f"{skill_value} is a pin command and must reach the write"
        )

    @pytest.mark.parametrize("skill_value", [
        "pin-memory", "prune-memory",          # bare: another plugin could own these
        "OTHER:pin-memory", "PACT:pin-memory-x", "PACT:something-else",
    ])
    def test_unqualified_and_foreign_names_are_refused(
        self, skill_value, tmp_path
    ):
        original = build_claude_md()
        (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
        rc, out, err = run_hook(
            {"hook_event_name": "PostToolUse",
             "tool_input": {"skill": skill_value}},
            tmp_path,
        )
        assert rc == 0
        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == original, (
            f"{skill_value} must NOT reach the write: the write is reachable "
            "only through a confirmed invocation of the two pin commands"
        )


class TestHotPath:

    def test_module_level_imports_are_stdlib_only(self):
        """A module-level plugin import moves failure BEFORE the command test,
        which is the one ordering this hook cannot lose. On a channel that
        fires for every prompt, that turns a plugin bug into a session-wide
        outage. Asserted structurally, on the AST, so it cannot rot.
        """
        tree = ast.parse(HOOK_SCRIPT.read_text(encoding="utf-8"))
        plugin_roots = {"shared", "staleness", "pin_caps", "pact_context"}
        offenders = []
        for node in tree.body:  # MODULE level only
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            elif isinstance(node, ast.Try):
                for sub in node.body:
                    if isinstance(sub, ast.Import):
                        targets += [a.name for a in sub.names]
                    elif isinstance(sub, ast.ImportFrom):
                        targets.append(sub.module or "")
            for name in targets:
                if name.split(".")[0] in plugin_roots:
                    offenders.append(name)
        assert offenders == [], (
            f"module-level plugin imports found: {offenders}. They must sit "
            "below the command test."
        )

    def test_hook_carries_no_banned_discriminator_or_literal(self):
        source = HOOK_SCRIPT.read_text(encoding="utf-8")
        for banned in ("is_lead", "resolve_agent_name", "session_registry"):
            assert banned not in source, (
                f"{banned} is banned in this hook: the marker write applies to "
                "every editor, with no role or frame check"
            )


# --------------------------------------------------------------------------
# End to end, through the real script and the real filesystem
# --------------------------------------------------------------------------

class TestEndToEnd:

    def _project(self, tmp_path, content):
        (tmp_path / "CLAUDE.md").write_text(content, encoding="utf-8")
        return tmp_path / "CLAUDE.md"

    def test_pin_command_writes_the_marker_and_changes_nothing_else(self, tmp_path):
        original = build_claude_md()
        target = self._project(tmp_path, original)

        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit",
             "prompt": "/PACT:pin-memory remember this"},
            tmp_path,
        )
        assert rc == 0
        written = target.read_text(encoding="utf-8")
        assert written != original, "the write did not happen"
        assert PINNED_START_MARKER in written
        assert PINNED_END_MARKER in written, (
            "the writer emitted only half the pair end to end"
        )
        # The same certificate the writer applies, re-applied from outside.
        assert len(written) == len(original) + len(START_LINE) + len(END_LINE)
        assert (
            written.replace(START_LINE, "").replace(END_LINE, "") == original
        )

    def test_an_ordinary_prompt_does_not_touch_the_file(self, tmp_path):
        original = build_claude_md()
        target = self._project(tmp_path, original)
        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit", "prompt": "what time is it"},
            tmp_path,
        )
        assert rc == 0
        assert target.read_text(encoding="utf-8") == original

    def test_a_near_miss_command_does_not_touch_the_file(self, tmp_path):
        original = build_claude_md()
        target = self._project(tmp_path, original)
        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit",
             "prompt": "/PACT:pin-memory-not-really"},
            tmp_path,
        )
        assert rc == 0
        assert target.read_text(encoding="utf-8") == original

    def test_a_file_with_no_pinned_section_is_left_alone(self, tmp_path):
        original = build_claude_md(include_pinned_heading=False)
        target = self._project(tmp_path, original)
        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/PACT:pin-memory"},
            tmp_path,
        )
        assert rc == 0
        assert target.read_text(encoding="utf-8") == original
        assert "## Pinned Context" not in target.read_text(encoding="utf-8")

    def test_running_twice_writes_the_marker_exactly_once(self, tmp_path):
        original = build_claude_md()
        target = self._project(tmp_path, original)
        for _ in range(3):
            rc, out, err = run_hook(
                {"hook_event_name": "UserPromptSubmit",
                 "prompt": "/PACT:pin-memory"},
                tmp_path,
            )
            assert rc == 0
        written = target.read_text(encoding="utf-8")
        assert written.count(PINNED_START_MARKER) == 1

    def test_a_fenced_pinned_body_is_left_untouched(self, tmp_path):
        original = build_claude_md(
            pinned_body="### A pin\n```\n## Not a heading\n```\nmore\n\n"
        )
        target = self._project(tmp_path, original)
        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/PACT:pin-memory"},
            tmp_path,
        )
        assert rc == 0
        assert target.read_text(encoding="utf-8") == original
        assert PINNED_START_MARKER not in target.read_text(encoding="utf-8")

    def test_absent_claude_md_is_never_created(self, tmp_path):
        """The hook must not bring the file into being under any circumstance."""
        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/PACT:pin-memory"},
            tmp_path,
        )
        assert rc == 0
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / ".claude" / "CLAUDE.md").exists()


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

class TestFencedBodyCensusEvent:
    """The refusal emits a countable signal.

    The frequency of a fenced pinned body has been measured on ONE disk, by the
    same people who chose the predicate -- a self-applied control over a single
    population. This event turns it into a live count across every consumer,
    and it arrives before anything decides to trust the declared boundary.
    """

    def _capture(self, monkeypatch, outcome):
        import shared.pact_context as pact_context
        import shared.session_journal as session_journal
        import pin_marker_writer

        events = []
        monkeypatch.setattr(session_journal, "append_event",
                            lambda event: events.append(event) or True)
        monkeypatch.setattr(pact_context, "init", lambda frame: None)
        pin_marker_writer._journal({}, "typed", "PACT:pin-memory", outcome)
        return events

    def test_a_fenced_skip_emits_the_census_event(self, monkeypatch):
        events = self._capture(monkeypatch, SkipReason.FENCED_BODY.value)
        types = [e["type"] for e in events]
        assert "pin_marker_write" in types
        assert "fenced_body_skipped" in types

    def test_the_census_event_carries_no_file_content(self, monkeypatch):
        events = self._capture(monkeypatch, SkipReason.FENCED_BODY.value)
        census = next(e for e in events if e["type"] == "fenced_body_skipped")
        assert set(census) == {"v", "type", "ts", "route", "command"}
        assert census["route"] == "typed"
        assert census["command"] == "PACT:pin-memory"

    def test_the_census_event_records_a_DECLINED_decision_not_a_guess(
        self, monkeypatch
    ):
        """It must NOT carry what the boundary "would have been". The only
        mechanism able to compute that is the fence tracker measured to be
        wrong on real shapes, so such a field would be a fabricated
        measurement -- worse than none, because it would read as data.
        """
        events = self._capture(monkeypatch, SkipReason.FENCED_BODY.value)
        census = next(e for e in events if e["type"] == "fenced_body_skipped")
        for forbidden in ("offset", "end_offset", "divergence", "would_have",
                          "fence_aware", "body", "content", "path"):
            assert not any(forbidden in key for key in census), (
                f"the census event carries {forbidden!r}, which would be a "
                "computed guess rather than a record of a declined decision"
            )

    @pytest.mark.parametrize("outcome", [
        "written", "noop_no_section", "noop_empty_section", "already_marked",
    ])
    def test_other_outcomes_do_not_emit_the_census_event(
        self, monkeypatch, outcome
    ):
        """NON-VACUITY. An event emitted unconditionally would count nothing."""
        events = self._capture(monkeypatch, outcome)
        assert [e["type"] for e in events] == ["pin_marker_write"]


class TestCollisionIsDistinguishableFromCompletedMigration:
    """THE OUTCOME SPLIT. A refused migration and a completed one used to be the
    same journal entry, so a collision was reported under a SUCCESS-shaped
    label and was unobservable.

    RESTORED OBSERVABILITY, NOT A NEW FEATURE: `unpaired` and `inverted_pair`
    were deleted as unreachable, they were unreachable for a PAIR reason, and
    the condition they reported migrated into the success label rather than
    vanishing with the pair.
    """

    def test_an_own_line_quote_in_the_body_reports_a_collision(self, tmp_path):
        """Narrowing the detector does not CLOSE the collision -- this document
        falls through to the write and the certificate refuses it. What changes
        is that the refusal is now named.
        """
        original = build_claude_md(
            pinned_body=f"### A pin\n{PINNED_START_MARKER}\nmore\n\n"
        )
        target = tmp_path / "CLAUDE.md"
        target.write_text(original, encoding="utf-8")
        rc, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit", "prompt": "/PACT:pin-memory"},
            tmp_path,
        )
        assert rc == 0
        assert target.read_text(encoding="utf-8") == original, (
            "the write proceeded into an ambiguous document"
        )

    def test_a_stray_own_line_copy_is_labelled_a_collision(
        self, tmp_path, monkeypatch
    ):
        """ARM 1 -- the label fires on the condition that CAUSED the failure.

        The stray copy sits under Working Memory, where the adjacency detector
        does not look, so the planner returns an `Insertion` and the
        CERTIFICATE is the mechanism that refuses. No injection: this is a
        document a user could be holding today.

        FAILING INPUT: a planner that refuses this document first -- the
        `Insertion` precondition reddens and the certificate is masked again
        -- or a certificate degraded to a constant True, which reddens the
        outcome assertion.
        """
        import pin_marker_writer
        from staleness import _resolve_project_claude_md_with_base

        original = build_claude_md(
            working=(
                f"\n### 2026-01-02\nA working entry.\n{PINNED_START_MARKER}\n"
            )
        )
        # PRECONDITIONS, asserted before any result is read.
        assert START_LINE in original, "FIXTURE INVALID: no own-line copy"
        assert isinstance(plan_insertion(original), Insertion), (
            "FIXTURE INVALID: the planner refused, so the certificate branch "
            "is never reached and any outcome below is a clean negative"
        )
        (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        resolved, _base = _resolve_project_claude_md_with_base()
        assert resolved == tmp_path / "CLAUDE.md", (
            "FIXTURE INVALID: the resolver escaped tmp_path and would read a "
            "real CLAUDE.md"
        )

        outcome = pin_marker_writer._plan_and_write()

        # The collision value is produced at exactly ONE site, inside the
        # certificate-failure branch, so the value is its own witness that the
        # branch was reached.
        assert outcome == SkipReason.MARKER_COLLISION.value

    def test_a_mid_line_mention_does_not_borrow_the_collision_label(
        self, tmp_path, monkeypatch
    ):
        """ARM 2 -- THE ARM THAT PROVES THE FIX. It cannot be built from a
        document alone; it needs an injected assembly defect.

        A mid-line mention is invisible to the certificate's unbounded
        replace, so it cannot make the certificate fail by itself. The only
        way to reach the failure branch while holding one is to break the
        ASSEMBLY. Under the old predicate -- the bare marker -- that genuine
        defect was reported as a benign collision.

        FAILING INPUT: restoring the bare marker at the label site. This test
        reddens and names the mislabel, while every other test in this file
        stays green. That is the whole reason this arm exists.
        """
        import pin_marker_writer
        import shared.pin_markers as pin_markers
        from staleness import _resolve_project_claude_md_with_base

        original = build_claude_md(
            pinned_body=(
                f"### A pin\nProse naming the {PINNED_START_MARKER} inline.\n\n"
            )
        )
        # PRECONDITIONS. The second is what makes the arm DISCRIMINATING:
        # without a bare-marker occurrence the OLD predicate would also have
        # answered "not a collision", and the arm would prove nothing.
        assert START_LINE not in original, (
            "FIXTURE INVALID: the copy is not mid-line, so the certificate "
            "would refuse for the collision reason instead"
        )
        assert PINNED_START_MARKER in original, (
            "FIXTURE INVALID: no bare mention, so the old predicate is never "
            "exercised and this arm cannot discriminate"
        )
        assert isinstance(plan_insertion(original), Insertion), (
            "FIXTURE INVALID: the planner refused before the certificate"
        )

        def broken_assembly(content, ins):
            return apply_insertion(content, ins) + "!"

        monkeypatch.setattr(pin_markers, "apply_insertion", broken_assembly)
        (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        resolved, _base = _resolve_project_claude_md_with_base()
        assert resolved == tmp_path / "CLAUDE.md", (
            "FIXTURE INVALID: the resolver escaped tmp_path and would read a "
            "real CLAUDE.md"
        )

        outcome = pin_marker_writer._plan_and_write()

        # MEASURED, NOT ASSUMED: the injection reached the writer. The import
        # of `apply_insertion` is function-local, so if it had bound an
        # unpatched reference the write would have succeeded and this would
        # read `written`.
        assert outcome != "written", (
            "the injected assembly defect never took effect, so the outcome "
            "below says nothing about the label"
        )
        assert outcome == "certificate_failed"
        assert outcome != SkipReason.MARKER_COLLISION.value

    def test_the_two_refusal_reasons_are_different_values(self):
        """A collision and an assembly defect call for opposite responses, so
        they must not share an outcome string."""
        assert SkipReason.MARKER_COLLISION.value != "certificate_failed"
        assert SkipReason.MARKER_COLLISION.value != SkipReason.ALREADY_MARKED.value

    def test_a_completed_migration_still_reports_already_marked(self):
        """NON-VACUITY for the split: if everything reported a collision, the
        distinction would be worthless."""
        old = build_claude_md()
        planned = plan_insertion(old)
        assert isinstance(planned, Insertion), "FIXTURE INVALID"
        assert plan_insertion(apply_insertion(old, planned)) is SkipReason.ALREADY_MARKED


class TestTheGuardHoldsUnderEveryLineTerminator:
    """CRLF TWINS. The corpus that missed this held line endings CONSTANT
    across every arm, so no number of arms could see it. Each test here states
    a boundary property and then asserts it under more than one terminator.

    THE DEFECT WAS AN EMERGENT GUARD. Nothing implemented `refuse a document
    that already carries a marker`. It fell out of the certificate's unbounded
    replace stripping two copies when both ended LF. A guard that exists only
    as a byte accident stops existing when the bytes change.
    """

    def _doc(self, newline, pinned_body):
        return build_claude_md(pinned_body=pinned_body).replace("\n", newline)

    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
    def test_a_document_already_carrying_a_marker_line_is_refused(self, newline):
        """THE FIX. Under LF this was refused by accident; under CRLF it was
        not refused at all and a second marker was written.

        FAILING INPUT: restoring the accident -- deleting the collision clause
        from the certificate. The `crlf` arm reddens; the `lf` arm does NOT,
        because the replace still catches it there. The lf arm is the CONTROL
        that shows the crlf arm is measuring the terminator and not the rule.
        """
        original = self._doc(newline, f"### A pin\n{PINNED_START_MARKER}\nmore\n\n")
        assert marker_line_present(original, PINNED_START_MARKER), (
            "FIXTURE INVALID: no marker line"
        )
        planned = plan_insertion(original)
        assert isinstance(planned, Insertion), (
            "FIXTURE INVALID: the planner refused, so the certificate is never "
            "consulted and the result below is a clean negative"
        )
        composed = apply_insertion(original, planned)
        assert composed.count(PINNED_START_MARKER) == 2, (
            "FIXTURE INVALID: the composition does not carry the second marker "
            "this test exists to refuse"
        )
        assert certify_expel_nothing(original, composed, planned) is False

    @pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
    def test_a_mid_line_mention_still_writes_under_either_terminator(self, newline):
        """THE OVER-REACH GUARD, and it is the reason the fix is a line
        predicate rather than a substring test. Prose naming the marker must
        still be written into.

        FAILING INPUT: widening the collision clause to `marker in old`. Both
        arms redden, which is what distinguishes over-reach from the defect --
        the fix reddens one arm, over-reach reddens both.
        """
        original = self._doc(
            newline, f"### A pin\nProse naming the {PINNED_START_MARKER} inline.\n\n"
        )
        assert PINNED_START_MARKER in original, "FIXTURE INVALID: no mention"
        assert not marker_line_present(original, PINNED_START_MARKER), (
            "FIXTURE INVALID: the mention occupies a line, so this is the "
            "collision case rather than the mid-line case"
        )
        planned = plan_insertion(original)
        assert isinstance(planned, Insertion), "FIXTURE INVALID: planner refused"
        assert certify_expel_nothing(
            original, apply_insertion(original, planned), planned
        ) is True

    def test_marker_line_present_is_terminator_agnostic(self):
        """The property is `occupies a line`, not `is followed by one specific
        byte sequence`. CR is included because `splitlines` splits on it, so
        excluding it would be an untested asymmetry rather than a decision.

        FAILING INPUT: reimplementing the predicate as `START_LINE in text`.
        The crlf and cr cases go False and this reddens.
        """
        for newline in ("\n", "\r\n", "\r"):
            text = f"above{newline}{PINNED_START_MARKER}{newline}below{newline}"
            assert marker_line_present(text, PINNED_START_MARKER) is True, (
                f"missed {newline!r}"
            )
        assert marker_line_present(
            f"prose {PINNED_START_MARKER} inline\n", PINNED_START_MARKER
        ) is False
        assert marker_line_present("no marker here\r\n", PINNED_START_MARKER) is False

    def test_the_shipped_terminator_pattern_carries_no_end_anchor(self):
        """A SWEEP RESULT PINNED, because the site is safe by its CALLERS
        rather than by construction.

        `_find_terminator_offset` matches a compiled pattern against lines that
        RETAIN a trailing carriage return. An end-anchored pattern therefore
        fails to match on CRLF and the scan returns `len(content)`, its
        NOT-FOUND sentinel -- silently extending the region to end of file.
        Measured: `^## Working Memory$` returns 24 on LF and the sentinel on
        CRLF, while the unanchored twin is correct on both.

        The shipped pattern is unanchored, so nothing is broken today. This
        pins the property so a future end-anchor is caught by a test rather
        than by a region that quietly swallows the rest of the document.

        FAILING INPUT: appending `$` to `_PINNED_TERMINATOR`.
        """
        from shared.pin_markers import _PINNED_TERMINATOR

        assert not _PINNED_TERMINATOR.pattern.rstrip().endswith("$"), (
            "an end-anchored terminator pattern silently fails on CRLF and "
            "extends the pinned region to end of file"
        )


class TestRegistration:

    @pytest.fixture
    def config(self):
        return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

    def _commands(self, entries):
        out = []
        for entry in entries:
            for hook in entry.get("hooks", []):
                out.append(hook)
        return out

    def test_registered_on_both_routes(self, config):
        ups = self._commands(config["hooks"]["UserPromptSubmit"])
        assert any("pin_marker_writer.py" in h["command"] for h in ups)

        post = config["hooks"]["PostToolUse"]
        skill_groups = [g for g in post if g.get("matcher") == "Skill"]
        assert len(skill_groups) == 1
        assert any(
            "pin_marker_writer.py" in h["command"]
            for h in skill_groups[0]["hooks"]
        )

    def test_both_registrations_are_async(self, config):
        """async is the STRUCTURAL half of the non-denial guard, measured with
        a control: the same script registered sync and exiting 2 blocks the
        prompt; registered async it does not, while its sentinel proves it ran.
        """
        found = 0
        for event in ("UserPromptSubmit", "PostToolUse"):
            for hook in self._commands(config["hooks"][event]):
                if "pin_marker_writer.py" in hook["command"]:
                    found += 1
                    assert hook.get("async") is True, (
                        f"the {event} registration is not async"
                    )
        assert found == 2

    def test_user_prompt_entry_is_appended_after_the_existing_three(self, config):
        """An entry inserted between the bootstrap writer and the prompt gate
        breaks a pinned order assertion elsewhere in the suite.
        """
        commands = [h["command"] for h in
                    self._commands(config["hooks"]["UserPromptSubmit"])]
        index = next(i for i, c in enumerate(commands) if "pin_marker_writer.py" in c)
        writer = next(i for i, c in enumerate(commands) if "bootstrap_marker_writer.py" in c)
        gate = next(i for i, c in enumerate(commands) if "bootstrap_prompt_gate.py" in c)
        assert writer < gate
        assert index > gate
        assert index == len(commands) - 1

    def test_the_registered_script_exists(self, config):
        assert HOOK_SCRIPT.is_file()


# --------------------------------------------------------------------------
# Non-goal
# --------------------------------------------------------------------------

def test_the_reader_is_wired_to_the_END_marker_only():
    """SUPERSEDES `test_no_reader_is_wired_to_the_markers`, whose CLAIM this
    change retires.

    That test pinned a STAGING decision -- ship the marker inert, wire a reader
    later -- and the user retired that charter. A declared END with no reader
    and no marker-aware writer is not a smaller change than this one, it is a
    BROKEN one: the writer would append new pins below the marker where no cap
    measures them.

    THE ASYMMETRY IS THE REAL CONTRACT AND IT IS WHAT THIS TEST NOW PINS. The
    parse consumes the END marker, because that is where the region stops. It
    has no reason to consult the START marker: the pinned scan BEGINS at the
    `## Pinned Context` heading, so the START declares a boundary the parser
    already knows. Wiring it would add a second opinion about a settled offset.
    """
    staleness_source = (HOOKS_DIR / "staleness.py").read_text(encoding="utf-8")
    assert "PINNED_END_MARKER" in staleness_source, (
        "the parse no longer reads the END marker, so the declared region end "
        "is unwired and the pair is inert again"
    )
    assert "PINNED_START_MARKER" not in staleness_source, (
        "the parse has started consulting the START marker; the pinned scan "
        "already begins at the heading and does not need a second opinion"
    )


# --------------------------------------------------------------------------
# The anchor window
# --------------------------------------------------------------------------

def doc_with_a_heading_above_the_memory_region() -> str:
    """A production-shaped document carrying a SECOND pinned heading ABOVE the
    memory region, inside the session block the production emitter puts there.

    THE FIXTURE IS DERIVED FROM THE PRODUCTION EMITTER AND THEN MODIFIED, never
    typed. `build_claude_md` supplies the shipping boundary layout, and this
    function splices one heading in above the memory START marker. A
    hand-written approximation of that layout is the defect this corpus was
    corrected for, so an arm that re-introduced one would test its author's
    idea of the document instead of the document.
    """
    doc = build_claude_md()
    at = doc.index(MEMORY_START_MARKER)
    return doc[:at] + "## Pinned Context\n\nINJECTED payload.\n\n" + doc[at:]


def doc_without_the_memory_markers() -> str:
    """The same production-shaped document with the memory marker PAIR removed.

    DERIVED BY DELETION FROM THE EMITTER'S OWN OUTPUT, which is what models the
    only route to this state: a user hand-edits a block the file labels
    do-not-edit. Every emitter writes the two markers unconditionally, so no
    shipped code path produces this shape and no fixture should claim to
    reproduce one.
    """
    doc = build_claude_md()
    return (
        doc
        .replace(MEMORY_START_MARKER + "\n", "")
        .replace(MEMORY_END_MARKER + "\n", "")
    )


class TestTheAnchorWindowIsTheMemoryRegion:
    """The window the anchor search runs in must be the region the target is
    DEFINED to live in.

    The pinned heading lives in the memory region. The managed region also
    holds the session block ABOVE it, so a search bounded to the managed
    region matches a heading there FIRST and the marker lands on it. Neither
    downstream guard catches that: the certificate declines placement in its
    own docstring, and the collision label answers a different question.

    THE ORACLE IS TWO-SIDED, and both sides are asserted below: the genuine
    anchor must STILL RESOLVE at its position inside the memory region, and
    the injected one must NOT be reachable. A one-sided arm that only checked
    the genuine anchor would pass under the wide window too, because the wide
    window still contains the genuine heading.

    THE BOUND ON THIS ORACLE, STATED SO IT IS NOT READ AS MORE: every document
    here CARRIES the memory markers, so these arms sit entirely inside the
    population the narrowing already serves. They VERIFY a narrowing. They
    CANNOT SCOPE one, and they say nothing about the marker-less case, which
    is the separate class below.
    """

    def test_the_genuine_anchor_inside_the_memory_region_still_resolves(self):
        """The narrowing must not cost the ordinary document its anchor."""
        doc = build_claude_md()
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion), (
            f"an ordinary production-shaped document was refused: {planned}"
        )
        assert doc[planned.start_offset:].startswith("## Pinned Context"), (
            "the START offset does not begin the pinned heading line"
        )
        assert planned.start_offset > doc.index(MEMORY_START_MARKER), (
            "the anchor resolved ABOVE the memory start marker"
        )

    def test_a_heading_injected_above_the_memory_region_is_not_reachable(self):
        """FAILING INPUT: widening the window back to `extract_managed_region`.

        Under the wide window the injected heading is the FIRST match, so the
        marker lands above the memory region and the first assertion reddens.
        """
        doc = doc_with_a_heading_above_the_memory_region()
        assert doc.count("## Pinned Context") == 2, (
            "FIXTURE INVALID: the injected heading is not in the document"
        )
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion), (
            f"FIXTURE INVALID: the planner refused, so nothing below is a "
            f"placement result: {planned}"
        )
        assert planned.start_offset > doc.index(MEMORY_START_MARKER), (
            "the anchor resolved on the INJECTED heading above the memory "
            "region, which is the defect this window narrowing closes"
        )

    def test_the_composed_document_marks_only_the_genuine_heading(self):
        """The same claim at the BYTES, one layer out from the offset.

        An offset assertion can hold while the composition still puts a marker
        somewhere unintended, so the placement is also read off the emitted
        document.
        """
        doc = doc_with_a_heading_above_the_memory_region()
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion), "FIXTURE INVALID: refused"
        composed = apply_insertion(doc, planned)

        above = composed[:composed.index(MEMORY_START_MARKER)]
        assert START_LINE not in above, (
            "a marker line landed ABOVE the memory region, on the injected "
            "heading rather than the declared one"
        )
        assert START_LINE + "## Pinned Context\n" in composed, (
            "the marker is not immediately above a pinned heading at all"
        )
        assert composed.count(START_LINE) == 1, (
            "more than one start marker was emitted"
        )


class TestAnAbsentMemoryRegionRefuses:
    """When the memory marker pair is absent, the write REFUSES rather than
    widening back to the managed region.

    WHY WIDENING IS UNSAFE ON EXACTLY THIS DOCUMENT. The emitter writes the
    managed marker, the title, THEN the session block, THEN the memory marker,
    so the session block sits ABOVE the memory marker BY CONSTRUCTION.
    Removing the memory markers does not remove the session block, so this
    document STILL HOLDS A POSITION where a heading can sit above the pinned
    section. A fall-back restores the wide window on the one shape that has
    that position.

    THIS CLASS IS SEPARATE FROM THE WINDOW CLASS ABOVE BECAUSE THE TWO HAVE
    DIFFERENT MUTANTS. Widening the window kills the window arms. Converting
    this refusal to a fall-back kills these arms while the window arms stay
    green, because their documents carry the pair and never reach a fall-back.
    """

    def test_a_managed_region_with_no_memory_pair_is_refused(self):
        """FAILING INPUT: replacing the refusal with a fall-back to the
        managed region. The planner then returns an Insertion and this
        reddens.
        """
        doc = doc_without_the_memory_markers()
        planned = plan_insertion(doc)
        assert planned is SkipReason.NO_MEMORY_REGION, (
            f"expected a refusal naming the absent memory region, got {planned}"
        )

    def test_the_refusal_is_not_one_of_the_earlier_ladder_steps(self):
        """The document must reach the memory-region step to be refused there.

        Without this the arm above could pass on a document refused for having
        no managed region or no pinned heading, which are different rungs and
        would make the verdict mean nothing about this one.
        """
        from shared.claude_md_manager import extract_managed_region

        doc = doc_without_the_memory_markers()
        region = extract_managed_region(doc)
        assert region is not None, (
            "FIXTURE INVALID: no managed region, so the refusal would be "
            "NOT_MIGRATED and this arm would be measuring the wrong rung"
        )
        assert "## Pinned Context" in region[0], (
            "FIXTURE INVALID: no pinned heading inside the managed region, so "
            "the refusal could be NO_SECTION instead"
        )

    def test_the_same_document_with_its_pair_restored_is_planned(self):
        """POSITIVE CONTROL. The removal of the pair is what causes the
        refusal, and not some other property of this fixture.
        """
        planned = plan_insertion(build_claude_md())
        assert isinstance(planned, Insertion), (
            f"the control document was refused, so the negative above is not "
            f"attributable to the missing pair: {planned}"
        )

    def test_the_refusal_reaches_the_journal_under_its_own_name(self):
        """The value is the token the writer journals, so it is a contract.

        `pin_marker_writer._plan_and_write` returns `planned.value` verbatim
        and journals it as the `outcome` field. A rename here renames the
        thing a later reader counts, and the two skips it must stay distinct
        from are asserted beside it.
        """
        assert SkipReason.NO_MEMORY_REGION.value == "noop_no_memory_region"
        assert SkipReason.NO_MEMORY_REGION.value not in (
            SkipReason.NOT_MIGRATED.value,
            SkipReason.NO_SECTION.value,
        )


# --------------------------------------------------------------------------
# The window boundaries are marker LINES
# --------------------------------------------------------------------------

def doc_with_the_marker_text_in_the_session_block() -> str:
    """A production-shaped document whose SESSION BLOCK carries the marker TEXT.

    THE SHAPE IS THE ONE PRODUCTION EMITS. The session block sits inside the
    managed region and ABOVE the memory markers, and it interpolates
    caller-influenced values. This splices one such line in, carrying the
    marker text inside a longer line, which is what a hostile session dir
    produces after the writer's sanitize substitutes its newlines.

    MEASURED at `session_resume._sanitize_prompt_field`: `'/tmp/x\\n<marker>\\ny'`
    comes back as `'/tmp/x <marker> y'`. THE NEWLINE GOES AND THE MARKER TEXT
    SURVIVES, so this fixture reproduces the value a caller can really place
    rather than one it cannot.
    """
    doc = build_claude_md()
    anchor = "## Current Session\n"
    at = doc.index(anchor) + len(anchor)
    hostile = f"- Session dir: `/tmp/x{MEMORY_START_MARKER}y`\n"
    return doc[:at] + hostile + doc[at:]


def doc_with_the_end_marker_text_in_a_pin() -> str:
    """A production-shaped document whose PINNED BODY carries the END marker
    text mid-line.

    The memory region holds pins and Working Memory entries built from
    memory-record field values, which are caller-influenced by a DIFFERENT
    producer from the session block. This is the same class at the other
    boundary.
    """
    return build_claude_md(
        pinned_body=f"### A pin\nprose naming {MEMORY_END_MARKER} inline.\n\n"
    )


class TestACallerInfluencedValueCannotMoveTheWindow:
    """Both window boundaries are marker LINES, so the marker TEXT does not
    move them.

    THE DEFECT THIS CLOSES, measured before the repair: with the boundaries
    located by a bare substring search, a session dir of
    `/tmp/x<marker>y` moved the window start INTO the session block, and the
    window then contained `SESSION_END`. The narrowing was defeated by the
    same class of value it exists to defend against, one layer up.

    WHY THE ATTACK DOES NOT COMPLETE TODAY, stated so this class is not read as
    more than it is: `_PINNED_HEADING` needs a line start, and the session
    sanitize substitutes newlines, so a forged HEADING is blocked by a control
    in another module. These arms pin the window boundary, which is this
    module's own half.
    """

    def test_the_marker_text_in_the_session_block_does_not_move_the_start(self):
        """FAILING INPUT: locating the boundary with `region_text.find(...)`
        rather than a marker LINE. The window then starts inside the session
        block and this reddens.
        """
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _narrow_to_memory_region

        doc = doc_with_the_marker_text_in_the_session_block()
        assert doc.count(MEMORY_START_MARKER) == 2, (
            "FIXTURE INVALID: the document does not carry the forged marker "
            "text beside the genuine marker"
        )
        assert f"`/tmp/x{MEMORY_START_MARKER}y`" in doc, (
            "FIXTURE INVALID: the forged text is not inside a longer line, so "
            "this fixture is not the shape a caller can produce"
        )

        region = extract_managed_region(doc)
        assert region is not None, "FIXTURE INVALID: no managed region"
        narrowed = _narrow_to_memory_region(region[0], region[1])
        assert narrowed is not None, "the window was refused on a sound document"

        window_text, _offset = narrowed
        assert "SESSION_END" not in window_text, (
            "the window starts inside the SESSION BLOCK and swallows it, "
            "which is the region this narrowing exists to exclude"
        )
        assert window_text.startswith("## Retrieved Context"), (
            "the window does not begin at the line after the genuine memory "
            "start marker"
        )

    def test_the_planner_still_anchors_on_the_declared_heading(self):
        """POSITIVE CONTROL AT THE PUBLIC PATH. NOT A KILL ARM, and the
        difference is recorded so this is not counted as coverage.

        MEASURED: this arm survives every mutant tried against the boundary
        logic and against the comparator. It cannot separate them, because the
        fixture carries a forged MARKER and not a forged HEADING, and the
        heading half of the attack is blocked by
        `session_resume._sanitize_prompt_field` in another module. So the
        public path produces the same answer with the window moved or not.

        WHAT IT IS FOR: the repair must not refuse a sound document, and this
        is what would redden if the narrowing became too tight. It measures
        the over-block direction, which is the fault this repository treats as
        cardinal.
        """
        doc = doc_with_the_marker_text_in_the_session_block()
        planned = plan_insertion(doc)
        assert isinstance(planned, Insertion), (
            f"the planner refused a sound document: {planned}"
        )
        assert doc[planned.start_offset:].startswith("## Pinned Context"), (
            "the START offset does not begin the pinned heading line"
        )
        composed = apply_insertion(doc, planned)
        above = composed[:composed.index("## Current Session")]
        assert START_LINE not in above, (
            "a marker line landed above the session heading"
        )

    def test_the_end_marker_text_in_a_pin_does_not_truncate_the_window(self):
        """The same rule at the OTHER boundary, with a different producer.

        FAILING INPUT: locating the end with `region_text.find(...)`. The
        window then stops at the pin that merely names the marker, and the
        pinned body is cut short.
        """
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _narrow_to_memory_region

        doc = doc_with_the_end_marker_text_in_a_pin()
        assert doc.count(MEMORY_END_MARKER) == 2, (
            "FIXTURE INVALID: the pin does not carry the end marker text"
        )
        region = extract_managed_region(doc)
        assert region is not None, "FIXTURE INVALID: no managed region"
        narrowed = _narrow_to_memory_region(region[0], region[1])
        assert narrowed is not None, "the window was refused on a sound document"

        window_text, _offset = narrowed
        assert "## Working Memory" in window_text, (
            "the window was truncated at a pin that merely names the end "
            "marker, so the memory region lost its tail"
        )

    def test_an_indented_marker_line_is_accepted(self):
        """THE TOLERANCE, PINNED IN THE DIRECTION IT WAS CHOSEN.

        `marker_line_span` compares STRIPPED, so an indented but faithful
        marker line is still a boundary. A raw comparison would refuse this
        document, which is the over-block direction.

        MEASURED, and recorded because the two predicates genuinely differ:
        `_find_terminator_offset` matches the RAW line and does NOT match this
        one. The disagreement does not reach the planner, because that scan
        runs INSIDE the window produced here, so the marker line is excluded
        before it is ever judged.
        """
        from shared.claude_md_manager import extract_managed_region
        from shared.pin_markers import _narrow_to_memory_region

        doc = build_claude_md().replace(
            MEMORY_START_MARKER + "\n", "    " + MEMORY_START_MARKER + "\n"
        )
        region = extract_managed_region(doc)
        assert region is not None, "FIXTURE INVALID: no managed region"
        narrowed = _narrow_to_memory_region(region[0], region[1])
        assert narrowed is not None, (
            "an indented but faithful marker line was refused, which is an "
            "over-block on a document the plugin itself could emit"
        )
        assert "SESSION_END" not in narrowed[0]
