"""
Location: pact-plugin/hooks/shared/subagent_meta.py
Summary: Registry-free readers of harness-written subagent data: the frame's
         `agent_id` shapes, team-config membership of a frame `agent_type`,
         and the platform's `agent-<agent_id>.meta.json`.
         Also the display-name validator `valid_name`, shared by the actor
         resolver and the cloud roster.
Used by: shared/actor_identity.py (the self-completion actor resolver, which
         re-exports `valid_name` and `MAX_NAME_CHARS`), shared/cloud_roster.py,
         shared/background_work.py and shared/turn_end_gate.py (which
         re-export these names for their existing callers), peer_inject.py.

TRUST BOUNDARY (load-bearing): actor_identity is an AUTHORITY surface and
reads frame data only through this module. This module must never import the
session registry, background_work or turn_end_gate, which reach it.
`tests/test_session_registry_trust_partition.py` enforces this with an AST
ban, so a later edit that adds a registry fallback here fails the suite.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import state_file
from .paths import get_claude_config_dir

# Largest meta.json read. The read itself is bounded (see subagent_metadata),
# so a file that grows after it is opened cannot make the read larger.
META_MAX_BYTES = 64 * 1024

# A path-safe id: the frame's agent_id and session_id both pass this before
# they are used to build a path. Always test it with .fullmatch: with .match,
# `$` also matches before a trailing newline, so "id\n" would pass.
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

# An Agent-tool subagent's `agent_id`: "a" followed by 16 lowercase hex.
SUBAGENT_ID = re.compile(r"a[0-9a-f]{16}")

# Platform-supplied agent types that are NOT this plugin's agents. Hard-coded
# because they come from the harness rather than from a file we can enumerate.
# This list going stale is a real exposure — see the residual note on
# `agent_type_names_a_member`.
PLATFORM_AGENT_TYPES = frozenset(
    {"general-purpose", "Explore", "Plan", "statusline-setup"}
)

# A usable display name: 1-128 characters, no control characters. Lives here
# rather than in actor_identity so cloud_roster can use it without importing
# actor_identity, which imports cloud_roster.
MAX_NAME_CHARS = 128
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


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


def known_agent_types() -> frozenset:
    """Agent-type stems this plugin ships, DERIVED AT RUNTIME from agents/.

    Derived rather than listed so that adding an agent file cannot silently
    open a collision with a teammate name. A hard-coded copy would go stale
    the day someone adds one, and the failure would be a mis-bind rather than
    an error.
    """
    try:
        agents_dir = plugin_agents_dir()
        return frozenset(p.stem for p in agents_dir.glob("*.md"))
    except (OSError, IndexError):
        return frozenset()


def plugin_agents_dir() -> Path:
    """This plugin's own `agents/` directory (hooks/shared -> plugin root)."""
    return Path(__file__).resolve().parents[2] / "agents"


def agent_type_names_a_member(
    agent_type: Any, team_name: str, *, agent_id: Any = None
) -> bool:
    """True iff the frame's `agent_type` is an IDENTITY rather than a TYPE.

    MEASURED 2026-09-11 on a live in-process Agent-Teams teammate PostToolUse
    Bash frame: `agent_type` carried the teammate's own NAME, not the
    `pact-`-prefixed agentType its team config records. `agent_name` was
    absent and `agent_id` carried no `@`, so no other route resolves. See
    `tests/fixtures/role_frames.py` ::
    captured_posttooluse_teammate_inprocess_bash_background.

    The test is MEMBERSHIP in the team config the platform wrote at spawn —
    a lookup in authoritative local state, not string surgery. Step 4 already
    strips this field today; this validates the value before trusting it.

    THE FRAME'S `agent_id` SHAPE IS READ FIRST. An in-process teammate's frame
    carries "a" + `agent_type` + "-" + 16 lowercase hex, and an Agent-tool
    subagent's carries "a" + 16 lowercase hex. A subagent-shaped id is never a
    member, whatever its type. A teammate-shaped id is built from `agent_type`
    and compared whole, never parsed, and goes to membership without the deny
    set, so a member named after an agent type is not refused on its own
    frame. Any other id, or none, takes the type checks below.

    RESIDUAL, STATED RATHER THAN CLAIMED AWAY, on a frame without a recognized
    `agent_id`. `classify_session_role` treats any `agent_type` outside
    `LEAD_AGENT_TYPES` as a teammate, so a generic Agent-tool subagent reaches
    this code. The deny set below reduces the collision to perverse naming — a
    member would have to be NAMED after a real agent type, and such a member is
    refused on that frame — but an UNKNOWN FUTURE PLATFORM TYPE colliding with
    a member name remains possible, and that case fails toward MIS-BIND rather
    than silence, which is the worse direction: a launch would be attributed
    to a teammate who did not make it. This is an accepted exposure, not an
    eliminated one.

    TMUX IS UNTESTED, NOT COVERED. Under tmux the frame reportedly carries the
    real type and no `agent_id`, so this returns False and the caller falls through to the
    registry, which works there because the in-process self-guard does not
    fire. That rests on one captured PreToolUse frame and NO Bash PostToolUse
    frame. It was UNEXERCISED during development because the development
    machine had no tmux teams — which bounds the VERIFICATION, not the
    behaviour. This plugin ships to consumers who may run tmux teams, so do
    not read "no teams to test against" as "a path nobody takes".
    """
    if not isinstance(agent_type, str) or not agent_type:
        return False
    if isinstance(agent_id, str):
        if SUBAGENT_ID.fullmatch(agent_id):
            return False
        if re.fullmatch(re.escape(f"a{agent_type}-") + "[0-9a-f]{16}", agent_id):
            return names_a_member(agent_type, team_name)
    from .session_state import is_safe_path_component

    if not is_safe_path_component(team_name):
        return False
    if agent_type in PLATFORM_AGENT_TYPES:
        return False
    if agent_type in known_agent_types():
        return False
    from .pact_context import iter_team_members

    return any(
        isinstance(m, dict) and m.get("name") == agent_type
        for m in iter_team_members(team_name)
    )


def names_a_member(name: Any, team: str) -> bool:
    """True iff `name` is a member in `team`'s config. `team` may come from stdin."""
    from .pact_context import iter_team_members
    from .session_state import is_safe_path_component

    if not isinstance(name, str) or not name or not is_safe_path_component(team):
        return False
    return any(m.get("name") == name for m in iter_team_members(team))


def subagent_launcher_id(input_data: Any) -> str:
    """The Agent-tool subagent's own `agent_id` on this frame, or "".

    A subagent's id is "a" followed by 16 lowercase hex [LIVE] — the same shape
    `agent_type_names_a_member` refuses a membership match for.

    AN ABSENT OR OTHER-SHAPED `agent_id` RETURNS "", NEVER AN INFERRED ID.
    Absence has been measured as the LEAD's own signature on the lead's own
    frames, but it is only INFERRED for an in-process teammate's frame, which
    nobody has captured. So "" means "not an Agent-tool subagent frame", and
    it is never read as "the lead". Role is decided elsewhere, on
    `agent_type` (pact_context.is_lead).

    Each caller treats "" as "do nothing for this frame":
      - background_work records no launch (reading absence as the lead would
        record a teammate's launch as the lead's and refuse the lead a turn
        end it had every right to end);
      - the cloud roster writers (dispatch_gate's `foreign` record,
        roster_writer's `bind`) write nothing;
      - actor_identity does not take its subagent step (step 4), and
        task_claim_gate's cloud tier stays a no-op.
    Each is the under-acting direction.
    """
    agent_id = input_data.get("agent_id") if isinstance(input_data, dict) else None
    if not isinstance(agent_id, str):
        return ""
    return agent_id if SUBAGENT_ID.fullmatch(agent_id) else ""


def subagent_metadata(input_data: dict) -> dict:
    """The platform's `agent-<agent_id>.meta.json` for a subagent frame, or {}.

    Looked for beside `agent_transcript_path`, then in the session's
    `subagents` folder beside `transcript_path`. Read only from under the
    config root's `projects` folder, and only when at most META_MAX_BYTES.

    The size bound is applied to the READ, not to an earlier stat: at most
    META_MAX_BYTES + 1 bytes are read from the opened file, and a file that
    yields more is rejected. A file that grows between a stat and the read
    therefore cannot make the read larger.
    """
    if not isinstance(input_data, dict):
        return {}
    agent_id = input_data.get("agent_id")
    if not isinstance(agent_id, str) or not SAFE_ID.fullmatch(agent_id):
        return {}
    filename = f"agent-{agent_id}.meta.json"
    candidates = []
    agent_transcript = input_data.get("agent_transcript_path")
    if _is_text(agent_transcript):
        candidates.append(Path(agent_transcript).parent / filename)
    transcript = input_data.get("transcript_path")
    session_id = input_data.get("session_id")
    if _is_text(transcript) and isinstance(session_id, str) and SAFE_ID.fullmatch(session_id):
        candidates.append(Path(transcript).parent / session_id / "subagents" / filename)
    root = get_claude_config_dir() / "projects"
    for path in candidates:
        try:
            text = state_file.read_text(path, root, max_bytes=META_MAX_BYTES)
        except state_file.TooLarge:
            return {}
        except OSError:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)
