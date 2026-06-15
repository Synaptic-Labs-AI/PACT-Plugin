"""
Location: pact-plugin/hooks/shared/variety_divergence.py
Summary: Pure-function variety divergence math for the wrap-up retrospective.
Used by: pact-plugin/commands/wrap-up.md §4 Orchestration Retrospective composer.

Computes the divergence between feature-level variety and the per-dispatch
variety distribution, surfacing miscalibration when the mean dispatch
variety differs from the feature variety by more than a threshold.

Mirrors variety_scorer.py module conventions:
- Pure function, no side effects, no disk reads.
- Fail-open semantics: bad input returns a structured advisory dict with
  `surfaced=False` and a `reason` key, NOT an exception.

Functions:
- compute_variety_divergence(feature_variety, dispatch_varieties,
  total_pact_dispatch_count=None, threshold=2) -> dict
- count_task_b_dispatch_sites(agent_dispatch_events,
  review_dispatch_events, remediation_events) -> int — the Q5 coverage
  denominator, counted from variety-independent journal markers.

Return shape (stable keys):
- `coverage`:  float in [0.0, 1.0] — fraction of pact-* dispatches with
               variety stamped. When total_pact_dispatch_count is None,
               assumed 1.0 (all known dispatches were stamped).
- `mean`:      int | None — rounded mean of stamped dispatch variety
               totals; None when dispatches is empty.
- `max`:       int | None — max of stamped dispatch totals; None when empty.
- `min`:       int | None — min of stamped dispatch totals; None when empty.
- `delta`:     int | None — abs(feature_variety - mean); None when either
               feature_variety is None or dispatches is empty.
- `surfaced`:  bool — True when delta >= threshold AND feature_variety is
               not None AND dispatches is non-empty.
- `direction`: "overshot" | "undershot" | None — populated when
               surfaced=True, None otherwise. "overshot" means
               feature_variety > mean (feature was estimated too high);
               "undershot" means feature_variety < mean (estimated too low).
- `reason`:    str | None — present when surfaced=False with a structural
               cause: "feature_variety_missing", "no_dispatches_stamped",
               "within_threshold", or "coverage_exceeds_unity" (the
               defense-in-depth tripwire returned when stamped dispatches
               outnumber the counted dispatch sites — coverage > 1.0,
               which signals a denominator regression). None when
               surfaced=True.

The composer in wrap-up.md §4 reads this dict and produces the §3.4
sample output prose. Tests live in test_per_dispatch_variety.py.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Threshold default
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 2  # see pact-variety.md §Variety Calibration Record


def compute_variety_divergence(
    feature_variety: int | None,
    dispatch_varieties: list[int],
    total_pact_dispatch_count: int | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """Compute divergence between feature variety and dispatch distribution.

    Args:
        feature_variety: feature-level variety total (4-16) or None when the
            feature task lacks `metadata.variety` (legacy / pre-rollout).
        dispatch_varieties: list of per-dispatch variety totals (int) read
            from each pact-* work task's `metadata.variety.total`. Tasks
            without variety stamping are omitted from this list.
        total_pact_dispatch_count: total count of pact-* work tasks in the
            session, including those without variety stamping. When None,
            assumed equal to `len(dispatch_varieties)` (coverage = 1.0).
        threshold: minimum delta between feature_variety and dispatch mean
            for the divergence to be surfaced. Default 2 per pact-variety.md
            (a delta of 2 represents one full variety band off).

    Returns:
        dict with keys: coverage, mean, max, min, delta, surfaced, direction,
        reason. See module docstring for semantics.
    """
    # --- Coverage ---
    stamped_count = len(dispatch_varieties)
    if total_pact_dispatch_count is None or total_pact_dispatch_count <= 0:
        coverage = 1.0 if stamped_count > 0 else 0.0
    else:
        coverage = stamped_count / total_pact_dispatch_count

    # --- Empty dispatches fail-open ---
    if stamped_count == 0:
        return {
            "coverage": coverage,
            "mean": None,
            "max": None,
            "min": None,
            "delta": None,
            "surfaced": False,
            "direction": None,
            "reason": "no_dispatches_stamped",
        }

    # --- Stats over stamped subset ---
    dispatch_sum = sum(dispatch_varieties)
    mean = round(dispatch_sum / stamped_count)
    dispatch_max = max(dispatch_varieties)
    dispatch_min = min(dispatch_varieties)

    # --- Defense-in-depth: coverage > 1.0 tripwire (advisory, NOT a clamp) ---
    # When the stamped dispatches outnumber the counted Task-B dispatch
    # sites, coverage exceeds 1.0 — a denominator regression. Surface it as
    # a self-reporting advisory rather than silently emitting coverage > 1.0
    # or clamping it (a clamp would HIDE the regression this is meant to
    # catch). With the distinct-site denominator
    # (count_task_b_dispatch_sites) this is zero-residual by construction,
    # so this path fires only on a future emit/denominator regression.
    # surfaced=False because a divergence computed over a broken denominator
    # is untrustworthy — the orchestrator should investigate the count, not
    # report the divergence. coverage is left UNCLAMPED so the anomaly is
    # visible in the output.
    if (
        total_pact_dispatch_count is not None
        and total_pact_dispatch_count > 0
        and stamped_count > total_pact_dispatch_count
    ):
        delta = (
            abs(feature_variety - mean)
            if isinstance(feature_variety, int)
            else None
        )
        return {
            "coverage": coverage,
            "mean": mean,
            "max": dispatch_max,
            "min": dispatch_min,
            "delta": delta,
            "surfaced": False,
            "direction": None,
            "reason": "coverage_exceeds_unity",
        }

    # --- Feature variety missing fail-open ---
    if not isinstance(feature_variety, int):
        return {
            "coverage": coverage,
            "mean": mean,
            "max": dispatch_max,
            "min": dispatch_min,
            "delta": None,
            "surfaced": False,
            "direction": None,
            "reason": "feature_variety_missing",
        }

    # --- Delta + threshold check ---
    delta = abs(feature_variety - mean)
    if delta >= threshold:
        direction = "overshot" if feature_variety > mean else "undershot"
        return {
            "coverage": coverage,
            "mean": mean,
            "max": dispatch_max,
            "min": dispatch_min,
            "delta": delta,
            "surfaced": True,
            "direction": direction,
            "reason": None,
        }

    return {
        "coverage": coverage,
        "mean": mean,
        "max": dispatch_max,
        "min": dispatch_min,
        "delta": delta,
        "surfaced": False,
        "direction": None,
        "reason": "within_threshold",
    }


def count_task_b_dispatch_sites(
    agent_dispatch_events: list[dict],
    review_dispatch_events: list[dict],
    remediation_events: list[dict],
) -> int:
    """Count the distinct Task-B dispatch SITES — the Q5 coverage denominator.

    coverage = (stamped dispatches) / (this count). The denominator is
    sourced from the variety-INDEPENDENT journal markers (agent_dispatch,
    review_dispatch reviewers, remediation) because those exist regardless
    of whether variety was stamped — the variety stream cannot see its own
    gaps. So an un-stamped dispatch still counts here and legitimately
    lowers coverage (that is exactly what coverage exists to surface).

    Count = len(agent_dispatch)
            + Σ len(review_dispatch[i].reviewers)
            + remediations whose task_id is NOT already an agent_dispatch
              task_id.

    Reviewers are counted via `review_dispatch.reviewers` and are NOT
    deduped: peer-review emits no `agent_dispatch`, so reviewers are
    disjoint from the agent_dispatch population by emit-site design. ONLY
    remediation can collide — a comPACT/orchestrate-dispatched remediation
    emits BOTH `remediation` AND `agent_dispatch` for the same task_id, so
    it is deduped to count once (via the agent_dispatch stream); a pure
    reuse-remediation (no agent_dispatch) is counted via the remediation
    stream. A remediation with a missing task_id is COUNTED (fail-safe:
    never undercount, so a dropped id can never inflate coverage above 1.0).

    By construction this denominator excludes teachback Task-A gates and
    signal/system tasks: they emit none of these three event types, so
    there is nothing to filter out.

    Pure function — no disk reads, no mutation. The caller passes event
    lists scoped to the current arc; the remediation/agent_dispatch
    task_id dedup is correct only WITHIN one arc, because the platform
    reuses task_ids across arcs (the arc boundary is applied upstream at
    read time).
    """
    agent_task_ids = {
        e.get("task_id")
        for e in agent_dispatch_events
        if e.get("task_id") is not None
    }
    reviewer_count = sum(
        len(e.get("reviewers") or []) for e in review_dispatch_events
    )
    remediation_count = sum(
        1
        for r in remediation_events
        if r.get("task_id") not in agent_task_ids
    )
    return len(agent_dispatch_events) + reviewer_count + remediation_count
