#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/peer_inject.py
Summary: SubagentStart hook that injects peer teammate list into newly
         spawned PACT agents via additionalContext.
Used by: hooks.json SubagentStart hook (matcher: pact-* agent types)

Replaces the manual pattern of listing peer names in task descriptions.
Agents automatically know who else is on the team.

Input: JSON from stdin with agent_id, agent_type
Output: JSON with hookSpecificOutput.additionalContext
"""

import json
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_team_name

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


_TEACHBACK_REMINDER = (
    "\n\nTEACHBACK TIMING: Send your teachback via SendMessage BEFORE any "
    "Edit/Write/Bash calls. This is step 4 of your On Start sequence. "
    "If you haven't sent a teachback yet, do it now before any implementation work."
)


def get_peer_context(
    agent_type: str,
    team_name: str,
    agent_name: str = "",
    teams_dir: str | None = None,
) -> str | None:
    """
    Build peer context string for a newly spawned agent.

    Includes a teachback timing reminder appended after the peer list
    to mechanically reinforce the teachback-before-work protocol.

    Args:
        agent_type: The spawning agent's type (e.g., "pact-backend-coder")
        team_name: Current team name
        agent_name: The spawning agent's unique name (e.g., "backend-coder-1")
        teams_dir: Override for teams directory (for testing)

    Returns:
        Context string with peer list and teachback reminder, or None if no team context
    """
    if not team_name:
        return None

    if teams_dir is None:
        teams_dir = str(Path.home() / ".claude" / "teams")

    config_path = Path(teams_dir) / team_name / "config.json"
    if not config_path.exists():
        return None

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None

    members = config.get("members", [])

    if agent_name:
        # Filter by exact name — excludes only the spawning agent itself
        peers = [m["name"] for m in members if m.get("name") != agent_name]
    else:
        # Fallback: filter by agentType. This excludes ALL agents of the same
        # type, not just the spawning agent. This is a known limitation when
        # the hook input does not include agent_name/agent_id.
        peers = [m["name"] for m in members if m.get("agentType") != agent_type]

    if not peers:
        peer_context = "You are the only active teammate on this team."
    else:
        peer_list = ", ".join(peers)
        peer_context = (
            f"Active teammates on your team: {peer_list}\n"
            f"You can message them via SendMessage for shared artifacts or blocking questions."
        )

    return peer_context + _TEACHBACK_REMINDER


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    agent_type = input_data.get("agent_type", "")
    agent_name = input_data.get("agent_name", "") or input_data.get("agent_id", "")
    team_name = get_team_name()

    context = get_peer_context(
        agent_type=agent_type,
        team_name=team_name,
        agent_name=agent_name,
    )

    if context:
        output = {
            "hookSpecificOutput": {
                "additionalContext": context
            }
        }
        print(json.dumps(output))
    else:
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
