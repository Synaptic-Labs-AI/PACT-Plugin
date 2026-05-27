"""
Phase-lull regression fixtures for teardown_request_emitter.py — Tier-1
emission-site coverage for the OPERATIONAL-LULL-AT-PHASE-BOUNDARY class.

The bug surface (#842): during a phase-transition gap, the active-task
count legitimately reaches zero for a brief window (the prior phase's
specialists have completed; the next phase's specialists have not yet
been dispatched). The lead-owned umbrella task remains in_progress to
mark the orchestration as live, but the count_active_tasks predicate
(which excludes lead-owned tasks) sees zero and the Tier-1 emitter
fires a teardown_request. Empirically observed against session
pact-450f3d63: all 6 Tier-1 teardown_request events correlate with
phase-transition gaps, NOT teachback A->B handoffs.

The Gate-6 fix (backend's C5): introduce a sixth gate
(has_in_progress_umbrella_orchestration) BEFORE Gate 3, suppressing
teardown emission whenever an umbrella-orchestration task is in_progress
on the team — regardless of count_active_tasks's tally.

PHANTOM-GREEN DISCIPLINE
========================

This file (C4) lands BEFORE backend's C5. Per the test-engineer +
backend-coder + lead synthesis at plan-mode, C4's V1/V6 assertions
must match CURRENT (pre-fix) broken behavior — they MUST be GREEN at
this commit. Backend's C5 then INVERTS those assertions in the same
atomic commit as the Gate-6 source. This is the phantom-green coupling
proof: the assertions are demonstrably tied to the source change, not
independent assertions that pass for unrelated reasons.

  Variant   At C4 (pre-fix)        Backend C5 flip       After-fix
  -------   --------------------   -------------------   ---------
  V1        teardown FIRES (bug)   assert SUPPRESSED     suppressed
  V6        teardown FIRES (bug)   assert SUPPRESSED     suppressed
  V8        (added by C5)          ---                   suppressed
  V2        teardown FIRES (H4)    UNCHANGED             fires (H4 latent)
  V3        teardown FIRES (legit) UNCHANGED             fires
  V4        teardown SUPPRESSED    UNCHANGED             suppressed

V2 (H4 unowned-B latent bug) is documented but NOT fixed by PR-B —
defensive coverage retained in the C10 promoted harness conversion.
V3 (no other tasks) and V4 (cross-teammate Y in_progress) are
negative controls — they exercise paths Gate 6 does NOT touch, so they
remain GREEN through both C4 and C5 and serve as the counter-test
discriminator. Reverting Gate 6 source alone would flip V1+V6+V8 (and
the multi-phase noise-budget regression in C8) to RED while leaving
V2/V3/V4 GREEN — the asymmetric cardinality is the load-bearing
proof that Gate 6 is the specific suppression mechanism.

COUNTER-TEST-BY-REVERT CARDINALITY (recorded per plan)
======================================================

Revert backend's Gate-6 source ONLY (keep all C4+C5 assertions):
  Expect: V1 RED + V6 RED + V8 RED + multi-phase noise-budget RED.
  V2 + V3 + V4 remain GREEN (they don't depend on Gate 6).
  Cardinality: {3-fixture RED + parametrized-matrix RED}.

Revert Tier-2 mirror ONLY (devops's C6): Tier-1 tests UNCHANGED
(this file is GREEN); Tier-2 file's tests RED. Verifies the two
emission sites have independent test coverage despite calling the same
shared helper.

FIXTURE SSOT IMPORT
===================

All on-disk task + team-config shapes are constructed via the SSOT
helpers at pact-plugin/tests/fixtures/disk_shapes.py. The
UMBRELLA_SUBJECT_PREFIXES tuple imported there is the same constant
the production has_in_progress_umbrella_orchestration helper imports;
drift between fixture-side and production-side prefixes is mechanically
impossible by construction (Risk 1 mitigation).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.disk_shapes import (
    UMBRELLA_SUBJECT_PREFIXES,
    make_specialist_task,
    make_team_config,
    make_umbrella_task,
)

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "teardown_request_emitter.py"


# =============================================================================
# Subprocess + filesystem fixture helpers — co-located per #551 fixture-
# location convention. Mirrors test_teardown_request_emitter.py's helpers
# but consumes the SSOT shape helpers from fixtures/disk_shapes.py.
# =============================================================================


def _run_emitter_subprocess(stdin_payload, env_extra=None):
    """Invoke teardown_request_emitter.py as a subprocess (production
    fidelity — same process model the platform uses). Returns
    (returncode, stdout, stderr) tuple.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(EMITTER)],
        input=payload_bytes,
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _write_session_context(
    home,
    session_id,
    project_dir,
    team_name,
    *,
    lead_session_id=None,
    members=None,
    lead_agent_id=None,
):
    """Mirror of test_teardown_request_emitter._write_session_context.
    Writes a session-context file + team-config so is_lead_context and
    count_active_tasks resolve correctly under the test HOME.
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": "",
            "started_at": "2026-05-27T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = (
        lead_session_id if lead_session_id is not None else session_id
    )
    cfg = {"leadSessionId": effective_lead}
    if lead_agent_id is not None:
        cfg["leadAgentId"] = lead_agent_id
    if members:
        cfg["members"] = list(members)
    (team_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def _write_task(home, team_name, task_dict):
    """Persist a task dict (from make_umbrella_task / make_specialist_task)
    to disk at ~/.claude/tasks/{team}/{id}.json."""
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_dict['id']}.json").write_text(
        json.dumps(task_dict), encoding="utf-8",
    )


def _journal_path(home, project_dir, session_id):
    slug = Path(project_dir).name
    return (
        home / ".claude" / "pact-sessions" / slug / session_id
        / "session-journal.jsonl"
    )


def _read_journal_events(home, project_dir, session_id, event_type=None):
    """Return all events (or filtered by event_type) from the session
    journal; [] if the journal file does not exist."""
    path = _journal_path(home, project_dir, session_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event_type is None or evt.get("type") == event_type:
            events.append(evt)
    return events


def _teardown_directive_in_stdout(stdout: str) -> bool:
    """True iff the emitter's stdout JSON contains a teardown directive
    (additionalContext under hookSpecificOutput). suppressOutput payloads
    are False; the directive emission is the only positive-emission shape.
    """
    if not stdout.strip():
        return False
    try:
        # Production emitter writes exactly one JSON payload to stdout.
        obj = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return False
    if not isinstance(obj, dict):
        return False
    if obj.get("suppressOutput"):
        return False
    hso = obj.get("hookSpecificOutput")
    return isinstance(hso, dict) and "additionalContext" in hso


def _setup_lead_session(home, team_name):
    """Common lead-session boilerplate. Returns (sid, project_dir)
    tuple. The lead-frame stdin shape (no agent_id, no teammate_name)
    classifies as lead under is_lead_context."""
    sid = "lead-sid"
    project_dir = "/tmp/phase-lull-test"
    _write_session_context(
        home, sid, project_dir, team_name,
        lead_session_id=sid,
        members=[
            {"name": "lead", "agentId": "agent-lead"},
            {"name": "preparer", "agentId": "agent-preparer"},
            {"name": "architect", "agentId": "agent-architect"},
        ],
        lead_agent_id="agent-lead",
    )
    return sid, project_dir


def _lead_taskcompleted_payload(team_name, task_id):
    """Lead-frame TaskCompleted stdin payload (no teammate_name,
    no agent_id — empirically the lead-context signature)."""
    return {
        "session_id": "lead-sid",
        "transcript_path": "/tmp/phase-lull-test/transcript.jsonl",
        "cwd": "/tmp/phase-lull-test",
        "agent_type": "PACT:pact-orchestrator",
        "hook_event_name": "TaskCompleted",
        "task_id": task_id,
        "task_subject": "lead-driven phase-lull fire",
        "task_description": "phase-lull fixture",
        "team_name": team_name,
    }


# =============================================================================
# SSOT contract pin — verify the disk_shapes.py constants we depend on are
# present and shaped correctly. Decouples this file's failures from
# unrelated regressions in the production helper or other consumers.
# =============================================================================


class TestSSOTContractPin:
    """Pin the SSOT contract surface this file depends on. If
    UMBRELLA_SUBJECT_PREFIXES drifts in shape (becomes a list, becomes
    empty, loses a key prefix), all downstream fixtures fail-fast here
    rather than at variant-test time with confusing assertion errors.
    """

    def test_umbrella_prefixes_is_tuple(self):
        """SSOT contract: UMBRELLA_SUBJECT_PREFIXES is an immutable
        tuple. Mutating-it-in-place attempts must fail at import time."""
        assert isinstance(UMBRELLA_SUBJECT_PREFIXES, tuple)

    def test_umbrella_prefixes_nonempty(self):
        """SSOT contract: at least one canonical prefix is registered.
        An empty tuple would silently disable Gate 6 (predicate never
        matches anything) — guard against accidental wipe."""
        assert len(UMBRELLA_SUBJECT_PREFIXES) >= 1

    def test_feature_prefix_registered(self):
        """The 'Feature: ' prefix is the canonical lead-owned umbrella
        marker emitted by /PACT:orchestrate / /PACT:comPACT. If this
        drifts, every Feature umbrella stops suppressing teardown."""
        assert "Feature: " in UMBRELLA_SUBJECT_PREFIXES


# =============================================================================
# V1 — Canonical orchestrate teachback A->B handoff in a phase-lull window.
#
# Setup: lead-owned umbrella task in_progress (subject "Feature: ..."),
# Task A (TEACHBACK) just completed, Task B (work) blocked-by A
# pending owner-wiring TaskUpdate. The teammate's owner field is briefly
# absent during the wiring split-write window. count_active_tasks sees
# zero (umbrella excluded by lead-owner filter; B excluded because
# owner is empty per H4 reasoning); the umbrella is the only signal
# that orchestration is live.
#
# CURRENT BEHAVIOR (C4 GREEN, pre-fix): teardown FIRES — bug.
# POST-FIX (C5 GREEN, after Gate 6): teardown SUPPRESSED.
# =============================================================================


class TestV1CanonicalPhaseLullTeachbackHandoff:
    """V1 — Phase-lull during canonical teachback A->B handoff while
    umbrella is in_progress. The bug case: teardown fires during a
    transient count==0 window even though the umbrella signals live
    orchestration.

    Backend's C5 will INVERT this assertion when adding Gate 6 source.
    """

    def test_v1_teardown_currently_fires_during_phase_lull(self, tmp_path):
        """V1 RED-state: teardown FIRES today during the phase-lull
        window (umbrella in_progress + count_active_tasks==0). Backend's
        C5 commit flips this to assert teardown SUPPRESSED.

        PHANTOM-GREEN DISCIPLINE: this assertion is intentionally
        coupled to the CURRENT bug; flipping it without the Gate 6
        source change would make the test pass for the wrong reason.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v1"
        _setup_lead_session(home, team)

        # Lead-owned umbrella in_progress — phase-lull marker.
        _write_task(home, team, make_umbrella_task(
            "U1", subject_prefix="Feature: ", subject_suffix="v1 phase-lull",
            status="in_progress",
        ))
        # Just-completed teachback task A — triggers the TaskCompleted hook.
        _write_task(home, team, make_specialist_task(
            "A1", owner="preparer",
            subject="preparer: TEACHBACK for v1",
            status="completed",
        ))
        # Task B (work) wiring not yet landed — owner empty (H4 window).
        b_task = make_specialist_task(
            "B1", owner="", subject="preparer: do work for v1",
            status="pending",
        )
        b_task["blockedBy"] = ["A1"]
        _write_task(home, team, b_task)

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A1")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"

        # CURRENT BEHAVIOR: teardown fires (bug).
        # Backend C5 inverts to: assert NOT _teardown_directive_in_stdout(out)
        assert _teardown_directive_in_stdout(out), (
            "V1 RED-state: teardown directive currently fires during phase-lull "
            "window (umbrella in_progress + count==0). Backend C5 commits the "
            "Gate 6 fix AND flips this assertion in the same commit."
        )

    def test_v1_teardown_request_currently_journaled_during_phase_lull(self, tmp_path):
        """V1 RED-state journal-side pin: the teardown_request event
        IS written to the session journal today (the directive emission
        and the journal write are paired). Backend's C5 flips this to
        assert the journal events list is empty.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v1-journal"
        sid, pdir = _setup_lead_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U1", subject_prefix="Feature: ", subject_suffix="v1 journal pin",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "A1", owner="preparer", subject="preparer: TEACHBACK", status="completed",
        ))

        _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A1")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(home, pdir, sid, event_type="teardown_request")

        # CURRENT BEHAVIOR: one teardown_request event journaled.
        # Backend C5 inverts to: assert events == []
        assert len(events) == 1, (
            f"V1 RED-state: exactly one teardown_request event currently "
            f"journaled during phase-lull window; got {len(events)} events. "
            f"Backend C5 flips this to assert events == []."
        )


# =============================================================================
# V2 — H4 latent bug: Task B owner=null at TaskCompleted(A).
#
# This is documented but NOT fixed by PR-B. The H4 unowned-B path causes
# count_active_tasks to exclude B (empty owner), driving count to zero
# and firing teardown. The Gate 6 fix targets the OPERATIONAL-LULL class
# (umbrella in_progress); V2 has NO umbrella, so Gate 6 doesn't apply
# and the H4 fire persists. V2 is a negative-control discriminator.
#
# CURRENT BEHAVIOR: teardown FIRES (H4). POST-FIX: UNCHANGED.
#
# Defensive regression coverage retained in the C10 promoted harness
# conversion (per plan: "V2 H4 unowned-B latent-bug coverage MUST be
# retained in C10").
# =============================================================================


class TestV2H4UnownedBLatentBug:
    """V2 — H4 latent bug: B owner=null at TaskCompleted(A); NO umbrella.
    Documents existing behavior; Gate 6 does not touch this path."""

    def test_v2_teardown_fires_on_h4_unowned_b_no_umbrella(self, tmp_path):
        """V2 STABLE: teardown fires today and STAYS firing post-fix
        because no umbrella is in_progress (Gate 6 has nothing to
        suppress against). Assertion shape UNCHANGED through C5.

        This is the load-bearing V2 negative control — Gate 6 must
        NOT spuriously suppress the H4 fire by accident."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v2"
        _setup_lead_session(home, team)

        # NO umbrella present — Gate 6 has no signal to short-circuit on.
        _write_task(home, team, make_specialist_task(
            "A2", owner="preparer", subject="preparer: TEACHBACK v2",
            status="completed",
        ))
        b_unowned = make_specialist_task(
            "B2", owner=None, subject="preparer: work v2", status="pending",
        )
        b_unowned["owner"] = None  # explicit H4 unowned-B shape
        b_unowned["blockedBy"] = ["A2"]
        _write_task(home, team, b_unowned)

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A2")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert _teardown_directive_in_stdout(out), (
            "V2 STABLE: teardown fires on H4 unowned-B with no umbrella; "
            "Gate 6 must NOT change this (no umbrella signal present)."
        )


# =============================================================================
# V3 — Baseline: A=completed, no other tasks, no umbrella. Legitimate
# teardown — the team genuinely has no remaining work. Gate 6 doesn't
# touch this path; assertion is stable through C5.
# =============================================================================


class TestV3BaselineNoOtherTasks:
    """V3 — Legitimate teardown baseline. No umbrella, no other tasks;
    teardown fires legitimately. Gate 6 must NOT suppress this."""

    def test_v3_teardown_fires_when_no_other_work_remains(self, tmp_path):
        """V3 STABLE: the canonical legitimate-teardown case. Verifies
        Gate 6 does not over-suppress (no umbrella, no peer work,
        teardown is the correct emission). Assertion UNCHANGED through
        C5."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v3"
        _setup_lead_session(home, team)

        # Only the just-completed task — no other work, no umbrella.
        _write_task(home, team, make_specialist_task(
            "A3", owner="preparer", subject="preparer: only task",
            status="completed",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "A3")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert _teardown_directive_in_stdout(out), (
            "V3 STABLE: teardown legitimately fires when no other work "
            "remains. Gate 6 must NOT over-suppress this case."
        )


# =============================================================================
# V4 — Cross-teammate concurrent: X completes while Y is in_progress.
# count_active_tasks=1 (Y counted); Gate 3 suppresses teardown today.
# Gate 6 does not touch this path; assertion stable through C5.
# =============================================================================


class TestV4CrossTeammateConcurrent:
    """V4 — Cross-teammate concurrent work. X's completion while Y is
    in_progress is suppressed via Gate 3 (count=1). Gate 6 doesn't
    apply (no umbrella needed); negative control for Gate 6 scope."""

    def test_v4_teardown_suppressed_when_other_teammate_in_progress(self, tmp_path):
        """V4 STABLE: cross-teammate concurrent work suppresses
        teardown via existing Gate 3 (count_active_tasks==1). Assertion
        UNCHANGED through C5 — verifies Gate 6 didn't accidentally
        REMOVE the count-based suppression."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v4"
        _setup_lead_session(home, team)

        # X (just completed)
        _write_task(home, team, make_specialist_task(
            "X1", owner="preparer", subject="preparer: X done",
            status="completed",
        ))
        # Y (in_progress)
        _write_task(home, team, make_specialist_task(
            "Y1", owner="architect", subject="architect: Y working",
            status="in_progress",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "X1")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V4 STABLE: teardown suppressed via existing Gate 3 when "
            "another teammate is in_progress. Gate 6 must NOT remove "
            "this Gate-3 suppression."
        )


# =============================================================================
# V6 — Pure phase-lull: lead-owned umbrella in_progress + just-completed
# teammate task; no Task B (the bug surface seen in pact-450f3d63).
# count_active_tasks=0 (umbrella excluded; teammate-task completed).
#
# CURRENT BEHAVIOR (C4 GREEN, pre-fix): teardown FIRES — bug.
# POST-FIX (C5 GREEN, after Gate 6): teardown SUPPRESSED.
#
# Distinct from V1 in that there is NO B-side wiring at all — the phase
# has genuinely ended; only the umbrella signals that the next phase
# will dispatch within the operational lull window (8-30s per Phase A
# diagnostic).
# =============================================================================


class TestV6PurePhaseLullUmbrellaOnly:
    """V6 — Pure phase-lull: umbrella in_progress + completed teammate
    task; no Task B. The most common bug shape seen in pact-450f3d63.

    Backend's C5 will INVERT this assertion when adding Gate 6 source.
    """

    def test_v6_teardown_currently_fires_with_umbrella_only(self, tmp_path):
        """V6 RED-state: teardown FIRES today with only an umbrella
        in_progress + completed teammate task. Backend's C5 flips this
        to assert teardown SUPPRESSED — the structural OPERATIONAL-LULL
        suppression that PR-B introduces.

        PHANTOM-GREEN DISCIPLINE: assertion is intentionally coupled
        to the current bug shape.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v6"
        _setup_lead_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U6", subject_prefix="Feature: ", subject_suffix="v6 pure lull",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "T6", owner="preparer", subject="preparer: v6 done",
            status="completed",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "T6")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert _teardown_directive_in_stdout(out), (
            "V6 RED-state: teardown directive currently fires during pure "
            "phase-lull window (umbrella in_progress + count==0, no Task B). "
            "Backend C5 flips this to assert teardown SUPPRESSED."
        )


# =============================================================================
# Parametrized scaffolding for the C8 multi-phase noise-budget regression.
#
# C4 lands the parametrized infrastructure (test ID matrix, fixture
# helpers); C8 populates the assertion shape with the noise-budget
# invariant. Backend's C5 may populate V8 (a phase-lull variant with
# multiple completed teammate tasks under the umbrella) in this
# parametrized class.
#
# The N=1..3 phase-count x N=1..3 specialist-per-phase matrix covers
# the combinatorial space the bug manifested in (6 fires over 4 phase
# transitions in pact-450f3d63 — N=4, M=multiple per phase). N=3 is
# the smallest exhaustive sample of the asymmetric counts that
# discriminate Gate-6-shaped suppression from Gate-3-shaped suppression.
# =============================================================================


class TestMultiPhaseNoiseBudgetScaffold:
    """Parametrized infrastructure for C8 noise-budget regression.

    At C4: scaffold only. The fixture-construction helper
    (_build_multi_phase_fixture) is exercised via a smoke parameter
    that verifies it does not crash on the matrix's corner cells. C8
    adds the load-bearing noise-budget assertion (max teardown
    emissions per N-phase orchestration MUST equal exactly 1 — the
    final genuine 1->0 transition after the umbrella completes).
    """

    @pytest.mark.parametrize(
        "n_phases,n_specialists_per_phase",
        [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)],
        ids=lambda v: f"n{v}",
    )
    def test_scaffold_matrix_fixture_constructs(
        self, tmp_path, n_phases, n_specialists_per_phase,
    ):
        """C4 smoke check: the 9-cell N=1,2,3 x M=1,2,3 matrix
        fixture-builder does not crash on any cell. C8 replaces this
        assertion with the noise-budget invariant: across N
        phase-transitions with M specialists each, the emitted
        teardown count MUST equal exactly 1.
        """
        fixture = _build_multi_phase_fixture(
            tmp_path, n_phases=n_phases,
            n_specialists_per_phase=n_specialists_per_phase,
        )
        # Smoke: fixture builds a non-empty task set with the expected
        # umbrella + specialist task layout. C8 replaces with noise-
        # budget assertion.
        assert fixture["umbrella_task_id"]
        assert len(fixture["specialist_task_ids"]) == (
            n_phases * n_specialists_per_phase
        )


def _build_multi_phase_fixture(
    tmp_path, *, n_phases: int, n_specialists_per_phase: int,
):
    """C8 noise-budget fixture builder. Lays down a lead-owned
    umbrella task in_progress plus n_phases * n_specialists_per_phase
    completed specialist tasks (mimicking N sequential phases with M
    teammates each).

    Returns a dict with the team_name, project_dir, umbrella_task_id,
    and specialist_task_ids list — C8 iterates the specialist IDs
    firing TaskCompleted for each and counts teardown emissions
    across the N-phase orchestration.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    team = f"team-noise-budget-n{n_phases}-m{n_specialists_per_phase}"
    sid, pdir = _setup_lead_session(home, team)

    umbrella_id = "U-multi"
    _write_task(home, team, make_umbrella_task(
        umbrella_id, subject_prefix="Feature: ",
        subject_suffix=f"multi-phase n={n_phases} m={n_specialists_per_phase}",
        status="in_progress",
    ))

    specialist_ids = []
    for phase_idx in range(n_phases):
        for specialist_idx in range(n_specialists_per_phase):
            tid = f"T-p{phase_idx}-s{specialist_idx}"
            _write_task(home, team, make_specialist_task(
                tid, owner=f"specialist-{specialist_idx}",
                subject=f"specialist-{specialist_idx}: phase {phase_idx} work",
                status="completed",
            ))
            specialist_ids.append(tid)

    return {
        "home": home,
        "session_id": sid,
        "project_dir": pdir,
        "team_name": team,
        "umbrella_task_id": umbrella_id,
        "specialist_task_ids": specialist_ids,
    }
