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
- extract_dispatch_coverage(dispatch_site_events) -> (variety_totals,
  total, malformed) — BOTH Q5 terms from ONE pass over ONE input list.
- extract_final_dispatch_coverage(dispatch_site_events, snapshot_events,
  dispatch_assessed_events=None) -> dict of TEN keys. The Q5 join:
  MEMBERSHIP comes from the `dispatch_site` stream, and the VALUE comes
  from the latest `task_metadata_snapshot` of the same task, so the
  distribution holds the FINAL total rather than the as-dispatched one.
  Four of the ten keys report what the join OBSERVED about the
  as-dispatched side, so a value taken from a snapshot cannot silently
  absorb a stamping gap, a producer defect, or a revision that left the
  total where it was. The optional third argument adds the
  dispatch-marked `variety_assessed` events as a fallback-path
  as-dispatched source (arm 2b); omitted, the join is the legacy
  two-source shape.

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
- `stamped`:   int — the numerator as a RAW COUNT.
- `total`:     int | None — the denominator as a RAW COUNT (None on the
               legacy no-denominator path). `stamped` and `total` are
               present on EVERY return path so a reader can render "0 of 5"
               rather than a bare `0.000`: the counts say which of the two
               zero-coverage states this is, and the ratio does not.
- `mean`:      int | None — rounded mean of stamped dispatch variety
               totals; None when dispatches is empty.
- `max`:       int | None — max of stamped dispatch totals; None when empty.
- `min`:       int | None — min of stamped dispatch totals; None when empty.
- `delta`:     int | None — abs(feature_variety - mean); None when either
               feature_variety is None or dispatches is empty.
- `surfaced`:  bool — True when delta >= threshold AND feature_variety is
               not None AND dispatches is non-empty; ALSO True for the
               `zero_coverage` state below, which is a finding rather than a
               degenerate case.
- `direction`: "overshot" | "undershot" | None — populated when
               surfaced=True, None otherwise. "overshot" means
               feature_variety > mean (feature was estimated too high);
               "undershot" means feature_variety < mean (estimated too low).
- `reason`:    str | None — the structural cause, or None on the ordinary
               surfaced-divergence path:
               * "no_dispatch_sites"   — total == 0: NOTHING WAS DISPATCHED,
                 so there is no entity to measure. surfaced=False. This is
                 the N/A state; rendering 0.000 for it manufactures a
                 compliance gap out of a missing stream.
               * "zero_coverage"       — total > 0 and stamped == 0: every
                 dispatch site exists and NONE was stamped. surfaced=TRUE —
                 the loudest legitimate reading of this metric, not a
                 degenerate case. (Replaces the former
                 "no_dispatches_stamped", whose name was FALSE here: it
                 said "no dispatches" when there were N.)
               * "feature_variety_missing", "within_threshold" — as before.
               * "coverage_exceeds_unity" — the defense-in-depth tripwire
                 when stamped outnumbers total. UNREACHABLE from Q5 under
                 the one-event topology (extract_dispatch_coverage cannot
                 produce it); retained for a caller assembling both terms
                 by hand.

The composer in wrap-up.md §4 reads this dict and produces the §3.4
sample output prose. compute_variety_divergence tests live in
test_per_dispatch_variety.py; the net-new helpers
(extract_dispatch_coverage, resolve_arc_start) live in
test_variety_divergence.py.
"""

from __future__ import annotations

from datetime import datetime

from .constants import VARIETY_ASSESSED_DISPATCH_SCOPE
from .teachback_schema import (
    MAX_DIMENSION,
    MIN_DIMENSION,
    _is_in_range_int,
    _VARIETY_DIMENSIONS,
    resolve_variety_total,
)


# ---------------------------------------------------------------------------
# Threshold default
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD = 2  # see pact-variety.md §Variety Calibration Record


# ---------------------------------------------------------------------------
# Timestamp parse — ONE implementation, two call sites in this module
# ---------------------------------------------------------------------------
def _parse_ts(value: object) -> datetime:
    """Normalize a journal `ts` and parse it into an instant.

    `make_event` stamps `ts` as `...Z` and `canonical_since()` emits
    `...+00:00`, so the two forms must be compared as INSTANTS and not as
    strings. A lexical compare is decided by the first byte after the
    seconds, and `+` (0x2B) sorts before `Z` (0x5A), so a `+00:00` stamp
    sorts before an equal-instant `Z` stamp.

    This is a local copy rather than an import of
    `session_journal._parse_ts`, which keeps this module decoupled. It is
    ONE implementation shared by the two callers below, so the count of
    implementations in the codebase stays at two.

    RAISES on an unparseable value. Each caller owns its own fail-open.
    """
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


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

    # --- Nothing stamped: `total` is the DISCRIMINATOR, not a detail ---
    # `stamped == 0` is NOT a terminal condition. Two opposite findings live
    # here and they must never render identically:
    #
    #   total == 0              -> no entity to measure. N/A.
    #   total > 0, stamped == 0 -> every site un-stamped. The metric's worst
    #                              legitimate reading, and the loudest signal
    #                              coverage can produce.
    #
    # Returning early on `stamped == 0` before consulting `total` suppressed
    # the second as if it were the first: a REAL gap reported as "nothing to
    # report". That is not a believable wrong answer beating a visible
    # refusal — it is an INVISIBLE REFUSAL reading as nothing-to-report.
    if stamped_count == 0:
        no_sites = total_pact_dispatch_count == 0 or total_pact_dispatch_count is None
        return {
            "coverage": coverage,
            "stamped": stamped_count,
            "total": total_pact_dispatch_count,
            "mean": None,
            "max": None,
            "min": None,
            "delta": None,
            # A 0% coverage gap is the finding, not a degenerate case to hide.
            "surfaced": not no_sites,
            "direction": None,
            "reason": "no_dispatch_sites" if no_sites else "zero_coverage",
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
            "stamped": stamped_count,
            "total": total_pact_dispatch_count,
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
            "stamped": stamped_count,
            "total": total_pact_dispatch_count,
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
            "stamped": stamped_count,
            "total": total_pact_dispatch_count,
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
        "stamped": stamped_count,
        "total": total_pact_dispatch_count,
        "mean": mean,
        "max": dispatch_max,
        "min": dispatch_min,
        "delta": delta,
        "surfaced": False,
        "direction": None,
        "reason": "within_threshold",
    }


def extract_dispatch_coverage(
    dispatch_site_events: list[dict],
) -> tuple[list[int], int, list[dict]]:
    """Derive BOTH Q5 coverage terms from ONE pass over the `dispatch_site`
    stream.

    Returns `(variety_totals, total, malformed)`:

    - `variety_totals` — the resolved variety total of every event that
      carries a resolvable stamp. **This list IS the numerator's source**:
      the numerator is `len(variety_totals)`, and the same list object is
      passed to `compute_variety_divergence` for mean/max/min/delta.
    - `total` — the DENOMINATOR: the number of `dispatch_site` events, i.e.
      the number of dispatch SITES. It counts the event's EXISTENCE, never
      the stamp's presence.
    - `malformed` — events carrying a `variety` value that could NOT be
      resolved. A DIFFERENT category from a missing stamp (see below).

    **Why one function returning the list, rather than a count the caller
    re-derives:** the numerator and denominator must be sourced over the
    same Task-B dispatch population. Returning a count would leave the
    caller to compute the totals in a second pass, making that invariant a
    rule maintained by agreement between two derivations. Here the numerator
    IS the length of the list the caller uses, so the coupling is an
    identity, not something a test has to keep true. There is deliberately
    no `stamped == len(variety_totals)` assertion anywhere: the two cannot
    diverge, so there is nothing to pin. Its absence is the design working,
    not a missing test.

    **Coverage > 1.0 is structurally impossible here.** Every element of
    `variety_totals` comes from an event that also incremented `total`, so
    `len(variety_totals) <= total` always — including for arbitrary,
    hostile, or partially-malformed input.

    **A missing stamp and a malformed stamp are different findings with
    different remedies, and are never merged:**

    - `variety` ABSENT — counted in `total`, absent from `variety_totals`,
      and NOT in `malformed`. An honest un-stamped dispatch: the coverage
      gap the metric exists to surface. The emit merges the on-disk task
      metadata with the owner-witnessing write, so an absent `variety` means
      the dispatch was never stamped ANYWHERE — a compliance signal, not a
      producer artefact.
    - `variety` PRESENT but unresolvable — counted in `total`, absent from
      `variety_totals`, AND listed in `malformed`. A data-quality defect.
      Folding these into the missing-stamp population would leave the ratio
      unchanged while reporting a normal gap as a producer defect, which
      trains readers to ignore the signal.

    A stamp recovered through a NON-CANONICAL resolver candidate (e.g. the
    four-dimension sum, with no `total` key) is STAMPED, not malformed:
    `resolve_variety_total` returning a value IS resolution, and which
    candidate won is not this consumer's business.

    Pure function; never raises. Non-list input yields `([], 0, [])`; a
    non-dict element is counted as a site (it existed) and contributes no
    total. The caller scopes `dispatch_site_events` to the current arc
    before calling — the platform reuses task_ids across arcs.
    """
    if not isinstance(dispatch_site_events, list):
        return [], 0, []

    variety_totals: list[int] = []
    malformed: list[dict] = []

    for event in dispatch_site_events:
        if not isinstance(event, dict):
            # It existed, so it is a site; it carries no resolvable stamp.
            continue
        # Absent key vs present-but-unresolvable are different findings, so
        # membership is tested before resolution is attempted.
        has_variety = "variety" in event
        resolved = resolve_variety_total(event.get("variety"))
        if resolved is not None:
            variety_totals.append(resolved)
        elif has_variety:
            malformed.append(event)

    return variety_totals, len(dispatch_site_events), malformed


def _latest_snapshot_by_task(snapshot_events: list[dict]) -> dict[str, dict]:
    """Index `task_metadata_snapshot` events by task id, keeping the LATEST.

    LATEST means the greatest PARSED instant. On an equal instant the LATER
    element in list order wins, which is why the compare is `>=`. Journal
    events stamp `ts` at second granularity, so a tie is ordinary, and the
    later line in journal order is the authoritative one for this stream.

    THIS TIE-BREAK IS THE OPPOSITE OF `resolve_arc_start`, which compares
    with `>` and keeps the FIRST of two equal instants. That function picks
    an arc BOUNDARY, where first-wins is the safe direction. This one picks
    the FINAL value, where last-wins is the definition of final.

    An event that is not a dict, that carries no `task_id`, or that carries
    a missing or unparseable `ts`, is skipped. A `task_id` of None is
    skipped rather than keyed, so it cannot join to a member that carries no
    task id either. The compare sits INSIDE the try, so a naive `ts`
    compared against an aware one raises TypeError and fails open.

    The caller passes the list in journal order, because the tie-break is
    positional. Pure function, and it does not raise.
    """
    latest: dict[str, tuple[datetime, dict]] = {}
    if not isinstance(snapshot_events, list):
        return {}
    for event in snapshot_events:
        if not isinstance(event, dict):
            continue
        task_id = event.get("task_id")
        if task_id is None:
            continue
        ts = event.get("ts")
        if not ts:
            continue
        key = str(task_id)
        try:
            parsed = _parse_ts(ts)
            current = latest.get(key)
            # `>=` is last-wins on an equal instant. See the docstring above
            # for why this direction is the opposite of resolve_arc_start.
            if current is None or parsed >= current[0]:
                latest[key] = (parsed, event)
        except (ValueError, TypeError):
            continue
    return {key: value[1] for key, value in latest.items()}


def _dimension_vector(variety: object) -> tuple[int, ...] | None:
    """The four dimension scores as a tuple, or None when the vector is not COMPLETE.

    COMPLETE means all four dimensions are present as non-bool ints inside
    [MIN_DIMENSION, MAX_DIMENSION]. A partial or out-of-range vector yields
    None rather than a shorter tuple, because a shorter tuple compares
    unequal to a full one and would report a revision that did not happen.

    The dimension NAMES and the range come from `teachback_schema`, which
    derives its dispatch projection from the same tuple. THE IMPORT IS
    DELIBERATE AND THIS PARAGRAPH RECORDS THE INTENT, so a reader who meets a
    cross-module private import does not replace it with a tidier local
    tuple. The intent is that the four names are stated in one list. Nothing
    in this module enforces that, and no arm here can, so read this as the
    reason for the import and not as a guarantee about a future edit.

    Pure function, and it does not raise. A non-dict input yields None.
    """
    if not isinstance(variety, dict):
        return None
    values: list[int] = []
    for name in _VARIETY_DIMENSIONS:
        value = variety.get(name)
        if not _is_in_range_int(value, MIN_DIMENSION, MAX_DIMENSION):
            return None
        values.append(value)
    return tuple(values)


def _latest_dispatch_assessed_by_task(
    dispatch_assessed_events: list[dict] | None,
) -> dict[str, dict]:
    """Index dispatch-marked `variety_assessed` events by task id, LATEST wins.

    Only events whose TOP-LEVEL `scope` equals
    VARIETY_ASSESSED_DISPATCH_SCOPE are eligible — the per-dispatch mirrors
    written by the dispatch command prose at the Task-B stamp site. A
    feature-level event (no `scope` field) never enters this index, so the
    two populations cannot cross-contaminate however they interleave.

    LATEST means the greatest PARSED instant, last-wins on an equal instant
    (the `_latest_snapshot_by_task` direction, not the `resolve_arc_start`
    direction): this index supplies a VALUE, and the later line in journal
    order is the authoritative one for a value. An event that is not a dict,
    carries no `task_id`, or carries a missing/unparseable/un-comparable
    `ts` is skipped, with the compare INSIDE the try so the naive-against-
    aware TypeError fails open. The caller passes the list arc-scoped and in
    journal order, because the platform reuses task ids across arcs and the
    tie-break is positional. A `None` argument yields {} — the two-argument
    legacy call shape of extract_final_dispatch_coverage never builds an
    index at all.

    Pure function, and it does not raise.
    """
    latest: dict[str, tuple[datetime, dict]] = {}
    if not isinstance(dispatch_assessed_events, list):
        return {}
    for event in dispatch_assessed_events:
        if not isinstance(event, dict):
            continue
        if event.get("scope") != VARIETY_ASSESSED_DISPATCH_SCOPE:
            continue
        task_id = event.get("task_id")
        if task_id is None:
            continue
        ts = event.get("ts")
        if not ts:
            continue
        key = str(task_id)
        try:
            parsed = _parse_ts(ts)
            current = latest.get(key)
            # `>=` is last-wins on an equal instant, matching the snapshot
            # index's value-semantics (see _latest_snapshot_by_task).
            if current is None or parsed >= current[0]:
                latest[key] = (parsed, event)
        except (ValueError, TypeError):
            continue
    return {key: value[1] for key, value in latest.items()}


def extract_final_dispatch_coverage(
    dispatch_site_events: list[dict],
    snapshot_events: list[dict],
    dispatch_assessed_events: list[dict] | None = None,
) -> dict:
    """Q5 coverage terms, carrying the FINAL variety total of each member.

    MEMBERSHIP comes from `dispatch_site_events` and from nothing else. The
    VALUE comes from the latest `task_metadata_snapshot` of the same task.
    So the distribution holds the total as it stands at read time, rather
    than the total as it stood at dispatch.

    The optional third argument adds a SECOND as-dispatched source: the
    dispatch-marked `variety_assessed` events (TOP-LEVEL
    `scope == "dispatch"`), written by the dispatch command prose at the
    Task-B stamp site. It is consulted ONLY on the fallback path, after
    neither the snapshot nor the `dispatch_site` event's own variety
    resolved a total — so a member that resolved before still resolves
    identically, and the new source only rescues members that would
    otherwise land in arm 3. A `None` (or omitted) third argument is the
    legacy two-source shape and behaves byte-identically to it.

    **THE NON-MEMBER EXCLUSION IS STRUCTURAL.** The loop iterates
    `dispatch_site_events`, so a snapshot for a task that no member names is
    unreachable. The snapshot stream holds far more units than the
    `dispatch_site` stream. A REDIRECT that sourced membership there would
    widen the population and break the one-member-means-one-dispatch
    property. This is a property of the iteration, and not a filter that a
    later edit can weaken by accident.

    Returns a dict with TEN keys:

    - `variety_totals` (list[int]) — the FINAL total of each member that
      resolved one. This list IS the distribution.
    - `sites` (int) — `len(dispatch_site_events)`. It counts the EXISTENCE
      of each event, and it is identical to the `total` term of
      `extract_dispatch_coverage`.
    - `malformed` (list[dict]) — the `dispatch_site` events that resolve no
      total on ANY of the three inputs — the latest
      `task_metadata_snapshot`, the event's own `variety`, and the
      dispatch-marked `variety_assessed` event — AND carry a PRESENT
      `variety` key.
    - `fallback_used` (int) — members of which the final value did NOT come
      from a snapshot. State that definition adjacent to the number wherever
      it is reported. IT COUNTS ARMS 2 AND 3 TOGETHER, so an arm-3 member is
      inside this number and has no final value at all.
    - `superseded` (int) — members of which the final TOTAL DIFFERS from the
      total resolved from the `dispatch_site` event's own `variety`, counted
      only where both resolve.
    - `late_stamped` (int) — members that took a snapshot value and carry NO
      `variety` key on their `dispatch_site` event. THE `dispatch_site` EMIT
      CARRIED NO STAMP, and the final value came from the snapshot.
    - `dispatch_malformed` (int) — members that took a snapshot value and
      carry a PRESENT but unresolvable `variety` on their `dispatch_site`
      event. A producer defect that the snapshot value would otherwise hide.
    - `superseded_dimensions_only` (int) — members of which the two totals
      each resolve and are EQUAL, and the two dimension vectors are each
      complete and DIFFER. A revision that moved dimensions and left the
      total where it was.
    - `dimensions_incomparable` (int) — members of which the two totals each
      resolve and are EQUAL, and at least one dimension vector is not
      complete. The vector comparison could not run, so these members are
      REPORTED rather than absorbed into the agreeing population.
    - `total_unresolved` (int) — members that resolved NO total on ANY of
      the three inputs. Arm 3 alone. `fallback_used` counts arms 2 and 3 together and
      keeps that union on purpose, so this term is the arm-3 half named
      separately and NOT a split of it. See the nesting statement below,
      because this count is CONTAINED IN `fallback_used` and CONTAINS
      `len(malformed)`.
      **A MISSING VECTOR AND A JUNK VECTOR ARE MERGED HERE ON PURPOSE, AND
      THAT IS THE OPPOSITE CHOICE FROM `late_stamped` AGAINST
      `dispatch_malformed`, SO THE GROUND IS STATED.** Those two report a
      PRODUCER state, where the cause IS the remedy: an absent stamp is
      stamped and a junk stamp is repaired at its writer. This one reports
      the REACH OF AN INSTRUMENT. It answers how many members the vector
      comparison could not reach, and a reader acts on it by re-examining
      the comparison rather than the stamp, which is one action whatever the
      cause. Revisit the merge if this counter fills, because a reader who
      must act on a non-zero value will then want the cause.

    **WHY `late_stamped` AND `dispatch_malformed` ARE TWO NUMBERS AND NOT
    ONE.** An ABSENT stamp and a PRESENT-but-unresolvable one are different
    findings with opposite remedies, and merging them is what the `malformed`
    term already exists to prevent on the fallback path. One counter across
    the two would re-merge them on the snapshot path, so the split here
    mirrors the split there.

    **ARM 1 PARTITIONS, AND HERE IS THE ARGUMENT. THE FALLBACK-PATH TERMS DO
    NOT PARTITION, AND THE NESTING STATEMENT BELOW IS WHERE THEY ARE STATED.**
    A member reaches arm 1 or it does not, and `fallback_used` counts exactly
    the members that do not, so `fallback_used` shares no member with the
    five arm-1 counters. Arm 1 then partitions SIX ways with no overlap,
    keyed first on the value on the `dispatch_site` event:

      1. it does not resolve and no `variety` key is present → `late_stamped`
      2. it does not resolve and a `variety` key is present → `dispatch_malformed`
      3. it resolves and the totals DIFFER                  → `superseded`
      4. it resolves, the totals agree, a vector is partial → `dimensions_incomparable`
      5. it resolves, the totals agree, vectors differ      → `superseded_dimensions_only`
      6. it resolves, the totals agree, vectors agree       → counted nowhere

    Cells 1 and 2 need the `dispatch_site` value to be unresolved and
    cells 3 to 6 need it to resolve, so no member reaches both groups. Cells 1 and 2 split
    on `"variety" in event`, which is one predicate with two outcomes. Cell 3
    needs the totals DIFFERENT and cells 4 to 6 need them EQUAL. Cells 4 and 5
    split on whether the two vectors are each complete. So a reader can add
    `superseded` and `superseded_dimensions_only` for a corrections count
    without a double count, and `malformed` stays a fallback-path term that
    shares no member with any of the five.

    **THE VECTOR COMPARISON DOES NOT REACH THE MOST FREQUENT CORRECTION
    CLASS, AND A READER MUST NOT TAKE THESE COUNTS FOR A CORRECTION COUNT.**
    A RATIONALE-ONLY CORRECTION IS STRUCTURALLY INVISIBLE HERE. The
    `dispatch_site` projection carries the four dimensions and their total
    and DROPS the `*_rationale` strings by design, so a corrected rationale
    has nothing on the dispatch side to compare against. Field evidence says
    rationales are corrected MORE frequently than totals, so the class these
    counters cannot see is larger than the class they can. Report
    `superseded` and `superseded_dimensions_only` as what they are, a count
    of corrections that moved a total or a dimension, and never as the number
    of corrections that occurred.

    **THE TWO ACCESSORS DIFFER, and that is the trap in this join.** A
    `dispatch_site` event holds the stamp at the TOP level. A snapshot holds
    it NESTED at `metadata.variety`. The snapshot side passes `metadata` as
    the second argument to `resolve_variety_total`, which keeps the
    non-canonical `metadata["variety_score"]` candidate reachable. The
    dispatch side passes no second argument, so that candidate is
    unreachable there.

    **THE CAUSE THAT MAKES THAT SAFE IS THE EMITTER PAYLOAD SHAPE, AND NOT
    THE RESOLVER ARGUMENT.** `_emit_dispatch_site` in `task_lifecycle_gate`
    builds its payload as a `task_id` plus an OPTIONAL `variety`, and it
    writes NO `metadata` key of any kind. So there is no input on the
    dispatch side for that candidate to read, and the asymmetry is INERT
    rather than merely tolerable. Read this paragraph before you change the
    call: if a `metadata` key is ever added to the `dispatch_site` payload,
    the protection stated here is gone and the two sides diverge for real.

    **A SECOND ASYMMETRY IS LIVE, AND IT IS THE ONE WITH A REACHABLE INPUT.**
    `_dispatch_site_variety` projects only `DISPATCH_VARIETY_KEYS`, the four
    dimensions plus the total, and its own docstring says a non-canonical
    `score` key is a legal candidate that the projection DELIBERATELY DROPS.
    So the `variety["score"]` candidate is unreachable on the dispatch side
    and reachable on the snapshot side. A task stamped with `score` alone and
    no `total` therefore emits a `dispatch_site` carrying NO `variety` key at
    all, and its snapshot resolves a value. That member is counted in
    `late_stamped`, which is the correct cell for it, and this note is here
    so a reader meets the cause rather than infers a defect.

    **THE FALLBACK, in order:**

    1. The latest snapshot value resolves. Use it. It is the FINAL value.
    2. It does not resolve, and the `dispatch_site` value resolves. Use the
       `dispatch_site` value and add one to `fallback_used`.
    2b. Neither resolves, and the task's dispatch-marked `variety_assessed`
       event resolves (third argument only). Use that value; the member
       still adds one to `fallback_used` — the value is as-dispatched, not
       the snapshot's final.
    3. None resolves. The member contributes no value, and add one to
       `fallback_used`.

    `fallback_used` counts arms 2 AND 3 together. Arm 2 alone answers a
    narrower question and keeps arm 3 unobserved, which is the failure this
    counter is here to prevent.

    **`fallback_used` IS DISJOINT FROM EACH ARM-1 COUNTER BY CONSTRUCTION.**
    A member that took the fallback did not reach arm 1, so it cannot reach
    `superseded`, `late_stamped`, `dispatch_malformed`,
    `superseded_dimensions_only` or `dimensions_incomparable`. The six-cell
    argument above states the rest of the partition.

    **THE RETURNED SET IS A PARTITION PLUS A NESTING, AND A READER CANNOT
    TREAT ALL TEN KEYS ALIKE.** The six arm-1 cells PARTITION arm 1, so their
    counts add. The three fallback-path counts NEST, so their counts do not:

        `fallback_used`  CONTAINS  `total_unresolved`  CONTAINS  `malformed`

    - `fallback_used` minus `total_unresolved` is the ARM-2 count, the
      members that fell back to a usable as-dispatched value — the
      `dispatch_site` event's own variety, or (third argument only) the
      task's dispatch-marked `variety_assessed` event.
    - `total_unresolved` minus `len(malformed)` is the arm-3 members that
      carry NO `variety` key, which is the honest un-stamped dispatch on the
      fallback path.

    **A READER WHO ADDS `fallback_used` AND `total_unresolved` DOUBLE-COUNTS**
    and gets a plausible total with no error to warn them. Report the two as
    a containment and never as an addition.

    **MALFORMED IS CLASSIFIED BY THE VALUE THE JOIN FINALLY USES.** After
    arm 3, a member is malformed if and only if `variety` is present on its
    `dispatch_site` event. With the third argument, a member whose
    `dispatch_site` variety is present-but-junk but whose dispatch-marked
    `variety_assessed` event resolves a total LEAVES the malformed list —
    the join resolved a value, so it no longer reports a data-quality
    finding on the stream it did not use. ONE CELL READS DIFFERENTLY FROM A
    CAREFUL HUMAN: a member with NO `variety` on its `dispatch_site` event,
    and a present-but-unresolvable one on its latest snapshot, reports an
    absent stamp rather than a data-quality defect. Revisit that cell if it
    fills, rather than pre-solve it with a third rule.

    **TWO PRECONDITIONS THIS FUNCTION CANNOT CHECK, so the caller owns
    them.** The lists must be scoped to the SAME arc with the SAME
    `--since` value, because the platform reuses task ids across arcs (the
    third list included — an unscoped per-dispatch event for a task id a
    prior arc used would join to this arc's member). And `snapshot_events`
    must arrive in journal order, because the tie-break is positional.

    **A STATED LIMIT: THE VALUE IS THE LATEST USABLE SNAPSHOT, NOT THE LATEST
    SNAPSHOT.** `_latest_snapshot_by_task` skips a snapshot on THREE causes: a
    missing `ts`, an unparseable `ts`, and a `ts` that PARSES and does not
    COMPARE, which is the naive-against-aware TypeError that function names.
    The ground covers all three: an event with no COMPARABLE instant has no
    position in the order this function sorts by. So if a task carries a NEWER
    snapshot that the skip removed, an EARLIER one supplies the value and the
    result is reported as final. NO COUNTER REPORTS THAT, and `fallback_used`
    does not, because a snapshot DID resolve.

    THIS LIMIT IS STATED RATHER THAN COUNTED, AND THE CHOICE HAS A GROUND. A
    counter here can report SKIPS and cannot report STALENESS, because a
    skipped event cannot be ORDERED against the one used and cannot be shown
    to be newer. A skipped event can equally be OLDER, in which case nothing is
    stale, so such a counter over-reports. A reader who meets a skip count
    adjacent to this paragraph reads it as a staleness count, which is worse
    than the limit stated plainly. Journal POSITION is an order this module
    uses, and only as a TIE-BREAK between equal instants: making position the
    primary order for one counter would put a second ordering rule into a
    function that has one, and a reader could not tell which rule produced
    the number. A counter named `snapshots_skipped_unusable_ts` was
    considered for this and DECLINED on those grounds.

    **ONE MEMBER FOR EACH DISPATCH IS A GUARANTEE THIS FUNCTION DOES NOT
    MAKE.** The loop iterates `dispatch_site_events`, so two events naming the
    same task contribute their final value TWICE and can count twice in
    `superseded`. What prevents that is NOT here: `_emit_dispatch_site` in
    `hooks/task_lifecycle_gate.py` takes an O_EXCL marker claim on
    `(team_name, task_id)` at step 7 of its ladder, so a repeated
    owner-bearing write yields one site rather than two. That claim is in a
    different file and this function inherits it. If it is ever relaxed, the
    double count appears here and no term in the returned dict names it.

    Pure function, and it does not raise. A non-list `dispatch_site_events`
    yields the empty result. A non-dict element counts as a site and joins
    to nothing, which mirrors `extract_dispatch_coverage`.
    """
    if not isinstance(dispatch_site_events, list):
        return {
            "variety_totals": [],
            "sites": 0,
            "malformed": [],
            "fallback_used": 0,
            "superseded": 0,
            "late_stamped": 0,
            "dispatch_malformed": 0,
            "superseded_dimensions_only": 0,
            "dimensions_incomparable": 0,
            "total_unresolved": 0,
        }

    latest_snapshots = _latest_snapshot_by_task(snapshot_events)
    latest_dispatch_assessed = _latest_dispatch_assessed_by_task(
        dispatch_assessed_events
    )

    variety_totals: list[int] = []
    malformed: list[dict] = []
    fallback_used = 0
    superseded = 0
    late_stamped = 0
    dispatch_malformed = 0
    superseded_dimensions_only = 0
    dimensions_incomparable = 0
    total_unresolved = 0

    for event in dispatch_site_events:
        site_value = None
        snapshot_value = None
        assessed_value = None
        has_variety = False
        site_vector = None
        snapshot_vector = None

        if isinstance(event, dict):
            # Membership is tested before resolution, so an ABSENT stamp and
            # a PRESENT-but-unresolvable one stay different findings.
            has_variety = "variety" in event
            site_variety = event.get("variety")
            site_value = resolve_variety_total(site_variety)
            site_vector = _dimension_vector(site_variety)
            task_id = event.get("task_id")
            if task_id is not None:
                snapshot = latest_snapshots.get(str(task_id))
                if snapshot is not None:
                    metadata = snapshot.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    snapshot_variety = metadata.get("variety")
                    # The snapshot nests the stamp, and `metadata` is the
                    # resolver's second argument, so candidate 3 stays live.
                    snapshot_value = resolve_variety_total(
                        snapshot_variety, metadata
                    )
                    snapshot_vector = _dimension_vector(snapshot_variety)
                assessed = latest_dispatch_assessed.get(str(task_id))
                if assessed is not None:
                    assessed_value = resolve_variety_total(
                        assessed.get("variety")
                    )

        # Arm 1: the snapshot carries the FINAL value. The six cells below
        # are the partition the docstring argues; each member reaches one.
        if snapshot_value is not None:
            variety_totals.append(snapshot_value)
            if site_value is None:
                # The dispatch contributed no total. Which of the two
                # findings this is depends on the KEY, not on the value:
                # an absent stamp and an unresolvable one have opposite
                # remedies, so they are counted apart.
                if has_variety:
                    dispatch_malformed += 1
                else:
                    late_stamped += 1
            elif site_value != snapshot_value:
                superseded += 1
            elif site_vector is None or snapshot_vector is None:
                # The totals agree and no vector comparison can run. Counted
                # rather than absorbed, because an uncounted member here
                # would read as agreement it was not measured to have.
                dimensions_incomparable += 1
            elif site_vector != snapshot_vector:
                superseded_dimensions_only += 1
            continue

        # Arms 2, 2b and 3: the final value did NOT come from a snapshot.
        fallback_used += 1
        if site_value is not None:
            variety_totals.append(site_value)
        elif assessed_value is not None:
            # ARM 2b (third argument only). The dispatch-marked
            # `variety_assessed` event resolved a total where the
            # `dispatch_site` event's own variety did not. The member still
            # counts as fallback: the value is as-dispatched, not the
            # snapshot's final one.
            variety_totals.append(assessed_value)
        else:
            # ARM 3. No total resolved on ANY stream. `fallback_used`
            # keeps the arms-2-and-3 union, and this names the arm-3 half
            # separately, so it is CONTAINED IN that union and never added
            # to it. `malformed` is the subset of these that carries a
            # present `variety` key, so it nests one level further in.
            total_unresolved += 1
            if has_variety:
                malformed.append(event)

    return {
        "variety_totals": variety_totals,
        "sites": len(dispatch_site_events),
        "malformed": malformed,
        "fallback_used": fallback_used,
        "superseded": superseded,
        "late_stamped": late_stamped,
        "dispatch_malformed": dispatch_malformed,
        "superseded_dimensions_only": superseded_dimensions_only,
        "dimensions_incomparable": dimensions_incomparable,
        "total_unresolved": total_unresolved,
    }


def resolve_arc_start(
    variety_assessed_events: list[dict],
    feature_task_id: str,
) -> str | None:
    """Resolve the current arc's start timestamp for `--since` scoping.

    Returns the LATEST `ts` among `variety_assessed` events whose `task_id`
    matches `feature_task_id`. The platform REUSES low task_ids across arcs,
    so the current feature's id can also match a PRIOR arc's
    `variety_assessed`; the latest-ts match is a candidate boundary, not
    current-arc provenance by itself. Returns None when no matching
    `variety_assessed` exists or when `feature_task_id` is None. None means
    the boundary is unavailable to the caller: current-arc metric readers
    must stop rather than omit `--since` and relabel the whole journal.

    Scope boundary (comPACT/rePACT-led arcs): feature-level
    `variety_assessed` events are written by three commands — the
    orchestrate feature assessment, the comPACT feature task, and the
    rePACT sub-feature task — so this RETURNS the event ts for a
    comPACT/rePACT feature id (returning None there held only while
    orchestrate was the sole writer). That never mis-scopes the
    retrospective: the wrap-up Q5/Q6 retrospective runs only against an
    orchestrate feature assessment (a comPACT workflow does not invoke the
    retrospective, and wrap-up skips trivial single-comPACT sessions), so
    no retro-path caller passes a comPACT/rePACT feature id. In a
    resumed comPACT-then-orchestrate session the wrap-up's feature id is the
    orchestrate feature, whose `variety_assessed` anchors `--since` and
    excludes the prior comPACT arc's events by ts.

    Timestamps are PARSED for the max, never lexically compared: `make_event`
    stamps `ts` as `...Z` while `canonical_since()` emits `...+00:00`, and a
    lexical compare across the two is wrong (`'+'` 0x2B sorts before `'Z'`
    0x5A). The normalize-and-parse is the module-level `_parse_ts`, a local
    copy rather than an import of `session_journal._parse_ts`, which keeps
    this module decoupled. It is ONE implementation with two call sites in
    this module, so the extract-a-shared-util trigger does not fire. The RETURN
    value is the original `ts` STRING of the latest event, so the caller
    passes it to `--since`, which `_ts_ge` re-parses. The parse AND the
    max-comparison both run inside one try/except, so an entry that is
    unparseable OR un-comparable (e.g. a parseable-but-naive ts compared
    against an aware one → TypeError) is skipped (fail-open), never raised.
    If no matching, usable entry remains → None.

    task_id matching is str-normalized (`str(event task_id) == str(feature
    task_id)`) so a future bare-int `variety_assessed` emit still matches a
    str feature_task_id. A None feature id returns before iteration, and an
    event whose task_id is None is skipped before normalization, so neither
    can alias the other. The string id "None" remains an ordinary id.

    arc_start relies on `variety_assessed` being emitted exactly once per
    arc per feature (writers: the orchestrate feature assessment, the
    comPACT feature task, the rePACT sub-feature task — each stamps its
    feature-level event once, immediately after the metadata stamp). If a
    future change ever re-emits it mid-arc for the same feature_task_id,
    switch from latest-ts to earliest-after-prior-arc-boundary — latest-ts
    would otherwise push arc_start forward and drop early-arc dispatches.

    Dispatch-marked events are excluded by the TOP-LEVEL `scope` field
    (VARIETY_ASSESSED_DISPATCH_SCOPE): per-dispatch mirrors carry the
    DISPATCH task's id, which never equals the feature task id within a
    session, so the id filter below already excludes them — the scope
    check is belt-and-braces that keeps the exactly-once invariant
    structural rather than incidental on task-id distinctness. A legacy
    event with no `scope` field remains feature-level by construction.

    Pure function — no disk reads, no mutation.
    """
    latest_ts: str | None = None
    latest_dt: datetime | None = None
    if feature_task_id is None:
        return None
    for event in variety_assessed_events:
        if event.get("scope") == VARIETY_ASSESSED_DISPATCH_SCOPE:
            continue
        task_id = event.get("task_id")
        if task_id is None:
            continue
        if str(task_id) != str(feature_task_id):
            continue
        ts = event.get("ts")
        if not ts:
            continue
        try:
            dt = _parse_ts(ts)
            # The comparison is INSIDE the try (mirroring _ts_ge): comparing
            # a parseable-but-naive ts against an aware one raises TypeError
            # — fail open (skip the entry) instead of crashing the read.
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
                latest_ts = ts
        except (ValueError, TypeError):
            continue
    return latest_ts


def count_emit_skips(skip_events: list[dict]) -> dict:
    """Count `journal_emit_skipped` events by skipped type and cause.

    Consumer-side counting, deliberately. The hooks that record these run as a
    SEPARATE OS PROCESS PER TOOL CALL, so an in-process tally resets every
    invocation and can never deliver a session total — the durable store is
    the journal, and the count happens here at read time.

    Returns `{"total": int, "by_type": {...}, "by_cause": {...}}`. `by_cause`
    keeps RAISED separate from RETURNED-FALSE: a writer defect and a schema
    rejection have different remedies, and reporting a single "failed" number
    rebuilds the ambiguity that capturing the bool removed.
    """
    by_type: dict = {}
    by_cause: dict = {}
    total = 0
    if not isinstance(skip_events, list):
        return {"total": 0, "by_type": by_type, "by_cause": by_cause}
    for ev in skip_events:
        if not isinstance(ev, dict):
            continue
        skipped = ev.get("skipped_type")
        cause = ev.get("cause")
        if not isinstance(skipped, str) or not skipped:
            continue
        total += 1
        by_type[skipped] = by_type.get(skipped, 0) + 1
        if isinstance(cause, str) and cause:
            by_cause[cause] = by_cause.get(cause, 0) + 1
    return {"total": total, "by_type": by_type, "by_cause": by_cause}
