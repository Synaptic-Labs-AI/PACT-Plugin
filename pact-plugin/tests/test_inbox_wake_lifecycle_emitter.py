"""
Behavioral invariants for pact-plugin/hooks/wake_lifecycle_emitter.py.

Pipes synthesized stdin payloads through the emitter via subprocess and
asserts:
- hookEventName="PostToolUse" present on all directive emits (REQUIRED;
  silent platform rejection without it).
- TaskCreate with post-count >= 1 emits Arm; 1->0 TaskUpdate emits
  Teardown. start-pending-scan's CronList idempotency absorbs
  redundant Arm emits on subsequent TaskCreates within the same
  active window.
- Non-status TaskUpdate, Task/Agent spawn, and TaskCreate at zero
  post-count are no-ops.
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


def _pact_session_env(tmp_path: Path) -> dict:
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


def _write_session_context(
    home: Path,
    session_id: str,
    project_dir: str,
    team_name: str,
    *,
    lead_session_id: str | None = None,
    members: list[dict] | None = None,
) -> None:
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
    # `members` defaults to [] (empty team config); pass a list of
    # {"name", "agentType"} dicts to exercise the team-config agentType
    # carve-out resolved by shared.intentional_wait._is_exempt_agent_type.
    team_dir = home / ".claude" / "teams" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    effective_lead = lead_session_id if lead_session_id is not None else session_id
    config_data: dict = {"leadSessionId": effective_lead}
    if members:
        config_data["members"] = list(members)
    (team_dir / "config.json").write_text(
        json.dumps(config_data),
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
    rc, out, _ = _run_emitter(b"\x00not-json\xff", env_extra=_pact_session_env(tmp_path))
    assert rc == 0
    assert json.loads(out) == {"suppressOutput": True}


def test_non_dict_stdin_exits_zero_with_suppress(tmp_path):
    rc, out, _ = _run_emitter("[]", env_extra=_pact_session_env(tmp_path))
    assert rc == 0
    assert json.loads(out) == {"suppressOutput": True}


def test_missing_team_name_exits_zero_with_suppress(tmp_path):
    # No session context file written → get_team_name() returns "".
    payload = json.dumps({
        "tool_name": "TaskCreate",
        "session_id": "abc",
        "cwd": "/tmp/x",
        "tool_input": {"taskId": "1"},
        "tool_response": {"task": {"id": "1"}},
    })
    rc, out, _ = _run_emitter(payload, env_extra=_pact_session_env(tmp_path))
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
        "tool_response": {"task": {"id": "task-1"}},
    }
    out = _emit_output(payload, home)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]


def test_arm_includes_idempotency_clause(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "1", status="pending", owner="x")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "1"}, "tool_response": {"task": {"id": "1"}},
    }, home)
    additional = out["hookSpecificOutput"]["additionalContext"]
    # Case-insensitive — directive prose capitalizes 'Idempotent' but
    # the substring 'idempotent' must still appear somewhere.
    assert "idempotent" in additional.lower()
    # Pin the no-op clause as well — anchors the WHY of idempotency.
    # Under cron mechanism, idempotency is enforced by start-pending-scan's
    # CronList match (the cron entry's existence is the armed-state bit).
    assert "no-op" in additional.lower()
    assert "cron" in additional.lower()


def test_teardown_includes_best_effort_clause(tmp_path):
    """Symmetric to Arm idempotency: pin the best-effort clause + the
    tolerance rationale for missing-cron-entry scenarios."""
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
    # Cron teardown tolerates a missing cron entry (stop-pending-scan
    # is idempotent / ignore-if-absent per INV-5).
    assert "tolerat" in additional.lower()


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
        "tool_input": {"taskId": "1"}, "tool_response": {"task": {"id": "1"}},
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
        "tool_input": {"taskId": "sig-1"}, "tool_response": {"task": {"id": "sig-1"}},
    }, home)
    assert out == {"suppressOutput": True}


def test_arm_on_create_owned_by_secretary_post_empty_carve_out(tmp_path):
    """POST-EMPTY-CARVE-OUT: a secretary-owned TaskCreate now triggers
    Arm because WAKE_EXCLUDED_AGENT_TYPES is empty and secretary tasks
    count toward the active tally.

    Pre-empty: this test asserted suppressOutput (the wake-side carve-
    out filtered secretary tasks before count_active_tasks reached the
    Arm threshold). Post-empty: secretary tasks DO count, so creating
    one triggers the Arm directive. This is desired behavior — it's
    exactly what enables the Bug A secretary-window fix at the count
    gate (the cron scan stays armed for the duration of secretary
    work, preventing the eager 1→0 Teardown).

    SELF_COMPLETE_EXEMPT_AGENT_TYPES on the self-completion side still
    contains pact-secretary (self-completion authority preserved); only
    the wake-side carve-out is empty."""
    home = tmp_path / "home"; home.mkdir()
    sid = "s"; pdir = "/tmp/p"; team = "t"
    # Team config records session-secretary with the privileged agentType.
    # Post-empty-carve-out: the agentType still resolves via team-config
    # lookup, but WAKE_EXCLUDED_AGENT_TYPES is empty so no membership match.
    _write_session_context(
        home, sid, pdir, team,
        members=[{"name": "session-secretary", "agentType": "pact-secretary"}],
    )
    _write_task(home, team, "sec-1", status="in_progress", owner="session-secretary")
    out = _emit_output({
        "tool_name": "TaskCreate", "session_id": sid, "cwd": pdir,
        "tool_input": {"taskId": "sec-1"}, "tool_response": {"task": {"id": "sec-1"}},
    }, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Post-empty WAKE_EXCLUDED_AGENT_TYPES: secretary TaskCreate must "
        f"emit Arm directive (count_active_tasks >= 1). "
        f"Got {out!r}. If suppressOutput, the wake-side carve-out has been "
        f"re-populated and this test must be inverted in lockstep."
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]


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
    assert "Skill(\"PACT:stop-pending-scan\")" in hso["additionalContext"]


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
    never contributed to the active count. stop-pending-scan is
    idempotent (no-op if cron entry absent), so this over-eager emit
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
    assert "Skill(\"PACT:stop-pending-scan\")" in hso["additionalContext"]


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
    assert "Skill(\"PACT:stop-pending-scan\")" in hso["additionalContext"]


def test_is_terminal_status_update_matches_completed_and_deleted():
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
    """Source-level structural pin (commit-9 tightened from substring-
    anywhere to regex-in-code-line): the emitter must CALL `_is_lead_session`
    (or equivalent guard against team_config.leadSessionId) before any
    directive emit. Without this structural anchor, the emitter would
    fire Arm/Teardown directives in teammate sessions (where they're
    inert at best, attacker-weaponizable at worst — a teammate session
    that arms a cron scan would scan the lead's task store from the wrong
    process).

    Tightening: require the guard symbol to appear within a control-flow
    construct (if/return/elif/while/assert), NOT just anywhere in source.
    A hostile edit removing the actual guard call but leaving a docstring
    mention would pass the prior substring check; the regex-in-code-line
    check catches the wiring-disconnect."""
    import re as _re
    src = (HOOK_DIR / "wake_lifecycle_emitter.py").read_text(encoding="utf-8")
    code_line_pattern = _re.compile(
        r"^\s*(if|return|elif|while|assert)\b.*(_is_lead_session|leadSessionId)",
        _re.MULTILINE,
    )
    assert code_line_pattern.search(src) is not None, (
        "INV-12 Layer 0 strict: wake_lifecycle_emitter.py missing guard "
        "CALL within control-flow construct (if/return/elif/while/assert). "
        "The prior substring check passed on docstring mentions; "
        "the strict check requires the guard to appear in actual code."
    )


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
        "tool_response": {"task": {"id": "task-x"}},
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
        "tool_input": {"taskId": "task-x"}, "tool_response": {"task": {"id": "task-x"}},
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
        emitter._decide_directive({
            "tool_name": "TaskUpdate",
            "session_id": "sid", "cwd": "/tmp/p",
            "tool_input": {"taskId": "1", "status": "completed"},
            "tool_response": {"id": "1", "status": "completed"},
        }, "team-x")
        assert mock_count.call_count >= 1, (
            "Expected count_active_tasks to run on terminal-status TaskUpdate"
        )


def test_count_active_tasks_called_on_taskcreate():
    """TaskCreate path also requires the count (positive-bound Arm
    predicate). Mirror of the terminal-status sanity test."""
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter

    from unittest.mock import patch
    with patch.object(emitter, "_is_lead_session", return_value=True), \
         patch.object(emitter, "_extract_task_id", return_value="1"), \
         patch.object(emitter, "count_active_tasks", return_value=1) as mock_count:
        emitter._decide_directive({
            "tool_name": "TaskCreate",
            "session_id": "sid", "cwd": "/tmp/p",
            "tool_input": {"taskId": "1"},
            "tool_response": {"task": {"id": "1"}},
        }, "team-x")
        assert mock_count.call_count >= 1


# ---------- Stdin payload size limit (sec-F1) ----------

def test_oversized_stdin_payload_fails_open_with_suppress(tmp_path):
    """Defense-in-depth size cap (sec-F1): a stdin payload exceeding
    _MAX_PAYLOAD_BYTES must be rejected with suppressOutput / exit 0,
    NOT parsed and not OOM. Synthesize a 2MB payload to comfortably
    exceed any reasonable 1MB threshold; assert fail-open behavior.

    The setup matches the WOULD-BE-ARM case (valid team_name, valid
    session context, would-be 0->1 transition) so the suppress signal
    can ONLY come from the size cap, not from any downstream guard like
    missing team_name or non-lead session_id.

    Counter-test-by-revert: removing the bounded read + size guard at
    main() entry produces an Arm directive instead of suppressOutput
    (the underlying path would otherwise emit Arm)."""
    home = tmp_path / "home"; home.mkdir()
    sid = "session-cap"; pdir = "/tmp/proj-cap"; team = "team-cap"
    # Set up a valid lead-session context + active task on disk so the
    # downstream path would emit Arm if the payload were processed.
    _write_session_context(home, sid, pdir, team)
    _write_task(home, team, "task-cap", status="in_progress", owner="x")
    # Build a payload structurally valid but bloated — 2MB of filler
    # padding. The structural shape is JSON so a no-cap path would
    # attempt a full parse and (with valid context) emit Arm.
    filler = "A" * (2 * 1024 * 1024)
    payload_dict = {
        "tool_name": "TaskCreate",
        "session_id": sid,
        "cwd": pdir,
        "tool_input": {"taskId": "task-cap", "filler": filler},
        "tool_response": {"task": {"id": "task-cap"}},
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    assert len(payload_bytes) > 1024 * 1024, (
        "Test setup: payload must exceed 1MB cap"
    )
    rc, out, _ = _run_emitter(payload_bytes, env_extra={"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir})
    assert rc == 0, "Expected fail-open exit-0 on oversized payload"
    parsed = json.loads(out)
    # Discriminator: with the cap in place, suppressOutput. Without the
    # cap, the would-be-Arm path emits hookSpecificOutput.additionalContext.
    assert parsed == {"suppressOutput": True}, (
        f"Expected size-cap rejection (suppressOutput), got {parsed!r}. "
        f"If parsed has hookSpecificOutput, the cap was bypassed and the "
        f"would-be-Arm path emitted instead."
    )


def test_emitter_documents_payload_size_cap_constant():
    """Source-level structural pin: the size cap constant must exist
    as a module-global so an editing LLM cannot 'simplify' the bounded
    read into an unbounded read by removing the cap inline."""
    src = (HOOK_DIR / "wake_lifecycle_emitter.py").read_text(encoding="utf-8")
    assert "_MAX_PAYLOAD_BYTES" in src
    # Cap must be a reasonable size (between 64KB and 16MB). Pin a
    # range rather than a literal so a future tune (e.g., 2MB) doesn't
    # require updating this test.
    sys.path.insert(0, str(HOOK_DIR))
    import wake_lifecycle_emitter as emitter
    assert isinstance(emitter._MAX_PAYLOAD_BYTES, int)
    assert 64 * 1024 <= emitter._MAX_PAYLOAD_BYTES <= 16 * 1024 * 1024


# ---------- Shape-resilience for _extract_task_id (#620) ----------

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "wake_lifecycle"

_REQUIRED_META_FIELDS = {
    "capture_session_id",
    "capture_date",
    "capture_method",
    "issue_ref",
}

# Enum of permitted capture_method values. `logging-shim` /
# `manual-stdin-redirect` are lossless captures; `synthesized` is
# hand-reconstructed and may be lossy (notes MUST disclose the source
# fixture); `legacy` is hand-constructed for backward-compat backstops
# only. README at fixtures/wake_lifecycle/README.md is the SSOT.
_PERMITTED_CAPTURE_METHODS = frozenset({
    "logging-shim",
    "manual-stdin-redirect",
    "synthesized",
    "legacy",
})


def test_all_wake_lifecycle_fixtures_carry_meta_provenance():
    """Convention enforcement for `tests/fixtures/wake_lifecycle/`: every
    JSON fixture MUST have a top-level `_meta` dict carrying the four
    required provenance fields. The README in that directory documents
    the convention; this test enforces it so a future contributor cannot
    silently add an un-provenanced fixture and weaken the structural
    defense against the #612-class shape-divergence regression.

    To extend this enforcement to a sibling fixture subdirectory (e.g.,
    a future `tests/fixtures/peer_inject/`), add a new test function
    that points at the new dir, or refactor this function to parametrize
    over a list of provenance-required fixture roots.
    """
    fixture_paths = sorted(FIXTURES_DIR.glob("*.json"))
    assert fixture_paths, (
        f"No JSON fixtures found in {FIXTURES_DIR}; convention is moot "
        f"if the directory is empty — verify the test is pointed at the "
        f"right path."
    )
    for fixture_path in fixture_paths:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert "_meta" in data, (
            f"{fixture_path.name}: missing top-level `_meta` sibling key. "
            f"See {FIXTURES_DIR.name}/README.md for the convention."
        )
        meta = data["_meta"]
        assert isinstance(meta, dict), (
            f"{fixture_path.name}: `_meta` must be a dict, got {type(meta).__name__}"
        )
        missing = _REQUIRED_META_FIELDS - set(meta.keys())
        assert not missing, (
            f"{fixture_path.name}: `_meta` missing required fields: {missing}. "
            f"Required: {_REQUIRED_META_FIELDS}."
        )
        method = meta["capture_method"]
        assert method in _PERMITTED_CAPTURE_METHODS, (
            f"{fixture_path.name}: capture_method={method!r} is not in "
            f"the permitted enum {_PERMITTED_CAPTURE_METHODS}. See "
            f"{FIXTURES_DIR.name}/README.md for the convention."
        )


class TestExtractTaskIdShapeResilience:
    """Pin _extract_task_id behavior across every shape it must handle.

    Production `TaskCreate` `tool_response` is **nested**
    (`tool_response.task.id`) per #612's logging-shim capture; production
    `TaskUpdate` `tool_response` is **flat** (`tool_response.id`). The
    regression in #620 was that the function only probed the flat shape,
    so every TaskCreate returned None and the auto-Arm path was dead.

    This class fossilizes the precedence + shape-resilience contract.
    Test #1 is the counter-test-by-revert for the #620 fix: reverting
    the nested-task probe makes it fail.
    """

    @staticmethod
    def _extract(input_data):
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        return emitter._extract_task_id(input_data)

    def test_taskcreate_production_nested_task_shape(self):
        """The #620 regression test. Pipes the production TaskCreate
        shape (`tool_response.task.id`) and asserts the id is extracted.
        Counter-test-by-revert: revert the nested-task probe and this
        fails — the function returns None, replicating the bug."""
        result = self._extract({"tool_response": {"task": {"id": "5"}}})
        assert result == "5"

    def test_taskupdate_production_flat_shape(self):
        """Fossilizes the working TaskUpdate shape. The flat fallback
        must keep working alongside the new nested probe."""
        result = self._extract({"tool_response": {"id": "5"}})
        assert result == "5"

    def test_tool_input_taskid_priority(self):
        """When both `tool_input.taskId` and a tool_response id are
        present, `tool_input` wins. Pins the precedence so a future
        reorder breaks this test rather than silently inverting."""
        result = self._extract({
            "tool_input": {"taskId": "from-input"},
            "tool_response": {"task": {"id": "from-response"}},
        })
        assert result == "from-input"

    def test_unknown_shape_returns_none(self):
        """Fail-open on unknown shape: an unrecognized `tool_response`
        sub-key returns None, allowing the caller to suppressOutput
        cleanly without crashing."""
        result = self._extract(
            {"tool_response": {"unexpected_key": {"id": "lost"}}}
        )
        assert result is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"tool_input": {}, "tool_response": {}},
            {},
        ],
        ids=["both-empty-dicts", "fully-empty-input"],
    )
    def test_empty_dicts_return_none(self, payload):
        """No id anywhere → None. Covers both the empty-sub-dicts and
        the fully-empty-input shapes."""
        assert self._extract(payload) is None

    @pytest.mark.parametrize(
        "bad_id",
        [5, None, ["x"], {"nested": "value"}, True],
        ids=["int", "none", "list", "dict", "bool"],
    )
    def test_non_string_id_returns_none(self, bad_id):
        """Only string ids are accepted. Pins the type discipline so
        a future relaxation (e.g., `str(tid)` coercion) breaks loudly.

        Probes both the nested and the flat path so a non-string id
        in either position is rejected."""
        # Nested path
        assert self._extract({"tool_response": {"task": {"id": bad_id}}}) is None
        # Flat path
        assert self._extract({"tool_response": {"id": bad_id}}) is None

    @pytest.mark.parametrize(
        "whitespace_id",
        ["   ", "\t", "\n", " \t\n ", " "],
        ids=["spaces", "tab", "newline", "mixed-whitespace", "nbsp"],
    )
    def test_whitespace_only_id_returns_none(self, whitespace_id):
        """Adversarial: a whitespace-only id is a string and truthy
        (passes `isinstance(tid, str) and tid`), but downstream
        `count_active_tasks` would silently fail to find a task by
        whitespace id — masking the real failure mode. The hook's
        `.strip()` handling rejects whitespace-only ids upfront so the
        function returns None and `_decide_directive` exits cleanly.

        Counter-test-by-revert: removing the `.strip()` handling makes
        this test fail (the function returns the whitespace string).
        Probes both the nested and the flat path so the discipline
        applies symmetrically.
        """
        # Nested path
        assert self._extract({"tool_response": {"task": {"id": whitespace_id}}}) is None
        # Flat path
        assert self._extract({"tool_response": {"id": whitespace_id}}) is None


def test_arm_emitted_on_captured_production_taskcreate_payload(tmp_path):
    """End-to-end #620 regression: pipe the captured production
    TaskCreate stdin (from `fixtures/wake_lifecycle/task_create_production_shape.json`)
    through the full hook entry-point and assert an Arm directive is
    emitted. Counter-test-by-revert: revert the nested-task probe and
    `_extract_task_id` returns None on this payload → the
    `if not _extract_task_id(...)` guard exits → no Arm emit → this
    test fails. The hand-crafted unit test
    `test_taskcreate_production_nested_task_shape` covers the same
    failure mode at the function level; this test additionally
    exercises the full subprocess pipe so a regression in the hook's
    main() wiring (e.g., re-introducing a flat-only probe somewhere
    downstream) is also caught."""
    fixture = json.loads(
        (FIXTURES_DIR / "task_create_production_shape.json").read_text(encoding="utf-8")
    )
    # Strip the diagnostic _meta sibling; the hook would tolerate it,
    # but pipe a clean payload to mirror what the platform actually
    # sends.
    fixture.pop("_meta", None)

    home = tmp_path / "home"; home.mkdir()
    sid = fixture["session_id"]
    pdir = fixture["cwd"]
    team = "team-prod"
    _write_session_context(home, sid, pdir, team)
    task_id = fixture["tool_response"]["task"]["id"]
    _write_task(home, team, task_id, status="pending", owner="backend-coder")

    out = _emit_output(fixture, home)
    hso = out.get("hookSpecificOutput")
    assert hso is not None, (
        f"Expected Arm directive on captured production TaskCreate; "
        f"got {out!r}. If `out == {{'suppressOutput': True}}`, the "
        f"nested-task probe in _extract_task_id is missing — see #620."
    )
    assert hso["hookEventName"] == "PostToolUse"
    assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]


# ---------- Arm directive on parallel-TaskCreate race (#637) ----------


class TestArmDirectiveOnParallelTaskCreateRace:
    """Pin the positive-bound TaskCreate Arm threshold against the
    parallel-batch race.

    A parallel TaskCreate batch lands all task files before either
    PostToolUse hook reads `count_active_tasks`. Under a strict-equality
    predicate (`count == 1`) every fire rejects because the post-state
    count is already N >= 2; the Arm directive never emits and the
    pending-scan cron entry is never created. Under the positive-bound
    predicate (`count >= 1`) every fire emits Arm and start-pending-scan's
    CronList idempotency (no-op when a matching cron entry is already
    on disk) absorbs the redundant emits within the active window.

    The two race-coupled tests below are the counter-test-by-revert
    targets: revert pact-plugin/hooks/wake_lifecycle_emitter.py to the
    strict-equality form and they fail; the four no-regression tests
    still pass. Cardinality {2 fail, 4 pass} is the falsifiable signature
    that the regression is actually exercised.

    Fixture provenance for the parallel-burst shape lives in the
    `_meta` blocks of `task_create_parallel_burst_first.json` and
    `task_create_parallel_burst_second.json` — that's the right surface
    for the issue cite per the no-issue-refs-in-test-names axiom.
    """

    @staticmethod
    def _load_fixture(name: str) -> dict:
        data = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
        data.pop("_meta", None)
        return data

    def _setup_session(self, tmp_path: Path, fixture: dict, team: str = "team-race"):
        home = tmp_path / "home"
        home.mkdir()
        sid = fixture["session_id"]
        pdir = fixture["cwd"]
        _write_session_context(home, sid, pdir, team)
        return home, team

    def test_arm_emits_on_parallel_burst_first_fire(self, tmp_path):
        """Race-coupled test #1. Pre-write tasks 10 and 11 (post-batch
        filesystem state); pipe the FIRST burst fixture through the hook.
        Strict-equality reverts FAIL here (count_active_tasks == 2);
        positive-bound passes."""
        fixture = self._load_fixture("task_create_parallel_burst_first.json")
        home, team = self._setup_session(tmp_path, fixture)
        _write_task(home, team, "10", status="pending", owner="backend-coder")
        _write_task(home, team, "11", status="pending", owner="test-engineer")

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm directive on parallel-burst first fire; "
            f"got {out!r}. If `out == {{'suppressOutput': True}}` the "
            f"TaskCreate threshold is strict-equality (== 1) and rejects "
            f"on the post-batch count of 2 — see #637."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]

    def test_arm_emits_on_parallel_burst_second_fire(self, tmp_path):
        """Race-coupled test #2. Same post-batch filesystem state as
        burst-first; pipe the SECOND burst fixture. The contract is
        'both fires must emit; start-pending-scan CronList idempotency
        handles the redundant emit at the skill layer.' Strict-equality reverts FAIL
        here for the same reason as burst-first."""
        fixture = self._load_fixture("task_create_parallel_burst_second.json")
        home, team = self._setup_session(tmp_path, fixture)
        _write_task(home, team, "10", status="pending", owner="backend-coder")
        _write_task(home, team, "11", status="pending", owner="test-engineer")

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm directive on parallel-burst second fire; "
            f"got {out!r}."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]

    def test_arm_emits_on_sequential_first_create(self, tmp_path):
        """No-regression sentinel for the original sequential 0->1 path.
        Only task 10 is pre-written; count_active_tasks returns 1.
        Passes under both strict-equality (== 1) and positive-bound
        (>= 1) predicates. Would FAIL only if a future edit broke the
        positive-bound path entirely (e.g., a typo flipping to `> 1`)."""
        fixture = self._load_fixture("task_create_parallel_burst_first.json")
        home, team = self._setup_session(tmp_path, fixture)
        _write_task(home, team, "10", status="pending", owner="backend-coder")

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm directive on sequential 0->1 TaskCreate; "
            f"got {out!r}."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]

    def test_teardown_emits_on_terminal_update_to_zero(self, tmp_path):
        """No-regression guard for the Teardown threshold (count == 0).
        This PR explicitly does NOT change Teardown; the test must pass
        under both the reverted strict-equality Arm and the relaxed
        positive-bound Arm."""
        home = tmp_path / "home"
        home.mkdir()
        sid = "pact-2877fe69"
        pdir = "/Users/mj/Sites/collab/PACT-prompt"
        team = "team-race"
        _write_session_context(home, sid, pdir, team)
        _write_task(home, team, "10", status="completed", owner="backend-coder")

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid,
            "cwd": pdir,
            "tool_input": {"taskId": "10", "status": "completed"},
            "tool_response": {"id": "10", "status": "completed"},
        }, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, f"Expected Teardown; got {out!r}."
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:stop-pending-scan\")" in hso["additionalContext"]

    def test_teardown_no_emit_on_terminal_update_with_residual(self, tmp_path):
        """No-regression guard against accidentally relaxing Teardown
        symmetrically with the Arm change. With one residual active task
        on disk, the terminal-status TaskUpdate for a different task
        leaves count > 0 and Teardown must NOT emit (suppressOutput
        sentinel). Passes under both revert and fix."""
        home = tmp_path / "home"
        home.mkdir()
        sid = "pact-2877fe69"
        pdir = "/Users/mj/Sites/collab/PACT-prompt"
        team = "team-race"
        _write_session_context(home, sid, pdir, team)
        _write_task(home, team, "10", status="completed", owner="backend-coder")
        _write_task(home, team, "11", status="in_progress", owner="test-engineer")

        out = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid,
            "cwd": pdir,
            "tool_input": {"taskId": "10", "status": "completed"},
            "tool_response": {"id": "10", "status": "completed"},
        }, home)
        assert out == {"suppressOutput": True}, (
            f"Expected suppressOutput sentinel (residual active task "
            f"keeps count > 0; Teardown must not emit); got {out!r}."
        )

    def test_no_emit_on_zero_active_count_taskcreate(self, tmp_path):
        """Lower-bound guard for the relaxed predicate. Pipe a
        TaskCreate fixture but write NO task files; count_active_tasks
        returns 0 and `>= 1` rejects. Pins the rule that zero must
        remain a no-op even after the threshold relaxation."""
        fixture = self._load_fixture("task_create_parallel_burst_first.json")
        home, _ = self._setup_session(tmp_path, fixture)
        # Deliberately do NOT write task 10's file. The hook's
        # _extract_task_id will still parse the id from tool_response,
        # but count_active_tasks reads the tasks dir and returns 0.

        out = _emit_output(fixture, home)
        assert out == {"suppressOutput": True}, (
            f"Expected suppressOutput sentinel (zero active count must "
            f"not emit Arm under the positive-bound predicate); "
            f"got {out!r}."
        )

    @pytest.mark.parametrize("burst_size", [1, 2, 3], ids=["N=1", "N=2", "N=3"])
    def test_arm_emits_for_parametrized_burst_size(self, tmp_path, burst_size):
        """Parametrized verification across burst sizes N in {1, 2, 3}.
        For each N, pre-write N task files (ids 10..10+N-1) and pipe the
        first-burst fixture through the hook. Positive-bound predicate
        emits Arm for every N >= 1. Strict-equality revert FAILS for
        N >= 2, expanding the cardinality of failing tests under revert
        beyond the two race-coupled cases above."""
        fixture = self._load_fixture("task_create_parallel_burst_first.json")
        home, team = self._setup_session(tmp_path, fixture)
        for offset in range(burst_size):
            _write_task(
                home, team, str(10 + offset),
                status="pending",
                owner=f"agent-{offset}",
            )

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm directive on parametrized burst (N={burst_size}); "
            f"got {out!r}."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]

    def test_teardown_sequence_after_parallel_burst_arm(self, tmp_path):
        """Lifecycle-symmetric guard for the Teardown side of the race
        window. Sequence: parallel burst raises count 0->2 (Arm fires) ->
        first task completes 2->1 (Teardown must NOT fire; residual
        active remains) -> second task completes 1->0 (Teardown fires).
        Pins that the Teardown threshold (count == 0) is unaffected by
        the relaxed Arm threshold, and that a parallel-burst-then-drain
        lifecycle terminates cleanly."""
        first_fixture = self._load_fixture("task_create_parallel_burst_first.json")
        home, team = self._setup_session(tmp_path, first_fixture)
        sid = first_fixture["session_id"]
        pdir = first_fixture["cwd"]

        # State after parallel burst: both task files on disk, in_progress.
        _write_task(home, team, "10", status="in_progress", owner="backend-coder")
        _write_task(home, team, "11", status="in_progress", owner="test-engineer")

        # First completion 2 -> 1: residual active task remains; no Teardown.
        _write_task(home, team, "10", status="completed", owner="backend-coder")
        out_first_complete = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid,
            "cwd": pdir,
            "tool_input": {"taskId": "10", "status": "completed"},
            "tool_response": {"id": "10", "status": "completed"},
        }, home)
        assert out_first_complete == {"suppressOutput": True}, (
            f"Expected suppressOutput when residual active task remains "
            f"(2 -> 1 transition); got {out_first_complete!r}. If this "
            f"emits Teardown, the Teardown threshold has been relaxed "
            f"symmetrically with the Arm change — see #637 review TE-1."
        )

        # Second completion 1 -> 0: Teardown fires.
        _write_task(home, team, "11", status="completed", owner="test-engineer")
        out_final = _emit_output({
            "tool_name": "TaskUpdate",
            "session_id": sid,
            "cwd": pdir,
            "tool_input": {"taskId": "11", "status": "completed"},
            "tool_response": {"id": "11", "status": "completed"},
        }, home)
        hso = out_final.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Teardown directive on terminal 1 -> 0 transition "
            f"after parallel-burst Arm; got {out_final!r}."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:stop-pending-scan\")" in hso["additionalContext"]

    def test_arm_emits_when_parallel_burst_includes_signal_task(self, tmp_path):
        """Predicate-invariance under the count_active_tasks signal-task
        filter. Parallel burst is (signal-task, real-task). The signal-
        task is filtered by count_active_tasks; the predicate sees only
        the lifecycle-relevant count (1, from the real task). Arm fires
        for the real-task fire on the post-filter count, demonstrating
        that the relaxed `>= 1` threshold composes correctly with the
        carve-out filter rather than bypassing it."""
        fixture = self._load_fixture("task_create_parallel_burst_first.json")
        home, team = self._setup_session(tmp_path, fixture)

        # Pre-write a signal-task (filtered) and a real lifecycle-relevant
        # task (not filtered). Post-filter count == 1.
        _write_task(
            home, team, "sig",
            status="in_progress",
            owner="x",
            metadata={"completion_type": "signal", "type": "blocker"},
        )
        _write_task(home, team, "10", status="pending", owner="backend-coder")

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm directive when burst is (signal-task, real-task) "
            f"and post-filter count == 1; got {out!r}. If suppressOutput, "
            f"the signal-task filter is being bypassed by the relaxed "
            f"predicate — predicate-invariance broken."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]

    def test_arm_emits_when_parallel_burst_includes_exempt_owner(self, tmp_path):
        """Predicate-invariance under the count_active_tasks self-
        complete-exempt-agentType filter. Parallel burst is
        (secretary-task, real-task). The secretary-owned task is filtered
        via team-config agentType lookup (#682); predicate sees
        post-filter count == 1 from the real task. Arm fires on the
        real-task fire, demonstrating composition with the exempt-agentType
        carve-out matches composition with the signal-task carve-out."""
        fixture = self._load_fixture("task_create_parallel_burst_first.json")
        # Override _setup_session to wire the team config with the
        # secretary's privileged agentType (#682 — name alone is
        # insufficient; the team config must record the agentType).
        home = tmp_path / "home"
        home.mkdir()
        sid = fixture["session_id"]
        pdir = fixture["cwd"]
        team = "team-race"
        _write_session_context(
            home, sid, pdir, team,
            members=[{"name": "session-secretary", "agentType": "pact-secretary"}],
        )

        # Pre-write a session-secretary-owned task (filtered via
        # team-config agentType) and a real lifecycle-relevant task
        # (not filtered). Post-filter count == 1.
        _write_task(home, team, "sec", status="in_progress", owner="session-secretary")
        _write_task(home, team, "10", status="pending", owner="backend-coder")

        out = _emit_output(fixture, home)
        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"Expected Arm directive when burst is (secretary-task, "
            f"real-task) and post-filter count == 1; got {out!r}. If "
            f"suppressOutput, the exempt-owner filter is being bypassed."
        )
        assert hso["hookEventName"] == "PostToolUse"
        assert "Skill(\"PACT:start-pending-scan\")" in hso["additionalContext"]


