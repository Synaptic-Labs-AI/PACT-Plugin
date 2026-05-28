"""
Multi-phase noise-budget regression for the OPERATIONAL-LULL-AT-PHASE-
BOUNDARY defense — Tier-1 aggregate emission count across N sequential
phase-transitions with M specialists each.

The load-bearing invariant: under an in_progress umbrella orchestration,
firing TaskCompleted on EVERY completed specialist in the matrix must
yield ZERO teardown emissions — regardless of N (phase count) or M
(specialists per phase). Only the umbrella's own transition to
completed produces the single legitimate teardown.

WHY THIS LIVES IN A SIBLING FILE
================================

Cross-cutting noise-budget regression suite is conceptually distinct
from "one more Tier-1 variant in test_teardown_request_emitter_phase_
lull.py". The C4 RED-state fixtures pin per-shape behavior (V1, V2,
V3, V4, V6); the C5 phantom-green flip + V8 commit pins the per-shape
post-fix behavior; C8 here pins the AGGREGATE multi-phase emission
count under the same Gate-6 defense. File-size signal also: the Tier-1
file is already 798 LOC after backend's C5; adding 200+ LOC of
parametrized noise-budget would push it past 1000 LOC.

PER LEAD-DIRECTED WHOLESALE REPLACEMENT
=======================================

The scaffold smoke at TestMultiPhaseNoiseBudgetScaffold in
test_teardown_request_emitter_phase_lull.py was always documented as
"C8 replaces with noise-budget invariant" (see that class's docstring).
This file's invariant supersedes it; the C4 scaffold class is removed
in this same commit. The `_build_multi_phase_fixture` helper STAYS in
the Tier-1 file (consumed via direct import from there) — single
fixture-builder, two consumers, no duplication.

COUNTER-TEST-BY-REVERT CARDINALITY
==================================

Revert backend's C5 Gate-6 source ONLY in teardown_request_emitter.py:
  Expect: all 9 cells of the parametrized matrix RED — the noise-budget
  invariant fires once per cell because pre-fix, each specialist's
  TaskCompleted produces a teardown emission (count_active_tasks reaches
  zero across N×M completed specialists with only the umbrella excluded
  by lead-owner filter).
  Cardinality: {9 RED}.

Revert devops's C6 Tier-2 mirror ONLY: this file UNCHANGED (GREEN).
The Tier-1 emission-site is what the noise-budget counts; Tier-2 marker
writes are out of scope here (separate file test_wake_lifecycle_emitter_
phase_lull.py covers that surface independently).

THE ASYMMETRIC RESPONSE PATTERN
===============================

Pre-fix: ONE specialist completion in an N=3 × M=3 orchestration
produces 9 teardowns (one per fire, since all specialists complete and
the lead-owner filter excludes only the umbrella). Post-fix: ZERO
teardowns under the umbrella. The 9-to-0 transition under one source-
file change is the bug-class signature; the parametrized matrix
quantifies how badly the bug scales (1x at N=1,M=1; 9x at N=3,M=3).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Cross-file imports: the parametrized fixture builder + the lead-frame
# subprocess helpers live in the Tier-1 file (per fixture-location
# convention they sit next to the V1-V8 per-shape tests). Single source
# of truth for the multi-phase fixture shape; two consumers (Tier-1
# per-shape file + this noise-budget file).
from test_teardown_request_emitter_phase_lull import (
    _build_multi_phase_fixture,
    _lead_taskcompleted_payload,
    _read_journal_events,
    _run_emitter_subprocess,
    _teardown_directive_in_stdout,
)


# =============================================================================
# Helpers — narrow to noise-budget semantics. The aggregate emission
# count across N×M specialist completions is the load-bearing surface;
# helpers below collapse the per-fire details into the aggregate.
# =============================================================================


def _fire_taskcompleted_for_each_specialist(fixture):
    """Walk the multi-phase fixture's specialist task IDs and fire a
    lead-frame TaskCompleted hook for each. Returns the list of
    per-fire outcomes (booleans: True iff that fire emitted the
    teardown directive).

    NOTE: every specialist is laid down in status='completed' by
    `_build_multi_phase_fixture`. Each TaskCompleted fire sees the
    same disk state (all completed + umbrella in_progress). Pre-fix,
    each unique task_id passes Gate 2's O_EXCL marker once and emits;
    post-fix, Gate 6 short-circuits before Gate 2 so no marker is
    ever burned.
    """
    home = fixture["home"]
    project_dir = fixture["project_dir"]
    team_name = fixture["team_name"]

    fires = []
    for task_id in fixture["specialist_task_ids"]:
        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team_name, task_id)),
            env_extra={
                "HOME": str(home), "CLAUDE_PROJECT_DIR": project_dir,
            },
        )
        assert rc == 0, (
            f"hook must exit 0 for specialist {task_id}; stderr={err}"
        )
        fires.append(_teardown_directive_in_stdout(out))
    return fires


def _count_teardown_journal_events(fixture):
    """Return the number of teardown_request events written to the
    lead's session journal under the fixture's HOME. Independent of
    the directive-emission count (the journal write is the falsifiable
    primitive; the directive is the recoverable hint)."""
    events = _read_journal_events(
        fixture["home"], fixture["project_dir"], fixture["session_id"],
        event_type="teardown_request",
    )
    return len(events)


# =============================================================================
# Multi-phase noise-budget invariant — the load-bearing aggregate
# guarantee. Across the 9-cell N=1,2,3 × M=1,2,3 matrix, firing
# TaskCompleted on every specialist under an in_progress umbrella
# produces ZERO teardowns (directive + journal both empty).
#
# Without Gate 6, this invariant breaks at N=1,M=1 (1 teardown) and
# scales linearly to N=3,M=3 (9 teardowns). The bug scales with
# orchestration size — exactly the signal seen in pact-450f3d63
# (6 Tier-1 teardown_request events over 4 phase-transitions).
# =============================================================================


class TestMultiPhaseNoiseBudgetUnderUmbrella:
    """Multi-phase noise-budget regression. Under an in_progress
    umbrella orchestration, the aggregate teardown emission count
    across N×M specialist completions MUST be zero — for every cell of
    the parametrized matrix. Counter-test: revert Gate-6 source ONLY
    flips all 9 cells RED."""

    @pytest.mark.parametrize(
        "n_phases,n_specialists_per_phase",
        [
            # 3x3 baseline (diagnostic-minimum coverage).
            (1, 1), (1, 2), (1, 3),
            (2, 1), (2, 2), (2, 3),
            (3, 1), (3, 2), (3, 3),
            # N=4 operational-norm extension (peer-review test-engineer
            # review-finding M3 / dispatch B6): PR-B's own session was
            # N>=4 teammate completions in flight by peer-review time
            # (backend + devops + test + auditor all in flight). The
            # 3x3 baseline is calibrated to the diagnostic; N=4 covers
            # the empirical-norm regime. Compute cost ~0.4s additional
            # for 4 cells x 2 halves (directive + journal) = 8 tests.
            (4, 1), (4, 2), (4, 3), (4, 4),
        ],
        ids=lambda v: f"n{v}",
    )
    def test_noise_budget_zero_teardown_directives_under_umbrella(
        self, tmp_path, n_phases, n_specialists_per_phase,
    ):
        """Aggregate directive-emission count across N×M specialist
        TaskCompleted fires under an in_progress umbrella MUST be zero.

        The directive-emission half of the noise-budget invariant; the
        journal-event half is the sibling test below. Two assertions
        per cell because the production code emits the directive AND
        writes the journal event in the same code path — both halves
        must be silent under Gate 6, and verifying both catches a
        partial regression (e.g., a refactor that skips one but not
        the other).
        """
        fixture = _build_multi_phase_fixture(
            tmp_path, n_phases=n_phases,
            n_specialists_per_phase=n_specialists_per_phase,
        )
        fires = _fire_taskcompleted_for_each_specialist(fixture)

        emit_count = sum(fires)
        assert emit_count == 0, (
            f"NOISE-BUDGET VIOLATION (directive half): "
            f"N={n_phases} phases x M={n_specialists_per_phase} specialists "
            f"under in_progress umbrella emitted {emit_count} teardown "
            f"directives. Expected 0 (Gate 6 must suppress all per-specialist "
            f"fires). per-fire booleans: {fires}"
        )

    @pytest.mark.parametrize(
        "n_phases,n_specialists_per_phase",
        [
            # 3x3 baseline (diagnostic-minimum coverage).
            (1, 1), (1, 2), (1, 3),
            (2, 1), (2, 2), (2, 3),
            (3, 1), (3, 2), (3, 3),
            # N=4 operational-norm extension (peer-review test-engineer
            # review-finding M3 / dispatch B6): PR-B's own session was
            # N>=4 teammate completions in flight by peer-review time
            # (backend + devops + test + auditor all in flight). The
            # 3x3 baseline is calibrated to the diagnostic; N=4 covers
            # the empirical-norm regime. Compute cost ~0.4s additional
            # for 4 cells x 2 halves (directive + journal) = 8 tests.
            (4, 1), (4, 2), (4, 3), (4, 4),
        ],
        ids=lambda v: f"n{v}",
    )
    def test_noise_budget_zero_teardown_journal_events_under_umbrella(
        self, tmp_path, n_phases, n_specialists_per_phase,
    ):
        """Aggregate journal-event count across N×M specialist
        TaskCompleted fires under an in_progress umbrella MUST be zero.

        The journal-event half of the invariant; the on-disk
        teardown_request event is the falsifiable primitive consumed by
        Tier-4 cron-staleness fallback. A partial-suppression bug that
        somehow skipped the directive but still wrote the journal
        event would trigger phantom Teardown emissions later via the
        cron replay path — this assertion catches that drift class.
        """
        fixture = _build_multi_phase_fixture(
            tmp_path, n_phases=n_phases,
            n_specialists_per_phase=n_specialists_per_phase,
        )
        _fire_taskcompleted_for_each_specialist(fixture)

        journal_count = _count_teardown_journal_events(fixture)
        assert journal_count == 0, (
            f"NOISE-BUDGET VIOLATION (journal half): "
            f"N={n_phases} phases x M={n_specialists_per_phase} specialists "
            f"under in_progress umbrella wrote {journal_count} "
            f"teardown_request journal events. Expected 0 (Gate 6 must "
            f"short-circuit before the append_event call)."
        )


# =============================================================================
# Sibling invariant — terminal teardown legitimacy. After the umbrella
# itself completes, firing TaskCompleted on the umbrella's task_id
# MUST yield exactly one legitimate teardown emission. This pins the
# "Gate 6 doesn't over-suppress" property at the end-of-orchestration
# boundary: the legitimate session-end signal is still surfaced.
# =============================================================================


class TestNoiseBudgetTerminalTeardownEmits:
    """After umbrella completes, exactly one teardown emits on the
    umbrella's own TaskCompleted fire. Pins legitimate session-end
    surfacing — Gate 6 must NOT over-suppress when the umbrella
    transitions out of in_progress."""

    @pytest.mark.parametrize(
        "n_phases,n_specialists_per_phase",
        # Diagonal sample of matrix — bounded runtime via diagonal-only
        # rather than full N×M. (4,4) extension paired with B6 / M3
        # operational-norm extension above.
        [(1, 1), (2, 2), (3, 3), (4, 4)],
        ids=lambda v: f"n{v}",
    )
    def test_umbrella_completion_emits_single_legitimate_teardown(
        self, tmp_path, n_phases, n_specialists_per_phase,
    ):
        """After all specialists fire under the umbrella (no teardowns
        per the noise-budget invariant) AND the umbrella itself is
        marked completed AND a TaskCompleted fires for the umbrella,
        exactly one teardown emits — the legitimate session-end
        signal.

        Sampled on the matrix diagonal (1×1, 2×2, 3×3) rather than the
        full 9 cells to keep test-suite runtime bounded; the per-shape
        Tier-1 V3 test pins the (0 specialists, no umbrella) baseline
        legitimate-teardown case; this test pins the orthogonal
        (N×M specialists, completed umbrella) post-orchestration case.
        """
        fixture = _build_multi_phase_fixture(
            tmp_path, n_phases=n_phases,
            n_specialists_per_phase=n_specialists_per_phase,
        )
        # Fire every specialist under the in_progress umbrella —
        # noise-budget invariant guarantees zero teardowns at this point.
        _fire_taskcompleted_for_each_specialist(fixture)
        assert _count_teardown_journal_events(fixture) == 0, (
            "Setup invariant: no teardown journaled under in_progress "
            "umbrella before the umbrella transitions."
        )

        # Transition the umbrella to completed on disk + fire the
        # TaskCompleted hook for it. Gate 6 returns False (no more
        # in_progress umbrella); Gates 1-4 hold (count==0, no
        # continuation, no marker); the legitimate teardown emits.
        home = fixture["home"]
        team_name = fixture["team_name"]
        umbrella_id = fixture["umbrella_task_id"]

        # Rewrite the umbrella task to status=completed.
        umbrella_path = (
            home / ".claude" / "tasks" / team_name / f"{umbrella_id}.json"
        )
        umbrella_data = json.loads(umbrella_path.read_text(encoding="utf-8"))
        umbrella_data["status"] = "completed"
        umbrella_path.write_text(json.dumps(umbrella_data), encoding="utf-8")

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team_name, umbrella_id)),
            env_extra={
                "HOME": str(home),
                "CLAUDE_PROJECT_DIR": fixture["project_dir"],
            },
        )
        assert rc == 0, f"umbrella-fire hook must exit 0; stderr={err}"

        # Exactly one directive emitted on the umbrella fire.
        assert _teardown_directive_in_stdout(out), (
            f"Umbrella completion must emit legitimate teardown directive; "
            f"got no directive. Gate 6 may be over-suppressing the "
            f"end-of-orchestration signal. stdout={out!r}"
        )

        # Exactly one teardown_request event journaled in total.
        final_count = _count_teardown_journal_events(fixture)
        assert final_count == 1, (
            f"End-of-orchestration legitimate teardown count: expected "
            f"exactly 1, got {final_count}. The N×M specialist fires "
            f"must contribute 0 (noise-budget) and the umbrella's own "
            f"completion contributes 1 (legitimate session-end)."
        )
