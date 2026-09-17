"""
Structural pin tests for the teammate wait-discipline layers (issue #1620).

Pins the "Wait Discipline for Self-Started Work" subsection added to the
Intentional Waiting section of skills/pact-agent-teams/SKILL.md — the
teammate-side teaching of the discipline the wait_filler_gate hook already
enforces (the hook landed agent-agnostic in PR #1615; the teaching surface
had not received it, so teammates got the gate's refusals without the prose
that explains them).

Layer pins are INDIVIDUALLY DISTINGUISHABLE: each layer is pinned by phrases
that occur only inside that layer's paragraph (occurrence-census verified at
authoring time — see the census record at the bottom of this file), so
deleting or rewording one layer flips exactly that layer's pins while the
other two stay green. The layer-ordering test additionally pins the
sequence L1 -> L2 -> L3 within the section.

PRESENCE pins, not counts — same convention as test_wake_ordering_pinned.py
(see that module's docstring for the lockstep-cost rationale). Matching
reuses its _phrase/_lines_outside_fences helpers via sibling import:
backtick-and-whitespace-normalized phrase matching over the FENCE-EXCLUDED
lines — a pinned phrase is real prose and is not satisfiable by fenced
example content (the heading pins' fence exclusion extended to phrases;
measured hole this closed at the bottom of this file) — plus line-anchored
exact heading pins outside fenced code blocks.

No protocol-extract mirror: the SET/CLEAR contract the extracts reference is
unchanged by this amendment (it adds a wait-classification subsection, not
new flag mechanics), so no pact-protocols.md byte-mirror is owed — verified
by grepping protocols/*.md for intentional_wait SET/CLEAR cross-references
at authoring time; none teach wait classification for self-started work.
"""

from pathlib import Path

import pytest

from test_wake_ordering_pinned import (  # noqa: E402 — sibling harness reuse
    _lines_outside_fences,
    _phrase,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILL = PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"

DISCIPLINE_HEADING = "### Wait Discipline for Self-Started Work"

# The class-split lead: the subsection exists because protocol waits and
# self-started work have different resolvers. Pinning the split's two arms
# catches a rewrite that collapses the classes back into one (the original
# gap: prose that taught only protocol waits).
CLASS_SPLIT_PINS = [
    "The waits above are **protocol waits**: a named resolver drives completion",
    "has no external resolver; nobody but you is watching it",
]

# One anchored phrase per layer (plus supporting witnesses). Layer anchors
# are the bold leads; witness phrases pin the load-bearing mechanics inside
# each layer so a body-gutting that keeps the lead line still flips pins.
LAYER_1_PINS = [
    "Layer 1 — In-turn when it fits",
    "whenever it fits the Bash tool timeout (declared max 600000 ms)",
    "the wait never exists as a turn boundary",
]
LAYER_2_PINS = [
    "Layer 2 — Never hold an unflagged dependency",
    "Before ending ANY turn whose deliverable depends on unfinished work",
    "the dead-man's handle that makes a stalled watcher detectable instead of silent",
    "it does not depend on any wake channel",
]
LAYER_3_PINS = [
    "Layer 3 — Escalate what you cannot hold",
    "must not sit invisibly in a backgrounded process",
    # The deviation-justification clause — the minimal in-process not-woken
    # rationale that stops a teammate reasoning its way back to
    # background-and-end-turn.
    "when you run in-process it surfaces only when something else starts your next turn, so the team-lead's channel is the only push you can count on",
    "Either split the work into timeout-sized chunks run in-turn, or transfer the watch explicitly",
    "flag the wait with expected_resolver=lead",
]
SILENCE_PINS = [
    "Silence is uninformative in both directions, and narrating a wait is noise in both",
    "manufactures the next turn without producing new information",
    # The hook is named so the refusal a teammate actually receives is taught,
    # not merely enforced (missed_wake_scan is named the same way in this
    # section's intro).
    "wait_filler_gate",
    "end it with no tool call at all",
]


def _fence_excluded(path: Path) -> str:
    """Backtick-and-whitespace-normalized text of the fence-EXCLUDED lines.
    Phrase pins match real prose only: a pinned phrase moved — or
    duplicated — into a fenced code block satisfies no pin and counts in no
    census (see the fence counter-check record at the bottom of this
    file)."""
    return _phrase("\n".join(_lines_outside_fences(path)))


def test_discipline_heading_present():
    """The subsection must exist as an exact heading line (fence-excluded,
    line-anchored) so it is discoverable and its anchor slug is a stable
    cross-ref target — same heading contract as test_wake_ordering_pinned."""
    lines = _lines_outside_fences(SKILL)
    assert DISCIPLINE_HEADING in lines, (
        f"{SKILL.name}: heading {DISCIPLINE_HEADING!r} not found as an exact "
        f"line. If the subsection was renamed, update this pin in lockstep."
    )


def _intentional_waiting_section() -> str:
    """Fence-excluded, normalized text of the '## Intentional Waiting'
    section only, so the ordering pin cannot be satisfied by layer mentions
    elsewhere in the file or by fenced example content inside the
    section."""
    lines = _lines_outside_fences(SKILL)
    starts = [i for i, line in enumerate(lines) if line == "## Intentional Waiting"]
    assert starts, "SKILL.md must contain '## Intentional Waiting' section"
    start = starts[0]
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return _phrase("\n".join(lines[start:end]))


def test_layers_ordered_within_section():
    """The three layers must appear in L1 -> L2 -> L3 order inside the
    Intentional Waiting section. The escalation layers read as a progression
    (fits -> flag -> escalate); reordering them breaks the teaching sequence
    even if every phrase pin stays green."""
    section = _intentional_waiting_section()
    positions = [
        section.find(_phrase(pin))
        for pin in (
            "Layer 1 — In-turn when it fits",
            "Layer 2 — Never hold an unflagged dependency",
            "Layer 3 — Escalate what you cannot hold",
        )
    ]
    assert all(pos != -1 for pos in positions), (
        f"layer anchor missing in Intentional Waiting section: "
        f"positions {positions} — a layer lead was deleted or reworded"
    )
    assert positions == sorted(positions), (
        f"layer anchors out of order (L1, L2, L3 expected): {positions}"
    )


def _pin_cases():
    cases = []
    for name, pins in (
        ("class-split", CLASS_SPLIT_PINS),
        ("layer-1", LAYER_1_PINS),
        ("layer-2", LAYER_2_PINS),
        ("layer-3", LAYER_3_PINS),
        ("silence", SILENCE_PINS),
    ):
        cases.extend((f"{name}::{pin[:44]}", pin) for pin in pins)
    return cases


@pytest.mark.parametrize(
    "phrase",
    [pin for _, pin in _pin_cases()],
    ids=[case_id for case_id, _ in _pin_cases()],
)
def test_wait_discipline_phrase_present(phrase: str):
    """Each load-bearing phrase of the wait-discipline layers must be present
    on the teammate teaching surface as REAL PROSE. Matching is
    backtick-and-whitespace-normalized over the fence-excluded lines:
    hard-wrap and inline-code renderings both satisfy the pin, fenced
    example content does NOT (moving the teaching into a code fence erodes
    the surface and must flip the pin), and a re-WORD does not. If a phrase
    was changed intentionally, update the pin in lockstep — otherwise the
    layer it carries eroded on a runtime-loaded surface."""
    assert _phrase(phrase) in _fence_excluded(SKILL), (
        f"{SKILL.name}: wait-discipline phrase {phrase!r} not found "
        f"(backtick-and-whitespace-normalized match). If the wording was "
        f"changed intentionally, update the pin in lockstep; otherwise a "
        f"wait-discipline layer is missing from the teammate teaching "
        f"surface."
    )


# ---------------------------------------------------------------------------
# Counter-test flip-set record (measured 2026-09-10, issue #1620 amendment).
# With skills/pact-agent-teams/SKILL.md alone stash-reverted to its
# pre-amendment state (this test module left at amended HEAD): exactly
# {20 failed, 0 passed} — the full module flips; no pin is satisfiable by
# pre-amendment prose. Post-restore: 20/20 green.
#
# Occurrence census (measured, fence-excluded normalized text): every
# pinned phrase occurs exactly 1x in SKILL.md, so the layers are
# INDIVIDUALLY DISTINGUISHABLE — deleting any one layer's paragraph flips
# exactly that layer's phrase pins plus the shared ordering pin (its
# missing-anchor assert), while the other two layers' phrase pins stay
# green.
#
# Fence-exclusion counter-check (measured 2026-09-10, remediation cycle 2):
# moving a pinned phrase out of prose and back in only inside a ``` fence —
# pre-fix module stayed {20 passed} (the hole: fenced content satisfied the
# phrase pin); fence-excluded matching flips exactly the moved phrase's pin
# {1 failed, 19 passed}. A fenced DUPLICATE (prose occurrence kept) leaves
# the module green and the census at 1x — the fenced copy is invisible to
# pins and census alike (pre-fix census counted it: 2x).
# ---------------------------------------------------------------------------
