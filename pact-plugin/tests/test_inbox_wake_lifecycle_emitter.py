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
  TaskCreate|TaskUpdate|Task|Agent.
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


def _write_session_context(home: Path, session_id: str, project_dir: str, team_name: str) -> None:
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
        if entry.get("matcher") == "TaskCreate|TaskUpdate|Task|Agent"
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
    assert "Skill(\"PACT:inbox-wake\")" in hso["additionalContext"]
    assert "Arm" in hso["additionalContext"]


def test_arm_includes_idempotency_clause(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="pending", owner="x")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1"}, "tool_response": {"id": "1"},
    }, home)
    assert "idempotent" in out["hookSpecificOutput"]["additionalContext"]


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
    assert "Teardown" in hso["additionalContext"]
    assert "Skill(\"PACT:inbox-wake\")" in hso["additionalContext"]


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


def test_no_teardown_on_completion_of_signal_task(tmp_path):
    """Completing a signal-task that never counted does not change tally."""
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
    assert out == {"suppressOutput": True}


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
