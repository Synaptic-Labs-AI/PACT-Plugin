"""
The instructions an agent follows after a team split, and when reading a
resumption claim past idle sessions.

PRESENCE PINS, NORMALISED. Backticks are stripped and whitespace runs
collapsed on both sides, so a re-wrap or an inline-code change does not fail
a pin and a re-word does. Bold markers are not stripped, so a bold lead-in is
pinned as its own fragment.

EXACT COUNTS. Each clause is pinned at the number of sites it occupies. The
state-recovery extract and its region in pact-protocols.md must both carry it
(the extract byte-equality gate checks they agree; this checks the clause is
in them). A presence check alone stays green when one of two sites is
reverted.

ABSENCE PINS. The one-hop wording the claim walk made false must not survive
in any instruction surface, where an agent would act on it.

Counter-test, measured by source-only revert in a scratch copy: restoring the
five instruction surfaces to the base tree -> 46 failed, 4 passed of 50. The
four that stay green are the surface-walk control, the NORM clause (it
predates this change and must survive it), and the absence of two forms that
never shipped on the base tree: the realign command's retired form and the
secretary exception to the spawn rule.
"""

from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
RECOVERY = "protocols/pact-state-recovery.md"
PROTOCOLS = "protocols/pact-protocols.md"
BOOTSTRAP = "commands/bootstrap.md"
PERSONA = "agents/pact-orchestrator.md"
HARVEST = "skills/pact-handoff-harvest/SKILL.md"


def _norm(text: str) -> str:
    return " ".join(text.replace("`", "").split())


def _count(rel: str, phrase: str) -> int:
    return _norm((PLUGIN / rel).read_text(encoding="utf-8")).count(_norm(phrase))


# (file, phrase, exact count)
PINS = [
    # The team-split step, in the extract and its SSOT region.
    (RECOVERY, "**Team-split recovery**", 1),
    (PROTOCOLS, "**Team-split recovery**", 1),
    (RECOVERY, "(when a notice, a SessionStart line or a refused spawn says "
               "PACT moved this session to another team, says PACT cannot tell "
               "which team is this session's, or says PACT's recorded team has "
               "no config)", 1),
    (PROTOCOLS, "(when a notice, a SessionStart line or a refused spawn says "
                "PACT moved this session to another team, says PACT cannot tell "
                "which team is this session's, or says PACT's recorded team has "
                "no config)", 1),
    # The sanctioned recovery command when the census cannot decide.
    (RECOVERY, 'cd "{plugin_root}/hooks" && python3 -m shared.realign_team '
               "--team <chosen team>", 1),
    (PROTOCOLS, 'cd "{plugin_root}/hooks" && python3 -m shared.realign_team '
                "--team <chosen team>", 1),
    # Only the lead runs the realign command.
    (RECOVERY, "Only the lead runs this command; a teammate never does.", 1),
    (PROTOCOLS, "Only the lead runs this command; a teammate never does.", 1),
    # The state with no live candidate team has its own first step.
    (RECOVERY, "1. Recorded team without a config, and no live candidate team "
               "that matches this session: run bootstrap Step 2's `TaskCreate` "
               "and `TaskUpdate` for the secretary, then spawn it.", 1),
    (PROTOCOLS, "1. Recorded team without a config, and no live candidate team "
                "that matches this session: run bootstrap Step 2's `TaskCreate` "
                "and `TaskUpdate` for the secretary, then spawn it.", 1),
    # A realign from a team that never existed leaves nothing to carry.
    (RECOVERY, "If it reports nothing to carry across, the recovery is done.", 1),
    (PROTOCOLS, "If it reports nothing to carry across, the recovery is done.", 1),
    (RECOVERY, "If a realign notice follows, continue from step 3; otherwise "
               "the recovery is done.", 1),
    (PROTOCOLS, "If a realign notice follows, continue from step 3; otherwise "
                "the recovery is done.", 1),
    # Carry-across.
    (RECOVERY, "re-create in `{team_name}` with `TaskCreate` each one whose "
               "work is still in flight", 1),
    (PROTOCOLS, "re-create in `{team_name}` with `TaskCreate` each one whose "
                "work is still in flight", 1),
    (RECOVERY, "Do not edit or delete the files in `tasks/{from}/`.", 1),
    (PROTOCOLS, "Do not edit or delete the files in `tasks/{from}/`.", 1),
    # Carry-across records the old id, which the post-compaction re-run keys on.
    (RECOVERY, "and citing its `tasks/{from}/` id in the new description", 1),
    (PROTOCOLS, "and citing its `tasks/{from}/` id in the new description", 1),
    # After a compaction, the recovery re-runs for tasks not yet re-created.
    (RECOVERY, "If the journal's latest `team_realigned` event names a `from` "
               "store whose in-flight tasks you have not re-created, run the "
               "team-split recovery below from its step 3. Skip tasks already "
               "re-created: their description cites the old id.", 1),
    (PROTOCOLS, "If the journal's latest `team_realigned` event names a `from` "
                "store whose in-flight tasks you have not re-created, run the "
                "team-split recovery below from its step 3. Skip tasks already "
                "re-created: their description cites the old id.", 1),
    (PERSONA, "After a compaction, do the same when the journal's latest "
              "`team_realigned` event names a `from` store whose in-flight "
              "tasks you have not re-created.", 1),
    # Re-created tasks keep their stamp, their payload pointers and their gate.
    (RECOVERY, "on a work task, its `variety` stamp", 1),
    (PROTOCOLS, "on a work task, its `variety` stamp", 1),
    (RECOVERY, "Reference a submitted teachback or handoff you still need by "
               "its old task-file path.", 1),
    (PROTOCOLS, "Reference a submitted teachback or handoff you still need by "
                "its old task-file path.", 1),
    (RECOVERY, "`TaskUpdate(<teachback id>, addBlocks=[<work id>])` and "
               "`TaskUpdate(<work id>, addBlockedBy=[<teachback id>])`", 1),
    (PROTOCOLS, "`TaskUpdate(<teachback id>, addBlocks=[<work id>])` and "
                "`TaskUpdate(<work id>, addBlockedBy=[<teachback id>])`", 1),
    (RECOVERY, "Finish steps 4 and 5 for every in-flight task before you spawn "
               "a specialist for any of them.", 1),
    (PROTOCOLS, "Finish steps 4 and 5 for every in-flight task before you spawn "
                "a specialist for any of them.", 1),
    # The quit-path consumption write, as a deviation clause.
    (BOOTSTRAP, "Normally a consumption write follows a confirmed resumption, "
                "as the refresh write does. This one follows surfacing alone", 1),
    # Old-store harvest.
    (HARVEST, "read `team_realigned` events the same way as source 1 "
              "(`--type team_realigned`)", 1),
    (HARVEST, "Those task ids belong to team `{from}`, not to your team", 1),
    # The bootstrap exception, beside the rule it qualifies.
    # The spawn rule: no specialist before the recovery; no one at all while
    # PACT cannot tell which team is this session's.
    (BOOTSTRAP, "The exception: when a notice, a SessionStart line or a refused "
                "spawn names a team split, follow the team-split recovery in "
                "`pact-state-recovery.md` before spawning any specialist, and "
                "when it says PACT cannot tell which team is this session's, "
                "spawn no one, the secretary included, until that recovery's "
                "realign command succeeds.", 1),
    (BOOTSTRAP, "An ABSENT config is the NORM", 1),
    # The persona's deviation line.
    (PERSONA, "Normally `TaskList` holds this session's work. If a notice, a "
              "SessionStart line or a refused spawn says PACT moved this session "
              "to another team, cannot tell which team is this session's, or has "
              "a recorded team with no config, follow the team-split recovery in "
              "`pact-state-recovery.md` before step 3", 1),
    # Claims read past sessions that did nothing.
    (RECOVERY, "or from an earlier one when the sessions in between did "
               "nothing (at most three back)", 1),
    (PROTOCOLS, "or from an earlier one when the sessions in between did "
                "nothing (at most three back)", 1),
    (RECOVERY, "or from an earlier session's when the sessions in between did "
               "nothing (at most three back)", 1),
    (PROTOCOLS, "or from an earlier session's when the sessions in between did "
                "nothing (at most three back)", 1),
    (BOOTSTRAP, "A claim is read from the previous session, or from the "
                "session before it when the previous one did nothing, up to "
                "three sessions back.", 1),
]


@pytest.mark.parametrize("rel,phrase,expected", PINS,
                         ids=[f"{rel}:{phrase[:40]}" for rel, phrase, _ in PINS])
def test_the_clause_is_at_each_of_its_sites(rel, phrase, expected):
    assert _count(rel, phrase) == expected


def _instruction_surfaces():
    surfaces = list((PLUGIN / "agents").glob("*.md"))
    surfaces += list((PLUGIN / "commands").glob("*.md"))
    surfaces += list((PLUGIN / "protocols").glob("*.md"))
    surfaces += list((PLUGIN / "skills").glob("*/SKILL.md"))
    return surfaces


def test_the_instruction_surfaces_are_found():
    """Control for the absence pins: the walk reaches every surface kind,
    including the ones whose wording the claim walk changed."""
    found = {p.relative_to(PLUGIN).as_posix() for p in _instruction_surfaces()}
    assert {RECOVERY, PROTOCOLS, BOOTSTRAP, PERSONA, HARVEST} <= found


@pytest.mark.parametrize("phrase", [
    "one hop back and no further",
    "the one-hop bound",
    "a harmless orphan record",
    "python3 -m shared.pact_context realign",
    "Why it is keyed that way",
    "What this write does and does not bound",
    "before spawning anyone but the secretary",
])
def test_a_retired_phrase_is_in_no_instruction_surface(phrase):
    hits = [p.relative_to(PLUGIN).as_posix() for p in _instruction_surfaces()
            if _norm(phrase) in _norm(p.read_text(encoding="utf-8"))]
    assert hits == []
