"""
GC-immune regression suite for the wrap-up Q5 coverage denominator + the
coverage-exceeds-unity tripwire (epic #972, children #971/#963).

This file is the dedicated home for the pure helpers in
shared/variety_divergence.py that back the §4 Orchestration Retrospective
Q5 (variety divergence):

  - extract_dispatch_coverage(dispatch_site_events) — derives BOTH Q5 terms
    from ONE pass over ONE list. The DENOMINATOR is the existence of each
    `dispatch_site` event; the NUMERATOR is the resolvable stamps within
    those same events. Because every element of `variety_totals` comes from
    an event that also incremented `total`, coverage > 1.0 is structurally
    impossible rather than merely unobserved.

  - the coverage_exceeds_unity early-return in compute_variety_divergence —
    a non-clamping advisory tripwire: when stamped > total it returns
    reason="coverage_exceeds_unity", surfaced=False, coverage left UNCLAMPED.
    It is now UNREACHABLE from the Q5 path (see above) and is retained only
    as defense-in-depth for a caller that assembles the two terms by hand.

The predecessor denominator — a 3-argument helper counting
agent_dispatch + review_dispatch.reviewers + deduped remediation — was
RETIRED when the denominator moved to the one-event topology. Its tests are
deleted rather than ported: they encoded 3-marker dedup semantics that have
no referent here, and porting them would have manufactured meaning. The
properties that survive the change of subject are re-expressed below and in
test_session_journal.py (arc-scoping), NOT dropped.

Non-vacuity: extract_dispatch_coverage is a NET-NEW symbol, so a source-only
revert removes it entirely (ImportError / collection error, not a clean
assertion fail). The behavioural guards that DO fail cleanly are the
absent-vs-malformed partition and the structural `stamped <= total` bound,
both asserted over arbitrary input below.

GC-immune: every fixture is a synthetic journal event built via
session_journal.make_event — zero dependence on the GC-reaped task store.
"""


import pytest

from shared.session_journal import make_event  # noqa: E402
from shared.variety_divergence import (  # noqa: E402
    _latest_snapshot_by_task,
    _parse_ts,
    compute_variety_divergence,
    extract_dispatch_coverage,
    extract_final_dispatch_coverage,
    resolve_arc_start,
)


# =============================================================================
# Fixture builders — faithful journal-event shapes (see
# _REQUIRED_FIELDS_BY_TYPE / _OPTIONAL_FIELDS_BY_TYPE in session_journal.py)
# =============================================================================

_TS = "2026-06-15T12:00:00Z"


class TestExtractDispatchCoverage:
    """The replacement denominator. Both Q5 terms, one pass, one list.

    These are the behavioural guards that replace the retired 3-marker
    tests. The partition they pin — ABSENT stamp vs PRESENT-but-unresolvable
    stamp — is the one thing the old helper had no concept of, because a
    marker either existed or it did not.
    """

    STAMPED = {"task_id": "1", "variety": {"total": 9}}
    ABSENT = {"task_id": "2"}
    MALFORMED = {"task_id": "3", "variety": {"total": "junk"}}
    DIMENSION_SUM = {
        "task_id": "4",
        "variety": {"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 2},
    }

    def test_denominator_counts_event_existence_not_stamp_presence(self):
        """The whole point of the one-event topology: an un-stamped dispatch
        is still a dispatch SITE, so it lowers coverage instead of vanishing
        from the denominator along with its stamp."""
        _totals, total, _malformed = extract_dispatch_coverage(
            [self.STAMPED, self.ABSENT, self.MALFORMED]
        )
        assert total == 3

    def test_absent_stamp_is_a_coverage_gap_not_a_malformed_stamp(self):
        """An absent stamp is an honest un-stamped dispatch — the gap
        coverage exists to surface. Folding it into `malformed` would leave
        the ratio unchanged while reporting a normal gap as a producer
        defect, which trains readers to ignore the malformed signal."""
        totals, total, malformed = extract_dispatch_coverage([self.ABSENT])
        assert totals == []
        assert total == 1
        assert malformed == [], (
            "a MISSING stamp and a MALFORMED stamp have different remedies "
            "and must never be merged"
        )

    def test_present_but_unresolvable_stamp_is_malformed(self):
        totals, total, malformed = extract_dispatch_coverage([self.MALFORMED])
        assert totals == []
        assert total == 1
        assert malformed == [self.MALFORMED]

    def test_dimension_sum_recovery_counts_as_stamped(self):
        """A stamp recovered through a non-canonical resolver candidate IS
        resolved; which candidate won is not this consumer's business.
        Counting it as malformed would record the dimension-sum recovery —
        20 events across 8 sessions — as a data-quality problem, inverting
        the fix that introduced it."""
        totals, _total, malformed = extract_dispatch_coverage(
            [self.DIMENSION_SUM]
        )
        assert totals == [8]
        assert malformed == []

    def test_one_unresolvable_stamp_costs_only_its_own_row(self):
        """Totality: the extraction is defined for every event the journal
        can hand it, so a single bad stamp cannot destroy the whole list."""
        totals, total, malformed = extract_dispatch_coverage(
            [self.STAMPED, self.MALFORMED, self.DIMENSION_SUM, self.ABSENT]
        )
        assert totals == [9, 8]
        assert total == 4
        assert len(malformed) == 1

    @pytest.mark.parametrize(
        "hostile",
        [None, "string", 42, {"not": "a list"}, object()],
    )
    def test_non_list_input_yields_the_empty_triple(self, hostile):
        assert extract_dispatch_coverage(hostile) == ([], 0, [])

    def test_non_dict_element_is_a_site_with_no_stamp(self):
        """It existed, so it counts as a site; it carries nothing resolvable.
        Never raises — this runs inside a wrap-up the session depends on."""
        totals, total, malformed = extract_dispatch_coverage(
            ["not-a-dict", None, self.STAMPED]
        )
        assert totals == [9]
        assert total == 3
        assert malformed == []


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

    @pytest.mark.parametrize(
        "events",
        [
            pytest.param([], id="empty"),
            pytest.param([{"task_id": "1"}], id="one_unstamped"),
            pytest.param(
                [{"task_id": "1", "variety": {"total": 9}}], id="one_stamped"
            ),
            pytest.param(
                [
                    {"task_id": "1", "variety": {"total": 9}},
                    {"task_id": "2"},
                    {"task_id": "3", "variety": {"total": "junk"}},
                    {"task_id": "4", "variety": {"n": 1}},
                    "not-a-dict",
                ],
                id="mixed_including_hostile",
            ),
        ],
    )
    def test_advisory_is_unreachable_from_the_real_helper(self, events):
        """Complement, strengthened by the one-event topology: over terms
        produced by extract_dispatch_coverage the advisory can NEVER fire,
        because every stamped event also incremented the total. The old
        version proved zero-residual on ONE fixture; this proves it
        structurally, including on hostile input."""
        variety_totals, total, _malformed = extract_dispatch_coverage(events)
        assert len(variety_totals) <= total, (
            "coverage > 1.0 must be structurally impossible, not merely "
            "unobserved — a stamped event that did not increment the total "
            "would mean the two terms came from different populations"
        )
        result = compute_variety_divergence(6, variety_totals, total)
        assert result["reason"] != "coverage_exceeds_unity"
        assert result["coverage"] <= 1.0

    def test_advisory_silent_at_exact_unity_boundary_within_threshold(self):
        """The advisory fires on stamped > total (STRICT). At the exact
        boundary stamped == total (coverage EXACTLY 1.0) it must NOT fire —
        the normal divergence path runs. feature=8, dispatch=[8,8], total=2:
        mean=8, delta=0 → within_threshold, coverage 2/2=1.0. This pins the
        strict-`>` semantics; under a `>`→`>=` regression the advisory would
        fire here (reason='coverage_exceeds_unity') and this test fails."""
        result = compute_variety_divergence(
            feature_variety=8,
            dispatch_varieties=[8, 8],  # stamped = 2
            total_pact_dispatch_count=2,  # total = 2  → stamped == total
        )
        assert result["coverage"] == pytest.approx(1.0)
        assert result["reason"] != "coverage_exceeds_unity"
        assert result["reason"] == "within_threshold"
        assert result["surfaced"] is False

    def test_advisory_at_unity_boundary_does_not_suppress_real_divergence(self):
        """The sharper boundary case (the survivor the review found): at
        stamped == total a REAL divergence must still SURFACE — the advisory
        must not pre-empt it. feature=4, dispatch=[8,8], total=2: coverage 1.0,
        mean 8, delta 4 ≥ 2 → SURFACED undershot. A `>`→`>=` regression fires
        the advisory here (surfaced=False, reason='coverage_exceeds_unity'),
        SUPPRESSING the divergence → this test fails under that mutation."""
        result = compute_variety_divergence(
            feature_variety=4,
            dispatch_varieties=[8, 8],  # stamped = 2
            total_pact_dispatch_count=2,  # stamped == total
        )
        assert result["coverage"] == pytest.approx(1.0)
        assert result["surfaced"] is True
        assert result["reason"] is None
        assert result["direction"] == "undershot"


# =============================================================================
# resolve_arc_start — the --since arc-boundary resolver (#963)
# =============================================================================


class TestDenominatorOmittedIsTheLegacyFailOpen:
    """The DEFAULT of `total_pact_dispatch_count` is a supported input.

    The docstring of `compute_variety_divergence` calls a None denominator
    the legacy path, and says it fails open to the all-stamped assumption
    rather than tripping the advisory. NO OTHER ARM IN THIS FILE CALLS THAT
    FUNCTION WITHOUT A DENOMINATOR, so the default value it ships with was
    unexercised.

    WHY A DEFAULT ARGUMENT IS WORTH ITS OWN ARM. Every caller that omits the
    parameter takes this path, and a caller that omits it writes nothing at
    the call site for a reader to notice. The branch is reached by SILENCE,
    which is the hardest kind of reach to remember to test.
    """

    def test_an_omitted_denominator_fails_open_and_does_not_divide(self):
        """Call it the way a legacy caller does, with the argument left out.

        The three assertions are one each on the three things the fail-open
        branch decides. `total` stays None, so the absent denominator is
        REPORTED rather than invented. `coverage` is 1.0, the all-stamped
        assumption. `surfaced` is False, so the advisory does not trip.

        THIS ARM ALSO GUARDS THE LADDER SHAPE ABOVE IT. The fail-open branch
        and the zero-denominator branch are one `if`/`elif`/`else` chain. If
        that `elif` became an `if`, the trailing `else` would rebind to it,
        and a None denominator would take the fail-open branch AND THEN reach
        the division, which raises a TypeError rather than returning at all.
        """
        result = compute_variety_divergence(8, [9, 8])
        assert result["total"] is None
        assert result["coverage"] == 1.0
        assert result["surfaced"] is False


class TestResolveArcStart:
    """resolve_arc_start(variety_assessed_events, feature_task_id): the LATEST
    variety_assessed.ts matching feature_task_id (the arc-start for --since),
    parse-not-lexical, fail-open None. Pure function, journal-event inputs."""

    def _va(self, task_id, ts):
        return make_event(
            "variety_assessed",
            task_id=task_id,
            variety={
                "novelty": 2,
                "scope": 2,
                "uncertainty": 2,
                "risk": 2,
                "total": 8,
            },
            ts=ts,
        )

    def test_returns_latest_ts_for_matching_feature_across_arcs(self):
        """The platform reuses task_ids across arcs, so the feature's id can
        match a PRIOR arc's variety_assessed too; the LATEST-ts match is the
        current arc. (Non-vacuity: a latest→earliest mutation returns the
        prior-arc ts and fails this.)"""
        events = [
            self._va("100", "2026-06-13T12:00:00Z"),  # prior arc
            self._va("100", "2026-06-14T12:00:00Z"),  # current arc
        ]
        assert resolve_arc_start(events, "100") == "2026-06-14T12:00:00Z"

    def test_filters_by_feature_task_id_ignoring_other_features(self):
        """A LATER variety_assessed for a DIFFERENT feature must NOT leak in —
        returns the matching feature's latest, not the global latest. (Proves
        the task_id filter is load-bearing.)"""
        events = [
            self._va("100", "2026-06-14T12:00:00Z"),
            self._va("200", "2026-06-15T12:00:00Z"),  # later, different feature
        ]
        assert resolve_arc_start(events, "100") == "2026-06-14T12:00:00Z"

    def test_dispatch_marked_event_for_same_id_does_not_move_arc_start(self):
        """A per-dispatch mirror (TOP-LEVEL scope="dispatch") for the SAME
        task id, landing AFTER the feature-level event, must NOT push
        arc_start forward — the scope filter excludes it before the
        latest-ts selection. This is the belt-and-braces half: the id
        filter already keeps per-dispatch events out (they carry the
        dispatch task's id), and this test pins the structural guarantee
        for the world where an emission mistakenly keys the feature id."""
        feature = self._va("100", "2026-06-14T12:00:00Z")
        marked = make_event(
            "variety_assessed",
            task_id="100",
            scope="dispatch",
            variety={"total": 4},
            ts="2026-06-15T12:00:00Z",
        )
        assert resolve_arc_start([feature, marked], "100") == (
            "2026-06-14T12:00:00Z"
        )

    def test_field_absent_event_remains_feature_level(self):
        """Legacy polarity: an event with NO scope field is feature-level.
        The latest field-absent match still wins, so a legacy journal
        renders identically to the pre-discriminator behavior."""
        events = [
            self._va("100", "2026-06-13T12:00:00Z"),
            self._va("100", "2026-06-14T12:00:00Z"),
        ]
        assert resolve_arc_start(events, "100") == "2026-06-14T12:00:00Z"

    def test_cross_format_compared_by_instant_returns_original_string(self):
        """Cross-format (prior arc in +00:00, current in Z) is compared by
        PARSED instant; the RETURN is the original ts STRING of the max (passed
        verbatim to --since, which _ts_ge re-parses)."""
        events = [
            self._va("100", "2026-06-14T00:00:00+00:00"),  # earlier
            self._va("100", "2026-06-14T12:00:00Z"),  # later (current)
        ]
        assert resolve_arc_start(events, "100") == "2026-06-14T12:00:00Z"

    def test_equal_instant_tie_keeps_the_first_seen_event(self):
        """Two feature-level events for the SAME feature id at the IDENTICAL
        parsed instant (a realistic tie: journal ts stamps at second
        granularity) resolve to the FIRST-SEEN event's original ts string,
        in either list order — the assertion over BOTH orders is what proves
        first-seen rather than any string-form preference.

        This pin documents CURRENT behavior, not a designed requirement:
        the strict `>` keeps the first of two equal instants, the
        boundary-selection direction (an arc boundary is the earliest edge
        of a tied cluster), matching the session_state feature selector and
        the OPPOSITE of the value-selection ties, which take the later
        element (see `test_equal_instant_tie_break_takes_the_later_element`
        for the snapshot index; `_latest_dispatch_assessed_by_task` shares
        that direction). wrap-up Q5 consumes this helper for `arc_start`,
        so an equal-ts feature pair is deterministic there rather than
        undefined.
        """
        z_form = self._va("100", "2026-06-14T12:00:00Z")
        offset_form = self._va("100", "2026-06-14T12:00:00+00:00")
        assert resolve_arc_start([z_form, offset_form], "100") == (
            "2026-06-14T12:00:00Z"
        )
        assert resolve_arc_start([offset_form, z_form], "100") == (
            "2026-06-14T12:00:00+00:00"
        )

    def test_returns_none_when_no_matching_feature(self):
        events = [self._va("200", "2026-06-14T12:00:00Z")]
        assert resolve_arc_start(events, "100") is None

    def test_none_feature_id_never_aliases_a_missing_task_id(self):
        events = [{
            "type": "variety_assessed",
            "ts": "2026-06-14T12:00:00Z",
            "variety": {"total": 8},
        }]
        assert resolve_arc_start(events, None) is None
        control = self._va("100", "2026-06-15T12:00:00Z")
        assert resolve_arc_start(events + [control], "100") == (
            "2026-06-15T12:00:00Z"
        )

    def test_explicit_none_task_id_cannot_match_another_feature(self):
        events = [{
            "type": "variety_assessed",
            "task_id": None,
            "ts": "2026-06-14T12:00:00Z",
            "variety": {"total": 8},
        }]
        assert resolve_arc_start(events, None) is None
        assert resolve_arc_start(events, "100") is None
        control = self._va("100", "2026-06-15T12:00:00Z")
        assert resolve_arc_start(events + [control], "100") == (
            "2026-06-15T12:00:00Z"
        )

    def test_string_none_remains_an_ordinary_feature_id(self):
        event = self._va("None", "2026-06-14T12:00:00Z")
        assert resolve_arc_start([event], "None") == "2026-06-14T12:00:00Z"
        assert resolve_arc_start([event], None) is None

    def test_returns_none_for_empty_events(self):
        assert resolve_arc_start([], "100") is None

    def test_skips_matching_event_missing_ts_keeps_valid_match(self):
        """A matching event with no `ts` is skipped; a remaining valid match
        wins. (Raw dict — make_event would auto-stamp a ts.)"""
        events = [
            self._va("100", "2026-06-14T12:00:00Z"),
            {"type": "variety_assessed", "task_id": "100", "variety": {"total": 8}},
        ]
        assert resolve_arc_start(events, "100") == "2026-06-14T12:00:00Z"

    def test_returns_none_when_only_match_missing_ts(self):
        events = [
            {"type": "variety_assessed", "task_id": "100", "variety": {"total": 8}},
        ]
        assert resolve_arc_start(events, "100") is None

    def test_returns_none_when_all_matches_unparseable(self):
        """Fail-open: matching entries whose ts is unparseable are skipped; if
        no parseable match remains → None (caller omits --since)."""
        events = [self._va("100", "garbage"), self._va("100", "also-bad")]
        assert resolve_arc_start(events, "100") is None

    def test_mixed_naive_and_aware_ts_does_not_crash_selects_aware_latest(self):
        """Regression: a sequence mixing a tz-AWARE ts ('...Z' → +00:00) with a
        tz-NAIVE-parseable one (a date-only '2026-06-16' → naive datetime via
        fromisoformat) must NOT raise TypeError ('can't compare offset-naive
        and offset-aware datetimes'). The `dt > latest_dt` compare lives INSIDE
        the try/except (mirroring _ts_ge), so the incomparable naive entry is
        SKIPPED, not fatal, and the latest tz-AWARE ts is returned.

        The date-only entry parses cleanly (it is NOT garbage) but is naive, so
        comparing it against an already-set AWARE latest is what raised — a
        parses-OK-but-incompatible probe, distinct from the unparseable case.
        Note the naive '2026-06-16' is date-wise LATER than both aware entries,
        so its being SKIPPED (not selected) is load-bearing: a regression that
        let it through would change the result.

        Structured assert-no-raise (catch → pytest.fail) so a pre-fix revert
        (compare moved back OUTSIDE the try) yields a deterministic FAILED, not
        a runtime ERROR that rtk's error-count-dropping summary could hide."""
        events = [
            self._va("100", "2026-06-14T12:00:00Z"),  # aware
            self._va("100", "2026-06-16"),  # naive (date-only) — the crasher
            self._va("100", "2026-06-15T12:00:00Z"),  # aware, the latest aware
        ]
        try:
            result = resolve_arc_start(events, "100")
        except TypeError as exc:  # pragma: no cover - the regression we guard
            pytest.fail(
                "resolve_arc_start raised on a mixed naive+aware ts sequence "
                f"(the try-scope regression resurfaced): {exc}"
            )
        assert result == "2026-06-15T12:00:00Z"


class TestExtractFinalDispatchCoverage:
    """The Q5 join: MEMBERSHIP from `dispatch_site`, VALUE from the latest
    `task_metadata_snapshot` of the same task.

    The defect this replaces: Q5 read the AS-DISPATCHED total, so a stamp
    revised after the dispatch was invisible to the calibration figure.

    Non-vacuity: `extract_final_dispatch_coverage` is a NET-NEW symbol, so a
    source-only revert removes it (ImportError at collection, not a clean
    assertion fail). The guards that DO fail cleanly are the non-member
    exclusion, the kept-member fallback, and the disjointness of the two
    counters, each asserted below on values that a broken implementation
    cannot reproduce by accident.
    """

    def _site(self, task_id, variety=None):
        """A `dispatch_site` event. `variety=None` means the key is ABSENT."""
        fields = {"task_id": task_id}
        if variety is not None:
            fields["variety"] = variety
        return make_event("dispatch_site", **fields)

    def _snap(self, task_id, metadata, ts=_TS):
        return make_event(
            "task_metadata_snapshot",
            task_id=task_id,
            metadata=metadata,
            ts=ts,
        )

    # -- The structural exclusion ------------------------------------------

    def test_non_member_snapshots_cannot_enter_the_distribution(self):
        """A snapshot for a task that NO dispatch_site names is unreachable,
        because the loop iterates the member list.

        The extras carry totals DISTINCT from each member value, which is
        what makes this arm able to FAIL: with a shared value, an
        implementation that admits non-members yields a list that looks
        correct. A REDIRECT that sourced membership from the snapshot stream
        would widen the population and break one-member-means-one-dispatch.
        """
        sites = [
            self._site("1", {"total": 9}),
            self._site("2", {"total": 10}),
        ]
        snapshots = [
            self._snap("1", {"variety": {"total": 9}}),
            self._snap("2", {"variety": {"total": 10}}),
            self._snap("90", {"variety": {"total": 15}}),  # non-member
            self._snap("91", {"variety": {"total": 16}}),  # non-member
        ]
        result = extract_final_dispatch_coverage(sites, snapshots)
        assert result["sites"] == 2
        assert len(result["variety_totals"]) <= result["sites"]
        assert sorted(result["variety_totals"]) == [9, 10]
        assert 15 not in result["variety_totals"]
        assert 16 not in result["variety_totals"]

    def test_member_with_no_snapshot_keeps_its_dispatch_site_value(self):
        """The opposite direction. An implementation that DROPS an
        un-snapshotted member passes the exclusion arm above and silently
        shrinks the denominator, which is the failure this design prevents.
        """
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})], []
        )
        assert result["variety_totals"] == [9]
        assert result["sites"] == 1
        assert result["fallback_used"] == 1
        # A MEMBER TAKES ONE CELL, NOT TWO. This member resolves a value, so
        # it belongs in `variety_totals` and NOWHERE else. `malformed` is the
        # cell it must NOT reach, and the assertions above cannot see it
        # arriving there, because they count the cells it DOES reach. A
        # `site_value` resolves only when the `variety` key is present, so
        # EVERY member on this path carries the flag the malformed branch
        # reads, and an `elif` turned into an `if` would report all of them.
        assert result["malformed"] == []
        assert result["superseded"] == 0

    def test_a_fallback_member_reaches_no_snapshot_resolved_cell(self):
        """The snapshot-resolved cells are keyed on the SNAPSHOT, so a member
        with no resolving snapshot is outside all of them.

        ONE MEMBER ALONE WOULD NOT SHOW THIS. A single fallback member only
        proves the five cells read 0; it cannot show the ladder is GATED on
        the snapshot rather than merely not reached by that input. The second
        member makes the counts attributable: it lands in `superseded` (site 5
        against snapshot 7), and that 1 is the whole arm-1 population. A
        ladder evaluated before the snapshot check would compare the fallback
        member's site value against a None snapshot, add a second
        `superseded`, and read 2 here.

        BOTH TOTALS ARE IN RANGE ON PURPOSE. `resolve_variety_total` accepts
        only 4..16, so a total of 3 resolves to None and would silently turn
        the "resolved" member into another fallback member — the fixture
        would then prove the domain claim with no arm-1 member in it at all.
        """
        fallback = self._site("1", {"total": 9})  # no snapshot -> fallback
        resolved = self._site("2", {"total": 5})
        snapshots = [self._snap("2", {"variety": {"total": 7}})]
        result = extract_final_dispatch_coverage([fallback, resolved], snapshots)
        assert result["fallback_used"] == 1
        # The resolved member is the ONLY member in an arm-1 cell.
        assert result["superseded"] == 1
        assert result["late_stamped"] == 0
        assert result["dispatch_malformed"] == 0
        assert result["superseded_dimensions_only"] == 0
        assert result["dimensions_incomparable"] == 0

    # -- Which value wins --------------------------------------------------

    def test_snapshot_value_wins_and_the_member_counts_as_superseded(self):
        """The whole point of the join: the FINAL total, not the
        as-dispatched one. 9 and 11 sit in different bands, so a reader can
        see which value the distribution carries."""
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})],
            [self._snap("1", {"variety": {"total": 11}})],
        )
        assert result["variety_totals"] == [11]
        assert result["superseded"] == 1
        assert result["fallback_used"] == 0

    def test_agreeing_streams_do_not_count_as_superseded(self):
        """`superseded` counts DISAGREEMENT, not the presence of a snapshot.
        Counting each snapshot-sourced member would report the corrected
        defect as universal and make the counter useless."""
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})],
            [self._snap("1", {"variety": {"total": 9}})],
        )
        assert result["variety_totals"] == [9]
        assert result["superseded"] == 0
        assert result["fallback_used"] == 0

    def test_snapshot_reaches_the_nested_variety_score_candidate(self):
        """The snapshot side passes `metadata` as the resolver's SECOND
        argument, so the non-canonical `metadata["variety_score"]` candidate
        stays reachable.

        Non-vacuity: drop that argument and the snapshot resolves nothing,
        the member falls back to 9, and `fallback_used` becomes 1. So the
        two assertions below both move under that mutation.
        """
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})],
            [self._snap("1", {"variety_score": 12})],
        )
        assert result["variety_totals"] == [12]
        assert result["fallback_used"] == 0

    # -- The two counters --------------------------------------------------

    def test_fallback_and_superseded_are_disjoint(self):
        """A member that took the fallback has no second value to compare
        against, so it cannot reach `superseded`. A reader can add the two
        counts together without a double count.

        Member 1 is superseded. Members 2 and 3 take the fallback, one for
        each arm: an unresolvable snapshot, and no snapshot at all.
        """
        sites = [
            self._site("1", {"total": 9}),
            self._site("2", {"total": 10}),
            self._site("3", {"total": 11}),
        ]
        snapshots = [
            self._snap("1", {"variety": {"total": 13}}),
            self._snap("2", {"variety": {"total": "junk"}}),
        ]
        result = extract_final_dispatch_coverage(sites, snapshots)
        assert result["superseded"] == 1
        assert result["fallback_used"] == 2
        assert result["superseded"] + result["fallback_used"] <= result["sites"]
        assert sorted(result["variety_totals"]) == [10, 11, 13]

    def test_fallback_counts_arm_three_where_neither_stream_resolves(self):
        """`fallback_used` is "the final value did NOT come from a
        snapshot", which covers arm 3 as well as arm 2. Counting arm 2 alone
        answers a narrower question and keeps arm 3 unobserved."""
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": "junk"})],
            [self._snap("1", {"variety": {"total": "junk"}})],
        )
        assert result["variety_totals"] == []
        assert result["fallback_used"] == 1
        assert result["superseded"] == 0

    # -- The latest-picker -------------------------------------------------

    def test_latest_snapshot_is_chosen_by_parsed_instant_not_by_position(self):
        """THE NAME MAKES TWO CLAIMS AND THIS FIXTURE PINS EACH OF THEM.

        A NON-UTC OFFSET IS WHAT MAKES THE TWO ORDERS DISAGREE AT UNEQUAL
        INSTANTS. `2026-06-15T00:00:00+02:00` IS the instant
        `2026-06-14T22:00:00Z`, so it is EARLIER than `2026-06-14T23:00:00Z`
        as an instant, and it sorts ABOVE it as a string, because the first
        byte that differs is the day, 5 against 4.

        - PARSED, which is shipped: the Z stamp is later, so the answer is 11.
        - LEXICAL: the offset stamp sorts higher, so the answer is 12.
        - BY POSITION, last element: the answer is 12.
        The parsed winner is placed FIRST, so a positional picker and a
        lexical picker each return 12 and each fails here.

        THE EARLIER FIXTURE SEPARATED NOTHING AND THAT IS WHY IT MOVED. It
        used two stamps a DAY apart in the two formats, where a lexical
        compare and a parsed compare AGREE, so the name claimed a
        discrimination the input could not make. Measured before this repair:
        the lexical mutant left this arm GREEN.

        THIS IS NOT THE SIBLING BELOW. That arm uses EQUAL instants in the
        two formats. This one uses UNEQUAL instants, so it holds when the
        equal-instant case is removed.
        """
        snapshots = [
            self._snap(
                "1", {"variety": {"total": 11}}, ts="2026-06-14T23:00:00Z"
            ),
            self._snap(
                "1", {"variety": {"total": 12}}, ts="2026-06-15T00:00:00+02:00"
            ),
        ]
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})], snapshots
        )
        assert result["variety_totals"] == [11]

    def test_equal_instant_tie_break_takes_the_later_element(self):
        """Journal events stamp `ts` at second granularity, so a tie is
        ordinary. LAST-WINS, because this function picks the FINAL value.
        `resolve_arc_start` keeps the FIRST of two equal instants, and it is
        correct for a different question: an arc BOUNDARY."""
        snapshots = [
            self._snap("1", {"variety": {"total": 11}}, ts=_TS),
            self._snap("1", {"variety": {"total": 12}}, ts=_TS),
        ]
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})], snapshots
        )
        assert result["variety_totals"] == [12]

    def test_unparseable_and_missing_ts_snapshots_are_skipped(self):
        """Fail-open, mirroring `resolve_arc_start`. A member keeps its usable
        snapshot, and a member with NO usable snapshot falls back.

        MEMBER 1 CARRIES ONLY UNREADABLE STAMPS AND THAT IS THE MECHANISM.
        It has no usable competitor, so an implementation that KEEPS a
        skipped entry lets that entry win BY DEFAULT rather than by a date
        comparison, and the member takes 11 or 12 in place of its own 9.
        THE SUBSTITUTE DATE BELONGS TO THE MUTANT, so a fixture that only
        re-dates the junk entries is not robust: a mutant that dates them to
        the epoch loses a comparison against a usable stamp and separates
        nothing. This shape separates for ANY substitute date.

        THE EARLIER FIXTURE SEPARATED NOTHING AND THAT IS WHY IT MOVED. It
        gave one member three snapshots with the usable one LAST AND
        GREATEST, so a kept entry lost the comparison and the answer held at
        13. Measured before this repair: the keeps-the-skipped mutant left
        this arm GREEN.

        MEMBER 2 KEEPS THE MIXED-STREAM PROPERTY the name claims: a readable
        stamp in the same call is used and is not collateral damage.
        """
        sites = [self._site("1", {"total": 9}), self._site("2", {"total": 10})]
        snapshots = [
            self._snap("1", {"variety": {"total": 11}}, ts="not-a-timestamp"),
            self._snap("1", {"variety": {"total": 12}}, ts=""),
            self._snap("2", {"variety": {"total": 13}}, ts=_TS),
        ]
        result = extract_final_dispatch_coverage(sites, snapshots)
        assert sorted(result["variety_totals"]) == [9, 13]
        assert result["fallback_used"] == 1

    # -- Classification ----------------------------------------------------

    def test_absent_stamp_is_a_coverage_gap_and_a_junk_stamp_is_malformed(self):
        """The partition the old helper pins, ON THE FALLBACK PATH ONLY. The
        two findings have opposite remedies and are never merged.

        THE SNAPSHOT LIST IS EMPTY AND THAT IS DELIBERATE. DO NOT GIVE THIS
        ARM A NON-EMPTY ONE. With no snapshots each member takes the
        fallback, so this arm reaches the fallback partition and NOT the
        join. An earlier docstring here claimed the partition is preserved
        "through the join", which its fixture cannot show.

        THE EMPTY LIST IS LOAD-BEARING RATHER THAN INCIDENTAL. This arm is
        the only detector of the rule that `malformed` is classified from the
        value the join FINALLY uses rather than from the `dispatch_site`
        value alone. An implementation that classifies early agrees with a
        correct one for EVERY input that has no snapshots, so only a
        classify-early mutation separates them, and this arm is what catches
        it. Widening the fixture can take that detection to zero and the
        suite would stay green.

        THE JOIN PATH IS COVERED BY ITS OWN SIBLING, which is the arm
        directly below. Add to that one, not to this one.
        """
        absent = self._site("1")
        junk = self._site("2", {"total": "junk"})
        result = extract_final_dispatch_coverage([absent, junk], [])
        assert result["variety_totals"] == []
        assert result["sites"] == 2
        assert result["malformed"] == [junk]
        assert result["fallback_used"] == 2

    def test_a_resolving_snapshot_reports_the_dispatch_side_it_replaced(self):
        """THE SIBLING OF THE ARM ABOVE, on the path where the join RUNS.

        The two members here are the same two shapes as the arm above, an
        ABSENT stamp and a JUNK one, and each now has a snapshot that
        RESOLVES. That is the input the empty-list fixture cannot produce and
        it is where the two implementations diverge.

        The value is taken from the snapshot in each case, so `malformed`
        empties and `fallback_used` falls to zero. WITHOUT THE TWO COUNTERS
        BELOW that is all a reader would see, and a dispatch that carried no
        stamp would be indistinguishable from one that carried a good one.

        EACH SIDE IS ASSERTED. The counters alone would pass against an
        implementation that drops the members entirely, so the resolved
        totals are asserted to land in the distribution as well.
        """
        absent = self._site("1")
        junk = self._site("2", {"total": "junk"})
        absent_two = self._site("3")
        snapshots = [
            self._snap("1", {"variety": {"total": 9}}),
            self._snap("2", {"variety": {"total": 11}}),
            self._snap("3", {"variety": {"total": 7}}),
        ]
        result = extract_final_dispatch_coverage(
            [absent, junk, absent_two], snapshots
        )
        # The positive half: the members are kept and carry snapshot values.
        assert sorted(result["variety_totals"]) == [7, 9, 11]
        assert result["sites"] == 3
        # The join emptied the fallback-path terms.
        assert result["malformed"] == []
        assert result["fallback_used"] == 0
        # THE TWO COUNTS ARE ASYMMETRIC ON PURPOSE, 2 AGAINST 1, AND THE
        # ASYMMETRY IS THE WHOLE POINT. Equal expected values would constrain
        # the MULTISET of the two counts and NOT THE MAPPING from counter to
        # value, so a transposition of the two counters would map 1 and 1 onto
        # 1 and 1 and pass. No additional assertion on these two cells can
        # repair that, because the blindness lives in the VALUES rather than
        # in the number of assertions. With 2 and 1 a transposition reads 1
        # and 2, so this pins WHICH counter carries WHICH finding.
        assert result["late_stamped"] == 2
        assert result["dispatch_malformed"] == 1
        # THE SIX CELLS ARE DISJOINT AND THESE TWO ZEROS ARE WHAT HOLD IT.
        # The arm-1 chain is a single `if`/`elif` ladder, so each member
        # reaches ONE cell. An edit that turns an `elif` into an `if` lets one
        # member land in TWO cells at once, and the counts above would not
        # move, because they count the cells the member DID reach. These two
        # assert the cells it must NOT reach.
        assert result["superseded"] == 0
        assert result["dimensions_incomparable"] == 0

    def test_a_present_but_null_variety_reaches_dispatch_malformed(self):
        """The discriminator is KEY PRESENCE, not the VALUE'S nullability.

        `late_stamped` is "carry NO `variety` key"; `dispatch_malformed` is
        "carry a PRESENT but unresolvable `variety`". A null value is PRESENT,
        so it is malformed. The `_site` helper cannot express this — its
        `variety=None` means the key is ABSENT — so the event is built
        directly, and the assertion below that the key SURVIVES construction
        is what keeps this fixture from silently degrading to the absent case.

        This is the ARM-1 leg: the member has a resolving snapshot, so it
        lands in the dispatch_malformed cell and NOT in late_stamped. The
        divergent assessed value is NOT a comparator: with no resolvable site
        total, the member must stay OUT of superseded as well.
        """
        null_site = make_event("dispatch_site", task_id="1", variety=None)
        assert "variety" in null_site and null_site["variety"] is None

        assessed = [
            make_event(
                "variety_assessed",
                task_id="1",
                scope="dispatch",
                variety={"total": 8},
                ts="2026-06-15T12:00:01Z",
            )
        ]
        result = extract_final_dispatch_coverage(
            [null_site], [self._snap("1", {"variety": {"total": 10}})], assessed
        )
        assert result["dispatch_malformed"] == 1
        assert result["late_stamped"] == 0
        assert result["superseded"] == 0

    def test_a_present_but_null_variety_reaches_the_malformed_list(self):
        """The ARM-3 leg of the same predicate: no snapshot and no assessed
        event, so nothing resolves a total and the member joins the
        `malformed` list on the KEY's presence alone."""
        null_site = make_event("dispatch_site", task_id="1", variety=None)
        assert "variety" in null_site and null_site["variety"] is None

        unresolved = extract_final_dispatch_coverage([null_site], [])
        assert unresolved["malformed"] == [null_site]
        assert unresolved["total_unresolved"] == 1

    def test_a_dimension_revision_that_holds_the_total_is_counted(self):
        """A correction that moved dimensions and left the total is REPORTED.

        This is the shape measured on real session data: a stamp went from
        novelty 2 and risk 3 to novelty 3 and risk 2, and the total stayed
        11 on each side. A counter that compares TOTALS reads zero on it.

        The two totals agreeing is asserted here as well. Without that, an
        implementation that counted a MOVED total would also pass.
        """
        member = self._site(
            "1", {"novelty": 2, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 3,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 2,
                        "total": 11,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["superseded"] == 0
        assert result["superseded_dimensions_only"] == 1
        assert result["dimensions_incomparable"] == 0

    def test_agreeing_vectors_are_not_a_dimension_revision(self):
        """The CONTROL for the arm above. Same shape, nothing revised.

        Without this arm, an implementation that counted every agreeing-total
        member as a dimension revision would pass the arm above.
        """
        stamp = {"novelty": 2, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        member = self._site("1", dict(stamp))
        snapshots = [self._snap("1", {"variety": dict(stamp)})]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["superseded"] == 0
        assert result["superseded_dimensions_only"] == 0
        assert result["dimensions_incomparable"] == 0

    def test_an_incomplete_vector_is_reported_not_absorbed(self):
        """A partial dimension vector cannot be compared, and it SAYS SO.

        The dispatch projection is tolerant of a partial stamp, so a member
        can carry a total and fewer than four dimensions. The totals agree
        here, so the member would otherwise read as full agreement that
        nothing measured.

        `superseded_dimensions_only` is asserted to be ZERO, which is what
        separates this from an implementation that treats a partial vector as
        a difference.
        """
        member = self._site("1", {"novelty": 2, "total": 11})
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 3,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 2,
                        "total": 11,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["dimensions_incomparable"] == 1
        assert result["superseded_dimensions_only"] == 0
        assert result["superseded"] == 0

    def test_an_out_of_range_dimension_makes_the_vector_incomparable(self):
        """The dimension guard checks the RANGE, and not only the TYPE.

        A dimension of 9 is an int, so a type-only guard accepts it and the
        vector reads as complete. The two vectors then DIFFER and the member
        reads as a dimension revision, which this data cannot support.

        The two totals are equal here, so the member reaches the cell where
        the vector comparison decides the answer. The two counters are each
        asserted, because a type-only guard MOVES the member from one to the
        other, and an assertion on one alone would not see the move.
        """
        member = self._site(
            "1", {"novelty": 9, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 2,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 3,
                        "total": 11,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["dimensions_incomparable"] == 1
        assert result["superseded_dimensions_only"] == 0

    def test_the_dimension_guard_holds_its_two_bounds_and_rejects_a_bool(self):
        """The guard is tested AT its bounds, and one member carries each case.

        A VALUE FAR FROM A BOUND TESTS THE REJECTION AND NOT THE BOUND. The
        sibling arm above feeds a 9, which the shipped guard and a guard
        moved by one BOTH reject, so that arm cannot see a moved bound. The
        discriminating value for a threshold moved by one is the single value
        that changes verdict, so this arm feeds 1 and 5 and not 0 and 9.

        THE THREE CASES NEED THREE MEMBERS AND CANNOT SHARE ONE. The low
        bound case needs the shipped vector COMPLETE, because the shipped
        answer is a dimension revision. The high bound case and the bool case
        need the shipped vector INCOMPLETE. Those are contradictory, so one
        member holding a 1, a 5 and a True is rejected under the shipped
        guard AND under each mutant, and it separates nothing.

        - member 1 carries a dimension of 1, the LOW bound. Its vector is
          complete and differs, so it is a dimension revision. A guard that
          rejects below 2 makes it incomparable.
        - member 2 carries a dimension of 5, one above the HIGH bound. It is
          incomparable. A guard that accepts up to 5 makes it a revision.
        - member 3 carries True against a 1. It is incomparable, because the
          guard rejects a bool. A guard that accepts a bool makes the two
          vectors AGREE, since True equals 1, so the member counts nowhere.
          A JSON `true` parses to a Python True, so this shape reaches the
          journal rather than being adversarial.

        THE TWO COUNTS ARE ASSERTED TOGETHER AND THEY ARE ASYMMETRIC, 1
        against 2. Each mutation MOVES one member between the two counters,
        or out of both, so an assertion on one counter alone would not see
        the move, and equal counts could not see a transposition.
        """
        low = self._site(
            "1", {"novelty": 1, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        high = self._site(
            "2", {"novelty": 5, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        flag = self._site(
            "3", {"novelty": True, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        ordinary = {"novelty": 2, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        snapshots = [
            self._snap("1", {"variety": dict(ordinary)}),
            self._snap("2", {"variety": dict(ordinary)}),
            self._snap(
                "3",
                {
                    "variety": {
                        "novelty": 1,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 3,
                        "total": 11,
                    }
                },
            ),
        ]
        result = extract_final_dispatch_coverage([low, high, flag], snapshots)
        assert result["variety_totals"] == [11, 11, 11]
        assert result["superseded_dimensions_only"] == 1
        assert result["dimensions_incomparable"] == 2
        assert result["superseded"] == 0

    def test_the_low_bound_is_accepted_on_its_own_member(self):
        """A SECOND DETECTOR FOR THE LOW BOUND, AND NOT A COPY OF THE ARM
        ABOVE. The arm above is the SOLE detector of three conditions: the
        two bounds moved by one, and the bool rejection removed. Lose that
        arm and the three reopen together with nothing else going red.

        THIS ARM FAILS IN THE OPPOSITE DIRECTION. The arm above asserts
        REJECTION for its 5 and its True: the guard refuses the value. THIS
        ONE ASSERTS ACCEPTANCE: a dimension of 1 is VALID, so the vector is
        COMPLETE and the member is a dimension revision. A guard that
        NARROWS breaks acceptance. A guard that WIDENS breaks rejection. One
        directional mutation cannot kill an acceptance arm and a rejection
        arm together, which is what makes this a detector rather than a copy.

        AND IT SEES A MOVE THE ARM ABOVE CANNOT. That arm holds three cases
        in ONE assertion pair, 1 against 2. A COMPOUND MUTATION THAT MOVES
        ONE MEMBER INTO THE REVISION CELL AND ANOTHER OUT OF IT PRESERVES
        THE PAIR AND GOES UNDETECTED. This arm has ONE member and its own
        assertion, so it has no second member to absorb the move.

        THE LOW BOUND IS THE CONDITION ORDINARY DATA REACHES. A 1 appears in
        a normal stamp. A 5 is out of range and a True is a producer defect.
        If one of the three must survive an arm loss, it is this one.

        The two totals AGREE at 11, so the member reaches the vector cell.
        """
        member = self._site(
            "1", {"novelty": 1, "scope": 3, "uncertainty": 3, "risk": 4, "total": 11}
        )
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 2,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 3,
                        "total": 11,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["superseded_dimensions_only"] == 1
        assert result["dimensions_incomparable"] == 0

    def test_the_high_bound_is_rejected_on_its_own_member(self):
        """A SECOND DETECTOR FOR THE HIGH BOUND, ON ITS OWN MEMBER.

        The arm above holds three cases in ONE assertion pair, so a COMPOUND
        mutation that moves one member into a cell and another out of it
        preserves the pair and goes undetected. THIS ARM HAS ONE MEMBER AND
        ITS OWN ASSERTION, so it has no second member to absorb a move.

        A dimension of 5 is ONE ABOVE the high bound, which is the value that
        changes verdict when the bound moves by one. A value far above the
        bound, such as 9, is refused by the shipped guard AND by a guard
        moved by one, so it cannot see the move.

        THE TWO CELLS ARE ASSERTED TOGETHER, because a guard that accepts up
        to 5 does not LOSE the member. It MOVES it: the vector completes, the
        two vectors differ, and the member reads as a dimension revision. An
        assertion on one cell alone would not see that move.
        """
        member = self._site(
            "1", {"novelty": 5, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 2,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 3,
                        "total": 11,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["dimensions_incomparable"] == 1
        assert result["superseded_dimensions_only"] == 0

    def test_a_bool_dimension_is_rejected_on_its_own_member(self):
        """A SECOND DETECTOR FOR THE BOOL REJECTION, ON ITS OWN MEMBER.

        `True` IS an int in Python and it equals 1, so a guard that checks
        the TYPE alone accepts it. A JSON `true` parses to a Python `True`,
        so this shape reaches the journal and is not adversarial.

        THE MOVE HERE IS OUT OF THE TWO CELLS AND NOT BETWEEN THEM, and that
        is why the two are asserted. The snapshot side carries a 1. Under the
        shipped guard the bool is refused, the vector is incomplete, and the
        member is incomparable. Under a guard that accepts a bool the two
        vectors AGREE, because `True` equals 1, so the member counts NOWHERE
        and each of the two cells reads zero. An assertion on
        `dimensions_incomparable` alone would see that, and an assertion on
        `superseded_dimensions_only` alone would not.
        """
        member = self._site(
            "1", {"novelty": True, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 1,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 3,
                        "total": 11,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["dimensions_incomparable"] == 1
        assert result["superseded_dimensions_only"] == 0

    def test_a_moved_total_is_superseded_and_not_a_dimension_revision(self):
        """The two superseded counters are DISJOINT, asserted on one member.

        The vectors differ here AND the total moved. The docstring argues
        that a moved total takes the member before any vector comparison
        runs, so a reader can add the two counts without a double count.
        This arm is that argument executed.
        """
        member = self._site(
            "1", {"novelty": 2, "scope": 3, "uncertainty": 3, "risk": 3, "total": 11}
        )
        snapshots = [
            self._snap(
                "1",
                {
                    "variety": {
                        "novelty": 3,
                        "scope": 3,
                        "uncertainty": 3,
                        "risk": 3,
                        "total": 12,
                    }
                },
            )
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [12]
        assert result["superseded"] == 1
        assert result["superseded_dimensions_only"] == 0
        assert result["dimensions_incomparable"] == 0

    def test_an_unusable_ts_drops_its_snapshot_and_no_counter_says_so(self):
        """THE STATED LIMIT, PINNED AS BEHAVIOUR. The value is the latest
        USABLE snapshot and not the latest snapshot.

        A snapshot with an unparseable `ts` has no instant, so it has no
        position in the order this join sorts by, and the picker drops it.
        WHEN THE DROPPED ONE IS THE NEWER ONE, AN EARLIER SNAPSHOT SUPPLIES
        THE VALUE AND THE RESULT REPORTS AS FINAL. That substitution is
        SILENT: no term in the returned set names it, and `fallback_used`
        does not, because a snapshot DID resolve.

        THE HELPER DOCSTRING STATES THAT LIMIT AND DECLINES A COUNTER FOR IT,
        on the ground that a skip count cannot show staleness. A skipped
        event cannot be ORDERED against the one used, so it cannot be shown
        to be newer than it, AND IT CAN BE THE OLDER ONE, in which case
        nothing is stale and a count of skips OVER-REPORTS. THE TWO HALVES
        ARE DIFFERENT CLAIMS AND THE DECLINE RESTS ON EACH: the first says
        such a count cannot ESTABLISH staleness, the second says it INFLATES
        the number it reports. THAT GROUND IS ABOUT ORDER AND NOT ABOUT AN
        ENUMERATION OF CAUSES, so it holds for a missing `ts`, for an
        unparseable one, and for one that parses and does not compare. THIS
        ARM PINS THE BEHAVIOUR THAT LIMIT DESCRIBES, so a later edit that
        starts counting the drop, or that sends the full member to the
        fallback as an alternative, goes RED here and becomes a decision
        rather than a drift.

        EACH COUNTER IS ASSERTED AT ZERO ON PURPOSE. The claim is that
        NOTHING reports the substitution, and a claim about the absence of a
        report needs each candidate reporter named.

        The dispatch value is 9 and the usable snapshot is 11, so 11 in the
        distribution shows the join ran and did not take the fallback.
        """
        member = self._site("1", {"total": 9})
        snapshots = [
            self._snap("1", {"variety": {"total": 11}}, ts=_TS),
            self._snap("1", {"variety": {"total": 12}}, ts="not-a-timestamp"),
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [11]
        assert result["fallback_used"] == 0
        assert result["total_unresolved"] == 0
        assert result["malformed"] == []
        assert result["late_stamped"] == 0
        assert result["dispatch_malformed"] == 0

    # -- The arm-3 counter and the nesting it joins -------------------------

    def test_total_unresolved_counts_arm_three_and_not_arm_two(self):
        """`total_unresolved` names the ARM-3 half: no total resolved on
        EITHER stream. An arm-2 member resolved its `dispatch_site` value,
        so it is NOT unresolved and must not be counted here.

        The fixture populates the two arms at DIFFERENT sizes, one arm-2
        member against two arm-3 members, so a counter that counted the
        union or counted arm 2 alone reads a different number.
        """
        sites = [
            self._site("1", {"total": 9}),
            self._site("2"),
            self._site("3"),
        ]
        result = extract_final_dispatch_coverage(sites, [])
        assert result["variety_totals"] == [9]
        assert result["total_unresolved"] == 2

    def test_fallback_used_is_unchanged_by_the_arm_three_counter(self):
        """A REGRESSION GUARD, and it is deliberately redundant.

        `fallback_used` keeps its meaning as the UNION of arms 2 and 3 and
        keeps its name. The arm-3 counter is an ADDITION and not a split.
        AN ADDITION THAT QUIETLY MOVES THE OLD NUMBER IS THE WAY THIS LANDS
        WRONG, because the new number looks correct and the old one is what
        a reader compares against a figure from before the change.

        THIS ARM EXISTS TO GO RED ON A FUTURE EDIT, so an existing arm that
        also catches the mutation today is not a reason to remove it. The
        fixture populates arm 2 AND arm 3, because a fixture that reaches
        one arm alone cannot see a counter narrowed to the other.
        """
        sites = [
            self._site("1", {"total": 9}),
            self._site("2", {"total": 10}),
            self._site("3"),
        ]
        result = extract_final_dispatch_coverage(sites, [])
        assert result["fallback_used"] == 3
        assert result["total_unresolved"] == 1

    def test_the_three_fallback_terms_nest_and_do_not_add(self):
        """`fallback_used` CONTAINS `total_unresolved` CONTAINS `malformed`.

        THE RETURNED SET CARRIES TWO RELATION TYPES AT THE SAME TIME: the
        six arm-1 cells PARTITION, and these three NEST. A reader who ADDS
        `fallback_used` and `total_unresolved` DOUBLE-COUNTS and gets a
        plausible number with no error, so the nesting needs an arm.

        THE THREE EXPECTED VALUES ARE DIFFERENT ON PURPOSE, 8 against 3
        against 1. TWO CELLS AT THE SAME EXPECTED VALUE CONSTRAIN THE
        MULTISET AND NOT THE MAPPING, so an equality in the three would
        hide a transposition of two of them.

        The members, grouped by the layer of the nesting they reach:
        - `arm_two` is FIVE members. Each resolved its own value, so each
          reaches `fallback_used` alone.
        - `arm_three_plain` is TWO members with NO `variety` key. They reach
          `total_unresolved` and are not malformed.
        - `arm_three_malformed` is ONE member with a PRESENT but
          unresolvable `variety`. It reaches all three layers.

        ARM 2 IS FIVE MEMBERS AND NOT ONE, AND THE SIZE IS LOAD-BEARING. At
        one member, an edit that makes `fallback_used` disjoint from
        `total_unresolved` drives the outer count BELOW the inner one, so
        the fixture cannot host the case where the layers come apart and the
        counts stay in order. At five it can, so the assertions below are
        measured against the case they exist for.
        """
        arm_two = [self._site(str(n), {"total": 9}) for n in range(1, 6)]
        arm_three_plain = [self._site("6"), self._site("7")]
        arm_three_malformed = [self._site("8", {"total": "junk"})]
        sites = arm_two + arm_three_plain + arm_three_malformed
        result = extract_final_dispatch_coverage(sites, [])
        # CONTAINMENT WRITTEN AS AN ACCOUNTING, WHICH IS WHAT AN ORDERING
        # CANNOT CARRY. Each line says an OUTER count equals its own extra
        # members PLUS THE MEASURED INNER COUNT, so an edit that makes two
        # layers disjoint drops the inner term from the outer sum and goes
        # RED here rather than in a reader. `a > b > c` does NOT prove
        # containment: disjoint layers satisfy it when the outer group
        # is the larger one, which is why no ordering appears below.
        assert len(result["malformed"]) == len(arm_three_malformed)
        assert result["total_unresolved"] == len(arm_three_plain) + len(
            result["malformed"]
        )
        assert result["fallback_used"] == len(arm_two) + result[
            "total_unresolved"
        ]

    # -- Hostile input -----------------------------------------------------

    @pytest.mark.parametrize(
        "hostile",
        [None, "string", 42, {"not": "a list"}, object()],
    )
    def test_non_list_members_yield_the_empty_result(self, hostile):
        """The empty result carries EVERY key the populated result carries.

        THIS ARM IS A SCHEMA GATE ON THE KEY SET, AND A NEW KEY SHOULD MAKE
        IT RED. That is the arm working rather than the arm breaking. It has
        caught a key-set change two rounds running. WHEN IT GOES RED, ADD THE
        NEW KEY TO THE DICT BELOW. DO NOT WEAKEN THE EQUALITY.

        The equality is against a whole dict on purpose. A reader that pulls
        a counter off this result must find it here too, so a key added to
        the populated return and forgotten here would raise a KeyError in a
        wrap-up rather than in a test.
        """
        assert extract_final_dispatch_coverage(hostile, []) == {
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

    @pytest.mark.parametrize(
        "hostile",
        [None, "string", 42, {"not": "a list"}, object()],
    )
    def test_non_list_snapshots_leave_each_member_on_its_fallback(self, hostile):
        """The join degrades to the old behaviour rather than raising. This
        runs inside a wrap-up that the session depends on."""
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})], hostile
        )
        assert result["variety_totals"] == [9]
        assert result["fallback_used"] == 1

    def test_non_dict_member_is_a_site_that_joins_to_nothing(self):
        """It existed, so it counts as a site. It carries nothing resolvable
        and it is not malformed, which mirrors `extract_dispatch_coverage`."""
        result = extract_final_dispatch_coverage(
            ["not-a-dict", None, self._site("1", {"total": 9})],
            [self._snap("1", {"variety": {"total": 11}})],
        )
        assert result["variety_totals"] == [11]
        assert result["sites"] == 3
        assert result["malformed"] == []
        assert result["fallback_used"] == 2

    def test_a_member_without_a_task_id_joins_to_nothing(self):
        """A `task_id` of None is skipped rather than keyed, so two events
        that each lack one cannot join to each other on the string "None"."""
        member = {"v": 1, "type": "dispatch_site", "variety": {"total": 9}}
        snapshots = [
            {
                "v": 1,
                "type": "task_metadata_snapshot",
                "metadata": {"variety": {"total": 14}},
                "ts": _TS,
            }
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [9]
        assert result["fallback_used"] == 1
        assert result["superseded"] == 0

    # -- The join key ------------------------------------------------------
    #
    # EACH ARM BELOW FEEDS THE TWO STREAMS DISAGREEING VALUES, and that is
    # what makes the arm able to fail. The site carries 9 and the snapshot
    # carries 12, so a join that MISSES returns [9] with `fallback_used` 1,
    # and a join that HITS returns [12] with `fallback_used` 0. On agreeing
    # values the two outcomes are one list and the arm measures nothing.

    def test_an_int_snapshot_id_joins_a_str_member_id(self):
        """The `str()` normalize sits on the TWO sides of the join, so the
        streams align when one of them emits a bare int.

        DIRECTION MATTERS AND THIS IS THE FIRST OF TWO. Here the SNAPSHOT
        carries the int. An index that keys on the raw value cannot be found
        by a stringified member lookup, so this arm fails when the normalize
        is removed from the INDEX side alone, and also when it is removed
        from the two sides together.
        """
        member = self._site("7", {"total": 9})
        snapshots = [self._snap(7, {"variety": {"total": 12}})]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [12]
        assert result["fallback_used"] == 0
        assert result["superseded"] == 1

    def test_an_int_member_id_joins_a_str_snapshot_id(self):
        """The opposite direction, and it is a DIFFERENT condition.

        Here the MEMBER carries the int. A lookup that keys on the raw value
        misses a stringified index, so this arm fails when the normalize is
        removed from the two sides together. It PASSES when the normalize is
        removed from the index side alone, which is what separates the two
        defects rather than merging them into one finding.
        """
        member = self._site(7, {"total": 9})
        snapshots = [self._snap("7", {"variety": {"total": 12}})]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [12]
        assert result["fallback_used"] == 0
        assert result["superseded"] == 1

    def test_a_member_with_no_task_id_does_not_join_the_string_none_key(self):
        """`emit_task_metadata_snapshot` writes the id through
        `sanitize_path_component(str(task_id))`, so a snapshot for a task
        with no id lands on the LITERAL STRING "None". A member with no id
        must not join it.

        This is the collision the member-side None guard prevents, and it is
        a shape the emit path can produce rather than an adversarial one. The
        sibling arm above covers the case where NEITHER side carries an id.
        This arm covers the mixed case, where the guard is the only thing
        holding the two apart.
        """
        member = self._site(None, {"total": 9})
        snapshots = [self._snap("None", {"variety": {"total": 12}})]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [9]
        assert result["fallback_used"] == 1
        assert result["superseded"] == 0

    def test_a_snapshot_with_no_task_id_does_not_join_the_string_none_key(self):
        """The mirror of the arm above, and the guard is on the other side.

        A snapshot that carries no id is skipped rather than keyed, so a
        member carrying the literal string "None" as its id cannot reach it.
        """
        member = self._site("None", {"total": 9})
        snapshots = [self._snap(None, {"variety": {"total": 12}})]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [9]
        assert result["fallback_used"] == 1
        assert result["superseded"] == 0

    # -- The latest-picker -------------------------------------------------

    def test_equal_instant_z_and_offset_forms_are_ordered_as_instants(self):
        """THE TWO STAMPS NAME ONE INSTANT IN TWO SPELLINGS, which is the
        case a byte compare gets incorrect.

        `make_event` stamps `...Z` and `canonical_since()` emits `...+00:00`.
        `+` (0x2B) sorts before `Z` (0x5A), so a byte compare finds the
        `+00:00` element SMALLER than an equal-instant `Z` element and keeps
        the earlier one. Last-wins on an equal instant says the later element
        wins, so the correct answer is 12 and a byte compare returns 11.

        THE SIBLING ARM THAT NAMES THE PARSE CANNOT REACH THIS. It separates
        its two stamps by a DAY, and a byte compare orders different dates
        correctly, so it returns the same answer for a parsed compare and for
        a byte compare. Equal instants are the only inputs that discriminate
        the two.
        """
        snapshots = [
            self._snap(
                "1", {"variety": {"total": 11}}, ts="2026-06-15T12:00:00Z"
            ),
            self._snap(
                "1", {"variety": {"total": 12}}, ts="2026-06-15T12:00:00+00:00"
            ),
        ]
        result = extract_final_dispatch_coverage(
            [self._site("1", {"total": 9})], snapshots
        )
        assert result["variety_totals"] == [12]

    def test_the_only_snapshot_having_no_ts_sends_the_member_to_the_fallback(
        self,
    ):
        """A snapshot with no `ts` is SKIPPED, and it is not dated to an
        early instant and kept.

        THE SIBLING SKIP ARM CANNOT REACH THIS, because it puts a usable
        snapshot LAST and greatest, so a picker that keeps a ts-less snapshot
        at the epoch returns the same answer. Here the ts-less snapshot is
        the ONLY one, so keeping it changes the result from the fallback
        value to the snapshot value.
        """
        member = self._site("1", {"total": 9})
        snapshots = [self._snap("1", {"variety": {"total": 12}}, ts="")]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [9]
        assert result["fallback_used"] == 1
        assert result["superseded"] == 0

    def test_a_non_dict_snapshot_element_does_not_raise(self):
        """The docstring says the function is pure and does not raise. A
        snapshot LIST holding a non-dict element is the input that tests it.

        The sibling hostile arms pass a non-list `snapshot_events` and a
        non-dict MEMBER. Neither reaches the element read inside the snapshot
        loop, so this arm covers the one remaining shape. The usable snapshot
        sits after the junk, so the arm fails on a raise and also on a loop
        that abandons the rest of the list.
        """
        member = self._site("1", {"total": 9})
        snapshots = [
            "not-a-dict",
            42,
            None,
            self._snap("1", {"variety": {"total": 12}}),
        ]
        result = extract_final_dispatch_coverage([member], snapshots)
        assert result["variety_totals"] == [12]
        assert result["fallback_used"] == 0
        assert result["superseded"] == 1


class TestTheThirdSkipCauseOfTheSnapshotPicker:
    """`_latest_snapshot_by_task` SKIPS ON THREE CAUSES, AND THIS PINS THE
    THIRD: a `ts` that PARSES and does NOT COMPARE.

    THE MECHANISM. `_parse_ts` is
    `datetime.fromisoformat(str(value).replace("Z", "+00:00"))`. A `ts` with
    NO timezone designator has no `Z` to replace, so it parses and returns a
    NAIVE datetime. The try block in the picker wraps the parse AND the
    compare, so `parsed >= current[0]` on a naive-against-aware pair raises
    TypeError and the event is skipped. THAT EVENT HAS A PARSED INSTANT AND
    NO COMPARABLE ONE, which is why the stated limit is about ORDER and not
    about instants.

    WHY THESE ARMS ARE HERE. The F4 stated limit in the helper docstring
    RESTS ON that parser property, and no arm pinned it. If a later edit
    makes `_parse_ts` attach UTC to a bare value, the third cause disappears
    and the shipped ground goes stale with nothing red.

    THE BOUND, SO A READER DOES NOT TAKE `THIS CAN HAPPEN` AS `THIS
    HAPPENS`. The path is reachable BY CONSTRUCTION of the parser. NO naive
    `ts` was found in a live journal, so THESE ARMS MAKE NO FIELD CLAIM.
    They pin that the path EXISTS and not that it occurs. A field claim is a
    journal census and a different question.

    THE ARMS IMPORT TWO PRIVATE NAMES. Other suite modules import private
    names from a `shared` module, so the pattern is established, and this is
    the first use of it for this module.
    """

    AWARE_EARLY = "2026-08-22T01:00:00Z"
    AWARE_LATE = "2026-08-22T02:00:00Z"
    NAIVE_EARLY = "2026-08-22T01:00:00"
    NAIVE_LATE = "2026-08-22T02:00:00"

    @staticmethod
    def _ev(task_id, ts, tag):
        """`tag` is a probe field. The picker carries the event through
        untouched, so a read of it back names WHICH event was selected."""
        return {"task_id": task_id, "ts": ts, "tag": tag}

    @staticmethod
    def _pick(events):
        return _latest_snapshot_by_task(events).get("1", {}).get("tag")

    def test_a_bare_ts_parses_naive_and_the_compare_raises(self):
        """THE MECHANISM, ISOLATED. This is the ONLY arm that does not call
        the picker, and it is what makes the two ordering arms below
        PREDICTIVE rather than observed.

        The `else` branch is the non-vacuity guard: if the compare stops
        raising, the arm says the path is UNREACHABLE rather than pass.
        """
        assert _parse_ts(self.AWARE_LATE).tzinfo is not None
        assert _parse_ts(self.NAIVE_LATE).tzinfo is None
        try:
            _ = _parse_ts(self.NAIVE_LATE) >= _parse_ts(self.AWARE_LATE)
        except TypeError as exc:
            assert "offset-naive and offset-aware" in str(exc)
        else:
            raise AssertionError("the compare did not raise: path UNREACHABLE")

    def test_positive_control_two_aware_instants_take_the_later_event(self):
        """CONTROL. With two aware instants the LATER event wins, so the
        picker orders correctly when no naive value is present."""
        assert (
            self._pick(
                [
                    self._ev("1", self.AWARE_EARLY, "early-aware"),
                    self._ev("1", self.AWARE_LATE, "late-aware"),
                ]
            )
            == "late-aware"
        )

    def test_an_aware_first_and_a_later_naive_second_keeps_the_aware(self):
        """The naive SECOND event is skipped, so the aware first one is
        returned. This covers the order the strong arm below does not: the
        naive event arrives SECOND rather than first."""
        assert (
            self._pick(
                [
                    self._ev("1", self.AWARE_EARLY, "first-aware"),
                    self._ev("1", self.NAIVE_LATE, "second-naive"),
                ]
            )
            == "first-aware"
        )

    def test_a_naive_first_and_a_later_aware_second_keeps_the_naive(self):
        """THE STRONG ARM. A LATER snapshot is DROPPED and an EARLIER one is
        reported as final. THIS IS THE F4 STATED LIMIT, reached through the
        third skip cause.

        Read this arm WITH the negative control below. On its own the
        outcome is compatible with an ordering defect.
        """
        assert (
            self._pick(
                [
                    self._ev("1", self.NAIVE_EARLY, "first-naive-EARLIER"),
                    self._ev("1", self.AWARE_LATE, "second-aware-LATER"),
                ]
            )
            == "first-naive-EARLIER"
        )

    def test_negative_control_the_same_order_all_aware_takes_the_later(self):
        """THE CONTROL THAT NAMES THE CAUSE. The SAME order with all aware
        values returns the LATER event.

        So the arm above is caused by the naive-against-aware MIX and NOT by
        the ordering. WITHOUT THIS ARM THAT DISTINCTION IS NOT MEASURED, and
        the strong arm would read as a possible ordering defect.
        """
        assert (
            self._pick(
                [
                    self._ev("1", self.AWARE_EARLY, "first-aware-EARLIER"),
                    self._ev("1", self.AWARE_LATE, "second-aware-LATER"),
                ]
            )
            == "second-aware-LATER"
        )


class TestExtractFinalDispatchCoverageDispatchAssessed:
    """The third argument: dispatch-marked `variety_assessed` events as a
    fallback-path as-dispatched source (arm 2b).

    The stream-level contract: per-dispatch mirrors carry the DISPATCH
    task's id and a TOP-LEVEL scope="dispatch" discriminator; feature-level
    events carry no scope field. Arm 2b fires ONLY when neither the latest
    snapshot (arm 1) nor the dispatch_site event's own variety (arm 2)
    resolved a total — so every pre-existing member outcome is unchanged,
    and the new source only rescues members that would otherwise land in
    arm 3.

    Non-vacuity: arm 2b is observable through four values a broken
    implementation cannot reproduce by accident — the rescued total in
    `variety_totals`, `fallback_used` still counting the member,
    `total_unresolved` NOT counting it, and `malformed` losing it.
    """

    _TS = "2026-06-15T12:00:00Z"

    def _site(self, task_id, variety=None):
        """A `dispatch_site` event. `variety=None` means the key is ABSENT."""
        fields = {"task_id": task_id}
        if variety is not None:
            fields["variety"] = variety
        return make_event("dispatch_site", **fields)

    def _va(self, task_id, ts, scope="dispatch", total=8):
        return make_event(
            "variety_assessed",
            task_id=task_id,
            scope=scope,
            variety={
                "novelty": 2,
                "scope": 2,
                "uncertainty": 2,
                "risk": 2,
                "total": total,
            },
            ts=ts,
        )

    # -- The rescue ----------------------------------------------------------

    def test_arm_2b_rescues_a_member_no_other_stream_resolved(self):
        """No snapshot, no variety on the dispatch_site event, and the
        dispatch-marked event resolves the total: the member contributes
        its total, counts ONCE in fallback_used, and leaves both
        total_unresolved and malformed at zero."""
        sites = [self._site("1")]  # un-stamped site
        assessed = [self._va("1", "2026-06-15T12:00:01Z", total=8)]
        result = extract_final_dispatch_coverage(sites, [], assessed)
        assert result["variety_totals"] == [8]
        assert result["fallback_used"] == 1
        assert result["total_unresolved"] == 0
        assert result["malformed"] == []
        assert result["superseded"] == 0
        assert result["late_stamped"] == 0
        assert result["dispatch_malformed"] == 0
        assert result["superseded_dimensions_only"] == 0
        assert result["dimensions_incomparable"] == 0

    def test_junk_site_variety_with_resolving_assessed_leaves_malformed(self):
        """A PRESENT-but-unresolvable dispatch_site variety that arm 2b
        rescues is no longer arm-3, so it leaves the malformed list: the
        join resolved a value and stops reporting a data-quality finding
        on the stream it did not use."""
        sites = [self._site("1", {"total": "junk"})]
        assessed = [self._va("1", "2026-06-15T12:00:01Z", total=8)]
        result = extract_final_dispatch_coverage(sites, [], assessed)
        assert result["variety_totals"] == [8]
        assert result["total_unresolved"] == 0
        assert result["malformed"] == []
        assert result["superseded"] == 0
        assert result["late_stamped"] == 0
        assert result["dispatch_malformed"] == 0
        assert result["superseded_dimensions_only"] == 0
        assert result["dimensions_incomparable"] == 0

    def test_site_value_still_wins_over_the_assessed_event(self):
        """Arm 2 order is pinned: the dispatch_site event's own variety
        resolves FIRST; the assessed event is the second as-dispatched
        source. Both resolve with distinct totals; the site's wins."""
        sites = [self._site("1", {"total": 9})]
        assessed = [self._va("1", "2026-06-15T12:00:01Z", total=8)]
        result = extract_final_dispatch_coverage(sites, [], assessed)
        assert result["variety_totals"] == [9]

    def test_snapshot_still_wins_over_everything(self):
        """Arm 1 is untouched by the third argument: a resolving snapshot
        supplies the FINAL value and the member does not count in
        fallback_used at all.

        `superseded` stays 0 because its comparator is the `dispatch_site`
        event's own variety, which is ABSENT here, not the assessed value
        (8). This is the ABSENT-site denial case: the resolving snapshot keeps
        the member on the site path, so the divergent assessed value can reach
        the result only if the comparator wrongly uses it. The present-null
        sibling, `test_a_present_but_null_variety_reaches_dispatch_malformed`,
        pins the distinct `dispatch_malformed` denial case.
        """
        sites = [self._site("1")]
        snapshots = [
            make_event(
                "task_metadata_snapshot",
                task_id="1",
                metadata={"variety": {"total": 10}},
                ts=self._TS,
            )
        ]
        assessed = [self._va("1", "2026-06-15T12:00:01Z", total=8)]
        result = extract_final_dispatch_coverage(sites, snapshots, assessed)
        assert result["variety_totals"] == [10]
        assert result["fallback_used"] == 0
        assert result["late_stamped"] == 1
        assert result["superseded"] == 0

    def test_the_site_value_is_the_comparator_when_both_values_resolve(self):
        """The comparator is the `dispatch_site` value even when a
        dispatch-marked `variety_assessed` value ALSO resolves.

        THE TWO CANDIDATES DISAGREE BY CONSTRUCTION: site 9, assessed 11,
        snapshot 11. The site comparator reads 9 != 11 and counts 1; an
        assessed-value comparator reads 11 == 11 and counts 0. The denial
        case — no resolvable site value — is pinned by the arm above, and
        cannot see this shape.
        """
        sites = [self._site("1", {"total": 9})]
        snapshots = [
            make_event(
                "task_metadata_snapshot",
                task_id="1",
                metadata={"variety": {"total": 11}},
                ts=self._TS,
            )
        ]
        assessed = [self._va("1", "2026-06-15T12:00:01Z", total=11)]
        result = extract_final_dispatch_coverage(sites, snapshots, assessed)
        assert result["superseded"] == 1

    # -- The stream boundary -------------------------------------------------

    def test_feature_level_events_never_join(self):
        """A feature-level variety_assessed event (no scope field) for the
        same task id does NOT resolve the member — the index admits
        dispatch-marked events only, so the two populations cannot
        cross-contaminate however they interleave."""
        sites = [self._site("1")]
        feature_level = [
            make_event(
                "variety_assessed",
                task_id="1",
                variety={"total": 8},
                ts="2026-06-15T12:00:01Z",
            )
        ]
        result = extract_final_dispatch_coverage(sites, [], feature_level)
        assert result["variety_totals"] == []
        assert result["total_unresolved"] == 1

    def test_latest_assessed_event_for_a_task_wins(self):
        """Two dispatch-marked events for one task (a re-emission): the
        LATEST by parsed instant supplies the value — the snapshot-index
        direction, not the arc-start direction."""
        sites = [self._site("1")]
        assessed = [
            self._va("1", "2026-06-15T12:00:01Z", total=6),
            self._va("1", "2026-06-15T13:00:00Z", total=7),
        ]
        result = extract_final_dispatch_coverage(sites, [], assessed)
        assert result["variety_totals"] == [7]

    def test_an_unresolvable_latest_assessed_event_does_not_scan_backward(self):
        """The latest timestamp-valid event is SELECTED before resolution.

        The earlier event resolves 8, but the later event carries only a junk
        total. No backward scan may rescue the earlier total. The site also
        carries a present junk variety, so arm 3 includes it in `malformed`.
        """
        sites = [self._site("1", {"total": "junk"})]
        assessed = [
            self._va("1", "2026-06-15T12:00:01Z", total=8),
            make_event(
                "variety_assessed",
                task_id="1",
                scope="dispatch",
                variety={"total": "junk"},
                ts="2026-06-15T13:00:00Z",
            ),
        ]
        result = extract_final_dispatch_coverage(sites, [], assessed)
        assert result["variety_totals"] == []
        assert result["fallback_used"] == 1
        assert result["total_unresolved"] == 1
        assert result["malformed"] == sites

    def test_assessed_event_for_another_task_never_joins(self):
        """The index is keyed on the member's task id; a dispatch-marked
        event for a different task is unreachable."""
        sites = [self._site("1")]
        assessed = [self._va("2", "2026-06-15T12:00:01Z", total=8)]
        result = extract_final_dispatch_coverage(sites, [], assessed)
        assert result["variety_totals"] == []
        assert result["total_unresolved"] == 1

    # -- The legacy call shape ------------------------------------------------

    def test_two_argument_call_is_unchanged(self):
        """The legacy two-source shape: no third argument, the member stays
        arm-3 exactly as before the stream existed."""
        result = extract_final_dispatch_coverage([self._site("1")], [])
        assert result["variety_totals"] == []
        assert result["fallback_used"] == 1
        assert result["total_unresolved"] == 1

    def test_none_and_non_list_third_arguments_behave_as_omitted(self):
        """None (the default) and a non-list both yield an empty index —
        fail-open on hostile input, mirroring the other two inputs."""
        sites = [self._site("1")]
        for bad in (None, "notalist", 42):
            result = extract_final_dispatch_coverage(sites, [], bad)
            assert result["total_unresolved"] == 1

    # -- The acceptance shape --------------------------------------------------

    def test_fresh_simulated_arc_resolves_every_site_without_fallback(self):
        """The mission's acceptance shape on one journal: a feature-level
        event anchoring the arc, a dispatch-marked event per dispatch, a
        dispatch_site member per dispatch, and a snapshot per task. Every
        site resolves from its snapshot, so fallback_used is 0 and the
        as-dispatched mirrors are all consumable."""
        sites = [self._site(str(i)) for i in (1, 2, 3)]
        snapshots = [
            make_event(
                "task_metadata_snapshot",
                task_id=str(i),
                metadata={
                    "variety": {
                        "novelty": 2,
                        "scope": 2,
                        "uncertainty": 2,
                        "risk": 2,
                        "total": 8,
                    }
                },
                ts=self._TS,
            )
            for i in (1, 2, 3)
        ]
        assessed = [
            self._va(str(i), f"2026-06-15T12:00:0{i}Z", total=8)
            for i in (1, 2, 3)
        ]
        result = extract_final_dispatch_coverage(sites, snapshots, assessed)
        assert result["sites"] == 3
        assert result["variety_totals"] == [8, 8, 8]
        assert result["fallback_used"] == 0
        assert result["total_unresolved"] == 0
