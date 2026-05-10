"""
Structural pin tests for the Read-Trigger Precondition rule.

Asserts the EXACT marker substring "wait for teammate's wake-signal SendMessage"
is present at all 5 canonical doc-surface locations:

  1. pact-plugin/protocols/pact-completion-authority.md (SSOT)
  2. pact-plugin/protocols/pact-protocols.md (mirror enforced upstream by
     verify-protocol-extracts.sh)
  3. pact-plugin/agents/pact-orchestrator.md §12 (persona mirror)
  4. pact-plugin/skills/pact-teachback/SKILL.md (teammate-side audit comment)
  5. pact-plugin/skills/pact-agent-teams/SKILL.md (teammate-side audit comment)

The marker phrase is the load-bearing contract — it is what an LLM reading any
of the 5 surfaces at runtime must encounter to know the categorical rule (raw
metadata reads MUST follow the wake-signal SendMessage, not precede it). STRICT
phrasing pin (lead-decided): the assertions match the verbatim string. If a
future re-wording is intentional, update this test in lockstep so the rule
survives the re-word.

The cross-surface drift-detection test asserts the marker is consistent across
all 5 surfaces; if any one surface drifts (typo, paraphrase), the test fails.

The per-surface count test additionally pins the EXACT occurrence count per
surface. This catches a phantom-green-via-presence-not-count failure shape:
removing one of two callouts in a multi-mention surface (e.g., dropping the
load-bearing §11 inline rule from pact-orchestrator.md while keeping the §12
cross-ref) leaves substring-presence assertions GREEN but degrades the lazy-
load fidelity for an LLM reading only the section that lost the marker.
A future intentional count change requires updating EXPECTED_COUNTS in
lockstep — the brittleness IS the point.

Counter-test-by-revert (manual / runbook-documented): cp-bak each of the 5 doc
files, `git checkout HEAD~1 -- <paths>`, run this test module — expect
cardinality {5+ fail} (one presence-pin per surface + drift-detection +
count-pin per surface). Restore the .bak files. See
pact-plugin/tests/runbooks/wake-lifecycle-teachback-rearm.md for the full
procedure.
"""

from pathlib import Path

import pytest

# The lead-confirmed STRICT marker substring. Verbatim — do not paraphrase.
MARKER_PHRASE = "wait for teammate's wake-signal SendMessage"

# Canonical doc-surface set. Path objects are resolved relative to the
# pact-plugin/ repo root (which is the parent of the tests/ directory this
# file lives in). pact-protocols.md is included as the upstream-enforced
# mirror of pact-completion-authority.md (verify-protocol-extracts.sh keeps
# them lockstep); the structural pin matches the actual reader-facing
# surface set rather than relying solely on the upstream script.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

DOC_SURFACES = [
    PLUGIN_ROOT / "protocols" / "pact-completion-authority.md",
    PLUGIN_ROOT / "protocols" / "pact-protocols.md",
    PLUGIN_ROOT / "agents" / "pact-orchestrator.md",
    PLUGIN_ROOT / "skills" / "pact-teachback" / "SKILL.md",
    PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md",
]

# Per-surface expected occurrence count. The SSOT and its
# verify-protocol-extracts.sh-mirrored sibling each carry the rule twice
# (once for teachback-context inspection, once for HANDOFF-context
# inspection). The persona surface carries the rule twice (once in the
# Teachback Review inline callout, once in the Expected Agent HANDOFF Format
# callout). The two SKILL audit comments each carry the rule once. Update
# this map in lockstep with any intentional change to surface counts —
# the brittleness IS the point: it catches accidental cross-ref deletion.
EXPECTED_COUNTS = {
    PLUGIN_ROOT / "protocols" / "pact-completion-authority.md": 2,
    PLUGIN_ROOT / "protocols" / "pact-protocols.md": 2,
    PLUGIN_ROOT / "agents" / "pact-orchestrator.md": 2,
    PLUGIN_ROOT / "skills" / "pact-teachback" / "SKILL.md": 1,
    PLUGIN_ROOT / "skills" / "pact-agent-teams" / "SKILL.md": 1,
}


@pytest.mark.parametrize("doc_path", DOC_SURFACES, ids=lambda p: p.name)
def test_marker_substring_present_in_doc_surface(doc_path: Path):
    """For each of the 4 canonical doc surfaces, assert the marker
    substring is present.

    The marker phrase is the load-bearing contract at the LLM-reader layer:
    an agent reading any of these surfaces at runtime must encounter the
    rule that raw metadata reads MUST follow the wake-signal SendMessage,
    not precede it. Removing the phrase from any one surface creates a
    blind spot for agents loading only that surface (lazy-load fidelity
    failure mode per the agent-reader-primary axiom).

    Counter-test-by-revert: revert any one of the 4 doc files to its
    pre-fix state and the corresponding parametrize cell fails.
    """
    assert doc_path.exists(), (
        f"Doc surface missing on disk: {doc_path}. The Read-Trigger "
        f"Precondition rule must be discoverable at all 4 canonical "
        f"surfaces."
    )
    text = doc_path.read_text(encoding="utf-8")
    assert MARKER_PHRASE in text, (
        f"Marker substring {MARKER_PHRASE!r} missing from {doc_path.name}. "
        f"STRICT phrasing pin (lead-decided): if the wording was changed "
        f"intentionally, update MARKER_PHRASE here in lockstep. Otherwise, "
        f"the categorical rule is missing from this surface and an LLM "
        f"reading only this surface will infer no precondition."
    )


def test_marker_substring_consistent_across_all_surfaces():
    """Cross-surface drift-detection: assert the marker substring count
    is non-zero on every surface so the rule is discoverable everywhere.

    Distinct from the parametrized test above: this test fails with a
    single message naming all surfaces missing the phrase, making the
    cross-surface gap visible in one place rather than as a list of
    parametrize failures. Useful when a future devops dispatch updates
    only 3 of the 4 surfaces (e.g., the SSOT + 2 mirrors but forgets
    one SKILL audit comment) — the parametrized test reports 1 RED;
    this test additionally pins the WHY (drift across surfaces).

    Counter-test-by-revert: removing the phrase from any one surface
    causes this test to fail with a list naming the missing surface.
    """
    missing = [
        doc.name for doc in DOC_SURFACES if MARKER_PHRASE not in doc.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"Marker substring {MARKER_PHRASE!r} missing from {len(missing)} of "
        f"{len(DOC_SURFACES)} doc surfaces: {missing}. Cross-surface drift "
        f"detected — the categorical rule must be discoverable at all 5 "
        f"canonical sites (SSOT + protocols mirror + persona mirror + 2 "
        f"SKILL audit comments)."
    )


def test_ssot_anchor_present_in_completion_authority():
    """Anchor target test: pact-completion-authority.md MUST host the
    canonical "Read-Trigger Precondition" subsection so cross-refs from
    the persona + skills can target a stable anchor. Pins the H3 heading
    by line-anchored exact match (per memory feedback_603_blind2 phantom-
    green guard against substring matching for section-presence).
    """
    src = (PLUGIN_ROOT / "protocols" / "pact-completion-authority.md").read_text(encoding="utf-8")
    has_heading = any(
        line.strip() == "### Read-Trigger Precondition"
        for line in src.splitlines()
    )
    assert has_heading, (
        "pact-completion-authority.md must host the H3 heading "
        "'### Read-Trigger Precondition' (line-anchored exact match) so "
        "cross-refs from pact-orchestrator and skills can target the "
        "canonical anchor #read-trigger-precondition."
    )


def test_persona_cross_refs_to_ssot_anchor():
    """Cross-ref integrity: pact-orchestrator.md §12 must link to the
    SSOT anchor #read-trigger-precondition so the lazy-load reference
    resolves correctly when an agent follows the ref. Pin the literal
    anchor slug rather than relying on prose link text — the slug is
    what GitHub-flavored markdown actually navigates to.
    """
    src = (PLUGIN_ROOT / "agents" / "pact-orchestrator.md").read_text(encoding="utf-8")
    assert "#read-trigger-precondition" in src, (
        "pact-orchestrator.md §12 must include a markdown link to "
        "#read-trigger-precondition (the SSOT anchor in pact-completion-"
        "authority.md). Without this link, the lazy-load reference is a "
        "broken nav target and an agent following it lands on a 404."
    )


@pytest.mark.parametrize(
    "doc_path", DOC_SURFACES, ids=lambda p: p.name
)
def test_marker_phrase_count_per_surface(doc_path: Path):
    """Pin the EXACT marker-phrase occurrence count per surface.

    Distinct from test_marker_substring_present_in_doc_surface (which only
    asserts presence): this test catches the phantom-green-via-presence-not-
    count failure shape. A surface with two load-bearing callouts can lose
    one of them — leaving substring-presence assertions GREEN — while
    silently degrading lazy-load fidelity for an LLM reading only the
    section that lost the marker.

    Empirically validated by mutation probe: dropping one of two markers in
    pact-orchestrator.md (the §11 inline rule, leaving the §12 cross-ref-
    only mention) leaves all presence-pin tests GREEN; only this count-pin
    test catches the regression.

    Brittleness is the point. A future intentional count change requires
    updating EXPECTED_COUNTS in lockstep — the failure surfaces the
    discrepancy in code review rather than letting silent erosion
    accumulate.
    """
    expected = EXPECTED_COUNTS[doc_path]
    text = doc_path.read_text(encoding="utf-8")
    actual = text.count(MARKER_PHRASE)
    assert actual == expected, (
        f"{doc_path.name}: marker phrase {MARKER_PHRASE!r} appears "
        f"{actual} time(s); expected exactly {expected}. If this change "
        f"is intentional (e.g., a deliberate consolidation removing a "
        f"redundant callout, or a deliberate addition adding a new "
        f"reader-facing section that needs the rule), update "
        f"EXPECTED_COUNTS in this file in lockstep. Otherwise the "
        f"discrepancy is silent erosion of the Read-Trigger Precondition "
        f"rule's coverage on this surface."
    )
