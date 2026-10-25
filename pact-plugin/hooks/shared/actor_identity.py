"""
Location: pact-plugin/hooks/shared/actor_identity.py
Summary: Resolve WHO made a hook frame's tool call (role, name, type) from
         harness-written data only.
Used by: task_lifecycle_gate.py (the self-completion rule and the
         lifecycle_decision journal event) and task_claim_gate.py (the
         exemption check on the actor's own tasks).

Inputs, all AUTHORED by the harness:
  - the frame's own `agent_type` and `agent_id` (hook stdin);
  - the team config `~/.claude/teams/<team>/config.json` (local only);
  - the subagent's `agent-<agent_id>.meta.json` under `~/.claude/projects/`
    (local and cloud), read through `subagent_meta.subagent_metadata`;
  - agent definition files, only to detect a shadowed plugin type (below).

AUTHORED IS NOT INTEGRITY-PROTECTED. The team config and meta.json are files
the teammate's own OS user can rewrite. Hook stdin is the only input a
teammate cannot edit, so where the frame and meta.json both offer a TYPE, the
frame wins (step 4). meta.json is still the only source of a cloud actor's
NAME, so a teammate that rewrites its own meta `name` can put another
member's name on its completion (misattribution in the advisory and journal),
and one that deletes its meta.json resolves with no name. Neither can be
closed here: the cloud has no other harness record of the name. A harness-side
TaskCompleted check that carries `teammate_name` is the real control.

TRUST BOUNDARY (load-bearing): this module is an AUTHORITY surface. It must
never read the session registry or any helper that reaches it
(`frame_team_and_name`, `teammate_launch_name`, `bind_launcher_identity`,
`resolve_agent_name`, `turn_end_gate.teammate_identity`). Registry values are
self-asserted by teammates and therefore forgeable. It reads frame data only
through `subagent_meta`, which imports neither the registry nor the modules
that reach it. `tests/test_session_registry_trust_partition.py` enforces both
with AST bans.

ROLE IS KEYED ON `agent_type` ONLY (the #812 invariant), through the SSOT
`pact_context.classify_session_role`. `agent_id` is read only to resolve a
NAME, never to decide lead versus teammate.

A TYPE IS NEVER DERIVED FROM A VALUE KNOWN TO BE A NAME. An in-process
teammate's frame carries its member NAME in `agent_type`, and a local
meta.json carries a NAME in `agentType`. Treating either as a type would let
a member named `pact-secretary` be exempted from the self-completion rule.
Every step that reads the frame's `agent_type` as a type first drops it when
it names a member of the team.

A SHADOWED PLUGIN TYPE IS NOT A TYPE. Measured on CC 2.1.310 (cloud): meta.json
records a plugin agent's type WITHOUT the `PACT:` prefix and carries no field
that says where the definition came from. So the bare type `pact-secretary`
is also what a project or user agent named `pact-secretary` would produce.
When the winning value is bare, names one of this plugin's agents, and a
definition with that name exists that is not this plugin's own (a project
`.claude/agents` file, or a user `<config>/agents` file that is not the
plugin's symlink), the type is ambiguous and resolves to None. A `PACT:`
prefix on the winning value is taken as plugin provenance and skips the check.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import pact_context, subagent_meta
from .paths import get_claude_config_dir

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

MAX_NAME_CHARS = 128
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Bounds for the shadowed-type scan: how far up from the frame's cwd to look
# for a project `.claude/agents`, how many definition files to read in one
# directory, and how much of each file holds its frontmatter.
_MAX_ANCESTORS = 64
_MAX_DEFINITION_FILES = 256
_FRONTMATTER_BYTES = 4096


@dataclass(frozen=True)
class ActorIdentity:
    """Who made the tool call.

    role:       one of the ROLE_* constants.
    name:       member or subagent NAME; None when unresolved.
    agent_type: a TYPE with `PACT:` stripped, never a member name and never a
                shadowed plugin type; or None.
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

    agent_type = input_data.get("agent_type")
    agent_id = input_data.get("agent_id")
    if not isinstance(agent_type, str):
        # classify_session_role said teammate on a truthy non-string type.
        return ActorIdentity(ROLE_TEAMMATE, None, None, SOURCE_FRAME_ONLY)
    # The frame's agent_type as a TYPE candidate: None when it names a member.
    frame_type = None if _names_member(agent_type, team_name) else agent_type

    # Step 3 / 3': in-process teammate, id "a<name>-<16hex>". The shape is
    # matched HERE first, so agent_type_names_a_member's no-id fallback is
    # never relied on.
    if isinstance(agent_id, str) and re.fullmatch(
        re.escape(f"a{agent_type}-") + "[0-9a-f]{16}", agent_id
    ):
        # agent_id spelled as the frame read: test_background_work pins that
        # every caller passes exactly input_data.get('agent_id').
        if not subagent_meta.agent_type_names_a_member(
            agent_type, team_name, agent_id=input_data.get("agent_id")
        ):
            # agent_type is a NAME on this frame: never use it as a type.
            return ActorIdentity(ROLE_TEAMMATE, None, None, SOURCE_FRAME_ONLY)
        name = agent_type
        raw = _config_raw_type(name, team_name) or _first_raw(
            _meta_candidates(subagent_meta.subagent_metadata(input_data)), name
        )
        return ActorIdentity(
            ROLE_TEAMMATE, name, _typed(raw, input_data), SOURCE_INPROCESS_ID
        )

    # Step 4: Agent-tool subagent (cloud teammates have this shape too). The
    # FRAME's type wins over meta.json's: the frame is hook stdin, which the
    # teammate cannot edit, while it can rewrite its own meta.json. meta.json
    # types are used only when the frame offers none (or offers a NAME).
    if subagent_meta.subagent_launcher_id(input_data):
        meta = subagent_meta.subagent_metadata(input_data)
        if not isinstance(meta, dict):
            meta = {}
        name = valid_name(meta.get("name"))
        raw = _first_raw([frame_type, *_meta_candidates(meta)], name)
        source = SOURCE_SUBAGENT_META if meta else SOURCE_SUBAGENT_NO_META
        return ActorIdentity(ROLE_TEAMMATE, name, _typed(raw, input_data), source)

    # Step 5: name@team (legacy / forward-compat).
    if isinstance(agent_id, str) and "@" in agent_id:
        at_name, _, at_team = agent_id.partition("@")
        if at_name and at_team and team_name and at_team.lower() == team_name.lower():
            return ActorIdentity(
                ROLE_TEAMMATE, at_name,
                _typed(_config_raw_type(at_name, team_name), input_data),
                SOURCE_AT_ID,
            )

    # Step 6: no usable id (tmux, or an unrecognised id). The frame type is a
    # type unless it names a member (then it is a NAME).
    return ActorIdentity(
        ROLE_TEAMMATE, None, _typed(frame_type, input_data), SOURCE_FRAME_ONLY
    )


def _meta_candidates(meta: Any) -> list:
    """meta.json TYPE candidates in precedence order: customAgentType >
    spawnedAgentType > agentType (local: a NAME, cloud: a type)."""
    if not isinstance(meta, dict):
        return []
    return [
        meta.get("customAgentType"),
        meta.get("spawnedAgentType"),
        meta.get("agentType"),
    ]


def _first_raw(candidates: list, name: str | None) -> str | None:
    """The first candidate that is a non-empty string, not `name`, and not
    `name` once `PACT:` is stripped (a value equal to the name is a name)."""
    for value in candidates:
        stripped = _strip(value)
        if stripped and stripped != name and value != name:
            return value
    return None


def _meta_type(
    meta: Any, frame_type: Any = None, name: str | None = None
) -> str | None:
    """First usable TYPE from a subagent meta.json, then `frame_type`; `PACT:`
    stripped. Kept for the precedence tests; step 4 reads the frame FIRST."""
    return _strip(_first_raw([*_meta_candidates(meta), frame_type], name))


def _typed(raw: Any, input_data: dict) -> str | None:
    """`raw` as a TYPE (`PACT:` stripped), or None when it is ambiguous.

    Ambiguous means: `raw` carries no `PACT:` prefix, its value names one of
    this plugin's agents, and an agent definition with that name exists that
    is not this plugin's own (see the module docstring).
    """
    stripped = _strip(raw)
    if stripped is None:
        return None
    if raw.startswith(pact_context.PACT_NAMESPACE):
        return stripped
    if stripped not in subagent_meta.known_agent_types():
        return stripped
    if _shadowed_by_other_definition(stripped, input_data):
        return None
    return stripped


def _shadowed_by_other_definition(type_name: str, input_data: dict) -> bool:
    """True iff a non-plugin agent definition named `type_name` exists in a
    project `.claude/agents` (from the frame's cwd or CLAUDE_PROJECT_DIR up to
    the filesystem root) or the user `<config>/agents` directory. Any error
    reading a directory counts as no definition there."""
    for agents_dir in _definition_dirs(input_data):
        try:
            files = sorted(agents_dir.rglob("*.md"))[:_MAX_DEFINITION_FILES]
        except OSError:
            continue
        for path in files:
            if _is_plugin_definition(path):
                continue
            if _definition_name(path) == type_name:
                return True
    return False


def _definition_dirs(input_data: dict) -> list:
    """Existing agent-definition directories, each listed once."""
    candidates = [get_claude_config_dir() / "agents"]
    starts = [input_data.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR")]
    for start in starts:
        if not isinstance(start, str) or not start:
            continue
        directory = Path(start)
        for _ in range(_MAX_ANCESTORS):
            candidates.append(directory / ".claude" / "agents")
            if directory.parent == directory:
                break
            directory = directory.parent
    seen, found = set(), []
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            key = candidate.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    return found


def _is_plugin_definition(path: Path) -> bool:
    """True iff `path` is a symlink into a PACT plugin's `agents/` directory
    (the links symlinks.py installs into `<config>/agents`). A regular file is
    a project or user definition, even when its text matches the plugin's."""
    try:
        if not path.is_symlink():
            return False
        target_dir = path.resolve().parent
        if target_dir == subagent_meta.plugin_agents_dir().resolve():
            return True
        if target_dir.name != "agents":
            return False
        manifest = target_dir.parent / ".claude-plugin" / "plugin.json"
        with open(manifest, encoding="utf-8") as f:
            data = json.loads(f.read(_FRONTMATTER_BYTES * 4))
        return isinstance(data, dict) and data.get("name") == "PACT"
    except (OSError, ValueError):
        return False


def _definition_name(path: Path) -> str | None:
    """The `name:` in an agent file's frontmatter; the file stem when the
    frontmatter has no name; None when the file has no frontmatter."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(_FRONTMATTER_BYTES)
    except OSError:
        return None
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() == "name":
            return value.strip().strip("\"'") or None
    return path.stem


def _config_raw_type(name: str, team_name: str) -> str | None:
    """The team-config `agentType` recorded for member `name`, unstripped."""
    for member in pact_context.iter_team_members(team_name):
        if member.get("name") == name:
            value = member.get("agentType")
            return value if isinstance(value, str) and value else None
    return None


def _names_member(value: Any, team_name: str) -> bool:
    if not isinstance(value, str) or not value or not team_name:
        return False
    return any(m.get("name") == value for m in pact_context.iter_team_members(team_name))


def valid_name(value: Any) -> str | None:
    """`value` when it is a usable display name (a str of 1-128 characters
    with no control characters), else None. Shared with task_lifecycle_gate,
    which applies it to the teammate-writable task owner before echoing it."""
    if (
        isinstance(value, str)
        and 0 < len(value) <= MAX_NAME_CHARS
        and not _CONTROL_CHARS.search(value)
    ):
        return value
    return None


def _strip(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    stripped = pact_context.strip_pact_namespace(value)
    return stripped or None
