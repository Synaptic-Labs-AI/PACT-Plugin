"""Harvest dispatch sites are the oracle for the prose enumerations of them.

COVERED, and this list is the scope — an enumeration not on it is not gated:
the orchestrator persona's trigger rows, the harvest skill's variant bullets,
and the skill's Consolidation trigger sentence. Each of those is compared
against the dispatch sites, never against another prose surface: completing one
of these lists is what shortens the others, so chaining them would route that
pressure into the oracle.

NOT gated here, and deliberately: the persona's propagation paragraph. It claims
which boundaries PROPAGATE, a different property from which run a Consolidation,
and ``test_exemption_policy_tripwires.py`` compares it against the dispatches
that carry the propagate sentence.

Before adding an arm here, check whether the enumeration you are gating is
coupled to one that is not. A partial gate over coupled lists manufactures the
desync it cannot see, and reports green in the same run.


The dispatch sites in ``commands/`` are the ground truth: each one tells the
secretary to follow a named harvest workflow, so the pair (command, variant) is
a fact about what the repository does. The orchestrator persona's trigger list
and the harvest skill's variant list are CLAIMS about those pairs. This module
asserts the claims equal the facts, in both directions, so adding a dispatch
without listing it and listing a trigger with no dispatch each redden.

BOUND, stated because it is narrower than the assertion looks: the multiset
counts dispatch sites, it does not identify them. Two distinct boundaries and
one boundary dispatched twice are the same count, so the gate reddens on the
second and the fix is to enumerate it — which is the right outcome either way.
A dispatch site carrying no ``Follow the <Variant> Harvest workflow`` string is
invisible here; give a new one that string or this gate cannot see it.

EXTRACTION RULE, stated because an under-matching extractor yields a green that
proves nothing and every local step of building it looks correct:

  * ground truth -- every ``Follow the <Variant> Harvest workflow`` in a
    ``commands/*.md``, paired with that file's stem. Several dispatch sites may
    yield the same pair, and THE TWO SURFACES COUNT THEM DIFFERENTLY because
    they claim different things. The persona enumerates BOUNDARIES, one row
    each, so it compares as a MULTISET: two boundaries in one command are two
    rows. The skill maps a variant to the commands that use it, so it compares
    as a SET: naming a command twice there would be wrong. A single unit for
    both surfaces is what let a second dispatch site hide.
  * persona -- every line of the Memory Processing Triggers block naming a
    variant with an arrow AND a backticked command.
  * skill -- every variant bullet's parenthesised comma list of commands.

Extraction reads whole file text, never line by line: a dispatch ``description=``
string spans many lines, so a line-oriented scan sees fragments of it.

Each source is asserted NON-EMPTY before the sets are compared. Two empty sets
are equal, so a comparison over a failed extraction would pass while measuring
nothing.

WHAT THIS DOES NOT CATCH: whether a dispatch fires at the workflow boundary its
prose claims. The pair is (command, variant); the boundary within the command is
prose with no machine-readable counterpart, so moving a harvest earlier or later
inside the same command changes nothing here.
"""

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_files as _tracked_files

PLUGIN = Path(__file__).resolve().parent.parent
COMMANDS = PLUGIN / "commands"
PERSONA = PLUGIN / "agents" / "pact-orchestrator.md"
SKILL = PLUGIN / "skills" / "pact-handoff-harvest" / "SKILL.md"

_DISPATCH = re.compile(r"Follow the (\w+) Harvest workflow")
_PERSONA_ROW = re.compile(r"→\s*(\w+) Harvest.*?`([\w-]+)`")
_SKILL_BULLET = re.compile(r"\*\*(\w+) Harvest\*\*[^\n]*?\(([^)]*)\)")

# Paragraphs claiming WHICH boundaries run a Consolidation Harvest, located by a
# phrase each one owns. A rewrite that drops the phrase empties the extraction
# and trips the non-empty guard rather than passing.
#
# The persona's propagation paragraph sat here and does not belong. It names the
# boundaries that PROPAGATE, counting `orchestrate`'s post-review STANDARD
# Harvest among them, so comparing it against Consolidation dispatchers equated
# two properties that agree on four stems only because `orchestrate` is in each
# list for a different reason. The repo denies the equivalence in its own words:
# that paragraph ends "never the variant's name", and the skill's Propagation
# rule calls the sentence "a mode, not a scope". Its correct oracle is the set of
# dispatches carrying the sentence, which `test_exemption_policy_tripwires.py`
# already compares it against, so removing it here loses no coverage.
_CONSOLIDATION_CLAIMS = {
    "skill consolidation trigger": (SKILL, "This is the deep-clean pass"),
}


def _command_files():
    """Tracked shipped commands, excluding untracked and deleted files."""
    relative = COMMANDS.relative_to(PLUGIN.parent).as_posix()
    return sorted(path for path in _tracked_files(PLUGIN.parent, relative)
                  if path.parent == COMMANDS and path.suffix == ".md")


def _dispatched():
    """(command stem, variant) for every harvest dispatch under commands/.

    A LIST, so a command dispatching one variant at two boundaries yields two
    entries. The persona compares as a MULTISET and the skill as a SET; see
    the per-surface unit in the parametrize below.
    """
    return [
        (path.stem, variant)
        for path in _command_files()
        for variant in _DISPATCH.findall(path.read_text(encoding="utf-8"))
    ]


def _persona_claims():
    """(command, variant) claimed by the orchestrator's trigger rows."""
    return [
        (command, variant)
        for variant, command in _PERSONA_ROW.findall(
            PERSONA.read_text(encoding="utf-8")
        )
    ]


def _skill_claims():
    """(command, variant) claimed by the harvest skill's variant bullets."""
    return [
        (command.strip(), variant)
        for variant, commands in _SKILL_BULLET.findall(
            SKILL.read_text(encoding="utf-8")
        )
        for command in commands.split(",")
    ]


def _excess(a, b):
    """Members of `a` not covered by `b`, preserving multiplicity for Counters."""
    diff = a - b
    return sorted(diff.elements() if isinstance(diff, Counter) else diff)


class TestHarvestTriggersAgreeAcrossSurfaces:
    """Dispatch sites are the oracle; both prose enumerations must match them."""

    @pytest.mark.parametrize(
        "name,claims,unit",
        [
            ("orchestrator persona", _persona_claims, Counter),
            ("harvest skill", _skill_claims, set),
        ],
    )
    def test_prose_enumeration_matches_the_dispatch_sites(self, name, claims, unit):
        dispatched = unit(_dispatched())
        assert dispatched, (
            f"no `Follow the <Variant> Harvest workflow` dispatch found under "
            f"{COMMANDS}. The oracle is empty, so the comparison below would "
            f"hold vacuously. Report this as an extraction failure, not as "
            f"agreement."
        )
        claimed = unit(claims())
        assert claimed, (
            f"the {name} surface yielded NO (command, variant) pairs. Either "
            f"the surface stopped enumerating them or the extraction rule in "
            f"this module's docstring no longer matches how it states them. "
            f"Report this as an extraction failure, not as agreement."
        )
        assert claimed == dispatched, (
            f"THE {name.upper()} AND THE DISPATCH SITES DISAGREE.\n"
            f"  dispatched but not claimed: {_excess(dispatched, claimed)}\n"
            f"  claimed but not dispatched: {_excess(claimed, dispatched)}\n"
            f"A dispatch tells the secretary to run a named workflow, so every "
            f"dispatched pair belongs in this surface and every pair it names "
            f"must have a dispatch. Update whichever side you did not just edit."
        )


def _command_stems():
    return {path.stem for path in _command_files()}


def _consolidation_dispatchers():
    """Commands with a Consolidation Harvest dispatch site."""
    return {
        path.stem
        for path in _command_files()
        if "Consolidation" in _DISPATCH.findall(path.read_text(encoding="utf-8"))
    }


def _claimed_boundaries(source, marker):
    """Command names named by the paragraph of `source` containing `marker`."""
    paragraphs = [
        para
        for para in source.read_text(encoding="utf-8").split("\n\n")
        if marker in para
    ]
    stems = _command_stems()
    return {
        name
        for para in paragraphs
        for name in re.findall(r"[\w-]+", para)
        if name in stems
    }


class TestConsolidationClaimsMatchTheDispatchSites:
    """Every Consolidation boundary must appear wherever the set is enumerated.

    The unit here is the BOUNDARY-NAME SET, not the (command, variant) multiset
    the trigger arm uses. Both differences are load-bearing. The variant is
    constant — every member is a Consolidation Harvest — so pairing would
    compare a padded set. And multiplicity is wrong: a command with two trigger
    rows, one Standard and one Consolidation, is named ONCE here, correctly,
    because such a paragraph claims a property of a boundary rather than a count
    of dispatch sites.

    EXTRACTION, stated because this module states the trigger arm's bound and a
    silent arm reads as an unbounded one: the claimed set is every word of the located
    paragraph that equals a ``commands/*.md`` stem. A command named there for
    ANY reason is therefore claimed, so a contrastive mention — "unlike
    `peer-review`" — would redden this arm. Today the located paragraph names only
    the boundaries it triggers, and the extraction is exact.

    WHAT THIS DOES NOT CATCH: the per-boundary REASON. A reason that goes stale
    while its command name stays present is invisible here.
    """

    @pytest.mark.parametrize("name,source,marker", [
        (label, source, marker)
        for label, (source, marker) in sorted(_CONSOLIDATION_CLAIMS.items())
    ])
    def test_every_consolidation_dispatcher_is_named(self, name, source, marker):
        dispatchers = _consolidation_dispatchers()
        assert dispatchers, (
            f"no Consolidation Harvest dispatch site found under {COMMANDS}. "
            f"The oracle is empty, so the comparison below would hold "
            f"vacuously. Report this as an extraction failure, not agreement."
        )
        claimed = _claimed_boundaries(source, marker)
        assert claimed, (
            f"the {name} in {source.name} named no command. Either the "
            f"paragraph stopped enumerating them or its locating phrase "
            f"{marker!r} was rewritten. Report this as an extraction failure, "
            f"not agreement."
        )
        assert claimed == dispatchers, (
            f"THE {name.upper()} AND THE CONSOLIDATION DISPATCH SITES DISAGREE.\n"
            f"  dispatches Consolidation but not named: "
            f"{sorted(dispatchers - claimed)}\n"
            f"  named but dispatches no Consolidation: "
            f"{sorted(claimed - dispatchers)}\n"
            f"Every boundary that runs a Consolidation Harvest belongs in this "
            f"paragraph. Update whichever side you did not just edit."
        )


def test_tracked_command_population_controls_untracked_files(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    plugin = repo / "pact-plugin"
    commands = plugin / "commands"
    commands.mkdir(parents=True)
    known = commands / "refresh.md"
    known.write_text("Follow the Consolidation Harvest workflow\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", "pact-plugin"],
        check=True, capture_output=True)
    monkeypatch.setitem(globals(), "PLUGIN", plugin)
    monkeypatch.setitem(globals(), "COMMANDS", commands)

    before = _command_files()
    assert known in before
    stray = commands / "000_backend_harvest_issue1714_probe.md"
    stray.write_text("Follow the Consolidation Harvest workflow\n", encoding="utf-8")
    try:
        assert _command_files() == before
    finally:
        stray.unlink()


def test_tracked_command_population_omits_a_deleted_member(monkeypatch):
    known = COMMANDS / "refresh.md"
    is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file", lambda path: False if path == known else is_file(path))
    assert known not in _command_files()
