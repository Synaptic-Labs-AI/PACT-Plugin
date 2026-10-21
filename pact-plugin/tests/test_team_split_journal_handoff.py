"""
The journal records this team's handoffs after a split, not the old team's.

After a resume the new team's task ids restart, so the old and the new task
stores can hold a task with the same id. The completion-time handoff record
reads the task from the store of the team the session RECORDS. When a
compaction wrote the pre-resume team back over a realigned record, every
completion read the old team's same-id task and journaled ITS handoff: the
journal looked complete and held another team's content, so a harvest that
trusted it saved the wrong records.

This file drives the whole incident chain over the real path derivations
(tests/fixtures/team_split.py), each hook through its own entry point:
a resume into a split, the first lead prompt (the write-back realigns the
record), the live store going idle, a lead compaction, then the lead
completing a task whose id exists in both stores. The journaled handoff must
be the realigned team's.

Counter-tests, measured by source-only revert in a scratch copy (cardinality
of this file's 3-case run): restoring session_init's computed default at a
compaction -> 1 failed (the chain cell journals the old team's handoff);
disabling the prompt-time write-back -> 1 failed (at the chain cell's
realign precondition: without it the record never leaves the old team).
"""

import io
import json
import os
from unittest.mock import patch


from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_lead_posttooluse_taskupdate_completed,
    captured_lead_userpromptsubmit_qualified,
)
from fixtures.team_split import (
    AGED_MTIME,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    read_journal,
    reset_context_module,
    saved_team_name,
    split_world,
    write_task,
)

_TASK_ID = "34"
_OLD_HANDOFF = {"produced": "the old team's review", "decisions": "old",
                "reasoning_chain": "old", "uncertainty": "old",
                "integration": "old", "open_questions": "old"}
_NEW_HANDOFF = {"produced": "this team's implementation", "decisions": "new",
                "reasoning_chain": "new", "uncertainty": "new",
                "integration": "new", "open_questions": "new"}


def _world(tmp_path, monkeypatch, *, saved_team):
    journal = ("task_metadata_snapshot", "session_end",
               {"type": "session_start", "session_id": OLD_SID,
                "project_dir": str(tmp_path / "project"), "source": "resume"})
    world = split_world(tmp_path, monkeypatch, saved_team=saved_team,
                        foreign="foreign-cwd", claude_md=True, journal=journal,
                        live_members=("coder-a",))
    old_store = world.tasks / OLD_TEAM
    old_store.mkdir(parents=True, exist_ok=True)
    write_task(old_store, {"id": _TASK_ID, "owner": "reviewer",
                           "status": "completed", "subject": "reviewer: review",
                           "metadata": {"handoff": _OLD_HANDOFF}})
    write_task(world.tasks / NEW_TEAM, {
        "id": _TASK_ID, "owner": "coder-a", "status": "completed",
        "subject": "coder-a: implement",
        "metadata": {"handoff": _NEW_HANDOFF}})
    return world


def _main(monkeypatch, world, module, frame):
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        try:
            module.main()
        except SystemExit:
            pass
    reset_context_module()


def _prompt(monkeypatch, world):
    import bootstrap_marker_writer
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=OLD_SID, cwd=str(world.project_dir))
    _main(monkeypatch, world, bootstrap_marker_writer, frame)


def _compact(monkeypatch, world):
    import session_init
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=OLD_SID, cwd=str(world.project_dir), source="compact")
    _main(monkeypatch, world, session_init, frame)


def _complete(monkeypatch, world):
    import task_lifecycle_gate
    frame = captured_lead_posttooluse_taskupdate_completed()
    frame.update(session_id=OLD_SID, cwd=str(world.project_dir))
    frame["tool_input"]["taskId"] = _TASK_ID
    frame["tool_response"]["taskId"] = _TASK_ID
    _main(monkeypatch, world, task_lifecycle_gate, frame)


def _journaled_handoffs(world):
    return [e["handoff"] for e in read_journal(world.session_dir)
            if e.get("type") == "agent_handoff"
            and str(e.get("task_id")) == _TASK_ID]


def test_after_realign_and_compaction_the_journal_holds_this_teams_handoff(
        tmp_path, monkeypatch):
    world = _world(tmp_path, monkeypatch, saved_team=OLD_TEAM)
    _prompt(monkeypatch, world)
    assert saved_team_name(world) == NEW_TEAM, "precondition: the prompt realigned"
    store = world.tasks / NEW_TEAM
    os.utime(store, (AGED_MTIME, AGED_MTIME))  # the live store goes idle
    _compact(monkeypatch, world)
    _complete(monkeypatch, world)
    assert _journaled_handoffs(world) == [_NEW_HANDOFF]


def test_a_record_naming_the_old_team_journals_no_handoff(tmp_path,
                                                                monkeypatch):
    """Control, REKEYED by the resolver seam: with the record on the
    pre-resume team while BOTH stores live, the substrate is the
    both-DISAGREE cell (aligned identity-match names the live team, the
    record names the old one, both dirs exist) — the resolver fails CLOSED,
    the emit suppresses, and NO handoff is journaled from either store. The
    pre-migration control journaled the old team's handoff via the raw
    persisted read — the #1020 divergence class the resolver closes."""
    world = _world(tmp_path, monkeypatch, saved_team=OLD_TEAM)
    _complete(monkeypatch, world)
    assert _journaled_handoffs(world) == []


def test_a_realigned_record_journals_this_teams_handoff(tmp_path, monkeypatch):
    """The same completion with the record already on the live team."""
    world = _world(tmp_path, monkeypatch, saved_team=NEW_TEAM)
    _complete(monkeypatch, world)
    assert _journaled_handoffs(world) == [_NEW_HANDOFF]
