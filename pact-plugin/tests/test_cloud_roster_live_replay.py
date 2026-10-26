"""
Location: pact-plugin/tests/test_cloud_roster_live_replay.py
Summary: Replays a REAL captured spawn sequence through the roster writers
         (dispatch_gate._record_roster_spawn and roster_writer) and checks the
         actor each agent then resolves to.
Used by: pytest.

Source: a live child `claude -p` run (Claude Code 2.1.310) on 2026-10-25,
commit 6ad4d6a, with the branch plugin loaded, a config-less team dir and
CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=2. The ORDER of frames, the tool_use_ids,
the agent_ids, the frame agent_type spellings and the tool_response keys
the writers read are copied from the captured hook stdin. Only the envelope
(session_id, transcript_path, cwd) comes from the shared fixtures, so the
hooks resolve the test team.

Sequence: lead bootstrap spawns the secretary; an unnamed general-purpose
spawn; a named 'ghost' Explore spawn that dispatch_gate admitted and a later
PreToolUse hook DENIED (no SubagentStart, no PostToolUse); alpha and beta in
one message; alpha's nested unnamed Explore (a teammate Pre(Agent) frame with
alpha's agent_id, then its SubagentStart); gamma in the background (its
PostToolUse lands before its SubagentStart); then the lead re-spawns 'ghost'.

The synthesized-frame tests for the same fix live in
test_cloud_roster_meta_claim.py; this file pins that the REAL frame order
and shapes drive the writers to the same outcome.
"""

from __future__ import annotations

import pytest

import dispatch_gate
import roster_writer
from fixtures.actor_frames import cloud_meta, cloud_subagent_frame, write_subagent_meta
from fixtures.roster_frames import (
    post_agent_frame,
    pre_agent_frame,
    read_roster,
    subagent_start_frame,
)
from shared import cloud_roster
from shared.actor_identity import (
    SOURCE_ROSTER_VERIFIED,
    SOURCE_SUBAGENT_META,
    SOURCE_UNATTRIBUTED,
    resolve_actor,
)

TEAM = "test-team"
LEAD = "PACT:pact-orchestrator"

SECRETARY, UNNAMED, ALPHA, BETA = (
    "a0441085bd51e9211", "a0d0b4630f2fe938d", "aeede072b78f7456c", "a6a74380646ec902f")
NESTED, GAMMA, GHOST = "aa13109c7e6b5d341", "aaa4685eb6f2559d4", "a984e15e9e4d54d45"

TU_SECRETARY = "toolu_01PrVUgBso1qzqCgZ5RgAsN4"
TU_UNNAMED = "toolu_011uz5H6LangN6aSjgvyEA2S"
TU_GHOST_DENIED = "toolu_01BKV3aooH4CGzCcJ9KT8N5o"
TU_ALPHA = "toolu_017JW6Bb2xhbnUPsGPMFSDZZ"
TU_BETA = "toolu_01SDuvZydQUyEjWftksSiAna"
TU_NESTED = "toolu_0161pPdJp4guWn2hgtp3o8jF"
TU_GAMMA = "toolu_01FQpc3tSAG7xSTewPhCnSyB"
TU_GHOST = "toolu_01FMeMuGEMdDKcEGGkjBhfLR"

# (event, frame agent_type, frame agent_id, tool_use_id, payload) in captured order.
# pre payload: (name, subagent_type, run_in_background); start: agent_type;
# post payload: the tool_response keys the writer reads.
LIVE_SEQUENCE = [
    ("pre", LEAD, None, TU_SECRETARY, ("secretary", "pact-secretary", False)),
    ("start", None, SECRETARY, None, "pact-secretary"),
    ("post", LEAD, None, TU_SECRETARY,
     {"agentId": SECRETARY, "agentType": "pact-secretary", "status": "completed"}),
    ("pre", LEAD, None, TU_UNNAMED, (None, "general-purpose", False)),
    ("start", None, UNNAMED, None, "general-purpose"),
    ("post", LEAD, None, TU_UNNAMED,
     {"agentId": UNNAMED, "agentType": "general-purpose", "status": "completed"}),
    ("pre", LEAD, None, TU_GHOST_DENIED, ("ghost", "Explore", False)),  # denied later
    ("pre", LEAD, None, TU_ALPHA, ("alpha", "general-purpose", False)),
    ("start", None, ALPHA, None, "general-purpose"),
    ("pre", LEAD, None, TU_BETA, ("beta", "general-purpose", False)),
    ("start", None, BETA, None, "general-purpose"),
    ("pre", "general-purpose", ALPHA, TU_NESTED, (None, "Explore", False)),
    ("start", None, NESTED, None, "Explore"),
    ("post", LEAD, None, TU_BETA,
     {"agentId": BETA, "agentType": "general-purpose", "status": "completed"}),
    ("post", "general-purpose", ALPHA, TU_NESTED,
     {"agentId": NESTED, "agentType": "Explore", "status": "completed"}),
    ("post", LEAD, None, TU_ALPHA,
     {"agentId": ALPHA, "agentType": "general-purpose", "status": "completed"}),
    ("pre", LEAD, None, TU_GAMMA, ("gamma", "general-purpose", True)),
    ("post", LEAD, None, TU_GAMMA,
     {"agentId": GAMMA, "status": "async_launched", "isAsync": True}),
    ("start", None, GAMMA, None, "general-purpose"),
    ("pre", LEAD, None, TU_GHOST, ("ghost", "Explore", False)),
    ("start", None, GHOST, None, "Explore"),
    ("post", LEAD, None, TU_GHOST,
     {"agentId": GHOST, "agentType": "Explore", "status": "completed"}),
]


def _replay(root, until=None):
    """Feed LIVE_SEQUENCE (optionally only up to tool_use_id `until`'s pre)."""
    for event, frame_type, frame_aid, tu, payload in LIVE_SEQUENCE:
        if until is not None and event == "pre" and tu == until:
            return
        if event == "pre":
            name, subagent_type, background = payload
            frame = pre_agent_frame(root, tu, name, subagent_type, agent_type=frame_type)
            frame["tool_input"]["run_in_background"] = background
            if frame_aid is not None:
                frame["agent_id"] = frame_aid
            dispatch_gate._record_roster_spawn("ALLOW", frame["tool_input"], frame)
        elif event == "start":
            roster_writer.handle_subagent_start(subagent_start_frame(root, frame_aid, payload))
        else:
            frame = post_agent_frame(root, tu, payload["agentId"], frame_agent_type=frame_type)
            frame["tool_response"] = dict(payload)
            if frame_aid is not None:
                frame["agent_id"] = frame_aid
            roster_writer.handle_post_agent(frame)


@pytest.fixture
def cloud(pact_context):
    pact_context(team_name=TEAM, session_id="test-session")
    from shared.paths import get_claude_config_dir

    root = get_claude_config_dir()
    (root / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
    return root


def _actor(root, aid, agent_type, meta_name=None):
    if meta_name is not None:
        write_subagent_meta(root, aid, cloud_meta(meta_name, agent_type))
    return resolve_actor(cloud_subagent_frame(root, agent_type, agent_id=aid), TEAM)


def test_live_sequence_writes_the_records_the_live_run_wrote(cloud):
    _replay(cloud)
    kinds = [(r["k"], r["tu"]) for r in read_roster(cloud, TEAM)]
    # The nested Pre(Agent) on alpha's frame is a foreign record, not an intent.
    assert ("foreign", TU_NESTED) in kinds
    assert ("intent", TU_NESTED) not in kinds
    # The denied ghost keeps its orphan intent and nothing attaches to it.
    assert ("intent", TU_GHOST_DENIED) in kinds
    assert not any(k in ("bind", "confirm") and tu == TU_GHOST_DENIED for k, tu in kinds)
    # The unnamed lead spawn writes nothing.
    assert not any(tu == TU_UNNAMED for _, tu in kinds)
    roster = cloud_roster.load_roster(TEAM)
    assert roster.binding_for(NESTED) is None
    for aid, tu, strength in ((SECRETARY, TU_SECRETARY, "confirmed"),
                              (ALPHA, TU_ALPHA, "confirmed"), (BETA, TU_BETA, "confirmed"),
                              (GAMMA, TU_GAMMA, "confirmed"), (GHOST, TU_GHOST, "confirmed")):
        binding = roster.binding_for(aid)
        assert binding is not None and (binding.tool_use_id, binding.strength) == (tu, strength)


@pytest.mark.parametrize("aid,agent_type,name", [
    (SECRETARY, "pact-secretary", "secretary"), (ALPHA, "general-purpose", "alpha"),
    (BETA, "general-purpose", "beta"), (GAMMA, "general-purpose", "gamma"),
    (GHOST, "Explore", "ghost"),
])
def test_live_named_spawns_resolve_roster_verified(cloud, aid, agent_type, name):
    _replay(cloud)
    actor = _actor(cloud, aid, agent_type, meta_name=name)
    assert (actor.name, actor.source) == (name, SOURCE_ROSTER_VERIFIED)


def test_live_nested_agent_without_a_meta_name_stays_unnamed(cloud):
    _replay(cloud)
    write_subagent_meta(cloud, NESTED, {"agentType": "Explore", "spawnDepth": 2,
                                        "parentAgentId": ALPHA, "toolUseId": TU_NESTED})
    actor = _actor(cloud, NESTED, "Explore")
    assert (actor.name, actor.source) == (None, SOURCE_SUBAGENT_META)


@pytest.mark.parametrize("respawned", [False, True], ids=["before-respawn", "after-respawn"])
def test_live_nested_impostor_claiming_ghost_is_unattributed(cloud, respawned):
    _replay(cloud, until=None if respawned else TU_GHOST)
    actor = _actor(cloud, NESTED, "Explore", meta_name="ghost")
    assert (actor.name, actor.source) == (None, SOURCE_UNATTRIBUTED)
