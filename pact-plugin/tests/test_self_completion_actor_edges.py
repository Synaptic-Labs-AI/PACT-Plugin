"""
Location: pact-plugin/tests/test_self_completion_actor_edges.py
Summary: TEST-phase edge cases for the self-completion rule (rule A) and its
         actor resolution (shared/actor_identity.py), beyond the CODE-phase
         verification tests.
Used by: the pact-plugin test suite.

Sibling of tests/test_task_lifecycle_gate.py (self-completion block) and
tests/test_actor_identity.py. Kept separate so the primary files' tight
advisory-list assertions stay uncluttered (pact-testing-strategies
§Sibling-file convention).

Areas (dispatch for Task #30, item 5):
  1. How often the unverified advisory fires: exactly one per completion
     of an owned task by an unresolved non-lead frame, and never on any
     other write.
  2. meta.json missing, malformed, the wrong JSON type, empty, or holding
     an invalid name.
  3. An exempt TYPE spoofed through a NAME (subagent name, task metadata,
     meta `agentType` equal to the name, frame `agent_type` equal to the
     name).
  4. Empty, absent or non-string owner.
  5. Signal tasks: both halves of the pattern are needed.
  6. Lead-shaped teammate compaction frames: the CAPTURED shape resolves as
     the lead, so the rule is silent (a documented residual, pinned here).
  7. The CAPTURED P2 subagent TaskUpdate frame (CC 2.1.310) resolves through
     the same path as the synthesized cloud frames.
"""

from __future__ import annotations

import json

import pytest

import task_lifecycle_gate as tlg
from fixtures.actor_frames import (
    SUBAGENT_ID,
    cloud_meta,
    cloud_subagent_frame,
    inprocess_frame,
    lead_frame,
    plain_frame,
    write_subagent_meta,
    write_team_config,
)
from fixtures.role_frames import (
    captured_compaction_teammate_postcompact,
    captured_compaction_teammate_precompact,
    captured_compaction_teammate_sessionstart,
    captured_posttooluse_subagent_taskupdate_completed,
    captured_pretooluse_subagent_taskupdate_first_call,
    captured_taskcompleted_subagent,
)
from shared.actor_identity import (
    ROLE_LEAD,
    ROLE_TEAMMATE,
    SOURCE_SUBAGENT_META,
    SOURCE_SUBAGENT_NO_META,
    resolve_actor,
)

TEAM = "test-team"

_HANDOFF = {
    "produced": "x", "decisions": "x", "reasoning_chain": "x",
    "uncertainty": "x", "integration": "x", "open_questions": "x",
}


def _with_task(frame: dict, task_id: str, owner, metadata: dict | None = None,
               subject: str = "implement foo") -> dict:
    """Attach the post-state task to a completion frame's tool_response."""
    meta = {"handoff": dict(_HANDOFF)}
    meta.update(metadata or {})
    frame["tool_input"] = {"taskId": task_id, "status": "completed"}
    frame["tool_response"] = {"task": {
        "id": task_id, "subject": subject, "owner": owner, "metadata": meta,
    }}
    return frame


def _config_root():
    from shared.paths import get_claude_config_dir
    return get_claude_config_dir()


def _self_rules(advisories) -> list[str]:
    return [rule for rule, _ in advisories if rule.startswith("self_completion")]


@pytest.fixture
def writebacks(monkeypatch):
    """Record _writeback_dispute calls instead of writing task files."""
    calls: list = []
    monkeypatch.setattr(tlg, "_writeback_dispute", lambda task_id: calls.append(task_id) or True)
    return calls


@pytest.fixture
def team(pact_context):
    pact_context(team_name=TEAM, session_id="test-session")
    return _config_root()


def _meta_path(root):
    return root / "projects" / "-proj" / "11111111-2222-3333-4444-555555555555" / "subagents" / (
        f"agent-{SUBAGENT_ID}.meta.json"
    )


# =============================================================================
# 1. How often the unverified advisory fires
# =============================================================================


def test_unverified_fires_once_per_completion_and_never_writes_back(team, writebacks):
    """One unresolved completion gives exactly one advisory: no duplicate and
    no escalation to the hard self_completion rule."""
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder")
    advisories = tlg.evaluate_lifecycle(payload)
    assert _self_rules(advisories) == ["self_completion_unverified"]
    assert writebacks == []


def test_unverified_message_names_task_owner_and_source(team, writebacks):
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder")
    msgs = [m for r, m in tlg.evaluate_lifecycle(payload) if r == "self_completion_unverified"]
    assert len(msgs) == 1
    assert "Task 7" in msgs[0]
    assert "'backend-coder'" in msgs[0]
    assert SOURCE_SUBAGENT_NO_META in msgs[0]
    assert SUBAGENT_ID not in msgs[0]  # never leaks the agent id


@pytest.mark.parametrize("status", ["in_progress", "pending", "deleted"])
def test_unverified_never_fires_on_non_completion_status(team, writebacks, status):
    payload = cloud_subagent_frame(team, "pact-backend-coder")
    payload["tool_input"] = {"taskId": "7", "status": status}
    payload["tool_response"] = {"task": {"id": "7", "subject": "x", "owner": "backend-coder",
                                         "metadata": {}}}
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []
    assert writebacks == []


def test_unverified_never_fires_on_owner_only_write(team, writebacks):
    """A claim (owner change, no status) is not a completion."""
    payload = cloud_subagent_frame(team, "pact-backend-coder")
    payload["tool_input"] = {"taskId": "7", "owner": "backend-coder"}
    payload["tool_response"] = {}
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_unverified_never_fires_on_taskcreate(team, writebacks):
    payload = cloud_subagent_frame(team, "pact-backend-coder")
    payload["tool_name"] = "TaskCreate"
    payload["tool_input"] = {"subject": "x", "description": "y"}
    payload["tool_response"] = {}
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_unverified_fires_for_general_purpose_subagent_completing_lead_owned_task(team, writebacks):
    """The noise case the coder flagged (MEDIUM): a lead-spawned
    general-purpose subagent with no meta.json completes a task the lead
    owns. The rule cannot tell this apart from a self-completion, so it
    fires the soft advisory. Pinned so a change in frequency is a visible,
    deliberate decision. The P1 and P2 probes saw meta.json present at a
    subagent's first tool call, so in practice this needs a missing or
    unreadable meta file."""
    payload = _with_task(cloud_subagent_frame(team, "general-purpose"), "7", "lead")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion_unverified"]
    assert writebacks == []


def test_general_purpose_subagent_with_meta_completing_lead_task_is_silent(team, writebacks):
    """The same completion with meta.json present (the shape P2 captured):
    the name resolves, differs from the owner, and rule A stays silent."""
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("probe-sub", "general-purpose"))
    payload = _with_task(cloud_subagent_frame(team, "general-purpose"), "7", "lead")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []
    assert writebacks == []


@pytest.mark.parametrize("make_frame", [lead_frame, plain_frame], ids=["lead", "plain"])
def test_unverified_never_fires_for_lead_or_plain_frames(team, writebacks, make_frame):
    payload = _with_task(make_frame(), "7", "backend-coder")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_completed_frames_without_team_are_silent(writebacks):
    """No PACT context (plain session, tmux teammate): team_name and the
    disk-fallback task read are empty, so the owner is empty and the rule is
    silent. The P3b probe (plain session, branch plugin) confirmed this live:
    no journal and no advisory."""
    payload = cloud_subagent_frame(_config_root(), "pact-backend-coder")
    payload["tool_response"] = {}
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


# =============================================================================
# 2. meta.json missing, malformed, wrong type, empty, invalid name
# =============================================================================


@pytest.mark.parametrize("raw", [
    "{not json",
    "",
    "[1, 2, 3]",
    '"a string"',
    "null",
    "42",
], ids=["truncated", "empty-file", "list", "string", "null", "number"])
def test_malformed_meta_degrades_to_no_meta(team, raw):
    path = _meta_path(team)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    actor = resolve_actor(cloud_subagent_frame(team, "pact-backend-coder"), TEAM)
    assert actor.role == ROLE_TEAMMATE
    assert actor.name is None
    assert actor.source == SOURCE_SUBAGENT_NO_META
    assert actor.agent_type == "pact-backend-coder"


@pytest.mark.parametrize("raw", ["{not json", "[]", "{}"], ids=["truncated", "list", "empty-dict"])
def test_malformed_meta_gives_unverified_not_disputed(team, writebacks, raw):
    path = _meta_path(team)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion_unverified"]
    assert writebacks == []


def test_malformed_meta_with_exempt_frame_type_is_silent(team, writebacks):
    """Cloud secretary whose meta.json is unreadable: the frame TYPE still
    exempts it (1b with name None). P2/P3c captured the frame agent_type as
    a TYPE ('PACT:pact-secretary'), which is what makes this safe."""
    path = _meta_path(team)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    payload = _with_task(cloud_subagent_frame(team, "PACT:pact-secretary"), "7", "secretary")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []
    assert writebacks == []


def test_meta_with_no_name_key_gives_unverified(team, writebacks):
    meta = cloud_meta("backend-coder", "pact-backend-coder")
    del meta["name"]
    write_subagent_meta(team, SUBAGENT_ID, meta)
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder")
    actor = resolve_actor(payload, TEAM)
    assert actor.source == SOURCE_SUBAGENT_META and actor.name is None
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion_unverified"]
    assert writebacks == []


@pytest.mark.parametrize("bad_name", ["", "x" * 129, "back\nend", "nul\x00", 7, None, ["a"]])
def test_meta_invalid_name_never_matches_owner(team, writebacks, bad_name):
    """An invalid meta name is discarded, never compared with the owner,
    even when the owner holds the identical bad value."""
    meta = cloud_meta("backend-coder", "pact-backend-coder")
    meta["name"] = bad_name
    write_subagent_meta(team, SUBAGENT_ID, meta)
    owner = bad_name if isinstance(bad_name, str) and bad_name else "backend-coder"
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", owner)
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion_unverified"]
    assert writebacks == []


def test_meta_name_at_max_length_is_accepted(team, writebacks):
    name = "n" * 128
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta(name, "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", name)
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion"]
    assert writebacks == ["7"]


def test_meta_for_a_different_agent_id_is_not_read(team, writebacks):
    """meta.json is keyed by the frame's own agent_id; another subagent's
    file in the same folder is never borrowed."""
    write_subagent_meta(team, "a" + "f" * 16, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder")
    assert resolve_actor(payload, TEAM).name is None
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion_unverified"]


@pytest.mark.parametrize("bad_id", ["a../../x", "a0123456789abcde", "A0123456789ABCDEF", "a0123456789abcdef0"])
def test_unrecognised_subagent_id_shapes_do_not_read_meta(team, writebacks, bad_id):
    """Only a<16 lowercase hex> is a subagent id. Anything else falls past
    step 4, and meta.json is never read (no path built from it)."""
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    frame = cloud_subagent_frame(team, "pact-backend-coder", agent_id=bad_id)
    actor = resolve_actor(frame, TEAM)
    assert actor.name is None
    assert actor.source != SOURCE_SUBAGENT_META


# =============================================================================
# 3. An exempt TYPE spoofed through a NAME
# =============================================================================


def test_subagent_named_like_exempt_type_is_not_exempt(team, writebacks):
    """Cloud subagent NAMED 'pact-secretary' whose harness type is
    general-purpose: its type comes from meta/frame, never from its name,
    so its self-completion draws the hard advisory."""
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("pact-secretary", "general-purpose"))
    payload = _with_task(cloud_subagent_frame(team, "general-purpose"), "7", "pact-secretary")
    actor = resolve_actor(payload, TEAM)
    assert (actor.name, actor.agent_type) == ("pact-secretary", "general-purpose")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion"]
    assert writebacks == ["7"]


def test_meta_agenttype_equal_to_name_is_not_a_type(team, writebacks):
    """A meta whose agentType equals the subagent's name (the LOCAL shape),
    with the frame type also equal to the name (the C5 'NAME' hypothesis):
    no type resolves, so 1b cannot exempt it."""
    write_subagent_meta(team, SUBAGENT_ID, {"name": "pact-secretary", "agentType": "pact-secretary"})
    payload = _with_task(cloud_subagent_frame(team, "pact-secretary"), "7", "pact-secretary")
    actor = resolve_actor(payload, TEAM)
    assert actor.name == "pact-secretary"
    assert actor.agent_type is None
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion"]
    assert writebacks == ["7"]


def test_qualified_meta_type_equal_to_unqualified_name_is_not_a_type(team, writebacks):
    """'PACT:pact-secretary' strips to the name 'pact-secretary'. The
    stripped value equals the name, so it is skipped as well."""
    write_subagent_meta(team, SUBAGENT_ID, {"name": "pact-secretary", "agentType": "PACT:pact-secretary"})
    payload = _with_task(cloud_subagent_frame(team, "PACT:pact-secretary"), "7", "pact-secretary")
    assert resolve_actor(payload, TEAM).agent_type is None
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion"]


@pytest.mark.parametrize("task_meta", [
    {"agent_type": "pact-secretary"},
    {"agentType": "pact-secretary"},
    {"actor_agent_type": "pact-secretary"},
    {"owner_agent_type": "PACT:pact-secretary"},
])
def test_task_metadata_cannot_claim_the_exempt_type(team, writebacks, task_meta):
    """Surface 1b reads the actor's harness type, never the task. A teammate
    writing an exempt type into its own task's metadata gains nothing."""
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder",
                         metadata=task_meta)
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion"]
    assert writebacks == ["7"]


def test_exempt_actor_completing_another_owners_task_is_silent_when_name_resolves(team, writebacks):
    """1b requires actor name == owner when the name resolves. A cloud
    secretary completing someone else's task is not exempt; rule A is
    silent anyway because the actor is not the owner (peer completion is
    rule B's scope)."""
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("secretary", "pact-secretary"))
    payload = _with_task(cloud_subagent_frame(team, "pact-secretary"), "7", "backend-coder")
    from shared.intentional_wait import is_self_complete_exempt
    task = payload["tool_response"]["task"]
    assert not is_self_complete_exempt(task, TEAM, actor_agent_type="pact-secretary",
                                       actor_name="secretary")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_owner_spoofed_to_secretary_by_non_exempt_inprocess_teammate(team, writebacks):
    """An in-process coder sets owner='secretary' and completes. The real
    secretary is a member with agentType pact-secretary, so surface 1
    (owner's config type) exempts the TASK. The actor is the coder, whose
    name differs from the owner, so rule A is silent. Pinned as a residual:
    rule A only polices actor == owner; a spoofed owner is rule B's scope."""
    write_team_config(team, TEAM, [
        {"name": "secretary", "agentType": "pact-secretary"},
        {"name": "coder-1", "agentType": "pact-backend-coder"},
    ])
    payload = _with_task(inprocess_frame(team, "coder-1"), "7", "secretary")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []
    assert writebacks == []


# =============================================================================
# 4. Empty, absent or non-string owner
# =============================================================================


@pytest.mark.parametrize("owner", ["", None, 0, False, [], {}], ids=repr)
def test_empty_or_falsy_owner_is_silent(team, writebacks, owner):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", owner)
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []
    assert writebacks == []


@pytest.mark.parametrize("owner", ["", None])
def test_empty_owner_is_silent_even_when_unresolved(team, writebacks, owner):
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", owner)
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_non_string_owner_is_silent(team, writebacks):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", ["backend-coder"])
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_owner_with_surrounding_space_does_not_match(team, writebacks):
    """Owner matching is exact: 'backend-coder ' is not 'backend-coder'."""
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder ")
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


def test_decision_actor_owner_match_false_on_empty_owner(team, writebacks):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "")
    decision: dict = {}
    tlg.evaluate_lifecycle(payload, decision=decision)
    assert decision["actor"]["owner_match"] is False
    assert decision["actor"]["name"] == "backend-coder"


# =============================================================================
# 5. Signal tasks
# =============================================================================


@pytest.mark.parametrize("signal_type", ["blocker", "algedonic"])
def test_cloud_signal_task_self_completion_is_exempt(team, writebacks, signal_type):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder",
                         metadata={"completion_type": "signal", "type": signal_type})
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []
    assert writebacks == []


@pytest.mark.parametrize("signal_type", ["blocker", "algedonic"])
def test_unresolved_signal_task_completion_is_exempt(team, writebacks, signal_type):
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder",
                         metadata={"completion_type": "signal", "type": signal_type})
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == []


@pytest.mark.parametrize("task_meta", [
    {"completion_type": "signal"},
    {"completion_type": "signal", "type": "other"},
    {"completion_type": "signal", "type": "Blocker"},
    {"type": "blocker"},
    {"completion_type": "Signal", "type": "blocker"},
], ids=["no-type", "other-type", "case-type", "no-completion-type", "case-completion-type"])
def test_half_signal_pattern_is_not_exempt(team, writebacks, task_meta):
    write_subagent_meta(team, SUBAGENT_ID, cloud_meta("backend-coder", "pact-backend-coder"))
    payload = _with_task(cloud_subagent_frame(team, "pact-backend-coder"), "7", "backend-coder",
                         metadata=task_meta)
    assert _self_rules(tlg.evaluate_lifecycle(payload)) == ["self_completion"]
    assert writebacks == ["7"]


# =============================================================================
# 6. Lead-shaped teammate compaction frames (captured 2026-09-14)
# =============================================================================


@pytest.mark.parametrize("captured", [
    captured_compaction_teammate_precompact,
    captured_compaction_teammate_sessionstart,
    captured_compaction_teammate_postcompact,
])
def test_captured_teammate_compaction_frames_resolve_as_lead(captured):
    """An in-process teammate's compaction frames are lead-shaped: lead
    agent_type, no agent_id. resolve_actor keys role on agent_type only
    (#812), so they resolve as the lead."""
    actor = resolve_actor(captured(), TEAM)
    assert actor.role == ROLE_LEAD


def test_lead_shaped_teammate_completion_is_silent_documented_residual(team, writebacks):
    """If a teammate's TaskUpdate frame were lead-shaped (the shape its
    compaction frames take), its self-completion would be silent: rule A
    cannot see it. Only compaction frames were captured in this shape, not
    a TaskUpdate. Pinned so that a future capture showing lead-shaped
    TaskUpdate frames from teammates is recognised as a gap and not a pass.
    The real fields of the captured frame are carried into the completion
    frame."""
    compaction = captured_compaction_teammate_postcompact()
    frame = {k: compaction[k] for k in ("agent_type", "session_id", "cwd", "transcript_path")}
    frame.update(hook_event_name="PostToolUse", tool_name="TaskUpdate")
    payload = _with_task(frame, "7", "backend-coder")
    decision: dict = {}
    advisories = tlg.evaluate_lifecycle(payload, decision=decision)
    assert decision["actor"]["role"] == "lead"
    assert _self_rules(advisories) == []
    assert writebacks == []


# =============================================================================
# 7. CAPTURED P2 subagent TaskUpdate frames (CC 2.1.310, 2026-10-24)
# =============================================================================


def _place_captured(frame: dict, root) -> dict:
    """Point the captured frame's placeholder paths at the test config root
    and write the meta beside it, keeping the captured key set intact."""
    from fixtures.actor_frames import LEAD_SID, lead_transcript

    frame["session_id"] = LEAD_SID
    frame["transcript_path"] = lead_transcript(root)
    return frame


def test_captured_subagent_posttooluse_resolves_through_meta(team, writebacks):
    frame = _place_captured(captured_posttooluse_subagent_taskupdate_completed(), team)
    write_subagent_meta(team, frame["agent_id"], cloud_meta("probe-sub", frame["agent_type"]))
    actor = resolve_actor(frame, TEAM)
    assert actor.role == ROLE_TEAMMATE
    assert actor.source == SOURCE_SUBAGENT_META
    assert actor.name == "probe-sub"
    assert actor.agent_type == "general-purpose"


def test_captured_subagent_posttooluse_self_completion_fires(team, writebacks):
    frame = _place_captured(captured_posttooluse_subagent_taskupdate_completed(), team)
    write_subagent_meta(team, frame["agent_id"], cloud_meta("probe-sub", frame["agent_type"]))
    task_id = frame["tool_input"]["taskId"]
    frame["tool_response"] = {"task": {"id": task_id, "subject": "sub-owned",
                                       "owner": "probe-sub", "metadata": {}}}
    assert _self_rules(tlg.evaluate_lifecycle(frame)) == ["self_completion"]
    assert writebacks == [task_id]


def test_captured_subagent_frame_has_no_teammate_or_team_name():
    """C7 input: neither the subagent's TaskUpdate frames nor its
    TaskCompleted frame carry teammate_name or team_name; the TaskCompleted
    frame does carry agent_id and agent_type."""
    for frame in (captured_posttooluse_subagent_taskupdate_completed(),
                  captured_pretooluse_subagent_taskupdate_first_call(),
                  captured_taskcompleted_subagent()):
        assert "teammate_name" not in frame and "team_name" not in frame
        assert frame["agent_id"] and frame["agent_type"] == "general-purpose"


def test_captured_frames_carry_capture_provenance():
    for frame in (captured_posttooluse_subagent_taskupdate_completed(),
                  captured_pretooluse_subagent_taskupdate_first_call(),
                  captured_taskcompleted_subagent()):
        method = frame["_meta"]["capture_method"]
        assert "2.1.310" in method and "SYNTHETIC" in method
        assert json.dumps(frame).count("probe-sub") == 0  # names live only in meta
