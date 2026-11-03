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

WHAT THIS MODULE CANNOT DO
    It verifies TEXT. Whether a coder or lead now behaves differently is not
    observable from a static read, and no assertion here should be read as
    evidence of a behaviour change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]

SURFACE_GLOBS = (
    "commands/*.md",
    "agents/*.md",
    "skills/*/SKILL.md",
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


def _section(text: str, anchor: str, end: str | None) -> str:
    """The text from the single occurrence of `anchor` to `end` (or EOF).

    An anchor that is missing or REPEATED fails. A duplicated anchor would
    otherwise let the pin read a different section while believing it read
    this one: "**Before next phase**" occurs three times in orchestrate.md
    (PREPARE, ARCHITECT, CODE), so anchoring a CODE-phase assertion on it
    alone would silently check the PREPARE checklist.
    """
    count = text.count(anchor)
    assert count == 1, f"anchor {anchor!r} occurs {count} times, expected 1"
    start = text.index(anchor)
    if end is None:
        return text[start:]
    stop = text.find(end, start + len(anchor))
    assert stop != -1, f"end marker {end!r} not found after {anchor!r}"
    return text[start:stop]


def _line_with(text: str, marker: str) -> str:
    """The single line containing `marker`. Same uniqueness contract as _section."""
    hits = [line for line in text.splitlines() if marker in line]
    assert len(hits) == 1, f"{marker!r} occurs on {len(hits)} lines, expected 1"
    return hits[0]


# ---------------------------------------------------------------------------
# Part (a): per-site positive pins, and the removal pins
# ---------------------------------------------------------------------------

# (file, section anchor, end marker, phrases that must appear inside the section)
SITE_PINS = (
    (
        "commands/orchestrate.md",
        "### CODE Phase → `pact-*-coder(s)`",
        "### ATOMIZE Phase (Scoped Orchestration Only)",
        (
            # orchestrate.md:780 — the coder GUIDELINES dispatch text.
            "Smoke Testing: Before each stage-ready report (the commit boundary), "
            "run the tests your change affects",
            "The full suite is not part of this boundary.",
            # orchestrate.md:856 — the CODE-phase boundary checklist.
            "Scoped test run green at every commit boundary",
            "the full suite is not a per-commit gate",
        ),
    ),
    (
        "commands/orchestrate.md",
        "### TEST Phase → `pact-test-engineer`",
        "## Agent Stall Detection",
        (
            # orchestrate.md:990 — the single pre-PR full run.
            "Full test suite run once — the single pre-PR full run",
        ),
    ),
)

# (file, line marker, phrases that must appear on that line)
LINE_PINS = (
    (
        "commands/comPACT.md",
        "comPACT mode (concurrent):",
        (
            "Before HANDOFF, run the tests your change affects",
            "The full suite is not part of this boundary.",
        ),
    ),
    (
        "commands/comPACT.md",
        "comPACT mode: Work directly",
        (
            "Before HANDOFF, run the tests your change affects",
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
    "rel,anchor,end,phrases",
    SITE_PINS,
    ids=[f"{rel.split('/')[-1]}:{anchor[:28]}" for rel, anchor, _, _ in SITE_PINS],
)
def test_the_scoped_wording_is_in_its_section(rel, anchor, end, phrases):
    body = _phrase(_section(_text(rel), anchor, end))
    for phrase in phrases:
        assert _phrase(phrase) in body, (
            f"{rel}: {phrase!r} is missing from the {anchor!r} section. This is the "
            "scoped test-run instruction; if the wording changed on purpose, update "
            "this pin with it."
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
_GATE = re.compile(r"(?i)\btests?\s+(?:are\s+)?(?:green|pass(?:ing)?)\b")
_SUITE = re.compile(r"(?i)\b(?:full|test)[\s-]suite\b")

CANDIDATE_PATTERNS = (_RUN, _RUN_BARE, _GATE, _SUITE)

# A candidate NAMES ITS SCOPE — or sits at the already-correct pre-PR boundary —
# and is therefore out of the class. Scope markers are either drawn from the
# fixed instruction text itself or are pre-existing scope-naming phrases; the
# test below enforces that, so a marker cannot be invented ad hoc to swallow a
# site that should have been allowlisted visibly.
SCOPE_MARKERS = (
    "the tests your change affects",
    "the tests the work affects",
    "the tests the change affects",
    "plus any suite subsets the plan names",
    "the smallest suite that contains",
    "test files that cover the modules",
    "commit boundary",
    "stage-ready report",
    "run the full suite once, as a whole",
    "single pre-pr full run",
    "pr/project gate",
    "not part of this boundary",
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

PREPR_MARKERS = (
    "before creating pr",
    "pre-pr",
    "before pr",
    "at the pr boundary",
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
)

# Measured on the fixed tree. The count is a floor, not a proof: a new candidate
# changes it, and that is the signal. A legitimate new line is either scoped (so
# it belongs in SCOPE_MARKERS' shape) or an explicit allowlist entry — in both
# cases this number moves and the operator decides, rather than the new site
# arriving silently.
BASELINE_CANDIDATES = 38


def _surfaces() -> list[Path]:
    found: list[Path] = []
    for pattern in SURFACE_GLOBS:
        matches = sorted(PLUGIN.glob(pattern))
        assert matches, f"surface glob {pattern!r} matched no files"
        found.extend(matches)
    return sorted(set(found))


def _candidates(surfaces):
    """Yield (relative path, line number, line) for every candidate line."""
    for path in surfaces:
        rel = path.relative_to(PLUGIN).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in CANDIDATE_PATTERNS):
                yield rel, lineno, line


def _in_class(line: str) -> bool:
    """IN = an imperative to run tests that names no scope and no PR boundary."""
    lowered = line.lower()
    if any(marker in lowered for marker in ALL_SCOPE_MARKERS):
        return False
    if any(marker in lowered for marker in PREPR_MARKERS):
        return False
    return bool(_RUN.search(line))


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
    assert len(candidates) == BASELINE_CANDIDATES, (
        f"candidate lines: {len(candidates)}, baseline {BASELINE_CANDIDATES}. "
        "If the new line is scoped, extend the scope markers; if it is legitimately "
        "exempt, add an allowlist entry with a reason; then update the baseline. "
        "Do not update the baseline on its own."
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
