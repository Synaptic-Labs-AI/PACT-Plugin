"""
The split-team hooks, each run as its own process, agree on the session's team.

In a live session every hook is a separate python3 process: nothing one hook
computes reaches the next except through the files on disk. These cells run
the real hook scripts as child processes, in the order the platform fires
them, against a scratch config root and project dir (CLAUDE_CONFIG_DIR, HOME,
CLAUDE_PROJECT_DIR), so any state a hook keeps only in memory, or any path
that resolves differently in a fresh process, shows up here and not in the
in-process cells.

The worlds are built by tests/fixtures/team_split.py; no resolver is patched.

Counter-tests, measured by source-only mutation in a scratch copy
(cardinality of this file's run, 6 cases):
  revert the census to count-before-corroborate -> 1 failed (the resume chain
    never leaves the pre-resume team)
  revert task_claim_gate's in-process check to `session_id == lead_session_id`
    -> 1 failed (the other teammate's task is auto-claimed on disk)
  revert session_init's compaction keep to the computed default -> 1 failed
    (the completion after the compaction warns about the old team's task)
  call bootstrap_prompt_gate's claim check without launch=True -> 1 failed
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from fixtures.role_frames import (
    captured_compaction_lead_sessionstart,
    captured_lead_userpromptsubmit_qualified,
    constructed_pretooluse_teammate_inprocess,
)
from fixtures.team_split import (
    AGED_MTIME,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    read_journal,
    saved_team_name,
    split_world,
    team_line,
    write_task,
)

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SEARCH = "has no metadata.handoff yet"
_FIRST_PROMPT_SID = "dddd2222-3333-4444-8555-666677778888"


def _run(world, hook, frame):
    """Run hooks/<hook>.py as a child process; return its parsed stdout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(HOME=str(world.tmp), CLAUDE_CONFIG_DIR=str(world.root),
               CLAUDE_PROJECT_DIR=str(world.project_dir),
               CLAUDE_PLUGIN_ROOT=str(HOOKS.parent))
    frame = {**frame, "cwd": str(world.project_dir)}
    proc = subprocess.run([sys.executable, str(HOOKS / f"{hook}.py")],
                          input=json.dumps(frame), capture_output=True,
                          text=True, env=env, cwd=str(world.project_dir),
                          timeout=120)
    assert proc.returncode in (0, 2), (hook, proc.returncode, proc.stderr[-2000:])
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _register(world, sid, values):
    path = world.root / "pact-sessions" / ".teammate-registry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for value in values:
            fh.write(json.dumps({"session_id": sid, "value": value}) + "\n")


def _lead_frame(factory, **fields):
    frame = factory()
    frame.update(session_id=OLD_SID, **fields)
    return frame


def _split(tmp_path, monkeypatch, **kw):
    journal = ("task_metadata_snapshot", "session_end",
               {"type": "session_start", "session_id": OLD_SID,
                "project_dir": str(tmp_path / "project"), "source": "startup"})
    return split_world(tmp_path, monkeypatch, journal=journal, claude_md=True,
                       foreign="foreign-cwd", live_members=("coder-a", "coder-b"),
                       **kw)


def test_a_resume_split_converges_across_separate_hook_processes(
        tmp_path, monkeypatch):
    """Resume, the first lead prompt, then an in-process teammate's tool
    call: each a separate process. The record and the CLAUDE.md team line
    name the live team, the move is journaled once, and the teammate's call
    gets the generic claim nudge without claiming the other teammate's
    task."""
    # The pre-resume team left its task store behind (its config is gone),
    # as a real split does.
    world = _split(tmp_path, monkeypatch, old_tasks=[
        {"id": "202", "owner": "coder-a", "status": "in_progress"}])
    pending = write_task(world.tasks / NEW_TEAM, {
        "id": "5", "owner": "coder-b", "status": "pending",
        "subject": "coder-b: work"})
    _register(world, OLD_SID, [f"coder-a@{NEW_TEAM}", f"coder-b@{NEW_TEAM}"])

    _run(world, "session_init",
         _lead_frame(captured_compaction_lead_sessionstart, source="resume"))
    _run(world, "bootstrap_marker_writer",
         _lead_frame(captured_lead_userpromptsubmit_qualified))
    teammate = constructed_pretooluse_teammate_inprocess("Edit")
    teammate.update(session_id=OLD_SID, agent_type="coder-a")
    claim = _run(world, "task_claim_gate", teammate)

    assert saved_team_name(world) == NEW_TEAM
    assert team_line(world.claude_md) == f"- Team: `{NEW_TEAM}`"
    moves = [(e["from"], e["to"]) for e in read_journal(world.session_dir)
             if e.get("type") == "team_realigned"]
    assert moves == [(OLD_TEAM, NEW_TEAM)]
    assert json.loads(pending.read_text(encoding="utf-8"))["status"] == "pending"
    nudge = claim.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "pre-assigned Task B" in nudge and "auto-claimed" not in nudge


def test_a_tmux_teammates_call_still_auto_claims_its_own_task(tmp_path,
                                                              monkeypatch):
    """Control: a separate-process teammate with its own registered id and
    one pending task of its own is auto-claimed, so the cell above is not
    passing because the claim path stopped working in a child process."""
    world = _split(tmp_path, monkeypatch, saved_team=NEW_TEAM)
    own = write_task(world.tasks / NEW_TEAM, {
        "id": "6", "owner": "coder-b", "status": "pending",
        "subject": "coder-b: work"})
    tmux_sid = "ffff8888-bbbb-4ccc-9ddd-eeeeeeeeeeee"
    _register(world, tmux_sid, [f"coder-b@{NEW_TEAM}"])
    teammate = constructed_pretooluse_teammate_inprocess("Edit")
    teammate.update(session_id=tmux_sid, agent_type="coder-b")
    teammate.pop("agent_id", None)
    _run(world, "task_claim_gate", teammate)
    assert json.loads(own.read_text(encoding="utf-8"))["status"] == "in_progress"


def _completion(task_id):
    return {"hook_event_name": "PreToolUse", "session_id": OLD_SID,
            "agent_type": "PACT:pact-orchestrator", "tool_name": "TaskUpdate",
            "tool_input": {"taskId": task_id, "status": "completed"}}


def _same_id_tasks(world):
    write_task(world.tasks / NEW_TEAM, {
        "id": "20", "owner": "coder-a", "status": "in_progress",
        "subject": "coder-a: implement",
        "metadata": {"handoff": {"produced": "x"}}})
    old_store = world.tasks / OLD_TEAM
    old_store.mkdir(parents=True, exist_ok=True)
    write_task(old_store, {"id": "20", "owner": "reviewer",
                           "status": "in_progress", "subject": "reviewer: old"})
    # Writing the task freshened the live store; age it again, or the census
    # would realign a stale record and the keep would go untested.
    os.utime(world.tasks / NEW_TEAM, (AGED_MTIME, AGED_MTIME))


def _warning(output):
    return output.get("hookSpecificOutput", {}).get("additionalContext", "")


def test_a_completion_after_a_compaction_reads_the_live_store(tmp_path,
                                                              monkeypatch):
    """A lead compaction in an idle split session, then the lead completing
    a task whose id also exists in the pre-resume store: the handoff-ordering
    check reads the live task (handoff present), so it says nothing."""
    world = _split(tmp_path, monkeypatch, saved_team=NEW_TEAM,
                   live_store_aged=True)
    _same_id_tasks(world)
    _run(world, "session_init",
         _lead_frame(captured_compaction_lead_sessionstart, source="compact"))
    assert saved_team_name(world) == NEW_TEAM
    assert _SEARCH not in _warning(_run(world, "handoff_ordering_gate",
                                        _completion("20")))


def test_a_completion_against_a_stale_record_warns(tmp_path, monkeypatch):
    """Control: the record names the pre-resume team, so the same completion
    in a child process reads the old team's same-id task and warns."""
    world = _split(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                   live_store_aged=True)
    _same_id_tasks(world)
    warning = _warning(_run(world, "handoff_ordering_gate", _completion("20")))
    assert _SEARCH in warning and "reviewer: old" in warning


def test_a_first_prompt_without_a_recorded_start_surfaces_the_predecessors_work(
        tmp_path, monkeypatch):
    """A lead whose SessionStart recorded nothing: its first prompt, in its
    own process, records the session and reads the predecessor the CLAUDE.md
    block names. The predecessor ended with in-progress tasks in its recorded
    team's store, so the claim names them and the marker lands in the new
    session's journal."""
    journal = (
        {"type": "session_start", "session_id": OLD_SID, "project_dir": "p",
         "source": "startup"},
        "session_end")
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        old_config=True, live_team=None, journal=journal,
                        claude_md=True, old_tasks=[
                            {"id": "202", "owner": "coder-a",
                             "status": "in_progress"},
                            {"id": "201", "owner": "preparer",
                             "status": "completed"}])
    frame = captured_lead_userpromptsubmit_qualified()
    frame.update(session_id=_FIRST_PROMPT_SID)
    output = _run(world, "bootstrap_prompt_gate", frame)
    text = json.dumps(output)
    assert "Work was in progress" in text
    assert f"in-progress tasks 202 in `tasks/{OLD_TEAM}/`" in text
    own = world.session_dir.parent / _FIRST_PROMPT_SID
    assert [e for e in read_journal(own)
            if e.get("type") == "session_resumption_surfaced"], text[:400]


def test_the_hook_scripts_run_in_this_file_are_the_plugins_own(tmp_path):
    """Precondition for every cell above: the scripts exist where the child
    processes start them."""
    for hook in ("session_init", "bootstrap_marker_writer", "task_claim_gate",
                 "handoff_ordering_gate", "bootstrap_prompt_gate"):
        assert (HOOKS / f"{hook}.py").is_file(), hook
