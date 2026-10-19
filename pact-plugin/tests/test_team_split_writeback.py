"""
The prompt-time team write-back runs after bootstrap and reports each move.

The bootstrap marker writer realigns the recorded team to the team the
platform is using on every lead prompt and Agent spawn. It used to run only
until the bootstrap marker was written, so a session that split after
bootstrap kept its stale team for the rest of its life. When the write-back
moves the team it journals ``team_realigned {from, to}`` and tells the lead
which in-progress tasks were left in the old team's store; when nothing moves
it says nothing.

bootstrap_marker_writer.main() runs in process over the real path derivations
(tests/fixtures/team_split.py), with nothing stubbed. Journal writes exit 0 on
a schema miss, so every journal assertion reads the event back from disk.

Counter-tests, measured by source-only revert in a scratch copy (cardinality
of this file's run):
  revert bootstrap_marker_writer.py and session_journal.py to the base tree
    -> 3 failed, 1 passed
  revert the census in pact_context.py to count-first -> 3 failed
  move the write-back back behind the marker fast path -> 2 failed
  report a move on every prompt -> 1 failed
"""

import io
import json
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
    split_world,
    team_line,
)

import bootstrap_marker_writer

_OLD_TASKS = [
    {"id": "215", "status": "in_progress", "owner": "coder-a"},
    {"id": "202", "status": "in_progress", "owner": "auditor"},
    {"id": "201", "status": "completed", "owner": "preparer"},
]
_PLUGIN_VERSION = "9.9.9"


def _world(tmp_path, monkeypatch, *, saved_team=OLD_TEAM, **kw):
    """A split world whose journal records a session_start, so the CLAUDE.md
    half of the write-back is armed."""
    project_dir = tmp_path / "project"
    journal = (
        "task_metadata_snapshot",
        "session_end",
        {"type": "session_start", "session_id": OLD_SID,
         "project_dir": str(project_dir), "source": "resume"},
    )
    world = split_world(tmp_path, monkeypatch, saved_team=saved_team,
                        journal=journal, claude_md=True, **kw)
    (world.plugin_root / ".claude-plugin").mkdir(exist_ok=True)
    (world.plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": _PLUGIN_VERSION}), encoding="utf-8")
    return world


def _set_marker(world):
    from bootstrap_gate import is_marker_set
    bootstrap_marker_writer._write_marker(
        world.session_dir, OLD_SID, str(world.plugin_root), _PLUGIN_VERSION)
    assert is_marker_set(world.session_dir), "precondition: marker is set"


def _prompt_frame(world):
    frame = captured_lead_userpromptsubmit_qualified()
    frame["session_id"] = OLD_SID
    frame["cwd"] = str(world.project_dir)
    return frame


def _agent_spawn_frame(world):
    return {
        "hook_event_name": "PostToolUse",
        "session_id": OLD_SID,
        "cwd": str(world.project_dir),
        "agent_type": "PACT:pact-orchestrator",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "pact-secretary", "name": "secretary"},
        "tool_response": {},
    }


def _run(monkeypatch, world, frame):
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        try:
            bootstrap_marker_writer.main()
        except SystemExit as exc:
            assert exc.code in (0, None)
    reset_context_module()
    return json.loads(out.getvalue())


def _realigned_events(world):
    return [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"]


def _assert_realigned(world, output):
    assert saved_team_name(world) == NEW_TEAM
    assert team_line(world.claude_md) == f"- Team: `{NEW_TEAM}`"
    events = _realigned_events(world)
    assert len(events) == 1, events
    assert events[0]["from"] == OLD_TEAM and events[0]["to"] == NEW_TEAM
    notice = output.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert OLD_TEAM in notice and NEW_TEAM in notice
    assert "202, 215" in notice
    assert "201" not in notice


def test_a_lead_prompt_realigns_the_team_and_reports_the_move(tmp_path,
                                                              monkeypatch):
    """Before bootstrap: the write-back moves the record and the CLAUDE.md
    team line to the live team, journals the move, and names the old team's
    in-progress tasks in the lead notice."""
    world = _world(tmp_path, monkeypatch, foreign="foreign-cwd",
                   old_tasks=_OLD_TASKS)
    output = _run(monkeypatch, world, _prompt_frame(world))
    _assert_realigned(world, output)


@pytest.mark.parametrize("frame_kind", ["prompt", "agent-spawn"])
def test_the_realign_still_runs_after_bootstrap(tmp_path, monkeypatch,
                                                frame_kind):
    """After bootstrap (marker set): a lead prompt, or a lead Agent spawn,
    still realigns the team and reports the move."""
    world = _world(tmp_path, monkeypatch, foreign="foreign-cwd",
                   old_tasks=_OLD_TASKS)
    _set_marker(world)
    frame = (_prompt_frame(world) if frame_kind == "prompt"
             else _agent_spawn_frame(world))
    output = _run(monkeypatch, world, frame)
    _assert_realigned(world, output)
    assert output["hookSpecificOutput"]["hookEventName"] == frame["hook_event_name"]


def test_an_aligned_session_gets_no_event_no_notice_and_no_write(tmp_path,
                                                                  monkeypatch):
    """The record already names the live team: nothing is journaled, the
    output is the silent envelope, and the record file is not rewritten."""
    from fixtures.team_split import stat_snapshot
    world = _world(tmp_path, monkeypatch, saved_team=NEW_TEAM)
    _set_marker(world)
    before = stat_snapshot(world.context_path, world.claude_md)
    output = _run(monkeypatch, world, _prompt_frame(world))
    assert _realigned_events(world) == []
    assert output == {"suppressOutput": True,
                      "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}}, output
    assert stat_snapshot(world.context_path, world.claude_md) == before
