"""
Behavioral invariants for pact-plugin/hooks/wake_lifecycle_emitter.py.

Pipes synthesized stdin payloads through the emitter via subprocess and
asserts:
- hookEventName="PostToolUse" present on all directive emits (REQUIRED;
  silent platform rejection without it).
- 0->1 active-task transition emits Arm; 1->0 emits Teardown.
- Non-status TaskUpdate, Task/Agent spawn, and TaskCreate at non-zero
  pre-state are no-ops.
- Fail-open exit-0 + suppressOutput sentinel on malformed stdin /
  missing team_name / unexpected exception.
- hooks.json registers the emitter under PostToolUse with matcher
  TaskCreate|TaskUpdate.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
EMITTER = HOOK_DIR / "wake_lifecycle_emitter.py"
HOOKS_JSON = HOOK_DIR / "hooks.json"


def _run_emitter(stdin_payload: str | bytes, env_extra: dict | None = None) -> tuple[int, str, str]:
    # Start from a clean env so the harness's CLAUDE_PROJECT_DIR doesn't
    # leak into the synthesized context resolution.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(EMITTER)],
        input=stdin_payload if isinstance(stdin_payload, bytes) else stdin_payload.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=10,
    )
    return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")


def _pact_session_env(tmp_path: Path, team_name: str) -> dict:
    """
    Build env vars + on-disk pact-session-context so the emitter's
    pact_context.init() resolves the team_name from the synthesized
    session-context file. The emitter calls pact_context.init(input_data)
    which reads session_id/project_dir from input_data and locates the
    context file under ~/.claude/pact-sessions/<slug>/<session_id>/.
    """
    home = tmp_path / "home"
    home.mkdir()
    return {"HOME": str(home)}


def _write_session_context(home: Path, session_id: str, project_dir: str, team_name: str, *, lead_session_id: str | None = None) -> None:
    # Match the resolution path used by pact_context._resolve_context_path:
    # ~/.claude/pact-sessions/<basename(project_dir)>/<session_id>/pact-session-context.json
    slug = Path(project_dir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team_name,
            "session_id": session_id,
            "project_dir": project_dir,
            "plugin_root": "",
            "started_at": "2026-04-30T00:00:00Z",
        }),
        encoding="utf-8",
    )
    # Team config drives the emitter's _is_lead_session guard. Default
    # behavior: caller's session_id IS the lead (the standard test
    # framing for these tests, which exercise lead-side behavior). Pass
    # `lead_session_id="some-other-id"` to simulate a teammate session.
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = lead_session_id if lead_session_id is not None else session_id
    (team_dir / "config.json").write_text(
        json.dumps({"leadSessionId": effective_lead}),
        encoding="utf-8",
    )


def _write_task(home: Path, team_name: str, task_id: str, **fields) -> None:
    tasks_dir = home / ".claude" / "tasks" / team_name
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------- hooks.json registration ----------

def test_hooks_json_registers_emitter_under_post_tool_use():
    cfg = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    posts = cfg["hooks"]["PostToolUse"]
    found = [
        entry for entry in posts
        if entry.get("matcher") == "TaskCreate|TaskUpdate"
    ]
    assert found, "PostToolUse with required matcher not registered"
    cmds = []
    for entry in found:
        for h in entry.get("hooks", []):
            cmds.append(h.get("command", ""))
    assert any("wake_lifecycle_emitter.py" in c for c in cmds), (
        "wake_lifecycle_emitter.py not wired to the matcher"
    )


# ---------- Fail-open paths ----------

def test_malformed_stdin_exits_zero_with_suppress(tmp_path):
    rc, out, _ = _run_emitter(b"\x00not-json\xff", env_extra=_pact_session_env(tmp_path, "t"))
    assert rc == 0
    assert json.loads(out) == {"suppressOutput": True}


def test_non_dict_stdin_exits_zero_with_suppress(tmp_path):
    rc, out, _ = _run_emitter("[]", env_extra=_pact_session_env(tmp_path, "t"))
    assert rc == 0
    assert json.loads(out) == {"suppressOutput": True}


def test_missing_team_name_exits_zero_with_suppress(tmp_path):
    # No session context file written → get_team_name() returns "".
    payload = json.dumps({
        "tool_name": "TaskCreate",
        "session_id": "abc",
        "cwd": "/tmp/x",
        "tool_input": {"taskId": "1"},
        "tool_response": {"id": "1"},
    })
    rc, out, _ = _run_emitter(payload, env_extra=_pact_session_env(tmp_path, "t"))
    assert rc == 0
    assert json.loads(out) == {"suppressOutput": True}


def test_unrelated_tool_no_op(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "session-1"; pdir = "/tmp/proj"
    _write_session_context(home, sid, pdir, "team-a")
    payload = json.dumps({
        "tool_name": "Read",
        "session_id": sid,
        "cwd": pdir,
        "tool_input": {},
        "tool_response": {},
    })
    rc, out, _ = _run_emitter(payload, env_extra={"HOME": str(home)})
    assert rc == 0
    assert json.loads(out) == {"suppressOutput": True}


def test_task_spawn_tool_no_op(tmp_path):
    """Task and Agent are spawn-tool internal names; they don't change
    active-task count, so they fall through to the no-op path."""
    home = tmp_path / "home"; home.mkdir()
    sid = "session-1"; pdir = "/tmp/proj"
    _write_session_context(home, sid, pdir, "team-a")
    for spawn_tool in ("Task", "Agent"):
        payload = json.dumps({
            "tool_name": spawn_tool,
            "session_id": sid,
            "cwd": pdir,
            "tool_input": {"description": "x"},
            "tool_response": {},
        })
        rc, out, _ = _run_emitter(payload, env_extra={"HOME": str(home)})
        assert rc == 0
        assert json.loads(out) == {"suppressOutput": True}, (
            f"{spawn_tool} should be no-op"
        )


# ---------- Arm directive (0 -> 1) ----------

def _emit_output(payload: dict, home: Path) -> dict:
    rc, out, err = _run_emitter(
        json.dumps(payload),
        env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": payload.get("cwd", "")},
    )
    assert rc == 0, f"non-zero exit; stderr={err}"
    return json.loads(out)


def test_arm_emitted_on_first_task_create(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "session-1"; pdir = "/tmp/proj"; team = "team-a"
    _write_session_context(home, sid, pdir, team)
    # Just-created task is on disk and active.
    _write_task(home, team, "task-1", status="in_progress", owner="backend-coder")
    payload = {
        "tool_name": "TaskCreate",
        "session_id": sid,
        "cwd": pdir,
        "tool_input": {"taskId": "task-1"},
        "tool_response": {"id": "task-1"},
    }
    out = _emit_output(payload, home)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:watch-inbox\")" in hso["additionalContext"]


def test_arm_includes_idempotency_clause(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="pending", owner="x")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1"}, "tool_response": {"id": "1"},
    }, home)
    additional = out["hookSpecificOutput"]["additionalContext"]
    # Case-insensitive — directive prose capitalizes 'Idempotent' but
    # the substring 'idempotent' must still appear somewhere.
    assert "idempotent" in additional.lower()
    # Pin the no-op clause as well — anchors the WHY of idempotency.
    assert "no-op if a valid STATE_FILE" in additional


def test_teardown_includes_best_effort_clause(tmp_path):
    """Symmetric to Arm idempotency: pin the best-effort clause + the
    'tolerates Monitor that died silently' rationale."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="completed", owner="x")
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {"id": "1", "status": "completed"},
    }, home)
    additional = out["hookSpecificOutput"]["additionalContext"]
    assert "best-effort" in additional.lower()
    assert "tolerates a Monitor that died silently" in additional


def test_arm_directive_contains_precondition_phrase(tmp_path):
    """Pin the canonical Arm precondition prose so an editing LLM
    stripping it for terseness loses the directive's WHY-context.
    Symmetric with session_init's pinned 'Active teammate tasks
    detected on session start' phrase."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="pending", owner="x")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1"}, "tool_response": {"id": "1"},
    }, home)
    assert "First active teammate task created" in out["hookSpecificOutput"]["additionalContext"]


def test_teardown_directive_contains_precondition_phrase(tmp_path):
    """Pin the canonical Teardown precondition prose."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="completed", owner="x")
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {"id": "1", "status": "completed"},
    }, home)
    assert "Last active teammate task completed" in out["hookSpecificOutput"]["additionalContext"]


def test_no_op_on_second_active_task_create(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    # Pre-existing active task + just-created task → 1->2 transition.
    _write_task(home, team, "existing", status="in_progress", owner="x")
    _write_task(home, team, "new", status="in_progress", owner="y")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "new"}, "tool_response": {"id": "new"},
    }, home)
    assert out == {"suppressOutput": True}


def test_no_op_on_create_of_signal_task(tmp_path):
    """Signal-tasks don't count toward lifecycle-relevant tally."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(
        home, team, "sig-1",
        status="in_progress",
        owner="x",
        metadata={"completion_type": "signal", "type": "blocker"},
    )
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "sig-1"}, "tool_response": {"id": "sig-1"},
    }, home)
    assert out == {"suppressOutput": True}


def test_no_op_on_create_owned_by_exempt_agent(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "sec-1", status="in_progress", owner="secretary")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "sec-1"}, "tool_response": {"id": "sec-1"},
    }, home)
    assert out == {"suppressOutput": True}


# ---------- Teardown directive (1 -> 0) ----------

def test_teardown_emitted_on_last_active_completion(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    # Task on disk is now completed (post-state); pre-state was active.
    _write_task(home, team, "1", status="completed", owner="x")
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {"id": "1", "status": "completed"},
    }, home)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:unwatch-inbox\")" in hso["additionalContext"]


def test_no_teardown_when_other_active_remains(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="completed", owner="x")
    _write_task(home, team, "2", status="in_progress", owner="y")
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1", "status": "completed"},
        "tool_response": {"id": "1", "status": "completed"},
    }, home)
    assert out == {"suppressOutput": True}


def test_no_teardown_on_non_status_taskupdate(tmp_path):
    """TaskUpdate that changes only owner/metadata/etc. must not Teardown."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="in_progress", owner="x")
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1", "owner": "y"},
        "tool_response": {"id": "1"},
    }, home)
    assert out == {"suppressOutput": True}


def test_teardown_emits_on_signal_task_completion_at_post_zero(tmp_path):
    """A1 simplification (see emitter docstring): post-only transition
    detector emits Teardown on any status=completed TaskUpdate when
    post==0, including the signal-task completion case where the task
    never contributed to the active count. Skill's Teardown is
    idempotent (no-op if STATE_FILE absent), so this over-eager emit
    is benign by design — replaces the prior hypothetical_pre filter."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(
        home, team, "sig",
        status="completed",
        owner="x",
        metadata={"completion_type": "signal", "type": "algedonic"},
    )
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "sig", "status": "completed"},
        "tool_response": {"id": "sig", "status": "completed"},
    }, home)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:unwatch-inbox\")" in hso["additionalContext"]


# ---------- _decide_directive direct unit coverage ----------

def test_decide_directive_module_importable():
    """Import the emitter directly and exercise _decide_directive
    with synthetic inputs to lock its transition table without
    subprocess overhead."""
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    # Unrelated tool → None
    assert emitter._decide_directive({"tool_name": "Read"}, "team") is None

    # TaskCreate without task id → None
    assert emitter._decide_directive(
        {"tool_name": "TaskCreate", "tool_input": {}, "tool_response": {}},
        "team",
    ) is None


# ---------- Terminal-status detection covers deleted (be-F2) ----------

def test_teardown_emitted_on_status_deleted_at_post_zero(tmp_path):
    """Both terminal statuses — `completed` and `deleted` — must trigger
    Teardown when the team's active count drops to zero. A status=deleted
    transition is structurally equivalent to status=completed for the
    wake-lifecycle (the task is no longer active work). Without this,
    a deleted-task TaskUpdate at post-zero leaves a phantom Monitor
    armed against an inbox no one is watching."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    # Task on disk is deleted (post-state); pre-state was active.
    _write_task(home, team, "1", status="deleted", owner="x")
    out = _emit_output({
        "tool_name": "TaskUpdate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1", "status": "deleted"},
        "tool_response": {"id": "1", "status": "deleted"},
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        "Expected Teardown directive on status=deleted at post-zero "
        f"(be-F2). Actual emit: {out!r}"
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:unwatch-inbox\")" in hso["additionalContext"]


def test_is_terminal_status_update_matches_completed_and_deleted(tmp_path):
    """Direct unit test on the terminal-status predicate. The behavioral
    contract is "task transitioned to a terminal status" — both
    `completed` and `deleted` are terminal."""
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter
    assert emitter._is_terminal_status_update({
        "tool_input": {"status": "deleted"},
        "tool_response": {},
    }) is True
    assert emitter._is_terminal_status_update({
        "tool_input": {"status": "completed"},
        "tool_response": {},
    }) is True
    # Sanity: an unrelated status remains False.
    assert emitter._is_terminal_status_update({
        "tool_input": {"status": "in_progress"},
        "tool_response": {},
    }) is False


# ---------- Lead-session guard (sec-M5 / te-MED-1) ----------

def test_emitter_guards_on_lead_session_id_structural():
    """Source-level structural pin: the emitter must call
    `_is_lead_session` (or equivalent guard against
    team_config.leadSessionId) before any directive emit. Without this
    structural anchor, the emitter would fire Arm/Teardown directives
    in teammate sessions (where they're inert at best, attacker-
    weaponizable at worst — a teammate session that arms a Monitor
    would watch the lead's inbox file from the wrong process)."""
    src = (HOOK_DIR / "wake_lifecycle_emitter.py").read_text(encoding="utf-8")
    assert "_is_lead_session" in src
    assert "leadSessionId" in src


def test_no_emit_when_session_id_does_not_match_lead(tmp_path):
    """Behavioral pin: a teammate-session TaskCreate that would
    otherwise emit Arm must be suppressed by the lead-session guard.
    Synthesize a session_id distinct from the team config's
    leadSessionId; verify the emit is suppressOutput."""
    home = tmp_path / "home"; home.mkdir()
    teammate_sid = "teammate-session-id"
    lead_sid = "lead-session-id"
    pdir = "/tmp/proj"
    team = "team-guard"
    # Session context: caller is the teammate; team config: lead is OTHER.
    _write_session_context(home, teammate_sid, pdir, team, lead_session_id=lead_sid)
    _write_task(home, team, "task-x", status="in_progress", owner="x")
    payload = {
        "tool_name": "TaskCreate",
        "session_id": teammate_sid,
        "cwd": pdir,
        "tool_input": {"taskId": "task-x"},
        "tool_response": {"id": "task-x"},
    }
    out = _emit_output(payload, home)
    assert out == {"suppressOutput": True}, (
        f"Teammate-session TaskCreate must be suppressed; got {out!r}"
    )


def test_no_emit_when_team_config_missing(tmp_path):
    """If team config.json is missing, _is_lead_session fail-closes —
    no emit. Documenting the fail-closed behavior so an editing LLM
    cannot 'simplify' the guard into fail-open during refactor."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "team-no-config"
    # Write only the session-context, NOT the team config.
    slug = Path(pdir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / sid
    sess_dir.mkdir(parents=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team, "session_id": sid, "project_dir": pdir,
            "plugin_root": "", "started_at": "2026-04-30T00:00:00Z",
        }), encoding="utf-8",
    )
    _write_task(home, team, "task-x", status="in_progress", owner="x")
    payload = {
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "task-x"}, "tool_response": {"id": "task-x"},
    }
    out = _emit_output(payload, home)
    assert out == {"suppressOutput": True}


# ---------- Perf reorder (arch2-M2) ----------

def test_count_active_tasks_not_called_on_metadata_only_taskupdate():
    """Performance invariant: count_active_tasks (filesystem glob+parse)
    must NOT run on a metadata-only TaskUpdate (no status change). The
    count is gated behind the cheap _is_terminal_status_update check
    so the typical task lifecycle (lots of metadata writes
    teachback_submit, intentional_wait, handoff, progress, memory_saved
    + a single status=completed transition) doesn't pay an O(N) team
    scan per metadata write."""
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    # Patch the imported reference at the call site (NOT shared.wake_lifecycle).
    # `from shared.wake_lifecycle import count_active_tasks` binds the name
    # `count_active_tasks` into emitter's module globals, so patching the
    # source module reference would miss this binding (phantom-green trap).
    from unittest.mock import patch
    with patch.object(emitter, "count_active_tasks") as mock_count:
        # Metadata-only TaskUpdate: no status field, just owner change.
        result = emitter._decide_directive({
            "tool_name": "TaskUpdate",
            "session_id": "sid", "cwd": "/tmp/p",
            "tool_input": {"taskId": "1", "owner": "y"},
            "tool_response": {"id": "1"},
        }, "team-x")
        # Without a lead-session guard pass, _decide_directive returns
        # early; we want to specifically exercise the post-tool-name +
        # post-task-id + non-terminal path. Bypass _is_lead_session by
        # patching it to True for this perf-invariant probe.
        # (The lead-session guard's correctness is covered separately
        # above; here we isolate the count_active_tasks ordering.)

    # Re-run with lead-guard bypassed to isolate the perf ordering invariant.
    with patch.object(emitter, "_is_lead_session", return_value=True), \
         patch.object(emitter, "count_active_tasks") as mock_count:
        result = emitter._decide_directive({
            "tool_name": "TaskUpdate",
            "session_id": "sid", "cwd": "/tmp/p",
            "tool_input": {"taskId": "1", "owner": "y"},
            "tool_response": {"id": "1"},
        }, "team-x")
        assert result is None
        assert mock_count.call_count == 0, (
            f"count_active_tasks should NOT run on metadata-only TaskUpdate; "
            f"called {mock_count.call_count} time(s)"
        )


def test_count_active_tasks_called_on_terminal_status_taskupdate():
    """Sanity-paired with the perf invariant: terminal-status
    TaskUpdates DO call count_active_tasks (otherwise the gate would be
    completely bypassed and Teardown would never fire)."""
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    from unittest.mock import patch
    with patch.object(emitter, "_is_lead_session", return_value=True), \
         patch.object(emitter, "count_active_tasks", return_value=0) as mock_count:
        result = emitter._decide_directive({
            "tool_name": "TaskUpdate",
            "session_id": "sid", "cwd": "/tmp/p",
            "tool_input": {"taskId": "1", "status": "completed"},
            "tool_response": {"id": "1", "status": "completed"},
        }, "team-x")
        assert mock_count.call_count >= 1, (
            "Expected count_active_tasks to run on terminal-status TaskUpdate"
        )


def test_count_active_tasks_called_on_taskcreate():
    """TaskCreate path also requires the count (to detect 0->1
    transition). Mirror of the terminal-status sanity test."""
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    from unittest.mock import patch
    with patch.object(emitter, "_is_lead_session", return_value=True), \
         patch.object(emitter, "_extract_task_id", return_value="1"), \
         patch.object(emitter, "count_active_tasks", return_value=1) as mock_count:
        result = emitter._decide_directive({
            "tool_name": "TaskCreate",
            "session_id": "sid", "cwd": "/tmp/p",
            "tool_input": {"taskId": "1"},
            "tool_response": {"id": "1"},
        }, "team-x")
        assert mock_count.call_count >= 1
