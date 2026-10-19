"""
A /clear keeps the session on the team it was on.

/clear gives every hook frame a NEW session id and a new session dir, while
the platform keeps the session on its existing team, whose config still
carries the PREVIOUS session's id. PACT used to compute a team from the new
id (a team that does not exist) and record it in the session context and the
project CLAUDE.md, and the per-prompt write-back could not repair it. The
resolver now follows the previous session's record, and adopts its team only
through identity: the team's config names a session on the chain of clears as
its lead, or that session journalled a corroborated move to the team. Every
other shape keeps the computed default.

The world is built on disk through the real path derivations
(tests/fixtures/team_split.py); the hooks' main() runs in process with nothing
stubbed, plus one row through real subprocesses.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_lead_userpromptsubmit_qualified,
)
from fixtures.team_split import (
    config_root,
    read_journal,
    reset_context_module,
    seed_plugin,
    seed_team,
    team_line,
    write_context_file,
    write_journal,
    write_session_block,
)

# The session before the /clear, and the platform team it is on.
PREV_SID = "5578b689-676f-4823-b1c5-a5a4c0a7f300"
PREV_TEAM = "session-5578b689"
# The session id every hook frame carries after the /clear.
CLEAR_SID = "e62f859f-4ddb-471b-a5ef-dbc7fc6d72b3"
# The team PACT computes from the new id: no such team exists.
COMPUTED_TEAM = "session-e62f859f"
# A second /clear after the first.
SECOND_CLEAR_SID = "c0ffee00-1111-4222-8333-444455556666"
# A session id nothing on the chain carried.
THIRD_SID = "77777777-8888-4999-aaaa-bbbbbbbbbbbb"
# Another live PACT session, running in the parent directory.
FOREIGN_SID = "cafe1234-5678-4abc-9def-0123456789ab"
FOREIGN_TEAM = "session-cafe1234"

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _clear_world(tmp_path, monkeypatch, *, lead_sid=PREV_SID, team_config=True,
                 prev_record=True, realigned_to=None, foreign=False):
    """The measured world just before a /clear: the previous session recorded
    PREV_TEAM, whose config names ``lead_sid`` as its lead."""
    root = config_root(tmp_path)
    project = tmp_path / "parent" / "project"
    (project / ".claude").mkdir(parents=True)
    plugin = seed_plugin(tmp_path / "plugin")
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (root / "teams").mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    if team_config:
        seed_team(root, PREV_TEAM, lead_sid=lead_sid, lead_cwd=str(project),
                  members=("secretary",))
    if foreign:
        seed_team(root, FOREIGN_TEAM, lead_sid=FOREIGN_SID,
                  lead_cwd=str(project.parent))
    prev_dir = write_context_file(PREV_SID, PREV_TEAM, project, plugin).parent
    events = [{"type": "session_start", "session_id": PREV_SID,
               "project_dir": str(project), "source": "startup"},
              "task_metadata_snapshot"]
    if realigned_to:
        events.append({"type": "team_realigned", "from": "session-old00000",
                       "to": realigned_to})
    write_journal(prev_dir, events)
    if prev_record:
        write_session_block(PREV_SID, PREV_TEAM, prev_dir, plugin)
    reset_context_module()
    return SimpleNamespace(
        tmp=tmp_path, root=root, project=project, plugin=plugin,
        prev_dir=prev_dir, clear_dir=prev_dir.parent / CLEAR_SID,
        claude_md=project / ".claude" / "CLAUDE.md",
    )


def _run_main(monkeypatch, world, module, frame):
    monkeypatch.chdir(world.project)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        try:
            module.main()
        except SystemExit:
            pass
    reset_context_module()


def _clear(monkeypatch, world, sid=CLEAR_SID):
    import session_init
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=sid, source="clear", cwd=str(world.project))
    _run_main(monkeypatch, world, session_init, frame)


def _prompt(monkeypatch, world, sid=CLEAR_SID):
    import bootstrap_marker_writer
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=sid, cwd=str(world.project))
    _run_main(monkeypatch, world, bootstrap_marker_writer, frame)


def _recorded_team(session_dir):
    return json.loads(
        (session_dir / "pact-session-context.json").read_text(encoding="utf-8")
    )["team_name"]


# -- the measured world -------------------------------------------------------


def test_a_clear_records_the_team_the_platform_kept(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _clear(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == PREV_TEAM
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"
    starts = [e for e in read_journal(world.clear_dir)
              if e.get("type") == "session_start"]
    assert [(s["source"], s["team"]) for s in starts] == [("clear", PREV_TEAM)]


def test_the_first_prompt_after_a_clear_keeps_the_team_and_writes_the_marker(
        tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _clear(monkeypatch, world)
    _prompt(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == PREV_TEAM
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"
    assert (world.clear_dir / "bootstrap-complete").is_file()


def test_a_prompt_repairs_a_clear_record_that_names_the_computed_team(
        tmp_path, monkeypatch):
    """The session's own journal is enough: a record already holding the
    computed team (written before this rule existed) is repaired by the
    first prompt's write-back."""
    world = _clear_world(tmp_path, monkeypatch)
    write_context_file(CLEAR_SID, COMPUTED_TEAM, world.project, world.plugin)
    write_journal(world.clear_dir, [{
        "type": "session_start", "session_id": CLEAR_SID,
        "project_dir": str(world.project), "source": "clear",
        "team": COMPUTED_TEAM, "prev_session_dir": str(world.prev_dir),
    }])
    _prompt(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == PREV_TEAM


def test_a_second_clear_still_follows_the_chain(tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    _clear(monkeypatch, world)
    _clear(monkeypatch, world, sid=SECOND_CLEAR_SID)
    second_dir = world.prev_dir.parent / SECOND_CLEAR_SID
    assert _recorded_team(second_dir) == PREV_TEAM
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"


def test_a_clear_after_a_realign_follows_the_recorded_move(tmp_path,
                                                           monkeypatch):
    """The team's lead id is a session the chain never carried (the team was
    reached by a realign), and the previous session journalled that move."""
    world = _clear_world(tmp_path, monkeypatch, lead_sid=THIRD_SID,
                         realigned_to=PREV_TEAM)
    _clear(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == PREV_TEAM


# -- fail-safe controls: the computed default, never a guess -------------------


def test_a_clear_after_an_unrecorded_move_keeps_the_default(tmp_path,
                                                           monkeypatch):
    world = _clear_world(tmp_path, monkeypatch, lead_sid=THIRD_SID)
    _clear(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == COMPUTED_TEAM


def test_a_clear_whose_previous_team_config_is_gone_keeps_the_default(
        tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch, team_config=False)
    _clear(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == COMPUTED_TEAM


def test_a_clear_with_no_previous_record_keeps_the_default(tmp_path,
                                                          monkeypatch):
    world = _clear_world(tmp_path, monkeypatch, prev_record=False)
    _clear(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == COMPUTED_TEAM


def test_a_foreign_live_team_in_the_parent_dir_is_never_adopted(
        tmp_path, monkeypatch):
    world = _clear_world(tmp_path, monkeypatch, team_config=False,
                         foreign=True)
    _clear(monkeypatch, world)
    _prompt(monkeypatch, world)
    assert _recorded_team(world.clear_dir) == COMPUTED_TEAM


def test_a_startup_never_follows_the_previous_record(tmp_path, monkeypatch):
    """Only a clear carries the team: a fresh startup with the same previous
    record keeps its computed team."""
    import session_init
    world = _clear_world(tmp_path, monkeypatch)
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=CLEAR_SID, source="startup", cwd=str(world.project))
    _run_main(monkeypatch, world, session_init, frame)
    assert _recorded_team(world.clear_dir) == COMPUTED_TEAM


# -- through real processes ---------------------------------------------------


def test_a_clear_carries_the_team_through_real_hook_processes(tmp_path,
                                                             monkeypatch):
    world = _clear_world(tmp_path, monkeypatch)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=str(world.tmp), CLAUDE_PROJECT_DIR=str(world.project),
               CLAUDE_PLUGIN_ROOT=str(world.plugin))
    start = captured_compaction_lead_sessionstart()
    start.update(session_id=CLEAR_SID, source="clear", cwd=str(world.project))
    prompt = captured_lead_userpromptsubmit_qualified()
    prompt.update(session_id=CLEAR_SID, cwd=str(world.project))
    for hook, frame in (("session_init.py", start),
                        ("bootstrap_marker_writer.py", prompt)):
        proc = subprocess.run(
            [sys.executable, str(_HOOKS / hook)], input=json.dumps(frame),
            capture_output=True, text=True, env=env, cwd=str(world.project),
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
    assert _recorded_team(world.clear_dir) == PREV_TEAM
    assert team_line(world.claude_md) == f"- Team: `{PREV_TEAM}`"
    assert (world.clear_dir / "bootstrap-complete").is_file()
