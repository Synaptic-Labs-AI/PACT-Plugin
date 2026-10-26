"""
One check decides whether a frame runs in the lead's session, and it holds
after a team split.

After a resume the platform's team config names a ``leadSessionId`` that no
hook frame carries: the lead and every in-process teammate keep the original
session id. Comparing a frame's session id against that config therefore read
every in-process teammate as a separate-process (tmux) teammate: the claim
gate auto-claimed the last-registered teammate's task for whoever called it,
teammate journal writes were skipped, and registry lookups under the shared id
named the wrong teammate. A frame now counts as the lead's session when its
session id is the one this session's lead context file records, OR the team
config's ``leadSessionId``. A tmux teammate has its own id and no context file,
so it never counts.

Every cell runs over the real path derivations (tests/fixtures/team_split.py):
the context file, the team config, the task store, the registry and the
journal are real files under the per-test tmp tree. No resolver is patched.

Counter-tests, measured by source-only revert of each mechanism in a scratch
copy (cardinality of this file's 25-case run):
  drop the context-file leg from is_lead_session_frame -> 7 failed
  drop the context-file leg from is_canonical_journal_frame -> 1 failed
    (the in-process teammate's handoff no longer reaches the journal)
  drop the team-config leg -> 1 failed
  revert task_claim_gate's in_process to `session_id == lead_session_id`
    -> 1 failed (the other teammate's task is auto-claimed)
  revert background_work's teammate-name check to the old leadSessionId
    compare -> 4 failed (both consumer cells among them: the failed
    launch is recorded under the last registrant, because the identity
    binder reads the registry itself and this check is its only guard)
  drop the step-3.5 gate in resolve_agent_name -> 1 failed
  remove the registry writer's lead-context skip -> 1 failed
  widen the registry glob to any session's context -> 1 failed
  add a persisted field to the context -> 1 failed
"""

import json

import pytest

from fixtures.role_frames import constructed_pretooluse_teammate_inprocess
from fixtures.team_split import (
    NEW_SID,
    NEW_TEAM,
    OLD_SID,
    OLD_TEAM,
    TMUX_SID,
    read_journal,
    reset_context_module,
    split_world,
    write_task,
)

_MEMBERS = ("coder-a", "coder-b")
_HANDOFF = {
    "produced": "rows", "decisions": "real seams", "reasoning_chain": "split",
    "uncertainty": "none", "integration": "staged", "open_questions": "none",
}


def _realigned(tmp_path, monkeypatch, **kw):
    """The record names the live team; teammates are members of it."""
    kw.setdefault("live_members", _MEMBERS)
    return split_world(tmp_path, monkeypatch, saved_team=NEW_TEAM, **kw)


def _no_split(tmp_path, monkeypatch):
    """The session's own team: its config names this session id."""
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM,
                        old_config=True, live_team=None)
    cfg_path = world.teams / OLD_TEAM / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["members"] += [{"agentId": f"{m}@{OLD_TEAM}", "name": m}
                       for m in _MEMBERS]
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return world


def _register_rows(world, sid, values):
    """Append registry rows {sid -> value} in order (last one wins on read)."""
    from shared.session_registry import get_registry_path
    path = get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for value in values:
            fh.write(json.dumps({"session_id": sid, "value": value}) + "\n")


def _teammate_frame(sid=OLD_SID, name="coder-a"):
    frame = constructed_pretooluse_teammate_inprocess("Edit")
    frame["session_id"] = sid
    frame["agent_type"] = name
    return frame


def _is_lead_session(frame, team):
    import shared.pact_context as ctx_module
    reset_context_module()
    return ctx_module.is_lead_session_frame(frame, team)


# -- the check itself ---------------------------------------------------------


def test_after_a_split_the_recorded_session_id_counts(tmp_path, monkeypatch):
    """The frame's id is the one the lead context records; the team config
    names another id. The frame is in the lead's session."""
    _realigned(tmp_path, monkeypatch)
    assert _is_lead_session({"session_id": OLD_SID}, NEW_TEAM) is True


def test_the_team_configs_lead_session_id_counts_without_a_context(
        tmp_path, monkeypatch):
    """No lead context exists for the frame's id, but the team config names
    it as the lead's session. The frame is in the lead's session."""
    _realigned(tmp_path, monkeypatch)
    assert _is_lead_session({"session_id": NEW_SID}, NEW_TEAM) is True


def test_a_session_matching_both_counts(tmp_path, monkeypatch):
    """No split: the context and the config name the same id."""
    _no_split(tmp_path, monkeypatch)
    assert _is_lead_session({"session_id": OLD_SID}, OLD_TEAM) is True


def test_a_tmux_teammate_never_counts(tmp_path, monkeypatch):
    """A separate-process teammate: its own id, no context file under it, and
    not the config's lead id."""
    _realigned(tmp_path, monkeypatch)
    assert _is_lead_session({"session_id": TMUX_SID}, NEW_TEAM) is False


@pytest.mark.parametrize("frame", [{"session_id": ""}, {}, {"session_id": 7}],
                         ids=["empty", "missing", "non-string"])
def test_a_frame_without_a_usable_session_id_never_counts(tmp_path,
                                                          monkeypatch, frame):
    """Control: no usable id never matches, whatever the context holds."""
    _realigned(tmp_path, monkeypatch)
    assert _is_lead_session(frame, NEW_TEAM) is False


def test_the_recorded_session_id_counts_when_the_team_config_is_unreadable(
        tmp_path, monkeypatch):
    """The context leg stands alone: the team has no config at all."""
    _realigned(tmp_path, monkeypatch)
    assert _is_lead_session({"session_id": OLD_SID}, "session-nocfg000") is True


# -- the claim gate -----------------------------------------------------------


def test_the_claim_gate_never_auto_claims_another_teammates_task(tmp_path,
                                                                 monkeypatch):
    """Two in-process teammates registered under the shared session id; the
    last-registered one has a pending, owned, unblocked task. The OTHER
    teammate's tool call gets the generic nudge, and that task stays pending
    on disk."""
    import task_claim_gate
    world = _realigned(tmp_path, monkeypatch)
    task_path = write_task(world.tasks / NEW_TEAM, {
        "id": "5", "owner": "coder-b", "status": "pending",
        "subject": "coder-b: work"})
    _register_rows(world, OLD_SID, [f"coder-a@{NEW_TEAM}", f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    advisory = task_claim_gate._evaluate(_teammate_frame(name="coder-a"))
    assert advisory == task_claim_gate._GENERIC_CLAIM_NUDGE
    assert json.loads(task_path.read_text(encoding="utf-8"))["status"] == "pending"


def test_the_claim_gate_still_auto_claims_for_a_tmux_teammate(tmp_path,
                                                              monkeypatch):
    """Control: a tmux teammate with its own registered id and one pending
    task of its own is still auto-claimed, so the cell above is not passing
    because the tmux branch stopped working."""
    import task_claim_gate
    world = _realigned(tmp_path, monkeypatch)
    task_path = write_task(world.tasks / NEW_TEAM, {
        "id": "6", "owner": "coder-b", "status": "pending",
        "subject": "coder-b: work"})
    _register_rows(world, TMUX_SID, [f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    task_claim_gate._evaluate(_teammate_frame(sid=TMUX_SID, name="coder-b"))
    assert json.loads(task_path.read_text(encoding="utf-8"))["status"] == (
        "in_progress")


# -- teammate journal writes --------------------------------------------------


def _handoff_write_frame(sid):
    return {
        "hook_event_name": "PostToolUse",
        "agent_type": "coder-a",
        "session_id": sid,
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "7", "metadata": {"handoff": _HANDOFF}},
        "tool_response": {"task": {"id": "7"}},
    }


def _all_journal_events(world):
    events = []
    for journal in (world.root / "pact-sessions").rglob("session-journal.jsonl"):
        events += read_journal(journal.parent)
    return events


@pytest.mark.parametrize("sid,expected", [(OLD_SID, 1), (TMUX_SID, 0)],
                         ids=["in-process teammate", "tmux control"])
def test_an_in_process_teammates_handoff_reaches_the_journal(
        tmp_path, monkeypatch, sid, expected):
    """A teammate writes a handoff onto its completed task: the in-process
    teammate's write lands in the lead's journal; a tmux teammate's does not
    (it is not the lead's journal process)."""
    import task_lifecycle_gate
    world = _realigned(tmp_path, monkeypatch)
    write_task(world.tasks / NEW_TEAM, {
        "id": "7", "owner": "coder-a", "status": "completed",
        "subject": "coder-a: implement"})
    reset_context_module()
    frame = _handoff_write_frame(sid)
    import shared.pact_context as ctx_module
    ctx_module.init(frame)  # the hook's main() runs init before evaluating
    task_lifecycle_gate.evaluate_lifecycle(frame)
    handoffs = [e for e in _all_journal_events(world)
                if e.get("type") == "agent_handoff"]
    assert len(handoffs) == expected, handoffs


# -- background-work name resolution ------------------------------------------


@pytest.mark.parametrize("agent_type", ["pact-backend-coder", "general"],
                         ids=["agent-type-not-a-member", "unknown-agent-type"])
def test_a_lead_session_frame_is_not_named_from_the_registry(
        tmp_path, monkeypatch, agent_type):
    """A frame in the lead's session with no agent_id and an agent_type that
    names no member (constructed: whether a teammate's failure frame carries
    agent_id is unmeasured). Registry rows under the shared id name the last
    registrant, so the frame must resolve to no teammate rather than to it."""
    from shared.background_work import teammate_launch_name
    world = _realigned(tmp_path, monkeypatch)
    _register_rows(world, OLD_SID, [f"coder-a@{NEW_TEAM}", f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    frame = {"session_id": OLD_SID, "agent_type": agent_type}
    assert teammate_launch_name(frame, NEW_TEAM) == ""


def test_a_member_named_frame_resolves_to_its_own_name(tmp_path, monkeypatch):
    """Control: an in-process teammate whose agent_type is its member name
    resolves to itself, unchanged."""
    from shared.background_work import teammate_launch_name
    world = _realigned(tmp_path, monkeypatch)
    _register_rows(world, OLD_SID, [f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    assert teammate_launch_name(
        {"session_id": OLD_SID, "agent_type": "coder-a"}, NEW_TEAM) == "coder-a"


def test_a_tmux_teammate_is_named_from_the_registry(tmp_path, monkeypatch):
    """Control: a tmux teammate (own id, no context) still resolves through
    its own registry row."""
    from shared.background_work import teammate_launch_name
    world = _realigned(tmp_path, monkeypatch)
    _register_rows(world, TMUX_SID, [f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    assert teammate_launch_name(
        {"session_id": TMUX_SID, "agent_type": "pact-backend-coder"},
        NEW_TEAM) == "coder-b"


# The two consumers of that resolution, driven through their own entry points.
# The frames are CONSTRUCTED, not captured: an agent_type that names no member,
# with no agent_id, is the shape that reaches the registry lookup. Whether a
# teammate's PostToolUseFailure frame carries agent_id is unmeasured, so the
# failure-frame cell pins the worst case.

_NON_MEMBER = "general-teammate"


def _background_bash(event):
    frame = {
        "hook_event_name": event, "session_id": OLD_SID,
        "agent_type": _NON_MEMBER, "tool_name": "Bash",
        "tool_input": {"command": "sleep 100", "run_in_background": True},
    }
    if event == "PostToolUseFailure":
        frame.update(error="exit 1", is_interrupt=False, duration_ms=0)
    return frame


def test_the_wait_filler_advisory_reads_a_split_like_no_split(tmp_path,
                                                              monkeypatch):
    """A background Bash launch in the lead's session, two registry rows under
    the shared id: the launch advisory decides exactly as it does in the same
    session without a split, rather than binding the last registrant."""
    import wait_filler_gate
    world = _realigned(tmp_path, monkeypatch)
    _register_rows(world, OLD_SID, [f"coder-a@{NEW_TEAM}", f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    split_verdict = wait_filler_gate.launch_advisory_applies(
        _background_bash("PreToolUse"))
    reset_context_module()

    control_tmp = tmp_path / "no-split"
    control_tmp.mkdir()
    control = _no_split(control_tmp, monkeypatch)
    _register_rows(control, OLD_SID, [f"coder-a@{OLD_TEAM}", f"coder-b@{OLD_TEAM}"])
    reset_context_module()
    control_verdict = wait_filler_gate.launch_advisory_applies(
        _background_bash("PreToolUse"))
    reset_context_module()
    assert control_verdict is False, "precondition: the no-split control"
    assert split_verdict is control_verdict


def test_a_failed_background_launch_is_not_charged_to_the_last_registrant(
        tmp_path, monkeypatch):
    """A PostToolUseFailure background Bash frame in the lead's session with no
    agent_id: track_files' registry write records nothing under the
    last-registered teammate's name."""
    from shared.background_work import record_background_launch, registry_path
    world = _realigned(tmp_path, monkeypatch)
    # The last registrant owns in-progress work, so a mis-bound launch has a
    # task to anchor to and would be recorded.
    write_task(world.tasks / NEW_TEAM, {
        "id": "8", "owner": "coder-b", "status": "in_progress",
        "subject": "coder-b: work"})
    _register_rows(world, OLD_SID, [f"coder-a@{NEW_TEAM}", f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    record_background_launch(_background_bash("PostToolUseFailure"))
    reset_context_module()
    path = registry_path(NEW_TEAM)
    text = path.read_text(encoding="utf-8") if path and path.exists() else ""
    assert "coder-b" not in text, text


# -- turn-end role ------------------------------------------------------------


def test_a_stop_in_the_lead_session_is_the_lead(tmp_path, monkeypatch):
    """A Stop frame in the lead's session whose agent_type is no lead
    spelling and no member: after a split it is still the lead."""
    from shared import turn_end_gate
    _realigned(tmp_path, monkeypatch)
    reset_context_module()
    role, _, team = turn_end_gate.resolve_role({
        "hook_event_name": "Stop", "session_id": OLD_SID,
        "agent_type": "custom-orchestrator"})
    assert (role, team) == (turn_end_gate.ROLE_LEAD, NEW_TEAM)


def test_a_tmux_stop_is_not_the_lead(tmp_path, monkeypatch):
    """Control: a Stop frame with a tmux id is not the lead."""
    from shared import turn_end_gate
    world = _realigned(tmp_path, monkeypatch)
    _register_rows(world, TMUX_SID, [f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    role, _, _ = turn_end_gate.resolve_role({
        "hook_event_name": "Stop", "session_id": TMUX_SID,
        "agent_type": "custom-orchestrator"})
    assert role != turn_end_gate.ROLE_LEAD


# -- registry writer ----------------------------------------------------------


def _registry_rows():
    from shared.session_registry import get_registry_path
    path = get_registry_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _seed_context_under(world, slug, sid):
    path = world.root / "pact-sessions" / slug / sid / "pact-session-context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": sid}), encoding="utf-8")


def test_registration_under_a_lead_session_id_is_skipped(tmp_path, monkeypatch):
    """A lead context exists for the registering id, under a project slug
    other than this one: the id is a lead session, so no row is written."""
    from shared import session_registry
    world = _realigned(tmp_path, monkeypatch)
    _seed_context_under(world, "another-project", TMUX_SID)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", TMUX_SID)
    session_registry.register(f"coder-b@{NEW_TEAM}")
    assert [r for r in _registry_rows() if r["session_id"] == TMUX_SID] == []


def test_registration_of_a_tmux_teammate_is_written(tmp_path, monkeypatch):
    """Control: no lead context under the id; a lead context for ANOTHER id
    (this session's) must not suppress the row."""
    from shared import session_registry
    _realigned(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", TMUX_SID)
    session_registry.register(f"coder-b@{NEW_TEAM}")
    assert [r["value"] for r in _registry_rows()
            if r["session_id"] == TMUX_SID] == [f"coder-b@{NEW_TEAM}"]


# -- agent-name resolution ----------------------------------------------------


def test_a_lead_session_frame_is_not_named_by_the_last_registry_row(
        tmp_path, monkeypatch):
    """Five rows under the shared id: a lead-session frame with no
    agent_name or agent_id is named from its agent_type, not the last row."""
    import shared.pact_context as ctx_module
    world = _realigned(tmp_path, monkeypatch,
                       live_members=tuple(f"coder-{i}" for i in range(1, 6)))
    _register_rows(world, OLD_SID,
                   [f"coder-{i}@{NEW_TEAM}" for i in range(1, 6)])
    reset_context_module()
    name = ctx_module.resolve_agent_name(
        {"session_id": OLD_SID, "agent_type": "pact-backend-coder"})
    assert name == "backend-coder"


def test_a_tmux_frame_is_named_from_its_registry_row(tmp_path, monkeypatch):
    """Control: a tmux frame still takes its own registry row's name."""
    import shared.pact_context as ctx_module
    world = _realigned(tmp_path, monkeypatch)
    _register_rows(world, TMUX_SID, [f"coder-b@{NEW_TEAM}"])
    reset_context_module()
    name = ctx_module.resolve_agent_name(
        {"session_id": TMUX_SID, "agent_type": "pact-backend-coder"})
    assert name == "coder-b"


# -- no new persisted field ---------------------------------------------------


def test_a_realign_adds_no_field_to_the_session_context(tmp_path, monkeypatch):
    """After the prompt-time write-back realigns the record, the context file
    holds exactly the fields it held before: the lead-session set is derived,
    never stored. (`harness` joined the SCHEMA with the non-TTY stamp work —
    every context carries it from build_context_cache, realign or not, so it
    is part of the expected set rather than a realign-added field.)"""
    import bootstrap_marker_writer
    world = split_world(tmp_path, monkeypatch, saved_team=OLD_TEAM)
    reset_context_module()
    import shared.pact_context as ctx_module
    frame = {"session_id": OLD_SID, "agent_type": "PACT:pact-orchestrator",
             "hook_event_name": "UserPromptSubmit"}
    ctx_module.init(frame)
    bootstrap_marker_writer._write_back_aligned_team_name(frame)
    context = json.loads(world.context_path.read_text(encoding="utf-8"))
    assert context["team_name"] == NEW_TEAM
    assert set(context) == {"team_name", "session_id", "project_dir",
                            "plugin_root", "started_at", "process_key",
                            "harness", "lead_address"}
