"""
Location: pact-plugin/hooks/shared/variety_scorer.py
Summary: Deterministic variety scoring for PACT orchestration.
Used by: Tests, hooks, potential CLI tooling.

Codifies the canonical variety scoring thresholds from pact-variety.md.
The orchestrator continues reasoning about variety; this module provides
the deterministic scoring backbone.

Phase 1 functions: validate_dimension, score_variety, route_workflow
Phase 3 functions: apply_learning_ii_adjustment, compute_calibration_drift,
                   apply_calibration_adjustment
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dimension bounds
# ---------------------------------------------------------------------------
MIN_DIMENSION = 1
MAX_DIMENSION = 4
DIMENSION_COUNT = 4

# ---------------------------------------------------------------------------
# Score range
# ---------------------------------------------------------------------------
MIN_SCORE = DIMENSION_COUNT * MIN_DIMENSION  # 4
MAX_SCORE = DIMENSION_COUNT * MAX_DIMENSION  # 16

# ---------------------------------------------------------------------------
# Workflow routing thresholds (inclusive upper bounds)
# ---------------------------------------------------------------------------
COMPACT_MAX = 6       # 4-6  -> comPACT
ORCHESTRATE_MAX = 10  # 7-10 -> orchestrate
PLAN_MODE_MAX = 14    # 11-14 -> plan-mode + orchestrate
# 15-16 -> research spike

# ---------------------------------------------------------------------------
# Workflow route names
# ---------------------------------------------------------------------------
ROUTE_COMPACT = "comPACT"
ROUTE_ORCHESTRATE = "orchestrate"
ROUTE_PLAN_MODE = "plan-mode"
ROUTE_RESEARCH_SPIKE = "research-spike"

# ---------------------------------------------------------------------------
# Learning II parameters
# ---------------------------------------------------------------------------
LEARNING_II_MIN_MATCHES = 5
LEARNING_II_MAX_BUMP = 1  # max +1 per dimension

# ---------------------------------------------------------------------------
# Calibration feedback parameters
# ---------------------------------------------------------------------------
CALIBRATION_WINDOW_SIZE = 5
CALIBRATION_MIN_SAMPLES = 5
CALIBRATION_MAX_ADJUSTMENT = 1  # max +/-1 total score adjustment
CALIBRATION_NOISE_THRESHOLD = 1.0  # abs(drift) must meet or exceed this


# ---------------------------------------------------------------------------
# Phase 1 functions
# ---------------------------------------------------------------------------


def validate_dimension(value: int, name: str = "dimension") -> None:
    """Validate a single variety dimension is within bounds.

    Args:
        value: Dimension score (1-4)
        name: Dimension name for error messages

    Raises:
        TypeError: If value is not an integer (booleans rejected)
        ValueError: If value is not in range [1, 4]
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(
            f"{name} must be an integer, got {type(value).__name__}"
        )
    if value < MIN_DIMENSION or value > MAX_DIMENSION:
        raise ValueError(
            f"{name} must be between {MIN_DIMENSION} and {MAX_DIMENSION}, "
            f"got {value}"
        )


def score_variety(
    novelty: int,
    scope: int,
    uncertainty: int,
    risk: int,
) -> int:
    """Compute variety score from four dimensions.

    Each dimension is scored 1 (Low) to 4 (Extreme).
    Total score ranges from 4 to 16.

    Args:
        novelty: How novel the task is (1=routine, 4=unprecedented)
        scope: How many concerns are involved (1=single, 4=cross-cutting)
        uncertainty: How clear requirements are (1=clear, 4=unknown)
        risk: Impact if implementation is wrong (1=low, 4=critical)

    Returns:
        Total variety score (4-16)

    Raises:
        TypeError: If any dimension is not an integer
        ValueError: If any dimension is out of range [1, 4]
    """
    validate_dimension(novelty, "novelty")
    validate_dimension(scope, "scope")
    validate_dimension(uncertainty, "uncertainty")
    validate_dimension(risk, "risk")
    return novelty + scope + uncertainty + risk


def route_workflow(score: int) -> str:
    """Map variety score to recommended workflow.

    Args:
        score: Total variety score (4-16)

    Returns:
        Workflow name: "comPACT" | "orchestrate" | "plan-mode" | "research-spike"

    Raises:
        ValueError: If score is out of range [4, 16]
    """
    if not isinstance(score, int) or isinstance(score, bool):
        raise TypeError(
            f"score must be an integer, got {type(score).__name__}"
        )
    if score < MIN_SCORE or score > MAX_SCORE:
        raise ValueError(
            f"score must be between {MIN_SCORE} and {MAX_SCORE}, got {score}"
        )

    if score <= COMPACT_MAX:
        return ROUTE_COMPACT
    if score <= ORCHESTRATE_MAX:
        return ROUTE_ORCHESTRATE
    if score <= PLAN_MODE_MAX:
        return ROUTE_PLAN_MODE
    return ROUTE_RESEARCH_SPIKE


# ---------------------------------------------------------------------------
# Phase 3 functions
# ---------------------------------------------------------------------------


def apply_learning_ii_adjustment(
    base_score: int,
    domain: str,
    calibration_matches: int,
) -> int:
    """Apply Learning II pattern-adjusted scoring.

    If sufficient calibration matches exist for the domain, bumps the
    score by +1. The specific dimension to bump is determined by the
    orchestrator's reasoning, not this function — this function applies
    the aggregate bump to the total score.

    Args:
        base_score: Original variety score (4-16)
        domain: Task domain string (e.g., "auth", "hooks"). The caller
            pre-filters calibration matches by domain before passing the
            count; this parameter is retained for API consistency and
            future domain-specific scoring extensions.
        calibration_matches: Number of matching pact-memory entries

    Returns:
        Adjusted score (base_score or base_score + 1, clamped to MAX_SCORE)
    """
    if calibration_matches < LEARNING_II_MIN_MATCHES:
        return base_score
    return min(base_score + LEARNING_II_MAX_BUMP, MAX_SCORE)


def compute_calibration_drift(
    calibration_records: list[dict],
    domain: str,
) -> float:
    """Compute mean drift from calibration records for a domain.

    Filters records by domain, takes the most recent CALIBRATION_WINDOW_SIZE,
    and computes mean(actual - initial).

    Args:
        calibration_records: List of CalibrationRecord dicts. Each should
            contain 'domain', 'actual_difficulty_score',
            'initial_variety_score', and optionally 'timestamp' for
            recency ordering. Records missing score fields are skipped.
        domain: Domain to filter by

    Returns:
        Mean drift (positive = underestimation, negative = overestimation).
        Returns 0.0 if insufficient samples (< CALIBRATION_MIN_SAMPLES).
    """
    # Filter by domain (case-insensitive)
    domain_lower = domain.lower()
    domain_records = [
        r for r in calibration_records
        if r.get("domain", "").lower() == domain_lower
    ]

    if len(domain_records) < CALIBRATION_MIN_SAMPLES:
        return 0.0

    # Sort by timestamp descending (most recent first) if timestamps exist,
    # otherwise take the first N records (assuming append-order).
    # Note: records without timestamps get sort key "" which sorts to
    # the front with reverse=True, treating them as most recent.
    if any(r.get("timestamp") for r in domain_records):
        domain_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    window = domain_records[:CALIBRATION_WINDOW_SIZE]

    # Skip records missing either score field (defensive against malformed data)
    scored = [
        r for r in window
        if r.get("actual_difficulty_score") is not None
        and r.get("initial_variety_score") is not None
    ]
    if not scored:
        return 0.0

    total_drift = sum(
        r["actual_difficulty_score"] - r["initial_variety_score"]
        for r in scored
    )
    return total_drift / len(scored)


def apply_calibration_adjustment(
    base_score: int,
    drift: float,
) -> int:
    """Apply calibration-based score adjustment.

    Args:
        base_score: Score after Learning II adjustment (4-16)
        drift: Mean drift from compute_calibration_drift

    Returns:
        Adjusted score, clamped to [MIN_SCORE, MAX_SCORE].
        Adjustment is at most +/-CALIBRATION_MAX_ADJUSTMENT.
        No adjustment if abs(drift) < CALIBRATION_NOISE_THRESHOLD.
    """
    if abs(drift) < CALIBRATION_NOISE_THRESHOLD:
        return base_score

    # Clamp adjustment to +/-1
    adjustment = max(
        -CALIBRATION_MAX_ADJUSTMENT,
        min(round(drift), CALIBRATION_MAX_ADJUSTMENT),
    )

    # Clamp result to valid score range
    return max(MIN_SCORE, min(base_score + adjustment, MAX_SCORE))
