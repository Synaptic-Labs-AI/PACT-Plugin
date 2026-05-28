"""
Phase-lull regression fixtures for wake_lifecycle_emitter.py — Tier-2
emission-site coverage for the OPERATIONAL-LULL-AT-PHASE-BOUNDARY class.

Mirror of test_teardown_request_emitter_phase_lull.py for the Tier-2
marker-writer path. Devops's C6 commit (`a0153c30`) added Gate 6 to
`_maybe_write_teammate_teardown_marker`, mirroring backend's C5 Gate 6
on the Tier-1 emitter. The two emission sites share the same
has_in_progress_umbrella_orchestration predicate from
hooks/shared/wake_lifecycle.py and the same UMBRELLA_SUBJECT_PREFIXES
SSOT from tests/fixtures/disk_shapes.py.

EMISSION SURFACE
================

Tier-2 fires on PostToolUse:TaskUpdate in TEAMMATE sessions (not the
lead's). The dominant case is `pact-secretary` self-completing a
memory-save task — its agentType is in SELF_COMPLETE_EXEMPT_AGENT_TYPES,
so the predicate ladder's Clause 6 witnesses the carve-out and writes
a `type="teardown"` marker to ~/.claude/teams/{team}/wake_inbox/. The
lead's wake_inbox_drain consumes the marker on its next UserPromptSubmit
and emits the actual Teardown directive cross-session.

PR-B's C6 inserts Gate 6 BEFORE Clause 4 (count_active_tasks): if a
lead-owned umbrella task is in_progress, the marker write is suppressed
even when count_active_tasks reaches zero — the same OPERATIONAL-LULL
defense as Tier-1.

COUNTER-TEST-BY-REVERT CARDINALITY (recorded per plan)
======================================================

Revert ONLY devops's C6 Gate-6 mirror in wake_lifecycle_emitter.py
(L726-738, keep backend's Tier-1 Gate-6 + this file's assertions):
  Expect: V1-T2, V6-T2, V8-T2 RED (each asserts marker EMPTY but
  reverted Tier-2 writes the teardown marker).
  V2-T2, V3-T2, V4-T2 remain GREEN (negative controls — Gate 6
  does not touch their paths).
  Cardinality: {3 RED, 3 GREEN}.

Revert ONLY backend's C5 Gate-6 source in teardown_request_emitter.py
(keep C6, the Tier-2 mirror): this file UNCHANGED (GREEN). The
Tier-1 test file gets the V1/V6/V8 RED hits. Independent test coverage
per emission site is structurally proven.

FIXTURE SSOT IMPORT
===================

All on-disk task + team-config shapes constructed via the SSOT helpers
at pact-plugin/tests/fixtures/disk_shapes.py. The
UMBRELLA_SUBJECT_PREFIXES tuple imported there is the same constant the
production has_in_progress_umbrella_orchestration helper imports;
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
    make_umbrella_task,
)

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"


# =============================================================================
# Subprocess + filesystem fixture helpers — co-located per #551 fixture-
# location convention. Mirrors test_wake_lifecycle_emitter.py helpers but
# consumes the SSOT shape helpers from fixtures/disk_shapes.py.
# =============================================================================


def _run_emitter(stdin_payload, env_extra=None):
    """Invoke wake_lifecycle_emitter.py as a subprocess (production
    fidelity). Returns (returncode, stdout, stderr)."""
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


def _emit_output(payload, home):
    """Wrapper that asserts the hook exited 0 and returns parsed stdout
    JSON. Mirrors test_wake_lifecycle_emitter._emit_output."""
    rc, out, err = _run_emitter(
        json.dumps(payload),
        env_extra={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": payload.get("cwd", ""),
        },
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    return json.loads(out) if out.strip() else {}


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
    """Write session-context + team-config so is_lead_context resolves
    correctly under the test HOME. Mirrors the Tier-2 file's helper."""
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


def _wake_inbox_dir(home, team_name):
    return home / ".claude" / "teams" / team_name / "wake_inbox"


def _read_teardown_markers(home, team_name):
    """Return parsed JSON payloads of all teardown markers (type='teardown')
    in the team's wake_inbox/. Empty list if dir doesn't exist."""
    inbox = _wake_inbox_dir(home, team_name)
    if not inbox.exists():
        return []
    markers = []
    for path in sorted(inbox.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "teardown":
            markers.append(obj)
    return markers


# =============================================================================
# Fixture builders — Tier-2 teammate-context scenarios.
# =============================================================================


# Canonical secretary identity used across the carve-out tests. The
# Tier-2 marker write fires when the just-completed task's owner has
# agentType in SELF_COMPLETE_EXEMPT_AGENT_TYPES (today: pact-secretary).
SECRETARY_NAME = "secretary"
SECRETARY_AGENT_ID = "agent-secretary"
SECRETARY_AGENT_TYPE = "pact-secretary"
TEAMMATE_SID = "teammate-sid"
LEAD_SID = "lead-sid"
PROJECT_DIR = "/tmp/tier2-phase-lull"


def _setup_secretary_teammate_session(home, team_name):
    """Common Tier-2 boilerplate. Writes a session-context for the
    teammate (secretary) session + team-config carrying secretary's
    agentType=pact-secretary so the SELF_COMPLETE_EXEMPT_AGENT_TYPES
    membership predicate matches."""
    _write_session_context(
        home, TEAMMATE_SID, PROJECT_DIR, team_name,
        lead_session_id=LEAD_SID,
        members=[
            {
                "name": SECRETARY_NAME, "agentId": SECRETARY_AGENT_ID,
                "agentType": SECRETARY_AGENT_TYPE,
            },
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )


def _secretary_taskupdate_payload(task_id, owner=SECRETARY_NAME):
    """Teammate-frame PostToolUse:TaskUpdate payload — the hook input
    shape that drives the Tier-2 marker write."""
    return {
        "tool_name": "TaskUpdate",
        "session_id": TEAMMATE_SID,
        "agent_id": SECRETARY_AGENT_ID,
        "cwd": PROJECT_DIR,
        "tool_input": {
            "taskId": task_id, "status": "completed", "owner": owner,
        },
        "tool_response": {
            "id": task_id, "status": "completed", "owner": owner,
        },
    }


# =============================================================================
# V1-T2 — Phase-lull during canonical teachback handoff.
#
# Setup: lead-owned umbrella in_progress + just-completed secretary
# task (memory-save self-completion). count_active_tasks would reach
# zero (umbrella excluded by lead-owner filter; secretary task
# completed). Pre-C6: marker IS written → phantom Tier-2 Teardown
# directive cross-session. Post-C6: Gate 6 short-circuits → marker
# NOT written.
#
# Counter-test discriminator: revert C6's Gate-6 source ONLY → V1-T2
# RED (marker reappears).
# =============================================================================


class TestV1Tier2PhaseLullTeachbackHandoff:
    """V1-T2 — Tier-2 mirror of Tier-1 V1. Phase-lull during canonical
    teachback handoff suppresses the Tier-2 marker write."""

    def test_v1_tier2_no_marker_when_umbrella_in_progress(self, tmp_path):
        """Gate 6 (Tier-2 mirror) suppresses the teardown marker write
        when an umbrella orchestration task is in_progress, even though
        the secretary's self-completion would otherwise drive the
        Tier-2 carve-out path to write one. Verifies devops C6's mirror
        is functionally equivalent to backend C5's Tier-1 Gate 6 on
        this fixture shape.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-v1"
        _setup_secretary_teammate_session(home, team)

        # Lead-owned umbrella in_progress — phase-lull marker.
        _write_task(home, team, make_umbrella_task(
            "U1", subject_prefix="Feature: ", subject_suffix="tier2 v1",
            status="in_progress",
        ))
        # Secretary's just-completed task — the Tier-2 carve-out trigger.
        _write_task(home, team, make_specialist_task(
            "T1", owner=SECRETARY_NAME,
            subject="secretary: memory-save tier2 v1",
            status="completed",
        ))

        _emit_output(_secretary_taskupdate_payload("T1"), home)

        markers = _read_teardown_markers(home, team)
        assert markers == [], (
            "V1-T2: Tier-2 Gate-6 mirror must suppress teardown marker "
            "write when umbrella is in_progress (OPERATIONAL-LULL "
            "suppression); got {!r}".format(markers)
        )


# =============================================================================
# V6-T2 — Pure phase-lull: umbrella in_progress + secretary self-complete;
# no other teammate tasks (the cleanest Tier-2 mirror of Tier-1 V6).
# =============================================================================


class TestV6Tier2PurePhaseLullUmbrellaOnly:
    """V6-T2 — Tier-2 mirror of Tier-1 V6. Cleanest phase-lull shape:
    umbrella in_progress + completed secretary task; no peer work."""

    def test_v6_tier2_no_marker_with_umbrella_only(self, tmp_path):
        """Pure phase-lull: only the umbrella and the just-completed
        secretary task on disk. Tier-2 Gate 6 short-circuits before
        Clause 4 (count_active_tasks); no marker written.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-v6"
        _setup_secretary_teammate_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U6", subject_prefix="PREPARE: ", subject_suffix="tier2 v6",
            status="in_progress",
        ))
        _write_task(home, team, make_specialist_task(
            "T6", owner=SECRETARY_NAME,
            subject="secretary: memory-save tier2 v6",
            status="completed",
        ))

        _emit_output(_secretary_taskupdate_payload("T6"), home)

        markers = _read_teardown_markers(home, team)
        assert markers == [], (
            "V6-T2: pure phase-lull (umbrella+secretary only) must not "
            "write a teardown marker; got {!r}".format(markers)
        )


# =============================================================================
# V8-T2 — Multi-completion under umbrella: umbrella in_progress + multiple
# completed teammate tasks + secretary self-complete. Stresses Gate 6
# against count_active_tasks's lifecycle-relevant filter (multiple
# completed tasks do NOT count, so count==0 still reachable).
# =============================================================================


class TestV8Tier2MultiCompletionUnderUmbrella:
    """V8-T2 — Tier-2 mirror of Tier-1 V8. Multiple completed teammate
    tasks under an in_progress umbrella; secretary's terminal-status
    TaskUpdate would drive count==0 without Gate 6."""

    def test_v8_tier2_no_marker_with_umbrella_and_multiple_completed(
        self, tmp_path,
    ):
        """Two completed teammate tasks plus the secretary completion;
        umbrella still in_progress. Without Gate 6, count==0 transition
        would fire the marker. Gate 6 suppresses regardless of
        accumulated completed tasks under the umbrella.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-v8"
        _setup_secretary_teammate_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U8", subject_prefix="CODE: ", subject_suffix="tier2 v8",
            status="in_progress",
        ))
        # Earlier specialist completions in the same phase.
        _write_task(home, team, make_specialist_task(
            "C8a", owner="backend-coder",
            subject="backend-coder: earlier work", status="completed",
        ))
        _write_task(home, team, make_specialist_task(
            "C8b", owner="architect",
            subject="architect: earlier review", status="completed",
        ))
        # Secretary's just-completed memory-save.
        _write_task(home, team, make_specialist_task(
            "T8", owner=SECRETARY_NAME,
            subject="secretary: memory-save tier2 v8",
            status="completed",
        ))

        _emit_output(_secretary_taskupdate_payload("T8"), home)

        markers = _read_teardown_markers(home, team)
        assert markers == [], (
            "V8-T2: multi-completion under umbrella must not write a "
            "teardown marker; got {!r}".format(markers)
        )


# =============================================================================
# V2-T2 — Negative control: secretary self-completion + NO umbrella.
# Gate 6 has nothing to short-circuit on; Clauses 4 + 6 hold; marker
# IS written. Verifies Gate 6 does NOT over-suppress.
#
# This is the Tier-2 load-bearing positive emission case — without
# Gate 6 reverting, this test stays GREEN. If Gate 6 were rewritten to
# always suppress (e.g., a fail-OPEN bug), this test flips RED.
# =============================================================================


class TestV2Tier2NoUmbrellaWritesMarker:
    """V2-T2 — Negative control: secretary self-completes with NO
    umbrella present. Gate 6 must NOT spuriously suppress."""

    def test_v2_tier2_marker_written_when_no_umbrella(self, tmp_path):
        """Without an umbrella in_progress, Gate 6's predicate returns
        False and the marker-write path proceeds (Clauses 4+6 hold).
        The Tier-2 carve-out genuinely needs to surface the secretary's
        self-completion to the lead's drain — this test pins that
        Gate 6 doesn't accidentally suppress the legitimate case.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-v2-positive"
        _setup_secretary_teammate_session(home, team)

        # NO umbrella; just the secretary's completed task.
        _write_task(home, team, make_specialist_task(
            "T2", owner=SECRETARY_NAME,
            subject="secretary: memory-save no-umbrella",
            status="completed",
        ))

        _emit_output(_secretary_taskupdate_payload("T2"), home)

        markers = _read_teardown_markers(home, team)
        assert len(markers) == 1, (
            "V2-T2: secretary self-complete with no umbrella must "
            "write exactly one teardown marker (Tier-2 legitimate "
            "emission); got {!r}".format(markers)
        )
        assert markers[0].get("task_id") == "T2"
        assert markers[0].get("team_name") == team


# =============================================================================
# V3-T2 — Negative control: signal-task secretary completion. Signal-
# tasks are filtered upstream by count_active_tasks's lifecycle-relevant
# filter AND by Clause 6's metadata-exclusion check. NO marker today
# regardless of Gate 6.
# =============================================================================


class TestV3Tier2SignalTaskExcluded:
    """V3-T2 — Negative control: signal-task secretary completion is
    filtered by the existing Clause 6 metadata guard, NOT Gate 6."""

    def test_v3_tier2_signal_task_writes_no_marker(self, tmp_path):
        """Signal-tasks (completion_type='signal' + type in
        {blocker, algedonic}) self-complete without surfacing a
        Teardown. Clause 6 excludes them BEFORE Gate 6 even matters;
        this pin confirms Gate 6 does not regress the pre-existing
        exclusion.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-v3-signal"
        _setup_secretary_teammate_session(home, team)

        signal_task = make_specialist_task(
            "T3sig", owner=SECRETARY_NAME,
            subject="secretary: blocker signal", status="completed",
        )
        signal_task["metadata"] = {
            "completion_type": "signal", "type": "blocker",
        }
        _write_task(home, team, signal_task)

        _emit_output(_secretary_taskupdate_payload("T3sig"), home)

        markers = _read_teardown_markers(home, team)
        assert markers == [], (
            "V3-T2: signal-task secretary completion must not write a "
            "teardown marker (existing Clause 6 exclusion); "
            "got {!r}".format(markers)
        )


# =============================================================================
# V4-T2 — Negative control: umbrella in_progress + ANOTHER teammate
# in_progress + secretary self-complete. count_active_tasks=1 (peer Y
# counted); Clause 4 would block emission regardless of Gate 6. This
# pin verifies Gate 6 ordering doesn't break the existing Clause-4
# count-based suppression.
# =============================================================================


class TestV4Tier2GateOrderingPreservesCountSuppression:
    """V4-T2 — Negative control: count-based suppression (Clause 4)
    remains effective. Gate 6 inserts BEFORE Clause 4 but must not
    short-circuit other emissions; doubly-suppressed cases stay
    suppressed."""

    def test_v4_tier2_marker_suppressed_when_other_teammate_in_progress(
        self, tmp_path,
    ):
        """Umbrella + Y in_progress + secretary self-complete:
        count_active_tasks=1 (Y counts), Clause 4 blocks. Gate 6
        ALSO blocks (umbrella in_progress) — doubly suppressed.
        Either gate alone is sufficient; this verifies the ordering
        doesn't expose a hole.
        """
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-v4"
        _setup_secretary_teammate_session(home, team)

        _write_task(home, team, make_umbrella_task(
            "U4", subject_prefix="ARCHITECT: ", subject_suffix="tier2 v4",
            status="in_progress",
        ))
        # Peer teammate Y in_progress.
        _write_task(home, team, make_specialist_task(
            "Y4", owner="architect",
            subject="architect: tier2 v4 work",
            status="in_progress",
        ))
        # Secretary completion.
        _write_task(home, team, make_specialist_task(
            "T4", owner=SECRETARY_NAME,
            subject="secretary: memory-save tier2 v4",
            status="completed",
        ))

        _emit_output(_secretary_taskupdate_payload("T4"), home)

        markers = _read_teardown_markers(home, team)
        assert markers == [], (
            "V4-T2: count-based suppression (Clause 4) plus umbrella "
            "suppression (Gate 6) must yield no marker; "
            "got {!r}".format(markers)
        )


# =============================================================================
# Symmetry pin — non-canonical umbrella prefix does NOT trigger Gate 6.
# Mirrors Tier-1 V8 negative-half pattern.
# =============================================================================


class TestTier2NonCanonicalUmbrellaPrefixDoesNotSuppress:
    """Symmetry pin: a task with a NON-canonical subject prefix
    (e.g., 'Bug: ...') is NOT treated as an umbrella by Gate 6.
    The marker write proceeds. Confirms predicate fidelity at
    the Tier-2 mirror — the SSOT prefix tuple is consulted, not
    a lenient substring match.
    """

    def test_tier2_non_canonical_prefix_writes_marker_when_count_zero(
        self, tmp_path,
    ):
        """A task with subject 'Bug: ...' (NOT in
        UMBRELLA_SUBJECT_PREFIXES) is NOT treated as an umbrella by
        the predicate; Gate 6 returns False; marker IS written.

        Fixture sets BUG1 to completed so count_active_tasks==0; only
        Gate 6 ordering determines the outcome. If Gate 6 spuriously
        matches 'Bug: ' as a canonical prefix (e.g., a refactor to
        endswith(': ') matching), no marker (RED). If Gate 6 correctly
        consults the SSOT tuple membership, marker IS written (GREEN).

        This is the load-bearing SSOT-fidelity assertion for the
        Tier-2 mirror — confirms predicate goes through
        UMBRELLA_SUBJECT_PREFIXES and not a looser shape-only match.
        """
        # Sanity: 'Bug: ' must NOT be in the SSOT tuple — if a future
        # change adds it, this test name + assertion no longer make sense.
        assert "Bug: " not in UMBRELLA_SUBJECT_PREFIXES, (
            "SSOT drift: 'Bug: ' is now a canonical umbrella prefix; "
            "this symmetry pin needs a new non-canonical example."
        )
        home = tmp_path / "home"
        home.mkdir()
        team = "team-tier2-noncanonical-count0"
        _setup_secretary_teammate_session(home, team)

        # Bug-tracker task COMPLETED so count==0; only Gate 6 ordering
        # determines the outcome.
        _write_task(home, team, make_specialist_task(
            "BUGc", owner="backend-coder",
            subject="Bug: legacy issue not an umbrella",
            status="completed",
        ))
        _write_task(home, team, make_specialist_task(
            "T-nc2", owner=SECRETARY_NAME,
            subject="secretary: memory-save noncanonical count0",
            status="completed",
        ))

        _emit_output(_secretary_taskupdate_payload("T-nc2"), home)

        markers = _read_teardown_markers(home, team)
        assert len(markers) == 1, (
            "Non-canonical 'Bug: ' prefix MUST NOT match Gate 6's "
            "predicate (SSOT tuple is consulted, not a lenient match). "
            "With count==0 and only Gate-6 ordering at play, the "
            "secretary carve-out marker MUST be written. "
            "got {!r}".format(markers)
        )
        assert markers[0].get("task_id") == "T-nc2"
