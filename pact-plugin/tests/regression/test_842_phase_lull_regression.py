"""
Maintained regression suite for the OPERATIONAL-LULL-AT-PHASE-BOUNDARY
class — promoted from the Phase A diagnostic harness at
tests/diagnostic/diagnostic_842_repro.py (cherry-picked at 7f2e87a4 of
14e8e2ba on investigate/842-phase-a-diagnostic).

The diagnostic harness was a one-shot 7-variant falsification exercise
that empirically traced all 6 Tier-1 teardown_request events in
pact-450f3d63's session journal to phase-transition gaps (not
teachback A->B handoffs as initially hypothesized). The harness ran
as a script, printed diagnostic traces to stdout, and was preserved
on a side branch.

This file is the maintained pytest port: same 7 variants, same fixture
intent, hardened into self-contained assertions that future regressions
trip RED. V2 (H4 unowned-B latent bug) is RETAINED as defensive coverage
per the PR-B plan ("V2 H4 unowned-B latent-bug coverage MUST be retained
in C10").

PORTABILITY NOTE
================

V7 (session-journal replay of pact-450f3d63) is environment-specific —
the source journal lives at
~/.claude/pact-sessions/PACT-prompt/450f3d63-178b-4296-8e68-3fc36961bcaa/
session-journal.jsonl, which only exists on the original investigation
host. The pytest port skips V7 with a clear marker when the journal is
absent, so CI on fresh checkouts stays green. The retained V7 test
preserves the empirical-replay capability for future investigation
runs on the same host.

RELATIONSHIP TO C4 + C7 + C8 TEST FILES
=======================================

This file is the END-TO-END defensive layer (per-variant subprocess
fires that mirror the diagnostic's exact fixture shapes). The per-shape
files cover the same surface with more focused assertions:

- test_teardown_request_emitter_phase_lull.py — Tier-1 per-shape
- test_wake_lifecycle_emitter_phase_lull.py — Tier-2 per-shape
- test_phase_lull_noise_budget.py — multi-phase aggregate invariant

V1, V3, V4, V6 from the harness are functionally equivalent to the
C4 + C5 per-shape tests; they ride here as defense-in-depth (a
deliberate redundancy because the harness was the empirical falsification
ground truth — its shapes are the historical record of what was actually
observed in pact-450f3d63). V2 is the load-bearing UNIQUE coverage in
this file — the H4 latent-bug regression guard that no other PR-B test
covers.

COUNTER-TEST-BY-REVERT POSTURE
==============================

V1 + V6: revert Gate 6 source ONLY → RED (same as the per-shape tests).
V2: STABLE through Gate 6 (no umbrella; H4 latent bug persists).
V3: STABLE through Gate 6 (legitimate teardown baseline).
V4: STABLE through Gate 6 (count-based suppression independent of Gate 6).
V5: STABLE — owner-empty-string path; documents the wiring split-write
    window without making it a Gate-6 concern.
V7: environment-specific; skipped on hosts without the source journal.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Reuse the canonical SSOT helpers from fixtures/disk_shapes.py.
from fixtures.disk_shapes import (
    UMBRELLA_SUBJECT_PREFIXES,
    make_specialist_task,
    make_umbrella_task,
)

# Reuse the canonical subprocess + filesystem helpers from the C4 file.
# Cross-file import keeps a single source of truth for the lead-frame
# stdin payload shape, session-context layout, and emitter discovery.
from test_teardown_request_emitter_phase_lull import (
    _lead_taskcompleted_payload,
    _read_journal_events,
    _run_emitter_subprocess,
    _setup_lead_session,
    _teardown_directive_in_stdout,
    _write_task,
)


# =============================================================================
# V1 — Canonical orchestrate teachback A->B handoff in a phase-lull window.
# Mirrors the harness's variant_canonical_teachback_handoff (diagnostic
# L100-144) with post-fix assertion shape.
# =============================================================================


class TestRegressionV1CanonicalTeachbackHandoff:
    """V1 regression: phase-lull during canonical teachback A->B
    handoff under in_progress umbrella. Gate 6 suppresses; without it,
    teardown fires (the pact-450f3d63 bug shape)."""

    def test_v1_no_teardown_emit_during_phase_lull(self, tmp_path):
        """Lead-owned umbrella in_progress + completed teachback A +
        pending B with wiring split-write window owner="". Post-fix,
        Gate 6 suppresses; the teardown directive is absent and no
        teardown_request event is journaled.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-regression-v1"
        sid, pdir = _setup_lead_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U1", subject_prefix="Feature: ", subject_suffix="regression v1",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "A1", owner="preparer",
            subject="preparer: TEACHBACK regression v1",
            status="completed",
        ))
        b_task = make_specialist_task(
            "B1", owner="", subject="preparer: work for regression v1",
            status="pending",
        )
        b_task["blockedBy"] = ["A1"]
        _write_task(home, team, b_task)

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A1")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V1 regression: Gate 6 must suppress teardown directive "
            "during phase-lull (umbrella in_progress + count==0)."
        )
        events = _read_journal_events(
            home, pdir, sid, event_type="teardown_request",
        )
        assert events == [], (
            f"V1 regression: no teardown_request event must be "
            f"journaled during phase-lull; got {events!r}"
        )


# =============================================================================
# V2 — H4 latent bug: Task B owner=null at TaskCompleted(A); NO umbrella.
#
# Defensive regression guard per PR-B plan. The H4 latent bug is
# documented but NOT fixed by PR-B; Gate 6 has no umbrella to short-
# circuit against here, so the bug persists. This test PINS that
# behavior so a future "fix the H4 case" change is intentional, not
# accidental — and so a future Gate-6 widening doesn't accidentally
# suppress the H4 fire under unrelated reasoning.
# =============================================================================


class TestRegressionV2H4UnownedBLatentBug:
    """V2 regression — H4 latent bug DEFENSIVE coverage. Documents
    the unfixed-by-PR-B behavior so future changes touching this
    code path acknowledge the latent bug rather than silently
    inverting it.

    Per PR-B plan: 'V2 H4 unowned-B latent-bug coverage MUST be
    retained in C10 conversion as defensive regression guard.'
    """

    def test_v2_teardown_fires_on_h4_unowned_b_no_umbrella(self, tmp_path):
        """H4 latent bug: B exists with owner=null at TaskCompleted(A);
        no umbrella in_progress. count_active_tasks excludes B
        (empty owner), driving count to 0; teardown fires. Gate 6
        does NOT touch this path (no umbrella signal).

        This is the documented unfixed-by-PR-B behavior; if this test
        flips GREEN in a future change, EITHER the H4 bug was
        intentionally fixed (great — flip the assertion explicitly
        and remove this docstring) OR a Gate-6 widening accidentally
        suppressed the H4 fire (bad — investigate).
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-regression-v2"
        _setup_lead_session(home, team)

        # NO umbrella — Gate 6 has no signal.
        _write_task(home, team, make_specialist_task(
            "A2", owner="preparer",
            subject="preparer: TEACHBACK regression v2",
            status="completed",
        ))
        b_unowned = make_specialist_task(
            "B2", owner=None,
            subject="preparer: work regression v2", status="pending",
        )
        b_unowned["owner"] = None
        b_unowned["blockedBy"] = ["A2"]
        _write_task(home, team, b_unowned)

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A2")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert _teardown_directive_in_stdout(out), (
            "V2 regression: H4 latent bug PERSISTS — teardown fires on "
            "H4 unowned-B with no umbrella. Gate 6 has no umbrella to "
            "short-circuit against. If this asserts True flips, "
            "either H4 was intentionally fixed (good) or Gate 6 was "
            "widened (investigate)."
        )


# =============================================================================
# V3 — Baseline: A=completed, no other tasks, no umbrella. Legitimate
# teardown. Gate 6 must NOT over-suppress.
# =============================================================================


class TestRegressionV3LegitimateBaseline:
    """V3 regression — legitimate teardown baseline. Pins that
    Gate 6 doesn't over-suppress the genuine 1->0 end-of-orchestration
    transition."""

    def test_v3_teardown_fires_when_no_other_work(self, tmp_path):
        """A completed, no other tasks, no umbrella: the canonical
        legitimate-teardown case. Gate 6 returns False (no umbrella);
        Gates 1-4 hold; teardown fires.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-regression-v3"
        _setup_lead_session(home, team)

        _write_task(home, team, make_specialist_task(
            "A3", owner="preparer",
            subject="preparer: regression v3 only task",
            status="completed",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A3")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert _teardown_directive_in_stdout(out), (
            "V3 regression: teardown legitimately fires when no other "
            "work remains. Gate 6 must NOT over-suppress."
        )


# =============================================================================
# V4 — Cross-teammate concurrent (the pact-450f3d63 surface pattern):
# X completes while Y is in_progress. count_active_tasks=1; teardown
# suppressed via Gate 3. Gate 6 independent.
# =============================================================================


class TestRegressionV4CrossTeammateConcurrent:
    """V4 regression — cross-teammate concurrent work. Pins existing
    Gate-3 count-based suppression."""

    def test_v4_teardown_suppressed_when_y_in_progress(self, tmp_path):
        """X completed, Y in_progress. count_active_tasks=1 (Y);
        Gate 3 suppresses. Gate 6 independent. Assertion pins Gate-3
        regression in the cross-teammate shape."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-regression-v4"
        _setup_lead_session(home, team)

        _write_task(home, team, make_specialist_task(
            "X1", owner="preparer",
            subject="preparer: regression v4 X done",
            status="completed",
        ))
        _write_task(home, team, make_specialist_task(
            "Y1", owner="architect",
            subject="architect: regression v4 Y in_progress",
            status="in_progress",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "X1")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V4 regression: cross-teammate suppression via Gate 3."
        )


# =============================================================================
# V5 — Strict #842 reading: A completed BEFORE B's owner-TaskUpdate
# landed (canonical orchestrate.md step-4 wiring split-write window).
# B has owner="" (empty string, distinct from H4's owner=null).
# =============================================================================


class TestRegressionV5OwnerEmptyStringWiringWindow:
    """V5 regression — owner empty-string wiring window. Documents the
    canonical orchestrate.md step-4 split-write window without making
    it a Gate-6 concern. With an in_progress umbrella present, Gate 6
    suppresses; without an umbrella, this collapses to the H4 path.

    This test pins the umbrella-present arm (otherwise V5 would just
    re-cover V2's surface)."""

    def test_v5_no_teardown_emit_with_umbrella_and_empty_owner_b(self, tmp_path):
        """A=completed, B=pending+owner='' (the wiring split-write
        window), umbrella in_progress. Gate 6 suppresses regardless
        of B's owner shape; the wiring-window has no impact on the
        post-fix behavior."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-regression-v5"
        sid, pdir = _setup_lead_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U5", subject_prefix="Feature: ", subject_suffix="regression v5",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "A5", owner="preparer",
            subject="preparer: TEACHBACK regression v5",
            status="completed",
        ))
        b_empty = make_specialist_task(
            "B5", owner="", subject="preparer: work regression v5",
            status="pending",
        )
        b_empty["blockedBy"] = ["A5"]
        _write_task(home, team, b_empty)

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A5")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V5 regression: Gate 6 suppresses regardless of B's owner "
            "shape during the wiring split-write window."
        )


# =============================================================================
# V6 — H1 variant: lead-owned umbrella + completed teammate task; no
# other tasks. The cleanest phase-lull shape; matches the pact-450f3d63
# bug surface directly.
# =============================================================================


class TestRegressionV6LeadOwnedUmbrellaPlusCompletedTeammate:
    """V6 regression — pure phase-lull (umbrella + completed teammate).
    The pact-450f3d63 bug shape; post-fix Gate 6 suppresses."""

    def test_v6_no_teardown_emit_with_umbrella_and_completed_teammate(self, tmp_path):
        """Single umbrella in_progress + single completed teammate
        task. Post-fix, Gate 6 short-circuits before Gate 3; teardown
        suppressed."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-regression-v6"
        sid, pdir = _setup_lead_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U6", subject_prefix="Feature: ", subject_suffix="regression v6",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "T6", owner="preparer",
            subject="preparer: regression v6 done",
            status="completed",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "T6")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V6 regression: pure phase-lull (umbrella + completed "
            "teammate) suppresses teardown post-fix."
        )


# =============================================================================
# V7 — Environment-specific session-journal replay of pact-450f3d63.
# The original journal lives on a single investigation host; the
# pytest port skips when absent so CI on fresh checkouts stays green.
# When the journal IS present, it asserts the original 6 Tier-1
# teardown events all carry the lead_terminal_taskupdate reason
# (the empirical signature of the OPERATIONAL-LULL bug).
# =============================================================================


PACT_450F3D63_JOURNAL = (
    Path.home() / ".claude" / "pact-sessions" / "PACT-prompt"
    / "450f3d63-178b-4296-8e68-3fc36961bcaa" / "session-journal.jsonl"
)


@pytest.mark.skipif(
    not PACT_450F3D63_JOURNAL.exists(),
    reason=(
        "pact-450f3d63 session journal not present on this host; "
        "V7 replay is environment-specific to the original investigation."
    ),
)
class TestRegressionV7Pact450f3d63JournalReplay:
    """V7 regression — empirical replay of pact-450f3d63's
    teardown_request events. Skipped on hosts without the source
    journal; preserved for repeat investigation on the host that has it.
    """

    def test_v7_journal_has_at_least_one_tier1_teardown(self):
        """The pact-450f3d63 session journal contains the empirical
        record that motivated PR-B. At minimum one Tier-1
        teardown_request event is present in the journal. The Phase A
        diagnostic counted 6; this test only pins the >=1 lower bound
        to keep the assertion robust if the journal is re-played or
        trimmed."""
        tier1_count = 0
        with PACT_450F3D63_JOURNAL.open(encoding="utf-8") as f:
            for line in f:
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "teardown_request":
                    continue
                if str(evt.get("tier", "")) == "1":
                    tier1_count += 1
        assert tier1_count >= 1, (
            "V7 regression: pact-450f3d63 journal must contain at "
            f"least one Tier-1 teardown_request event (empirical "
            f"PR-B motivation); got {tier1_count}."
        )

    def test_v7_journal_tier1_events_have_lead_terminal_taskupdate_reason(self):
        """Every Tier-1 teardown_request event in the source journal
        carries reason='lead_terminal_taskupdate' (the post-#763
        canonical reason token for Tier-1 fires). This pins the
        empirical signature for the OPERATIONAL-LULL bug class — any
        future Tier-1 event with a different reason indicates a new
        bug surface or a reason-token rename to track."""
        with PACT_450F3D63_JOURNAL.open(encoding="utf-8") as f:
            reasons = set()
            for line in f:
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") != "teardown_request":
                    continue
                if str(evt.get("tier", "")) == "1":
                    reasons.add(evt.get("reason", ""))
        if not reasons:
            pytest.skip("no Tier-1 teardown_request events in journal")
        assert reasons == {"lead_terminal_taskupdate"}, (
            f"V7 regression: Tier-1 reason tokens unexpected; "
            f"got {reasons!r}. PR-B's empirical motivation was the "
            f"'lead_terminal_taskupdate' shape only."
        )
