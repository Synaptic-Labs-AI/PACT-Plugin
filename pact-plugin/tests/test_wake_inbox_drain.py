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
