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
