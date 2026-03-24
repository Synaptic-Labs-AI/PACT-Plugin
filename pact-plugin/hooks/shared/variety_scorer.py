"""
Location: pact-plugin/hooks/shared/variety_scorer.py
Summary: Deterministic variety scoring for PACT orchestration.
Used by: Tests, hooks, potential CLI tooling.

Codifies the canonical variety scoring thresholds from pact-variety.md.
The orchestrator continues reasoning about variety; this module provides
the deterministic scoring backbone.

Functions: validate_dimension, score_variety, route_workflow
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
# Functions
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
