"""Structural pin for the PACT_PR_GREEDY_FIX greedy remediation path in
commands/peer-review.md.

The greedy path's correctness rests on prose that (a) references the injected
"PACT Runtime Config" block BY NAME, (b) scopes greedy to STRICTLY REVERSIBLE
auto-delegation of fixes while keeping merge/close/push behind explicit user
approval, (c) carries an explicit HARD SACROSANCT carve-out, and (d) SURFACES
guardrail-excluded findings instead of silently dropping them. That discipline
lives only in prose; a future edit could silently weaken it, and the resulting
behavior (greedy auto-authorizing an irreversible action, or dropping an
exclusion) is exactly what the pre-build reversibility probes proved unsafe.
These guards make that regression loud.

Keyed on STABLE anchors + semantic token co-occurrence within the greedy
section only (not exact prose), so benign rewording survives but dropping a
load-bearing clause fails.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PEER_REVIEW = PLUGIN_ROOT / "commands" / "peer-review.md"


def _norm(text: str) -> str:
    """Lowercase + collapse whitespace so token checks survive line-wrapping."""
    return re.sub(r"\s+", " ", text.lower())


def _full() -> str:
    return PEER_REVIEW.read_text(encoding="utf-8")


def _greedy_section() -> str:
    """The greedy branch only — from the GREEDY-CONFIG-REF anchor to the Step A
    header it preempts — so a token living elsewhere in peer-review.md cannot
    satisfy the co-occurrence asserts."""
    text = _full()
    start = text.find("ANCHOR-STABLE: GREEDY-CONFIG-REF")
    assert start != -1, "peer-review.md lost the GREEDY-CONFIG-REF anchor"
    end = text.find("Initial Gate Question", start)
    assert end != -1, "peer-review.md greedy section: Step A boundary not found"
    return _norm(text[start:end])


class TestGreedyAnchorsPresent:
    """Each load-bearing clause carries a stable anchor; removing the clause
    removes its anchor and reds this test."""

    @pytest.mark.parametrize("anchor", [
        "ANCHOR-STABLE: GREEDY-CONFIG-REF",
        "ANCHOR-STABLE: GREEDY-PATH",
        "ANCHOR-STABLE: GREEDY-REVERSIBILITY",
        "ANCHOR-STABLE: GREEDY-SACROSANCT",
    ])
    def test_anchor_present(self, anchor):
        assert anchor in _full(), f"peer-review.md lost the {anchor} clause"


class TestGreedyConfigReference:
    def test_references_injected_block_by_name(self):
        sec = _greedy_section()
        assert "pact runtime config" in sec, (
            "greedy path no longer references the injected PACT Runtime Config block by name"
        )
        assert "pr greedy-fix" in sec, (
            "greedy path no longer keys on the PR greedy-fix option"
        )

    def test_off_or_absent_falls_back_to_default_gate(self):
        sec = _greedy_section()
        assert "absent" in sec and "off" in sec, (
            "greedy path no longer states OFF/absent == default gate"
        )
        assert "unchanged" in sec, (
            "greedy path no longer states the default gate is UNCHANGED for non-opted-in consumers"
        )


class TestGreedyReversibilityConstraint:
    """The single most important correctness property: greedy auto-delegates
    ONLY reversible fixes; every irreversible action stays user-gated."""

    def test_scopes_to_reversible_delegation(self):
        assert "reversible" in _greedy_section(), (
            "greedy path lost the reversible-scoping constraint"
        )

    def test_merge_close_push_stay_user_gated(self):
        sec = _greedy_section()
        for token in ("merge", "close", "push"):
            assert token in sec, (
                f"greedy path no longer names '{token}' among the user-gated irreversible actions"
            )
        assert ("user-gated" in sec) or ("user still approves" in sec) or ("never merges" in sec), (
            "greedy path no longer keeps irreversible actions behind explicit user approval"
        )

    def test_hard_sacrosanct_carveout(self):
        assert "sacrosanct" in _greedy_section(), (
            "greedy path lost the hard SACROSANCT carve-out"
        )


class TestGreedyExclusionSurfacing:
    def test_exclusions_surfaced_not_dropped(self):
        sec = _greedy_section()
        assert "end-of-run summary" in sec, (
            "greedy path no longer surfaces exclusions as an end-of-run summary"
        )
        assert "do not" in sec, (
            "greedy path no longer forbids silently dropping / per-finding-prompting exclusions"
        )
        assert "out of scope" in sec, (
            "greedy path no longer names the out-of-scope exclusion class"
        )
