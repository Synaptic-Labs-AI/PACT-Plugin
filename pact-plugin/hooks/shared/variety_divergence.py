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
- `coverage`:  float — fraction of pact-* dispatches with variety stamped;
               normally in [0.0, 1.0] but NOT clamped. In the
               `coverage_exceeds_unity` advisory branch it is returned
               UNCLAMPED >= 1.0 (the stamped/total ratio when total > 0, or
               a finite stamped-count signal when the computed denominator
               collapsed to 0 with stamps present) so a denominator
               regression stays visible; it is debug-only there
               (surfaced=False). When total_pact_dispatch_count is None,
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
sample output prose. compute_variety_divergence tests live in
test_per_dispatch_variety.py; the net-new helpers
(count_task_b_dispatch_sites, resolve_arc_start) live in
test_variety_divergence.py.
"""

from __future__ import annotations

from datetime import datetime


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

    Note: `mean` is round()-ed (banker's rounding, round-half-to-even) and
    the threshold check uses that rounded mean. At an exact .5 mean (e.g.
    sum/count == 6.5) round-half-to-even can tip the surfaced decision by
    one — an accepted minor boundary effect of a heuristic band, not a bug.

    Returns:
        dict with keys: coverage, mean, max, min, delta, surfaced, direction,
        reason. See module docstring for semantics.
    """
    # --- Coverage ---
    stamped_count = len(dispatch_varieties)
    if total_pact_dispatch_count is None or total_pact_dispatch_count < 0:
        # None = legacy / no denominator passed; negative = impossible /
        # garbage input (a real dispatch-site count is never < 0). Both
        # fail-open to the all-stamped assumption rather than tripping the
        # regression advisory — a negative is a caller bug, not a meaningful
        # denominator collapse.
        coverage = 1.0 if stamped_count > 0 else 0.0
    elif total_pact_dispatch_count == 0:
        # COMPUTED denominator == 0. With stamps firing (stamped > 0) this is
        # the WORST denominator regression — every dispatch marker is absent
        # while variety stamps exist. Do NOT fail-open to 1.0 (that would
        # HIDE it); the coverage_exceeds_unity advisory below trips.
        # coverage is a FINITE >=1.0 signal (the stamped count, i.e.
        # denominator-treated-as-1) rather than +inf, to avoid an inf
        # footgun in downstream formatting/arithmetic — it is debug-only
        # (surfaced=False; the composer emits the advisory, not a ratio).
        # stamped == 0 here is a genuinely empty session and returns via the
        # empty-dispatch fail-open.
        coverage = float(stamped_count) if stamped_count > 0 else 0.0
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
    # sites (stamped > total), coverage exceeds 1.0 — a denominator
    # regression. This ALSO covers the computed-total==0-with-stamps
    # collapse (every marker absent while stamps fire → coverage +inf): the
    # guard requires total >= 0 (a real, non-negative computed denominator)
    # AND stamped > total, so the total==0 collapse trips here instead of
    # fail-opening to coverage=1.0. None (legacy) and negative (garbage)
    # are handled in the coverage block above and never reach this branch.
    # Surface it as a self-reporting advisory rather than silently emitting
    # coverage > 1.0 or clamping it (a clamp would HIDE the regression this
    # is meant to catch). surfaced=False because a divergence computed over
    # a broken denominator is untrustworthy — the orchestrator should
    # investigate the count, not report the divergence. coverage is left
    # UNCLAMPED so the anomaly is visible in the output.
    if (
        total_pact_dispatch_count is not None
        and total_pact_dispatch_count >= 0
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
    """Count the Task-B dispatch SITES — the Q5 coverage denominator.

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

    agent_dispatch is EVENT-counted (`len`), NOT deduped by distinct
    task_id. This is correct because the caller arc-scopes the reads FIRST
    (the journal `--since` boundary) → the input is a SINGLE arc → within
    one arc every Task-B has a unique task_id → event-count == distinct
    count. The "one agent_dispatch per task_id" property is GUARANTEED by
    that arc-scoping precondition, not an unguarded assumption. Do NOT
    "harden" this to a distinct-id count: across arcs the platform REUSES
    low task_ids, so a prior arc's task-8 and the current arc's task-8 are
    GENUINELY DISTINCT dispatches — collapsing them by id would be wrong on
    unscoped input, buys no real robustness (it leans on the same
    --since-first precondition that already makes event-count correct), and
    fails more quietly on misuse than the loud over-count it would replace.

    Reviewers are counted via `review_dispatch.reviewers` (by name) and are
    NOT deduped: peer-review emits no `agent_dispatch`, so reviewers are
    disjoint from the agent_dispatch population by emit-site design. A
    reviewer REUSED as a fixer is not a double-count: reuse creates a NEW
    fix Task-B (a distinct task_id in `remediation`), so the review-work and
    the fix-work are two legitimately-distinct dispatches.

    ONLY remediation is deduped against agent_dispatch — a
    comPACT/orchestrate-dispatched remediation emits BOTH `remediation` AND
    `agent_dispatch` for the same task_id, so it is counted once (via the
    agent_dispatch stream); a pure reuse-remediation (no agent_dispatch) is
    counted via the remediation stream. Two `remediation` events that share
    a task_id are NOT deduped among themselves: that is a bounded over-count
    that only UNDER-states coverage (never >1.0) and is backstopped by the
    `coverage_exceeds_unity` advisory in compute_variety_divergence, so no
    self-dedup is warranted. A remediation with a missing task_id is COUNTED
    (fail-safe: never undercount, so a dropped id can't inflate coverage).

    DIRECTIONAL backstop caveat: the `coverage_exceeds_unity` advisory in
    compute_variety_divergence only catches the OVER-count direction
    (numerator > denominator → coverage >1.0). The OPPOSITE — a
    remediation↔agent_dispatch task_id THREADING MISMATCH that fails to
    dedup a site that should have deduped — OVER-counts the denominator and
    UNDER-states coverage (a false stamping gap), which has NO advisory
    backstop. That residual is bounded (one extra site per mismatched
    remediation) and the str-normalized dedup key (below) is the primary
    guard against the most likely mismatch (int vs str task_id).

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
        str(e.get("task_id"))
        for e in agent_dispatch_events
        if e.get("task_id") is not None
    }
    # Count reviewers only when `reviewers` is a list — a stray string value
    # would otherwise have its CHARACTERS counted by len().
    reviewer_count = sum(
        len(e.get("reviewers"))
        for e in review_dispatch_events
        if isinstance(e.get("reviewers"), list)
    )
    # str-normalize the dedup key so an int agent task_id and a str
    # remediation task_id (or vice versa) still match. A remediation with a
    # missing task_id stringifies to "None" (never in the agent set, which
    # excludes None) → counted (fail-safe: never undercount).
    remediation_count = sum(
        1
        for r in remediation_events
        if str(r.get("task_id")) not in agent_task_ids
    )
    return len(agent_dispatch_events) + reviewer_count + remediation_count


def resolve_arc_start(
    variety_assessed_events: list[dict],
    feature_task_id: str,
) -> str | None:
    """Resolve the current arc's start timestamp for `--since` scoping.

    Returns the LATEST `ts` among `variety_assessed` events whose `task_id`
    matches `feature_task_id`. The platform REUSES low task_ids across arcs,
    so the current feature's id can also match a PRIOR arc's
    `variety_assessed`; the latest-ts match is the current arc (this is why
    a plain most-recent `read-last` of ANY feature is wrong). Returns None
    when no matching `variety_assessed` exists (legacy/trivial session) →
    the caller omits `--since` → whole-journal read (fail-open; single-arc
    behavior unchanged).

    Scope boundary (comPACT-led arcs): only the orchestrate
    variety-assessment step emits `variety_assessed`, so this returns None
    for a comPACT feature id. That is BENIGN and never mis-scopes the
    retrospective: the wrap-up Q5/Q6 retrospective runs only against an
    orchestrate feature assessment (a comPACT workflow does not invoke the
    retrospective, and wrap-up skips trivial single-comPACT sessions). In a
    resumed comPACT-then-orchestrate session the wrap-up's feature id is the
    orchestrate feature, whose `variety_assessed` anchors `--since` and
    excludes the prior comPACT arc's events by ts. So None-for-comPACT never
    occurs on the retro path that consumes this helper.

    Timestamps are PARSED for the max, never lexically compared: `make_event`
    stamps `ts` as `...Z` while `canonical_since()` emits `...+00:00`, and a
    lexical compare across the two is wrong (`'+'` 0x2B sorts before `'Z'`
    0x5A). The 2-line normalize-and-parse is duplicated locally (rather than
    importing `session_journal._parse_ts`) to keep this module decoupled; if
    a third ts-parse site ever appears, extract a shared util. The RETURN
    value is the original `ts` STRING of the latest event, so the caller
    passes it to `--since`, which `_ts_ge` re-parses. The parse AND the
    max-comparison both run inside one try/except, so an entry that is
    unparseable OR un-comparable (e.g. a parseable-but-naive ts compared
    against an aware one → TypeError) is skipped (fail-open), never raised.
    If no matching, usable entry remains → None.

    task_id matching is str-normalized (`str(event task_id) == str(feature
    task_id)`) so a future bare-int `variety_assessed` emit still matches a
    str feature_task_id.

    arc_start relies on `variety_assessed` being emitted exactly once per arc
    (sole writer: the orchestrate variety-assessment step). If a future
    change ever re-emits it mid-arc for the same feature_task_id, switch from
    latest-ts to earliest-after-prior-arc-boundary — latest-ts would
    otherwise push arc_start forward and drop early-arc dispatches.

    Pure function — no disk reads, no mutation.
    """
    def _parse(value: object) -> datetime:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    latest_ts: str | None = None
    latest_dt: datetime | None = None
    for event in variety_assessed_events:
        if str(event.get("task_id")) != str(feature_task_id):
            continue
        ts = event.get("ts")
        if not ts:
            continue
        try:
            dt = _parse(ts)
            # The comparison is INSIDE the try (mirroring _ts_ge): comparing
            # a parseable-but-naive ts against an aware one raises TypeError
            # — fail open (skip the entry) instead of crashing the read.
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
                latest_ts = ts
        except (ValueError, TypeError):
            continue
    return latest_ts
