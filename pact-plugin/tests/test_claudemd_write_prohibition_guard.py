"""Structural prose-surface regression guard for the CLAUDE.md write prohibition.

A teammate must not write a ``CLAUDE.md`` file in a project directory or a home
directory. The rule lives only in prose, in the pact-agent-teams skill body,
and the worktree boundary check allow-lists ``CLAUDE.md``, so no mechanical
control stands behind it. A later edit can narrow it back with nothing red.

THE NARROWING THIS GUARDS. The rule first said "do not edit or create". That
wording names a tool call the teammate ISSUES. It does not reach a write the
teammate CAUSES through a script, a command, or a save path it invokes, and
that indirect route is the one that produced the write. So the load-bearing
property is the ROUTE CLASS, not the presence of the rule: an edit that keeps
the prohibition and drops the route class re-opens the hole.

TWO CARRIER KINDS, AND THE SECOND IS THE ONE A TEAMMATE READS. The skill body
holds the rule for a teammate that loads the skill. The DISPATCH TEMPLATES in
``commands/`` emit their own copy of the rule into a task description, and that
copy reaches a teammate that never loads the skill body. A rule widened in the
skill alone stays narrow on the surface that carries it to its obeyer.

SLICE RULE FOR THE SKILL BODY, stated beside the assertions that use it: the
guarded region is the blockquote paragraph that opens with the bold marker
``**CLAUDE.md is not yours to write**`` and runs to the next blank line. The
slice is paragraph-bounded rather than file-bounded, so a route token sitting
in some unrelated part of the skill cannot satisfy these arms.

SLICE RULE FOR A TEMPLATE SITE, and it does different work. A template region
begins at ``TEMPLATE_START_ANCHOR`` and ends at the first
``TEMPLATE_END_ANCHOR`` after it, inclusive. It is ANCHOR-BOUNDED rather than
line-bounded, and that is load-bearing rather than a detail: a comPACT mission
is ONE physical line that also says "Do not create new documentation artifacts"
and names ``docs/``, so a line-bounded slice would let an unrelated route word
elsewhere on that line satisfy an arm. A missing anchor is a LOUD RED and not
an empty slice.

Keyed on structural tokens (semantic co-occurrence), not on exact prose, so
benign rewording survives and a narrowing fails. THE TOKEN SET HAS ONE HOME:
the same constants and the same predicates run over the skill body and over
each template region, so the two kinds of carrier cannot drift into different
definitions of the property.

  W1 — the prohibition paragraph is present and addresses the teammate.
  W2 — the paragraph carries the ROUTE CLASS: it says the rule is not limited
       to a directly-issued tool call, and it names indirect routes.
  W3 — the paragraph keeps its two bounds (target and role) and stays
       unconditional, so it does not slide back behind a worktree condition.
  W4 — each declared template site yields a region, the regions agree with
       each other, and each one satisfies W1 to W3.
  W5 — no command file names an EDITING ACT against ``CLAUDE.md`` without the
       route class. This reaches a SIXTH site on the day it is written, which
       the declared list cannot do.

WHAT A GREEN HERE DOES NOT MEAN. The five template regions are compared to EACH
OTHER for byte agreement, and to the skill body only at the PROPERTY level.
They are paraphrases in a different frame rather than byte copies: the skill
body is a standalone blockquote and a template region sits inside a quoted
dispatch string with placeholders. A byte comparison across the two frames
would redden on each benign edit, and a guard that reddens on benign edits
trains its reader to quiet it.

THE RESIDUAL, AT ITS TRUE WIDTH. A reword of the skill body that CHANGES the
route class also changes what these shared constants require, so the template
arms redden with it. The shape no arm here catches is a skill-body reword that
KEEPS the same route class and changes the meaning some other way.
"""
import re
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_glob as _tracked_glob

PLUGIN_ROOT = Path(__file__).parent.parent
AGENT_TEAMS_SKILL = PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md"

BLOCK_MARKER = "**CLAUDE.md is not yours to write**"

# Indirect routes the widened rule must name. Two of these must appear, so
# dropping one phrasing in a reword survives and dropping the CLASS fails.
#
# THIS SET HAS ONE HOME AND BOTH CARRIER KINDS READ IT. The skill-body arms and
# the template arms below share these constants, so the property cannot be
# widened on one surface and left narrow on the other.
INDIRECT_ROUTE_TOKENS = ("script", "command you invoke", "save path")
INDIRECT_ROUTE_FLOOR = 2

# THE DECLARED TEMPLATE SITES, and the value is the COUNT of regions in that
# file rather than a boolean. Counting is the point: `comPACT.md` carries the
# rule at TWO dispatch shapes, and a repair that fixed one of them would leave
# the other narrow while a presence check stayed green.
#
# THIS IS A FLOOR AND IT REFUSES MOVEMENT IN EITHER DIRECTION. A site deleted
# leaves the compared set in silence, which is the shrinkage a derived walk
# cannot see. A site ADDED without an entry here also reddens, which forces a
# sixth template to join the agreement and the property arms on the day it is
# written. Do not edit a count to quiet a red. Read the file first.
DECLARED_TEMPLATE_SITES = {
    "commands/orchestrate.md": 1,
    "commands/comPACT.md": 2,
    "commands/peer-review.md": 1,
    "commands/rePACT.md": 1,
}

# THE SLICE ANCHORS FOR A TEMPLATE REGION. A region runs from the start anchor
# to the first end anchor after it, inclusive.
#
# WHY ANCHORS AND NOT A MARKER PAIR. A marker comment placed here would sit
# INSIDE a quoted dispatch string, so it would be copied into every emitted
# task description and shipped to every teammate, in a place no reader can act
# on it.
#
# WHY NOT THE PHYSICAL LINE. A comPACT mission is ONE line that also says "Do
# not create new documentation artifacts" and names `docs/`. A line-bounded
# slice would let a route word elsewhere on that line satisfy an arm.
TEMPLATE_START_ANCHOR = "As a teammate, do NOT write a `CLAUDE.md` file"
TEMPLATE_END_ANCHOR = "flag it in your HANDOFF instead."

# ARM W5. A line that names an EDITING ACT against `CLAUDE.md`. The rule this
# guards was once written that way, and the narrow form is what a later editor
# reaches for.
#
# EACH PATTERN IS BOUND TO ITS OBJECT, and that binding is what keeps the arm
# quiet on a correct file. A verb alone would select the comPACT mission line,
# because that line says "Do not create new documentation artifacts" and names
# `CLAUDE.md` elsewhere on the same line. The object is the discriminator.
NARROW_ACT_PATTERNS = (
    r"(?:do not|don't|never)\s+(?:edit|create)(?:\s+or\s+(?:edit|create))?\s+"
    r"(?:a\s+)?`?claude\.md",
    r"(?:do not|don't|never)\s+edit\s+it\b",
)


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so token checks survive line-wrapping."""
    return re.sub(r"\s+", " ", text.lower())


# ---------------------------------------------------------------------------
# THE PROPERTY PREDICATES.
#
# ONE DEFINITION EACH, called by the skill-body arm AND by the template arm.
# A template arm that re-implemented a predicate would drift from it, and each
# hand-carry of an edit is correct until the one that is not. This is what the
# module docstring means by "the token set has one home": the tokens and the
# predicates that read them sit here, and the two carrier kinds share them.
#
# Each takes an ALREADY NORMALIZED block and returns True when the property
# holds.
# ---------------------------------------------------------------------------


def addresses_the_teammate(block: str) -> bool:
    return "as a teammate" in block


def forbids_the_write(block: str) -> bool:
    return bool(re.search(r"do not write a `?claude\.md`? file", block))


def is_not_limited_to_a_direct_tool_call(block: str) -> bool:
    # The coupling is "not only" bound to a tool name, inside this region. A
    # revert to "do not edit or create ..." drops it.
    return bool(re.search(r"not only an?\s+`?(edit|write)`?", block))


def names_indirect_routes(block: str) -> bool:
    found = [t for t in INDIRECT_ROUTE_TOKENS if t in block]
    return len(found) >= INDIRECT_ROUTE_FLOOR


def binds_the_indirect_write_to_the_teammate(block: str) -> bool:
    return "that write is yours" in block


def keeps_the_target_bound(block: str) -> bool:
    return "project directory" in block and "home directory" in block


def stays_unconditional_across_worktree_state(block: str) -> bool:
    return "with or without a worktree" in block


def keeps_the_handoff_hatch(block: str) -> bool:
    return "flag it in your handoff" in block


def the_hatch_does_not_renarrow_the_class(block: str) -> bool:
    return "instead of editing it directly" not in block


# THE SHARED PROPERTY SET. Each entry names the property and the consequence of
# losing it, so a fault report from a template region reads without a lookup.
PROPERTY_PREDICATES = (
    ("addresses the teammate role", addresses_the_teammate),
    ("forbids WRITING the file", forbids_the_write),
    ("says the rule is not limited to a direct Edit or Write",
     is_not_limited_to_a_direct_tool_call),
    ("names the indirect routes", names_indirect_routes),
    ("says the indirect write belongs to the teammate",
     binds_the_indirect_write_to_the_teammate),
    ("keeps the target bound (project and home directory)",
     keeps_the_target_bound),
    ("stays unconditional across worktree state",
     stays_unconditional_across_worktree_state),
    ("keeps the HANDOFF escape hatch", keeps_the_handoff_hatch),
    ("does not re-narrow the hatch to a direct edit",
     the_hatch_does_not_renarrow_the_class),
)


def property_faults(block: str) -> list:
    """The properties `block` does NOT satisfy, by name."""
    return [name for name, predicate in PROPERTY_PREDICATES if not predicate(block)]


def template_regions(path: Path) -> list:
    """Each prohibition region in one template file, per the slice rule.

    A region runs from `TEMPLATE_START_ANCHOR` to the first
    `TEMPLATE_END_ANCHOR` after it, inclusive. A start with no end after it is
    DROPPED rather than run to end-of-file: a slice that ran to the end would
    swallow the rest of the command file, and each property arm would then read
    words the rule does not contain. The count arm reports the loss.
    """
    text = path.read_text(encoding="utf-8")
    regions = []
    cursor = 0
    while True:
        start = text.find(TEMPLATE_START_ANCHOR, cursor)
        if start == -1:
            return regions
        end = text.find(TEMPLATE_END_ANCHOR, start)
        if end == -1:
            return regions
        stop = end + len(TEMPLATE_END_ANCHOR)
        regions.append(text[start:stop])
        cursor = stop


def narrow_act_lines(path: Path) -> list:
    """Lines in `path` that name an editing act against CLAUDE.md with no route
    class beside it.

    THE SLICE HERE IS THE LINE, and that is correct for THIS arm alone. It asks
    whether a narrow prohibition is written anywhere in the file, so its unit is
    the text a later editor types rather than the region the other arms read.
    Each pattern is bound to its object, so the unrelated "Do not create new
    documentation artifacts" on the same comPACT line is a non-member.
    """
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        low = _norm(line)
        if "claude.md" not in low:
            continue
        if not any(re.search(p, low) for p in NARROW_ACT_PATTERNS):
            continue
        if is_not_limited_to_a_direct_tool_call(low):
            continue
        found.append(f"{path.name}:{number}")
    return found


@pytest.fixture(scope="module")
def block() -> str:
    """The normalized prohibition paragraph, per the slice rule in the module
    docstring. Fails loudly when the marker is gone, so a renamed heading is a
    red arm rather than a silently empty slice."""
    text = AGENT_TEAMS_SKILL.read_text(encoding="utf-8")
    idx = text.find(BLOCK_MARKER)
    assert idx != -1, (
        f"{AGENT_TEAMS_SKILL.name} no longer carries the prohibition marker "
        f"{BLOCK_MARKER!r}. Either the rule was removed or its marker was "
        f"renamed; both leave every arm in this file measuring nothing."
    )
    end = text.find("\n\n", idx)
    paragraph = text[idx:] if end == -1 else text[idx:end]
    assert paragraph.strip(), "prohibition paragraph sliced empty"
    return _norm(paragraph)


class TestW1ProhibitionPresent:
    """The rule is present and addresses the teammate."""

    def test_addresses_the_teammate(self, block):
        assert addresses_the_teammate(block), (
            "prohibition no longer addresses the teammate role; the role bound "
            "is what keeps the orchestrator and the secretary outside the rule"
        )

    def test_forbids_the_write(self, block):
        assert forbids_the_write(block), (
            "prohibition no longer forbids WRITING the file"
        )


class TestW2RouteClass:
    """The widened class: the rule covers a write the teammate CAUSES, not only
    a tool call it ISSUES. This is the arm that reddens on a narrowing edit."""

    def test_states_the_rule_is_not_limited_to_a_direct_tool_call(self, block):
        assert is_not_limited_to_a_direct_tool_call(block), (
            "prohibition lost the clause saying the rule is NOT limited to an "
            "Edit or a Write the teammate issues. Without it the sentence "
            "names an editing ACT, and a write caused through a script or a "
            "save path reads as out of scope."
        )

    def test_names_indirect_routes(self, block):
        found = [t for t in INDIRECT_ROUTE_TOKENS if t in block]
        assert names_indirect_routes(block), (
            f"prohibition names only {len(found)} indirect route(s) {found}; "
            f"floor is {INDIRECT_ROUTE_FLOOR} of {list(INDIRECT_ROUTE_TOKENS)}. "
            "The indirect route is the one that produced the write this rule "
            "exists to stop."
        )

    def test_binds_the_indirect_write_to_the_teammate(self, block):
        assert binds_the_indirect_write_to_the_teammate(block), (
            "prohibition names indirect routes but no longer says the "
            "resulting write belongs to the teammate, so a reader can treat a "
            "script's write as somebody else's action"
        )


class TestW3BoundsAndUnconditionality:
    """The two bounds do different work and neither substitutes for the other.
    The rule must also stay unconditional: it was once gated behind a worktree
    condition, which switched it off in the case where it carries weight."""

    def test_keeps_the_target_bound(self, block):
        assert keeps_the_target_bound(block), (
            "prohibition lost its target bound (a project directory or a home "
            "directory); without it a document a test builds in a temporary "
            "directory falls inside the rule"
        )

    def test_stays_unconditional_across_worktree_state(self, block):
        assert stays_unconditional_across_worktree_state(block), (
            "prohibition lost its unconditional statement. A worktree-gated "
            "rule is off in the one case where the file is present and the "
            "write lands on it."
        )

    def test_handoff_escape_hatch_is_a_write_not_an_edit(self, block):
        # The escape hatch must not re-narrow the class it just widened.
        assert keeps_the_handoff_hatch(block), (
            "prohibition lost the handoff escape hatch, which is what makes "
            "the rule actionable when a task asks for a CLAUDE.md update"
        )
        assert the_hatch_does_not_renarrow_the_class(block), (
            "the handoff escape hatch says 'instead of editing it directly', "
            "which re-narrows the widened class back to a direct edit"
        )


class TestW4TheDispatchTemplatesCarryTheSameRule:
    """ARM W4. THE SURFACE A TEAMMATE ACTUALLY READS.

    The skill body can fail to load. A dispatch template always reaches the
    teammate it dispatches, because its text IS the task description. So a rule
    that is wide in the skill and narrow here is narrow where it counts.
    """

    def test_the_instrument_finds_the_declared_sites(self):
        """A CONTROL ON THE WALK, and it must come before each arm below.

        AN EMPTY REGION SET SATISFIES EVERY OTHER ARM IN THIS CLASS. A renamed
        anchor, a moved file, or a reworded sentence yields zero regions, and
        an agreement arm over zero regions is green.
        """
        faults = []
        for name, expected in sorted(DECLARED_TEMPLATE_SITES.items()):
            path = PLUGIN_ROOT / name
            if not path.is_file():
                faults.append(f"{name}: no file at that path")
                continue
            found = len(template_regions(path))
            if found != expected:
                faults.append(f"{name}: {found} region(s), declared {expected}")
        assert not faults, (
            f"the declared template sites do not match the tree: {faults}. "
            f"THREE CAUSES, AND THEY MEAN DIFFERENT THINGS. A site was "
            f"REMOVED, which is the silent shrinkage this floor refuses. A "
            f"site was ADDED and not declared, which leaves a new dispatch "
            f"template outside every arm here. Or an anchor was reworded, "
            f"which leaves the region unreadable. Slice rule: a region runs "
            f"from {TEMPLATE_START_ANCHOR!r} to the first "
            f"{TEMPLATE_END_ANCHOR!r} after it. Read the file. DO NOT edit a "
            f"count to quiet this red."
        )

    def test_the_template_copies_agree_with_each_other(self):
        """The copies are compared to EACH OTHER, and not to the skill body.

        THE COMPARISON IS CASE-SENSITIVE after a whitespace flatten. The rule
        shouts `do NOT write`, and a copy that says the same words in a quieter
        voice is a weaker rule for a reader who scans. A casefolded compare
        would pass that in silence.
        """
        seen = {}
        for name in sorted(DECLARED_TEMPLATE_SITES):
            path = PLUGIN_ROOT / name
            if not path.is_file():
                continue
            for index, region in enumerate(template_regions(path)):
                seen[f"{name}#{index}"] = re.sub(r"\s+", " ", region).strip()
        distinct = set(seen.values())
        assert len(distinct) <= 1, (
            f"the dispatch templates carry {len(distinct)} different wordings "
            f"of one rule, across {sorted(seen)}. One rule held in many places "
            f"drifts, and a reader of any single template cannot tell. Copy "
            f"one region over the others. IF THE CHANGE IS DELIBERATE, edit "
            f"every site in ONE commit. That tax is the mechanism rather than "
            f"an accident of it."
        )

    def test_each_template_copy_carries_the_route_class(self):
        """The property arm, over the SAME predicates the skill body uses."""
        faults = {}
        for name in sorted(DECLARED_TEMPLATE_SITES):
            path = PLUGIN_ROOT / name
            if not path.is_file():
                continue
            for index, region in enumerate(template_regions(path)):
                missing = property_faults(_norm(region))
                if missing:
                    faults[f"{name}#{index}"] = missing
        assert not faults, (
            f"these dispatch-template copies lost a property of the rule: "
            f"{faults}. The load-bearing one is the ROUTE CLASS: a copy that "
            f"names only an editing act does not reach a write the teammate "
            f"CAUSES through a script, a command, or a save path it invokes, "
            f"and that indirect route is the one that produced the write this "
            f"rule exists to stop."
        )


def _command_files(root):
    """Tracked command templates; a working-tree walk reads scratch files."""
    return sorted(_tracked_glob(root, "commands/*.md", allow_empty=True))


class TestW5NoCommandFileNarrowsTheRule:
    """ARM W5. THE ARM THAT REACHES A SIXTH SITE.

    `DECLARED_TEMPLATE_SITES` is a LIST, so it covers what somebody wrote down.
    This reads every command file, so a NEW template that names an editing act
    against CLAUDE.md reddens on the day it is written, with no edit here.
    """

    def test_the_command_walk_finds_files(self):
        """A control. An empty walk agrees with the arm below."""
        found = _command_files(PLUGIN_ROOT)
        assert found, (
            f"the walk over {PLUGIN_ROOT / 'commands'}/*.md returned nothing, "
            f"so the arm below passes over an empty set."
        )

    def test_no_command_file_names_an_editing_act_alone(self):
        narrow = []
        for path in _command_files(PLUGIN_ROOT):
            narrow.extend(narrow_act_lines(path))
        assert not narrow, (
            f"these command-file lines forbid EDITING `CLAUDE.md` and carry no "
            f"route class: {narrow}. A reader parses an editing act as an "
            f"`Edit` or a `Write` it issues, so a write caused through a "
            f"script, a command, or a save path it invokes reads as out of "
            f"scope. Widen the sentence to cover a write by any route, as the "
            f"declared template sites do."
        )


def test_an_untracked_command_needle_is_not_read(tmp_path):
    """An untracked scratch command must not join the read population.

    Positive control: a TRACKED file carrying the narrow act IS read and
    reported, so an empty population cannot pass this arm.
    """
    root = tmp_path / "plugin"
    (root / "commands").mkdir(parents=True)
    tracked = root / "commands" / "tracked.md"
    tracked.write_text("Do not edit CLAUDE.md\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   check=True, capture_output=True)
    needle = root / "commands" / "zzprobe.md"
    needle.write_text("Do not edit CLAUDE.md\n", encoding="utf-8")

    population = _command_files(root)

    assert tracked in population, "the tracked control is missing from the population"
    assert needle not in population, (
        f"untracked needle entered the command population: {needle}")
    assert narrow_act_lines(needle) == ["zzprobe.md:1"], (
        "the needle would be flagged, so only the population excludes it")
    assert [hit for path in population for hit in narrow_act_lines(path)] == [
        "tracked.md:1"], "the oracle over the population changed"
