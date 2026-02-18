#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/team_guard.py
Summary: PreToolUse hook matching Task — blocks agent dispatch if team_name
         is specified but the team doesn't exist yet.
Used by: hooks.json PreToolUse hook (matcher: Task)

Enforces the "create team before dispatching agents" rule at the platform
level, replacing the prompt-based reminder.

Input: JSON from stdin with tool_input containing Task parameters
Output: JSON with hookSpecificOutput.permissionDecision if blocking
"""

import json
import sys
from pathlib import Path


def check_team_exists(tool_input: dict, teams_dir: str | None = None) -> str | None:
    """
    Check if the team specified in a Task call exists.

    Args:
        tool_input: The Task tool's input parameters
        teams_dir: Override for teams directory (for testing)

    Returns:
        Error message if team doesn't exist, None if OK
    """
    team_name = tool_input.get("team_name")
    if not team_name:
        return None  # No team_name = not a team dispatch, allow
    team_name = team_name.lower()

    if teams_dir is None:
        teams_dir = str(Path.home() / ".claude" / "teams")

    team_config = Path(teams_dir) / team_name / "config.json"
    if not team_config.exists():
        return (
            f"Team '{team_name}' does not exist yet. "
            f"Call TeamCreate(team_name=\"{team_name}\") before dispatching agents."
        )

    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    error = check_team_exists(tool_input)

    if error:
        output = {
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": error
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
