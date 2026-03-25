"""
Fuzz and property-based tests for hooks/shared/variety_scorer.py.

Supplements test_variety_scorer.py with:
1. Exhaustive parametrized tests for all 256 input combinations (4^4)
2. Property tests: monotonicity, no tier skipping, valid range invariant
3. End-to-end scoring pipeline (score -> route consistency)

Uses hypothesis if available, parametrized fallback if not.
"""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.variety_scorer import (
    COMPACT_MAX,
    MAX_DIMENSION,
    MAX_SCORE,
    MIN_DIMENSION,
    MIN_SCORE,
    ORCHESTRATE_MAX,
    PLAN_MODE_MAX,
    ROUTE_COMPACT,
    ROUTE_ORCHESTRATE,
    ROUTE_PLAN_MODE,
    ROUTE_RESEARCH_SPIKE,
    route_workflow,
    score_variety,
)

# All valid dimension values
ALL_DIMS = list(range(MIN_DIMENSION, MAX_DIMENSION + 1))  # [1, 2, 3, 4]

# All 256 combinations
ALL_COMBINATIONS = list(itertools.product(ALL_DIMS, repeat=4))

# Workflow tiers for ordering checks
TIER_ORDER = [ROUTE_COMPACT, ROUTE_ORCHESTRATE, ROUTE_PLAN_MODE, ROUTE_RESEARCH_SPIKE]
TIER_INDEX = {name: idx for idx, name in enumerate(TIER_ORDER)}


# =============================================================================
# Exhaustive: all 256 combinations produce valid results
# =============================================================================


class TestExhaustiveCombinations:
    """Exhaustive parametrized test for all 256 (4^4) dimension combos."""

    @pytest.mark.parametrize(
        "novelty,scope,uncertainty,risk",
        ALL_COMBINATIONS,
    )
    def test_score_in_valid_range(self, novelty, scope, uncertainty, risk):
        """Every valid input combination produces a score in [4, 16]."""
        score = score_variety(novelty, scope, uncertainty, risk)
        assert MIN_SCORE <= score <= MAX_SCORE, (
            f"score_variety({novelty},{scope},{uncertainty},{risk}) = {score} "
            f"outside [{MIN_SCORE}, {MAX_SCORE}]"
        )

    @pytest.mark.parametrize(
        "novelty,scope,uncertainty,risk",
        ALL_COMBINATIONS,
    )
    def test_route_returns_valid_workflow(self, novelty, scope, uncertainty, risk):
        """Every valid score maps to a recognized workflow."""
        score = score_variety(novelty, scope, uncertainty, risk)
        route = route_workflow(score)
        assert route in TIER_ORDER, (
            f"route_workflow({score}) = '{route}' not in {TIER_ORDER}"
        )

    @pytest.mark.parametrize(
        "novelty,scope,uncertainty,risk",
        ALL_COMBINATIONS,
    )
    def test_score_equals_dimension_sum(self, novelty, scope, uncertainty, risk):
        """Score is always the arithmetic sum of dimensions."""
        score = score_variety(novelty, scope, uncertainty, risk)
        assert score == novelty + scope + uncertainty + risk


# =============================================================================
# Property: monotonicity
# =============================================================================


class TestMonotonicity:
    """Increasing any single dimension never decreases the score."""

    @pytest.mark.parametrize(
        "base",
        # Sample of base combinations to test monotonicity from
        [(1, 1, 1, 1), (1, 2, 3, 4), (2, 2, 2, 2), (3, 3, 3, 3), (1, 1, 1, 4)],
    )
    @pytest.mark.parametrize("dim_index", [0, 1, 2, 3])
    def test_increasing_dimension_never_decreases_score(self, base, dim_index):
        """Bumping any dimension by 1 never decreases the score."""
        base_list = list(base)
        if base_list[dim_index] >= MAX_DIMENSION:
            pytest.skip("Already at max dimension")

        base_score = score_variety(*base)
        bumped = base_list.copy()
        bumped[dim_index] += 1
        bumped_score = score_variety(*bumped)

        assert bumped_score >= base_score, (
            f"Increasing dim {dim_index} from {base} to {bumped}: "
            f"score went from {base_score} to {bumped_score}"
        )

    @pytest.mark.parametrize(
        "novelty,scope,uncertainty,risk",
        ALL_COMBINATIONS,
    )
    def test_exhaustive_monotonicity_novelty(self, novelty, scope, uncertainty, risk):
        """Exhaustive: bumping novelty by 1 never decreases score."""
        if novelty >= MAX_DIMENSION:
            return  # Nothing to bump
        base = score_variety(novelty, scope, uncertainty, risk)
        bumped = score_variety(novelty + 1, scope, uncertainty, risk)
        assert bumped >= base


# =============================================================================
# Property: no tier skipping
# =============================================================================


class TestNoTierSkipping:
    """Adjacent scores map to the same or adjacent tiers — no jumps."""

    @pytest.mark.parametrize("score", range(MIN_SCORE, MAX_SCORE))
    def test_adjacent_scores_same_or_adjacent_tier(self, score):
        """score and score+1 are in the same tier or adjacent tiers."""
        tier_lo = TIER_INDEX[route_workflow(score)]
        tier_hi = TIER_INDEX[route_workflow(score + 1)]
        assert abs(tier_hi - tier_lo) <= 1, (
            f"Tier jump at scores {score}->{score+1}: "
            f"{route_workflow(score)} -> {route_workflow(score+1)}"
        )


# =============================================================================
# Property: score-route consistency
# =============================================================================


class TestScoreRouteConsistency:
    """Verify that score ranges map to expected routes per spec."""

    def test_compact_range_correct(self):
        for score in range(MIN_SCORE, COMPACT_MAX + 1):
            assert route_workflow(score) == ROUTE_COMPACT

    def test_orchestrate_range_correct(self):
        for score in range(COMPACT_MAX + 1, ORCHESTRATE_MAX + 1):
            assert route_workflow(score) == ROUTE_ORCHESTRATE

    def test_plan_mode_range_correct(self):
        for score in range(ORCHESTRATE_MAX + 1, PLAN_MODE_MAX + 1):
            assert route_workflow(score) == ROUTE_PLAN_MODE

    def test_research_spike_range_correct(self):
        for score in range(PLAN_MODE_MAX + 1, MAX_SCORE + 1):
            assert route_workflow(score) == ROUTE_RESEARCH_SPIKE

    def test_all_scores_covered(self):
        """Every score in [MIN_SCORE, MAX_SCORE] has a route."""
        for score in range(MIN_SCORE, MAX_SCORE + 1):
            route = route_workflow(score)
            assert route in TIER_ORDER


# =============================================================================
# Property: valid range invariant
# =============================================================================


class TestValidRangeInvariant:
    """All valid inputs produce outputs within the valid range."""

    @pytest.mark.parametrize(
        "novelty,scope,uncertainty,risk",
        ALL_COMBINATIONS,
    )
    def test_score_route_roundtrip(self, novelty, scope, uncertainty, risk):
        """score -> route never crashes and returns a valid route."""
        score = score_variety(novelty, scope, uncertainty, risk)
        route = route_workflow(score)
        assert isinstance(route, str)
        assert len(route) > 0


# =============================================================================
# Hypothesis property tests (if available)
# =============================================================================

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


@pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")
class TestHypothesisProperties:
    """Property-based tests using hypothesis."""

    VALID_DIM = st.integers(min_value=MIN_DIMENSION, max_value=MAX_DIMENSION)

    @given(n=VALID_DIM, s=VALID_DIM, u=VALID_DIM, r=VALID_DIM)
    @settings(max_examples=500)
    def test_score_in_range(self, n, s, u, r):
        score = score_variety(n, s, u, r)
        assert MIN_SCORE <= score <= MAX_SCORE

    @given(n=VALID_DIM, s=VALID_DIM, u=VALID_DIM, r=VALID_DIM)
    @settings(max_examples=500)
    def test_score_is_sum(self, n, s, u, r):
        assert score_variety(n, s, u, r) == n + s + u + r

    @given(n=VALID_DIM, s=VALID_DIM, u=VALID_DIM, r=VALID_DIM)
    @settings(max_examples=500)
    def test_route_is_valid(self, n, s, u, r):
        score = score_variety(n, s, u, r)
        route = route_workflow(score)
        assert route in TIER_ORDER

    @given(
        n=VALID_DIM, s=VALID_DIM, u=VALID_DIM, r=VALID_DIM,
        dim=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=500)
    def test_monotonicity(self, n, s, u, r, dim):
        """Increasing a dimension never decreases the score."""
        dims = [n, s, u, r]
        if dims[dim] >= MAX_DIMENSION:
            return
        base = score_variety(*dims)
        dims[dim] += 1
        bumped = score_variety(*dims)
        assert bumped >= base
