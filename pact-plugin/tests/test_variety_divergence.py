"""
GC-immune regression suite for the wrap-up Q5 coverage denominator + the
coverage-exceeds-unity tripwire (epic #972, children #971/#963).

This file is the dedicated home for the two pure helpers in
shared/variety_divergence.py that back the §4 Orchestration Retrospective
Q5 (variety divergence):

  - count_task_b_dispatch_sites(agent_dispatch, review_dispatch, remediation)
    — the Q5 coverage DENOMINATOR, sourced from variety-INDEPENDENT journal
    markers so an un-stamped dispatch still counts and rightly lowers
    coverage. Pre-#971 the denominator was len(agent_dispatch), which
    undercounted because peer-review emits review_dispatch (not
    agent_dispatch) and remediation emits remediation (not agent_dispatch)
    → coverage could exceed 1.0 (6/2 = 3.0 in the #958/PR #970 single-arc
    session). This helper restores the coupled-pair invariant.

  - the coverage_exceeds_unity early-return in compute_variety_divergence —
    a non-clamping advisory tripwire (#971): when stamped > total it returns
    reason="coverage_exceeds_unity", surfaced=False, coverage left UNCLAMPED.
    Zero-residual with the distinct-site denominator, so it fires only on a
    future denominator/emit regression.

Non-vacuity: count_task_b_dispatch_sites is a NET-NEW symbol, so a
source-only revert removes it entirely (ImportError / collection error, not
a clean assertion fail). The PRIMARY non-vacuity proof here is therefore a
PAIRED intact/neutered test — coverage computed BOTH via the new helper and
via the old len(agent_dispatch), asserted mutually-exclusive across the 1.0
boundary — a standing CI guard that fails the instant the denominator source
regresses. Plus branch-coupled mutation contrasts for the A-medium dedup.

GC-immune: every fixture is a synthetic journal event built via
session_journal.make_event — zero dependence on the GC-reaped task store.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.session_journal import make_event  # noqa: E402
from shared.variety_divergence import (  # noqa: E402
    compute_variety_divergence,
    count_task_b_dispatch_sites,
)


# =============================================================================
# Fixture builders — faithful journal-event shapes (see
# _REQUIRED_FIELDS_BY_TYPE / _OPTIONAL_FIELDS_BY_TYPE in session_journal.py)
# =============================================================================

_TS = "2026-06-15T12:00:00Z"


def _agent_dispatch(task_id, agent="coder", phase="CODE", ts=_TS):
    return make_event(
        "agent_dispatch", agent=agent, task_id=task_id, phase=phase, ts=ts
    )


def _review_dispatch(reviewers, pr_number=970, ts=_TS):
    return make_event(
        "review_dispatch",
        pr_number=pr_number,
        pr_url=f"https://github.com/o/r/pull/{pr_number}",
        reviewers=list(reviewers),
        ts=ts,
    )


def _remediation(task_id=None, cycle=1, fixer="coder", ts=_TS):
    fields = {"cycle": cycle, "items": ["F1"], "fixer": fixer, "ts": ts}
    if task_id is not None:
        fields["task_id"] = task_id
    return make_event("remediation", **fields)


# The #958 / PR #970 single-arc fixture (the worked example in issue #971).
def _f971_markers():
    """The three Q5 denominator markers for the single-arc PR-#970 session.

      agent_dispatch : task8 (CODE), task11 (TEST)              -> 2 sites
      review_dispatch: reviewers [14, 15, 16]                   -> 3 sites
      remediation    : task20 (reuse, un-stamped), task21       -> 2 sites
                       (neither 20 nor 21 is an agent_dispatch task_id)
    denominator = 2 + 3 + 2 = 7
    """
    agent = [_agent_dispatch("8", phase="CODE"), _agent_dispatch("11", phase="TEST")]
    review = [_review_dispatch(["14", "15", "16"])]
    remediation = [_remediation(task_id="20"), _remediation(task_id="21")]
    return agent, review, remediation


# Numerator: dispatch_variety totals for the SIX stamped dispatches
# (task20 is un-stamped, so it is absent from the variety stream).
_F971_STAMPED_TOTALS = [6, 8, 8, 8, 7, 7]  # task8, 11, 14, 15, 16, 21


# =============================================================================
# count_task_b_dispatch_sites — the Q5 coverage denominator (#971)
# =============================================================================


class TestCoverageDenominator:
    """count_task_b_dispatch_sites: distinct Task-B dispatch sites."""

    def test_single_arc_denominator_counts_review_and_remediation_sites(self):
        """Single-arc PR session: the denominator counts variety-INDEPENDENT
        dispatch sites, so peer-review reviewers and remediation fixers are
        counted (not just orchestrate coders) and the un-stamped reuse
        dispatch still counts. (#971 worked example → 2 + 3 + 2 = 7.)"""
        agent, review, remediation = _f971_markers()
        assert count_task_b_dispatch_sites(agent, review, remediation) == 7

    def test_single_arc_coverage_is_below_unity(self):
        """The #971 regression killed: NEW coverage = 6 stamped / 7 sites
        ≈ 0.857 ≤ 1.0 (the old len(agent_dispatch)=2 path gave 6/2 = 3.0)."""
        agent, review, remediation = _f971_markers()
        denom = count_task_b_dispatch_sites(agent, review, remediation)
        result = compute_variety_divergence(
            feature_variety=6,
            dispatch_varieties=_F971_STAMPED_TOTALS,
            total_pact_dispatch_count=denom,
        )
        assert result["coverage"] == pytest.approx(6 / 7)
        assert result["coverage"] <= 1.0

    def test_new_denominator_crosses_unity_boundary_old_did_not(self):
        """PRIMARY non-vacuity (paired intact/neutered, standing CI guard).

        Computes coverage BOTH ways over the SAME single-arc fixture:
          NEW: count_task_b_dispatch_sites(...) = 7 → 6/7 ≈ 0.857 ≤ 1.0
          OLD: len(agent_dispatch)            = 2 → 6/2 = 3.0   > 1.0  (the bug)
        Mutually exclusive across the 1.0 boundary → this guard fails the
        instant the denominator source regresses back to len(agent_dispatch).
        Stronger than a source-only revert for a net-new symbol (which would
        remove the function → collection error, not a clean assertion fail).
        """
        agent, review, remediation = _f971_markers()
        stamped = _F971_STAMPED_TOTALS

        new_denom = count_task_b_dispatch_sites(agent, review, remediation)
        old_denom = len(agent)  # the pre-#971 denominator source

        new_cov = compute_variety_divergence(6, stamped, new_denom)["coverage"]
        old_cov = compute_variety_divergence(6, stamped, old_denom)["coverage"]

        # intact: the fixed denominator keeps coverage a valid fraction
        assert new_denom == 7
        assert new_cov == pytest.approx(6 / 7)
        assert new_cov <= 1.0
        # neutered: the old denominator produces the nonsensical > 1.0 coverage
        assert old_denom == 2
        assert old_cov == pytest.approx(3.0)
        assert old_cov > 1.0

    def test_orchestrate_only_session_denominator_unchanged(self):
        """No-change guard: an orchestrate-only session (CODE+TEST, no
        peer-review/remediation) has no review/remediation sites, so the new
        denominator equals len(agent_dispatch) — the path that already worked
        is unchanged (no regression)."""
        agent = [_agent_dispatch("8"), _agent_dispatch("11")]
        denom = count_task_b_dispatch_sites(agent, [], [])
        assert denom == len(agent) == 2
        result = compute_variety_divergence(8, [8, 8], denom)
        assert result["coverage"] == pytest.approx(1.0)

    def test_reviewers_counted_by_length_not_identity(self):
        """review_dispatch contributes len(reviewers) sites; A-medium did NOT
        add reviewer_task_ids, so the reviewer identifiers are counted by
        list length only and are disjoint from agent_dispatch by emit-site
        design (no dedup against reviewers)."""
        agent = [_agent_dispatch("8")]
        review = [_review_dispatch(["r1", "r2", "r3", "r4"])]
        assert count_task_b_dispatch_sites(agent, review, []) == 1 + 4

    def test_multiple_review_dispatch_events_sum_their_reviewers(self):
        """Σ len(review_dispatch[i].reviewers) across multiple review events."""
        review = [_review_dispatch(["a", "b"]), _review_dispatch(["c", "d", "e"])]
        assert count_task_b_dispatch_sites([], review, []) == 5

    def test_empty_inputs_yield_zero(self):
        """Fail-open shape: all-empty marker lists → 0 sites."""
        assert count_task_b_dispatch_sites([], [], []) == 0


class TestRemediationDedup:
    """A-medium remediation/agent_dispatch task_id dedup (#971, auditor anchor)."""

    def test_compact_remediation_sharing_agent_task_id_counted_once(self):
        """A comPACT/orchestrate remediation emits BOTH `remediation` AND
        `agent_dispatch` for the same task_id → counted ONCE (via the
        agent_dispatch stream)."""
        agent = [_agent_dispatch("8"), _agent_dispatch("30")]
        remediation = [_remediation(task_id="30")]  # collides with agent task30
        # 2 agent + 0 reviewers + 0 (task30 ∈ agent ids → excluded) = 2
        assert count_task_b_dispatch_sites(agent, [], remediation) == 2

    def test_pure_reuse_remediation_without_agent_dispatch_is_counted(self):
        """A pure reuse-remediation (no matching agent_dispatch) IS counted
        via the remediation stream."""
        agent = [_agent_dispatch("8")]
        remediation = [_remediation(task_id="20")]  # ∉ agent ids
        assert count_task_b_dispatch_sites(agent, [], remediation) == 2

    def test_idless_remediation_is_counted_failsafe(self):
        """A remediation with NO task_id is counted (fail-safe: never
        undercount, so a dropped id can't inflate coverage above 1.0)."""
        agent = [_agent_dispatch("8")]
        remediation = [_remediation(task_id=None)]
        assert count_task_b_dispatch_sites(agent, [], remediation) == 2

    def test_dedup_is_load_bearing_versus_naive_count(self):
        """Non-vacuity for the dedup branch: dropping the `task_id ∉
        agent_task_ids` filter would double-count the colliding remediation.
        Proven by contrast with the naive no-dedup count over the same
        fixture (deduped 2 < naive 3)."""
        agent = [_agent_dispatch("8"), _agent_dispatch("30")]
        remediation = [_remediation(task_id="30")]
        deduped = count_task_b_dispatch_sites(agent, [], remediation)
        naive_no_dedup = len(agent) + len(remediation)  # un-filtered logic
        assert deduped == 2
        assert naive_no_dedup == 3
        assert deduped < naive_no_dedup


# =============================================================================
# coverage_exceeds_unity advisory tripwire (#971)
# =============================================================================


class TestCoverageExceedsUnityAdvisory:
    """The non-clamping coverage>1.0 tripwire in compute_variety_divergence."""

    def test_advisory_fires_when_stamped_exceeds_total(self):
        """Synthetic stamped > total (zero-residual under the real denominator,
        so it is exercised directly): reason set, surfaced=False, coverage
        left UNCLAMPED so the anomaly is visible."""
        result = compute_variety_divergence(
            feature_variety=8,
            dispatch_varieties=[8, 8, 8],  # stamped = 3
            total_pact_dispatch_count=2,  # total = 2 < stamped
        )
        assert result["reason"] == "coverage_exceeds_unity"
        assert result["coverage"] == pytest.approx(1.5)  # 3/2, UNCLAMPED
        assert result["coverage"] > 1.0
        assert result["surfaced"] is False

    def test_advisory_does_not_clamp_to_unity(self):
        """Explicit: the advisory does NOT clamp coverage to 1.0 — a clamp
        would HIDE the very denominator regression this tripwire exists to
        catch."""
        result = compute_variety_divergence(4, [12, 12], 1)  # stamped 2 > total 1
        assert result["reason"] == "coverage_exceeds_unity"
        assert result["coverage"] == pytest.approx(2.0)
        assert result["coverage"] != 1.0

    def test_advisory_fires_even_when_feature_variety_missing(self):
        """The tripwire precedes the feature_variety_missing fail-open, so it
        fires (delta=None) even with no feature variety — the broken
        denominator is surfaced regardless."""
        result = compute_variety_divergence(None, [8, 8, 8], 2)
        assert result["reason"] == "coverage_exceeds_unity"
        assert result["delta"] is None
        assert result["surfaced"] is False

    def test_real_denominator_is_zero_residual(self):
        """Complement: over a REAL count_task_b_dispatch_sites denominator,
        stamped can never exceed total (un-stamped dispatches still count), so
        the advisory is zero-residual. The #971 fixture: 6 stamped ≤ 7 sites."""
        agent, review, remediation = _f971_markers()
        denom = count_task_b_dispatch_sites(agent, review, remediation)
        result = compute_variety_divergence(6, _F971_STAMPED_TOTALS, denom)
        assert result["reason"] != "coverage_exceeds_unity"
        assert result["coverage"] <= 1.0
