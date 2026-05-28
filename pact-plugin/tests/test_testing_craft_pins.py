"""
Structural pins for the §9 `Testing Craft Patterns` cluster in
`pact-plugin/skills/pact-testing-strategies/SKILL.md`.

Each pin guards a specific silent-drift surface that the planning-artifact
scrub test and other existing structural tests do NOT cover:

  - M1: the 3 pact-memory IDs (d319e8e1, 0bc2c78d, f3f3d093) must be present
        AND each must live under its expected named subsection within §9.1.
        Catches: future PR silently removes or moves a citation. Existing
        scrub test catches SHA-looking-without-marker; this pin catches
        SHA-looking-without-presence and SHA-looking-at-wrong-surface.

  - M2: at least one `planning-artifact-exempt:` marker must contain BOTH
        the substrings `pact-memory` AND `content-addressable`. Catches:
        future PR drifts the marker reason text to a misleading wording
        (e.g., "old commit hash from refactor") while keeping a marker
        present. Pins the convention's reason-text shape.

  - M3: any `see X above` prose cross-link inside the §9 cluster must point
        at an earlier `### ` / `#### ` heading whose name contains X.
        Catches: future PR renames §9.1 heading or reorders §9.x
        sub-sections, silently rotting the prose cross-link.

  - M4: the §9 opener prose contains a cardinality count-word
        (`one|two|three|four|five|six|seven|eight|nine|ten`) that names
        the number of §9.x sub-sections. The count-word and the §9.x
        sub-heading count must agree. Catches: future PR adds §9.4 (or
        removes §9.3) without updating the opener's count-word.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


PACT_PLUGIN_ROOT = Path(__file__).parent.parent
SKILL_PATH = (
    PACT_PLUGIN_ROOT / "skills" / "pact-testing-strategies" / "SKILL.md"
)

# Cluster boundary anchors. The §9 cluster starts at the `## Testing Craft
# Patterns` H2 heading and ends at the next `## ` H2 heading.
CLUSTER_OPEN = "## Testing Craft Patterns"
CLUSTER_NEXT_H2 = re.compile(r"^## (?!#)", re.MULTILINE)


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _cluster_lines() -> list[str]:
    """Return the lines of the §9 `Testing Craft Patterns` cluster, from
    the cluster-opening H2 heading through (but not including) the next
    H2 heading."""
    text = _read_skill()
    start = text.index(CLUSTER_OPEN)
    rest = text[start:]
    # Find the next H2 AFTER the cluster-opening H2 itself.
    next_h2_match = CLUSTER_NEXT_H2.search(rest, pos=len(CLUSTER_OPEN))
    end = next_h2_match.start() if next_h2_match else len(rest)
    return rest[:end].splitlines()


# ─── M1: pact-memory ID presence + surface ────────────────────────────────

ID_TO_EXPECTED_SUBSECTION = {
    "d319e8e1": "### Author-blindness in HANDOFF arithmetic",
    "0bc2c78d": "#### Discriminator vs the ASPIRATIONAL-HANDOFF sister pattern",
    "f3f3d093": "#### Canonical mitigation",
}


def _subsection_containing(lines: list[str], target_idx: int) -> str:
    """Return the nearest preceding `### ` or `#### ` heading line for the
    line at `target_idx`. The cluster's top-level H2 (`## Testing Craft
    Patterns`) does NOT count — we want the granular subsection."""
    for i in range(target_idx, -1, -1):
        line = lines[i].rstrip()
        if line.startswith("#### ") or line.startswith("### "):
            return line
    return ""


def test_m1_pact_memory_ids_present_at_named_subsections():
    """Each of the 3 pact-memory IDs MUST be present in SKILL.md AND live
    under its expected `###` / `####` subsection within §9.1.

    Surface check uses a "softer" semantic: the ID must appear somewhere
    BEFORE the next sibling heading transitions out of its expected
    subsection. This is reordering-resistant within the subsection (the
    ID can move a line up or down) but catches relocation to a different
    subsection entirely.
    """
    lines = _cluster_lines()
    missing: list[str] = []
    misplaced: list[tuple[str, str, str]] = []

    for memory_id, expected_subsection in ID_TO_EXPECTED_SUBSECTION.items():
        hit_idx: int | None = None
        for idx, line in enumerate(lines):
            if memory_id in line:
                hit_idx = idx
                break
        if hit_idx is None:
            missing.append(memory_id)
            continue
        actual_subsection = _subsection_containing(lines, hit_idx)
        if actual_subsection != expected_subsection:
            misplaced.append((memory_id, expected_subsection, actual_subsection))

    if missing:
        pytest.fail(
            f"pact-memory ID(s) missing from §9 cluster: {missing}. "
            f"Each of d319e8e1, 0bc2c78d, f3f3d093 must be cited inline "
            f"at point-of-relevance per CLAUDE.md LLM-load distinction pin."
        )
    if misplaced:
        rows = "\n  ".join(
            f"{mid}: expected under `{exp}`, actually under `{act}`"
            for mid, exp, act in misplaced
        )
        pytest.fail(
            f"pact-memory ID(s) found at wrong surface:\n  {rows}\n"
            f"Move the citation back to its expected subsection or update "
            f"this test's ID_TO_EXPECTED_SUBSECTION mapping with rationale."
        )


# ─── M2: exempt-marker wording pins the pact-memory convention ────────────

_EXEMPT_MARKER_LINE = re.compile(
    r"<!--\s*planning-artifact-exempt:\s*([^>]*?)\s*-->"
)


def test_m2_at_least_one_exempt_marker_pins_pact_memory_convention():
    """At least one `planning-artifact-exempt:` marker in the §9 cluster
    MUST contain BOTH the substrings `pact-memory` AND `content-addressable`
    in its reason text. Pins the marker's reason-text convention against
    future drift to misleading wording (e.g., \"old commit hash\")."""
    cluster_text = "\n".join(_cluster_lines())
    markers = _EXEMPT_MARKER_LINE.findall(cluster_text)
    if not markers:
        pytest.fail(
            "No `planning-artifact-exempt:` markers found in §9 cluster. "
            "The 3 pact-memory citations need preceding-line exempt markers "
            "to silence the SHA-looking structural-scrub regex."
        )
    qualifying = [
        m for m in markers
        if "pact-memory" in m and "content-addressable" in m
    ]
    if not qualifying:
        pytest.fail(
            "No `planning-artifact-exempt:` marker in §9 cluster names "
            "the pact-memory convention (must contain BOTH `pact-memory` "
            "AND `content-addressable` substrings). Found markers:\n  "
            + "\n  ".join(repr(m) for m in markers)
            + "\nRestore the convention's reason-text per "
            "`skills/pact-testing-strategies/SKILL.md` exempt-marker shape."
        )


# ─── M3: `see X above` cross-link anchor-resistance ───────────────────────

_SEE_X_ABOVE = re.compile(r"see (?P<anchor>[A-Z][\w-]*(?:\s+[\w-]+){0,3})\s+above")


def test_m3_see_x_above_prose_links_resolve_to_earlier_headings():
    """Any `see X above` prose cross-link inside the §9 cluster MUST point
    at an earlier `###` / `####` heading whose text contains X (case-
    sensitive substring). Catches future PR renaming §9.1's heading or
    reordering §9.x sub-sections without updating the cross-link."""
    lines = _cluster_lines()
    findings: list[str] = []

    for idx, line in enumerate(lines):
        for m in _SEE_X_ABOVE.finditer(line):
            anchor = m.group("anchor")
            # Search BEFORE this line for any heading containing the anchor.
            resolved = False
            for j in range(idx - 1, -1, -1):
                prior = lines[j].rstrip()
                if (
                    prior.startswith("### ")
                    or prior.startswith("#### ")
                    or prior.startswith("## ")
                ) and anchor in prior:
                    resolved = True
                    break
            if not resolved:
                findings.append(
                    f"Line {idx + 1}: `see {anchor} above` — no earlier "
                    f"heading in §9 cluster contains substring `{anchor}`"
                )

    if findings:
        pytest.fail(
            "§9 cluster cross-link anchor resolution failed:\n  "
            + "\n  ".join(findings)
            + "\nRename the cross-link to match the new heading text, or "
            "restore the heading text the cross-link expects."
        )


# ─── M4: §9 opener count-word matches §9.x sub-heading cardinality ────────

_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_COUNT_WORD_RE = re.compile(
    r"\ball (?P<word>"
    + "|".join(_COUNT_WORDS.keys())
    + r")\b"
)


def test_m4_opener_count_word_matches_subsection_cardinality():
    """The §9 opener prose contains an `all <count-word>` phrase naming
    the number of §9.x sub-sections. The count-word and the actual `###`
    sub-heading count must agree. Catches future PR adding or removing
    a §9.x sub-section without updating the opener's count-word."""
    lines = _cluster_lines()

    # Opener prose is the lines AFTER the cluster's H2 and BEFORE the
    # first `### ` sub-heading.
    opener_lines: list[str] = []
    for line in lines[1:]:  # skip the `## Testing Craft Patterns` line
        if line.startswith("### "):
            break
        opener_lines.append(line)
    opener_text = " ".join(opener_lines)

    match = _COUNT_WORD_RE.search(opener_text)
    if not match:
        pytest.fail(
            "§9 opener prose has no `all <count-word>` phrase. The opener "
            "should name the cluster's pattern cardinality (e.g., `all "
            "three independently`) so M4 can pin opener-vs-subsection "
            "drift. Restore the count-word or update this test."
        )
    declared_count = _COUNT_WORDS[match.group("word")]

    actual_count = sum(1 for line in lines if line.startswith("### "))

    if declared_count != actual_count:
        pytest.fail(
            f"§9 opener claims `all {match.group('word')}` "
            f"({declared_count} sub-patterns) but the cluster has "
            f"{actual_count} `### ` sub-headings. Update the opener's "
            f"count-word to match the current cluster cardinality."
        )
