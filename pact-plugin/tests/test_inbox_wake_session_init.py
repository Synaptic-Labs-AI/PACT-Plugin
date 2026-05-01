"""
Structural invariants for session_init.py's resume-with-active-tasks
Arm directive (Option-C gap closure for #591).

Source-grep tests: pin the directive prose, the count_active_tasks
call site, and the unconditional-emission discipline. We don't run
session_init.py end-to-end — these are file-parsing fences.
"""

from pathlib import Path

import pytest

SESSION_INIT_PATH = (
    Path(__file__).resolve().parent.parent / "hooks" / "session_init.py"
)


@pytest.fixture(scope="module")
def src() -> str:
    return SESSION_INIT_PATH.read_text(encoding="utf-8")


def test_imports_count_active_tasks_from_wake_lifecycle(src):
    assert "from shared.wake_lifecycle import count_active_tasks" in src


def test_calls_count_active_tasks(src):
    # Single call site at the resume-Arm branch.
    assert src.count("count_active_tasks(team_name)") >= 1


def test_directive_references_watch_inbox_command_slug(src):
    assert 'Skill("PACT:watch-inbox")' in src


def test_directive_includes_idempotency_clause(src):
    # Cycle 4 directive prose: "Idempotent — no-op if a valid
    # STATE_FILE is already on disk." Source is split across two
    # quoted strings via Python implicit-concat, so substring matches
    # must accommodate the line break — pin shorter fragments.
    assert "idempotent" in src.lower()
    assert "no-op if a valid" in src
    assert "STATE_FILE is already on disk" in src


def test_directive_includes_active_task_trigger_phrase(src):
    """The Tier-0 directive must declare the precondition (active tasks
    on disk) so an LLM reader cannot misread it as unconditional Arm
    on every session start."""
    assert "Active teammate tasks detected" in src


def test_directive_emitted_only_when_count_positive(src):
    """Guard the emission with a positive-count check. The directive
    must NOT fire on sessions with zero active teammate tasks."""
    assert "if active_count > 0:" in src


def test_directive_appended_to_context_parts(src):
    """The directive flows through Tier-0 additionalContext via the
    context_parts append channel, not via a separate emission path."""
    # Source contains a `context_parts.append(` near the Arm directive.
    assert "context_parts.append(" in src
    # And the directive prose lives in that block.
    assert (
        "Active teammate tasks detected on session start." in src
    )


# ---------- Behavioral: session_init Arm-emit gate fires only when count>0 ----------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

SESSION_INIT_HOOK = SESSION_INIT_PATH


_ARM_DIRECTIVE_PHRASE = "Active teammate tasks detected on session start."


def _stage_pact_session(home: Path, team: str, sid: str, pdir: str) -> None:
    slug = Path(pdir).name
    sess_dir = home / ".claude" / "pact-sessions" / slug / sid
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": team,
            "session_id": sid,
            "project_dir": pdir,
            "plugin_root": "",
            "started_at": "2026-04-30T00:00:00Z",
        }),
        encoding="utf-8",
    )


def _stage_active_task(home: Path, team: str) -> None:
    tasks_dir = home / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "1.json").write_text(
        json.dumps({"id": "1", "status": "in_progress", "owner": "backend-coder"}),
        encoding="utf-8",
    )


def _run_session_init(home: Path, sid: str, pdir: str, source: str = "resume") -> dict:
    payload = json.dumps({"session_id": sid, "cwd": pdir, "source": source})
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update({"HOME": str(home), "CLAUDE_PROJECT_DIR": pdir})
    proc = subprocess.run(
        [sys.executable, str(SESSION_INIT_HOOK)],
        input=payload.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, f"session_init exited {proc.returncode}; stderr={proc.stderr!r}"
    return json.loads(proc.stdout.decode("utf-8") or "{}")


def test_session_init_omits_arm_directive_when_no_active_tasks(tmp_path):
    """Behavioral pin (B4): Arm-emit gate must fire only when
    count_active_tasks > 0. Pure-structural source-grep is false-RED-prone
    on benign refactor (e.g., extracting a helper); subprocess execution
    confirms the gate's actual emit semantics. With zero active tasks
    on disk, the directive prose must NOT appear in additionalContext."""
    home = tmp_path / "home"; home.mkdir()
    # session_id[:8] filters to [a-f0-9-]; use a pure-hex session_id so
    # generate_team_name returns a predictable team name.
    sid = "abcdef01-no-tasks-here"
    pdir = "/tmp/pi-empty"
    team = "pact-abcdef01"
    _stage_pact_session(home, team, sid, pdir)
    # Stage the team's tasks dir but leave it empty.
    (home / ".claude" / "tasks" / team).mkdir(parents=True)
    out = _run_session_init(home, sid, pdir)
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE not in additional, (
        "Arm directive emitted with zero active tasks — gate is broken"
    )


def test_session_init_emits_arm_directive_when_active_tasks_present(tmp_path):
    """Symmetric behavioral pin: with one active task on disk,
    additionalContext must carry the Arm directive's precondition phrase."""
    home = tmp_path / "home"; home.mkdir()
    sid = "deadbeef-active-task-present"
    pdir = "/tmp/pi-active"
    team = "pact-deadbeef"
    _stage_pact_session(home, team, sid, pdir)
    _stage_active_task(home, team)
    out = _run_session_init(home, sid, pdir)
    additional = out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert _ARM_DIRECTIVE_PHRASE in additional, (
        "Arm directive missing despite active task on disk — gate is broken"
    )
    # And the directive references the canonical command slug.
    assert 'Skill("PACT:watch-inbox")' in additional
