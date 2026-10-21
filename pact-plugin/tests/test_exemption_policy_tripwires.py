"""
Executable tripwires over the Q5 EXEMPTION POLICY.

The denominator is now hook-emitted and therefore author-proof: no command
file can move it by writing prose. What CAN still move it is the exemption
sets — they decide which dispatches are sites at all. This file converts the
prose rules guarding them into assertions that can fail.

Each pin below names, in its own docstring, the MUTATION that reddens it. A
pin whose mutation cannot be named is not a pin; it is coverage-shaped
decoration, and this file exists because that failure has already shipped
twice in this area.

NOT PINNED FOR THE DENOMINATOR — the auditor's `metadata.type` state. The
denominator keys on `is_teachback_exempt`, which has no metadata surface, so
that fact is irrelevant to it. It IS pinned lower down for a different
consumer: the harvest propagation rule keeps the Working Memory block away
from an auditor that has not reported, which is only meaningful while auditors
stay non-exempt, which depends on that dispatch carrying no `type`.
"""
import ast
import json
import re
from pathlib import Path

import pytest

from shared import intentional_wait  # noqa: E402
from shared.intentional_wait import (  # noqa: E402
    SELF_COMPLETE_EXEMPT_AGENT_TYPES,
    TEACHBACK_EXEMPT_AGENT_TYPES,
    is_self_complete_exempt,
    is_teachback_exempt,
)

from test_harvest_trigger_coherence import _claimed_boundaries  # noqa: E402 — sibling extraction rule

VARIETY_PROTOCOL = Path(__file__).parent.parent / "protocols" / "pact-variety.md"
INTENTIONAL_WAIT_SRC = (
    Path(__file__).parent.parent / "hooks" / "shared" / "intentional_wait.py"
)


def _assigned_value_node(source, target_name):
    """Return the AST value node assigned to a module-level `target_name`.

    Handles both `X = ...` and the annotated `X: frozenset = ...` form.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == target_name:
                return node.value
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == target_name:
                    return node.value
    raise AssertionError(
        f"no module-level assignment to {target_name!r} found -- the constant "
        f"was renamed or moved, which this file's pins depend on"
    )


def _write_team_config(teams_dir, team_name, members):
    team_dir = Path(teams_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": team_name, "members": members}),
        encoding="utf-8",
    )
    return str(teams_dir)


@pytest.fixture
def teams_dir(tmp_path):
    d = tmp_path / "teams"
    d.mkdir()
    return str(d)


class TestTheTwoExemptionSetsAreNotAliased:
    """The source comment says: "DO NOT recouple by ALIASING to the prior
    constant." Aliasing is an IDENTITY claim, so the test must be one.

    MUTATION THAT REDDENS: in `shared/intentional_wait.py`, replace the
    `TEACHBACK_EXEMPT_AGENT_TYPES = frozenset({...})` literal with
    `TEACHBACK_EXEMPT_AGENT_TYPES = SELF_COMPLETE_EXEMPT_AGENT_TYPES`.

    WHY IDENTITY AND NOT CONTENTS. The two frozensets hold identical
    contents today, so a content-shaped pin (`T == S`, or comparing either
    against a literal) stays GREEN on an aliased pair and detects nothing.
    `is not` is the only form that separates "two sets that happen to
    match" from "one set under two names".

    AND WHY NOT A PIN ON THE CONTENTS BEING EQUAL TODAY. That would redden
    on a change the module explicitly invites -- its docstring supports
    future divergence, a rote-only agentType joining one set with a one-line
    change. A test that fires on an intended edit is worse than no test, so
    the content relationship is recorded in this paragraph and asserted
    nowhere. A previous test did assert it, by binding a local alias and
    comparing that local against the constant it had just been assigned
    from; both arms were true by the assignment and no change to this module
    could redden either. Do not re-add that shape.

    TWO PINS, TWO DIFFERENT OBSERVATIONS -- neither subsumes the other:
    - `test_the_two_sets_are_distinct_objects` reads the IMPORTED objects
      and catches `T = S`.
    - `test_the_teachback_set_is_not_derived_from_the_self_complete_set`
      reads the SOURCE and additionally catches `T = frozenset({*S})`,
      which the identity pin cannot see.
    """

    def test_the_two_sets_are_distinct_objects(self):
        assert TEACHBACK_EXEMPT_AGENT_TYPES is not SELF_COMPLETE_EXEMPT_AGENT_TYPES, (
            "TEACHBACK_EXEMPT_AGENT_TYPES has been ALIASED to "
            "SELF_COMPLETE_EXEMPT_AGENT_TYPES. These are two policy surfaces "
            "answering different questions -- 'should this owner be dispatched "
            "through a teachback gate?' vs 'may this owner self-complete?' -- "
            "and aliasing them means a future change to either silently moves "
            "the other, including the Q5 denominator."
        )

    def test_the_teachback_set_is_not_derived_from_the_self_complete_set(self):
        """The coupling the identity pin above CANNOT see.

        `is not` compares objects, so it catches `T = S` and nothing else.
        It passes on `T = frozenset({*S})` and `T = frozenset(set(S))`,
        which build a DISTINCT object whose contents are still DERIVED from
        S -- measured, not assumed: `frozenset({*S}) is S` is False while
        `frozenset({*S}) == S` is True. That is the plausible shape of a
        well-meant decoupling that decouples nothing: it satisfies the
        identity pin while leaving a later edit to S silently moving T, and
        with it the Q5 denominator.

        Reads the SHIPPED SOURCE rather than the imported values because the
        forbidden thing is a source relationship. By the time the module is
        imported, `frozenset({*S})` has already collapsed into an ordinary
        frozenset carrying no trace of where its members came from -- the
        evidence exists only in the assignment expression.

        Deliberately narrow: it forbids REFERENCING the other constant, not
        every non-literal form. `T = frozenset(_ROTE_AGENT_TYPES)` off some
        independent source stays green, because that is a real decoupling.

        MUTATIONS THAT REDDEN: `TEACHBACK_EXEMPT_AGENT_TYPES =
        SELF_COMPLETE_EXEMPT_AGENT_TYPES` (also caught by the pin above), or
        `= frozenset({*SELF_COMPLETE_EXEMPT_AGENT_TYPES})` (caught ONLY
        here).
        """
        value = _assigned_value_node(
            INTENTIONAL_WAIT_SRC.read_text(encoding="utf-8"),
            "TEACHBACK_EXEMPT_AGENT_TYPES",
        )
        referenced = {
            n.id for n in ast.walk(value) if isinstance(n, ast.Name)
        }
        assert "SELF_COMPLETE_EXEMPT_AGENT_TYPES" not in referenced, (
            "TEACHBACK_EXEMPT_AGENT_TYPES is now DERIVED from "
            "SELF_COMPLETE_EXEMPT_AGENT_TYPES in the source. Even when the "
            "two are distinct objects -- so the identity pin stays green -- "
            "deriving one from the other recouples them: editing the "
            "self-complete carve-out silently moves the teachback carve-out, "
            "and with it which dispatches count as Q5 sites. Build the set "
            "from its own literal, or from a source that is not the other "
            "policy surface."
        )


class TestAuditorDispatchesCount:
    """A `pact-auditor` owner is NOT teachback-exempt, so auditor dispatches
    are dispatch SITES and land in the Q5 denominator.

    MUTATION THAT REDDENS: add `"pact-auditor"` to
    TEACHBACK_EXEMPT_AGENT_TYPES.
    """

    def test_pact_auditor_is_not_teachback_exempt(self):
        assert "pact-auditor" not in TEACHBACK_EXEMPT_AGENT_TYPES, (
            "pact-auditor has been added to TEACHBACK_EXEMPT_AGENT_TYPES, "
            "which REMOVES every auditor dispatch from the Q5 coverage "
            "denominator. Auditor dispatches COUNT: an un-stamped auditor "
            "dispatch is a genuine coverage gap whose remedy is stamping it, "
            "never exempting it so the number improves."
        )

    def test_an_auditor_owner_resolves_as_non_exempt_end_to_end(self, teams_dir):
        """The set-membership assertion above is about the constant; this one
        drives the real predicate over a real team config, so it also covers
        the resolution path the denominator actually calls."""
        _write_team_config(teams_dir, "t", [
            {"name": "auditor", "agentType": "pact-auditor"},
        ])
        assert is_teachback_exempt("auditor", "t", teams_dir) is False


class TestTheEmitPredicateReadsTheSSOT:
    """Consumer (a): the emit predicate resolves exemption through
    TEACHBACK_EXEMPT_AGENT_TYPES and no other set.

    This is BEHAVIOURAL rather than an import/AST check on purpose: an
    import proves the name is in scope, not that the decision consults it.

    MUTATION THAT REDDENS: in `_is_teachback_exempt_agent_type`, change
    `agent_type in TEACHBACK_EXEMPT_AGENT_TYPES` to
    `agent_type in SELF_COMPLETE_EXEMPT_AGENT_TYPES`. The monkeypatch below
    then stops changing the verdict.
    """

    def test_predicate_verdict_follows_the_teachback_constant(
        self, teams_dir, monkeypatch
    ):
        _write_team_config(teams_dir, "t", [
            {"name": "someone", "agentType": "pact-auditor"},
        ])
        # Baseline: not exempt, because pact-auditor is not in the SSOT.
        assert is_teachback_exempt("someone", "t", teams_dir) is False

        # Move ONLY the teachback SSOT; the verdict must follow it.
        monkeypatch.setattr(
            intentional_wait,
            "TEACHBACK_EXEMPT_AGENT_TYPES",
            frozenset({"pact-auditor"}),
        )
        assert is_teachback_exempt("someone", "t", teams_dir) is True, (
            "the emit predicate's verdict did not follow "
            "TEACHBACK_EXEMPT_AGENT_TYPES, so it is resolving exemption "
            "through some other set -- the denominator and its policy have "
            "drifted apart"
        )


class TestProtocolProseNamesTheSameConstant:
    """Consumer (b): the protocol prose.

    HONEST SCOPE, and it must not be reported as more than this: prose cannot
    READ an SSOT, so this checks only that it NAMES the same constant. A red
    here means SOMEONE REWORDED THE PROSE -- it does NOT mean the consumers
    drifted. It is included because a protocol that names a symbol which no
    longer exists is its own defect, not because it verifies the coupling.

    MUTATION THAT REDDENS: rename the constant in the protocol prose, or
    remove the sentence naming it.
    """

    def test_variety_protocol_names_the_exemption_constant(self):
        text = VARIETY_PROTOCOL.read_text(encoding="utf-8")
        assert "TEACHBACK_EXEMPT_AGENT_TYPES" in text, (
            "the Q5 denominator protocol no longer names "
            "TEACHBACK_EXEMPT_AGENT_TYPES -- the category justification rests "
            "on that constant being a pre-existing declaration, so a doc that "
            "stops naming it loses the independent ground the ruling needs"
        )


ORCHESTRATOR_PERSONA = Path(__file__).parent.parent / "agents" / "pact-orchestrator.md"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"


def _metadata_keys(literal):
    """KEY NAMES of a metadata literal, read TEXTUALLY rather than parsed.

    The only question asked of these literals is "is a `type` key present", and
    answering it does not require the literal to be valid JSON. `json.loads`
    used to answer it, and RAISED on two ordinary spellings: a single-quoted
    dict, and the `"halt"|"alert"` alternation this repo already uses to
    document an algedonic dispatch. A crash inside a test that scans OTHER files
    diagnoses the scanner rather than the file that broke it -- and swallowing
    that crash would have converted it into a silent blind spot, which is worse
    than the crash. Reading the text answers the question on all four spellings
    without needing either.

    Keys are anchored to `{` or `,` so a `type` substring inside a VALUE is not
    read as a key: `{"note": "see type: below"}` yields `note` alone. Without
    that anchor this would redden on a correct dispatch, and a guard that fires
    on correct work is deleted by the first person it annoys.

    Newlines are ordinary whitespace to this pattern, so a literal the caller
    read across several lines needs no special casing here.
    """
    return set(re.findall(r'[{,]\s*["\']?(\w+)["\']?\s*:', literal))


_METADATA_START = re.compile(r"metadata\s*(?:=|:)\s*\{")


def _metadata_literals(text):
    """Yield (offset, literal) for every metadata literal in `text`.

    Brace-balanced and newline-tolerant, so a multiline `TaskCreate(...)` is
    read whole. Scanning the WHOLE FILE rather than pre-cut blocks is what makes
    that safe: a block boundary can never cut a literal in half, because the
    literal is found before any block is considered.

    `literal` is None when the braces never close. The caller must FAIL on that
    rather than skip it -- a literal that cannot be read must not pass as clean.
    """
    for m in _METADATA_START.finditer(text):
        i = text.index("{", m.start())
        depth = 0
        for j in range(i, len(text)):
            depth += (text[j] == "{") - (text[j] == "}")
            if depth == 0:
                yield m.start(), text[i:j + 1]
                break
        else:
            yield m.start(), None


def _enclosing_block(text, offset):
    """The blank-line-delimited block containing `offset`.

    This is the ASSOCIATION unit: a metadata literal belongs to the dispatch
    written around it. Line-scoping was a cruder proxy for the same idea, and it
    lost every multiline call -- which is the house style for substantial
    dispatches in the files this scans.
    """
    start = 0
    for m in re.finditer(r"\n[ \t]*\n", text):
        if m.start() > offset:
            return text[start:m.start()]
        start = m.end()
    return text[start:]


PROPAGATE_SENTENCE = "Then propagate the store into the Working Memory block."
PROPAGATION_PARAGRAPH_MARKER = "may appear only at a boundary"
PROPAGATING_COMMANDS = frozenset({"wrap-up"})

# Any literal a command hands an agent. NOT narrowed to a harvest dispatch
# description -- see _propagating_sites.
_CARRIER_RE = re.compile(r'(?:description|message)="((?:[^"\\]|\\.)*)"', re.S)

_WRAP_UP = COMMANDS_DIR / "wrap-up.md"
# wrap-up's step-6 branches, in document order. The propagation decision is
# taken PER BRANCH, so these labels are the slice boundaries the branch arm
# reads. They are also the labels tests/test_wrapup_datasafety_contracts.py
# slices on, so a rename moves both files together or reddens both.
_WRAP_UP_BRANCHES = (
    "**A — PR is MERGED**",
    "**B — No PR exists**",
    "**C — PR exists but is not merged**",
)


def _propagating_sites():
    """Command stems that instruct a propagation, one entry per instructing
    site (a list, so the count is visible to the caller).

    THE UNIT IS THE SITE, NOT THE COMMAND, and that is a correction rather than
    a refinement. A command can hold several propagation sites and they need not
    agree: `wrap-up` propagates on the two step-6 branches that END the arc, and
    must not on the branch that preserves an open PR. While the unit was the
    command, membership of the set asserted `wrap-up` propagates FULL STOP --
    true of two branches, false of the third, and the third is the one that
    respawns reviewers. A command-granular container cannot hold a branch-level
    truth, so the container changed.

    THE CARRIER IS NOT PINNED TO A DISPATCH DESCRIPTION. The sentence IS the
    instruction; whatever literal carries it propagates. The earlier form also
    required `Follow the ... Harvest workflow` in the SAME literal, which made a
    propagate request sent to an already-running secretary invisible here -- and
    that request is exactly what a per-branch decision needs, because the branch
    is not known until long after the harvest is dispatched.
    """
    stems = []
    for path in sorted(COMMANDS_DIR.glob("*.md")):
        for literal in _CARRIER_RE.findall(path.read_text(encoding="utf-8")):
            if PROPAGATE_SENTENCE in literal:
                stems.append(path.stem)
    return stems


def _wrap_up_branch_slices():
    """wrap-up's three step-6 branches, keyed by label.

    Raises on a label that is absent, duplicated or out of order rather than
    returning a partial map. An absence assertion over an empty slice passes for
    the wrong reason, and a slice that ran to the wrong end reports a confident
    wrong answer -- both read as agreement.
    """
    text = _WRAP_UP.read_text(encoding="utf-8")
    starts = []
    for label in _WRAP_UP_BRANCHES:
        hits = [m.start() for m in re.finditer(re.escape(label), text)]
        assert len(hits) == 1, (
            f"expected exactly one {label!r} in {_WRAP_UP.name}, found "
            f"{len(hits)}. These labels are the branch arm's slice boundaries: "
            "re-anchor it on whatever now delimits the branches. Do not delete "
            "the arm and do not let the slice run to the wrong end."
        )
        starts.append(hits[0])
    assert starts == sorted(starts), (
        f"the step-6 branch labels are out of document order in "
        f"{_WRAP_UP.name}: {starts}"
    )
    slices = {}
    for i, (label, start) in enumerate(zip(_WRAP_UP_BRANCHES, starts)):
        # The last branch ends at the next top-level heading, not at EOF --
        # otherwise its slice swallows every later step and an absence
        # assertion over it is answering a question about the whole file.
        nxt = text.find("\n## ", start)
        end = starts[i + 1] if i + 1 < len(starts) else (
            nxt if nxt != -1 else len(text)
        )
        slices[label] = text[start:end]
    return slices


class TestOnlyTheSafeBoundariesPropagateTheWorkingMemoryBlock:
    """A harvest writes the Working Memory block only when its dispatch carries
    the propagate sentence, and that sentence belongs only at a boundary after
    which no independent-judgement agent spawns before a later harvest. The
    persona paragraph states the rule and names the boundaries; the dispatch
    sites are the fact. These arms hold the two together.

    NOT COVERED, stated rather than implied: WHETHER a named boundary is in
    fact safe. That is a claim about spawn order inside a command, which has no
    machine-readable counterpart. These arms redden when a quiet site gains the
    sentence or the persona stops naming a site that carries it.

    THE PERSONA NAMES COMMANDS AND THE SITES ARE FINER, so the two arms below
    are deliberately at different grains and the third exists to cover the gap.
    A command is a propagating BOUNDARY when any of its sites propagates, which
    is what arm 2 compares; whether the RIGHT sites within it propagate is what
    arm 3 holds, for the one command whose sites disagree.
    """

    def test_no_quiet_dispatch_carries_the_propagate_sentence(self):
        """Reddens when a site outside the propagating set gains the sentence,
        or when the sentence vanishes from the tree (non-vacuity: the count is
        pinned, so an empty extraction fails loudly).

        TWO SITES, in one command: `wrap-up`'s two arc-closing step-6
        branches. The count is per SITE, so it moves when a
        branch gains or loses its propagation even though the command set does
        not -- which is the whole reason the unit changed.
        """
        carrying = _propagating_sites()
        assert carrying, (
            "nothing under %s carries %r -- either every site went quiet or "
            "the carrier extraction stopped matching. Report this as an "
            "extraction failure, not as agreement." % (COMMANDS_DIR, PROPAGATE_SENTENCE)
        )
        assert len(carrying) == 2, carrying
        assert set(carrying) <= PROPAGATING_COMMANDS, sorted(set(carrying) - PROPAGATING_COMMANDS)

    def test_every_propagating_site_is_named_in_the_persona_paragraph(self):
        """Reddens when the persona paragraph names a command that does not
        propagate, or omits one that does. Reuses the coherence test's
        extraction rule: every bare word equal to a `commands/*.md` stem."""
        claimed = _claimed_boundaries(ORCHESTRATOR_PERSONA, PROPAGATION_PARAGRAPH_MARKER)
        assert claimed, (
            "the persona paragraph located by %r named no command, or the marker "
            "was rewritten. Report this as an extraction failure, not as agreement."
            % PROPAGATION_PARAGRAPH_MARKER
        )
        assert claimed == set(_propagating_sites())

    def test_the_wrap_up_branch_that_preserves_an_open_pr_does_not_propagate(self):
        """The branch-level fact a command-granular set could not hold.

        `wrap-up`'s three step-6 branches do not agree, and the disagreement is
        the point. A merged PR and no PR each END the arc: nothing whose value
        is independent judgement spawns after them, so those two propagate. The
        open-PR branch does the opposite -- it writes `session_paused`, which
        makes the next session a declared resumption, and its own report sends
        the user back into review. The agents that spawn next are therefore the
        ones judging this arc, and a propagation here hands them this arc's own
        conclusions. The resumption marker cannot undo that: it suppresses the
        secretary's spawn rebuild, and this write has already landed on disk.

        Reddens in both directions -- a propagation appearing on the open-PR
        branch, and one disappearing from either branch that closes the arc.
        Do not satisfy it by making the three branches symmetric: the asymmetry
        is the fix, and symmetry here is the defect.
        """
        merged, no_pr, open_pr = (
            _wrap_up_branch_slices()[label] for label in _WRAP_UP_BRANCHES
        )
        assert PROPAGATE_SENTENCE in merged, (
            "wrap-up's merged-PR branch no longer propagates. The arc is over "
            "there and nothing else propagates it, so the block never receives "
            "this session's records."
        )
        assert PROPAGATE_SENTENCE in no_pr, (
            "wrap-up's no-PR branch no longer propagates. Same consequence as "
            "the merged branch: the arc is over and nothing else writes."
        )
        assert PROPAGATE_SENTENCE not in open_pr, (
            "wrap-up's open-PR branch propagates. That branch keeps the arc "
            "alive -- it writes session_paused and sends the user back into "
            "review -- so this write reaches the agents spawned to judge the "
            "arc, and the resumption marker cannot take it back."
        )

    def test_auditor_shaped_task_is_not_self_complete_exempt(self):
        """The fact the propagation rule rests on: an auditor's task stays
        live until the auditor itself reports. Reddens if either exemption
        surface starts admitting auditors -- the dependency editor's tripwire."""
        assert "pact-auditor" not in SELF_COMPLETE_EXEMPT_AGENT_TYPES
        assert is_self_complete_exempt(
            {"owner": "auditor", "metadata": {"completion_type": "signal"}}, ""
        ) is False

    def test_no_auditor_dispatch_site_sets_metadata_type(self):
        """Reddens if an auditor dispatch gains `metadata.type` — whether inside
        the completion_type literal or as a SEPARATE literal on the same call.
        Both were reproduced; the second passed before this widening.

        SCOPED TO AUDITOR DISPATCH BLOCKS, not to every metadata literal in
        the tree. A blocker or algedonic dispatch MUST carry `metadata.type` --
        `is_self_complete_exempt` requires it -- so a file-wide ban would redden
        on correct future work. A `type` key on some other dispatch's metadata
        is out of scope BY DESIGN: it is not this dispatch's metadata.

        THE ASSOCIATION UNIT IS THE BLOCK, NOT THE LINE. A line-scoped scan sees
        only a literal sharing a line with `auditor`, so a multiline
        `TaskCreate(...)` -- the house style for substantial dispatch blocks in
        these very files -- is invisible to it, and the arm stays GREEN on a
        dispatch that would make `is_self_complete_exempt` treat the auditor as
        exempt. That was live, not hypothetical: a probe carrying `type` passed
        green before this change.

        LATENT, and structural rather than absent: a markdown table has no blank
        lines, so an entire table is one block. A row mentioning an auditor and a
        different row carrying a legitimate `type` would be associated by this
        unit and redden correct work. Measured zero in commands/ today, and the
        shape exists in this repo's markdown elsewhere. Two dispatches sharing
        one block is the same hazard by another route. If either appears, the
        unit must tighten to the enclosing call rather than the block.

        Sites are globbed rather than listed, so a third dispatch file is
        scanned without anyone remembering to add it, and the assertion below is
        a FLOOR rather than an inventory -- growth is silent. DELIBERATE LOSS,
        recorded rather than left to be discovered later: this arm can no longer
        report that the scanned population changed. An "unexpected site" signal
        would reintroduce the redden-on-correct-work direction in a softer form.

        THE FLOOR ALSO GUARDS THE DIRECTORY, and that is emergent rather than
        designed, so it is easy to destroy by accident. Repointing COMMANDS_DIR
        at agents/ makes both named files vanish and the floor fires -- measured,
        and it is the ONLY thing catching that route, because
        `q.parent == COMMANDS_DIR` is true by construction for anything
        `COMMANDS_DIR.glob()` yields and so cannot see a repointing. Do not
        "simplify" this to a bare non-empty check: the directory protection goes
        with it, and agents/ and protocols/ carry legitimate `type`-bearing
        literals that would come into range all at once.

        Keys are read by `_metadata_keys` rather than parsed, which IS
        prospective -- both literals in the tree today are valid JSON.
        """
        sites = sorted(COMMANDS_DIR.glob("*.md"))
        assert sites and all(
            q.parent == COMMANDS_DIR and q.suffix == ".md" for q in sites
        ), (
            "site derivation must stay markdown directly under commands/. The "
            ".py fixtures legitimately construct signal tasks carrying "
            "completion_type AND type, and agents/ and protocols/ carry "
            "legitimate algedonic and blocker literals. Line-scoping used to be "
            "a SECOND thing holding those out of range; widening to blocks spent "
            "it, so this glob is now the only thing that does. Got %s" % sites
        )
        found = {}
        for path in sites:
            text = path.read_text()
            for offset, lit in _metadata_literals(text):
                if "auditor" not in _enclosing_block(text, offset):
                    continue
                assert lit is not None, (
                    "%s: a metadata literal at offset %d never closes its "
                    "braces, so this arm cannot read it. A literal it cannot "
                    "read must not pass as clean -- fix the literal, or the "
                    "scanner if the literal is correct." % (path.name, offset)
                )
                found.setdefault(path.name, []).append(lit)
                assert "type" not in _metadata_keys(lit), (path.name, lit)
        missing = {"orchestrate.md", "comPACT.md"} - set(found)
        # EMPTY and INCOMPLETE are different diagnoses and want opposite first
        # actions. Empty means nothing was scanned at all, so the literals are
        # probably fine and the scanner is not; incomplete means the scan worked
        # and a site really lost its literal. One message for both would send a
        # maintainer whose helper is dead hunting a literal that is intact.
        assert found, (
            "NOTHING scanned: no auditor dispatch literal was found in any of "
            "the %d site(s). Suspect the SCANNER before the files -- a dead "
            "`_metadata_literals` or `_enclosing_block`, a glob matching "
            "nothing, or COMMANDS_DIR pointing somewhere else. Sites: %s"
            % (len(sites), [q.name for q in sites])
        )
        assert not missing, (
            "no auditor dispatch literal found in %s. These are a FLOOR, not an "
            "inventory: additional dispatch files are welcome and are scanned "
            "automatically. But when a KNOWN site stops yielding a literal, the "
            "type checks above ran on nothing for that file and this arm became "
            "silently weaker. Found literals in %s."
            % (sorted(missing), sorted(found))
        )

    def test_the_key_reader_does_not_confuse_completion_type_for_type(self):
        """The one string whose silent failure would take the whole arm with it.

        If `type` were read out of `completion_type`, the arm above would redden
        on BOTH correct dispatches in the tree, and the first person it annoyed
        would delete it. Asserted rather than reasoned about: the word-boundary
        argument for why that cannot happen is correct, and arguments of exactly
        that shape have been wrong twice in this file's history.

        The single-quoted and the alternation spellings below are the two that
        `json.loads` could not read at all. They are here to witness that the
        reader now ANSWERS THE QUESTION on them -- not merely that it declines
        to crash, which a bare try/except would also have achieved while going
        blind.

        The last assertion is the reason keys are anchored to `{` or `,`: a
        `type` substring inside a VALUE is not a key, and reading it as one
        would be a false positive on correct work.
        """
        assert _metadata_keys('{"completion_type": "signal"}') == {"completion_type"}
        assert _metadata_keys("{'completion_type': 'signal'}") == {"completion_type"}
        assert _metadata_keys('{"type": "algedonic", "level": "halt"|"alert"}') == {
            "type",
            "level",
        }
        assert _metadata_keys('{"note": "see type: below"}') == {"note"}
