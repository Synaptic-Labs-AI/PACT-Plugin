"""
How a move of the session's recorded team is recorded and reported.

shared.session_resume.append_team_realigned writes the team_realigned event
and record_team_realign turns its outcome into the lead notice. Both hooks
that move the record call them: bootstrap_marker_writer's prompt-time
write-back and session_init at launch.

* A move away from a team that never existed (a computed session-<id8> name
  the platform did not use, as when the platform names the team by the full
  session id) is no split: nothing is recorded and no notice is shown.
* The write-back judges the move from the context file on disk, so a write
  that did not land records nothing.
* When the journal cannot take the event, the lead is told the move could not
  be recorded, with the old team named, and never told it was recorded.
* One move gives one event, also when two processes make it at once; a later
  move back and forth is recorded each time.
* A realign is not a session start: the recorded start survives it.

Everything runs in process over the real path derivations
(tests/fixtures/team_split.py); a cell that removes a seam names it.

Counter-tests, measured by source-only mutation of session_resume.py and
bootstrap_marker_writer.py (failures in this file's run):
  record a move from a team that never existed -> 2 failed
  judge the write-back's move from the cache, not the file -> 1 failed
  report an append that returned False as recorded -> 1 failed
  drop the check against the latest event -> 2 failed
  drop the flock -> 1 failed (the concurrency cell, 8 of 8 runs; intact,
    it passed 8 of 8)
  drop started_at from the context write -> 1 failed
  drop the Started value from the CLAUDE.md write -> 1 failed
"""

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import captured_lead_userpromptsubmit_qualified
from fixtures.team_split import (
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    read_journal,
    reset_context_module,
    saved_team_name,
    seed_team,
    split_world,
    team_line,
)

import bootstrap_marker_writer
from shared import session_resume

OLD_TASKS = (
    {"id": "202", "subject": "in flight", "status": "in_progress"},
)
STARTED_LINE = "- Started: 2026-10-19 07:00:00 UTC"


def _world(tmp_path, monkeypatch, **overrides):
    """A split world whose journal records a session_start, so the CLAUDE.md
    half of the write-back is armed."""
    project_dir = tmp_path / "project"
    journal = (
        "task_metadata_snapshot",
        "session_end",
        {"type": "session_start", "session_id": OLD_SID,
         "project_dir": str(project_dir), "source": "resume"},
    )
    kwargs = dict(saved_team=OLD_TEAM, old_tasks=OLD_TASKS, journal=journal,
                  claude_md=True)
    kwargs.update(overrides)
    return split_world(tmp_path, monkeypatch, **kwargs)


def _init(world):
    import shared.pact_context as ctx_module
    reset_context_module()
    ctx_module.init({"session_id": OLD_SID})


def _prompt(monkeypatch, world):
    frame = captured_lead_userpromptsubmit_qualified()
    frame["session_id"] = OLD_SID
    frame["cwd"] = str(world.project_dir)
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit):
            bootstrap_marker_writer.main()
    reset_context_module()
    return json.loads(out.getvalue()).get("hookSpecificOutput", {}).get(
        "additionalContext", "")


def _moves(world):
    return [(e["from"], e["to"]) for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"]


# ── an old team that never existed is no split ────────────────────────────

def test_a_move_from_a_team_that_never_existed_records_nothing(
        tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch, old_tasks=())
    _init(world)
    assert session_resume.record_team_realign(OLD_TEAM, NEW_TEAM) is None
    assert _moves(world) == []


def test_a_move_from_a_team_whose_store_survives_is_recorded(
        tmp_path, monkeypatch):
    """Control: a split deletes the old config but keeps its task store."""
    world = _world(tmp_path, monkeypatch)
    _init(world)
    notice = session_resume.record_team_realign(OLD_TEAM, NEW_TEAM)
    assert f"from team `{OLD_TEAM}` to `{NEW_TEAM}`" in notice
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_a_move_from_a_team_whose_config_survives_is_recorded(
        tmp_path, monkeypatch):
    """Control: a config alone also proves the team existed."""
    world = _world(tmp_path, monkeypatch, old_tasks=(), old_config=True)
    _init(world)
    assert session_resume.record_team_realign(OLD_TEAM, NEW_TEAM)
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_the_full_session_id_team_realigns_silently(tmp_path, monkeypatch):
    """The platform named the team by the full session id; the computed
    session-<id8> name was never a team. The write-back moves the record and
    tells the lead nothing."""
    world = _world(tmp_path, monkeypatch, old_tasks=(), live_team=None)
    seed_team(world.root, OLD_SID, lead_sid=OLD_SID,
              lead_cwd=str(world.project_dir))
    notice = _prompt(monkeypatch, world)
    assert saved_team_name(world) == OLD_SID, "precondition: the record moved"
    assert _moves(world) == []
    assert "PACT moved this session" not in notice


# ── the write-back reads the move from disk ───────────────────────────────

def test_a_write_back_whose_write_did_not_land_records_nothing(
        tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    import shared.pact_context as ctx_module
    monkeypatch.setattr(ctx_module, "persist_context", lambda *a, **k: None)
    notice = _prompt(monkeypatch, world)
    assert saved_team_name(world) == OLD_TEAM
    assert _moves(world) == []
    assert "PACT moved this session" not in notice


def test_a_write_back_that_lands_records_the_move(tmp_path, monkeypatch):
    """Control for the cell above."""
    world = _world(tmp_path, monkeypatch)
    notice = _prompt(monkeypatch, world)
    assert saved_team_name(world) == NEW_TEAM
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]
    assert f"from team `{OLD_TEAM}` to `{NEW_TEAM}`" in notice


# ── an event that does not land is never reported as recorded ─────────────

def test_a_journal_that_refuses_the_event_gets_the_unrecorded_notice(
        tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    _init(world)
    monkeypatch.setattr(session_resume, "append_event", lambda *a, **k: False)
    assert (session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
            == session_resume.REALIGN_FAILED)
    notice = session_resume.record_team_realign(OLD_TEAM, NEW_TEAM)
    assert "could not record the move" in notice
    assert f"`{OLD_TEAM}` as the earlier team" in notice
    assert "PACT moved this session from team" not in notice
    assert _moves(world) == []


# ── one move, one event ───────────────────────────────────────────────────

def test_the_same_move_twice_is_recorded_once(tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    _init(world)
    assert (session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
            == session_resume.REALIGN_RECORDED)
    assert (session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
            == session_resume.REALIGN_ALREADY_RECORDED)
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_a_launch_that_loses_the_race_gets_no_notice(tmp_path, monkeypatch):
    """A SessionStart caller (the default) whose move another process already
    recorded shows nothing: the notice went to the process that recorded."""
    world = _world(tmp_path, monkeypatch)
    _init(world)
    session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
    assert session_resume.record_team_realign(OLD_TEAM, NEW_TEAM) is None
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_a_lead_prompt_that_loses_the_race_keeps_its_notice(
        tmp_path, monkeypatch):
    """A teammate's compaction recorded the move first; the lead's prompt
    write-back still tells the lead, and no second event is written."""
    world = _world(tmp_path, monkeypatch)
    _init(world)
    session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
    notice = _prompt(monkeypatch, world)
    assert saved_team_name(world) == NEW_TEAM, "precondition: the prompt moved it"
    assert f"from team `{OLD_TEAM}` to `{NEW_TEAM}`" in notice
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_a_move_back_and_forth_is_recorded_each_time(tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    _init(world)
    session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
    session_resume.append_team_realigned(NEW_TEAM, OLD_TEAM)
    session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM), (NEW_TEAM, OLD_TEAM),
                             (OLD_TEAM, NEW_TEAM)]


def test_a_move_from_another_team_to_the_same_one_is_recorded(
        tmp_path, monkeypatch):
    """The latest event moved into NEW_TEAM from a different team: a move
    from OLD_TEAM into NEW_TEAM is a different move and is recorded."""
    world = _world(tmp_path, monkeypatch)
    _init(world)
    (world.root / "tasks" / "session-cccc3333").mkdir(parents=True)
    session_resume.append_team_realigned("session-cccc3333", NEW_TEAM)
    session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
    assert _moves(world) == [("session-cccc3333", NEW_TEAM),
                             (OLD_TEAM, NEW_TEAM)]


# One racer: a separate python3 process that waits for the shared start file,
# then records the move. Separate processes, not threads or a fork, so each
# one takes its own flock exactly as two hook processes would.
_RACER = """
import sys, time
from pathlib import Path
from shared import session_resume
ready, go, session_dir = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ready.touch()
deadline = time.monotonic() + 30
while not go.exists() and time.monotonic() < deadline:
    pass  # spin, so every racer leaves the start line together
print(session_resume.append_team_realigned(
    sys.argv[4], sys.argv[5], session_dir=session_dir))
"""


def test_concurrent_records_of_one_move_write_one_event(tmp_path, monkeypatch):
    """Processes that make the same move at once (a lead prompt racing a
    teammate's compaction) append one event between them."""
    world = _world(tmp_path, monkeypatch)
    hooks_dir = str(Path(__file__).resolve().parents[1] / "hooks")
    # The racers import the hooks package through PYTHONPATH.
    env = dict(os.environ, HOME=str(tmp_path), PYTHONPATH=hooks_dir,
               CLAUDE_CONFIG_DIR=str(world.root))
    go = tmp_path / "go"
    racers = []
    for n in range(12):
        ready = tmp_path / f"ready-{n}"
        racers.append((ready, subprocess.Popen(
            [sys.executable, "-c", _RACER, str(ready), str(go),
             str(world.session_dir), OLD_TEAM, NEW_TEAM],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )))
    deadline = time.monotonic() + 30
    while (not all(ready.exists() for ready, _ in racers)
           and time.monotonic() < deadline):
        time.sleep(0.01)
    go.touch()
    outcomes = []
    for _, racer in racers:
        out, err = racer.communicate(timeout=60)
        assert racer.returncode == 0, err
        outcomes.append(out.strip())
    assert outcomes.count(session_resume.REALIGN_RECORDED) == 1, outcomes
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_a_lock_that_cannot_be_taken_still_records(tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    _init(world)
    # A directory where the lock file goes: opening it fails.
    (world.session_dir / ".team-realigned.lock").mkdir()
    assert (session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
            == session_resume.REALIGN_RECORDED)
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


def test_a_symlinked_lock_path_is_never_followed(tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    _init(world)
    target = tmp_path / "elsewhere" / "planted"
    target.parent.mkdir()
    link = world.session_dir / ".team-realigned.lock"
    link.symlink_to(target)
    assert (session_resume.append_team_realigned(OLD_TEAM, NEW_TEAM)
            == session_resume.REALIGN_RECORDED)
    assert not target.exists()
    assert link.is_symlink()
    assert _moves(world) == [(OLD_TEAM, NEW_TEAM)]


# ── a realign is not a session start ──────────────────────────────────────

def test_a_write_back_keeps_the_recorded_start(tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    claude_md = world.claude_md
    text = claude_md.read_text(encoding="utf-8")
    lines = [STARTED_LINE if line.startswith("- Started:") else line
             for line in text.splitlines()]
    claude_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    started_at = json.loads(world.context_path.read_text())["started_at"]
    _prompt(monkeypatch, world)
    assert team_line(claude_md) == f"- Team: `{NEW_TEAM}`", "precondition: realigned"
    assert STARTED_LINE in claude_md.read_text(encoding="utf-8").splitlines()
    assert json.loads(world.context_path.read_text())["started_at"] == started_at


def test_read_session_started_reads_only_the_block_value(tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch)
    started = session_resume.read_session_started(str(world.project_dir))
    assert started and started.endswith(" UTC")
    assert session_resume.read_session_started("") is None
    assert session_resume.read_session_started(str(tmp_path / "nowhere")) is None
