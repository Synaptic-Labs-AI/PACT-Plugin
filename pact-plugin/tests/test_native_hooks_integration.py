"""
Cross-tier integration tests for #763 native-hooks Teardown integration.

These tests exercise the FULL native-hooks dispatch surface end-to-end:
TaskCompleted hook (Tier-1) + PostToolUse:TaskUpdate teammate-marker
write (Tier-2 producer) + UserPromptSubmit drain (Tier-2 consumer) +
the journal event trace that Tier-4 cron staleness can replay from.

Per architect #764 §8.5 + #763 refinement §3, this module covers the
cross-tier scenarios that no single-hook unit test exercises:

  TestLeadDrivenCompletionFiresTier1Only — graceful lead path: Tier-1
    emits, NO Tier-2 marker is written.
  TestSecretarySelfCompleteFiresTier2Only — predicate-witnessed carve-
    out: Tier-1 cannot fire (caller is teammate), Tier-2 marker
    written, drained on next lead UserPromptSubmit, journal event
    emitted with tier="2".
  TestStopSweepDoubleFireDeduplicated — both Tier-1 and Tier-2 firing
    for same (team, task): the marker idempotency suppresses the
    second emit. Exactly one teardown_request event total.
  TestFreshSessionPostMergeValidation — documentation-only placeholder
    for the gold-standard option-c counter-test-by-revert (git revert
    + fresh session). NOT CI-runnable per CLAUDE.md "Hooks cannot be
    smoke-tested in-session" pin.

Per teachback Q2 resolution: option (b) parametric simulation is the
CI-runnable mechanism (see test_wake_lifecycle_emitter.py::
TestRetiredPostToolUseTeardownDoesNotFire::test_revert_C5_produces_
double_emission); option (c) git-revert is the manual post-merge
runbook below.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
WAKE_LIFECYCLE_EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"
WAKE_INBOX_DRAIN = HOOK_DIR / "wake_inbox_drain.py"
TEARDOWN_REQUEST_EMITTER = HOOK_DIR / "teardown_request_emitter.py"


# =============================================================================
# Test helpers (mirror test_wake_inbox_drain.py + test_wake_lifecycle_*.py)
# =============================================================================


def _run_hook(hook_path, stdin_payload, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(hook_path)],
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
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": "",
            "started_at": "2026-05-16T00:00:00Z",
        }),
        encoding="utf-8",
    )
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = (
        lead_session_id if lead_session_id is not None else session_id
    )
    config_data = {"leadSessionId": effective_lead}
    if lead_agent_id is not None:
        config_data["leadAgentId"] = lead_agent_id
    if members:
        config_data["members"] = list(members)
    (team_dir / "config.json").write_text(
        json.dumps(config_data), encoding="utf-8",
    )


def _write_task(home, team_name, task_id, **fields):
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _wake_inbox_dir(home, team_name):
    return home / ".claude" / "teams" / team_name / "wake_inbox"


def _marker_dir(home, team_name):
    return home / ".claude" / "teams" / team_name / ".teardown_request_emitted"


def _journal_path(home, project_dir, session_id):
    slug = Path(project_dir).name
    return (
        home / ".claude" / "pact-sessions" / slug / session_id
        / "session-journal.jsonl"
    )


def _read_teardown_events(home, project_dir, session_id):
    """Read all teardown_request events from the lead's journal."""
    path = _journal_path(home, project_dir, session_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "teardown_request":
            events.append(event)
    return events


# =============================================================================
# TestLeadDrivenCompletionFiresTier1Only
# =============================================================================


class TestLeadDrivenCompletionFiresTier1Only:
    """The dominant case: lead operator runs `TaskUpdate(status=
    completed)` from the lead's session. The native TaskCompleted hook
    fires in the lead's process; Tier-1 emits a teardown_request event
    via teardown_request_emitter.py. Tier-2 (teammate marker) is NOT
    written because the caller is the lead, not a teammate.
    """

    def test_lead_terminal_taskupdate_emits_tier1_event(self, tmp_path):
        """Fire TaskCompleted in the lead's session; assert exactly one
        teardown_request event written with tier="1".
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-tier1"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "L1", status="completed", owner="backend-coder")

        _run_hook(
            TEARDOWN_REQUEST_EMITTER,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "L1", "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        events = _read_teardown_events(home, pdir, lead_sid)
        assert len(events) == 1, (
            f"Tier-1 lead-driven path must emit 1 event; got {events!r}"
        )
        assert events[0].get("tier") == "1"
        assert events[0].get("task_id") == "L1"
        assert events[0].get("team_name") == team

    def test_lead_terminal_taskupdate_writes_no_teardown_marker(self, tmp_path):
        """The lead-driven path goes through Tier-1; the wake_inbox/
        teammate-marker write (Tier-2) MUST NOT fire because the caller
        is the lead. A marker here would produce a duplicate Tier-2
        drain event on the next UserPromptSubmit.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-no-marker"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "L2", status="completed", owner="backend-coder")

        # Fire the PostToolUse:TaskUpdate hook in the LEAD's session.
        _run_hook(
            WAKE_LIFECYCLE_EMITTER,
            json.dumps({
                "tool_name": "TaskUpdate",
                "session_id": lead_sid, "cwd": pdir,
                "tool_input": {
                    "taskId": "L2", "status": "completed",
                    "owner": "backend-coder",
                },
                "tool_response": {
                    "id": "L2", "status": "completed",
                    "owner": "backend-coder",
                },
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        inbox = _wake_inbox_dir(home, team)
        if inbox.exists():
            markers = list(inbox.glob("*.json"))
            teardown_markers = []
            for m in markers:
                try:
                    payload = json.loads(m.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "teardown":
                    teardown_markers.append(m)
            assert teardown_markers == [], (
                f"Lead-driven path must NOT write teardown markers; "
                f"got {[m.name for m in teardown_markers]}"
            )

    def test_parallel_burst_then_drain_lifecycle_symmetry(self, tmp_path):
        """Lifecycle-symmetry guard for the parallel-burst case:
        count 0->2 (Arm conditions met somewhere upstream) -> first
        task completes 2->1 (Tier-1 Gate 3 sees count==1, no emit) ->
        second task completes 1->0 (Tier-1 emits exactly 1 event).

        Pins the cross-tier behavior: with two tasks completing in
        sequence, the journal accumulates exactly ONE teardown_request
        event (on the 1->0 transition), not two. The intermediate
        2->1 fire must be suppressed by Gate 3 (count_active_tasks
        returns 1).
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-lifecycle-symmetry"
        _write_session_context(
            home, lead_sid, pdir, team,
            members=[
                {"name": "backend-coder", "agentId": "agent-bc"},
                {"name": "test-engineer", "agentId": "agent-te"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )

        # First completion 2 -> 1: T1 completes; T2 still in_progress
        # → Tier-1 Gate 3 sees count==1 → no emit.
        _write_task(home, team, "T1", status="completed", owner="backend-coder")
        _write_task(home, team, "T2", status="in_progress", owner="test-engineer")
        rc1, out1, _ = _run_hook(
            TEARDOWN_REQUEST_EMITTER,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T1", "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc1 == 0
        assert json.loads(out1).get("suppressOutput") is True, (
            f"2->1 transition: Tier-1 must suppress when count==1; "
            f"got {out1!r}"
        )
        intermediate_events = _read_teardown_events(home, pdir, lead_sid)
        assert intermediate_events == [], (
            f"2->1 transition must NOT write event; got "
            f"{intermediate_events!r}"
        )

        # Second completion 1 -> 0: T2 completes → Tier-1 Gate 3 sees
        # count==0 → emit. Final cardinality: 1 event total over the
        # full lifecycle.
        _write_task(home, team, "T2", status="completed", owner="test-engineer")
        _run_hook(
            TEARDOWN_REQUEST_EMITTER,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "T2", "team_name": team,
                "teammate_name": "test-engineer",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        final_events = _read_teardown_events(home, pdir, lead_sid)
        assert len(final_events) == 1, (
            f"Full lifecycle (2->1->0) must produce exactly 1 "
            f"teardown_request event (on the 1->0 transition); got "
            f"{len(final_events)}: {final_events!r}"
        )
        assert final_events[0].get("task_id") == "T2", (
            f"Event must attribute to the task whose completion drove "
            f"count to 0 (T2); got task_id={final_events[0].get('task_id')!r}"
        )


# =============================================================================
# TestSecretarySelfCompleteFiresTier2Only
# =============================================================================


class TestSecretarySelfCompleteFiresTier2Only:
    """The empirical case observed in session pact-25158ec6: the
    secretary self-completes a memory-save task (Tasks #1 + #13 in
    that session). Per pact-completion-authority.md, the secretary
    is in SELF_COMPLETE_EXEMPT_AGENT_TYPES so this self-completion is
    legitimate. The PostToolUse:TaskUpdate hook fires in the secretary's
    session — Tier-1 (TaskCompleted in lead's session) cannot fire
    because the lead didn't make the call.

    Tier-2 captures this: the teammate-side
    _maybe_write_teammate_teardown_marker writes a wake_inbox marker;
    the lead's next UserPromptSubmit drains it and emits a Tier-2
    teardown_request event.
    """

    def test_secretary_self_complete_writes_teammate_marker(self, tmp_path):
        """The secretary's PostToolUse:TaskUpdate fire writes a
        type="teardown" marker to the team's wake_inbox/.
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "secretary-sid"
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-secretary-tier2"
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id=lead_sid,
            members=[
                {
                    "name": "secretary", "agentId": "agent-sec",
                    "agentType": "pact-secretary",
                },
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        _write_task(
            home, team, "S1",
            status="completed", owner="secretary",
        )

        _run_hook(
            WAKE_LIFECYCLE_EMITTER,
            json.dumps({
                "tool_name": "TaskUpdate",
                "session_id": teammate_sid, "cwd": pdir,
                "tool_input": {
                    "taskId": "S1", "status": "completed",
                    "owner": "secretary",
                },
                "tool_response": {
                    "id": "S1", "status": "completed",
                    "owner": "secretary",
                },
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        inbox = _wake_inbox_dir(home, team)
        assert inbox.exists(), "wake_inbox must be created"
        markers = list(inbox.glob("*.json"))
        teardown_markers = [
            json.loads(m.read_text(encoding="utf-8"))
            for m in markers
            if json.loads(m.read_text(encoding="utf-8")).get("type") == "teardown"
        ]
        assert len(teardown_markers) == 1, (
            f"Secretary self-complete must write 1 teardown marker; "
            f"got {markers!r}"
        )
        assert teardown_markers[0].get("task_id") == "S1"

    def test_marker_drained_on_lead_userpromptsubmit_emits_tier2_event(
        self, tmp_path,
    ):
        """Full Tier-2 flow: teammate writes marker, lead's
        UserPromptSubmit drains it, lead's session-journal gets a
        teardown_request event with tier="2".
        """
        home = tmp_path / "home"; home.mkdir()
        teammate_sid = "secretary-sid"
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-tier2-drain"
        members = [
            {
                "name": "secretary", "agentId": "agent-sec",
                "agentType": "pact-secretary",
            },
            {"name": "lead", "agentId": "agent-lead"},
        ]
        # Set up the TEAMMATE session context (drives the PostToolUse fire).
        _write_session_context(
            home, teammate_sid, pdir, team,
            lead_session_id=lead_sid,
            members=members,
            lead_agent_id="agent-lead",
        )
        # ALSO set up the LEAD session context — the drain hook resolves
        # pact-session-context.json under {session_id}/, so without a
        # lead-side context file, get_team_name() returns empty and the
        # drain short-circuits to suppressOutput. Same team config, just
        # a second per-session context file.
        _write_session_context(
            home, lead_sid, pdir, team,
            lead_session_id=lead_sid,
            members=members,
            lead_agent_id="agent-lead",
        )
        # Step 1: teammate writes marker via PostToolUse:TaskUpdate.
        _write_task(
            home, team, "S2",
            status="completed", owner="secretary",
        )
        _run_hook(
            WAKE_LIFECYCLE_EMITTER,
            json.dumps({
                "tool_name": "TaskUpdate",
                "session_id": teammate_sid, "cwd": pdir,
                "tool_input": {
                    "taskId": "S2", "status": "completed",
                    "owner": "secretary",
                },
                "tool_response": {
                    "id": "S2", "status": "completed",
                    "owner": "secretary",
                },
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        # Step 2: simulate the lead's next UserPromptSubmit. The drain
        # reads pact-session-context.json for session_id=lead_sid (the
        # second context-file write above is what unblocks this).
        rc, out, err = _run_hook(
            WAKE_INBOX_DRAIN,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "go",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )
        assert rc == 0, f"Drain must exit 0; stderr={err}"
        parsed = json.loads(out)
        hso = parsed.get("hookSpecificOutput")
        assert hso is not None, (
            f"Drain must emit Teardown directive; got {parsed!r}"
        )
        assert "PACT:stop-pending-scan" in hso.get("additionalContext", "")

        # Step 3: assert lead-side journal got the Tier-2 event.
        events = _read_teardown_events(home, pdir, lead_sid)
        assert len(events) == 1, (
            f"Tier-2 path must write 1 journal event; got {events!r}"
        )
        assert events[0].get("tier") == "2"

    def test_marker_drained_with_correct_tier_2_attribution(self, tmp_path):
        """The Tier-2 journal event carries reason="wake_inbox_drained"
        — distinguishable from Tier-1's reason="lead_terminal_
        taskupdate" so audit consumers can route on the categorical
        token.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-tier2-attrib"
        _write_session_context(home, lead_sid, pdir, team)
        # Manually pre-populate a teardown marker (simulating what C3
        # would write).
        inbox = _wake_inbox_dir(home, team)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "20260516T170000Z-x-S3.json").write_text(
            json.dumps({
                "schema_version": 1, "type": "teardown",
                "task_id": "S3",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715794800000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            }),
            encoding="utf-8",
        )

        _run_hook(
            WAKE_INBOX_DRAIN,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "go",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        events = _read_teardown_events(home, pdir, lead_sid)
        assert len(events) == 1
        assert events[0].get("reason") == "wake_inbox_drained", (
            f"Tier-2 reason must be 'wake_inbox_drained'; "
            f"got {events[0].get('reason')!r}"
        )


# =============================================================================
# TestStopSweepDoubleFireDeduplicated
# =============================================================================


class TestStopSweepDoubleFireDeduplicated:
    """Stop-sweep secondary firing (per stopHooks.ts:334-425) can cause
    TaskCompleted to re-fire across sessions. The marker idempotency
    must catch this: when Tier-1 has already emitted for (team,
    task_id), a subsequent Tier-2 drain MUST NOT produce a duplicate
    journal event.
    """

    def test_tier1_then_tier2_emits_once(self, tmp_path):
        """Tier-1 fires first, creating the .teardown_request_emitted/
        marker. Then a Tier-2 marker for the same task is drained.
        Production policy: the journal event is emitted ONCE total.

        The Tier-2 drain detects the existing marker (via the same
        O_EXCL test-and-set pattern shared between Tier-1 and Tier-2
        per architect refinement spec §2 C4 "Tier-1 / Tier-2 idempotency
        check"). The drain SHOULD still consume the inbox marker file
        (it's stale), but MUST NOT write a duplicate journal event.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-dedup-1-2"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "D1", status="completed", owner="backend-coder")

        # Tier-1 fires first.
        _run_hook(
            TEARDOWN_REQUEST_EMITTER,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "D1", "team_name": team,
                "teammate_name": "backend-coder",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        # Then a Tier-2 marker for the same task appears (Stop-sweep
        # would write this if the teammate had been the original
        # caller; here we simulate it being there for whatever reason).
        inbox = _wake_inbox_dir(home, team)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "20260516T170100Z-x-D1.json").write_text(
            json.dumps({
                "schema_version": 1, "type": "teardown",
                "task_id": "D1",
                "team_name": team,
                "owner": "backend-coder",
                "timestamp_ms": 1715794860000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            }),
            encoding="utf-8",
        )

        # Tier-2 drain.
        _run_hook(
            WAKE_INBOX_DRAIN,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "go",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        events = _read_teardown_events(home, pdir, lead_sid)
        # CRITICAL: only ONE event total despite both paths firing.
        assert len(events) == 1, (
            f"Tier-1 + Tier-2 dedup: expected exactly 1 journal "
            f"event; got {len(events)}: {events!r}. Marker idempotency "
            f"must prevent the double-emit cascade."
        )

    def test_tier2_then_tier1_emits_once(self, tmp_path):
        """Symmetric: Tier-2 drain fires first (claims the marker);
        a later Tier-1 fire for the same (team, task) MUST NOT
        produce a duplicate event.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-int-dedup-2-1"
        _write_session_context(home, lead_sid, pdir, team)
        _write_task(home, team, "D2", status="completed", owner="secretary")

        # Pre-populate the Tier-2 marker.
        inbox = _wake_inbox_dir(home, team)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "20260516T170200Z-x-D2.json").write_text(
            json.dumps({
                "schema_version": 1, "type": "teardown",
                "task_id": "D2",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715794920000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            }),
            encoding="utf-8",
        )

        # Tier-2 drain fires first.
        _run_hook(
            WAKE_INBOX_DRAIN,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "go",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        # Then Tier-1 fires (Stop-sweep secondary).
        _run_hook(
            TEARDOWN_REQUEST_EMITTER,
            json.dumps({
                "session_id": lead_sid, "cwd": pdir,
                "hook_event_name": "TaskCompleted",
                "task_id": "D2", "team_name": team,
                "teammate_name": "secretary",
            }),
            env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
        )

        events = _read_teardown_events(home, pdir, lead_sid)
        assert len(events) == 1, (
            f"Tier-2 + Tier-1 dedup: expected exactly 1 journal "
            f"event; got {len(events)}: {events!r}"
        )


# =============================================================================
# TestFreshSessionPostMergeValidation — documentation-only runbook
# =============================================================================


class TestFreshSessionPostMergeValidation:
    """NOT CI-runnable per CLAUDE.md "Hooks cannot be smoke-tested
    against the running plugin in-session" pin.

    This class is the OPTION-C COUNTER-TEST-BY-REVERT RUNBOOK per
    teachback Q2 resolution (hybrid (b)+(c) strategy). Option (b)
    parametric simulation is CI-runnable and lives in
    test_wake_lifecycle_emitter.py::TestRetiredPostToolUseTeardown
    DoesNotFire::test_revert_C5_produces_double_emission. Option (c)
    is the gold-standard git-revert verification documented below
    for the post-merge reviewer.

    -------------------------------------------------------------------
    POST-MERGE COUNTER-TEST-BY-REVERT RUNBOOK (option c)
    -------------------------------------------------------------------

    Purpose: empirical falsifiability anchor for C5 retirement.
    Architect spec §2 C5 lines 411-412 documents cardinality target:
    `{teardown_request event count: pre-C5 -> 2, post-C5 -> 1}` for
    a lead-driven 1->0 TaskUpdate(completed).

    Execution (post-merge of #763's PR, in a fresh `claude --resume`
    session):

      1. Identify the C5 commit SHA from `git log --oneline | grep
         -i 'retire.*PostToolUse.*Teardown\\|C5'`.

      2. From a fresh worktree pinned to that merge:
           git checkout <merge_sha>
           pytest pact-plugin/tests/test_teardown_request_emitter.py \\
                  pact-plugin/tests/test_wake_lifecycle_emitter.py \\
                  pact-plugin/tests/test_native_hooks_integration.py \\
                  -v
         All tests in those modules must PASS.

      3. Revert C5 in-place (do NOT commit):
           git checkout <c5_sha>^ -- pact-plugin/hooks/wake_lifecycle_emitter.py

      4. Re-run the same scope:
           pytest pact-plugin/tests/test_teardown_request_emitter.py \\
                  pact-plugin/tests/test_wake_lifecycle_emitter.py \\
                  pact-plugin/tests/test_native_hooks_integration.py \\
                  -v
         Expected cardinality of FAIL:
           - test_post_c5_lead_terminal_taskupdate_no_teardown_directive
             goes RED (PostToolUse Teardown branch fires again).
           - test_revert_C5_produces_double_emission's post_c5
             cardinality assertion goes RED (the directive prose
             reappears in additionalContext).
           - TestLeadDrivenCompletionFiresTier1Only's
             test_lead_terminal_taskupdate_writes_no_teardown_marker
             remains GREEN (this is the marker write, orthogonal).

      5. Restore the original:
           git checkout <merge_sha> -- pact-plugin/hooks/wake_lifecycle_emitter.py
         Verify byte-identical restore:
           git diff --quiet -- pact-plugin/hooks/wake_lifecycle_emitter.py
         Exit 0 confirms clean restore. Re-run the test suite to
         confirm ALL GREEN.

      6. Fresh-session validation (NOT CI-runnable per CLAUDE.md pin):
           a. Start a new `claude --resume` session.
           b. Run a /PACT:comPACT with a teammate that goes idle then
              has its task lead-completed.
           c. Verify `~/.claude/pact-sessions/.../session-journal.jsonl`
              contains a teardown_request event with tier="1".
           d. Verify the cron entry deleted (the lead invoked
              /PACT:stop-pending-scan per additionalContext directive).
           e. Repeat for the Tier-2 carve-out path: have the secretary
              self-complete a memory-save task; verify the journal
              entry has tier="2", reason="wake_inbox_drained".

    A successful runbook execution falsifies the assumption that
    C5's deletion is mutually exclusive with C2's addition (per
    architect §8.1 TestCounterTestByRevert). Bundled-commit cardinality
    caveat: C5 ships as its own commit (per refinement §4 atomicity
    notes), so `git revert -n <c5_sha>` is the correct technique here
    — no source-only-revert nuance applies.
    -------------------------------------------------------------------
    """

    def test_runbook_documented_in_class_docstring(self):
        """Pin that the post-merge runbook IS documented in this
        class's docstring (not deleted by a future refactor).

        The runbook is the option-c half of the hybrid counter-test-by-
        revert strategy. Its presence in the test file (rather than
        a separate runbook .md) is intentional — colocates with the
        CI-runnable option-b assertion in
        test_wake_lifecycle_emitter.py for cross-reference.
        """
        doc = TestFreshSessionPostMergeValidation.__doc__ or ""
        assert "POST-MERGE COUNTER-TEST-BY-REVERT RUNBOOK" in doc, (
            "Class docstring must contain the option-c runbook header"
        )
        # Pin load-bearing steps so a partial deletion is caught.
        assert "git checkout <merge_sha>" in doc
        assert "test_revert_C5_produces_double_emission" in doc
        assert "fresh-session validation" in doc.lower()
        assert "session-journal.jsonl" in doc

    def test_runbook_references_companion_ci_option_b(self):
        """The option-c runbook references its option-b CI counterpart
        so reviewers know about both halves of the hybrid strategy.
        """
        doc = TestFreshSessionPostMergeValidation.__doc__ or ""
        assert "test_wake_lifecycle_emitter.py" in doc
        assert "test_revert_C5_produces_double_emission" in doc
        assert "option (b)" in doc.lower() or "option-b" in doc.lower()
