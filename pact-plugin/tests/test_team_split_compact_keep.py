"""
A compaction keeps the team this session recorded.

After a resume realigns PACT to the team the platform is using, every hook
frame still carries the ORIGINAL session id, so the team name session_init
computes from that id is always the pre-resume one. A compaction must not
write it back over the realigned record. An in-process teammate's compaction
arrives lead-shaped (the captured frames carry the lead's agent_type, session
id and transcript path), so the keep is decided by the SessionStart source,
not by whose frame it is: only ``compact`` keeps; ``startup``, ``resume`` and
``clear`` re-derive exactly as before.

Every world here ages the realigned team's task store past the census recency
window and adds a live session of another project. So neither the census nor
a fresh store can be what keeps the name: only the compaction keep can.

session_init.main() runs in process over the real path derivations
(tests/fixtures/team_split.py), with nothing stubbed. The handoff-ordering
cells call that hook's own evaluators, which read the real context file and
task store.

Counter-tests, measured by source-only revert in a scratch copy (cardinality
of this file's run, 21 cases):
  revert session_init.py to the base tree -> 11 failed, 10 passed
  apply the keep to every source -> 3 failed (resume, startup, clear)
  keep the recorded team without consulting the resolver -> 1 failed
  drop the context-module init before the resolve -> 3 failed (the census
    never runs at SessionStart: neither launch realigns and a vanished
    recorded team is kept)
  drop the census's team-activity witness -> 2 failed (the launch control)
  initialise the context module for a rejected session id -> 2 failed
  revert persist_context to always write -> 3 failed (the no-write cells
    and the no-split control)
"""

import io
import json
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_compaction_teammate_sessionstart,
)
from fixtures.team_split import (
    FOREIGN_TEAM,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    reset_context_module,
    saved_team_name,
    split_world,
    stat_snapshot,
    team_line,
    write_task,
)

import session_init

COMPACT_FRAMES = {
    "lead": captured_compaction_lead_sessionstart,
    "lead-shaped teammate": captured_compaction_teammate_sessionstart,
}
GONE_TEAM = "session-deadbeef"


def _frame(world, source, role="lead"):
    frame = COMPACT_FRAMES[role]()
    frame["session_id"] = OLD_SID
    frame["cwd"] = str(world.project_dir)
    frame["source"] = source
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


def _realigned_world(tmp_path, monkeypatch, **overrides):
    """The record already names the realigned team; its store is idle past
    the recency window, and another project's session is live."""
    kwargs = dict(saved_team=NEW_TEAM, live_store_aged=True,
                  foreign="foreign-cwd", claude_md=True)
    kwargs.update(overrides)
    return split_world(tmp_path, monkeypatch, **kwargs)


@pytest.mark.parametrize("role", list(COMPACT_FRAMES))
def test_a_compaction_keeps_the_realigned_team(tmp_path, monkeypatch, role):
    """The record and the CLAUDE.md team line stay on the realigned team, and
    the SessionStart context names that team, never the pre-resume one."""
    world = _realigned_world(tmp_path, monkeypatch)
    block_before = world.claude_md.read_text(encoding="utf-8")
    context = _run_session_init(monkeypatch, world, _frame(world, "compact", role))
    assert saved_team_name(world) == NEW_TEAM
    assert team_line(world.claude_md) == f"- Team: `{NEW_TEAM}`"
    assert world.claude_md.read_text(encoding="utf-8") == block_before
    assert NEW_TEAM in context
    assert OLD_TEAM not in context


@pytest.mark.parametrize("role", list(COMPACT_FRAMES))
def test_a_compaction_that_keeps_the_team_writes_nothing(tmp_path, monkeypatch,
                                                          role):
    """Neither the context file nor the project CLAUDE.md is rewritten: both
    keep their inode and mtime, not just their bytes."""
    world = _realigned_world(tmp_path, monkeypatch)
    before = stat_snapshot(world.context_path, world.claude_md)
    _run_session_init(monkeypatch, world, _frame(world, "compact", role))
    assert stat_snapshot(world.context_path, world.claude_md) == before


@pytest.mark.parametrize("source", ["resume", "startup", "clear"])
def test_other_sources_re_derive_the_team_as_before(tmp_path, monkeypatch,
                                                     source):
    """Boundary pin of today's launch and clear behaviour in the aged-store
    world ONLY: the realigned team's store is idle past the recency window and
    the only fresh team is another project's, so the census finds no winner
    and re-deriving lands on the pre-resume name. Only a compaction keeps; the
    realign returns through the prompt-time write-back once the live store is
    written again. With a fresh live store a launch realigns instead (the two
    launch cells below)."""
    world = _realigned_world(tmp_path, monkeypatch)
    _run_session_init(monkeypatch, world, _frame(world, source))
    assert saved_team_name(world) == OLD_TEAM


@pytest.mark.parametrize("source", ["startup", "resume"])
def test_a_launch_realigns_to_a_fresh_corroborating_team(tmp_path, monkeypatch,
                                                         source):
    """The recorded team's config is gone, this session lived as a team, and
    a fresh live team corroborates as its own: the launch's census picks it,
    so the record names that team from SessionStart on."""
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        foreign="foreign-cwd", claude_md=True)
    _run_session_init(monkeypatch, world, _frame(world, source))
    assert saved_team_name(world) == NEW_TEAM


@pytest.mark.parametrize("source", ["startup", "resume"])
def test_a_launch_without_team_activity_never_realigns(tmp_path, monkeypatch,
                                                       source):
    """Control: the same world, but the journal shows no team activity (a
    session that never lived as a team). The census stays off and the
    recorded team stands."""
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        foreign="foreign-cwd", claude_md=True,
                        journal=("variety_assessed",))
    _run_session_init(monkeypatch, world, _frame(world, source))
    assert saved_team_name(world) == OLD_TEAM


def test_a_compaction_whose_recorded_team_is_gone_takes_the_census_winner(
        tmp_path, monkeypatch):
    """The recorded team's config is absent and a live team corroborates as
    this session's: the compaction realigns to it rather than keeping the
    vanished team."""
    world = split_world(tmp_path, monkeypatch, saved_team=GONE_TEAM,
                        foreign="foreign-cwd", claude_md=True)
    _run_session_init(monkeypatch, world, _frame(world, "compact"))
    assert saved_team_name(world) == NEW_TEAM


def test_a_compaction_whose_recorded_team_is_gone_and_undecided_keeps_it(
        tmp_path, monkeypatch):
    """Control for the cell above: with no census winner the recorded team is
    the default and stands, never the pre-resume computed name."""
    world = split_world(tmp_path, monkeypatch, saved_team=GONE_TEAM,
                        live_store_aged=True, foreign="foreign-cwd",
                        claude_md=True)
    _run_session_init(monkeypatch, world, _frame(world, "compact"))
    assert saved_team_name(world) == GONE_TEAM


def test_a_compaction_keeps_a_recorded_team_without_checking_its_lead_cwd(
        tmp_path, monkeypatch):
    """Stated boundary: the keep reads only this session's own record and
    checks no cwd, because every writer of the record corroborates. A recorded
    team whose config exists is kept even when its lead runs elsewhere. Flip
    this cell with any hardening of the keep."""
    world = split_world(tmp_path, monkeypatch, saved_team=FOREIGN_TEAM,
                        live_store_aged=True, foreign="foreign-cwd",
                        claude_md=True)
    _run_session_init(monkeypatch, world, _frame(world, "compact"))
    assert saved_team_name(world) == FOREIGN_TEAM


def test_a_compaction_without_a_split_changes_nothing(tmp_path, monkeypatch):
    """Control: the recorded team is the session's own (its config names this
    session id). A compaction leaves both files exactly as they were."""
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        old_config=True, live_team=None, claude_md=True)
    before = stat_snapshot(world.context_path, world.claude_md)
    _run_session_init(monkeypatch, world, _frame(world, "compact"))
    assert saved_team_name(world) == OLD_TEAM
    assert stat_snapshot(world.context_path, world.claude_md) == before


def test_completing_a_task_after_a_compaction_reads_the_realigned_store(
        tmp_path, monkeypatch):
    """After the compaction, the handoff-ordering check reads the realigned
    team's task: its handoff is present, so no warning. The pre-resume store
    holds a task with the same id, another subject and no handoff, which a
    check reading the stale team would warn about."""
    world = _realigned_world(tmp_path, monkeypatch)
    write_task(world.tasks / NEW_TEAM, {
        "id": "20", "owner": "auditor", "status": "in_progress",
        "subject": "auditor: observe", "metadata": {"handoff": {"produced": "x"}},
    })
    old_store = world.tasks / OLD_TEAM
    old_store.mkdir(parents=True, exist_ok=True)
    write_task(old_store, {"id": "20", "owner": "reviewer",
                           "status": "in_progress", "subject": "reviewer: old"})
    _run_session_init(monkeypatch, world, _frame(world, "compact"))

    import handoff_ordering_gate
    reset_context_module()
    advisory = handoff_ordering_gate._evaluate({
        "hook_event_name": "PreToolUse",
        "session_id": OLD_SID,
        "agent_type": "PACT:pact-orchestrator",
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "20", "status": "completed"},
    })
    assert advisory is None, advisory


@pytest.mark.parametrize("bad_id", ["unknown-deadbeef", "0001639f\nx"],
                         ids=["sentinel", "control-char"])
def test_a_rejected_session_id_binds_no_session_context(tmp_path, monkeypatch,
                                                        bad_id):
    """The keep initialises the context module so the census can read this
    session's journal, but never for a session id session_init rejects:
    binding a context path from it would point later reads and writes at a
    directory named after the rejected id. The disk-artifact pins in
    test_session_init.py do not see this, because nothing in this run writes
    through the binding."""
    import shared.pact_context as ctx_module
    world = _realigned_world(tmp_path, monkeypatch)
    frame = _frame(world, "compact")
    frame["session_id"] = bad_id
    monkeypatch.chdir(world.project_dir)
    reset_context_module()
    with patch("sys.stdin", io.StringIO(json.dumps(frame))), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit):
            session_init.main()
    bound = ctx_module.is_initialized()
    reset_context_module()
    assert bound is False


def _wiring_frame():
    """A lead TaskUpdate that wires a specialist's work task: owner plus
    addBlockedBy in the same write."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": OLD_SID,
        "agent_type": "PACT:pact-orchestrator",
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "30", "owner": "architect",
                       "addBlockedBy": ["29"]},
    }


def _add_specialist_member(world, team):
    cfg_path = world.teams / team / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["members"].append({"agentId": f"architect@{team}", "name": "architect",
                           "agentType": "pact-architect"})
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")


def test_wiring_an_unstamped_task_after_a_compaction_is_denied(tmp_path,
                                                                monkeypatch):
    """After the compaction the record still names the live team, so the
    dispatch-variety check finds the work task in the live store and denies
    wiring it without a variety stamp."""
    import handoff_ordering_gate
    world = _realigned_world(tmp_path, monkeypatch)
    _add_specialist_member(world, NEW_TEAM)
    write_task(world.tasks / NEW_TEAM, {
        "id": "30", "status": "pending", "subject": "architect: design"})
    _run_session_init(monkeypatch, world, _frame(world, "compact"))
    reset_context_module()
    verdict = handoff_ordering_gate._evaluate_dispatch_variety(_wiring_frame())
    assert verdict and "no metadata.variety stamp" in verdict, verdict


def test_wiring_against_a_stale_record_is_silently_bypassed(tmp_path,
                                                            monkeypatch):
    """Control, and the bypass the compaction keep prevents: with the record
    naming the pre-resume team, whose store does not hold the task, the same
    write passes unchecked."""
    import handoff_ordering_gate
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        live_store_aged=True)
    _add_specialist_member(world, NEW_TEAM)
    write_task(world.tasks / NEW_TEAM, {
        "id": "30", "status": "pending", "subject": "architect: design"})
    reset_context_module()
    assert handoff_ordering_gate._evaluate_dispatch_variety(_wiring_frame()) is None


def test_the_stale_store_world_would_warn(tmp_path, monkeypatch):
    """Control for the cell above: with the record naming the pre-resume team,
    the same completion warns about the old task, so the cell above can tell
    the two records apart."""
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM)
    old_store = world.tasks / OLD_TEAM
    old_store.mkdir(parents=True, exist_ok=True)
    write_task(old_store, {"id": "20", "owner": "reviewer",
                           "status": "in_progress", "subject": "reviewer: old"})
    import handoff_ordering_gate
    reset_context_module()
    advisory = handoff_ordering_gate._evaluate({
        "hook_event_name": "PreToolUse",
        "session_id": OLD_SID,
        "agent_type": "PACT:pact-orchestrator",
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "20", "status": "completed"},
    })
    assert advisory and "reviewer: old" in advisory
