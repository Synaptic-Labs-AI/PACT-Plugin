"""
A launch that finds work in flight surfaces a resumption claim.

A plain resume in the middle of a workflow left no record that the session
was resuming one: the resumption marker was written only when a pause or
refresh claim surfaced. The secretary keys its Working Memory hold on that
marker, so it rebuilt the block in the middle of the feature. A launch
(``startup`` or ``resume``) now also surfaces a claim when the previous
session's journal holds a workflow phase that started and never finished, or
its recorded team's task store holds in-progress tasks. A compaction or a
clear never does, a pause or refresh claim still wins, and a previous session
that is still live is never read as finished work.

session_init.main() runs in process over the real path derivations
(tests/fixtures/team_split.py); the marker is read back from the journal.

Counter-tests, measured by source-only mutation in a scratch copy
(cardinality of this file's run, 14 cases):
  remove the in-flight arm -> 7 failed
  treat a /clear as a launch -> 1 failed (the measured clear shape)
  drop the ended-predecessor check -> 1 failed
  read the recorded team after this launch rewrote it -> 1 failed
  let the in-flight claim outrank a refresh claim -> 1 failed
  ignore unfinished phases -> 3 failed
  keep open phases across a consolidation -> 1 failed
  compare the own dir by raw string -> 1 failed
  call the first-prompt check without launch=True -> 1 failed
"""

import io
import json
import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from fixtures.role_frames import captured_compaction_lead_sessionstart
from fixtures.team_split import (
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    clear_frame,
    clear_world,
    read_journal,
    reset_context_module,
    run_hook,
    saved_team_name,
    split_world,
)

import session_init

_CLAIM = "Work was in progress"
_IN_FLIGHT = [
    {"id": "215", "status": "in_progress", "owner": "coder-a"},
    {"id": "202", "status": "in_progress", "owner": "auditor"},
    {"id": "201", "status": "completed", "owner": "preparer"},
]
_DONE = [{"id": "201", "status": "completed", "owner": "preparer"}]
_STARTUP_SID = "dddd2222-3333-4444-8555-666677778888"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _frame(world, source, session_id=OLD_SID):
    frame = captured_compaction_lead_sessionstart()
    frame.update(session_id=session_id, cwd=str(world.project_dir),
                 source=source)
    return frame


def _run(monkeypatch, world, frame):
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


def _claimed_ids(context):
    """The task ids the claim names, read from its own clause: a bare
    substring check also matches the digits in the test's tmp paths."""
    found = re.search(r"in-progress tasks ([\d, ]+) in `tasks/", context)
    return set(found.group(1).split(", ")) if found else set()


def _surfaced(session_dir):
    return [e for e in read_journal(session_dir)
            if e.get("type") == "session_resumption_surfaced"]


def _world(tmp_path, monkeypatch, *, old_tasks=(), journal=None, **kw):
    """The session recorded OLD and ran its work there; the CLAUDE.md
    session block names this session's dir, so a resume reads it back."""
    if journal is None:
        journal = ("task_metadata_snapshot", "session_end")
    return split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                       old_tasks=old_tasks, journal=journal, claude_md=True,
                       live_store_aged=True, **kw)


def test_a_resume_with_in_progress_tasks_surfaces_a_claim(tmp_path,
                                                          monkeypatch):
    """The recorded team's store holds in-progress tasks: the resume names
    them, and the resumption marker is journaled."""
    world = _world(tmp_path, monkeypatch, old_tasks=_IN_FLIGHT)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert _CLAIM in context
    assert _claimed_ids(context) == {"202", "215"}
    assert len(_surfaced(world.session_dir)) == 1


def test_the_own_dir_is_recognised_through_another_spelling(tmp_path,
                                                            monkeypatch):
    """The CLAUDE.md block names this session's dir through a symlinked
    parent, so its spelling differs from the dir the context module derives.
    It is still this session's own dir: the claim names the recorded team's
    in-progress tasks rather than being refused as a live predecessor's."""
    from fixtures.team_split import write_session_block
    world = _world(tmp_path, monkeypatch, old_tasks=_IN_FLIGHT)
    link = tmp_path / "config-link"
    link.symlink_to(world.root, target_is_directory=True)
    spelled = link / world.session_dir.relative_to(world.root)
    assert str(spelled) != str(world.session_dir)
    write_session_block(OLD_SID, OLD_TEAM, spelled, world.plugin_root)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert _CLAIM in context and f"tasks/{OLD_TEAM}/" in context
    assert len(_surfaced(world.session_dir)) == 1


def test_a_resume_with_an_unfinished_phase_surfaces_a_claim(tmp_path,
                                                            monkeypatch):
    """A phase started and never finished, no in-progress task: the resume
    still surfaces a claim."""
    journal = ("task_metadata_snapshot",
               {"type": "phase_transition", "phase": "CODE", "status": "started"},
               "session_end")
    world = _world(tmp_path, monkeypatch, old_tasks=_DONE, journal=journal)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert _CLAIM in context
    assert len(_surfaced(world.session_dir)) == 1


def test_a_finished_phase_surfaces_nothing(tmp_path, monkeypatch):
    """Control for the cell above: the same phase completed later."""
    journal = ("task_metadata_snapshot",
               {"type": "phase_transition", "phase": "CODE", "status": "started"},
               {"type": "phase_transition", "phase": "CODE", "status": "completed"},
               "session_end")
    world = _world(tmp_path, monkeypatch, old_tasks=_DONE, journal=journal)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert _CLAIM not in context
    assert _surfaced(world.session_dir) == []


def test_a_phase_open_before_a_consolidation_surfaces_nothing(tmp_path,
                                                              monkeypatch):
    """A phase started, and the session consolidated after it (a wrap-up or
    a refresh): the workflow was closed out, so the launch claims nothing."""
    journal = ("task_metadata_snapshot",
               {"type": "phase_transition", "phase": "CODE", "status": "started"},
               "session_consolidated",
               "session_end")
    world = _world(tmp_path, monkeypatch, old_tasks=_DONE, journal=journal)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert _CLAIM not in context
    assert _surfaced(world.session_dir) == []


def test_the_first_prompt_also_surfaces_a_predecessors_work(tmp_path,
                                                            monkeypatch):
    """When SessionStart recorded nothing, the first lead prompt records the
    session and reads the predecessor the CLAUDE.md block names. An ended
    predecessor with an unfinished phase is claimed there too, and the
    marker lands in the new session's journal."""
    import bootstrap_prompt_gate
    journal = (
        {"type": "session_start", "session_id": OLD_SID, "project_dir": "p",
         "source": "startup"},
        {"type": "phase_transition", "phase": "CODE", "status": "started"},
        "session_end")
    world = _world(tmp_path, monkeypatch, old_tasks=_DONE, journal=journal)
    own_dir = world.session_dir.parent / _STARTUP_SID
    own_dir.mkdir(parents=True)
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    text = bootstrap_prompt_gate._record_unrecorded_lead(
        {"session_id": _STARTUP_SID, "agent_type": "PACT:pact-orchestrator"},
        str(own_dir))
    reset_context_module()
    assert _CLAIM in text
    assert len(_surfaced(own_dir)) == 1


def test_a_resume_with_no_work_in_flight_surfaces_nothing(tmp_path,
                                                          monkeypatch):
    world = _world(tmp_path, monkeypatch, old_tasks=_DONE)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert _CLAIM not in context
    assert _surfaced(world.session_dir) == []


def test_a_compaction_never_surfaces_the_claim(tmp_path, monkeypatch):
    """Work in flight, but the session did not launch: no claim."""
    world = _world(tmp_path, monkeypatch, old_tasks=_IN_FLIGHT)
    context = _run(monkeypatch, world, _frame(world, "compact"))
    assert _CLAIM not in context
    assert _surfaced(world.session_dir) == []


@pytest.mark.parametrize("source,claimed", [("clear", False), ("startup", True)])
def test_a_clear_never_surfaces_the_work_of_the_session_it_replaced(
        tmp_path, monkeypatch, source, claimed):
    """The measured clear shape: a new session id and dir, after a session
    that ended with tasks in flight in its recorded team's store. A /clear
    is not a launch, so it claims nothing; a startup in the same place does
    (the control)."""
    world = clear_world(tmp_path, monkeypatch, prev_team=OLD_TEAM,
                        old_tasks=_IN_FLIGHT, live_store_aged=True)
    frame = clear_frame(world, captured_compaction_lead_sessionstart)
    frame["source"] = source
    context = run_hook(world, "session_init", frame)
    assert (_CLAIM in context) is claimed
    assert (len(_surfaced(world.clear_dir)) == 1) is claimed


def test_the_claim_reads_the_team_recorded_before_this_launch(tmp_path,
                                                              monkeypatch):
    """The launch realigns the record to the live team, whose store is empty;
    the in-progress tasks sit in the team recorded before the launch, and the
    claim names them."""
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        old_tasks=_IN_FLIGHT, claude_md=True,
                        foreign="foreign-cwd")
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert saved_team_name(world) == NEW_TEAM, (
        "precondition: this launch realigned the record")
    assert _CLAIM in context and _claimed_ids(context) == {"202", "215"}


def test_a_refresh_claim_wins_over_work_in_flight(tmp_path, monkeypatch):
    """A refresh claim and in-progress tasks: the refresh claim surfaces and
    the in-flight claim does not, so a session gets one resumption prompt."""
    journal = ("task_metadata_snapshot",
               {"type": "session_refreshed", "ts": _now(),
                "consolidation_completed": True, "halt_active": False,
                "feature_subject": "the refreshed feature"},
               "session_end")
    world = _world(tmp_path, monkeypatch, old_tasks=_IN_FLIGHT, journal=journal)
    context = _run(monkeypatch, world, _frame(world, "resume"))
    assert "the refreshed feature" in context
    assert _CLAIM not in context


def _startup_after(tmp_path, monkeypatch, prev_journal):
    """The previous session (OLD) recorded the CLAUDE.md block; a NEW session
    starts in the same project and reads OLD's journal as its predecessor."""
    world = _world(tmp_path, monkeypatch, old_tasks=_DONE, journal=prev_journal)
    return world, _run(monkeypatch, world,
                       _frame(world, "startup", session_id=_STARTUP_SID))


def test_a_startup_reads_a_finished_predecessors_unfinished_phase(
        tmp_path, monkeypatch):
    """Control for the cell below: the predecessor ended with a phase open,
    so a new session's startup surfaces the claim."""
    _, context = _startup_after(tmp_path, monkeypatch, (
        {"type": "session_start", "session_id": OLD_SID, "project_dir": "p",
         "source": "startup"},
        {"type": "phase_transition", "phase": "CODE", "status": "started"},
        "session_end"))
    assert _CLAIM in context


def test_a_startup_never_reads_a_live_predecessor(tmp_path, monkeypatch):
    """The predecessor has a phase open but has not ended (no session_end
    after its last session_start): it may be a parallel live session, so a
    new session's startup surfaces nothing."""
    _, context = _startup_after(tmp_path, monkeypatch, (
        {"type": "session_start", "session_id": OLD_SID, "project_dir": "p",
         "source": "startup"},
        {"type": "phase_transition", "phase": "CODE", "status": "started"}))
    assert _CLAIM not in context
