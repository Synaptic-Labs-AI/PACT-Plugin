#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/roster_writer.py
Summary: Writes the cloud roster's `bind` and `confirm` records, which attach a
         spawned agent's harness `agent_id` to the `intent` record
         dispatch_gate wrote from the lead's Agent call.
Used by: hooks.json, under two events:
           - SubagentStart (its own entry, no matcher): `bind`, written before
             the new agent's first turn.
           - PostToolUse matched on `Agent` (lead frames only): `confirm`, from
             the harness-paired `tool_use_id` and `tool_response.agentId`.
         Both write only when shared.cloud_roster.applies(team) holds (a team
         directory with no config.json: the cloud shape). Design:
         docs/architecture/cloud-roster.md §3.2.

SubagentStart binding, in order:
  1. meta join: the subagent's meta.json names a `toolUseId` with an
     unattached intent of the same type and the same name, at spawnDepth 1.
     MEASURED (WP0, CC 2.1.310, cloud): meta.json does NOT exist yet when
     SubagentStart fires, so this path does not fire today. It is kept for a
     harness that writes meta earlier, and every bind records `meta_present`
     so the shipped hook keeps measuring it.
  2. type join: the LATEST spawn event of the frame's type (a lead intent or
     a nested `foreign` spawn, in roster order) when it is an unattached lead
     intent from the last 10 minutes. Measured: each spawn's PreToolUse and
     SubagentStart run back to back, even for two spawns in one lead message
     and for a nested spawn, so the latest same-type event is this start's
     own spawn. After a foreign (nested) spawn of that type, nothing binds,
     so a nested agent cannot take a lead intent; an older orphan intent
     (a spawn another hook or the user denied) no longer blocks the join.
     A lead-overlap race (Pre B, Pre C, Start B) binds B to C's intent; B's
     meta name then disagrees (unattributed), and the confirms correct it.
  3. otherwise nothing; the confirm attaches it later. ACCEPTED RESIDUAL: until
     then that agent resolves meta-only, as before the roster.

PostToolUse confirm: for a foreground spawn this fires at the agent's first
handback; for a background spawn it fires at launch, BEFORE that agent's
SubagentStart (measured), which is why the bind paths look only at
unattached intents.

Why a SubagentStart frame may write `bind`: its stdin is harness-written and
announces a spawn. A teammate could cause one only by spawning a nested
subagent, and the bind is still checked against a lead-written intent and its
type; a later confirm overrides a wrong bind (cloud_roster fold rule 4).
A bind-only binding also names nobody unless the agent's meta.json carries
the same name (actor_identity step 4), so a type join that lands on the wrong
agent (an orphan intent from a denied spawn, see cloud_roster N1) cannot put
the intent's name on it.

SACROSANCT: fail-open. Every path prints suppressOutput and exits 0; this hook
never emits context or a decision.

Input: JSON from stdin (SubagentStart or PostToolUse frame).
Output: {"suppressOutput": true}, always.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

try:
    import shared.pact_context as pact_context
    from shared import cloud_roster, subagent_meta
    from shared.tool_response import extract_tool_response
    _IMPORTS_OK = True
except BaseException:  # noqa: BLE001 — fail-open: a broken import writes nothing
    _IMPORTS_OK = False

# How old an unattached intent may be and still take a type join.
_TYPE_JOIN_WINDOW = timedelta(minutes=10)


def _team(frame: dict) -> str:
    pact_context.init(frame)
    return pact_context.get_team_name()


def handle_subagent_start(frame: dict, teams_dir: str | None = None) -> bool:
    """Append a `bind` for this SubagentStart frame when one join is unique.
    Returns True iff a record was written."""
    # The agent's own a<16hex> id, or "" (an in-process teammate's turn start,
    # or no id): only Agent-tool subagents are bound. Not a role decision.
    agent_id = subagent_meta.subagent_launcher_id(frame)
    agent_type = frame.get("agent_type")
    if not agent_id:
        return False
    if not (isinstance(agent_type, str) and cloud_roster.TYPE_VALUE.fullmatch(agent_type)):
        return False
    team = _team(frame)
    if not cloud_roster.applies(team, teams_dir):
        return False
    roster = cloud_roster.load_roster(team, teams_dir)
    if roster is None or roster.binding_for(agent_id) is not None:
        return False  # no intents yet, or already bound (a resumed agent)

    frame_type = cloud_roster.strip_type(agent_type)
    meta = subagent_meta.subagent_metadata(frame)
    meta_present = bool(meta)
    unattached = [
        i for i in roster.unattached_intents()
        if cloud_roster.strip_type(i.type) == frame_type
    ]

    chosen, via = _meta_join(meta, unattached), cloud_roster.BIND_VIA_META
    if chosen is None:
        chosen, via = _type_join(roster, frame_type), cloud_roster.BIND_VIA_TYPE
    if chosen is None:
        return False
    return cloud_roster.append_record(team, {
        "k": cloud_roster.KIND_BIND, "tu": chosen.tool_use_id, "aid": agent_id,
        "type": agent_type, "via": via, "meta_present": meta_present,
    }, teams_dir)


def _meta_join(meta: dict, unattached: list):
    """The unattached intent meta.json's `toolUseId` names, when its name
    matches and the agent is a direct child of the lead."""
    if not meta or meta.get("spawnDepth") != 1:
        return None
    tool_use_id = meta.get("toolUseId")
    name = subagent_meta.valid_name(meta.get("name"))
    for intent in unattached:
        if intent.tool_use_id == tool_use_id and intent.name == name:
            return intent
    return None


def _type_join(roster, frame_type: str):
    """The intent of the latest same-type spawn event, when that event is an
    unattached lead intent written inside the join window; else None."""
    event = roster.latest_spawn(frame_type)
    if event is None or event.kind != cloud_roster.KIND_INTENT:
        return None  # nothing recorded, or the latest is a nested spawn
    if event.tool_use_id in roster.attached:
        return None
    written = cloud_roster.parse_timestamp(event.ts)
    now = datetime.now(timezone.utc)
    if written is None or not timedelta(0) <= now - written <= _TYPE_JOIN_WINDOW:
        return None
    return roster.intent(event.tool_use_id)


def handle_post_agent(frame: dict, teams_dir: str | None = None) -> bool:
    """Append a `confirm` for a lead PostToolUse(Agent) frame whose
    tool_use_id has an intent. Returns True iff a record was written."""
    if frame.get("tool_name") != "Agent" or not pact_context.is_lead(frame):
        return False
    tool_use_id = frame.get("tool_use_id")
    if not (isinstance(tool_use_id, str) and cloud_roster.TOOL_USE_ID.fullmatch(tool_use_id)):
        return False
    response = extract_tool_response(frame)
    agent_id = response.get("agentId")
    if not (isinstance(agent_id, str) and subagent_meta.SUBAGENT_ID.fullmatch(agent_id)):
        return False
    team = _team(frame)
    if not cloud_roster.applies(team, teams_dir):
        return False
    roster = cloud_roster.load_roster(team, teams_dir)
    if roster is None or roster.intent(tool_use_id) is None:
        return False  # an unnamed or denied spawn: nothing to confirm
    record = {"k": cloud_roster.KIND_CONFIRM, "tu": tool_use_id, "aid": agent_id}
    agent_type = response.get("agentType")
    if isinstance(agent_type, str) and cloud_roster.TYPE_VALUE.fullmatch(agent_type):
        record["type"] = agent_type
    return cloud_roster.append_record(team, record, teams_dir)


def main() -> None:
    try:
        frame = json.load(sys.stdin)
        if _IMPORTS_OK and isinstance(frame, dict):
            event = frame.get("hook_event_name")
            if event == "SubagentStart":
                handle_subagent_start(frame)
            elif event == "PostToolUse":
                handle_post_agent(frame)
    except Exception:  # noqa: BLE001 — fail-open on every path
        pass
    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
