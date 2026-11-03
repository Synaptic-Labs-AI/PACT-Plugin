"""The emitter's journal attribution, through a REAL hook process.

`agent_handoff_emitter.py` runs as a child interpreter against a seeded home:
a session context file naming the team, that team's task store, and (for the
suppression arms) a second, live task store. Nothing is imported, patched or
stubbed in the child. Its environment is built from nothing, so no variable
the test harness scrubbed or set in this process reaches it by inheritance.

These arms cover what an in-process run cannot: that the real process
resolves the store it records as `team`, and that a mixed-store payload is
journalled as `handoff_emit_suppressed` by the process itself.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
EMITTER = HOOKS_DIR / "agent_handoff_emitter.py"

TEAM = "session-emitsub"
OTHER = "session-elsewhere"
SID = "dddddddd-4444-5555-6666-777777777777"
SLUG = "emitsub-project"

HANDOFF = {"produced": "p", "decisions": "d", "reasoning_chain": "r",
           "uncertainty": "u", "integration": "i", "open_questions": "q"}


def _seed(tmp_path: Path, *, other_store: bool) -> tuple[Path, Path]:
    """(home, session_dir): context file, this team's store holding task 7,
    and optionally a second live store."""
    home = tmp_path / "home"
    session_dir = home / ".claude" / "pact-sessions" / SLUG / SID
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps({
        "team_name": TEAM, "session_id": SID, "project_dir": f"/tmp/{SLUG}",
        "plugin_root": "", "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    store = home / ".claude" / "tasks" / TEAM
    store.mkdir(parents=True)
    (store / "7.json").write_text(json.dumps({
        "id": "7", "owner": "architect", "subject": "record subject",
        "status": "completed", "metadata": {"handoff": HANDOFF},
    }), encoding="utf-8")
    if other_store:
        (home / ".claude" / "tasks" / OTHER).mkdir(parents=True)
    return home, session_dir


def _run(home: Path, *, payload_team: str, payload_subject: str):
    env = {
        "HOME": str(home),
        # pact_context derives the session dir slug from this basename.
        "CLAUDE_PROJECT_DIR": f"/tmp/{SLUG}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, str(EMITTER)],
        input=json.dumps({
            "hook_event_name": "TaskCompleted", "session_id": SID,
            "task_id": "7", "task_subject": payload_subject,
            "teammate_name": "architect", "team_name": payload_team,
        }),
        capture_output=True, text=True, env=env, cwd=str(home), timeout=30,
    )


def _events(session_dir: Path, event_type: str) -> list[dict]:
    journal = session_dir / "session-journal.jsonl"
    if not journal.exists():
        return []
    return [e for e in (json.loads(line) for line in
                        journal.read_text(encoding="utf-8").splitlines() if line.strip())
            if e.get("type") == event_type]


def test_the_handoff_event_names_the_store_the_process_resolved(tmp_path):
    home, session_dir = _seed(tmp_path, other_store=False)
    result = _run(home, payload_team=TEAM, payload_subject="record subject")
    assert result.returncode == 0, result.stderr
    handoffs = _events(session_dir, "agent_handoff")
    assert [e.get("team") for e in handoffs] == [TEAM]
    assert _events(session_dir, "handoff_emit_suppressed") == []


def test_a_mixed_store_payload_is_journalled_as_suppressed(tmp_path):
    """The payload names a second live store and a subject the resolved
    record does not carry: the process writes the suppression event and
    neither the handoff nor the snapshot."""
    home, session_dir = _seed(tmp_path, other_store=True)
    result = _run(home, payload_team=OTHER, payload_subject="another subject")
    assert result.returncode == 0, result.stderr
    suppressed = _events(session_dir, "handoff_emit_suppressed")
    assert [(e.get("task_id"), e.get("cause")) for e in suppressed] == [("7", "mixed_store")]
    assert _events(session_dir, "agent_handoff") == []
    assert _events(session_dir, "task_metadata_snapshot") == []


def test_control_a_second_store_with_an_agreeing_subject_still_emits(tmp_path):
    """Control for the suppression arm: the same two live stores, but the
    subjects agree, so one signal is missing and the handoff is written
    under the resolved store."""
    home, session_dir = _seed(tmp_path, other_store=True)
    result = _run(home, payload_team=OTHER, payload_subject="record subject")
    assert result.returncode == 0, result.stderr
    assert [e.get("team") for e in _events(session_dir, "agent_handoff")] == [TEAM]
    assert _events(session_dir, "handoff_emit_suppressed") == []


def _move_task_to_bare_base(home: Path, *, with_sibling: bool) -> None:
    """Leave the named store in place (empty) and put task 7 at the bare
    tasks base instead, optionally with a sibling key so a snapshot would be
    eligible."""
    tasks = home / ".claude" / "tasks"
    record = json.loads((tasks / TEAM / "7.json").read_text(encoding="utf-8"))
    if with_sibling:
        record["metadata"]["variety"] = {"total": 7}
    (tasks / TEAM / "7.json").unlink()
    (tasks / "7.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_bare_base_task_is_never_journalled_under_the_named_store(tmp_path):
    """The resolved store is TEAM and holds no task 7; the only task 7 is at
    the bare tasks base. Neither the handoff nor the snapshot is written."""
    home, session_dir = _seed(tmp_path, other_store=False)
    _move_task_to_bare_base(home, with_sibling=True)
    result = _run(home, payload_team=TEAM, payload_subject="record subject")
    assert result.returncode == 0, result.stderr
    assert _events(session_dir, "agent_handoff") == []
    assert _events(session_dir, "task_metadata_snapshot") == []


def test_control_a_named_store_task_snapshot_carries_the_team(tmp_path):
    """Control for the bare-base arm: the same record, with its sibling key,
    in the named store emits both events under that store's team."""
    home, session_dir = _seed(tmp_path, other_store=False)
    record_path = home / ".claude" / "tasks" / TEAM / "7.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["metadata"]["variety"] = {"total": 7}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    result = _run(home, payload_team=TEAM, payload_subject="record subject")
    assert result.returncode == 0, result.stderr
    assert [e.get("team") for e in _events(session_dir, "agent_handoff")] == [TEAM]
    assert [e.get("team") for e in _events(session_dir, "task_metadata_snapshot")] == [TEAM]


def test_with_no_resolved_store_a_bare_base_task_still_emits_without_team(tmp_path):
    """Unchanged behaviour: when the context names no team, no store
    resolves, the default read reaches the bare base, and the handoff is
    written with no `team`."""
    home, session_dir = _seed(tmp_path, other_store=False)
    _move_task_to_bare_base(home, with_sibling=False)
    (home / ".claude" / "tasks" / TEAM).rmdir()
    context = session_dir / "pact-session-context.json"
    data = json.loads(context.read_text(encoding="utf-8"))
    data["team_name"] = ""
    context.write_text(json.dumps(data), encoding="utf-8")
    result = _run(home, payload_team="", payload_subject="record subject")
    assert result.returncode == 0, result.stderr
    handoffs = _events(session_dir, "agent_handoff")
    assert len(handoffs) == 1 and "team" not in handoffs[0]
