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

WHAT THE DETECTOR DOES NOT SEE
    The gate predicate covers a tests-pass and a suite-pass form with or
    without a modal, so "tests must pass" IS a candidate and is held OUT only
    because it names no unbounded set — the merge-boundary value statements in
    protocols/pact-protocols.md and protocols/pact-s5-policy.md classify that
    way today. What remains outside the detector is a demand phrased with no
    run verb, no pass predicate and no suite noun at all ("make the tests
    work before you commit", "verify the change is covered"); such a line is
    not a candidate, so neither the census nor the controls can see it.

WHAT THIS MODULE CANNOT DO
    It verifies TEXT. Whether a coder or lead now behaves differently is not
    observable from a static read, and no assertion here should be read as
    evidence of a behaviour change.
"""

from __future__ import annotations

import collections
import re
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
        ),
    ),
    (
        "commands/orchestrate.md",
        "- [ ] Scoped test run green",
        (
            # orchestrate.md:856 — the CODE-phase boundary checklist.
            "Scoped test run green at every commit boundary",
            "the full suite is not a per-commit gate",
        ),
    ),
    (
        "commands/orchestrate.md",
        "- [ ] Full test suite run once",
        (
            # orchestrate.md:990 — the single pre-PR full run.
            "Full test suite run once — the single pre-PR full run",
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

_RUN = re.compile(
    r"(?i)\b(?:run|execute)\s+(?:the\s+|your\s+|a\s+)?"
    r"(?:(?:full|affected|relevant|integration|unit|smoke|fast|scoped)\s+)?"
    r"(?:tests?|suites?)\b"
)
_RUN_BARE = re.compile(r"(?i)\btests?\s+runs?\b")
# Covers both a tests-pass and a SUITE-pass predicate, with or without a modal:
# "tests pass", "tests are green", "the suite must be green", "suites must
# pass", "a suite passes". The suite half was missing and let the whole
# suite-predicate family through uncaught.
_GATE = re.compile(
    r"(?i)\b(?:tests?|suites?)\s+(?:(?:are|is|must\s+be|must|will|should)\s+)?"
    r"(?:green|pass(?:es|ing)?)\b"
)
_SUITE = re.compile(r"(?i)\b(?:full|test)[\s-]suite\b")

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
# not the defect class, so it stays out.
PREPR_MARKERS = (
    "before creating pr",
    "pre-pr",
    "before pr",
    "at the pr boundary",
)

# A line that demands or gates an UNBOUNDED set without a run verb — the other
# half of the class, previously a candidate the classifier silently discarded.
_UNBOUNDED_SET = re.compile(
    r"(?i)\b(?:all|the full|full)\s+(?:tests?|suites?)\b|\bfull[\s-]suite\b"
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
    r"(?i)\b(?:not just|not only|in addition to|as well as|then the full suite|not enough)\b"
)

# ...and the phrase has to be NEAR the demand it qualifies. An instruction line
# in these surfaces is one several-thousand-character line, so a bare
# whole-line search finds a contrast word in an unrelated clause: the two
# comPACT carriers contain "not only an `Edit` or a `Write`" in the CLAUDE.md
# prohibition, hundreds of characters from the test-run demand, and a
# whole-line rule classified both shipped carriers as in-class.
_CONTRAST_BEFORE = 160
_CONTRAST_AFTER = 240


def _contrast(line: str) -> bool:
    """A contrast phrase within reach of the run demand it qualifies."""
    match = _RUN.search(line)
    if not match:
        return False
    lo = max(0, match.start() - _CONTRAST_BEFORE)
    hi = match.end() + _CONTRAST_AFTER
    return bool(_CONTRAST.search(line[lo:hi]))

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
BASELINE_CANDIDATES = 48
BASELINE_BY_FILE = {
    "agents/pact-backend-coder.md": 1,
    "agents/pact-database-engineer.md": 1,
    "agents/pact-devops-engineer.md": 1,
    "agents/pact-frontend-coder.md": 1,
    "agents/pact-orchestrator.md": 3,
    "agents/pact-test-engineer.md": 6,
    "commands/comPACT.md": 4,
    "commands/orchestrate.md": 4,
    "protocols/pact-phase-transitions.md": 1,
    "protocols/pact-protocols.md": 6,
    "protocols/pact-s5-policy.md": 2,
    "protocols/pact-scope-phases.md": 1,
    "protocols/pact-variety.md": 1,
    "protocols/pact-workflows.md": 1,
    "skills/pact-agent-teams/SKILL.md": 2,
    "skills/pact-architecture-patterns/references/anti-patterns.md": 1,
    "skills/pact-coding-standards/SKILL.md": 2,
    "skills/pact-prepare-research/references/api-exploration-template.md": 2,
    "skills/pact-prepare-research/references/requirements-analysis.md": 1,
    "skills/pact-testing-strategies/SKILL.md": 4,
    "skills/pact-testing-strategies/references/integration-patterns.md": 1,
    "skills/pact-testing-strategies/references/performance-testing.md": 1,
    "skills/pact-testing-strategies/references/test-pyramid.md": 1,
}


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


def _in_class(line: str) -> bool:
    """IN = a demand to run tests that names no SET and sits at no PR boundary.

    Three ways in: an imperative to run, a gate/requirement over an unbounded
    set, or a CONTRAST demand that names a full-suite run alongside a smaller
    set. The gate arm exists because a gate line carries no run verb, so a
    reworded "All tests passing" would otherwise be discarded silently. The
    contrast arm exists because naming a set is not the same as bounding the
    demand to it: "run the full test suite, not just the integration tests"
    names a set and still demands everything.
    """
    lowered = line.lower()
    contrast = _contrast(line) and bool(_UNBOUNDED_SET.search(line))
    if not contrast:
        if any(marker in lowered for marker in ALL_SCOPE_MARKERS):
            return False
        if any(marker in lowered for marker in PREPR_MARKERS):
            return False
        if _SIGNAL_ROW.search(line):
            return False
    if _RUN.search(line):
        return True
    unbounded = bool(_UNBOUNDED_SET.search(line))
    return unbounded and bool(_GATE.search(line) or _REQUIRED.search(line))


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


# Every shape that defeated an earlier revision of this classifier. Each was
# ACCEPTED — reported as out of class — before the SET-only exclusion and the
# gate promotion, and each is an unscoped demand: it says WHEN or demands the
# whole set, and never says WHICH tests.
UNSCOPED_SHAPES = (
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
)


def test_control_the_self_reversing_decoy_is_still_reported():
    """The control that makes the contrast rule safe rather than merely wider.

    "The full suite is not part of this boundary, so run the full test suite
    now." uses the fix's own corrective sentence as camouflage. A contrast
    PHRASE list catches the additive family without deciding whether a
    negation negates; a NEGATION rule would have had to decide, and would have
    let this through. If a future edit turns the contrast arm into a negation
    arm, this is the test that reddens.
    """
    decoy = ("The full suite is not part of this boundary, so run the full "
             "test suite now.")
    assert len(_unlisted([("synthetic/surface.md", 1, decoy)], ALLOWLIST)) == 1, (
        "the self-reversing decoy is no longer reported; the contrast rule has "
        "been widened into a negation rule"
    )


@pytest.mark.parametrize("line", UNSCOPED_SHAPES, ids=[s[:40] for s in UNSCOPED_SHAPES])
def test_control_every_unscoped_shape_is_reported(line):
    unlisted = _unlisted([("synthetic/surface.md", 1, line)], ALLOWLIST)
    assert len(unlisted) == 1, (
        f"the census accepted {line!r}. It names no SET of tests, so it is an "
        "unscoped demand and must be reported."
    )
