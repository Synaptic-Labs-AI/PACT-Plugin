"""
Integration tests for hooks/wake_inbox_drain.py — the lead-side
UserPromptSubmit hook that drains wake_inbox markers and emits a single
_ARM_DIRECTIVE additionalContext block per prompt.

Combined drain + B-1 count-fallback path. Lead-only gated; teammate
sessions short-circuit to suppressOutput regardless of inbox or count
state. Single-emit discipline: if drain consumed markers, the
count-fallback is NOT run (drain wins; second emit would be redundant).

Counter-test-by-revert: see
tests/runbooks/wake-lifecycle-arm-starvation.md.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
DRAIN = HOOK_DIR / "wake_inbox_drain.py"


def _run_drain(stdin_payload, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    payload_bytes = (
        stdin_payload if isinstance(stdin_payload, bytes)
        else stdin_payload.encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, str(DRAIN)],
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
            "started_at": "2026-05-14T00:00:00Z",
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


def _write_marker(home, team_name, filename, payload):
    inbox = home / ".claude" / "teams" / team_name / "wake_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / filename
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _write_scan_armed_event(home, session_id, project_dir, armed_at=1715731200):
    """Append a `scan_armed` event to the session's journal — mirrors the
    write performed by commands/start-pending-scan.md Step 5 (the CLI
    form `python3 session_journal.py write --type scan_armed ...`). The
    test writes the JSONL line directly to keep the test self-contained
    and not depend on the CLI subprocess.
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    journal = sess_dir / "session-journal.jsonl"
    event = {
        "v": 1,
        "type": "scan_armed",
        "ts": "2026-05-15T00:00:00Z",
        "armed_at": armed_at,
    }
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return journal


def _write_scan_disarmed_event(home, session_id, project_dir, disarmed_at=1715734800):
    """Append a `scan_disarmed` event to the session's journal — mirrors
    the write performed by commands/stop-pending-scan.md Step 5 (paired
    writer to scan_armed). Symmetric with `_write_scan_armed_event`.
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    journal = sess_dir / "session-journal.jsonl"
    event = {
        "v": 1,
        "type": "scan_disarmed",
        "ts": "2026-05-15T00:01:00Z",
        "disarmed_at": disarmed_at,
    }
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return journal


def _drain_out(payload, home):
    rc, out, err = _run_drain(
        json.dumps(payload),
        env_extra={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": payload.get("cwd", ""),
        },
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    return json.loads(out)


# ─── Drain path tests ──────────────────────────────────────────────────


def test_drain_emits_arm_when_marker_present(tmp_path):
    """Test 11 — Drain primary path. With a marker present in the lead's
    wake_inbox/, the hook emits _ARM_DIRECTIVE via additionalContext and
    deletes the marker.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-drain-emits"
    _write_session_context(home, lead_sid, pdir, team)
    marker_path = _write_marker(
        home, team, "20260514T160526Z-some-session-X.json",
        {
            "schema_version": 1,
            "written_at": "2026-05-14T16:05:26.123Z",
            "writer_session_id": "some-session",
            "tool_name": "TaskUpdate",
            "task_id": "X",
            "owner": "backend-coder",
            "trigger": "teammate_self_claim_in_progress",
        },
    )

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, f"Drain must emit Arm; got {out!r}"
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]
    assert not marker_path.exists(), (
        "Drain must unlink the consumed marker"
    )


def test_drain_no_emit_when_inbox_empty_and_count_zero(tmp_path):
    """Test 12 — Negative case. Empty inbox and count=0 → suppressOutput.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-drain-empty"
    _write_session_context(home, lead_sid, pdir, team)
    # No tasks, no markers.

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    assert out.get("suppressOutput") is True, (
        f"Empty inbox + count=0 must suppressOutput; got {out!r}"
    )


def test_fallback_emits_arm_when_count_positive_and_no_marker(tmp_path):
    """Test 13 — B-1 fallback path. Empty inbox but a lifecycle-relevant
    teammate task is on disk → count_active_tasks >= 1 → emit Arm.
    Covers the lead-side unowned-create-then-owner-update dispatch
    pattern surface where no teammate-side write opportunity exists.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-drain-fallback"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "Q", status="in_progress", owner=teammate_owner)

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Fallback must emit Arm when count >= 1; got {out!r}"
    )
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]


def test_fallback_suppressed_in_teammate_session(tmp_path):
    """Test 14 — Lead-only gate. A teammate-session UserPromptSubmit
    with markers on disk AND a positive count must STILL suppressOutput
    because the drain hook is lead-targeted.
    """
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = "teammate-sid"
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-drain-teammate"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, teammate_sid, pdir, team,
        lead_session_id=lead_sid,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "R", status="in_progress", owner=teammate_owner)
    marker_path = _write_marker(
        home, team, "20260514T160600Z-other-R.json",
        {
            "schema_version": 1, "trigger": "teammate_self_claim_in_progress",
            "tool_name": "TaskUpdate", "task_id": "R", "owner": teammate_owner,
        },
    )

    out = _drain_out({
        "session_id": teammate_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    assert out.get("suppressOutput") is True, (
        f"Teammate-session drain must suppressOutput; got {out!r}"
    )
    # Marker must remain on disk — only the lead session is authorized
    # to drain it.
    assert marker_path.exists(), (
        "Teammate-session drain must NOT consume markers"
    )


def test_drain_handles_malformed_marker_as_signal(tmp_path):
    """Test 15 — Fail-conservative on malformed marker. A non-JSON file
    in the inbox is still treated as a wake signal: emit Arm AND delete
    the file. Rationale: a truncated/corrupted marker indicates a
    teammate-session writer attempted the signal and was interrupted;
    the wake intent stands.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-drain-malformed"
    _write_session_context(home, lead_sid, pdir, team)
    inbox = home / ".claude" / "teams" / team / "wake_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    malformed = inbox / "20260514T160700Z-x-y.json"
    malformed.write_text("{ this is not json ", encoding="utf-8")

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Malformed marker must still emit Arm; got {out!r}"
    )
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]
    assert not malformed.exists(), (
        "Drain must unlink the malformed marker"
    )


def test_drain_single_emit_when_both_paths_trigger(tmp_path):
    """Test 16 — Single-emit discipline. With BOTH a marker present AND
    count >= 1, the hook emits exactly one _ARM_DIRECTIVE block (drain
    path consumes; fallback is skipped).
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-drain-single-emit"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(home, team, "S", status="in_progress", owner=teammate_owner)
    marker_path = _write_marker(
        home, team, "20260514T160800Z-some-S.json",
        {
            "schema_version": 1, "trigger": "teammate_self_claim_in_progress",
            "tool_name": "TaskUpdate", "task_id": "S", "owner": teammate_owner,
        },
    )

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    # Exactly one additionalContext block: the JSON stdout contains
    # _ARM_DIRECTIVE prose exactly once.
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"Expected exactly 1 _ARM_DIRECTIVE emit; got {arm_count} in "
        f"stdout={out_str!r}"
    )
    assert not marker_path.exists(), "Marker must be drained"


# ─── Producer-side idempotency tests ───────────────────────────────────


def test_fallback_suppressed_when_scan_armed_event_present(tmp_path):
    """Producer-side idempotency invariant. When a `scan_armed` journal
    event exists in the lead's session-journal AND the B-1 fallback
    path would otherwise fire (count >= 1, no markers), the hook
    suppresses the redundant Arm directive — the cron is already armed.

    Counter-test-by-revert: deleting the journal-event check in
    wake_inbox_drain.py flips this test to RED (the hook would emit
    Arm despite the scan_armed event being present).

    Mutation-pair partner:
    `test_drain_emits_even_when_scan_armed_event_present` pins the
    placement of this check — specifically that it lives ONLY on the
    B-1 fallback path, NOT inside the drain path. The two tests
    together pin a two-axis design decision:
      - delete-the-check mutation → THIS test goes RED
      - relocate-the-check-into-drain-path mutation → partner test
        goes RED
    Keep both green to preserve the design.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-scan-armed-suppress"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T1", status="in_progress", owner=teammate_owner,
    )
    _write_scan_armed_event(home, lead_sid, pdir)

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    assert out.get("suppressOutput") is True, (
        f"scan_armed present + count >= 1 must suppressOutput "
        f"(redundant Arm); got {out!r}"
    )


def test_fallback_emits_when_no_scan_armed_event(tmp_path):
    """Baseline preservation. When no `scan_armed` event is present in
    the journal (cold-start or post-Teardown window) AND count >= 1,
    the hook emits exactly 1 Arm directive — the pre-fix behavior is
    preserved on the no-event path.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-scan-no-event"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T2", status="in_progress", owner=teammate_owner,
    )
    # No scan_armed event written.

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"No scan_armed event + count >= 1 must emit exactly 1 Arm "
        f"directive; got {arm_count} in stdout={out_str!r}"
    )


def test_outer_guard_catches_unexpected_exception(tmp_path):
    """Outer-guard regression invariant. The narrowed
    `except (ImportError, AttributeError, TypeError): pass` around the
    journal-event read is a safety net for future refactors that could
    introduce lazy imports, missing attributes on a reshaped event
    dict, or unguarded comparisons. The call surface today (eager
    top-level import + two layers of internal `except Exception`
    inside read_last_event and _read_last_event_at) catches all
    currently-exercisable failures BEFORE they propagate, so the outer
    guard appears unreachable on the happy path.

    This test pins the guard's behavior by monkey-patching
    `wake_inbox_drain.read_last_event` (the bound reference inside the
    hook module) to raise ImportError, then asserting the hook still
    emits the Arm directive — fail-conservative on unexpected failure
    rather than crashing.

    Replaces the prior `test_fallback_emits_when_journal_unreadable`
    which was phantom-green: it wrote malformed JSON to the journal,
    but `_read_last_event_at`'s inner try-except absorbs malformed JSON
    and returns None without propagating, so the outer guard was never
    exercised. Cross-lane convergence on the same root cause from both
    the test angle (phantom-green) and implementation angle
    (over-broad catch).

    Cannot use subprocess + monkeypatch directly (cross-process).
    Wrapper script imports the hook, replaces the bound reference,
    then invokes main().
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-outer-guard"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T3", status="in_progress", owner=teammate_owner,
    )

    # In-process wrapper: import the hook, replace its bound
    # `read_last_event` with a raising stub, then invoke main(). The
    # subprocess invocation isolates HOME so the hook reads the
    # tmp_path session context.
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(HOOK_DIR)!r})\n"
        "import wake_inbox_drain\n"
        "def _raise(*_a, **_k):\n"
        "    raise ImportError('simulated lazy-import failure')\n"
        "wake_inbox_drain.read_last_event = _raise\n"
        "wake_inbox_drain.main()\n",
        encoding="utf-8",
    )

    payload = json.dumps({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    })
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update({"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir})
    proc = subprocess.run(
        [sys.executable, str(wrapper)],
        input=payload.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"non-zero exit; stderr={proc.stderr.decode('utf-8')}"
    )
    out_str = proc.stdout.decode("utf-8")
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"Outer guard must catch the simulated ImportError and fall "
        f"through to emit; got {arm_count} in stdout={out_str!r}"
    )


def test_drain_emits_even_when_scan_armed_event_present(tmp_path):
    """Drain-path-bypass invariant. Drain-path markers are FRESH
    cross-session signals worth surfacing regardless of armed-state.
    When BOTH a wake_inbox marker AND a scan_armed event exist, the
    drain path consumes the marker and emits Arm — the producer-side
    idempotency check is ONLY on the B-1 fallback path, not the
    drain path.

    Mutation-pair partner:
    `test_fallback_suppressed_when_scan_armed_event_present` pins
    that the check fires on the fallback path. THIS test pins that
    the check does NOT also fire on the drain path. Together:
      - relocate-the-check-into-drain-path mutation → THIS test
        goes RED (drain marker would be suppressed instead of
        surfaced)
      - delete-the-check mutation → partner test goes RED
    Both green pins the placement design.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-scan-drain-bypass"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T4", status="in_progress", owner=teammate_owner,
    )
    _write_scan_armed_event(home, lead_sid, pdir)
    marker_path = _write_marker(
        home, team, "20260515T000100Z-fresh-T4.json",
        {
            "schema_version": 1,
            "trigger": "teammate_self_claim_in_progress",
            "tool_name": "TaskUpdate", "task_id": "T4",
            "owner": teammate_owner,
        },
    )

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Drain path must emit Arm even with scan_armed present; "
        f"got {out!r}"
    )
    assert 'Skill("PACT:start-pending-scan")' in hso["additionalContext"]
    assert not marker_path.exists(), "Drain must consume the marker"


# ─── scan_armed vs scan_disarmed truth-table tests ─────────────────────


def test_fallback_emits_when_armed_then_disarmed(tmp_path):
    """Post-Teardown re-emit invariant. When scan_armed is followed by a
    more-recent scan_disarmed event, the suppression check falls through
    to the emit branch — the cron is no longer armed (teardown fired),
    so a fresh Arm directive must surface to re-arm on the next 0->1
    transition.

    Counter-test-by-revert: reverting the two-event comparison back to
    one-event-presence ("if scan_armed is not None: suppress") flips
    this test to RED — the stale scan_armed event would suppress the
    needed re-arm directive.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-armed-then-disarmed"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T5", status="in_progress", owner=teammate_owner,
    )
    # scan_armed at t=100, scan_disarmed at t=200 (more recent).
    _write_scan_armed_event(home, lead_sid, pdir, armed_at=100)
    _write_scan_disarmed_event(home, lead_sid, pdir, disarmed_at=200)

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"armed-then-disarmed + count >= 1 must emit Arm (no stale "
        f"suppression); got {arm_count} in stdout={out_str!r}"
    )


def test_fallback_suppressed_when_armed_no_disarm(tmp_path):
    """Baseline suppression invariant under the two-event model. When
    scan_armed exists and no scan_disarmed event follows, the check
    suppresses the redundant Arm directive — the cron is currently
    armed. Mirrors the pre-symmetry behavior; preserved across the
    two-event refactor.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-armed-no-disarm"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T6", status="in_progress", owner=teammate_owner,
    )
    _write_scan_armed_event(home, lead_sid, pdir, armed_at=100)
    # No scan_disarmed event.

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    assert out.get("suppressOutput") is True, (
        f"armed + no disarm must suppress; got {out!r}"
    )


def test_fallback_suppressed_when_armed_disarmed_rearmed(tmp_path):
    """Re-arm dominance invariant. When the event sequence is
    arm -> disarm -> re-arm (scan_armed at t=300, scan_disarmed at
    t=200, scan_armed at t=100), the most-recent-of-each-type read
    yields scan_armed at t=300 > scan_disarmed at t=200, so suppression
    correctly applies — the cron is currently armed.

    Counter-test-by-revert: changing the strict-greater comparison to
    `>=` or `<` would not break this test (300 > 200 holds either
    way), but reverting to one-event-presence would also keep this
    test green (since scan_armed exists). This test pins the
    re-arm-dominance branch of the truth table — paired with
    test_fallback_emits_when_armed_then_disarmed, the two tests
    together pin the strict-greater comparison.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-armed-disarmed-rearmed"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T7", status="in_progress", owner=teammate_owner,
    )
    # Event-order on disk: arm(t=100), disarm(t=200), arm(t=300).
    # read_last_event returns the LAST event of each type by reverse
    # scan, so scan_armed=300, scan_disarmed=200. 300 > 200 → suppress.
    _write_scan_armed_event(home, lead_sid, pdir, armed_at=100)
    _write_scan_disarmed_event(home, lead_sid, pdir, disarmed_at=200)
    _write_scan_armed_event(home, lead_sid, pdir, armed_at=300)

    out = _drain_out({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    }, home)
    assert out.get("suppressOutput") is True, (
        f"armed-disarmed-rearmed (re-arm most recent) must suppress; "
        f"got {out!r}"
    )


# ─── Strict-greater equality-boundary test ─────────────────────────────


def test_fallback_emits_when_armed_at_equals_disarmed_at(tmp_path):
    """Strict-greater equality-boundary invariant. When scan_armed.armed_at
    is exactly equal to scan_disarmed.disarmed_at (same-second arm-then-
    disarm via `$(date +%s)`), the comparator `armed_at > disarmed_at`
    evaluates False and falls through to the count_active_tasks fallback
    — fail-conservative emit. Same-second timestamp collisions are
    realistic at second-resolution epoch sources.

    Counter-test-by-revert: mutating the operator from `>` to `>=` at
    wake_inbox_drain.py flips this test (the equality case would
    short-circuit-suppress, masking the conservative emit intent).
    Mutating to `<` would similarly flip this test (the suppress branch
    would never fire at the boundary). The strict-greater operator
    choice IS the contract; this test pins it.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-armed-equals-disarmed"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T8", status="in_progress", owner=teammate_owner,
    )
    # Same-second collision: armed_at == disarmed_at == 200.
    _write_scan_armed_event(home, lead_sid, pdir, armed_at=200)
    _write_scan_disarmed_event(home, lead_sid, pdir, disarmed_at=200)

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"Same-second armed_at == disarmed_at must fall through to "
        f"count_active_tasks fallback (fail-conservative emit); got "
        f"{arm_count} Arm directives in stdout={out_str!r}. "
        f"A 0 count here indicates the strict-greater operator was "
        f"weakened to `>=` — equality now suppresses incorrectly."
    )


# ─── Bool-vs-int discrimination parametric tests ───────────────────────


def _write_event_with_value(home, session_id, project_dir, event_type, field, value):
    """Append a journal event with a raw field value. Bypasses
    session_journal.py's write-side schema validator, which would reject
    bool-in-int fields per _REQUIRED_FIELDS_BY_TYPE. The hook-layer
    bool-discrimination guards in wake_inbox_drain.py are defense-in-
    depth against this exact path: a malformed event on disk (corrupted
    journal, out-of-band writer, future schema drift). This helper
    simulates that disk state directly.
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    journal = sess_dir / "session-journal.jsonl"
    event = {
        "v": 1,
        "type": event_type,
        "ts": "2026-05-15T00:00:00Z",
        field: value,
    }
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return journal


def test_fallback_emits_when_armed_at_is_bool(tmp_path):
    """Bool-vs-int discrimination invariant for armed_at. Python's
    `True`/`False` are instances of `int` (since `bool` subclasses
    `int`), so a naive `isinstance(armed_at, int)` check would let bool
    values flow through the timestamp comparison as 1 or 0. The
    `not isinstance(armed_at, bool)` clause rejects bool values as
    malformed; the hook falls through to count_active_tasks (fail-
    conservative emit).

    Counter-test-by-revert: removing `not isinstance(armed_at, bool)`
    from wake_inbox_drain.py flips this test — the bool True (==int 1)
    would pass the int check; with disarmed absent the hook would
    short-circuit-suppress; this test would see 0 Arm directives
    instead of 1.

    Defense-in-depth: the write-side schema validator at
    session_journal.py _validate_event_schema also rejects bool-in-int
    (pinned by test_session_journal.py). This test pins the
    INDEPENDENT hook-layer guard, which defends against journal
    corruption / out-of-band writes that bypass the validator.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-armed-bool"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T9", status="in_progress", owner=teammate_owner,
    )
    # armed_at as Python bool True → JSON true → loaded back as
    # Python bool True (isinstance(True, int) is True; isinstance(True,
    # bool) is True). With the bool-guard intact, this event is treated
    # as malformed and the hook falls through to the fallback.
    _write_event_with_value(home, lead_sid, pdir, "scan_armed", "armed_at", True)
    # No scan_disarmed event; without the bool-guard, the bool True
    # would short-circuit-suppress at the `disarmed is None → suppress`
    # branch (since `isinstance(True, int)` is True).

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"armed_at=bool(True) must be rejected by the bool-guard and "
        f"fall through to count_active_tasks (fail-conservative emit); "
        f"got {arm_count} Arm directives in stdout={out_str!r}. "
        f"A 0 count here indicates `not isinstance(armed_at, bool)` "
        f"was removed — bool True flowed through the int check and "
        f"suppressed Arm incorrectly."
    )


def test_fallback_emits_when_disarmed_at_is_bool(tmp_path):
    """Bool-vs-int discrimination invariant for disarmed_at. Symmetric
    pin to test_fallback_emits_when_armed_at_is_bool but targeted at
    the disarmed_at guard at the inner-branch comparison
    `isinstance(disarmed_at, int) and not isinstance(disarmed_at, bool)
    and armed_at > disarmed_at`.

    Fixture shape: armed_at is a well-typed int (so the outer arm-
    presence branch is entered) AND disarmed_at is a bool. The inner
    bool-guard on disarmed_at rejects it; the comparison is skipped;
    the hook falls through to the fallback emit path.

    Counter-test-by-revert: removing `not isinstance(disarmed_at,
    bool)` from wake_inbox_drain.py flips this test. The bool False
    (==int 0) would pass the int check; the comparison
    `armed_at(100) > disarmed_at(False==0)` is True; the hook
    short-circuit-suppresses. Without the bool-guard, this test sees
    0 Arm directives instead of 1.

    Distinct from the armed_at test: the disarmed_at guard is at a
    SEPARATE site (the inner branch); removing one guard does not
    affect the other. Two tests pin two sites independently.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-disarmed-bool"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T10", status="in_progress", owner=teammate_owner,
    )
    # armed_at is a well-typed int; disarmed_at is bool False.
    # Without the bool-guard, isinstance(False, int) is True and
    # armed_at(100) > disarmed_at(0==False) suppresses incorrectly.
    _write_scan_armed_event(home, lead_sid, pdir, armed_at=100)
    _write_event_with_value(home, lead_sid, pdir, "scan_disarmed", "disarmed_at", False)

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"disarmed_at=bool(False) must be rejected by the disarmed-at "
        f"bool-guard; the hook should fall through to the fallback "
        f"emit; got {arm_count} Arm directives in stdout={out_str!r}. "
        f"A 0 count here indicates `not isinstance(disarmed_at, bool)` "
        f"was removed — bool False flowed through the int check, "
        f"armed_at(100) > 0 suppressed Arm incorrectly."
    )


# ─── Widened-except contract test (Finding-2) ──────────────────────────


@pytest.mark.parametrize("exception_class", [
    ImportError,
    AttributeError,
    TypeError,
    ValueError,
    OSError,
    RuntimeError,
    KeyError,
])
def test_outer_guard_catches_arbitrary_exception(tmp_path, exception_class):
    """Widened-except contract invariant. The producer-side idempotency
    block's outer guard must catch arbitrary exceptions raised by
    `read_last_event` and fall through to fail-conservative emit. The
    catch clause is `except Exception` (widened from the prior narrow
    tuple `(ImportError, AttributeError, TypeError)` to symmetrically
    handle any failure shape from the journal-read path).

    Parametrized over 7 exception classes — 3 in the original narrow
    tuple (ImportError, AttributeError, TypeError) plus 4 outside it
    (ValueError, OSError, RuntimeError, KeyError). Under the widened
    catch, all 7 rows are handled by the producer-side guard directly
    and the hook emits Arm via the fallback path.

    Counter-test-by-revert: re-narrowing the except clause back to
    `(ImportError, AttributeError, TypeError)` flips rows 4-7 to
    propagate past the producer-side guard. main()'s outer fail-open
    catches the exception but emits suppressOutput instead of Arm —
    the failure mode is silent under-emission, not crash. This test
    pins the producer-side `except Exception` as the contract that
    keeps emit fail-conservative.

    Mechanism: in-process wrapper-script subprocess (mirrors the
    pattern at test_outer_guard_catches_unexpected_exception) monkey-
    patches `wake_inbox_drain.read_last_event` to raise the
    parametrized exception class.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-widened-except"
    teammate_owner = "backend-coder"
    _write_session_context(
        home, lead_sid, pdir, team,
        members=[
            {"name": teammate_owner, "agentId": "agent-bc"},
            {"name": "lead", "agentId": "agent-lead"},
        ],
        lead_agent_id="agent-lead",
    )
    _write_task(
        home, team, "T11", status="in_progress", owner=teammate_owner,
    )

    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(HOOK_DIR)!r})\n"
        "import wake_inbox_drain\n"
        f"_exc_class = {exception_class.__name__}\n"
        "def _raise(*_a, **_k):\n"
        "    raise _exc_class('simulated widened-except contract probe')\n"
        "wake_inbox_drain.read_last_event = _raise\n"
        "wake_inbox_drain.main()\n",
        encoding="utf-8",
    )

    payload = json.dumps({
        "session_id": lead_sid, "cwd": pdir,
        "hook_event_name": "UserPromptSubmit",
        "prompt": "go",
    })
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update({"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir})
    proc = subprocess.run(
        [sys.executable, str(wrapper)],
        input=payload.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, (
        f"non-zero exit on {exception_class.__name__}; "
        f"stderr={proc.stderr.decode('utf-8')}"
    )
    out_str = proc.stdout.decode("utf-8")
    arm_count = out_str.count("First active teammate task created")
    assert arm_count == 1, (
        f"Widened-except contract violation for {exception_class.__name__}: "
        f"the producer-side guard must catch the exception and fall "
        f"through to count_active_tasks (fail-conservative emit); "
        f"got {arm_count} Arm directives in stdout={out_str!r}. "
        f"A 0 count indicates the exception propagated past the "
        f"producer-side except block; re-narrow the except clause "
        f"to (ImportError, AttributeError, TypeError) regression."
    )
