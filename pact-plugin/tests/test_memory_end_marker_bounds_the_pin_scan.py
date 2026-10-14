"""The pin scan stops at the memory end marker.

WHAT THIS GUARDS. `staleness.locate_pinned` resolves the memory block by its
two markers and searches for the Pinned section inside it, and the section's
stop prefixes, built from `PACT_BOUNDARY_PREFIXES`, also end it at any
`<!-- PACT_MEMORY_` line. So the memory end marker bounds the pinned body two
ways, and the controls below show what happens when neither holds: a body that
runs on swallows the text below the marker, and a memory start marker left
without its end makes the block MALFORMED, so the section is not read at all.

THE RENAME IS THE EDIT THIS FILE EXISTS TO REDDEN. It happens in
`claude_md_manager`, which is neither the reader nor the pin writer, so the
comments at those two functions do not reach the person who makes it.

=====================================================================
THE SHAPE RULING, AND WHY THERE IS NO STRUCTURAL ARM HERE
=====================================================================

A structural arm would assert that `MEMORY_END_MARKER` starts with one member
of `PACT_BOUNDARY_PREFIXES`. THAT IS NOT A TAUTOLOGY: the marker text and the
prefix tuple are two constants, and one is not computed from the other, so a
rename moves one side and not the other.

IT IS OMITTED FOR COVERAGE, NOT FOR TAUTOLOGY. Such an arm relates two
constants and runs no consumer. The property does not live in the constants. It
lives in the reader, which builds its terminator from the prefixes. Give that
function its own alternation, or drop its boundary branch, and the two constants
keep their relation while the bound is gone. A structural arm is GREEN through
that. The drive below is RED through it, and RED through the rename as well, so
the structural shape kills a strict subset and earns no place.

THE TWO SIDES OF THE DRIVE ARE INDEPENDENT. Side one is the INPUT document,
built from `MEMORY_END_MARKER` imported below. Side two is the OBSERVED OUTPUT
of the shipped reader, produced by a terminator built from
`PACT_BOUNDARY_PREFIXES`. One is a constant and the other is a run, so the
second side does not restate the first.

=====================================================================
THE FIXTURE RULES, EACH ANSWERING A MEASURED WAY THIS FILE GOES BLIND
=====================================================================

1. THE MARKER IS IMPORTED, NEVER SPELLED. A spelled `<!-- PACT_MEMORY_END -->`
   survives the rename in this file. It continues to carry the `PACT_MEMORY_`
   prefix, so the reader continues to stop, AND THIS FILE STAYS GREEN WHILE THE
   PROPERTY IS DEAD. That is the accurate defect these arms are built for, and a
   spelled literal is the one edit that hides it.

2. THE DOCUMENT CARRIES NO `## Working Memory` HEADING, AND THE OMISSION IS THE
   WHOLE REASON THE ARMS SEPARATE. MEASURED across four document shapes: with
   that heading between the pins and the memory end marker, the scan stops at
   the HEADING, the marker is never reached, and a renamed marker returns a body
   byte-identical to the canonical one. The arms measure nothing in that shape.
   Only a document with no terminator between the pinned body and the marker
   reaches the bound. Do not add a section heading below the pins here.

3. THE ABSENCE ASSERTION IS PAIRED WITH A PRESENCE ONE. The guard arm asserts
   the body EXCLUDES the text after the marker, and an absence is satisfied by a
   parse that returned nothing at all. So it also asserts the parse resolved and
   that the body carries the pin. Without that, total instrument failure reads
   as a pass.

WHAT THESE ARMS DO NOT COVER, STATED SO EACH NEGATIVE IS READABLE.

- THE SHIPPED TEMPLATE EMITS `## Working Memory` BELOW THE PINS, so on a
  document that template produces, the pinned scan stops at that heading and
  this bound is not reached. The bound is a LAST-RESORT one, and it is reached
  through a document that lost the heading, which a user edit can produce. These
  arms therefore guard a shape the emitter does not make.
- THEY DO NOT ATTRIBUTE THE STOP. They observe that the scan stopped, not WHY.
  A future reader that stops at the same offset for a different cause is green
  here, and green is correct in that case.
- THEY COVER THE READER ONLY. The pin writer names `MEMORY_END_MARKER`
  directly, so a rename breaks it loudly there and needs no arm.
"""

import staleness
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
)

# The text below the memory end marker. If the bound is gone, the pinned body
# swallows this line, so its presence in the body IS the observable.
BEYOND_THE_REGION = "SENTINEL-BELOW-THE-MEMORY-END-MARKER"

# A name outside `PACT_BOUNDARY_PREFIXES`. It is spelled here ON PURPOSE, and
# the reason is the opposite of fixture rule 1: this literal must NOT track the
# production marker. It stands for "a marker the alternation does not match",
# which is what the production marker becomes after the rename this file
# guards. Rule 1 governs the marker under test, not this one.
A_MARKER_OUTSIDE_THE_PREFIX_FAMILY = "<!-- NOT_A_PACT_BOUNDARY_END -->"

PIN_BODY = "### A pin\nPinned prose.\n"


def build_document(end_marker: str, start_marker: str = MEMORY_START_MARKER) -> str:
    """Compose a managed document with `end_marker` closing the memory region.

    The memory region holds Retrieved Context and Pinned Context and NO Working
    Memory heading. See fixture rule 2: a heading there terminates the scan
    before the marker, and the two arms then read the same document.
    """
    return (
        "# Project Memory\n"
        "\n"
        f"{MANAGED_START_MARKER}\n"
        "<!-- PACT_SESSION_START -->\n"
        "## Current Session\n"
        "- Resume: a session line\n"
        "<!-- PACT_SESSION_END -->\n"
        f"{start_marker}\n"
        "## Retrieved Context\n"
        "\n"
        "## Pinned Context\n"
        "\n"
        f"{PIN_BODY}"
        f"{end_marker}\n"
        f"{BEYOND_THE_REGION}\n"
        f"{MANAGED_END_MARKER}\n"
    )


def test_the_pinned_body_stops_at_the_memory_end_marker():
    """The guard. A rename out of the prefix family reddens this.

    The three assertions are ordered coarse to fine, so the leg that reddens
    first names the defect.
    """
    parsed = staleness._parse_pinned_section(build_document(MEMORY_END_MARKER))

    assert parsed is not None, (
        "The pinned section did not resolve at all, so the assertions below "
        "measure nothing. Repair this before reading the rest of the file."
    )
    body = parsed[2]

    assert "### A pin" in body, (
        "The body does not carry the pin, so this arm is not reading the "
        "pinned section and its absence check proves nothing."
    )
    assert BEYOND_THE_REGION not in body, (
        "The pinned body ran past the memory end marker. The section is "
        "searched for inside the memory block, and the PACT_MEMORY_ stop "
        "prefix ends it at that marker too; both bounds have gone."
    )
    assert MEMORY_END_MARKER not in body, (
        "The body carries the memory end marker line itself, so the scan "
        "stopped past it rather than at it."
    )


def test_a_marker_outside_the_prefix_family_does_not_bound_the_scan():
    """The separation control. It shows the guard arm can go red.

    Without this, a document that stopped the scan for some other cause would
    pass the guard while measuring nothing. This arm drives the same document
    with neither memory marker in place, so no memory block bounds the search
    and no stop prefix matches the substitute end line, and the body then
    carries the text that the guard arm requires to be absent.
    """
    parsed = staleness._parse_pinned_section(
        build_document(A_MARKER_OUTSIDE_THE_PREFIX_FAMILY,
                       start_marker="<!-- NOT_A_PACT_BOUNDARY_START -->")
    )

    assert parsed is not None, (
        "The control document did not resolve a pinned section, so this "
        "control cannot show the separation it exists to show."
    )
    body = parsed[2]

    assert BEYOND_THE_REGION in body, (
        "A marker outside PACT_BOUNDARY_PREFIXES bounded the scan anyway, so "
        "the guard arm above passes for a cause other than the prefix "
        "membership it claims to measure. The fixture separates nothing."
    )


def test_a_memory_start_left_without_its_end_reads_nothing():
    """The other half of the control: only the end marker renamed.

    The memory start marker then has no end, the block is MALFORMED, and the
    section is not read at all, so the text below can never be swallowed.
    """
    assert staleness._parse_pinned_section(
        build_document(A_MARKER_OUTSIDE_THE_PREFIX_FAMILY)
    ) is None
