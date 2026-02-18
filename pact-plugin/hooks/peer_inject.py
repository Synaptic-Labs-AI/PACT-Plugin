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
import os
from pathlib import Path


def get_peer_context(
    agent_type: str,
    team_name: str,
    teams_dir: str | None = None,
) -> str | None:
    """
    Build peer context string for a newly spawned agent.

    Args:
        agent_type: The spawning agent's type (e.g., "pact-backend-coder")
        team_name: Current team name
        teams_dir: Override for teams directory (for testing)

    Returns:
        Context string with peer list, or None if no team context
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

    # Filter out the agent being spawned (match by agentType)
    peers = [m["name"] for m in members if m.get("agentType") != agent_type]

    if not peers:
        return "You are the only active teammate on this team."

    peer_list = ", ".join(peers)
    return (
        f"Active teammates on your team: {peer_list}\n"
        f"You can message them via SendMessage for shared artifacts or blocking questions."
    )


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    agent_type = input_data.get("agent_type", "")
    team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "")

    context = get_peer_context(
        agent_type=agent_type,
        team_name=team_name,
    )

    if context:
        output = {
            "hookSpecificOutput": {
                "additionalContext": context
            }
        }
        print(json.dumps(output))

    sys.exit(0)


if __name__ == "__main__":
    main()
