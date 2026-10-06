#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/pin_markers.py

Summary: Pure planner for the declared `## Pinned Context` marker PAIR. Decides
WHERE the two markers go and WHETHER they go in at all, composes the new file
content, and certifies that the composition expelled nothing. No I/O, no
filesystem, no hook frame, no exceptions.

EVERY LOCATION COMES FROM THE FENCE-AWARE PARSER, `shared.claude_md_markers`.
This module splits no lines and searches no text. The managed block, the
memory block and the marker pair are `find_block` lookups, the section is one
`find_section` call inside the memory block, and the two insertion offsets are
`Line.start` values, so both are line starts by construction. A fenced,
indented-code or commented-out copy of a heading or a marker is therefore an
example and never an anchor, and a lookup the parser cannot answer with
certainty (UNKNOWN, DUPLICATE, MALFORMED) is a refusal that names the line.

A PAIR, EMITTED IN ONE COMPOSITION. Both marker lines go in together or neither
does. A document carrying exactly one of them is HALF-MARKED: not an error, and
not repaired here, but reported as `unpaired` rather than as a success.

THE PAIR AND THE MARKER-AWARE WRITER ARE ONE UNIT, and that is the safety
argument rather than a preference. An END marker with a marker-BLIND pin writer
CREATES a gap it did not have before: the writer anchors on the heading, appends
at the end of the section, and lands the new pin BELOW the END marker where no
cap measures it. So `commands/pin-memory.md` must place new pins above the END
marker, and this module is only half of that story. That half is an
instruction, not a mechanism: no code path in this repository inserts a pin,
and the caps gate only judges the resulting Edit.

Used by: `hooks/pin_marker_writer.py`, which owns every side effect -- stdin,
path resolution, the file lock, the atomic write and the journal. Keeping the
planner pure lets a test drive whole documents through one function and
compare before against after, with no hook process and no disk; the cap this
feature leads to is a two-state predicate, so a region change has to be
certified on DOCUMENT PAIRS.

THE SHAPE OF THE WRITE IS THE SAFETY ARGUMENT. The insertion is PURE OFFSET
INSERTION: two literals are spliced in at two computed offsets and no existing
byte is parsed, rewritten, reordered or dropped. A rebuild from parsed entries
silently deletes whatever it does not recognise, and the target file is
gitignored and unrecoverable. See `certify_expel_nothing` for the mechanical
certificate, and read its scope note before trusting it to cover placement.

SPANS ONLY SHORTEN. This module adds two marker lines and nothing else. The
pin readers take the declared END as a ceiling on the section they count, so a
marker placed here can pull a counted span in and never push it out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
)
from shared.claude_md_markers import Document, Located, State, parse, uncertainty_added

# The section's heading, terminator and stop prefixes, and the block-interior
# helper, are the Pinned locator's own (`staleness.locate_pinned`), so the
# section this planner marks is the section every pin reader counts.
from staleness import _PINNED_HEADING, _PINNED_STOP_PREFIXES, _PINNED_TERMINATOR, _interior

# The literal lines that get spliced in. The trailing newline is part of each
# unit: the certificate below is stated over these LINES, not over the bare
# markers, so a marker and its newline can never be accounted separately.
START_LINE = PINNED_START_MARKER + "\n"
END_LINE = PINNED_END_MARKER + "\n"


def is_line_start(text: str, offset: int) -> bool:
    """True when `offset` begins a line of `text`.

    Every offset the planner produces is a `Line.start`, so this holds by
    construction there. The certificate still checks it, because a mid-line
    offset splits a user's line in two and leaves a fragment on each side of
    the marker, and every other clause of the certificate would pass.

    OFFSET 0 IS HANDLED FIRST: written as a bare `text[offset - 1] == "\\n"`,
    offset 0 would read the LAST character of the file.
    """
    if offset <= 0:
        return offset == 0
    if offset > len(text):
        return False
    return text[offset - 1] == "\n"


@dataclass(frozen=True)
class Insertion:
    """Where the two marker lines go, as absolute offsets into the FULL file.

    TWO offsets and TWO literals, and the ORDER BETWEEN THEM IS A REAL STATE
    THAT CAN BE WRONG: crossed offsets duplicate bytes, so the certificate's
    length assertion catches them. BOTH OFFSETS ARE LINE STARTS.

    Carries the literals but NOT the new content. The writer composes the new
    content through `apply_insertion`, so exactly one site in the codebase
    assembles these bytes and the certificate can wrap that one site.
    """

    start_offset: int   # start of the `## Pinned Context` heading line
    end_offset: int     # start of the row that ends the section
    start_line: str
    end_line: str


class SkipReason(str, Enum):
    """Why no insertion happened. Journalled, so a later reader can tell WHICH
    precondition declined rather than only that nothing occurred.
    """

    # No managed block. Migration emits the `## Pinned Context` heading and the
    # managed markers by the same mechanism, so a file without the block has no
    # section this writer could mark.
    NOT_MIGRATED = "noop_not_migrated"
    # The managed block is there and the MEMORY block inside it is not.
    # REFUSE rather than widen to the managed block: the session block sits
    # ABOVE the memory block by construction, so a wider window can anchor on
    # a heading there first. Emitting the missing pair is refused too, because
    # it would write into a block the file labels do-not-edit.
    NO_MEMORY_REGION = "noop_no_memory_region"
    # No visible `## Pinned Context` heading in the memory block. Placing the
    # markers would mean CREATING the section, which this write never does.
    NO_SECTION = "noop_no_section"
    # A heading with only blank rows under it. A boundary around it is one no
    # reader believes in.
    EMPTY_SECTION = "noop_empty_section"
    ALREADY_MARKED = "already_marked"
    # Both markers sit in the memory block, the END above the START. This
    # writer will not repair it: moving a marker mutates existing bytes, which
    # the pure-insertion shape excludes.
    INVERTED_PAIR = "inverted_pair"
    # Exactly ONE of the two markers sits in the memory block. Completing the
    # pair would be a repair, and this writer emits both lines or none.
    UNPAIRED = "unpaired"
    # A complete pair sits in the memory block, but not on the two rows this
    # writer emits it on (directly above the heading, and the row that ends
    # the section). Not this writer's output, so it is left alone.
    MARKER_COLLISION = "noop_marker_collision"
    # Totality guard: `plan_insertion` promises never to raise, and a caller
    # that hands in a non-`str` gets this refusal rather than a traceback out
    # of a hook that is forbidden to fail.
    PLAN_FAILED = "error_plan_failed"


@dataclass(frozen=True)
class Refusal:
    """A lookup the parser could not answer with certainty: UNKNOWN, DUPLICATE
    or MALFORMED, for the managed block, the memory block, the section or the
    marker pair; or UNKNOWN for the text the insertion would produce. The
    writer leaves the file untouched and journals `value`, which names the
    line through `Located.reason`.
    """

    located: Located

    @property
    def value(self) -> str:
        return f"refused_{self.located.state.value.lower()}: {self.located.reason}"


def _pair_state(
    doc: Document, scope: tuple[int, int], heading: int, last: int
) -> SkipReason | Refusal | None:
    """The marker pair's state in the memory block, or None when neither
    marker is there and the pair can go in.

    The pair counts as this writer's own only on the two rows it emits:
    START directly above the heading, END on the row that ends the section.
    """
    pair = doc.find_block(PINNED_START_MARKER, PINNED_END_MARKER, scope)
    if pair.state is State.ABSENT:
        return None
    if pair.state is State.FOUND:
        if pair.spans[0] == (heading - 1, last + 1):
            return SkipReason.ALREADY_MARKED
        return SkipReason.MARKER_COLLISION
    if pair.state is State.MALFORMED:
        # One marker of each with the END first is an inverted pair; one
        # marker alone is half a pair. Every other defect (a stray, a second
        # copy of either marker) is refused with the parser's reason.
        start = doc.find_marker(PINNED_START_MARKER, scope).state
        end = doc.find_marker(PINNED_END_MARKER, scope).state
        if start is State.FOUND and end is State.FOUND:
            return SkipReason.INVERTED_PAIR
        if {start, end} == {State.FOUND, State.ABSENT}:
            return SkipReason.UNPAIRED
    return Refusal(pair)


def plan_insertion(content: str) -> Insertion | SkipReason | Refusal:
    """Decide what to insert, or why not. Pure. Never raises.

    Each step resolves the block that encloses the next, and acts on its state
    before looking inside it:

    1. The managed block. ABSENT -> NOT_MIGRATED.
    2. The memory block, inside the managed block. ABSENT -> NO_MEMORY_REGION.
    3. The `## Pinned Context` section, inside the memory block: its first
       visible heading through the row before the next H1 or H2 heading or
       PACT boundary marker. ABSENT -> NO_SECTION. Only blank rows under the
       heading -> EMPTY_SECTION. Fenced lines are code, so a heading-shaped
       line inside a fenced snippet in a pin body does not end the section.
    4. The marker pair, inside the memory block (see `_pair_state`).
    5. The text the insertion would produce. A marker row is an HTML comment
       line, so it can end a comment or declaration the user opened above the
       heading and closed inside the section. More rows PACT cannot read than
       the file has -> a `Refusal` naming the line.

    At any step, UNKNOWN, DUPLICATE or MALFORMED -> a `Refusal` naming the
    line, so the file is left byte-identical rather than guessed at.
    """
    try:
        doc = parse(content)
        managed = doc.find_block(MANAGED_START_MARKER, MANAGED_END_MARKER)
        if managed.state is State.ABSENT:
            return SkipReason.NOT_MIGRATED
        if managed.state is not State.FOUND:
            return Refusal(managed)

        memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER, _interior(managed))
        if memory.state is State.ABSENT:
            return SkipReason.NO_MEMORY_REGION
        if memory.state is not State.FOUND:
            return Refusal(memory)
        scope = _interior(memory)

        section = doc.find_section(
            _PINNED_HEADING, _PINNED_TERMINATOR, scope, stop_prefixes=_PINNED_STOP_PREFIXES
        )
        if section.state is State.ABSENT:
            return SkipReason.NO_SECTION
        if section.state is not State.FOUND:
            return Refusal(section)
        heading, last = section.spans[0]
        if all(not doc.lines[row].content.strip() for row in range(heading + 1, last + 1)):
            return SkipReason.EMPTY_SECTION

        declined = _pair_state(doc, scope, heading, last)
        if declined is not None:
            return declined

        # `last + 1` always exists: the memory END marker row closes the scope,
        # and it is a stop prefix, so the section ends on or before the row
        # above it.
        insertion = Insertion(
            start_offset=doc.lines[heading].start,
            end_offset=doc.lines[last + 1].start,
            start_line=START_LINE,
            end_line=END_LINE,
        )
        reason = uncertainty_added(doc, parse(apply_insertion(content, insertion)))
        if reason is not None:
            return Refusal(Located(State.UNKNOWN, (), reason, None))
        return insertion
    except Exception:  # noqa: BLE001 -- totality guard; see SkipReason.PLAN_FAILED
        return SkipReason.PLAN_FAILED


def apply_insertion(content: str, ins: Insertion) -> str:
    """Compose the new content. The ONLY site that assembles these bytes.

    The START line goes above the `## Pinned Context` heading and the END line
    above the row that ends the section, each on its own line, so the heading
    and its body stay exactly where they already sit.
    """
    return (
        content[:ins.start_offset]
        + ins.start_line
        + content[ins.start_offset:ins.end_offset]
        + ins.end_line
        + content[ins.end_offset:]
    )


def certify_expel_nothing(old: str, new: str, ins: Insertion) -> bool:
    """Return True iff `new` is `old` plus exactly the two marker lines, at the
    two planned offsets, both of which are line starts in `old`.

    THIS IS A REFUSAL, NOT A TEST. The writer runs it before the write and
    skips the write when it returns False, so a composition that cannot be
    proven byte-preserving never reaches the disk.

    SCOPE: THIS CERTIFIES TWO STRINGS, NOT A FILE. The writer reads with
    universal-newline translation, so a CRLF document arrives here already
    converted to LF; that conversion is inherited from every writer of the
    file and this function cannot see it.

    What it checks, each clause independently of `apply_insertion`:

    - both offsets are line starts in `old` (a mid-line offset splits a user's
      line, and every byte-level clause below would still pass);
    - the length grew by exactly the two lines, which catches a dropped or
      duplicated byte and CROSSED offsets (the middle slice comes back empty
      and the tail is emitted twice);
    - the two lines sit at their planned positions in `new`, and cutting them
      out at those positions gives back `old` exactly, which catches a byte
      that moved without the length changing.

    The cut is POSITIONAL, never a search for the marker text: a copy of the
    marker lines inside a fenced example elsewhere in the file is user text,
    and it neither blocks the write nor stands in for the inserted line.

    IT DOES NOT PROVE THE MARKERS LANDED IN THE RIGHT PLACE. Any pair of
    ordered line-start offsets passes. Placement comes from the planner's
    lookups and is constrained by tests. A document that already carries the
    pair, or one marker of it, is refused by the planner before this runs.

    Returns False rather than raising on any anomaly, including a non-`str`
    argument: every failure of this function must land on the refuse side.
    """
    try:
        start, end = ins.start_offset, ins.end_offset
        start_len, end_len = len(ins.start_line), len(ins.end_line)
        if not (is_line_start(old, start) and is_line_start(old, end)):
            return False
        if len(new) != len(old) + start_len + end_len:
            return False
        moved_end = end + start_len
        if new[start:start + start_len] != ins.start_line:
            return False
        if new[moved_end:moved_end + end_len] != ins.end_line:
            return False
        return new[:start] + new[start + start_len:moved_end] + new[moved_end + end_len:] == old
    except Exception:  # noqa: BLE001 -- a certificate must refuse, never raise
        return False
