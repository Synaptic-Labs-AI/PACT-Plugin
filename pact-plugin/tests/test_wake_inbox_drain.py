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

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
DRAIN = HOOK_DIR / "wake_inbox_drain.py"

# Byte-coupled with session_journal.make_event's ts format and with the
# `_TS_FMT` literal in wake_inbox_drain.py producer-side idempotency
# check. Used by `_iso_ts` to render integer-epoch helper arguments as
# canonical ISO strings the consumer's strptime can parse.
ISO_FORMAT_LITERAL = "%Y-%m-%dT%H:%M:%SZ"


def _iso_ts(epoch_seconds: int) -> str:
    """Render an integer-epoch as the canonical ISO-8601 UTC string
    that wake_inbox_drain.py's strptime literal parses
    (`%Y-%m-%dT%H:%M:%SZ`)."""
    return datetime.datetime.fromtimestamp(
        epoch_seconds, tz=datetime.timezone.utc
    ).strftime(ISO_FORMAT_LITERAL)


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


_DEFAULT_SCAN_ARMED_TS = _iso_ts(1715731200)
_DEFAULT_SCAN_DISARMED_TS = _iso_ts(1715734800)


def _write_scan_armed_event(home, session_id, project_dir, ts=_DEFAULT_SCAN_ARMED_TS):
    """Append a `scan_armed` event to the session's journal — mirrors the
    write performed by commands/start-pending-scan.md Step 5 (the CLI
    form `python3 session_journal.py write --type scan_armed ...`). The
    test writes the JSONL line directly to keep the test self-contained
    and not depend on the CLI subprocess.

    `ts` is the auto-stamped ISO-8601 UTC timestamp matching
    `session_journal.make_event`'s format literal; the producer-side
    idempotency check in `wake_inbox_drain.py:684+` parses it via
    strptime to int epoch. Callers may override `ts` to control the
    comparison branch under test (use `_iso_ts(epoch)` for integer-
    epoch readability).
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    journal = sess_dir / "session-journal.jsonl"
    event = {
        "v": 1,
        "type": "scan_armed",
        "ts": ts,
    }
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    return journal


def _write_scan_disarmed_event(home, session_id, project_dir, ts=_DEFAULT_SCAN_DISARMED_TS):
    """Append a `scan_disarmed` event to the session's journal — mirrors
    the write performed by commands/stop-pending-scan.md Step 5 (paired
    writer to scan_armed). Symmetric with `_write_scan_armed_event`.

    `ts` is the auto-stamped ISO-8601 UTC timestamp; the producer-side
    idempotency check parses it via strptime to int epoch. Callers may
    override `ts` to control the comparison branch under test.
    """
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    journal = sess_dir / "session-journal.jsonl"
    event = {
        "v": 1,
        "type": "scan_disarmed",
        "ts": ts,
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
        "session_id": teammate_sid,
        "agent_id": "agent-bc",
        "cwd": pdir,
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
    arm_count = out_str.count("Active teammate work detected")
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
    arm_count = out_str.count("Active teammate work detected")
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
    arm_count = out_str.count("Active teammate work detected")
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
    _write_scan_armed_event(home, lead_sid, pdir, ts=_iso_ts(100))
    _write_scan_disarmed_event(home, lead_sid, pdir, ts=_iso_ts(200))

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("Active teammate work detected")
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
    _write_scan_armed_event(home, lead_sid, pdir, ts=_iso_ts(100))
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
    _write_scan_armed_event(home, lead_sid, pdir, ts=_iso_ts(100))
    _write_scan_disarmed_event(home, lead_sid, pdir, ts=_iso_ts(200))
    _write_scan_armed_event(home, lead_sid, pdir, ts=_iso_ts(300))

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


def test_fallback_emits_when_armed_ts_equals_disarmed_ts(tmp_path):
    """Strict-greater equality-boundary invariant. When scan_armed.ts is
    exactly equal to scan_disarmed.ts (same-second arm-then-disarm at
    second-resolution `%Y-%m-%dT%H:%M:%SZ` precision), the comparator
    `armed_epoch > disarmed_epoch` evaluates False and the hook falls
    through to the count_active_tasks fallback — fail-conservative
    emit. Same-second timestamp collisions are realistic at second-
    resolution epoch sources.

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
    # Same-second collision: scan_armed.ts == scan_disarmed.ts (both
    # render to the same ISO string at second resolution).
    same_second_ts = _iso_ts(200)
    _write_scan_armed_event(home, lead_sid, pdir, ts=same_second_ts)
    _write_scan_disarmed_event(home, lead_sid, pdir, ts=same_second_ts)

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("Active teammate work detected")
    assert arm_count == 1, (
        f"Same-second scan_armed.ts == scan_disarmed.ts must fall "
        f"through to count_active_tasks fallback (fail-conservative "
        f"emit); got {arm_count} Arm directives in stdout={out_str!r}. "
        f"A 0 count here indicates the strict-greater operator was "
        f"weakened to `>=` — equality now suppresses incorrectly."
    )


# ─── Malformed-ts discrimination parametric tests ──────────────────────


def _write_event_with_value(home, session_id, project_dir, event_type, field, value):
    """Append a journal event with a raw field value. Bypasses
    session_journal.py's write-side schema validator, which would reject
    bool-in-int fields per _REQUIRED_FIELDS_BY_TYPE. The hook-layer
    fail-conservative guards in wake_inbox_drain.py are defense-in-
    depth against this exact path: a malformed event on disk (corrupted
    journal, out-of-band writer, future schema drift). This helper
    simulates that disk state directly.

    The `field`/`value` pair OVERRIDES any default field in the event
    dict (including `ts`) — the caller's assignment is the last
    statement in the dict literal so caller-supplied values win.
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


def test_fallback_emits_when_armed_ts_is_malformed_falls_through_to_count_path(tmp_path):
    """Fail-conservative-on-malformed-ts COMPOSITE invariant for scan_armed.

    Pre-#821 failure mode (bool-vs-int): Python `bool` subclassed `int`,
    so a naive `isinstance(armed_at, int)` check would let bool values
    flow through the timestamp comparison as 1 or 0; the
    `not isinstance(armed_at, bool)` clause rejected them as malformed.

    Post-#821 failure mode (ts-string-malformation): the consumer reads
    `scan_armed.ts` (auto-stamped ISO string) and parses via strptime.
    A writer-bug or journal-corruption that lands `ts=42` / `ts=True` /
    `ts=None` / `ts=""` / unparseable string must NOT cause the
    producer-side idempotency check to suppress incorrectly.

    Layered defense (composite invariant — what this test pins):

      1. Outer `try: ... except Exception:` (wake_inbox_drain.py ~696/733):
         catch-all for the producer-side block; on ANY raise inside the
         block (including TypeError from strptime(42, FMT)), execution
         falls through to count_active_tasks (fail-conservative emit).
         THIS is the load-bearer that the documented `ts=42` scenario
         exercises directly — even with the inner str-guard and inner
         try/except both stripped, the outer catch still produces the
         correct fall-through behavior.

      2. `isinstance(armed_ts, str) and armed_ts` str-guard +
         inner `try/except (TypeError, ValueError)` around strptime:
         defense-in-depth REDUNDANCY layers that short-circuit cleanly
         (no raise → no outer catch needed) when ts is recognizably
         malformed. Good engineering for future failure modes not yet
         anticipated by the current test suite (e.g., a future ts shape
         that strptime accepts but yields a nonsense epoch); NOT
         independent load-bearers for the documented `ts=42` scenario.

    Counter-test-by-revert (CUMULATIVE strip, empirical, verified by
    review-phase F1 probe). Each row strips LAYERS ON TOP OF the prior
    row's mutation — these are NOT 3 independent strips.

      Row 1 (strip only the str-guard): test STILL passes. The inner
        try/except catches the strptime TypeError raised on ts=42.

      Row 2 (CUMULATIVE: strip the str-guard AND the inner try/except):
        test STILL passes. The outer `except Exception:` catches the
        propagating TypeError.

      Row 3 (CUMULATIVE: strip the str-guard AND the inner try/except
        AND narrow the outer `except Exception:` to `except ImportError:`):
        test FAILS. With all three layers stripped, the malformed-ts
        TypeError now propagates past the producer-side block to
        main()'s top-level catch which prints _SUPPRESS_OUTPUT
        (under-emit, 0 Arm directives observed).

    What this test pins: the COMPOSITE invariant that the malformed-ts
    fall-through must hold. The outer catch is the cheapest single
    layer to remove that breaks the invariant (under Row 3's
    cumulative mutation), but Row 1 + Row 2 demonstrate that the
    str-guard and inner try/except are honest defense-in-depth —
    they short-circuit cleanly when ts is recognizably malformed,
    sparing the outer catch, AND they are robust against future
    hypothetical refactors that might widen / narrow / replace the
    outer catch.

    Distinct from the symmetric ..._disarmed_ts_... test: the armed-
    side malformation hits the arm-presence guard (`if armed is not
    None`) and the inner armed-ts parse block never executes the
    happy path; the disarmed-side malformation hits the inner-branch
    comparator (the arm-presence guard has already validated armed).
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-armed-malformed-ts"
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
    # armed.ts as integer 42 (not str) → isinstance(armed_ts, str) is
    # False → armed_epoch stays None → hook falls through to
    # count_active_tasks. The composite defense (str-guard + inner
    # try/except + outer `except Exception:`) means stripping any 1
    # or 2 layers leaves the fall-through intact; ONLY the cumulative
    # 3-layer strip breaks it — see composite-invariant docstring
    # above for the empirical CUMULATIVE counter-test-by-revert recipe.
    _write_event_with_value(home, lead_sid, pdir, "scan_armed", "ts", 42)
    # No scan_disarmed event. The composite invariant pinned: the
    # malformed-ts fall-through must hold. Cumulative strip of all
    # 3 layers (str-guard + inner try/except + outer except Exception)
    # would propagate the TypeError to main()'s top-level catch which
    # prints _SUPPRESS_OUTPUT (under-emit, 0 Arm directives).

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("Active teammate work detected")
    assert arm_count == 1, (
        f"scan_armed.ts=int(42) must fall through to "
        f"count_active_tasks (fail-conservative emit) via the "
        f"composite-layered defense (str-guard + inner strptime "
        f"try/except + outer `except Exception:`); got {arm_count} "
        f"Arm directives in stdout={out_str!r}. A 0 count here "
        f"indicates ALL THREE layers were stripped cumulatively — "
        f"the malformed-ts TypeError propagated past the producer-"
        f"side block to main()'s top-level catch which prints "
        f"_SUPPRESS_OUTPUT, causing under-emit incorrectly. See the "
        f"composite-invariant docstring above for the CUMULATIVE "
        f"counter-test-by-revert recipe (each layer individually is "
        f"defense-in-depth; only the 3-layer cumulative strip breaks "
        f"the invariant)."
    )


def test_fallback_emits_when_disarmed_ts_is_malformed_falls_through_to_count_path(tmp_path):
    """Fail-conservative-on-malformed-ts COMPOSITE invariant for scan_disarmed.
    Symmetric pin to
    test_fallback_emits_when_armed_ts_is_malformed_falls_through_to_count_path
    but targeted at the INNER-branch malformed-ts handling on
    `disarmed_ts` parsing.

    Pre-#821 failure mode (bool-vs-int): Python bool False (==int 0)
    passing through `isinstance(disarmed_at, int)` would suppress when
    `armed_at > 0`; the `not isinstance(disarmed_at, bool)` clause
    rejected it.

    Post-#821 failure mode (ts-string-malformation): the consumer
    parses `scan_disarmed.ts` via strptime; the disarmed-side defense
    is layered identically to the armed-side:

      1. Outer `try: ... except Exception:` (wake_inbox_drain.py
         catching the producer-side block): the load-bearer that
         catches a propagating strptime TypeError when disarmed.ts is
         non-str (e.g., bool False). THIS is the layer the documented
         `disarmed_ts=False` scenario exercises directly.

      2. Inner `isinstance(disarmed_ts, str) and disarmed_ts` guard +
         `try/except (TypeError, ValueError)` around strptime: defense-
         in-depth REDUNDANCY that short-circuits cleanly on
         recognizably-malformed disarmed_ts, sparing the outer catch.
         NOT independently load-bearing for the documented scenario.

    Fixture shape: armed_ts is a well-typed ISO string (so the
    arm-presence guard `if armed is not None` AND the armed-side
    str-guard both pass), AND disarmed_ts is a non-str (`False`). The
    disarmed-side composite defense causes the comparator to be
    skipped (whether via disarmed-side inner short-circuit or outer
    catch); the hook falls through to fail-conservative emit.

    Distinct from the armed_ts test: the disarmed_ts handling lives at
    a SEPARATE site (the inner-branch comparator of the producer-side
    block, AFTER the arm-presence guard has validated armed) — even
    though the same OUTER `except Exception:` catches both sides, the
    symmetric pin pair pins the structural symmetry of the armed-side
    and disarmed-side parsing patterns.

    Counter-test-by-revert (CUMULATIVE strip, empirical, verified by
    review-phase F1 probe). Each row strips LAYERS ON TOP OF the prior
    row's mutation — these are NOT 3 independent strips.

      Row 1 (strip only the disarmed-side str-guard): test STILL
        passes. The disarmed-side inner try/except catches the
        strptime TypeError raised on ts=False.

      Row 2 (CUMULATIVE: strip the disarmed-side str-guard AND the
        disarmed-side inner try/except): test STILL passes. The outer
        `except Exception:` catches the propagating TypeError.

      Row 3 (CUMULATIVE: strip the disarmed-side str-guard AND the
        disarmed-side inner try/except AND narrow the outer
        `except Exception:` to `except ImportError:`): test FAILS.
        With all three layers stripped, the malformed disarmed-ts
        TypeError now propagates past the producer-side block to
        main()'s top-level catch which prints _SUPPRESS_OUTPUT
        (under-emit, 0 Arm directives observed).

    What this test pins: the COMPOSITE invariant that the disarmed-
    side malformed-ts fall-through must hold. The outer catch is the
    cheapest single layer to remove that breaks the invariant (under
    Row 3's cumulative mutation), but the disarmed-side str-guard
    and inner try/except are honest defense-in-depth — they short-
    circuit cleanly when disarmed_ts is recognizably malformed,
    sparing the outer catch.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-disarmed-malformed-ts"
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
    # scan_armed.ts is well-typed ISO; scan_disarmed.ts is bool False.
    # The composite defense (disarmed-side str-guard + disarmed-side
    # inner try/except + outer `except Exception:`) means stripping
    # any 1 or 2 layers leaves the fall-through intact; ONLY the
    # cumulative 3-layer strip breaks it — see composite-invariant
    # docstring above for the empirical CUMULATIVE counter-test-by-
    # revert recipe.
    _write_scan_armed_event(home, lead_sid, pdir, ts=_iso_ts(100))
    _write_event_with_value(home, lead_sid, pdir, "scan_disarmed", "ts", False)

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("Active teammate work detected")
    assert arm_count == 1, (
        f"scan_disarmed.ts=bool(False) must fall through to the "
        f"fallback emit (fail-conservative) via the composite-layered "
        f"defense (disarmed-side str-guard + disarmed-side inner "
        f"strptime try/except + outer `except Exception:`); got "
        f"{arm_count} Arm directives in stdout={out_str!r}. A 0 count "
        f"here indicates ALL THREE layers were stripped cumulatively "
        f"— the malformed disarmed-ts TypeError propagated past the "
        f"producer-side block to main()'s top-level catch which "
        f"prints _SUPPRESS_OUTPUT, causing under-emit incorrectly. "
        f"See the composite-invariant docstring above for the "
        f"CUMULATIVE counter-test-by-revert recipe (each layer "
        f"individually is defense-in-depth; only the 3-layer "
        f"cumulative strip breaks the invariant)."
    )


# ─── Format-drift fall-through coverage (F6 from PR #820 peer-review) ──


@pytest.mark.parametrize(
    "drifted_ts_armed",
    [
        # Sub-second-fraction: strptime against `_TS_FMT = '%Y-%m-%dT%H:%M:%SZ'`
        # raises ValueError on the trailing `.123` — fall-through expected.
        "2026-05-15T00:00:00.123Z",
        # Sub-second-fraction (3-digit milliseconds variant).
        "2026-05-15T00:00:00.999Z",
        # Mixed TZ: explicit `+00:00` offset suffix instead of `Z`. strptime
        # against `Z`-anchored `_TS_FMT` raises ValueError on the `+00:00`.
        "2026-05-15T00:00:00+00:00",
        # Mixed TZ: non-zero offset. Same fail-conservative behavior.
        "2026-05-15T00:00:00-05:00",
        # Future-relaxation candidate: `fromisoformat`-shape with no tz suffix
        # at all. strptime against Z-anchored format raises ValueError.
        "2026-05-15T00:00:00",
        # Trailing whitespace inside the string (a writer-bug variant that
        # `isinstance(ts, str) and ts` passes but strptime rejects).
        # EMPIRICAL: `datetime.strptime('2026-05-15T00:00:00Z ',
        # '%Y-%m-%dT%H:%M:%SZ')` raises `ValueError: unconverted data
        # remains:  ` on Python 3.9.6 / 3.12.7 / 3.13.5 / 3.14.5
        # (verified 2026-05-24). This case exercises the inner
        # try/except (TypeError, ValueError) fall-through path as the
        # docstring claims — NOT a valid-parse-no-disarm path. Pin
        # blocks future re-investigation of an earlier phantom claim
        # that strptime silently ignores trailing whitespace.
        "2026-05-15T00:00:00Z ",
    ],
    ids=[
        "subsecond_3digit",
        "subsecond_999",
        "mixed_tz_plus0000",
        "mixed_tz_minus0500",
        "no_tz_suffix",
        "trailing_whitespace",
    ],
)
def test_fallback_emits_when_armed_ts_format_drifts_falls_through_to_count_path(
    tmp_path, drifted_ts_armed
):
    """Format-drift fail-conservative COMPOSITE invariant for scan_armed.ts.

    F6 finding from PR #820 peer-review (test-engineer Task #45): the
    Q3 audit-prose ban on `fromisoformat` switching (and the architect
    §3.3 binding decision against direct lex compare) BOTH rest on a
    behavioral claim — that any `ts` shape that doesn't match the
    canonical `%Y-%m-%dT%H:%M:%SZ` literal MUST cause the producer-
    side idempotency check to fall through to count_active_tasks
    (fail-conservative emit), NOT to crash, suppress incorrectly, or
    silently misorder events. The format-drift behavior is the
    architectural-correctness defense the strptime-not-lex-compare
    pin (test_python_consumer_parses_ts_via_strptime_not_string_compare)
    relies on — but until F6, no test EXERCISED the fall-through
    behavior under format drift; only the audit-prose ban and the
    strptime-presence pin defended the same surface structurally.

    Fixture: write a `scan_armed` event with a drifted-format `ts` on
    disk (parametrized across 6 representative drift shapes covering
    sub-second fractions, mixed TZ suffixes, no-TZ-suffix, and
    trailing-whitespace bug). No `scan_disarmed` event. The hook must
    fall through to count_active_tasks (1 in-progress teammate task
    → 1 Arm directive emit).

    Layered defense (COMPOSITE invariant — what this test pins):

      1. Outer `try: ... except Exception:` (wake_inbox_drain.py
         ~696/733): catches ANY raise from the producer-side block;
         strptime's ValueError on drifted `ts` propagates here in the
         worst case.
      2. Inner `try/except (TypeError, ValueError)` around strptime
         (wake_inbox_drain.py ~717/720): catches ValueError directly,
         sets `armed_epoch = None`, falls through cleanly without
         escalating to the outer catch. This is the layer most
         exercised by format-drift inputs (the str-guard at
         `isinstance(armed_ts, str) and armed_ts` passes — all 6
         parametrized drift shapes ARE non-empty strings — so
         the strptime call IS reached and raises ValueError, caught
         by the inner try/except).
      3. `isinstance(armed_ts, str) and armed_ts` str-guard
         (wake_inbox_drain.py ~716): defense-in-depth for non-str
         malformed-ts (covered by the sibling _is_malformed_ test);
         not the load-bearing layer for format-drift since drifted
         shapes are str-typed.

    Counter-test-by-revert (CUMULATIVE strip, empirical, verified
    during F6 fold). Each row strips LAYERS ON TOP OF the prior
    row — these are NOT independent strips. The cumulative framing
    matches the discipline established for the Q2 retargeted-test
    docstrings in commit-g (per secretary `0d19dfbd`).

      Row 1 (strip ONLY the inner `try/except (TypeError, ValueError)`):
        test STILL passes for all 6 parametrized drift shapes —
        strptime's ValueError propagates past the inner catch but
        the outer `except Exception:` still catches it, and
        fall-through to count_active_tasks still occurs.

      Row 2 (CUMULATIVE: strip the inner try/except AND narrow the
        outer `except Exception:` to `except ImportError:`): test
        FAILS for all 6 parametrized drift shapes — the ValueError
        now propagates past both layers to main()'s top-level
        catch which prints _SUPPRESS_OUTPUT (under-emit, 0 Arm
        directives observed).

    What this test pins: the COMPOSITE invariant that format-drifted
    `ts` falls through to count_active_tasks. This DEFENDS the
    behavioral claim that the Q3 audit-prose ban rests on —
    `fromisoformat` switching (or any format relaxation that introduces
    drift between writer and reader) is fail-conservative, not
    silently-broken. Pairs with the F4 strptime-not-lex-compare pin
    in test_pending_scan_coupling_invariant.py: F4 pins the STRUCTURAL
    shape (consumer uses strptime CALL with result-binding); F6 pins
    the BEHAVIORAL outcome (drifted format → fall-through).
    Together they form the test-coverage defense for the
    architectural decision to use strptime rather than lex compare.

    Symmetric pin for scan_disarmed.ts in
    test_fallback_emits_when_disarmed_ts_format_drifts_falls_through_to_count_path.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = f"team-armed-format-drift"
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
        home, team, "T-fd", status="in_progress", owner=teammate_owner,
    )
    # armed.ts is a str (passes isinstance check) but does NOT match the
    # canonical _TS_FMT → strptime raises ValueError → inner
    # try/except catches → armed_epoch stays None → hook falls
    # through to count_active_tasks (fail-conservative emit).
    _write_event_with_value(
        home, lead_sid, pdir, "scan_armed", "ts", drifted_ts_armed,
    )
    # No scan_disarmed event.

    rc, out_str, err = _run_drain(
        json.dumps({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    arm_count = out_str.count("Active teammate work detected")
    assert arm_count == 1, (
        f"scan_armed.ts={drifted_ts_armed!r} (format-drift shape) MUST "
        f"fall through to count_active_tasks (fail-conservative emit) "
        f"via the composite-layered defense (inner strptime "
        f"try/except + outer `except Exception:`); got {arm_count} Arm "
        f"directives in stdout={out_str!r}. A 0 count here indicates "
        f"the inner try/except AND the outer except Exception were "
        f"BOTH stripped cumulatively — the strptime ValueError "
        f"propagated to main()'s top-level catch which prints "
        f"_SUPPRESS_OUTPUT, causing under-emit incorrectly. This is "
        f"the BEHAVIORAL counterpart to the F4 strptime-not-lex-compare "
        f"structural pin in test_pending_scan_coupling_invariant.py; "
        f"the Q3 audit-prose ban on fromisoformat (and the architect "
        f"§3.3 binding against direct lex compare) rests on this "
        f"format-drift fall-through behavior being load-bearing."
    )


@pytest.mark.parametrize(
    "drifted_ts_disarmed",
    [
        "2026-05-15T00:00:00.123Z",
        "2026-05-15T00:00:00.999Z",
        "2026-05-15T00:00:00+00:00",
        "2026-05-15T00:00:00-05:00",
        "2026-05-15T00:00:00",
        "2026-05-15T00:00:00Z ",
    ],
    ids=[
        "subsecond_3digit",
        "subsecond_999",
        "mixed_tz_plus0000",
        "mixed_tz_minus0500",
        "no_tz_suffix",
        "trailing_whitespace",
    ],
)
def test_fallback_emits_when_disarmed_ts_format_drifts_falls_through_to_count_path(
    tmp_path, drifted_ts_disarmed
):
    """Format-drift fail-conservative COMPOSITE invariant for
    scan_disarmed.ts. Symmetric pin to
    test_fallback_emits_when_armed_ts_format_drifts_falls_through_to_count_path
    targeting the INNER-branch comparator's disarmed-side strptime
    parse.

    Fixture: armed.ts is well-typed canonical ISO; disarmed.ts is a
    drifted-format str. The arm-presence guard passes; the inner
    armed-side strptime succeeds; armed_epoch is well-typed. Then
    the inner disarmed-side strptime raises ValueError on the
    drifted disarmed.ts; the disarmed-side inner try/except catches;
    disarmed_epoch stays None; the
    `disarmed_epoch is not None and armed_epoch > disarmed_epoch`
    comparison is skipped; the hook falls through to
    count_active_tasks.

    Counter-test-by-revert cumulative recipe is symmetric to the
    armed-side test — see armed-side docstring for the layered
    defense and the empirical cumulative-strip rows.
    """
    home = tmp_path / "home"; home.mkdir()
    lead_sid = "lead-sid"
    pdir = "/tmp/p"
    team = "team-disarmed-format-drift"
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
        home, team, "T-fd", status="in_progress", owner=teammate_owner,
    )
    # armed.ts is well-typed canonical; disarmed.ts is format-drift.
    _write_scan_armed_event(home, lead_sid, pdir, ts=_iso_ts(100))
    _write_event_with_value(
        home, lead_sid, pdir, "scan_disarmed", "ts", drifted_ts_disarmed,
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
    arm_count = out_str.count("Active teammate work detected")
    assert arm_count == 1, (
        f"scan_disarmed.ts={drifted_ts_disarmed!r} (format-drift shape) "
        f"MUST fall through to count_active_tasks (fail-conservative "
        f"emit) via the composite-layered defense (disarmed-side inner "
        f"strptime try/except + outer `except Exception:`); got "
        f"{arm_count} Arm directives in stdout={out_str!r}. A 0 count "
        f"here indicates the inner disarmed-side try/except AND the "
        f"outer except Exception were BOTH stripped cumulatively — "
        f"the strptime ValueError propagated to main()'s top-level "
        f"catch which prints _SUPPRESS_OUTPUT, causing under-emit "
        f"incorrectly. Pairs with the F4 strptime-not-lex-compare "
        f"structural pin; F6 BEHAVIORAL defense for the format-drift "
        f"fall-through invariant."
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
    arm_count = out_str.count("Active teammate work detected")
    assert arm_count == 1, (
        f"Widened-except contract violation for {exception_class.__name__}: "
        f"the producer-side guard must catch the exception and fall "
        f"through to count_active_tasks (fail-conservative emit); "
        f"got {arm_count} Arm directives in stdout={out_str!r}. "
        f"A 0 count indicates the exception propagated past the "
        f"producer-side except block; re-narrow the except clause "
        f"to (ImportError, AttributeError, TypeError) regression."
    )


# =============================================================================
# C0 consumer side + C4 type-aware dispatch tests for #763
# =============================================================================
#
# C0 (consumer): _drain_markers handles per-type accounting. Pre-C0
# markers (no `type` field) default to "arm" for backward-compat.
# Corrupt or unknown-type markers also default to "arm" (fail-
# conservative — produce a wake signal even on a malformed marker).
#
# C4 (dispatch): _decide_and_emit routes type="teardown" markers to
# _emit_teardown (which prints the _TEARDOWN_DIRECTIVE additionalContext)
# AND writes a teardown_request journal event with tier="2", reason=
# "wake_inbox_drained". When both arm + teardown markers drain in one
# fire, teardown takes precedence (it reflects the most recent
# completion-authority state).
#
# Counter-test-by-revert for C4 dispatch: removing the `type` field
# read from _drain_markers flips test_drain_dispatch_on_type_field
# results (all markers route to _emit_arm, even type="teardown" ones).


class TestArmMarkerTypeFieldBackwardCompat:
    """C0 consumer side: pre-C0 markers (no `type` field) drain as arm.
    Corrupt JSON markers and unknown-type markers ALSO drain as arm
    (fail-conservative). The default is the architecture's hinge:
    legacy markers on disk during the upgrade window must not be lost.
    """

    def test_drain_marker_without_type_field_treated_as_arm(self, tmp_path):
        """A marker without a `type` field (pre-C0 shape) drains as
        an arm signal — wake_inbox_drain emits _ARM_DIRECTIVE.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c0-bw-no-type"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160000Z-x-T1.json",
            {
                # Pre-C0 marker shape — no `type` field.
                "schema_version": 1,
                "trigger": "teammate_self_claim_in_progress",
                "tool_name": "TaskUpdate",
                "task_id": "T1",
                "owner": "backend-coder",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Pre-C0 marker must drain as arm; got {out!r}"
        )
        assert "PACT:start-pending-scan" in hso.get("additionalContext", ""), (
            f"Drain output must invoke start-pending-scan (arm); got {hso!r}"
        )

    def test_drain_marker_with_unknown_type_treated_as_arm(self, tmp_path):
        """Unknown `type` tokens (e.g. "purple", "halt") drain as arm.
        Fail-conservative dispatch — producing a wake signal on an
        unexpected token is safer than dropping it (the operator can
        always re-run /PACT:stop-pending-scan if the cron is already
        retired).
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c0-bw-unknown-type"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160100Z-x-T2.json",
            {
                "schema_version": 1, "type": "purple_unknown_token",
                "trigger": "teammate_self_claim_in_progress",
                "tool_name": "TaskUpdate", "task_id": "T2",
                "owner": "backend-coder",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Unknown-type marker must drain (fail-conservative arm); "
            f"got {out!r}"
        )
        assert "PACT:start-pending-scan" in hso.get("additionalContext", ""), (
            f"Drain output must default to arm on unknown type; got {hso!r}"
        )


class TestDrainDispatchOnTypeField:
    """C4 dispatch: _drain_markers returns per-type count dict;
    _decide_and_emit routes based on the type token. Arm markers go to
    _emit_arm (existing behavior); Teardown markers go to _emit_teardown
    (new) and write a teardown_request journal event.
    """

    def test_arm_marker_routes_to_emit_arm(self, tmp_path):
        """A type="arm" marker drains via the arm path — output
        contains _ARM_DIRECTIVE prose (start-pending-scan).
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-arm-routes"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160200Z-x-T3.json",
            {
                "schema_version": 1, "type": "arm",
                "trigger": "teammate_self_claim_in_progress",
                "tool_name": "TaskUpdate", "task_id": "T3",
                "owner": "backend-coder",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None
        assert "PACT:start-pending-scan" in hso.get("additionalContext", "")
        assert "PACT:stop-pending-scan" not in hso.get("additionalContext", "")

    def test_teardown_marker_routes_to_emit_teardown(self, tmp_path):
        """A type="teardown" marker drains via the teardown path —
        output contains _TEARDOWN_DIRECTIVE prose (stop-pending-scan).
        This is the C4 net-new dispatch branch.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-teardown-routes"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160300Z-x-T4.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "T4",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792180000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Teardown marker must produce hookSpecificOutput; got {out!r}"
        )
        assert "PACT:stop-pending-scan" in hso.get("additionalContext", ""), (
            f"Drain must emit Teardown directive; got {hso!r}"
        )
        assert "PACT:start-pending-scan" not in hso.get("additionalContext", ""), (
            f"Teardown path must NOT emit Arm prose; got {hso!r}"
        )

    def test_drain_consumes_teardown_marker_file(self, tmp_path):
        """The teardown marker file is unlinked from wake_inbox after
        drain. The disk must reflect successful consumption.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-teardown-consumed"
        _write_session_context(home, lead_sid, pdir, team)
        marker_path = _write_marker(
            home, team, "20260516T160400Z-x-T5.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "T5",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792240000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        assert not marker_path.exists(), (
            "Teardown marker must be unlinked after drain"
        )


class TestTeardownDrainEmitsJournalEvent:
    """C4 journal-write: when a teardown marker drains, a
    teardown_request event is written to the journal with tier="2",
    reason="wake_inbox_drained". The event is the falsifiable trace
    that Tier-4 cron staleness can replay from.
    """

    def test_single_teardown_marker_writes_one_event(self, tmp_path):
        """One teardown marker drains -> one teardown_request event in
        the journal with tier="2" reason="wake_inbox_drained".
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-journal-single"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160500Z-x-T6.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "T6",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792300000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)

        # Read the journal for the lead's session.
        slug = Path(pdir).name
        journal = (
            home / ".claude" / "pact-sessions" / slug / lead_sid
            / "session-journal.jsonl"
        )
        assert journal.exists(), "Journal file must be created on drain"
        teardown_events = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
            and json.loads(line).get("type") == "teardown_request"
        ]
        assert len(teardown_events) == 1, (
            f"Single teardown drain must write exactly 1 event; "
            f"got {teardown_events!r}"
        )
        ev = teardown_events[0]
        assert ev.get("tier") == "2", (
            f"Tier-2 emission must carry tier='2'; got tier={ev.get('tier')!r}"
        )
        assert ev.get("reason") == "wake_inbox_drained", (
            f"Tier-2 reason must be 'wake_inbox_drained'; "
            f"got {ev.get('reason')!r}"
        )

    def test_multiple_teardown_markers_same_drain_writes_one_event(
        self, tmp_path,
    ):
        """Stop-sweep secondary firings may produce N markers per
        drain pass (one per re-entrant TaskCompleted fire in a teammate
        session). The journal event is written ONCE per drain to
        avoid N-fold pollution.

        Pins architect refinement Q1 resolution (per teachback
        coordination with backend-coder-1).
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-journal-multi"
        _write_session_context(home, lead_sid, pdir, team)
        for idx, task_id in enumerate(["T7a", "T7b", "T7c"]):
            _write_marker(
                home, team,
                f"20260516T16060{idx}Z-x-{task_id}.json",
                {
                    "schema_version": 1, "type": "teardown",
                    "task_id": task_id,
                    "team_name": team,
                    "owner": "secretary",
                    "timestamp_ms": 1715792400000 + idx,
                    "trigger": "self_complete_exempt_or_stop_sweep",
                },
            )

        _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)

        slug = Path(pdir).name
        journal = (
            home / ".claude" / "pact-sessions" / slug / lead_sid
            / "session-journal.jsonl"
        )
        teardown_events = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
            and json.loads(line).get("type") == "teardown_request"
        ]
        assert len(teardown_events) == 1, (
            f"Multiple teardown markers in same drain must produce "
            f"exactly 1 journal event (ONE-per-drain cardinality per "
            f"architect Q1 resolution); got {len(teardown_events)}: "
            f"{teardown_events!r}"
        )

    def test_event_team_name_matches_drain_team(self, tmp_path):
        """The journal event's team_name matches the lead's team —
        not the team_name field FROM the marker (which could be
        spoofed). Pins the producer-side authority chain.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-journal-team-name"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160700Z-x-T8.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "T8",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792500000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)

        slug = Path(pdir).name
        journal = (
            home / ".claude" / "pact-sessions" / slug / lead_sid
            / "session-journal.jsonl"
        )
        events = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
            and json.loads(line).get("type") == "teardown_request"
        ]
        assert len(events) == 1
        assert events[0].get("team_name") == team


class TestMixedArmTeardownDrain:
    """C4 precedence rule: when a drain consumes BOTH arm and teardown
    markers in the same UserPromptSubmit, teardown takes precedence.
    The teardown reflects the most recent completion-authority state.
    """

    def test_both_marker_types_drained_teardown_takes_precedence(
        self, tmp_path,
    ):
        """Drain has both arm + teardown markers; the emitted directive
        is Teardown (stop-pending-scan), NOT Arm (start-pending-scan).
        The cron should be retired, not armed.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-mixed-precedence"
        _write_session_context(home, lead_sid, pdir, team)
        _write_marker(
            home, team, "20260516T160800Z-x-T9a.json",
            {
                "schema_version": 1, "type": "arm",
                "trigger": "teammate_self_claim_in_progress",
                "tool_name": "TaskUpdate", "task_id": "T9a",
                "owner": "backend-coder",
            },
        )
        _write_marker(
            home, team, "20260516T160801Z-x-T9b.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "T9b",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792500000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None
        ac = hso.get("additionalContext", "")
        assert "PACT:stop-pending-scan" in ac, (
            f"Mixed drain: teardown must take precedence; got {hso!r}"
        )
        # Arm prose must NOT appear in the same output — single-emit
        # discipline preserved.
        assert "PACT:start-pending-scan" not in ac, (
            f"Mixed drain: Arm must NOT also emit; got {hso!r}"
        )

    def test_both_marker_files_consumed(self, tmp_path):
        """Mixed-drain consumes ALL markers, not just the teardown one.
        Leaving arm markers on disk would re-fire on the next prompt
        and confuse the lifecycle.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-c4-mixed-consume"
        _write_session_context(home, lead_sid, pdir, team)
        arm_marker = _write_marker(
            home, team, "20260516T160900Z-x-Aa.json",
            {
                "schema_version": 1, "type": "arm",
                "trigger": "teammate_self_claim_in_progress",
                "tool_name": "TaskUpdate", "task_id": "Aa",
                "owner": "backend-coder",
            },
        )
        teardown_marker = _write_marker(
            home, team, "20260516T160901Z-x-Tb.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "Tb",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792600000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        assert not arm_marker.exists(), "Arm marker must be drained"
        assert not teardown_marker.exists(), "Teardown marker must be drained"


class TestArmTeardownRaceDisambiguation:
    """Same-prompt arm+teardown disambiguation. When BOTH marker kinds
    drain together AND the team still has lifecycle-relevant work on
    disk (count_active_tasks > 0), the teardown marker is STALER than
    the arm and the directive emitted MUST be Arm, not Teardown.

    Concrete race the disambiguation defends against: teammate Y
    completes terminal task Z (writes teardown marker when count was
    0); teammate X then claims a new task A (writes arm marker); lead
    UserPromptSubmit drains both in one pass. Without this guard the
    lead is told to tear down cron while task A is active.

    Counter-test-by-revert: removing the `count_active_tasks(...) > 0`
    check in `_decide_and_emit` flips this test RED (teardown wins
    instead of arm).
    """

    def test_same_prompt_arm_plus_teardown_emits_arm_when_count_nonzero(
        self, tmp_path,
    ):
        """Mixed drain + active task on disk → emit ARM (cron stays
        armed). The stale teardown marker yields to the fresh arm
        marker because the team is still active.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-f2-race-disambiguation"
        teammate_owner = "backend-coder"
        _write_session_context(
            home, lead_sid, pdir, team,
            members=[
                {"name": teammate_owner, "agentId": "agent-bc"},
                {"name": "lead", "agentId": "agent-lead"},
            ],
            lead_agent_id="agent-lead",
        )
        # Active lifecycle-relevant task on disk — the team is NOT idle
        # despite the stale teardown marker.
        _write_task(
            home, team, "A_active",
            status="in_progress", owner=teammate_owner,
        )
        # Stale teardown marker (written when count was 0, before the
        # arm marker landed for the new task).
        teardown_marker = _write_marker(
            home, team, "20260516T160600Z-x-Z_stale.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": "Z_stale",
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792300000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )
        # Fresh arm marker (written when teammate X claimed task
        # A_active).
        arm_marker = _write_marker(
            home, team, "20260516T160700Z-x-A_active.json",
            {
                "schema_version": 1, "type": "arm",
                "trigger": "teammate_self_claim_in_progress",
                "tool_name": "TaskUpdate", "task_id": "A_active",
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
            f"Mixed drain with active tasks must emit a directive; "
            f"got {out!r}"
        )
        ac = hso.get("additionalContext", "")
        assert "PACT:start-pending-scan" in ac, (
            f"Mixed drain + count>0 must emit ARM (cron stays armed); "
            f"got additionalContext={ac!r}"
        )
        # The stale teardown signal must NOT win the dispatch.
        assert "PACT:stop-pending-scan" not in ac, (
            f"Mixed drain + count>0 must NOT emit Teardown — "
            f"the stale teardown marker yields to the fresh arm; "
            f"got additionalContext={ac!r}"
        )
        # Both markers still get consumed (cleanup invariant preserved
        # from the existing mixed-drain test).
        assert not teardown_marker.exists(), (
            "Stale teardown marker must be drained even when arm wins"
        )
        assert not arm_marker.exists(), (
            "Arm marker must be drained on the emit path"
        )


class TestMalformedTeardownMarkerDemotedToArm:
    """Defensive classifier: a marker with `type="teardown"` but no
    valid `task_id` field cannot participate in the Tier-1/Tier-2
    dedup invariant (which keys on task_id). The classifier demotes
    such malformed markers to "arm" so the wake intent still stands
    via the safe fail-conservative default — emit Arm via the existing
    path, not Teardown via a dedup-bypassing fallback.

    Counter-test-by-revert: removing the demote-to-arm branch in
    `_drain_markers` flips this test RED — the marker would then
    count as teardown and trigger _emit_teardown() without going
    through `_already_emitted_teardown`.
    """

    def test_teardown_marker_missing_task_id_emits_arm_not_teardown(
        self, tmp_path,
    ):
        """A solitary `type="teardown"` marker with NO task_id field
        is classified as a wake signal (Arm) — NOT a Teardown — so the
        directive emitted is start-pending-scan, not stop-pending-scan.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid"
        pdir = "/tmp/p"
        team = "team-f3-malformed-teardown"
        _write_session_context(home, lead_sid, pdir, team)
        # Marker says "teardown" but task_id is absent — malformed
        # producer or schema drift.
        malformed = _write_marker(
            home, team, "20260516T161000Z-x-noid.json",
            {
                "schema_version": 1, "type": "teardown",
                # task_id intentionally omitted.
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792700000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Malformed teardown marker must still emit a wake signal; "
            f"got {out!r}"
        )
        ac = hso.get("additionalContext", "")
        # Malformed → demoted to arm → Arm directive emitted.
        assert "PACT:start-pending-scan" in ac, (
            f"Malformed teardown marker must demote to Arm directive; "
            f"got additionalContext={ac!r}"
        )
        # And MUST NOT emit Teardown — that would bypass the
        # Tier-1/Tier-2 dedup invariant.
        assert "PACT:stop-pending-scan" not in ac, (
            f"Malformed teardown marker must NOT emit Teardown "
            f"(dedup invariant bypass); got additionalContext={ac!r}"
        )
        # Marker is consumed regardless of classification.
        assert not malformed.exists(), (
            "Malformed marker must be drained"
        )

    @pytest.mark.parametrize(
        "unsafe_task_id",
        [
            "../foo",          # parent-dir traversal
            "/etc/passwd",     # absolute-path
            "foo/bar",         # embedded separator
            "foo\\bar",        # embedded backslash (Windows-style)
            "foo\x00bar",      # embedded NUL
            ".",               # current-dir literal
            "..",              # parent-dir literal
            " ",               # whitespace-only (post-strip empty would already reject; pre-strip whitespace IS rejected by allowlist)
            "foo bar",         # embedded space
            "foo;rm -rf /",    # shell-metachar payload
        ],
        ids=[
            "parent_traversal", "absolute_path", "embedded_slash",
            "embedded_backslash", "embedded_nul", "dot_literal",
            "dotdot_literal", "whitespace_only", "embedded_space",
            "shell_metachar",
        ],
    )
    def test_teardown_marker_unsafe_task_id_demoted_to_arm(
        self, tmp_path, unsafe_task_id,
    ):
        """Drain-side defense-in-depth: a teardown marker whose task_id
        body field fails `is_safe_path_component` is demoted to arm,
        same as the missing-task_id case. Path-traversal payloads
        (`"../foo"`, `"/etc/passwd"`), embedded separators / NUL bytes,
        and the `.`/`..` literals must NOT reach `_already_emitted_
        teardown`'s `marker_dir / task_id` Path-join.

        The producer-side guard at `wake_lifecycle_emitter._extract_task_id`
        already rejects these at extraction time. This drain-side guard
        is belt-and-suspenders against a direct marker writer
        (buggy/malicious code path bypassing the producer) shipping a
        path-traversal payload to the wake_inbox.
        """
        home = tmp_path / "home"; home.mkdir()
        lead_sid = "lead-sid-unsafe-tid"
        pdir = "/tmp/p"
        team = "team-unsafe-task-id-demote"
        _write_session_context(home, lead_sid, pdir, team)
        malformed = _write_marker(
            home, team, "20260518T000000Z-x-unsafe.json",
            {
                "schema_version": 1, "type": "teardown",
                "task_id": unsafe_task_id,
                "team_name": team,
                "owner": "secretary",
                "timestamp_ms": 1715792700000,
                "trigger": "self_complete_exempt_or_stop_sweep",
            },
        )

        out = _drain_out({
            "session_id": lead_sid, "cwd": pdir,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "go",
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Unsafe-task_id teardown marker must still emit a wake "
            f"signal; got {out!r}"
        )
        ac = hso.get("additionalContext", "")
        assert "PACT:start-pending-scan" in ac, (
            f"Unsafe-task_id teardown marker must demote to Arm "
            f"directive (path-safety guard); got additionalContext={ac!r}"
        )
        assert "PACT:stop-pending-scan" not in ac, (
            f"Unsafe-task_id teardown marker must NOT emit Teardown "
            f"(path-traversal payload must not reach the dedup path-"
            f"join); got additionalContext={ac!r}"
        )
        # Marker is consumed regardless of classification — never leaves
        # the unsafe payload on disk.
        assert not malformed.exists(), (
            "Unsafe-task_id marker must be drained"
        )

        # No sidecar Teardown-emit marker should have been created at
        # the path-traversal target. Verify the team's
        # `.teardown_request_emitted/` directory does not contain a
        # file resolved from the unsafe task_id.
        marker_dir = (
            home / ".claude" / "teams" / team / ".teardown_request_emitted"
        )
        if marker_dir.exists():
            # Any file under marker_dir is acceptable only if its name
            # would not represent a path-traversal materialization. The
            # demote-to-arm path should not create any file here at all,
            # but defense-in-depth check: ensure no file is named after
            # the unsafe payload.
            for child in marker_dir.iterdir():
                assert child.name != unsafe_task_id, (
                    f"Unsafe task_id payload {unsafe_task_id!r} "
                    f"materialized into the Teardown-emit dir as "
                    f"{child!r}; drain-side guard failed"
                )

