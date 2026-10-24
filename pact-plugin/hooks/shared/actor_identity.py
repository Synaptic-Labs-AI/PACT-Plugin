"""
Location: pact-plugin/hooks/shared/actor_identity.py
Summary: Resolve WHO made a hook frame's tool call (role, name, type) from
         harness-written data only.
Used by: task_lifecycle_gate.py (the self-completion rule and the
         lifecycle_decision journal event).

Inputs, all written by the harness, never by a teammate:
  - the frame's own `agent_type` and `agent_id` (hook stdin);
  - the team config `~/.claude/teams/<team>/config.json` (local only);
  - the subagent's `agent-<agent_id>.meta.json` under `~/.claude/projects/`
    (local and cloud), read through `turn_end_gate.subagent_metadata`.

TRUST BOUNDARY (load-bearing): this module is an AUTHORITY surface. It must
never read the session registry or any helper that reaches it
(`frame_team_and_name`, `teammate_launch_name`, `bind_launcher_identity`,
`resolve_agent_name`, `turn_end_gate.teammate_identity`). Registry values are
self-asserted by teammates and therefore forgeable.
`tests/test_session_registry_trust_partition.py` enforces this with an AST ban.

ROLE IS KEYED ON `agent_type` ONLY (the #812 invariant), through the SSOT
`pact_context.classify_session_role`. `agent_id` is read only to resolve a
NAME, never to decide lead versus teammate.

A TYPE IS NEVER DERIVED FROM A VALUE KNOWN TO BE A NAME. An in-process
teammate's frame carries its member NAME in `agent_type`, and a local
meta.json carries a NAME in `agentType`. Treating either as a type would let
a member named `pact-secretary` be exempted from the self-completion rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import pact_context

ROLE_LEAD = "lead"
# Any non-lead agent frame: an in-process teammate, an Agent-tool subagent,
# or a tmux teammate.
ROLE_TEAMMATE = "teammate"
# No agent_type: a plain or non-PACT primary session.
ROLE_UNKNOWN = "unknown"

SOURCE_LEAD_FRAME = "lead_frame"
SOURCE_INPROCESS_ID = "inprocess_id"          # a<name>-<16hex> + config member
SOURCE_SUBAGENT_META = "subagent_meta"        # a<16hex> + meta.json read
SOURCE_SUBAGENT_NO_META = "subagent_no_meta"  # a<16hex>, meta missing/unreadable
SOURCE_AT_ID = "at_id"                        # name@team (legacy/forward-compat)
SOURCE_FRAME_ONLY = "frame_only"              # teammate role, no usable id
SOURCE_NONE = "none"                          # role unknown

_MAX_NAME_CHARS = 128
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ActorIdentity:
    """Who made the tool call.

    role:       one of the ROLE_* constants.
    name:       member or subagent NAME; None when unresolved.
    agent_type: a TYPE with `PACT:` stripped, never a member name; or None.
    source:     one of the SOURCE_* constants (which resolution step won).
    """

    role: str
    name: str | None
    agent_type: str | None
    source: str

    def journal_view(self) -> dict:
        """Plain dict of the four fields, for the lifecycle_decision event.

        Holds no agent_id, session id or path.
        """
        return {
            "role": self.role,
            "source": self.source,
            "name": self.name,
            "agent_type": self.agent_type,
        }


_UNKNOWN = ActorIdentity(ROLE_UNKNOWN, None, None, SOURCE_NONE)


def resolve_actor(input_data: Any, team_name: str) -> ActorIdentity:
    """Resolve the frame's actor. Never raises.

    Any internal error returns ActorIdentity(ROLE_UNKNOWN, None, None,
    SOURCE_NONE), which the self-completion rule treats as silent.

    Args:
        input_data: parsed hook stdin.
        team_name: the session's team (may be ""), used for config lookups.
    """
    try:
        return _resolve(input_data, team_name if isinstance(team_name, str) else "")
    except Exception:  # noqa: BLE001 — totality is the contract
        return _UNKNOWN


def _resolve(input_data: Any, team_name: str) -> ActorIdentity:
    if not isinstance(input_data, dict):
        return _UNKNOWN
    role = pact_context.classify_session_role(input_data)
    if role == "lead":
        return ActorIdentity(ROLE_LEAD, None, None, SOURCE_LEAD_FRAME)
    if role != "teammate":
        return _UNKNOWN

    # Lazy: both are heavy modules, and a module-level import could open a
    # cycle with intentional_wait or dispatch_helpers.
    from . import background_work, turn_end_gate

    agent_type = input_data.get("agent_type")
    agent_id = input_data.get("agent_id")
    if not isinstance(agent_type, str):
        # classify_session_role said teammate on a truthy non-string type.
        return ActorIdentity(ROLE_TEAMMATE, None, None, SOURCE_FRAME_ONLY)

    # Step 3 / 3': in-process teammate, id "a<name>-<16hex>". The shape is
    # matched HERE first, so agent_type_names_a_member's no-id fallback is
    # never relied on.
    if isinstance(agent_id, str) and re.fullmatch(
        re.escape(f"a{agent_type}-") + "[0-9a-f]{16}", agent_id
    ):
        # agent_id spelled as the frame read: test_background_work pins that
        # every caller passes exactly input_data.get('agent_id').
        if not background_work.agent_type_names_a_member(
            agent_type, team_name, agent_id=input_data.get("agent_id")
        ):
            # agent_type is a NAME on this frame: never use it as a type.
            return ActorIdentity(ROLE_TEAMMATE, None, None, SOURCE_FRAME_ONLY)
        name = agent_type
        actor_type = _config_type(name, team_name) or _meta_type(
            turn_end_gate.subagent_metadata(input_data), name=name
        )
        return ActorIdentity(ROLE_TEAMMATE, name, actor_type, SOURCE_INPROCESS_ID)

    # Step 4: Agent-tool subagent (cloud teammates have this shape too).
    if background_work.subagent_launcher_id(input_data):
        meta = turn_end_gate.subagent_metadata(input_data)
        if not isinstance(meta, dict):
            meta = {}
        name = _valid_name(meta.get("name"))
        actor_type = _meta_type(meta, frame_type=agent_type, name=name)
        source = SOURCE_SUBAGENT_META if meta else SOURCE_SUBAGENT_NO_META
        return ActorIdentity(ROLE_TEAMMATE, name, actor_type, source)

    # Step 5: name@team (legacy / forward-compat).
    if isinstance(agent_id, str) and "@" in agent_id:
        at_name, _, at_team = agent_id.partition("@")
        if at_name and at_team and team_name and at_team.lower() == team_name.lower():
            return ActorIdentity(
                ROLE_TEAMMATE, at_name, _config_type(at_name, team_name), SOURCE_AT_ID
            )

    # Step 6: no usable id (tmux, or an unrecognised id). The frame type is a
    # type unless it names a member (then it is a NAME).
    frame_type = _strip(agent_type)
    if frame_type and _names_member(agent_type, team_name):
        frame_type = None
    return ActorIdentity(ROLE_TEAMMATE, None, frame_type, SOURCE_FRAME_ONLY)


def _meta_type(
    meta: Any, frame_type: Any = None, name: str | None = None
) -> str | None:
    """First usable TYPE from a subagent meta.json, then the frame.

    Precedence: meta customAgentType > spawnedAgentType > agentType (local:
    a NAME, cloud: a type) > the frame's agent_type. `PACT:` is stripped, and
    any candidate equal to `name` is skipped, because it is a name.
    """
    candidates = []
    if isinstance(meta, dict):
        candidates = [
            meta.get("customAgentType"),
            meta.get("spawnedAgentType"),
            meta.get("agentType"),
        ]
    candidates.append(frame_type)
    for value in candidates:
        stripped = _strip(value)
        if stripped and stripped != name and value != name:
            return stripped
    return None


def _config_type(name: str, team_name: str) -> str | None:
    """The team-config `agentType` recorded for member `name`, stripped."""
    for member in pact_context._iter_members(team_name):
        if member.get("name") == name:
            return _strip(member.get("agentType"))
    return None


def _names_member(value: Any, team_name: str) -> bool:
    if not isinstance(value, str) or not value or not team_name:
        return False
    return any(m.get("name") == value for m in pact_context._iter_members(team_name))


def _valid_name(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_NAME_CHARS
        and not _CONTROL_CHARS.search(value)
    ):
        return value
    return None


def _strip(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    stripped = pact_context.strip_pact_namespace(value)
    return stripped or None
