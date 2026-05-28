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

This file landed in two commits — the prior commit (C4) shipped V1/V6
asserting CURRENT (pre-fix) broken behavior; this commit (C5) flipped
those assertions and added the Gate 6 source in the SAME atomic
commit. The coupling is the phantom-green proof: reverting Gate 6
source ALONE (keeping the post-flip assertions) regresses V1/V6/V8 +
the multi-phase noise-budget regression to RED while V2/V3/V4 stay
GREEN. The asymmetric cardinality discriminates Gate 6's specific
suppression mechanism from unrelated causes.

  Variant   Pre-C5 (broken)       Post-C5 (after Gate 6)
  -------   --------------------   ----------------------
  V1        teardown FIRES (bug)   teardown SUPPRESSED
  V6        teardown FIRES (bug)   teardown SUPPRESSED
  V8        (added by C5)          teardown SUPPRESSED
  V2        teardown FIRES (H4)    teardown FIRES (H4 latent — unchanged)
  V3        teardown FIRES (legit) teardown FIRES (unchanged)
  V4        teardown SUPPRESSED    teardown SUPPRESSED (unchanged)

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

    def test_v1_teardown_suppressed_during_phase_lull(self, tmp_path):
        """V1 GREEN post-fix: teardown is SUPPRESSED during the phase-
        lull window (umbrella in_progress + count_active_tasks==0).
        Gate 6 short-circuits before Gate 3 reads the (degenerate) 0
        count.

        PHANTOM-GREEN DISCIPLINE: this assertion is coupled to the
        Gate 6 source change in the SAME atomic commit. Reverting the
        Gate 6 source flips this back to RED, recovering the C4
        baseline. The counter-test-by-revert cardinality is recorded
        in the commit docstring.
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

        # POST-FIX BEHAVIOR: Gate 6 suppresses teardown when umbrella
        # in_progress (signature-based detection on subject prefix).
        assert not _teardown_directive_in_stdout(out), (
            "V1 GREEN post-fix: teardown directive must be SUPPRESSED "
            "during phase-lull (umbrella in_progress + count==0). "
            "Reverting the Gate 6 source flips this back to RED."
        )

    def test_v1_teardown_request_not_journaled_during_phase_lull(self, tmp_path):
        "V1 GREEN post-fix journal-side pin: no teardown_request event "
        "is written to the session journal during phase-lull. The "
        "directive emission and the journal write are paired; Gate 6 "
        "short-circuits both."
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

        # POST-FIX BEHAVIOR: zero teardown_request events journaled
        # when Gate 6 suppresses the emission path.
        assert events == [], (
            f"V1 GREEN post-fix: zero teardown_request events expected "
            f"during phase-lull window; got {len(events)} events. "
            f"Reverting Gate 6 source flips this back to RED (1 event)."
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

    def test_v6_teardown_suppressed_with_umbrella_only(self, tmp_path):
        """V6 GREEN post-fix: teardown is SUPPRESSED with only an
        umbrella in_progress + completed teammate task. The structural
        OPERATIONAL-LULL suppression Gate 6 provides — Tier-1
        emission-site coverage of the canonical bug shape seen in
        pact-450f3d63.

        PHANTOM-GREEN DISCIPLINE: assertion coupled to the Gate 6
        source change in the same atomic commit. Reverting Gate 6
        source flips this back to RED.
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
        assert not _teardown_directive_in_stdout(out), (
            "V6 GREEN post-fix: teardown directive must be SUPPRESSED "
            "during pure phase-lull window (umbrella in_progress + "
            "count==0, no Task B). Reverting Gate 6 source flips this "
            "back to RED."
        )


# =============================================================================
# V8 — Subprocess-integration variant added in C5 alongside the Gate 6 source
# change. Covers a phase-lull window where the umbrella is in_progress AND
# multiple completed teammate tasks are present (the canonical "phase wound
# down; next phase has not started" shape). Distinct from V1 (Task B mid-
# wiring) and V6 (single completed teammate task) — V8 exercises the
# "many done, none pending" multi-completion shape.
#
# The N=1..3 phase-count x M=1..3 specialist-per-phase aggregate regression
# is covered by tests/test_phase_lull_noise_budget.py (separate file; same
# `_build_multi_phase_fixture` helper, different invariant — aggregate
# emission count under the umbrella MUST be zero).
# =============================================================================


class TestV8MultiCompletionUnderUmbrella:
    """V8 — Multiple completed teammate tasks under in_progress umbrella.

    Added at C5. The fixture has the umbrella in_progress + several
    completed teammate tasks from the prior phase + no pending B-side
    work yet (the next phase's specialists have not yet been
    dispatched). Gate 6 must suppress teardown — this is the multi-
    completion phase-lull shape.
    """

    def test_v8_teardown_suppressed_with_umbrella_and_multiple_completed(
        self, tmp_path,
    ):
        """V8 GREEN post-fix: teardown SUPPRESSED when the umbrella is
        in_progress and multiple teammate tasks from the prior phase
        are completed. Distinguishes the multi-completion shape from
        V6's single-completion shape — same Gate 6 mechanism, broader
        fixture coverage.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v8"
        _setup_lead_session(home, team)

        # Lead-owned umbrella in_progress.
        _write_task(home, team, make_umbrella_task(
            "U8", subject_prefix="Feature: ", subject_suffix="v8 multi-completion",
            status="in_progress",
        ))
        # Three teammate tasks already completed from the prior phase.
        for idx, owner in enumerate(("preparer", "architect", "analyst")):
            _write_task(home, team, make_specialist_task(
                f"T8-{idx}", owner=owner,
                subject=f"{owner}: v8 prior-phase work",
                status="completed",
            ))

        # The hook fires on the LAST teammate task's completion — that's
        # the moment count_active_tasks transitions to 0 in the legacy
        # path. Gate 6 must short-circuit before that.
        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "T8-2")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V8 GREEN post-fix: teardown directive must be SUPPRESSED "
            "when the umbrella is in_progress AND multiple completed "
            "teammate tasks are present (multi-completion phase-lull "
            "shape). Reverting Gate 6 source flips this back to RED."
        )

    def test_v8_non_canonical_umbrella_prefix_still_emits(self, tmp_path):
        """V8 negative pin: an umbrella-shaped task whose subject does
        NOT match UMBRELLA_SUBJECT_PREFIXES does not trip Gate 6.
        Signature-based detection must reject arbitrary lead-owned
        in_progress tasks — only the canonical prefixes count.
        Distinguishes Gate 6 from a naive "any in_progress task with
        no owner" predicate."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v8-neg"
        _setup_lead_session(home, team)

        # A lead-owned in_progress task whose subject does NOT start
        # with any canonical umbrella prefix. This is structurally
        # similar to an umbrella but signature-distinct.
        non_canonical = make_umbrella_task(
            "U8N", subject_prefix="Internal note: ",
            subject_suffix="not an umbrella", status="in_progress",
        )
        # subject_prefix is applied verbatim; assert it does not match.
        assert not any(
            non_canonical["subject"].startswith(p)
            for p in UMBRELLA_SUBJECT_PREFIXES
        )
        _write_task(home, team, non_canonical)
        _write_task(home, team, make_specialist_task(
            "T8N", owner="preparer", subject="preparer: v8-neg done",
            status="completed",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "T8N")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": "/tmp/phase-lull-test"},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert _teardown_directive_in_stdout(out), (
            "V8 negative pin: Gate 6 must NOT suppress when the in_progress "
            "task's subject is signature-distinct from "
            "UMBRELLA_SUBJECT_PREFIXES. The teardown emits via the "
            "existing legitimate-teardown path."
        )


# =============================================================================
# V9 — Peer-review phase-lull: `Review: ` umbrella prefix coverage. The
# `/PACT:peer-review` orchestration creates an umbrella task with subject
# `Review: <topic>`. Without `Review: ` in UMBRELLA_SUBJECT_PREFIXES, the
# Gate-6 predicate misses the umbrella and teardown_request fires during
# the peer-review phase-transition lull — exact OPERATIONAL-LULL class
# bug recurring for a different prefix.
#
# Counter-test-by-revert: remove `'Review: '` from UMBRELLA_SUBJECT_PREFIXES
# ONLY → V9 RED; V1+V6+V8 stay GREEN (they use other prefixes). Cardinality
# {1 RED on the peer-review-specific test; 7-prefix sweep in
# test_shared_wake_lifecycle.py also RED at the 'Review: ' parametrized
# cell, total {2 RED}}. Confirms `Review: ` is the ONLY suppressor in this
# fixture and the predicate honors the SSOT tuple.
# =============================================================================


class TestV9PeerReviewPhaseLullSuppression:
    """V9 — peer-review phase-lull: umbrella with `Review: ` prefix.
    Mirrors V6's structural shape (umbrella + completed teammate, no
    Task B) but uses the peer-review prefix added per B1 remediation.

    Why a dedicated class instead of extending V1/V6: the bug class
    (OPERATIONAL-LULL) is general but the specific prefix-coverage gap
    is peer-review-orchestration-specific. A grep for
    'TestV9PeerReviewPhaseLull' from a future investigator surfaces the
    exact provenance — peer-review was the bug-class instance that
    surfaced the prefix-coverage discipline."""

    def test_v9_teardown_suppressed_during_peer_review_phase_lull(self, tmp_path):
        """V9 phase-lull: `Review: <topic>` umbrella in_progress +
        completed teammate task (e.g., a reviewer's TEACHBACK or review
        deliverable). Post-B1, Gate 6 short-circuits via the
        `'Review: '` prefix; teardown directive absent; no
        teardown_request event journaled."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v9"
        sid, pdir = _setup_lead_session(home, team)

        # Peer-review umbrella in_progress.
        _write_task(home, team, make_umbrella_task(
            "U9", subject_prefix="Review: ", subject_suffix="v9 peer-review",
            status="in_progress",
        ))
        # Just-completed reviewer task (e.g., test-engineer review handoff).
        _write_task(home, team, make_specialist_task(
            "T9", owner="test-engineer",
            subject="test-engineer: review v9 phase-lull",
            status="completed",
        ))

        rc, out, err = _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "T9")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"hook must exit 0; stderr={err}"
        assert not _teardown_directive_in_stdout(out), (
            "V9 phase-lull: teardown directive must be suppressed when "
            "a `Review: ` umbrella is in_progress. If this fires, "
            "either `'Review: '` was removed from UMBRELLA_SUBJECT_PREFIXES "
            "(B1 regression) or the predicate dropped its prefix-match "
            "for this entry specifically."
        )

    def test_v9_teardown_request_not_journaled_during_peer_review_phase_lull(
        self, tmp_path,
    ):
        """V9 journal-side pin paired with the directive-emission pin.
        Mirrors V1's journal-pin pattern — the journal write is the
        falsifiable primitive consumed by the Tier-4 cron-staleness
        fallback; a partial-suppression bug that skips the directive
        but writes the journal event would trigger phantom Teardown
        emissions later via cron replay."""
        home = tmp_path / "home"
        home.mkdir()
        team = "team-phase-lull-v9-journal"
        sid, pdir = _setup_lead_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U9", subject_prefix="Review: ", subject_suffix="v9 journal pin",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "T9", owner="test-engineer",
            subject="test-engineer: review v9 journal",
            status="completed",
        ))

        _run_emitter_subprocess(
            json.dumps(_lead_taskcompleted_payload(team, "T9")),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_journal_events(home, pdir, sid, event_type="teardown_request")
        assert events == [], (
            f"V9 phase-lull: no teardown_request event must be journaled "
            f"under a `Review: ` umbrella in_progress; got {events!r}"
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
