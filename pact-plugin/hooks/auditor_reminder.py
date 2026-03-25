#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/auditor_reminder.py
Summary: PostToolUse hook on Task tool that reminds the orchestrator to dispatch
         a pact-auditor (opt-out default) when a coder is spawned without one present.
Used by: hooks.json PostToolUse hook (matcher: Task)

Checks whether the dispatched agent is a coder type and, if so, whether the
team already has an auditor member. If no auditor is present, emits a
systemMessage reminder that auditor dispatch is the default and requires
explicit justification to skip.

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin with tool_name, tool_input, tool_output
Output: JSON systemMessage on stdout if reminder needed, nothing otherwise
"""

import json
import sys
from pathlib import Path

from shared.error_output import hook_error_json

# Coder agent types that warrant an auditor check.
# pact-n8n is excluded: it produces JSON workflow configs, not source code;
# auditor observation is less applicable.
CODER_TYPES = frozenset({
    "pact-backend-coder",
    "pact-frontend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
})


def _team_has_auditor(team_name: str, teams_dir: str | None = None) -> bool:
    """Check if the team already has an auditor member.

    Args:
        team_name: Team name (will be lowercased)
        teams_dir: Override for teams directory (for testing)

    Returns:
        True if an auditor member exists in the team config
    """
    if not team_name:
        return True  # No team context — suppress reminder

    if teams_dir is None:
        teams_dir = str(Path.home() / ".claude" / "teams")

    config_path = Path(teams_dir) / team_name.lower() / "config.json"
    if not config_path.exists():
        return True  # Can't read config — suppress reminder

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True  # Malformed or unreadable — suppress reminder

    for member in data.get("members", []):
        if member.get("agentType") == "pact-auditor":
            return True

    return False


def check_auditor_needed(tool_input: dict, teams_dir: str | None = None) -> str | None:
    """Determine if an auditor reminder should be emitted.

    Args:
        tool_input: The Task tool's input parameters
        teams_dir: Override for teams directory (for testing)

    Returns:
        Reminder message string if needed, None otherwise
    """
    subagent_type = tool_input.get("subagent_type", "")
    if subagent_type not in CODER_TYPES:
        return None

    team_name = tool_input.get("team_name", "")
    if _team_has_auditor(team_name, teams_dir):
        return None

    return (
        "\U0001f50e Coder dispatched without a concurrent auditor.\n"
        "Auditor dispatch is the default — to skip, state: "
        '"Auditor skipped: [justification]".\n'
        "Valid skip reasons: single coder on familiar pattern, "
        "variety reassessed below 7, user requested skip.\n"
        "See pact-audit.md for full dispatch protocol."
    )


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        tool_input = input_data.get("tool_input", {})
        reminder = check_auditor_needed(tool_input)

        if reminder:
            print(json.dumps({"systemMessage": reminder}))

        sys.exit(0)

    except Exception as e:
        # Fail open — never block agent dispatch
        print(f"Hook warning (auditor_reminder): {e}", file=sys.stderr)
        print(hook_error_json("auditor_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
