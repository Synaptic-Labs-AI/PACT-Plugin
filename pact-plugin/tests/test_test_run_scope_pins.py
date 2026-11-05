"""Guard for the test-run boundary/scope class on the LLM-loaded surfaces.

Location: pact-plugin/tests/test_test_run_scope_pins.py
Summary: pins the scoped test-run wording at every rewritten site, pins the
         REMOVAL of every unscoped sentence the fix replaced, and runs an
         enumerate-and-assert census so a NEW unscoped obligation cannot land
         silently. Three parts, all load-bearing, explained below.
Used by: the full-suite merge gate.

WHY THE NEGATIVE HALF EXISTS
    A presence pin proves the new sentence is there; it cannot prove the old
    one is gone. comPACT.md carried the identical sentence twice, so a fix that
    rewrote one and missed the other would leave the expensive reading live
    with every presence pin green. Each removed sentence is therefore asserted
    to have count == 0, per file.

WHY _phrase STRIPS ASTERISKS AS WELL AS BACKTICKS
    Two of the removed sentences begin with a bold marker (``**Run tests**``).
    A needle that omitted the asterisks would never match the real line, so the
    negative count would read 0 and the pin would pass while the sentence was
    still present — the vacuous-pin failure this module exists to catch. Bold
    and emphasis markers are formatting, not text, so both sides drop them.

WHY THE CANDIDATE PATTERNS INCLUDE "full suite" AND "test run"
    Measured on the fix: patterns keyed only on "test suite" and the
    verb-first "run tests" form were blind to two of the rewritten sites
    (orchestrate.md's commit-boundary checklist and comPACT.md's post-batch
    full run), because both say "full suite" rather than "test suite". A
    detector that cannot see the sites the fix touched cannot see a new one
    either.

WHY A BOUNDARY WORD IS NOT A SCOPE
    The class rule excludes a site that names the SET of tests to run. Naming
    only WHERE — "at every commit boundary", "before each stage-ready report",
    "at the PR boundary" — leaves the set unbounded, which is the defect. An
    earlier revision excluded on either kind of phrase and accepted "Run the
    full test suite at every commit boundary", i.e. it re-admitted the exact
    defect through the vocabulary the fix itself introduced. Only SET_MARKERS
    exclude now, and a boundary phrase buys nothing on its own.

    The same asymmetry ran the other way for gate-style lines: a candidate
    matching a tests-pass predicate or a suite noun was never promoted into the
    class, so "All tests passing" and "Full suite required at every commit."
    were discarded silently. _in_class promotes those when the line names an
    unbounded set.

A SET WORD IS ALSO CAMOUFLAGE, AND A CONTRAST PHRASE IS WHAT CLOSES IT
    "Run the full test suite in addition to the affected tests." names a set
    and still demands the whole suite, so a SET marker alone would exempt it.
    The arm that catches it keys on a CONTRAST phrase ("not just", "not only",
    "in addition to", "as well as", "then the full suite", "not enough") next
    to a full-suite run demand, and ignores the SET marker entirely for that
    line.

    The alternative was a NEGATION rule, and it is deliberately rejected: to
    decide that "The full suite is not part of this boundary, so run the full
    test suite now." is still a demand, a negation rule has to work out what
    the negation scopes over. A phrase list sidesteps that question, and the
    decoy control below is what proves the decoy is still reported.

THE BOUND, STATED IN SIX PARTS — the statement of record for the whole detector
    The PR body's limit statement uses this decomposition, and so does this
    module. KNOWN_NOT_CAUGHT below asserts what the whole DETECTOR cannot see,
    so it has to be stated against the detector's vocabulary rather than
    against one arm's.
      VOCABULARY — a line is considered at all only if it carries a run-verb
        demand, a tests/suites pass predicate, or a suite noun, each in a
        recognised SHAPE: the run verb with an optional determiner
        (the/your/a) and an optional adjective — subset (full/affected/
        relevant/integration/unit/smoke/fast/scoped), quantifier
        (all/every/both) or whole-set (whole/entire/complete) — immediately
        before a tests/suites noun; the pass predicate as noun plus an
        optional modal (are/is/will/should/must/remain/stay, with optional
        "be" and "all") plus green/pass; the suite noun as full/test suite.
        A demand
        phrased outside these shapes ("make the tests work", "verify the
        change is covered", "run all the tests", where the determiner follows
        the quantifier) is not even a candidate. The pass predicate covers
        both nouns with or without a modal, so "tests must pass" IS a
        candidate and is held out only because it names no unbounded set —
        the merge-boundary value rows in protocols/pact-protocols.md and
        protocols/pact-s5-policy.md classify that way today.
      UNBOUNDEDNESS — the unbounded-set requirement ("all", "every" or "both"
        in either position relative to the noun, or the pre-nominal "full",
        "whole", "entire" or "complete" with "tests"/"suites") gates the
        PROMOTION arm below, not the whole class: a run-verb demand is
        in-class even when it names no set at all ("Run the tests." is
        reported), which over-catches in the safe direction and is deliberate
        — the census exists to make an unscoped demand VISIBLE, and a reader
        can dismiss a false positive it is shown.
      CONTRAST — a line that names a set is still in-class when a contrast
        phrase from a six-string list ("not just", "not only", "in addition",
        "as well as", "then the full suite", "not enough") sits within 500
        characters of a run-verb demand. The anchoring is run-verb ONLY: the
        noun-form limit is witnessed below, with a landing control proving a
        widened anchor set would catch the witness. A gate-form line cannot
        enter the contrast arm AT ALL, whatever the anchoring — the arm
        requires a run-verb or noun-form demand and a gate-form line has
        neither — so its contrast immunity is structural rather than an
        anchoring limit, and it is stated here instead of witnessed. The 500
        is a measured separation, not a derivation: the
        shapes this catches sit inside 400 characters and the comPACT
        carriers' unrelated "not only an `Edit` or a `Write`" sits 873 and
        971 characters away.
      SEGMENTATION — segments split on sentence periods and template pipes,
        not on ";" or dashes, so a scope marker that shares a segment with a
        demand excuses that demand ("Run the full test suite before HANDOFF;
        add unit tests" is one segment; witnessed below). Splitting on more
        boundaries was rejected: an exemption and the demand it applies to
        can sit on opposite sides of a split, and the pre-PR checkpoint puts
        its marker before a colon for exactly that reason.
      PROMOTION — a gate/requirement line is promoted into the class when its
        segment names an unbounded set, but only when that segment carries no
        scope marker and no pre-PR marker: "All tests passing (the tests your
        change affects)" is NOT promoted, because the co-located marker
        scopes the segment (witnessed below).
      EXEMPTION — a pre-PR boundary phrase ("before creating PR", "pre-PR",
        "before PR", "at the PR boundary") exempts the whole segment it sits
        in, including a co-located per-commit full-run demand ("Before PR,
        run the full test suite at every commit boundary" is not reported;
        witnessed below). The exemption is segment-wide because the phrase
        names the boundary the demand belongs to.

    KNOWN_NOT_CAUGHT below is this bound made executable: every entry is a
    spelling one of these parts predicts will be missed, each asserted NOT
    caught. A red there is not a defect — it means the rule grew and the
    documented boundary is stale, so the docstring, the list and the PR body
    move together. This paragraph, that list, and the PR body's limit are three
    statements of ONE boundary.

WHAT THIS MODULE CANNOT DO
    It verifies TEXT. Whether a coder or lead now behaves differently is not
    observable from a static read, and no assertion here should be read as
    evidence of a behaviour change.
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]

SURFACE_GLOBS = (
    "commands/*.md",
    "agents/*.md",
    "skills/*/SKILL.md",
    # A skill's references/ files are opened on demand rather than auto-loaded,
    # but they still instruct the agent that opens them, so an unscoped demand
    # there is the same defect one hop later.
    "skills/*/references/*.md",
    # Every other markdown a skill dir carries (pattern catalogs, function
    # tables, per-skill READMEs) is one opened-file hop from its SKILL.md,
    # and the plugin-root reference/ and templates/ files are opened the same
    # way — an unscoped demand there instructs the same reader.
    "skills/*/*.md",
    "reference/*.md",
    "templates/*.md",
    "protocols/*.md",
)

KNOWN_SITE_FILES = (
    "commands/orchestrate.md",
    "commands/comPACT.md",
    "protocols/pact-workflows.md",
    "protocols/pact-protocols.md",
)


def _phrase(text: str) -> str:
    """Backticks, asterisks and line wrapping are not part of the instruction."""
    return " ".join(text.replace("`", "").replace("*", "").split())


def _text(rel: str) -> str:
    return (PLUGIN / rel).read_text(encoding="utf-8")


def _line_with(text: str, marker: str) -> str:
    """The single line containing `marker`.

    An anchor that is missing or REPEATED fails. A duplicated anchor would
    otherwise let the pin read a different site while believing it read this
    one: "**Before next phase**" occurs three times in orchestrate.md
    (PREPARE, ARCHITECT, CODE), so anchoring a CODE-phase assertion on it
    alone would silently check the PREPARE checklist.
    """
    hits = [line for line in text.splitlines() if marker in line]
    assert len(hits) == 1, f"{marker!r} occurs on {len(hits)} lines, expected 1"
    return hits[0]


# ---------------------------------------------------------------------------
# Part (a): per-site positive pins, and the removal pins
# ---------------------------------------------------------------------------

# The fallback that makes the instruction actionable when a module-to-test
# mapping finds nothing. Named here rather than left to the carrier-identity
# pin: identity only proves the three carriers AGREE, so a shared rewrite of
# the fallback would keep every carrier pin green. Measured — the clause was
# added to all three carriers and this module passed 43/43 without naming it.
FALLBACK_CLAUSE = (
    "where the project's runner does not expose tests by file or name, the suite "
    "named in the runner's own configuration"
)

# The other half of the fallback, and named for the same reason: the runner
# clause covers a suite that cannot be found, this one covers a module that has
# no suite at all. Identity alone cannot see either being rewritten.
NO_SUITE_CLAUSE = (
    "if the module belongs to no suite at all, add a verification test for the "
    "change, run it, and name it in your HANDOFF"
)

# (file, line marker, phrases that must appear on that line). Line scoping, not
# section scoping: a section pin over the CODE phase could not tell its two
# sites apart, so a site could lose its instruction while the phrase survived
# elsewhere in the same section — measured, and every test stayed green.
LINE_PINS = (
    (
        "commands/orchestrate.md",
        "Smoke Testing:",
        (
            # orchestrate.md:780 — the coder GUIDELINES dispatch text.
            "Smoke Testing: Before each stage-ready report (the commit boundary), "
            "run the tests your change affects",
            FALLBACK_CLAUSE,
            NO_SUITE_CLAUSE,
            "The full suite is not part of this boundary.",
            # The verification-tests sentence was rewritten with the fix
            # ("Tests you add" narrowed from "Your tests"); pin the new form
            # so a revert to the unscoped-era phrasing reddens.
            "Tests you add are verification tests",
        ),
    ),
    (
        "commands/orchestrate.md",
        "- [ ] Scoped test run green",
        (
            # orchestrate.md:856 — the CODE-phase boundary checklist.
            "Scoped test run green at every commit boundary",
            "the tests the change affects",
            "the full suite is not a per-commit gate",
            # The obligation clause, not just the opening phrases: the whole
            # tail was unpinned, so a rewrite of it left every pin green.
            "it is owed once at the PR boundary",
            "the TEST-phase checklist below owns it",
            "if TEST is skipped, the lead runs it once before opening the PR",
            # The skip branch's own defer option: without it the TEST-skipped
            # lead owed a local full run even where the project's gate covers
            # it — the same double-run cost the checklist exists to remove.
            "or defers to the project's required full-suite gate once the PR exists",
        ),
    ),
    (
        "commands/orchestrate.md",
        "- [ ] Full test suite run once",
        (
            # orchestrate.md:990 — the single pre-PR full run. The tail is
            # pinned too: the deferral clause is what makes the project-gate
            # branch satisfiable before the PR exists, and "as a whole rather
            # than a subset of paths" is the path-less property the issue
            # asked for — an earlier cut pinned only the opening phrase, so a
            # revert of either clause kept this pin green.
            "Full test suite run once — the single pre-PR full run",
            "run it as a whole rather than a subset of paths",
            "once the PR exists",
        ),
    ),
    (
        "commands/comPACT.md",
        "comPACT mode (concurrent):",
        (
            "Before HANDOFF, run the tests your change affects",
            FALLBACK_CLAUSE,
            NO_SUITE_CLAUSE,
            "The full suite is not part of this boundary.",
        ),
    ),
    (
        "commands/comPACT.md",
        "comPACT mode: Work directly",
        (
            "Before HANDOFF, run the tests your change affects",
            FALLBACK_CLAUSE,
            NO_SUITE_CLAUSE,
            "The full suite is not part of this boundary.",
        ),
    ),
    (
        "commands/comPACT.md",
        "**After all concurrent agents complete**",
        ("then run the full suite once, as a whole",),
    ),
    (
        "commands/comPACT.md",
        "**Run the affected tests**",
        (
            "Run the affected tests",
            "The full suite is the PR/project gate, not this boundary.",
        ),
    ),
    (
        "protocols/pact-workflows.md",
        "**Run the affected tests**",
        (
            "Run the affected tests",
            "The full suite is the PR/project gate, not this boundary.",
        ),
    ),
    (
        "protocols/pact-protocols.md",
        "**Run the affected tests**",
        (
            "Run the affected tests",
            "The full suite is the PR/project gate, not this boundary.",
        ),
    ),
)

# The protocol extract and its SSOT mirror are byte-compared by
# scripts/verify-protocol-extracts.sh; pin the pair here as well so a one-sided
# edit reddens this module first, with a message naming the divergence.
PROTOCOL_PAIR = ("protocols/pact-workflows.md", "protocols/pact-protocols.md")

# (file, sentence that must NOT survive the fix). Each was measured present in
# the parent revision before the fix (counts 1, 1, 2, 1, 2, 1, 1, 1, 1), so a
# zero here is a removal rather than a sentence that never existed.
REMOVED = (
    ("commands/orchestrate.md", "Run the test suite before completing."),
    (
        "commands/orchestrate.md",
        "All tests passing (full test suite; fix any tests your changes break)",
    ),
    ("commands/orchestrate.md", "- [ ] All tests passing"),
    ("commands/orchestrate.md", "Your tests are verification tests"),
    ("commands/comPACT.md", "Run test suite before HANDOFF."),
    ("commands/comPACT.md", "run full test suite."),
    (
        "commands/comPACT.md",
        "Run tests — verify work passes. If tests fail → return to specialist "
        "for fixes (create new agent task, repeat).",
    ),
    (
        "protocols/pact-workflows.md",
        "Run tests — verify work passes. If tests fail → return to specialist "
        "for fixes before committing.",
    ),
    (
        "protocols/pact-protocols.md",
        "Run tests — verify work passes. If tests fail → return to specialist "
        "for fixes before committing.",
    ),
)


@pytest.mark.parametrize(
    "rel,marker,phrases",
    LINE_PINS,
    ids=[f"{rel.split('/')[-1]}:{marker[:28]}" for rel, marker, _ in LINE_PINS],
)
def test_the_scoped_wording_is_on_its_line(rel, marker, phrases):
    body = _phrase(_line_with(_text(rel), marker))
    for phrase in phrases:
        assert _phrase(phrase) in body, (
            f"{rel}: {phrase!r} is missing from the line carrying {marker!r}."
        )


# The operational core is duplicated VERBATIM in the three dispatch carriers,
# because each is a separate string injected into a specialist's prompt and a
# reference would not reach the reader. Presence pins alone cannot keep them
# aligned — three carriers could each contain the fragments while drifting in
# the middle — so the span itself is compared.
CORE_START = "run the tests your change affects"
CORE_END = "The full suite is not part of this boundary."
OPERATIONAL_CARRIERS = (
    ("commands/orchestrate.md", "Smoke Testing:"),
    ("commands/comPACT.md", "comPACT mode (concurrent):"),
    ("commands/comPACT.md", "comPACT mode: Work directly"),
)


def _operational_core(rel: str, marker: str) -> str:
    line = _line_with(_text(rel), marker)
    assert line.count(CORE_START) == 1, f"{rel}: {CORE_START!r} occurs {line.count(CORE_START)}x on the carrier line"
    assert line.count(CORE_END) == 1, f"{rel}: {CORE_END!r} occurs {line.count(CORE_END)}x on the carrier line"
    start = line.index(CORE_START)
    end = line.index(CORE_END) + len(CORE_END)
    return line[start:end]


def test_the_operational_core_is_identical_across_its_three_carriers():
    cores = [_phrase(_operational_core(rel, marker)) for rel, marker in OPERATIONAL_CARRIERS]
    distinct = set(cores)
    assert len(distinct) == 1, (
        "the three dispatch carriers no longer share one operational statement; "
        f"found {len(distinct)} variants:\n"
        + "\n".join(f"  {rel} [{marker[:28]}]: {len(core)} chars"
                    for (rel, marker), core in zip(OPERATIONAL_CARRIERS, cores))
    )


def test_the_operational_core_occurs_exactly_three_times():
    """A fourth copy ANYWHERE in the surfaces, or a lost carrier, must not pass.

    Counted over every enumerated surface rather than over the two files the
    carriers live in: a copy placed in any other instruction surface is a
    fourth carrier, and scoping the count to the two files would let it
    through. Measured at the fix: the core occurs exactly three times across
    all surfaces, so the wider scan costs nothing.
    """
    core = _phrase(_operational_core(*OPERATIONAL_CARRIERS[0]))
    combined = _phrase("".join(_text(rel) for rel in _surface_rels()))
    assert combined.count(core) == 3, (
        f"the operational core occurs {combined.count(core)} times across every "
        "instruction surface, expected 3 (1 canonical + 2 dispatch carriers)"
    )


def test_the_protocol_extract_and_its_ssot_mirror_are_identical():
    left, right = (_phrase(_line_with(_text(rel), "**Run the affected tests**"))
                   for rel in PROTOCOL_PAIR)
    assert left == right, (
        "the protocol extract and the SSOT mirror diverged; edit both in the same "
        "commit or scripts/verify-protocol-extracts.sh will fail"
    )


@pytest.mark.parametrize(
    "rel,sentence", REMOVED, ids=[f"{rel.split('/')[-1]}:{s[:34]}" for rel, s in REMOVED]
)
def test_the_unscoped_sentence_is_gone(rel, sentence):
    body = _phrase(_text(rel))
    assert body.count(_phrase(sentence)) == 0, (
        f"{rel}: {sentence!r} is still present. The fix replaced this sentence with a "
        "scoped one; its survival leaves the unscoped reading live."
    )


@pytest.mark.parametrize(
    "rel,sentence", REMOVED, ids=[f"{rel.split('/')[-1]}:{s[:34]}" for rel, s in REMOVED]
)
def test_every_removal_match_is_live(rel, sentence):
    """Control for the negative half: the matcher must FIRE on the old sentence.

    Without this, a needle that no longer matches real text would count 0 and
    pass the removal pin while the sentence sat in the file.
    """
    wrapped = _phrase(f"prefix {sentence} suffix")
    assert wrapped.count(_phrase(sentence)) == 1


# ---------------------------------------------------------------------------
# Part (b): the enumerate-and-assert census
# ---------------------------------------------------------------------------

# The adjective slot carries the quantifiers (all/every/both) and the whole-set
# adjectives (whole/entire/complete) as well as the subset adjectives: "Run all
# tests", "Run every test" and "Run the whole test suite" are the plainest
# unscoped phrasings of the defect, and until the slot accepted them they were
# not even candidates — invisible to the census, the unlisted check and the
# counted baseline alike. The whole-set trio matches _UNBOUNDED_SET's list on
# purpose: the two patterns widened together or the asymmetry returns.
_RUN = re.compile(
    r"(?i)\b(?:run|execute)\s+(?:the\s+|your\s+|a\s+)?"
    r"(?:(?:full|affected|relevant|integration|unit|smoke|fast|scoped"
    r"|all|every|both|whole|entire|complete)\s+)?"
    r"(?:tests?|suites?)\b"
)
_RUN_BARE = re.compile(r"(?i)\btests?\s+runs?\b")
# The noun form "test run", which the verb-first pattern cannot see. The
# commit-boundary checklist says "Scoped test run green at every commit
# boundary"; without this, dropping its set parenthetical left an unscoped
# obligation the classifier never even treated as a demand.
#
# Bounded to the PREDICATE form on purpose: bare "test run" is an ordinary noun
# ("covered by at least one test run"), and matching that promoted the correct
# carriers, whose own text contains that sentence, to in-class.
_RUN_NOUN = re.compile(r"(?i)\btest\s+run\s+(?:is\s+|must\s+)?(?:green|pass(?:es|ing)?)\b")
# Covers both a tests-pass and a SUITE-pass predicate, with or without a modal
# and with or without "be"/"all" inside it: "tests pass", "tests are green",
# "the suite must be green", "suites must pass", "a suite passes", "the suite
# should be green", "the suites must all pass". The remain/stay verbs (both
# inflections, for plural and singular subjects) belong here for the same
# reason the quantifiers belong in _RUN: the pass predicate is claimed
# vocabulary, and a shape outside the vocabulary is not even a candidate —
# invisible to the census, the unlisted check and the counted baseline alike.
_GATE = re.compile(
    r"(?i)\b(?:tests?|suites?)\s+"
    r"(?:(?:are|is|will|should|must|remains?|stays?)(?:\s+be)?\s+)?"
    r"(?:all\s+)?(?:green|pass(?:es|ing)?)\b"
)
_SUITE = re.compile(r"(?i)\b(?:full|test)[\s-]suites?\b")

CANDIDATE_PATTERNS = (_RUN, _RUN_BARE, _GATE, _SUITE)

# A candidate is out of the class only when it names the SET of tests to run.
# A phrase that names only WHEN or WHERE ("at every commit boundary", "before
# each stage-ready report") leaves the set unbounded and must NOT exclude —
# excluding on it was the defect that let "Run the full test suite at every
# commit boundary" pass the census.
SCOPE_MARKERS = (
    "the tests your change affects",
    "the tests the work affects",
    "the tests the change affects",
    "plus any suite subsets the plan names",
    "the smallest suite that contains",
    "test files that cover the modules",
)

PREEXISTING_SCOPE_PHRASES = (
    "smoke tests",
    "integration tests",
    "unit tests",
    "affected tests",
    "affected test",
)

# Both sets name a scope, so both take a site out of the class. They are kept
# apart only so the grounding test can hold the new-wording markers to a
# stricter standard than the phrases that already existed before the fix.
ALL_SCOPE_MARKERS = SCOPE_MARKERS + PREEXISTING_SCOPE_PHRASES

# The already-correct full-suite boundary. A pass/fail question asked HERE is
# not the defect class, so it stays out. As a REGEX, not a substring: the raw
# substring "before pr" also matches inside "before proceeding", which turned
# the commonest English closer into a pre-PR exemption.
_PREPR = re.compile(
    r"(?i)\bbefore\s+creating\s+pr\b"
    r"|\bpre-pr\b"
    r"|\bbefore\s+pr(?![a-z])"
    r"|\bat\s+the\s+pr\s+boundary\b"
)

# Sentence and template-line boundaries. NOT ":" — the Pre-PR checkpoint puts
# its marker before a colon and its question after it, and splitting there
# would separate an exemption from the demand it applies to.
_SEGMENT_BOUNDARY = re.compile(r"(?<=\.)\s+|\|")


def _segments(line: str) -> list[str]:
    return [segment for segment in _SEGMENT_BOUNDARY.split(line) if segment.strip()]

# A line that demands or gates an UNBOUNDED set without a run verb — the other
# half of the class, previously a candidate the classifier silently discarded.
# Both orders: "all of the suites", "both test suites", "every suite", and
# "the test suites must all pass". A quantifier-only rule in one direction
# missed every post-nominal form.
_UNBOUNDED_SET = re.compile(
    r"(?i)\b(?:all|every|both)\b[^.\n]{0,25}\b(?:tests?|suites?)\b"
    r"|\b(?:tests?|suites?)\b[^.\n]{0,25}\ball\b"
    # "full" was the only whole-set adjective this accepted; "whole", "entire"
    # and "complete" say the same thing and were a fourth unstated bound.
    r"|\b(?:the\s+)?(?:full|whole|entire|complete)\s+(?:tests?|suites?)\b"
    r"|\b(?:full|whole|entire|complete)[\s-]suites?\b"
)
_REQUIRED = re.compile(r"(?i)\b(?:required|mandatory|must run|needs? to run)\b")

# A signal-table row describes a state ("All tests pass, adequate coverage");
# it demands nothing, so it is a status description rather than an obligation.
_SIGNAL_ROW = re.compile(r"\*\*(?:GREEN|YELLOW|RED)\*\*")

# A CONTRAST demand adds the whole suite to a named subset, so the subset
# marker does not bound it. Kept as a phrase list rather than a negation rule:
# a negation rule would have to decide whether "not part of this boundary"
# negates the demand, and that is exactly the shape of the self-reversing
# decoy the census must keep catching.
_CONTRAST = re.compile(
    r"(?i)\b(?:not just|not only|in addition|as well as|then the full suite|not enough)\b"
)

# ...and the phrase has to be NEAR the demand it qualifies. An instruction line
# in these surfaces is one several-thousand-character line, so a bare
# whole-line search finds a contrast word in an unrelated clause: the two
# comPACT carriers contain "not only an `Edit` or a `Write`" in the CLAUDE.md
# prohibition, 873 and 971 characters from the nearest run demand. Measured
# margin: the shapes this must catch sit inside 400 characters; the carriers sit
# beyond 870. 500 is the separation, and it is a measured constant, not a
# derived one.
_CONTRAST_WINDOW = 500


def _contrast(line: str) -> bool:
    """A contrast phrase within reach of a RUN-VERB demand it could qualify.

    Anchoring is run-verb only: gate-form and noun-form demands do not anchor
    a contrast phrase (that limit is witnessed in KNOWN_NOT_CAUGHT), so the
    anchor set here is _RUN, not the wider demand definition the class rule
    uses. Every match is considered, not the first: a line can carry a scoped
    run near the front and an additive full-suite run far behind it, and
    anchoring on only the first demand made the later one invisible.
    """
    run_starts = [match.start() for match in _RUN.finditer(line)]
    if not run_starts:
        return False
    return any(
        min(abs(contrast.start() - run_start) for run_start in run_starts)
        <= _CONTRAST_WINDOW
        for contrast in _CONTRAST.finditer(line)
    )

# Standing exemptions, each with the reason it is not the defect class. Keyed by
# a CONTENT marker rather than a line number: a line number drifts silently,
# and this list is small enough that every entry should be read on sight.
ALLOWLIST = (
    (
        "skills/pact-agent-teams/SKILL.md",
        "(re-edit files, re-run tests, etc.)",
        "A parenthetical listing what revising a rejected HANDOFF entails. It sets "
        "no boundary and gates no commit; it is not an obligation site.",
    ),
    (
        "skills/pact-testing-strategies/SKILL.md",
        "After restore, re-run the test scope",
        "A mutation-probe recovery step: 'the test scope' back-references the scope "
        "already run in the same recipe. It is a recovery instruction, not a "
        "commit-boundary obligation.",
    ),
    (
        "commands/comPACT.md",
        "then run the full suite once, as a whole",
        "The one deliberate full-suite exception: the post-concurrency batch, where "
        "separately scoped changes first meet. It names itself the integration "
        "exception and bounds the run to the batch, so the set is not unbounded; it "
        "is exempt rather than scoped by a SET marker.",
    ),
    (
        "skills/pact-architecture-patterns/references/anti-patterns.md",
        "npm run test:coverage",
        "A shell command inside an example block, not an instruction to run a "
        "boundary gate; it names a package script, not a test set.",
    ),
    (
        "skills/pact-testing-strategies/references/performance-testing.md",
        "k6 run tests/load/api-load-test.js",
        "A load-test invocation inside an example block for the k6 tool; it names "
        "one script path and carries no boundary obligation.",
    ),
    (
        "commands/peer-review.md",
        "Use these facts to run the tests",
        "An operational pointer inside the cold-reviewer prompt: the "
        "{operational_facts} placeholder immediately above it carries the "
        "how-to-run (interpreter or venv form, lint gate command). The reviewer "
        "runs tests to verify findings; it sets no boundary and gates no commit.",
    ),
    (
        "skills/pact-coding-standards/SKILL.md",
        "every test green. The import-hygiene check",
        "Describes a blind spot in behavioural tests — a dead import passes every "
        "test green. It demands nothing and names no boundary; the quantifier that "
        "catches it was added for 'every suite must pass', not for prose.",
    ),
)

# Measured on the fixed tree. The count is a floor, not a proof: a new candidate
# changes it, and that is the signal. A legitimate new line is either scoped (so
# it belongs in SCOPE_MARKERS' shape) or an explicit allowlist entry — in both
# cases this number moves and the operator decides, rather than the new site
# arriving silently.
#
# Per file as well as in total: with only a total, one candidate added in one
# file and one lost in another cancel out, and the operator who sees "38, same
# as before" has nothing to look at. The failure names the file that moved.
BASELINE_BY_FILE = {
    "agents/pact-backend-coder.md": 1,
    "agents/pact-database-engineer.md": 1,
    "agents/pact-devops-engineer.md": 1,
    "agents/pact-frontend-coder.md": 1,
    "agents/pact-orchestrator.md": 3,
    "agents/pact-qa-engineer.md": 1,
    "agents/pact-test-engineer.md": 7,
    "commands/comPACT.md": 4,
    "commands/orchestrate.md": 6,
    # One candidate, allowlisted above as the cold-reviewer prompt's
    # operational pointer; the row keeps the file visible to the census so a
    # SECOND candidate line here reddens the count.
    "commands/peer-review.md": 1,
    "protocols/pact-phase-transitions.md": 1,
    "protocols/pact-protocols.md": 6,
    "protocols/pact-s5-policy.md": 2,
    "protocols/pact-scope-phases.md": 1,
    "protocols/pact-variety.md": 1,
    "protocols/pact-workflows.md": 1,
    # Rows for the extended glob families. Both files hold exactly one
    # candidate line today, each out of class (descriptive prose in a
    # background-job note; a "Create test suite:" step inside an example
    # block) — counted so that a NEW candidate in these files reddens the
    # baseline rather than arriving silently.
    "reference/unattended-runs.md": 1,
    "skills/n8n-workflow-patterns/ai_agent_workflow.md": 1,
    "skills/pact-agent-teams/SKILL.md": 2,
    "skills/pact-architecture-patterns/references/anti-patterns.md": 1,
    "skills/pact-coding-standards/SKILL.md": 2,
    "skills/pact-prepare-research/references/api-exploration-template.md": 2,
    "skills/pact-prepare-research/references/requirements-analysis.md": 1,
    "skills/pact-testing-strategies/SKILL.md": 6,
    "skills/pact-testing-strategies/references/integration-patterns.md": 1,
    "skills/pact-testing-strategies/references/performance-testing.md": 1,
    "skills/pact-testing-strategies/references/test-pyramid.md": 1,
}

# Derived, never restated: the per-file map is the source of truth, so a second
# literal cannot drift away from it.
BASELINE_CANDIDATES = sum(BASELINE_BY_FILE.values())


def _surfaces() -> list[Path]:
    found: list[Path] = []
    for pattern in SURFACE_GLOBS:
        matches = sorted(PLUGIN.glob(pattern))
        assert matches, f"surface glob {pattern!r} matched no files"
        found.extend(matches)
    return sorted(set(found))


def _surface_rels() -> list[str]:
    return [path.relative_to(PLUGIN).as_posix() for path in _surfaces()]


def _candidates(surfaces):
    """Yield (relative path, line number, line) for every candidate line."""
    for path in surfaces:
        rel = path.relative_to(PLUGIN).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in CANDIDATE_PATTERNS):
                yield rel, lineno, line


def _segment_in_class(segment: str) -> bool:
    """The class rule applied to ONE sentence or clause.

    Three ways in: an imperative to run, a gate/requirement over an unbounded
    set, or a CONTRAST demand that names a full-suite run alongside a smaller
    set. The gate arm exists because a gate line carries no run verb, so a
    reworded "All tests passing" would otherwise be discarded silently. The
    contrast arm exists because naming a set is not the same as bounding the
    demand to it: "run the full test suite, not just the integration tests"
    names a set and still demands everything.
    """
    lowered = segment.lower()
    demand = bool(_RUN.search(segment) or _RUN_NOUN.search(segment))
    unbounded = bool(_UNBOUNDED_SET.search(segment))
    if _contrast(segment) and unbounded and demand:
        return True
    if any(marker in lowered for marker in ALL_SCOPE_MARKERS):
        return False
    if _PREPR.search(segment):
        return False
    if demand:
        return True
    return unbounded and bool(_GATE.search(segment) or _REQUIRED.search(segment))


def _in_class(line: str) -> bool:
    """IN if ANY segment of the line is in class.

    Per SEGMENT, not per line. These dispatch templates are single physical
    lines that carry the test obligation and a test-count noun in different
    sentences, so a scope phrase anywhere on the line used to excuse the whole
    line: the pre-fix carriers put "New unit tests for logic changes." next to
    "Run test suite before HANDOFF." and the restored defect classified as
    scoped. A signal row stays a whole-line exclusion because "a signal row is
    a status description" is a property of the row, not of one of its cells.
    """
    if _SIGNAL_ROW.search(line):
        return False
    return any(_segment_in_class(segment) for segment in _segments(line))


def _allowlisted(rel: str, line: str, allowlist) -> bool:
    lowered = line.lower()
    return any(entry_rel == rel and marker.lower() in lowered
               for entry_rel, marker, _ in allowlist)


def _unlisted(items, allowlist):
    """The census decision, over (rel, lineno, line) items.

    The control tests call THIS function with synthetic items, so the control
    cannot drift from the code path the real census uses.
    """
    return [
        (rel, lineno, line)
        for rel, lineno, line in items
        if _in_class(line) and not _allowlisted(rel, line, allowlist)
    ]


def _census():
    surfaces = _surfaces()
    candidates = list(_candidates(surfaces))
    return surfaces, candidates, _unlisted(candidates, ALLOWLIST)


def test_the_surface_enumeration_is_not_empty():
    surfaces = _surfaces()
    assert len(surfaces) >= 50, f"only {len(surfaces)} surfaces enumerated"
    present = {path.relative_to(PLUGIN).as_posix() for path in surfaces}
    for rel in KNOWN_SITE_FILES:
        assert rel in present, f"{rel} is missing from the enumerated surfaces"
    # The glob families beyond skills/*/SKILL.md are pinned by representative
    # members: a family whose files hold zero candidates leaves no baseline
    # row, so dropping its glob would otherwise keep every test green while
    # re-opening the blindness the family was added to close.
    for rel in (
        "reference/config.md",
        "templates/environment-model.md",
        "skills/n8n-code-javascript/BUILTIN_FUNCTIONS.md",
    ):
        assert rel in present, f"{rel} is missing — a surface glob family was dropped"


def test_the_candidate_baseline_holds():
    _, candidates, _ = _census()
    by_file = collections.Counter(rel for rel, _, _ in candidates)
    moved = [
        f"  {rel}: {by_file.get(rel, 0)} now, {pinned} pinned"
        for rel, pinned in sorted(BASELINE_BY_FILE.items())
        if by_file.get(rel, 0) != pinned
    ] + [
        f"  {rel}: {count} now, not in the baseline"
        for rel, count in sorted(by_file.items()) if rel not in BASELINE_BY_FILE
    ]
    assert len(candidates) == BASELINE_CANDIDATES and not moved, (
        f"candidate lines: {len(candidates)}, baseline {BASELINE_CANDIDATES}.\n"
        + ("files whose candidate count moved:\n" + "\n".join(moved) + "\n" if moved else "")
        + "Read the listed file(s): if the new line is scoped, extend the scope "
        "markers; if it is legitimately exempt, add an allowlist entry with a "
        "reason; if a candidate was LOST, that may be the classifier no longer "
        "matching a site still in the class. Then update the baseline. Do not "
        "update the baseline on its own."
    )


def test_no_in_class_site_is_unlisted():
    _, _, unlisted = _census()
    assert unlisted == [], (
        "unscoped test-run obligation(s) with no scope and no written exemption:\n"
        + "\n".join(f"  {rel}:{lineno}: {line.strip()[:120]}"
                    for rel, lineno, line in unlisted)
    )


def test_the_allowlist_is_live():
    """A standing exemption that no longer matches anything must not linger."""
    _, candidates, _ = _census()
    for rel, marker, reason in ALLOWLIST:
        hits = [c for c in candidates if c[0] == rel and marker.lower() in c[2].lower()]
        assert len(hits) == 1, (
            f"allowlist entry {marker!r} in {rel} matches {len(hits)} candidates, "
            f"expected 1 — the exemption has gone stale ({reason})"
        )


def test_scope_markers_are_grounded_in_the_fixed_text():
    """Every scope marker is either in the new wording or a pre-existing phrase.

    This is what stops the scope rule from being widened after the fact: a
    marker invented to make a site pass has no home in either set.
    """
    fixed = " ".join(_phrase(_text(rel)) for rel in KNOWN_SITE_FILES).lower()
    allowed = set(PREEXISTING_SCOPE_PHRASES)
    ungrounded = [m for m in SCOPE_MARKERS if m not in fixed and m not in allowed]
    assert ungrounded == [], f"scope markers with no grounding: {ungrounded}"


# ---------------------------------------------------------------------------
# Part (c): both controls
# ---------------------------------------------------------------------------

SYNTHETIC_UNSCOPED = "Testing: Run the test suite before completing."


def test_control_the_candidate_matcher_fires():
    """(i) Matcher-liveness: the detector is live on an unscoped instruction."""
    assert any(pattern.search(SYNTHETIC_UNSCOPED) for pattern in CANDIDATE_PATTERNS)


def test_control_the_census_reports_an_unlisted_candidate():
    """(ii) Census-level: the SAME decision function flags a new unscoped site.

    Run against the real allowlist, so a green here also proves the exemptions
    do not swallow a site they were never written for.
    """
    unlisted = _unlisted([("synthetic/surface.md", 1, SYNTHETIC_UNSCOPED)], ALLOWLIST)
    assert len(unlisted) == 1, "the census did not report a synthetic unscoped site"


def test_control_a_scoped_instruction_is_not_reported():
    """Discrimination arm: the census is not simply 'flag everything'."""
    scoped = ("Testing: Before each commit boundary, run the tests your change "
              "affects. The full suite is not part of this boundary.")
    assert _unlisted([("synthetic/surface.md", 1, scoped)], ALLOWLIST) == []


# The remain/stay predicates close a docstring-vs-implementation gap: the pass
# predicate is claimed vocabulary, so these must be CANDIDATES — a non-candidate
# is invisible to the counted baseline. They stay out of the class because they
# name no unbounded set, held out exactly like the documented "tests must pass".
# Both inflections are pinned: a singular subject takes "remains"/"stays", and
# a list that accepted only the bare verbs would re-open the same gap one
# conjugation over.
@pytest.mark.parametrize(
    "line",
    (
        "Tests remain green before commit.",
        "Tests stay green before commit.",
        "The suite remains green.",
        "Suites stay green after the merge.",
    ),
    ids=["remain", "stay", "remains", "stays"],
)
def test_control_remain_stay_predicates_are_candidates_but_bounded(line):
    assert any(pattern.search(line) for pattern in CANDIDATE_PATTERNS), (
        f"{line!r} is not a candidate; the pass predicate is claimed "
        "vocabulary and a non-candidate is invisible to the baseline"
    )
    assert _unlisted([("synthetic/surface.md", 1, line)], ALLOWLIST) == [], (
        f"{line!r} was reported; it names no unbounded set and is held out "
        "like 'tests must pass'"
    )


# Filler for the distance shapes: long enough to push a contrast phrase past a
# small window without changing what the line demands.
_FAR = "filler " * 50

# Every shape that defeated an earlier revision of this classifier. Each was
# ACCEPTED — reported as out of class — before the SET-only exclusion and the
# gate promotion, and each is an unscoped demand: it says WHEN or demands the
# whole set, and never says WHICH tests.
CAUGHT_SHAPES = (
    "Run the full test suite.",
    "Run the full test suite at every commit boundary.",
    "Run the full test suite before each stage-ready report.",
    "- [ ] All tests passing",
    "Full suite required at every commit.",
    # The suite-pass predicate family: each says the whole set must be green
    # without running-verb agreement, and each was accepted until the gate
    # predicate covered a SUITE as well as "tests".
    "The full suite must be green before commit.",
    "Make sure the full suite passes before committing.",
    "All suites must pass before the commit.",
    # The additive family: each NAMES a set and still demands the whole suite,
    # so the SET marker must not bound the demand.
    "Run the full test suite, not just the integration tests you changed.",
    "Run the full test suite before completing; the affected tests alone are not enough.",
    "Run the full test suite in addition to the affected tests.",
    # Adjacent vocabulary in the suite-predicate family: quantifiers other than
    # "all", a "be" inside the modal, and the plural "suites".
    "Every suite must pass before the commit.",
    "Both test suites must pass before the commit.",
    "All of the suites must pass before the commit.",
    "The full suite should be green before commit.",
    "The test suites must all pass before the commit.",
    # Demands that a first-match-only contrast scan missed: the qualifying
    # phrase sits far from the FIRST run demand, or before the second one.
    _FAR + "Run the tests your change affects, then " + _FAR
    + "run the full test suite in addition to the affected tests.",
    "In addition to the affected tests, and after " + _FAR + "run the full test suite.",
    "Run the affected tests. " + _FAR + "In addition, run the full test suite.",
    # Caught DESPITE its wording rather than because of it: "in full" names no
    # set, but the run verb makes the line a candidate and nothing excludes it.
    "Run the tests in full before committing.",
    # Whole-set adjectives other than "full". A contrast phrase sat 30 chars
    # from a recognised run demand and every documented bound was satisfied, so
    # the miss was a fourth, unstated bound in the set vocabulary.
    "Run the affected tests, then in addition the whole test suite.",
    "Make the entire suite green before commit.",
    "The complete suite must be green before commit.",
    # F1: the pre-PR marker used to be a raw substring, so "before pr" matched
    # inside "before proceeding" and this common closer was exempted.
    "Before proceeding, run the full test suite.",
    # F3: the commit-boundary checklist with its set parenthetical removed. The
    # noun form "test run green" was not treated as a demand at all.
    "- [ ] Scoped test run green at every commit boundary",
    # F2: the PRE-FIX carrier shapes, taken from the parent revision. Each
    # carries a test-count noun ("unit tests") in the same line as the unscoped
    # demand, which is what let a scope phrase excuse the whole line.
    "Testing: New unit tests for logic changes. Fix broken existing tests. "
    "Run test suite before HANDOFF.",
    "Testing: New unit tests for logic changes (optional for trivial changes). "
    "Fix broken existing tests. Run test suite before HANDOFF.",
    # Quantifier and whole-set adjectives between the verb and the noun — the
    # plainest unscoped phrasings. Until the adjective slot accepted them these
    # were not even candidates, so no guard layer could see them at all; each
    # of these arms failed (was not reported) against the narrower slot.
    "Run all tests before completing.",
    "Run every test before completing.",
    "Run both test suites before HANDOFF.",
    "Run the whole test suite before HANDOFF.",
    "Run the entire test suite before completing.",
    "Run the complete test suite before completing.",
    # Moved here from KNOWN_NOT_CAUGHT: with "whole" in the adjective slot the
    # run demand is recognised, so the contrast phrase now has an anchor. The
    # retired note blamed the anchoring; the anchoring was sound, the
    # vocabulary was the gap.
    "Run the whole test suite, not just the integration tests you changed.",
)


# The executable form of the residual bound. Every spelling below defeats the
# lexical rule TODAY, and each is asserted NOT to be reported — so the boundary
# is tested rather than described. If a future change starts catching one, this
# test reddens and the docstring and the PR body are updated together with it.
# That is the point: three statements of one limit must move as one.
#
# The noun-form contrast witness is named because a landing control executes
# it against a widened anchor set: it must carry an unbounded set AND a
# noun-form demand AND a contrast phrase AND a set marker — drop any one and
# it either reddens at head (no marker) or can never redden at all (no
# unbounded set, the vacuity the external review caught in the first cut).
NOUN_CONTRAST_WITNESS = (
    "The full suite stays out of this boundary: test run green at every "
    "commit boundary, not just the unit tests."
)

KNOWN_NOT_CAUGHT = (
    (
        "The whole test set must pass before commit.",
        "vocabulary: 'test set' is not a suite noun, so the line is not even a candidate",
    ),
    (
        "The test set must pass before commit.",
        "vocabulary: no suite noun for the demand to attach to",
    ),
    (
        "The suite as a whole must be green before commit.",
        "vocabulary: a bare 'suite' is neither an accepted suite noun nor followed by a modal, so the line is not even a candidate",
    ),
    (
        "All of our checks must be green before commit.",
        "vocabulary: 'checks' is not a tests/suites noun, so the quantifier binds nothing",
    ),
    (
        "Nothing red before commit.",
        "vocabulary: no run verb, no pass predicate and no suite noun, so it is not a candidate",
    ),
    (
        "Nothing is red.",
        "vocabulary: a state with no run verb, pass predicate or suite noun",
    ),
    (
        "No failing tests before commit.",
        "vocabulary: 'no failing tests' is not a tests-pass predicate",
    ),
    (
        "Make the tests work before you commit.",
        "vocabulary: 'work' is not a pass predicate, so the line is not a candidate",
    ),
    (
        "Verify the change is covered.",
        "vocabulary: no run verb, no pass predicate and no suite noun, so it is not a candidate",
    ),
    # One witness per remaining part, so the list exercises the bound it states
    # rather than only its first clause.
    (
        "Make sure the tests are green before committing.",
        "unboundedness: 'the tests' is a bare definite plural, not one of the quantifier or whole-set forms, so the demand reads as bounded",
    ),
    (
        "Run the full test suite on top of the affected tests.",
        "contrast: 'on top of' is not one of the six contrast strings, so the named set bounds the demand",
    ),
    # Contrast anchoring is run-verb only, so camouflage beside a noun-form
    # demand is not reported. Frozen by design: widening the anchor set would
    # widen the arm, and the arm is frozen at its stated bound. The witness
    # deliberately KEEPS a set marker ("unit tests"): without one the demand
    # branch reports the line at head and it cannot be a not-caught entry at
    # all, and the marker is no vacuity here because the contrast branch runs
    # BEFORE the marker check — a widened anchor set overrides it. The landing
    # control below proves that by execution. A gate-form contrast witness is
    # impossible, not merely hard: the arm requires a run-verb or noun-form
    # demand and a gate-form line has neither, so no anchoring change alone
    # can make one redden — stated in the docstring instead of witnessed.
    (
        NOUN_CONTRAST_WITNESS,
        "contrast: a noun-form demand does not anchor a contrast phrase — only "
        "run-verb demands do; the co-located set marker holds the line out at "
        "head, and the branch order is what lets a widened anchor set override it",
    ),
    # Segmentation granularity: the split is sentence periods and pipes, not
    # semicolons, so a co-located scope marker excuses a demand in the same
    # segment. The counted baseline still reddens when such a line lands.
    (
        "Run the full test suite before HANDOFF; add unit tests for logic changes.",
        "segmentation: ';' is not a segment boundary, so a scope marker in the same segment "
        "excuses a demand elsewhere in it",
    ),
    # Promotion is blocked by a co-located scope marker: the docstring's
    # promotion claim is conditional, and this is the witness.
    (
        "All tests passing (the tests your change affects)",
        "promotion: the gate arm promotes only an unscoped segment — a co-located scope "
        "marker blocks the promotion",
    ),
    # The pre-PR exemption is segment-wide, so it can swallow a co-located
    # per-commit full-run demand. Baseline visibility is the mitigation.
    (
        "Before PR, run the full test suite at every commit boundary.",
        "exemption: a pre-PR boundary phrase exempts the whole segment, including a "
        "co-located per-commit full-run demand",
    ),
    # Bound-edge spellings observed in verify-only re-review: each defeats a
    # different arm for a different stated reason, so each is witnessed
    # separately rather than folded into one entry.
    (
        "Every test must be run before commit.",
        "vocabulary: the passive voice puts the noun before the run verb, which no "
        "recognised shape covers, so the line is not even a candidate",
    ),
    (
        "The full suite has to pass before commit.",
        "promotion: 'has to' is outside the predicate modal list, so the gate arm "
        "never fires and an unbounded-set line phrased this way is not promoted",
    ),
    (
        "Run each test before HANDOFF.",
        "vocabulary: 'each' is outside the stated quantifier list (all/every/both), "
        "so the run-verb shape does not match and the line is not a candidate",
    ),
)


# The parts of the stated bound, in the docstring's order: the three detector
# vocabulary parts plus the three doctrine parts (segmentation granularity,
# gate-arm promotion, segment-wide pre-PR exemption). A KNOWN_NOT_CAUGHT
# reason must lead with one of these, so the list stays expressed against the
# bound rather than against free prose.
BOUND_PARTS = (
    "vocabulary",
    "unboundedness",
    "contrast",
    "segmentation",
    "promotion",
    "exemption",
)


def test_every_residual_entry_names_the_part_of_the_bound_it_fails():
    """The residual list is expressed against the six-part bound, not free prose.

    Every reason leads with the part it belongs to, so the list and the
    docstring cannot drift into describing different boundaries.
    """
    for shape, reason in KNOWN_NOT_CAUGHT:
        part = reason.split(":", 1)[0]
        assert part in BOUND_PARTS, (
            f"{shape!r}: the reason does not start with one of {BOUND_PARTS}: "
            f"{reason!r}"
        )


@pytest.mark.parametrize(
    "shape,reason", KNOWN_NOT_CAUGHT, ids=[s[:38] for s, _ in KNOWN_NOT_CAUGHT]
)
def test_control_known_not_caught_shapes_stay_uncaught(shape, reason):
    """The residual bound, asserted rather than described.

    A red here is not a defect — it means the rule grew and the documented
    boundary is now stale. Update the docstring's bound, this list, and the PR
    body together.
    """
    unlisted = _unlisted([("synthetic/surface.md", 1, shape)], ALLOWLIST)
    assert unlisted == [], (
        f"the census now reports {shape!r} ({reason}). The documented bound says it "
        "does not. Either narrow the rule back, or move this shape to "
        "CAUGHT_SHAPES and correct the docstring and the PR body in the same change."
    )


def test_control_the_self_reversing_decoy_is_still_reported():
    """The control that makes the contrast rule safe rather than merely wider.

    "The full suite is not part of this boundary, so run the full test suite
    now, in addition to the affected tests." uses the fix's own corrective
    sentence as camouflage and carries its own scope marker, so the demand
    branch cannot be what reports it — only the contrast arm can. A contrast
    PHRASE list catches the additive family without deciding whether a
    negation negates; a NEGATION rule would have had to decide, and would have
    let this through. If a future edit turns the contrast arm into a negation
    arm, or deletes it, this is the test that reddens.
    """
    decoy = ("The full suite is not part of this boundary, so run the full "
             "test suite now, in addition to the affected tests.")
    assert any(marker in decoy.lower() for marker in ALL_SCOPE_MARKERS), (
        "the decoy no longer carries a scope marker; without one the demand "
        "branch alone reports it, and a neutered contrast arm would stay green"
    )
    assert len(_unlisted([("synthetic/surface.md", 1, decoy)], ALLOWLIST)) == 1, (
        "the self-reversing decoy is no longer reported; the contrast rule has "
        "been widened into a negation rule"
    )


def test_the_noun_contrast_witness_reddens_under_widened_anchoring(monkeypatch):
    """Mutation landing control for the noun-form contrast witness.

    A not-caught entry pins the anchoring bound only if widening the anchor
    set would actually catch it — otherwise it asserts a bound nobody can
    cross, which was the defect the external review caught in the first cut.
    The mutation anchors _contrast on the noun-form demand as well (everything
    else unchanged); under it the witness MUST redden. The retired shape,
    which lacked an unbounded set and so could not redden under any anchoring,
    must stay green under the same mutation — the pair proves this control
    discriminates vacuous from live witnesses.
    """
    def widened(line):
        anchors = [match.start() for match in _RUN.finditer(line)]
        anchors += [match.start() for match in _RUN_NOUN.finditer(line)]
        if not anchors:
            return False
        return any(
            min(abs(match.start() - anchor) for anchor in anchors) <= _CONTRAST_WINDOW
            for match in _CONTRAST.finditer(line)
        )

    monkeypatch.setattr(sys.modules[__name__], "_contrast", widened)
    retired = ("Test run green at every commit boundary, not just the "
               "affected tests.")
    assert _unlisted([("synthetic/surface.md", 1, NOUN_CONTRAST_WITNESS)], ALLOWLIST), (
        "the noun-form contrast witness no longer reddens under widened "
        "anchoring; it has gone vacuous — something other than the anchoring "
        "is excluding it"
    )
    assert _unlisted([("synthetic/surface.md", 1, retired)], ALLOWLIST) == [], (
        "the retired shape reddens under widened anchoring; it was not vacuous "
        "after all — re-examine its removal"
    )


@pytest.mark.parametrize("line", CAUGHT_SHAPES, ids=[s[:40] for s in CAUGHT_SHAPES])
def test_control_every_unscoped_shape_is_reported(line):
    unlisted = _unlisted([("synthetic/surface.md", 1, line)], ALLOWLIST)
    assert len(unlisted) == 1, (
        f"the census accepted {line!r}. It names no SET of tests, so it is an "
        "unscoped demand and must be reported."
    )
