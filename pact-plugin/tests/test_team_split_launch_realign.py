"""
A launch that moves the session's recorded team records the move.

On a resume into a team split, session_init's census can realign the record
to the team the platform is using before any prompt runs. The prompt-time
write-back then finds the record already aligned and sees no move, so
session_init records the move itself: one team_realigned {from, to} event in
the session journal and the realign notice in the SessionStart context. The
harvest of the earlier team's task store, the post-compaction recovery check
and the lead's notice all key on that event.

Only a resume or a compaction records. A clear re-derives the team and
records nothing; a launch that falls back to a stale name whose team has no
config is not a move to the platform's team; a frame that is not the lead's
writes nothing.

session_init.main() and bootstrap_marker_writer.main() run in process over
the real path derivations (tests/fixtures/team_split.py), with nothing
stubbed except where a cell names the seam it removes.

Counter-tests, measured by source-only mutation in a scratch copy
(cardinality of this file's run, 13 cases):
  remove the launch record -> 6 failed (both resume arms, the first-prompt
    and second-launch arms, both compaction arms)
  drop the config check on the new team -> 1 failed (the stale fallback)
  compare against the in-memory team instead of the persisted one ->
    1 failed (the persist that did not land)
  drop the /clear carry-over -> 1 failed (the clear cell's precondition)
  record on every source -> 0 failed: EQUIVALENT on every reachable shape.
    Only a resume or a compaction keeps the session id, and both already
    record; startup, clear and fork get a new id with no saved record, so
    the source gate never changes the outcome.
"""

import io
import json
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_sessionstart,
    captured_lead_sessionstart_qualified,
    captured_lead_userpromptsubmit_qualified,
    captured_plain_sessionstart,
    captured_teammate_sessionstart,
)
from fixtures.team_split import (
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    read_journal,
    reset_context_module,
    clear_frame,
    clear_world,
    recorded_team,
    saved_team_name,
    split_world,
)

import bootstrap_marker_writer
import session_init

GONE_TEAM = "session-deadbeef"
# A new session's own id: it has no record yet.
FRESH_SID = "12345678-9abc-4def-8123-456789abcdef"

OLD_TASKS = (
    {"id": "201", "subject": "done", "status": "completed"},
    {"id": "202", "subject": "in flight", "status": "in_progress"},
)


def _launch_frame(world, source, *, shape=captured_lead_sessionstart_qualified,
                  session_id=OLD_SID):
    frame = shape()
    frame["session_id"] = session_id
    frame["cwd"] = str(world.project_dir)
    frame["source"] = source
    return frame


def _prompt_frame(world):
    frame = captured_lead_userpromptsubmit_qualified()
    frame["session_id"] = OLD_SID
    frame["cwd"] = str(world.project_dir)
    return frame


def _run_session_init(monkeypatch, world, frame):
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    reset_context_module()
    return json.loads(out.getvalue()).get("hookSpecificOutput", {}).get(
        "additionalContext", "")


def _run_prompt(monkeypatch, world):
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(_prompt_frame(world)))), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit):
            bootstrap_marker_writer.main()
    reset_context_module()
    return json.loads(out.getvalue()).get("hookSpecificOutput", {}).get(
        "additionalContext", "")


def _realigned_events(world):
    return [e for e in read_journal(world.session_dir)
            if e.get("type") == "team_realigned"]


def _split(tmp_path, monkeypatch, **overrides):
    """The record names the pre-resume team, whose config is gone; the team
    the platform is using corroborates as this session's and its store is
    fresh, so the census at launch picks it."""
    kwargs = dict(saved_team=OLD_TEAM, old_tasks=OLD_TASKS,
                  foreign="foreign-cwd", claude_md=True)
    kwargs.update(overrides)
    return split_world(tmp_path, monkeypatch, **kwargs)


def _assert_recorded(world, context, old_team):
    assert saved_team_name(world) == NEW_TEAM
    events = _realigned_events(world)
    assert [(e["from"], e["to"]) for e in events] == [(old_team, NEW_TEAM)], events
    assert f"from team `{old_team}` to `{NEW_TEAM}`" in context


def test_a_resume_that_realigns_records_the_move_and_tells_the_lead(
        tmp_path, monkeypatch):
    world = _split(tmp_path, monkeypatch)
    context = _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    _assert_recorded(world, context, OLD_TEAM)
    assert "In-progress tasks still in `tasks/" + OLD_TEAM + "/`: 202." in context


def test_a_lead_resumed_without_its_agent_flag_records_the_move(
        tmp_path, monkeypatch):
    """A resumed lead whose frame carries no agent_type is recovered as the
    lead from its persisted context, and its realign is recorded the same."""
    world = _split(tmp_path, monkeypatch)
    context = _run_session_init(
        monkeypatch, world,
        _launch_frame(world, "resume", shape=captured_plain_sessionstart))
    _assert_recorded(world, context, OLD_TEAM)


def test_the_first_prompt_after_it_records_nothing_more(tmp_path, monkeypatch):
    world = _split(tmp_path, monkeypatch)
    _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    notice = _run_prompt(monkeypatch, world)
    assert len(_realigned_events(world)) == 1
    assert "PACT moved this session" not in notice


def test_a_second_launch_on_the_aligned_record_records_nothing_more(
        tmp_path, monkeypatch):
    world = _split(tmp_path, monkeypatch)
    _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    context = _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    assert len(_realigned_events(world)) == 1
    assert "PACT moved this session" not in context


@pytest.mark.parametrize("shape", [
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_sessionstart,
], ids=["lead", "lead-shaped teammate"])
def test_a_compaction_whose_recorded_team_is_gone_records_the_move(
        tmp_path, monkeypatch, shape):
    world = _split(tmp_path, monkeypatch, saved_team=GONE_TEAM)
    # The vanished team left its task store behind, as a real split does: a
    # team with neither a config nor a store is not one the session ran in.
    (world.tasks / GONE_TEAM).mkdir(parents=True, exist_ok=True)
    context = _run_session_init(
        monkeypatch, world, _launch_frame(world, "compact", shape=shape))
    _assert_recorded(world, context, GONE_TEAM)


def test_a_compaction_that_keeps_the_record_records_nothing(
        tmp_path, monkeypatch):
    """Control: the record already names the platform's team, so the keep
    leaves it as it is."""
    world = _split(tmp_path, monkeypatch, saved_team=NEW_TEAM,
                   live_store_aged=True)
    context = _run_session_init(monkeypatch, world, _launch_frame(world, "compact"))
    assert saved_team_name(world) == NEW_TEAM
    assert _realigned_events(world) == []
    assert "PACT moved this session" not in context


def test_a_resume_on_an_aligned_record_records_nothing(tmp_path, monkeypatch):
    """Control: the census picks the team the record already names."""
    world = _split(tmp_path, monkeypatch, saved_team=NEW_TEAM)
    context = _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    assert saved_team_name(world) == NEW_TEAM
    assert _realigned_events(world) == []
    assert "PACT moved this session" not in context


def test_a_clear_that_carries_the_team_records_nothing(tmp_path, monkeypatch):
    """A /clear in the measured shape: a new session id and session dir, the
    earlier session in the same process recorded the platform's team. The new
    session's record names that team (carried), and no move is journaled or
    announced, in either session's journal: keeping a team is not a move. A
    clear has no record of its own to move, so restricting which sources
    record cannot change this outcome; the source gate has no reachable kill."""
    world = clear_world(tmp_path, monkeypatch, prev_team=NEW_TEAM)
    context = _run_session_init(monkeypatch, world,
                                clear_frame(world, captured_lead_sessionstart_qualified))
    assert recorded_team(world.clear_dir) == NEW_TEAM, (
        "precondition: the clear carried the team")
    moves = [e for d in (world.session_dir, world.clear_dir)
             for e in read_journal(d) if e.get("type") == "team_realigned"]
    assert moves == []
    assert "PACT moved this session" not in context


def test_a_fallback_to_a_stale_team_without_config_is_not_recorded(
        tmp_path, monkeypatch):
    """The record names the platform's team, its store is idle past the
    census window, so the resume falls back to the pre-resume name, whose
    config is gone. That is not a move to the team the platform is using."""
    world = _split(tmp_path, monkeypatch, saved_team=NEW_TEAM,
                   live_store_aged=True)
    context = _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    assert saved_team_name(world) == OLD_TEAM, "precondition: the record fell back"
    assert _realigned_events(world) == []
    assert "PACT moved this session" not in context


def test_a_fresh_startup_with_no_record_records_nothing(tmp_path, monkeypatch):
    world = _split(tmp_path, monkeypatch)
    _run_session_init(
        monkeypatch, world,
        _launch_frame(world, "startup", session_id=FRESH_SID))
    fresh_dir = world.session_dir.parent / FRESH_SID
    assert [e for e in read_journal(fresh_dir)
            if e.get("type") == "team_realigned"] == []
    assert _realigned_events(world) == []


def test_a_teammate_frame_writes_nothing(tmp_path, monkeypatch):
    """A teammate's SessionStart, even one carrying the lead's session id,
    neither moves the record nor records a move."""
    world = _split(tmp_path, monkeypatch)
    context = _run_session_init(
        monkeypatch, world,
        _launch_frame(world, "resume", shape=captured_teammate_sessionstart))
    assert saved_team_name(world) == OLD_TEAM
    assert _realigned_events(world) == []
    assert "PACT moved this session" not in context


def test_a_persist_that_does_not_land_records_nothing(tmp_path, monkeypatch):
    """The move is read back from the context file, so a launch whose persist
    wrote nothing records no move it did not make."""
    world = _split(tmp_path, monkeypatch)
    monkeypatch.setattr(session_init, "persist_context", lambda *a, **k: None)
    context = _run_session_init(monkeypatch, world, _launch_frame(world, "resume"))
    assert saved_team_name(world) == OLD_TEAM
    assert _realigned_events(world) == []
    assert "PACT moved this session" not in context
