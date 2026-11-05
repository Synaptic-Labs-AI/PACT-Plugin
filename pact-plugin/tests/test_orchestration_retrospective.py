"""
Tests for orchestration retrospective in wrap-up.md.

Tests cover:
1. Orchestration Retrospective section exists
2. Four assessment questions defined
3. pact-memory save convention documented
4. Estimation pattern question documented
5. Q5/Q6 journal-read hardening
6. Q5/Q6 extractions are total (no direct-indexing comprehension)
7. Q5's recovery path depends on the dimension-sum resolver candidate
8. Q7 handoff_refusal_degraded question presence pin
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared.teachback_schema import resolve_variety_total  # noqa: E402
from shared.session_journal import _ts_ge  # noqa: E402
from shared.variety_divergence import (  # noqa: E402
    extract_dispatch_coverage,
    extract_final_dispatch_coverage,
    resolve_arc_start,
)

WRAPUP_PATH = Path(__file__).parent.parent / "commands" / "wrap-up.md"


@pytest.fixture
def q5():
    """The Q5 question line, module-scoped for the classes that execute the
    expression it carries."""
    return _question_line(
        WRAPUP_PATH.read_text(encoding="utf-8"), "5. **Variety divergence**"
    )


@pytest.fixture
def q6():
    return _question_line(
        WRAPUP_PATH.read_text(encoding="utf-8"),
        "6. **Variety acknowledgment signals**",
    )


class TestOrchestrationRetrospective:
    """Tests for orchestration retrospective in wrap-up command."""

    @pytest.fixture
    def wrapup_content(self):
        return WRAPUP_PATH.read_text(encoding="utf-8")

    def test_retrospective_section_exists(self, wrapup_content):
        assert "Orchestration Retrospective" in wrapup_content

    def test_variety_accuracy_question(self, wrapup_content):
        assert "Variety accuracy" in wrapup_content

    def test_phase_efficiency_question(self, wrapup_content):
        assert "Phase efficiency" in wrapup_content

    def test_specialist_fit_question(self, wrapup_content):
        assert "Specialist fit" in wrapup_content

    def test_estimation_pattern_question(self, wrapup_content):
        assert "Estimation pattern" in wrapup_content

    def test_memory_save_convention(self, wrapup_content):
        assert "orchestration_calibration" in wrapup_content

    def test_handoff_refusal_degradation_question(self, wrapup_content):
        # Presence pin for the Q7 degrade-telemetry consumer: the heading and
        # the event-type literal it reads are the load-bearing tokens.
        assert "HANDOFF refusal degradations" in wrapup_content
        assert "handoff_refusal_degraded" in wrapup_content


def _backticked_expression(line, prefix):
    """Return the single inline-backticked code span in `line` that starts with
    `prefix`. The retrospective questions carry their extraction code inline, so
    this lifts the ACTUAL documented expression out of the instruction rather
    than restating it in the test — a restatement would drift from the markdown
    silently and pin nothing."""
    for span in re.findall(r"`([^`]+)`", line):
        if span.startswith(prefix):
            return span
    raise AssertionError(f"no backticked expression starting with {prefix!r}")


# ---------------------------------------------------------------------------
# THE EXTRACTION RUNNERS: ONE FOR EACH QUESTION, AND ONE FOR EACH Q5 PATH.
#
# THE DEFECT THAT PRODUCED THIS SPLIT, recorded so nobody re-merges them. A
# SINGLE runner took `snapshot_events` with a default of EMPTY. An empty
# snapshot stream sends each member of the Q5 join to the FALLBACK, where the
# join does not read the snapshot side at all. So a caller that omitted the
# argument selected a path it did not choose, and the choice was invisible at
# the call site. Measured: this file detected 1 mutant of 15 in a sweep of the
# join helper. The mutants that inverted the value precedence, narrowed the
# resolver on the snapshot side, and widened membership from the snapshot
# stream were ALL invisible to it.
#
# WHAT THE SPLIT MAKES STRUCTURALLY IMPOSSIBLE, in mechanism and not in an
# assertion. Each item below is enforced by a signature or by a namespace, so
# no reviewer vigilance is necessary.
#
#   1. A Q5 caller CANNOT take the fallback path in silence. The two paths are
#      different functions and the path is readable from the NAME, without a
#      reader inspecting the argument.
#   2. A Q5 caller CANNOT call the joined runner and receive the fallback. The
#      joined runner REFUSES an empty snapshot list, and it raises.
#   3. A Q6 caller CANNOT pass a snapshot value, and a Q6 expression CANNOT
#      read one. The Q6 runner has no such parameter and puts no
#      `snapshot_events` name in its namespace, so a Q6 expression that ever
#      reaches for one raises NameError rather than read a silent empty list.
#
# WHAT THE SPLIT DOES NOT MAKE IMPOSSIBLE, stated because an aspirational
# claim here would repeat the defect. A file in which EVERY Q5 arm calls the
# fallback runner STAYS blind to each snapshot-side mutant. No signature
# can prevent that. A mutation sweep detects it, and a signature does not.
#
# THE THIRD STREAM, AND WHY IT IS NOT A PATH SELECTOR. The documented Q5
# expression names `dispatch_assessed` (the arc-scoped dispatch-marked
# variety_assessed events, the join's arm-2b source), so both Q5 runners
# put that name in the namespace. An EMPTY default for it does NOT select
# a path the caller did not choose — unlike an empty `snapshot_events`,
# which sends every member to the fallback — because arm 2b only rescues
# members no other stream resolved; with it empty, arm-1 and arm-2
# outcomes are byte-identical. An arm that tests arm 2b through the
# documented expression passes the stream explicitly, so the call site
# states what it exercises, same discipline as the runner names.
# ---------------------------------------------------------------------------
def _exec_documented_chain(expressions, namespace):
    """Execute the documented extraction chain and return the namespace.

    `exec` is the point, not a shortcut: wrap-up.md is executed by an LLM at
    runtime, so the only way to pin the instruction's BEHAVIOUR (rather than
    grep its wording) is to run the expression the file actually carries. The
    input is repo-controlled markdown.

    ACCEPTS A SEQUENCE, and that is load-bearing since the Q5 join. The join
    carries the helper CALL and the unpack as two separate backticked spans,
    so a lift of the unpack alone execs a statement that reads nothing from
    `events` and raises NameError. Running the spans IN ORDER pins the whole
    documented path rather than one line of it.

    THE NAMESPACE IS THE CALLER'S, and that is the whole mechanism of the
    split. What a runner puts in the namespace decides what the documented
    text can reach, so a name that is absent is unreachable rather than
    quietly empty.
    """
    if isinstance(expressions, str):
        expressions = [expressions]
    for expression in expressions:
        # noqa: S102, repo-controlled instruction text
        exec(expression, namespace)  # noqa: S102
    return namespace


def _q5_namespace(events, snapshot_events, dispatch_assessed,
                  read_output=None):
    return {
        "events": events,
        "snapshot_events": snapshot_events,
        "dispatch_assessed": dispatch_assessed,
        "output": read_output,
        "json": json,
        "resolve_variety_total": resolve_variety_total,
        "extract_dispatch_coverage": extract_dispatch_coverage,
        "extract_final_dispatch_coverage": extract_final_dispatch_coverage,
    }


def _run_q5_extraction(expressions, events, snapshot_events,
                       dispatch_assessed=(), read_output=None):
    """Run the Q5 chain on the JOINED path. `snapshot_events` is REQUIRED.

    THE EMPTY LIST IS REFUSED, and the refusal is the mechanism. An empty
    snapshot stream sends each member to the fallback, so a caller that passed
    one would name this runner and receive the other path. That is the exact
    silent substitution the split exists to prevent, and it fails loudly here
    instead. A caller that MEANS the fallback calls the fallback runner.

    `dispatch_assessed` (the arm-2b source) defaults EMPTY: an empty third
    stream is not a path selection — see the runner-divide comment above.

    `read_output` seeds the namespace's `output` (the stdout a journal read
    prints) for arms that execute the documented `va_events` binding. Default
    None: an expression that reaches for `output` without a seeded read fails
    LOUDLY (json.loads(None) raises), staying unreachable rather than
    quietly empty.
    """
    if not snapshot_events:
        raise ValueError(
            "_run_q5_extraction pins the JOINED path and an empty "
            "snapshot_events selects the FALLBACK path for every member. "
            "Call _run_q5_extraction_on_the_fallback_path when that is what "
            "you mean, so the call site states which path it tests."
        )
    return _exec_documented_chain(
        expressions, _q5_namespace(events, snapshot_events, dispatch_assessed,
                                   read_output)
    )


def _run_q5_extraction_on_the_fallback_path(expressions, events,
                                            dispatch_assessed=()):
    """Run the Q5 chain where NO member has a snapshot.

    EACH MEMBER TAKES THE FALLBACK and keeps its `dispatch_site` value. The
    snapshot side of the join is not read. An arm that calls this runner
    cannot detect a defect on the snapshot side, and the NAME says so at the
    call site rather than in a comment a reader can miss.

    `dispatch_assessed` (the arm-2b source) defaults EMPTY, so the fallback
    arms here keep their pre-arm-2b numbers; an arm that tests the rescue
    through the documented expression passes the stream explicitly.
    """
    return _exec_documented_chain(
        expressions, _q5_namespace(events, [], dispatch_assessed)
    )


def _run_q6_extraction(expression, events):
    """Run the Q6 chain. THERE IS NO SNAPSHOT PARAMETER, BY CONSTRUCTION.

    Q6 reads the acknowledgment flags and it does not read the snapshot
    stream. The namespace carries no `snapshot_events` name, so a Q6
    expression that ever reaches for one raises NameError. That failure
    direction is correct: a loud stop beats a silent read of an empty list.
    """
    return _exec_documented_chain(
        expression,
        {
            "events": events,
            "resolve_variety_total": resolve_variety_total,
        },
    )


def _question_line(content, prefix):
    """Return the single wrap-up.md line beginning with `prefix` (each
    retrospective question is one long markdown line). Asserting WITHIN the
    specific question line is stronger than a whole-file substring grep."""
    for line in content.splitlines():
        if line.lstrip().startswith(prefix):
            return line
    raise AssertionError(f"question line not found for prefix {prefix!r}")


class TestRetroReadHardening:
    """#966 (docs-only): wrap-up Q5/Q6 journal-read guidance hardening — the
    explicit single-JSON-array parse, the output-masking bans, the Q5
    masked-empty re-read guard, and the Q6 fallback-on-zero that prevents a
    fabricated 0% signal rate from a masked/crashed read.

    These are STRUCTURAL (prose) assertions: they detect REMOVAL or
    rewording-away of the hardening instruction, but cannot prove an LLM
    obeys it at runtime — a known vacuity limit of prose tests, documented
    as a residual risk in the TEST HANDOFF. They are the appropriate
    coverage for a docs-only change whose logic is not extracted to code.
    """

    @pytest.fixture
    def wrapup_content(self):
        return WRAPUP_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def q5(self, wrapup_content):
        return _question_line(wrapup_content, "5. **Variety divergence**")

    @pytest.fixture
    def q6(self, wrapup_content):
        return _question_line(wrapup_content, "6. **Variety acknowledgment signals**")

    # --- Q5 read-mechanics hardening ---

    def test_q5_states_single_json_array_parse(self, q5):
        assert "SINGLE JSON array" in q5
        assert "json.loads(output)" in q5

    def test_q5_warns_against_line_by_line_parse(self, q5):
        assert "do NOT parse line-by-line" in q5

    def test_q5_bans_output_masking_pipes(self, q5):
        # the three constructs that mask a parse crash as emptiness
        assert "2>/dev/null" in q5
        assert "|| echo" in q5
        assert "`head`" in q5

    def test_q5_masked_empty_guard_precedes_omission_conclusion(self, q5):
        # the re-read guard must appear, and gate the omission conclusion
        assert "masked-empty guard" in q5
        assert "re-read the raw" in q5
        assert "session-journal.jsonl" in q5
        # RE-ANCHORED, strength unchanged. The landmark was the omission
        # note's old wording, "pre-dates per-dispatch stamping" -- which
        # asserted a CAUSE the reader cannot verify and which is false in
        # both branches: a session predating the emit and a session whose
        # emit path is broken produce the same journal signature. The note
        # now states what was OBSERVED, so the landmark moves with it.
        # Identical assertion shape: same three guard requirements, same
        # single ordering check, one substituted landmark string.
        assert "no `dispatch_site` events recorded this arc" in q5
        # ordering: the guard must precede the OMISSION conclusion
        assert q5.index("masked-empty guard") < q5.index(
            "no `dispatch_site` events recorded this arc"
        )

    def test_q5_stops_on_a_failed_snapshot_read_rather_than_passing_empty(
        self, q5
    ):
        """THE ONE PLACE IN THIS QUESTION WHERE AN INCORRECT FALLBACK READS
        AS A CLEAN GREEN.

        A failed snapshot read that degrades to an empty list sends EVERY
        member to the fallback. Q5 then reports the OLD as-dispatched numbers
        carrying the NEW name, and the surface shows a plausible figure with
        a correct label. No count moves and no error prints, so the reader
        has nothing to notice.

        The instruction must therefore carry FOUR parts, and each assertion
        below is one of them: the stop rule, the explicit ban on the empty
        list, the consequence that explains WHY the ban is not optional, and
        the carve-out that keeps a genuine zero usable. The consequence half
        is what stops a reader reasoning back to the empty list as a
        harmless default.
        """
        assert "A FAILED snapshot read STOPS this question" in q5
        assert "the same as a failed `dispatch_site` read" in q5
        assert "Do NOT fall back to an empty list" in q5
        assert "report the OLD as-dispatched numbers under the new name" in q5
        assert "with nothing on the surface to show it" in q5
        # The carve-out: an empty read that survives the masked-empty guard
        # is a legitimate zero, so the stop rule cannot swallow it.
        assert "legitimate zero" in q5
        assert "report `fallback_used` equal to `sites`" in q5

    def test_q5_states_the_stop_rule_before_it_calls_the_join_helper(self, q5):
        """ORDERING, and it is load-bearing rather than cosmetic. A stop rule
        stated AFTER the helper call reaches a reader who has called the
        helper. The ban must precede the call it applies to."""
        assert q5.index("A FAILED snapshot read STOPS this question") < q5.index(
            "extract_final_dispatch_coverage"
        )

    def test_q5_scopes_the_snapshot_read_to_the_same_arc_as_the_site_read(
        self, q5
    ):
        """The platform reuses task ids across arcs, so an unscoped snapshot
        read can supply a prior arc's value for a current arc's member. The
        helper cannot check this, so the instruction owns it."""
        assert "read --type task_metadata_snapshot" in q5
        assert "the SAME `--since` value" in q5
        assert "BOTH reads carry the validated `--since '{arc_start}'`" in q5

    def test_q5_denominator_comes_from_the_one_pass_helper(self, q5):
        """Successor to the retired-helper pin. The NEGATIVE half is the
        load-bearing one: it is what fails if someone reinstates
        `count_task_b_dispatch_sites` in the prose, which would leave the
        retirement silently incomplete.

        The positive half names `extract_final_dispatch_coverage` since the
        Q5 join: MEMBERSHIP continues to come from the `dispatch_site`
        stream, and the VALUE comes from the latest `task_metadata_snapshot`
        of the same task."""
        assert "extract_final_dispatch_coverage" in q5
        assert "count_task_b_dispatch_sites" not in q5, (
            "the retired 3-marker helper must not be named in Q5 — a "
            "reinstated mention means the denominator was not actually moved"
        )

    def test_q5_denominator_argument_is_the_resolvable_count_not_the_site_count(
        self, q5
    ):
        """THE FORBIDDEN-DENOMINATOR RULE, as an assertion instead of prose.

        Q5 marks `sites` FORBIDDEN as the third argument to
        `compute_variety_divergence`, because passing it revives the coverage
        ratio this question no longer reports. Until this pin that rule was
        carried by prose alone: a future edit could swap the argument back and
        nothing in the suite would go red.

        SCOPED TO THE DOCUMENTED CALL SPAN, which is what makes it a pin
        rather than decoration. Both obvious alternatives are unfalsifiable
        for this purpose, and the occupancy counts are why -- do not loosen
        this to either of them:

        - A line-wide `"sites" not in q5` is RED TODAY against correct prose.
          `sites` occurs SEVEN times on the Q5 line as a legitimately
          reported COUNT; the token is forbidden in one position, not on the
          line.
        - A bare `"len(variety_totals)" in <file>` cannot fail for the reason
          it claims. That literal occurs FOUR times in wrap-up.md, so the
          sample-output region alone satisfies it and the call could be
          changed to `sites` with the pin still green.

        The FULL call expression occurs exactly ONCE in the file, so lifting
        it is the one form that observes the argument this rule governs.
        Verified alongside it: the prefix `compute_variety_divergence(`
        selects exactly one backticked span, and correctly EXCLUDES the bare
        no-paren mention inside the FORBIDDEN sentence itself.

        Read the call out of the markdown rather than restating it, for the
        same reason `_backticked_expression` exists: a restatement drifts from
        the instruction silently and pins nothing.

        MUTATION THAT REDDENS: change the third argument in wrap-up.md from
        `len(variety_totals)` to `sites`.
        """
        call = _backticked_expression(q5, "compute_variety_divergence(")

        assert call.endswith("len(variety_totals))"), (
            "Q5's divergence call must take len(variety_totals) as its third "
            "argument -- the number of dispatches carrying a RESOLVABLE "
            f"stamp. Got: {call!r}"
        )
        assert "sites" not in call, (
            "`sites` has been passed to compute_variety_divergence. That "
            "revives the coverage ratio Q5 no longer reports, by making the "
            "denominator every recorded dispatch site rather than the ones "
            "whose stamp resolves. `sites` is a reported COUNT, never a "
            f"denominator. Got: {call!r}"
        )

    def test_q5_reads_the_dispatch_site_stream(self, q5):
        """The denominator is the dispatch_site event's EXISTENCE. Reading the
        old variety-independent markers for Q5 would rebuild the two-population
        split the one-event topology exists to remove."""
        assert "--type dispatch_site" in q5
        for retired_marker in ("--type agent_dispatch", "--type review_dispatch",
                               "--type remediation"):
            assert retired_marker not in q5

    def test_q5_derives_the_numerator_from_no_separate_stream(self, q5):
        """The coupling risk MOVED when the helper made `stamped <= total` an
        identity: the identity is internal to the helper and cannot stop a
        CONSUMER bypassing it. A future edit could take `total` from
        `extract_dispatch_coverage` while re-deriving the numerator from its
        own `dispatch_variety` read — the original two-population defect,
        verbatim, one level out.

        Keyed on the READ COMMAND, not the bare string, and that is
        deliberate: `dispatch_variety` must stay NAMEABLE as the Class-1
        cross-check witness. The pin is on it not being a RATIO INPUT.

        MUTATION THAT REDDENS: add a `read --type dispatch_variety` to Q5.
        """
        assert "--type dispatch_variety" not in q5, (
            "Q5 has reacquired its own dispatch_variety read. Both ratio "
            "terms must come from the single extract_dispatch_coverage pass; "
            "a second stream is how the numerator and denominator drift onto "
            "different populations. Naming dispatch_variety as the Class-1 "
            "cross-check witness is fine — READING it as a ratio input is not."
        )

    def test_q5_arc_scoped_with_since(self, q5):
        assert "Inputs and shared Q5-Q7 prerequisites" in q5
        assert "EVERY one carrying `--since '{arc_start}'`" in q5

    # --- Q6 structural safety (no explicit re-read guard; rests on the
    #     parse bans + fallback-on-zero) ---

    def test_q6_states_single_json_array_parse(self, q6):
        assert "SINGLE JSON array" in q6
        assert "json.loads(output)" in q6

    def test_q6_bans_output_masking_pipes(self, q6):
        assert "2>/dev/null" in q6
        assert "|| echo" in q6
        assert "`head`" in q6

    def test_q6_fallback_on_zero_prevents_fabricated_signal_rate(self, q6):
        """The structural guarantee that a masked/crashed read (which yields
        zero teachback_ack events) cannot be reported as a real 0% signal
        rate: Q6 falls back to the task store ONLY when the journal yields
        zero teachback_ack events (#966 noted a masked read corrupting Q6 to
        0% vs a true 44%)."""
        assert "zero `teachback_ack` events" in q6
        assert "fall back to the legacy" in q6

    def test_q6_arc_scoped_with_since(self, q6):
        assert "--since" in q6

    def test_q6_carries_masked_empty_reread_guard(self, q6):
        """Q6 now MIRRORS Q5's explicit masked-empty re-read guard (the
        follow-up that closes the Q5/Q6 asymmetry): a masked/crashed
        teachback_ack read must be re-confirmed against the raw journal before
        any conclusion, so it cannot be reported as a real 0% cargo-cult
        signal rate (#966 noted a masked read corrupting Q6 to 0% vs a true
        44%). The guard must precede the fallback-on-zero branch."""
        assert "masked-empty guard" in q6
        assert "re-read the raw" in q6
        assert "session-journal.jsonl" in q6
        assert "_ts_ge(e.get(\"ts\"), arc_start)" in q6
        assert "genuine zero" in q6
        assert "never a zero rate" in q6
        # ordering: the re-read guard precedes the fallback-on-zero clause
        assert q6.index("masked-empty guard") < q6.index("fall back to the legacy")


class TestTotalExtraction:
    """Both retrospective extractions must be TOTAL — defined for every event
    the journal can hand them — rather than aborting on the first bad one.

    Both questions used a direct-indexing comprehension, so a single event
    missing its key raised `KeyError` and destroyed the WHOLE list, not just
    that event. The blast radius is the session, not the row: measured
    2026-07-28 over 2254 journals, 20 `dispatch_variety` events lacked a usable
    `variety['total']`, and they sat in 8 sessions holding 289 events between
    them.

    These are STRUCTURAL (prose) assertions with the vacuity limit the sibling
    class documents: they detect the guard being removed or reworded away, but
    cannot prove an LLM obeys it at runtime. The negative halves are the
    load-bearing ones — they fail if a direct-indexing form is reinstated.
    """

    @pytest.fixture
    def wrapup_content(self):
        return WRAPUP_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def q5(self, wrapup_content):
        return _question_line(wrapup_content, "5. **Variety divergence**")

    @pytest.fixture
    def q6(self, wrapup_content):
        return _question_line(wrapup_content, "6. **Variety acknowledgment signals**")

    def test_q5_sources_both_terms_through_the_shared_helper(self, q5):
        """C-6 moved the resolver call INSIDE `extract_dispatch_coverage`, so
        Q5 no longer names it. The guarantee it carried — that the total is
        resolved rather than direct-indexed — did not go away; it moved into
        code, and is pinned behaviourally by
        `test_variety_divergence.py::TestExtractDispatchCoverage`. A prose pin
        satisfiable by an import line is weaker than a behavioural one.

        The helper is `extract_final_dispatch_coverage` since the Q5 join,
        and the behavioural pin moved with it, to
        `test_variety_divergence.py::TestExtractFinalDispatchCoverage`."""
        assert "extract_final_dispatch_coverage" in q5
        assert "shared.variety_divergence" in q5

    def test_q5_does_not_direct_index_the_total(self, q5):
        """The negative half: `[e["variety"]["total"] for e in events]` is the
        shape that aborts the whole comprehension on one malformed stamp."""
        assert '["variety"]["total"]' not in q5
        assert "for e in events]" not in q5

    def test_q5_sample_keys_the_two_delta_forms_off_surfaced(self, wrapup_content):
        """The rendering must key off the returned `surfaced` flag rather than
        re-deriving the verdict from `delta`. A renderer that decides for
        itself when a delta is worth surfacing reimplements the threshold, and
        the two copies drift silently — the reader sees a confident verdict
        that the computation never reached.

        AIMED AT THE SAMPLE BLOCK, NOT THE QUESTION LINE, and that is load-
        bearing: the flag is what SEPARATES the two forms, so the property
        lives where the forms are declared. The question line names `surfaced`
        only as one dict key among seven, and a pin there would assert a
        membership that survives every rendering mistake this guards against.

        SCOPE: structural (prose), with the vacuity limit this class documents
        — it detects the keying being dropped or reworded away, not an LLM
        disobeying it at run time. The behavioural half (that
        `compute_variety_divergence` sets `surfaced` correctly) is pinned in
        `test_per_dispatch_variety.py` and `test_variety_divergence.py`.

        MUTATION THAT REDDENS: strip `surfaced=True` / `surfaced=False` from
        the two form headers, leaving them separated by the delta wording
        alone — i.e. a sample that renders regardless of the flag.
        """
        headers = [
            line for line in wrapup_content.splitlines()
            if line.startswith("**SURFACED**") or line.startswith("**IN BAND**")
        ]
        assert len(headers) == 2, (
            "expected exactly one SURFACED and one IN BAND form header in the "
            f"Q5 sample block, found {len(headers)}: {headers}"
        )
        surfaced_header = next(h for h in headers if h.startswith("**SURFACED**"))
        in_band_header = next(h for h in headers if h.startswith("**IN BAND**"))
        assert "`surfaced=True`" in surfaced_header, (
            "the SURFACED form no longer declares `surfaced=True`. Without it "
            "the sample tells a renderer to decide from `delta` alone, which "
            "is the threshold reimplemented in prose."
        )
        assert "`surfaced=False`" in in_band_header, (
            "the IN BAND form no longer declares `surfaced=False`. The two "
            "forms are then separated by wording rather than by the flag the "
            "computation actually returns."
        )

    def test_q6_guards_the_flag_extraction(self, q6):
        assert "isinstance(e.get(\"rationale_articulates_this_dispatch\"), str)" in q6

    def test_q6_does_not_direct_index_the_flag(self, q6):
        assert "for e in events]" not in q6

    def test_q6_excludes_unreadable_acks_from_both_terms(self, q6):
        """Counting an unreadable ack in the denominator but not the numerator
        dilutes the rate toward "no concern" — the direction that hides the
        signal Q6 exists to surface."""
        assert "total_teachbacks = len(flags)" in q6
        assert "len(events) - len(flags)" in q6

    def test_q6_does_not_divide_when_no_flag_is_readable(self, q6):
        """`len(flags)` can be 0 while `events` is non-empty — a divisor the
        previous `len(events)` form could never produce."""
        assert "total_teachbacks > 0" in q6


class TestQ5ExtractionDependsOnTheDimensionSumCandidate:
    """The Q5 EXTRACTION PATH — the expression wrap-up.md actually carries —
    rests on `resolve_variety_total`'s FOURTH candidate (the four-dimension
    sum) and on no other.

    Measured 2026-07-28 across 2254 session journals: of the 20 `dispatch_variety`
    events lacking a usable `variety['total']`, 20 recover — and 0 are
    recoverable through candidate 2 (`score`) or candidate 3
    (`metadata.variety_score`). The malformed shape in the wild is exactly the
    four dimensions with no total.

    These RUN the documented expression rather than calling the resolver
    directly, and the distinction is the whole point. The resolver's own
    dimension-sum tests already exist in `test_teachback_schema.py`, so a test
    that calls `resolve_variety_total(...)` here would re-pin THEIR guard under
    a Q5 name and leave the extraction path unpinned. Executing the expression
    couples the candidate to this consumer: removing candidate 4 breaks these,
    and so does reverting the expression to a direct `variety['total']` index.

    Dropping candidate 4 fails DARK — the resolver returns `None` rather than
    raising, so the events vanish from the numerator and coverage drops with
    nothing to attribute it to.
    """

    # The malformed stamp exactly as it appears in the journal corpus: the four
    # dimensions, no total, no score. Candidates 1 and 2 cannot fire on it, and
    # candidate 3 reads a metadata argument the Q5 expression never passes — so
    # a recovery here can only have come from the dimension sum.
    CORPUS_MALFORMED_EVENT = {
        "task_id": "2",
        "variety": {"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 2},
    }
    CANONICAL_EVENT = {"task_id": "1", "variety": {"total": 9}}

    @pytest.fixture
    def q5_expression(self, q5):
        """The documented chain, IN ORDER: the helper call, then the unpack.

        Q5 carries the join as two spans. Lifting the unpack alone runs a
        statement that reads nothing from `events`, so the pair is the unit
        that pins the extraction path.
        """
        return [
            _backticked_expression(q5, "coverage = "),
            _backticked_expression(q5, "variety_totals, "),
        ]

    def test_higher_candidates_cannot_fire_on_the_corpus_shape(self):
        """Fixture control. Without it, a shape that candidate 1 resolved would
        pass every test below while proving nothing about candidate 4."""
        variety = self.CORPUS_MALFORMED_EVENT["variety"]
        assert "total" not in variety
        assert "score" not in variety
        assert resolve_variety_total({}, {"variety_score": 8}) == 8, (
            "candidate 3 is live, so its silence below is the Q5 expression "
            "not passing metadata — not the candidate being gone"
        )

    def test_extraction_recovers_the_corpus_malformed_stamp(self, q5_expression):
        namespace = _run_q5_extraction_on_the_fallback_path(
            q5_expression, [self.CANONICAL_EVENT, self.CORPUS_MALFORMED_EVENT]
        )
        assert namespace["variety_totals"] == [9, 8], (
            "the four-dimension sum is the ONLY path by which the 20 malformed "
            "stamps in the dispatch_site corpus reach Q5's numerator; drop "
            "candidate 4 and this returns [9], silently, with no exception"
        )

    def test_extraction_survives_an_unresolvable_event(self, q5_expression):
        """The C-1 defect itself: ONE bad event used to destroy the whole list.
        A stamp that no candidate can resolve must cost only its own row."""
        events = [
            {"task_id": "1", "variety": {"novelty": 2}},  # partial: unresolvable
            self.CANONICAL_EVENT,
            {"task_id": "3"},                              # no variety key at all
            "not-a-dict",                                  # not even an object
        ]
        namespace = _run_q5_extraction_on_the_fallback_path(q5_expression, events)
        assert namespace["variety_totals"] == [9]

    def test_extraction_is_empty_not_raising_when_nothing_resolves(
        self, q5_expression
    ):
        events = [{"task_id": "1", "variety": {}}, {"task_id": "2"}]
        namespace = _run_q5_extraction_on_the_fallback_path(q5_expression, events)
        assert namespace["variety_totals"] == []


class TestQ6ExtractionSurvivesAnUnreadableAck:
    """The Q6 flag extraction, run the same way and for the same reason.

    Measured 2026-07-28: 616 `teachback_ack` events, 0 missing the flag — the
    journal schema types that key `str`, which is why. So this guard is
    defense-in-depth against a shape the corpus has not yet produced, which is
    exactly when it is cheap to add.
    """

    @pytest.fixture
    def q6_expression(self, q6):
        return _backticked_expression(q6, "flags = ")

    def test_extraction_drops_only_the_unreadable_acks(self, q6_expression):
        events = [
            {"task_id": "1", "rationale_articulates_this_dispatch": "yes"},
            {"task_id": "2"},                                        # key absent
            {"task_id": "3", "rationale_articulates_this_dispatch": None},
            {"task_id": "4", "rationale_articulates_this_dispatch": "no"},
            "not-a-dict",
        ]
        assert _run_q6_extraction(q6_expression, events)["flags"] == ["yes", "no"]

    def test_extraction_is_empty_not_raising_when_no_flag_is_readable(
        self, q6_expression
    ):
        """`len(flags) == 0` with `events` non-empty is the divisor the previous
        `len(events)` form could never produce — the case Q6's prose must (and
        does) tell the reader not to divide by."""
        assert _run_q6_extraction(q6_expression, [{"task_id": "1"}])["flags"] == []


class TestQ5DocumentedChainReadsTheSnapshotStream:
    """THE DOCUMENTED CHAIN MUST BE EXERCISED WITH A NON-EMPTY SNAPSHOT LIST,
    and nothing else in this file does that.

    Each other Q5 caller in this file calls
    `_run_q5_extraction_on_the_fallback_path`, where the snapshot stream is
    not read at all. On that path the documented chain returns the same
    numbers as the pre-join helper, so a change that stops the join reading
    the snapshot stream leaves each of those arms green. THE ARMS BELOW ARE
    THE ONLY ONES IN THIS FILE THAT CAN GO RED FOR A SNAPSHOT-SIDE DEFECT.

    The runner split makes the path visible at each call site. It does NOT
    make this class redundant, because no signature can require that a file
    holds an arm on the joined path.

    The two arms below feed the streams DISAGREEING values through the
    expressions the file actually carries, so they fail if the documented
    chain stops taking the value from the snapshot.
    """

    SITE_TOTAL = 9
    SNAPSHOT_TOTAL = 12

    @pytest.fixture
    def q5_expression(self, q5):
        """The documented chain, IN ORDER: the helper call, then the unpack."""
        return [
            _backticked_expression(q5, "coverage = "),
            _backticked_expression(q5, "variety_totals, "),
        ]

    def _events(self):
        return [{"task_id": "1", "variety": {"total": self.SITE_TOTAL}}]

    def _snapshots(self):
        return [
            {
                "task_id": "1",
                "ts": "2026-06-15T12:00:00Z",
                "metadata": {"variety": {"total": self.SNAPSHOT_TOTAL}},
            }
        ]

    def test_the_chain_takes_the_value_from_the_snapshot_not_the_site(
        self, q5_expression
    ):
        """The whole cause of the join. The site says 9 and the snapshot says
        12, so the distribution must carry 12."""
        namespace = _run_q5_extraction(
            q5_expression, self._events(), self._snapshots()
        )
        assert namespace["variety_totals"] == [self.SNAPSHOT_TOTAL]

    def test_the_chain_keeps_membership_from_the_dispatch_site_stream(
        self, q5_expression
    ):
        """MEMBERSHIP does not widen. The extra snapshot names a task that no
        member names, and it carries a total DISTINCT from each member value,
        which is what makes this arm able to fail."""
        snapshots = self._snapshots() + [
            {
                "task_id": "99",
                "ts": "2026-06-15T12:00:00Z",
                "metadata": {"variety": {"total": 15}},
            }
        ]
        namespace = _run_q5_extraction(q5_expression, self._events(), snapshots)
        assert namespace["sites"] == 1
        assert namespace["variety_totals"] == [self.SNAPSHOT_TOTAL]


class TestQ5DocumentedChainProducesTheThirdStream:
    """THE THIRD STREAM MUST BE PRODUCED BY THE DOCUMENTED CHAIN ITSELF, and
    nothing else in this file executes its binding or filter.

    The substring pins on wrap-up.md (test_dispatch_emission_prose.py) pin
    the Q5 third-argument wiring as TEXT: they never execute it. The runners
    seed `dispatch_assessed` in the namespace themselves, so a prose edit
    that drops the `va_events` binding, reorders it behind the filter, or
    drops the filter span keeps every other arm green. THE ARM BELOW IS THE
    ONLY ONE IN THIS FILE THAT LIFTS THE BINDING AND THE FILTER from the
    documented text and runs them ahead of the join, so the third argument
    reaches the join BY THE PROSE'S OWN HAND, not the harness's.

    It fails on three prose regressions the other pins miss together: an
    unbound `va_events` (the lift or the exec raises), a dropped filter
    span (the join reads the runner's empty seed and the un-snapshotted
    member stays unresolved), and a dropped third argument (same unresolved
    member through a different break).
    """

    FEATURE_TOTAL = 9
    ASSESSED_TOTAL = 8
    SNAPSHOT_TOTAL = 10

    @pytest.fixture
    def third_stream_chain(self, q5):
        """The documented chain, IN ORDER: the scoped-read binding, the
        scope filter, the helper call, then the unpack."""
        return [
            _backticked_expression(q5, "va_events = "),
            _backticked_expression(q5, "dispatch_assessed = "),
            _backticked_expression(q5, "coverage = "),
            _backticked_expression(q5, "variety_totals, "),
        ]

    def _read_output(self):
        """The scoped `variety_assessed` read's stdout, as `read` prints it:
        the arc's feature-level event (stream content the filter must
        exclude) and one dispatch-marked mirror for the un-snapshotted
        member."""
        return json.dumps(
            [
                {
                    "type": "variety_assessed",
                    "task_id": "1",
                    "variety": {"total": self.FEATURE_TOTAL},
                    "ts": "2026-06-15T12:00:01Z",
                },
                {
                    "type": "variety_assessed",
                    "task_id": "2",
                    "scope": "dispatch",
                    "variety": {"total": self.ASSESSED_TOTAL},
                    "ts": "2026-06-15T12:00:02Z",
                },
            ]
        )

    def test_the_documented_binding_and_filter_feed_the_join(
        self, third_stream_chain
    ):
        """Member 1 resolves from its snapshot (arm 1); member 2 has no
        snapshot and no site stamp, so it resolves ONLY through the third
        stream the documented chain produced (arm 2b)."""
        events = [{"task_id": "1"}, {"task_id": "2"}]
        snapshots = [
            {
                "task_id": "1",
                "ts": "2026-06-15T12:00:03Z",
                "metadata": {"variety": {"total": self.SNAPSHOT_TOTAL}},
            },
        ]
        namespace = _run_q5_extraction(
            third_stream_chain, events, snapshots,
            read_output=self._read_output(),
        )
        assert namespace["variety_totals"] == [
            self.SNAPSHOT_TOTAL, self.ASSESSED_TOTAL
        ]
        assert namespace["coverage"]["fallback_used"] == 1
        assert namespace["coverage"]["total_unresolved"] == 0


class TestTheRunnerDivideIsEnforcedAtRunTime:
    """The three guarantees of the runner divide, pinned as BEHAVIOUR.

    THE DIVIDE IS ENFORCED BY A SIGNATURE AND BY A NAMESPACE. That is
    stronger than a docstring, because it acts at run time rather than on a
    reader. IT DOES NOT PIN ITSELF. Remove the empty-list refusal, or put
    `snapshot_events` back into the Q6 namespace, and no other arm in this
    file goes red. These three arms are what fails.

    EACH ARM CARRIES ITS POSITIVE HALF, and that half is load-bearing. An
    arm that asserts only a raise passes against a runner that raises on
    each input, so each arm below also shows the accepted call returning its
    value. Without that half the arm cannot separate a working guard from a
    broken runner.
    """

    EVENTS = [{"task_id": "1", "variety": {"total": 9}}]
    SNAPSHOTS = [
        {
            "task_id": "1",
            "ts": "2026-06-15T12:00:00Z",
            "metadata": {"variety": {"total": 12}},
        }
    ]

    @pytest.fixture
    def q5_expression(self, q5):
        """The documented chain, IN ORDER: the helper call, then the unpack."""
        return [
            _backticked_expression(q5, "coverage = "),
            _backticked_expression(q5, "variety_totals, "),
        ]

    def test_the_joined_runner_refuses_an_empty_snapshot_list(
        self, q5_expression
    ):
        """An empty snapshot list sends each member to the fallback, so a
        caller that named the JOINED runner would receive the OTHER path.
        The refusal is what stops that substitution from being silent.

        The `match` ties this arm to the refusal and not to any ValueError.
        """
        with pytest.raises(ValueError, match="FALLBACK"):
            _run_q5_extraction(q5_expression, self.EVENTS, [])
        # POSITIVE HALF: a non-empty list is accepted and takes the join.
        # The site says 9 and the snapshot says 12, so 12 proves the joined
        # path ran rather than the fallback.
        namespace = _run_q5_extraction(
            q5_expression, self.EVENTS, self.SNAPSHOTS
        )
        assert namespace["variety_totals"] == [12]

    def test_the_joined_runner_has_no_default_for_the_snapshot_argument(
        self, q5_expression
    ):
        """A DEFAULT IS WHAT ALLOWED A CALLER TO SELECT A PATH IT DID NOT
        CHOOSE. A two-argument call must stop with a TypeError rather than
        proceed on a fallback nobody asked for."""
        with pytest.raises(TypeError):
            _run_q5_extraction(q5_expression, self.EVENTS)
        # POSITIVE HALF: the three-argument call is accepted.
        namespace = _run_q5_extraction(
            q5_expression, self.EVENTS, self.SNAPSHOTS
        )
        assert namespace["variety_totals"] == [12]

    def test_a_q6_expression_cannot_reach_the_snapshot_stream(self):
        """The Q6 namespace carries NO `snapshot_events` name, so a Q6
        expression that reaches for one STOPS rather than read a silent
        empty list.

        THE EXPRESSION HERE IS SYNTHETIC ON PURPOSE. This arm pins the
        NAMESPACE and not the Q6 prose, because the Q6 prose reads no
        snapshot stream today. A future prose edit that reached for one must
        fail loudly, and this arm is what makes that the outcome.
        """
        with pytest.raises(NameError):
            _run_q6_extraction("probe = len(snapshot_events)", self.EVENTS)
        # POSITIVE HALF: the Q6 namespace does carry `events`, so the raise
        # above is an absent NAME and not a broken runner.
        namespace = _run_q6_extraction("probe = len(events)", self.EVENTS)
        assert namespace["probe"] == 1


class TestComPACTDoesNotInvokeTheRetrospective:
    """Scope pin for a load-bearing docstring claim with no test: variety_
    divergence.py's `resolve_arc_start` rests its never-mis-scopes argument
    (no retro-path caller passes a comPACT/rePACT feature id) on the
    boundary claim "a comPACT workflow does not invoke the retrospective".
    The claim's basis is STRUCTURAL, and this pins it as it exists today.

    The line between reference and invocation, derived from what the
    retrospective actually keys on: comPACT.md NAMES the wrap-up
    Orchestration Retrospective as the downstream consumer of its feature
    stamp (the Feature-task variety stamp step) — a naming — but carries
    NONE of the retrospective's EXECUTION tokens, the arc-scoped reads and
    join helpers wrap-up's Q5/Q6 questions are built from. If a future
    comPACT flow grows a real retrospective invocation, this pin fails and
    the docstring claim (with the never-mis-scopes argument resting on it)
    must be revisited in the same change.

    `compute_variety_divergence` is deliberately NOT in the token set: it
    appears in comPACT.md TODAY as a consumer naming ("the load-bearing
    input `compute_variety_divergence` reads"), the same reference class as
    the wrap-up mention, and the retrospective cannot run from a naming
    alone — its execution signature is the reads plus the coverage join.
    """

    COMPACT_PATH = (
        Path(__file__).parent.parent / "commands" / "comPACT.md"
    )

    RETROSPECTIVE_EXECUTION_TOKENS = (
        "arc_start",                      # the arc-scope derivation
        "resolve_arc_start",
        "--type dispatch_site",           # the Q5 population read
        "--type task_metadata_snapshot",  # the Q5 final-value read
        "--type journal_emit_skipped",    # the Q5 sample-loss read
        "extract_final_dispatch_coverage",
        "--type teachback_ack",           # the Q6 signal read
    )

    def test_compact_names_the_consumer_but_carries_no_invocation(self):
        compact = self.COMPACT_PATH.read_text(encoding="utf-8")
        # The reference half, anchored so the pin cannot pass vacuously:
        # comPACT points at the retrospective as the consumer of its stamp.
        assert "wrap-up Orchestration Retrospective" in compact, (
            "comPACT.md no longer names the wrap-up Orchestration "
            "Retrospective as its stamp's consumer — the reference half of "
            "this pin moved; re-anchor it to the current wording."
        )
        present = [
            token
            for token in self.RETROSPECTIVE_EXECUTION_TOKENS
            if token in compact
        ]
        assert not present, (
            f"comPACT.md carries retrospective execution tokens {present}; "
            "the resolve_arc_start docstring's 'comPACT does not invoke the "
            "retrospective' claim no longer holds structurally — revisit "
            "that docstring in the same change."
        )


_SJ_SCRIPT = str(
    Path(__file__).parent.parent / "hooks" / "shared" / "session_journal.py"
)


def _journal_cli(session_dir, *args, data=None):
    command = [sys.executable, _SJ_SCRIPT, *args, "--session-dir", str(session_dir)]
    if data is not None:
        command.extend(["--data", json.dumps(data)])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(session_dir.parent)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def _write_event(session_dir, event_type, **fields):
    return _journal_cli(session_dir, "write", "--type", event_type, data=fields)


def _read_events(session_dir, event_type, since=None):
    args = ["read", "--type", event_type]
    if since is not None:
        args.extend(["--since", since])
    return json.loads(_journal_cli(session_dir, *args).stdout)


def _stamp(total):
    return {
        "novelty": 2,
        "scope": 2,
        "uncertainty": 2,
        "risk": 2,
        "total": total,
    }


def _run_documented_witness(q5, feature_task, events, feature_task_id):
    """Execute wrap-up's witness expressions, stopping before any Q5 join."""
    arc_start = resolve_arc_start(events, feature_task_id)
    namespace = {
        "feature_task": feature_task,
        "feature_task_id": feature_task_id,
        "variety_assessed_events": events,
        "arc_start": arc_start,
    }
    if arc_start is None:
        return "UNAVAILABLE", namespace
    if "variety" not in feature_task.get("metadata", {}):
        return "UNAVAILABLE", namespace
    for prefix in (
        "feature_metadata =",
        "task_witness =",
        "latest_feature_events =",
    ):
        _exec_documented_chain(_backticked_expression(q5, prefix), namespace)
    if len(namespace["latest_feature_events"]) != 1:
        return "UNAVAILABLE", namespace
    for prefix in ("feature_event =", "event_witness ="):
        _exec_documented_chain(_backticked_expression(q5, prefix), namespace)
    feature_event = namespace["feature_event"]
    if (
        "variety" not in feature_event
        or namespace["event_witness"] != namespace["task_witness"]
    ):
        return "UNAVAILABLE", namespace
    feature_variety = resolve_variety_total(feature_event["variety"])
    namespace["feature_variety"] = feature_variety
    return (
        "NO FEATURE VARIETY" if feature_variety is None else "WITNESSED",
        namespace,
    )


class TestQ5Q7ReportingContract:
    def test_shared_gate_precedes_metrics_and_none_never_omits_since(self):
        content = WRAPUP_PATH.read_text(encoding="utf-8")
        q5 = _question_line(content, "5. **Variety divergence**")
        q6 = _question_line(content, "6. **Variety acknowledgment signals**")
        q7 = _question_line(content, "7. **HANDOFF refusal degradations**")
        assert q5.index("Before ANY Q5-Q7 metric read") < q5.index(
            "read --type dispatch_site"
        )
        assert q5.index("feature_task_id` must be non-`None") < q5.index(
            "arc_start = resolve_arc_start"
        )
        assert q5.index("A `None` result is UNAVAILABLE") < q5.index(
            "read --type dispatch_site"
        )
        for line in (q5, q6, q7):
            assert "when `arc_start` is `None`" not in line
            assert "_ts_ge(e.get(\"ts\"), arc_start)" in line
        assert "Q6 and Q7: skipped" in q5
        assert "Q6 UNAVAILABLE" in q6
        assert "Q7 UNAVAILABLE" in q7

    def test_five_outcomes_and_one_ordered_suffix(self):
        content = WRAPUP_PATH.read_text(encoding="utf-8")
        assert "**UNAVAILABLE — shared-prerequisite failure**" in content
        assert content.count("**One common suffix**") == 1
        assert "no `dispatch_site` events recorded this arc" in content
        assert "including task-id-reuse dedup" in content
        assert "last line of the form" not in content
        assert content.count("resolvable totals across") == 3
        assert content.count(
            "The current-arc feature assessment exists, but its total is unresolvable."
        ) == 1
        assert "exactly one of five `{form}` values" in content
        assert "Q6 actually ran and its dual-trigger fired" in content
        suffix = content.split("**One common suffix**", 1)[1]
        ordered = [
            "dispatch sites left no sample",
            "recorded dispatch_site events had no resolvable variety total",
            "fallback_used",
            "total_unresolved",
            "superseded:",
            "late_stamped",
            "dispatch_malformed",
            "superseded_dimensions_only",
            "dimensions_incomparable",
            "Each final value rests on emitter preconditions",
            "feature_task_id:",
            "The current-arc feature assessment exists",
        ]
        positions = [suffix.index(token) for token in ordered]
        assert positions == sorted(positions)

    def test_documented_witness_match_mismatch_and_duplicate(self):
        q5 = _question_line(
            WRAPUP_PATH.read_text(encoding="utf-8"), "5. **Variety divergence**"
        )
        event = {
            "type": "variety_assessed",
            "task_id": "100",
            "ts": "2026-06-14T12:00:00Z",
            "variety": _stamp(14),
        }
        feature_task = {
            "metadata": {"variety": {**_stamp(14), "risk_rationale": "text"}}
        }
        outcome, _ = _run_documented_witness(q5, feature_task, [event], "100")
        assert outcome == "WITNESSED"
        mismatch = {
            "metadata": {"variety": {**_stamp(13), "risk_rationale": "text"}}
        }
        outcome, _ = _run_documented_witness(q5, mismatch, [event], "100")
        assert outcome == "UNAVAILABLE"
        outcome, _ = _run_documented_witness(
            q5, feature_task, [event, dict(event)], "100"
        )
        assert outcome == "UNAVAILABLE"

    def test_real_journal_unresolvable_witness_is_no_feature(self, tmp_path):
        session_dir = tmp_path / "session"
        ts = "2026-06-14T12:00:00Z"
        _write_event(
            session_dir,
            "variety_assessed",
            task_id="100",
            variety={"total": "unresolvable"},
            ts=ts,
        )
        _write_event(
            session_dir,
            "dispatch_site",
            task_id="2",
            variety=_stamp(14),
            ts="2026-06-14T13:00:00Z",
        )
        events = _read_events(session_dir, "variety_assessed")
        feature_task = {
            "metadata": {
                "variety": {"total": "unresolvable", "risk_rationale": "text"}
            }
        }
        q5 = _question_line(
            WRAPUP_PATH.read_text(encoding="utf-8"), "5. **Variety divergence**"
        )
        outcome, namespace = _run_documented_witness(
            q5, feature_task, events, "100"
        )
        assert outcome == "NO FEATURE VARIETY"
        assert namespace["arc_start"] == ts
        assert len(_read_events(session_dir, "dispatch_site", ts)) == 1

    @pytest.mark.parametrize("missing_metadata", [True, False])
    def test_real_journal_prior_arc_cannot_anchor_without_a_witness(
        self, tmp_path, missing_metadata
    ):
        session_dir = tmp_path / "session"
        _write_event(
            session_dir,
            "variety_assessed",
            task_id="100",
            variety=_stamp(8),
            ts="2026-06-13T12:00:00Z",
        )
        _write_event(
            session_dir,
            "dispatch_site",
            task_id="2",
            variety=_stamp(8),
            ts="2026-06-13T13:00:00Z",
        )
        _write_event(
            session_dir,
            "dispatch_site",
            task_id="3",
            variety=_stamp(14),
            ts="2026-06-14T13:00:00Z",
        )
        feature_task = {
            "metadata": (
                {} if missing_metadata
                else {"variety": {**_stamp(9), "risk_rationale": "text"}}
            )
        }
        events = _read_events(session_dir, "variety_assessed")
        q5 = _question_line(
            WRAPUP_PATH.read_text(encoding="utf-8"), "5. **Variety divergence**"
        )
        outcome, namespace = _run_documented_witness(
            q5, feature_task, events, "100"
        )
        assert outcome == "UNAVAILABLE"
        assert "coverage" not in namespace

    def test_real_journal_scoped_raw_guard_ignores_prior_reused_task(self, tmp_path):
        session_dir = tmp_path / "session"
        prior_ts = "2026-06-13T12:00:00Z"
        arc_start = "2026-06-14T12:00:00Z"
        _write_event(
            session_dir,
            "dispatch_site",
            task_id="42",
            variety=_stamp(8),
            ts=prior_ts,
        )
        _write_event(
            session_dir,
            "variety_assessed",
            task_id="100",
            variety=_stamp(9),
            ts=arc_start,
        )
        assert _read_events(session_dir, "dispatch_site", arc_start) == []
        raw_events = [
            json.loads(line)
            for line in (session_dir / "session-journal.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        scoped_raw = [
            event for event in raw_events
            if event.get("type") == "dispatch_site"
            and _ts_ge(event.get("ts"), arc_start)
        ]
        assert scoped_raw == []
        assert any(
            event.get("type") == "dispatch_site" for event in raw_events
        )
