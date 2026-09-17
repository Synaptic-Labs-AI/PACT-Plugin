#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/peer_inject.py
Summary: SubagentStart hook that injects the teammate block (role marker,
         peer list, teachback and completion-authority notes) into a team
         member's context, via additionalContext.
Used by: hooks.json SubagentStart hook. It runs at every subagent start,
         including an in-process teammate's turn starts, and injects only
         when the frame's agent type names a member of the resolved team.
         Any other start, such as Explore, general-purpose, Plan or a
         PACT-typed Agent-tool subagent, gets nothing.

The platform delivers this output into context once per context window: at
spawn, and after each compaction. A member the check misses at spawn gets no
block until it next compacts; its role and team are still in its spawn prompt.

Replaces the manual pattern of listing peer names in task descriptions.
Agents automatically know who else is on the team.

SACROSANCT: every raisable path in main() is wrapped in try/except that
defaults to passthrough (exit 0 with suppressOutput). A hook bug must
never block a SubagentStart event. Mirrors the fail-open contract
documented in bootstrap_gate.py and bootstrap_prompt_gate.py.

Input: JSON from stdin with agent_type and session_id
Output: JSON with hookSpecificOutput.additionalContext
"""

from __future__ import annotations

import json
import sys
from pathlib import Path  # noqa: F401  # re-export: corpus patches peer_inject.Path.home

import shared.pact_context as pact_context
from shared.plugin_manifest import (  # noqa: F401  # re-export: static-import guard + corpus introspection
    format_plugin_banner,
)

# The peer-context builder + its prelude templates, agent-name sanitizer,
# and trailing reminders now live in shared/peer_context.py (one SSOT serving
# BOTH this SubagentStart hook and session_init's SessionStart teammate-branch).
# Re-export them here so existing import sites (tests, etc.) keep working.
from shared.peer_context import (  # noqa: F401
    get_peer_context,
    _sanitize_agent_name,
    _BOOTSTRAP_PRELUDE_TEMPLATE,
    _TEACHBACK_REMINDER,
    _COMPLETION_AUTHORITY_NOTE,
)

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        # input_data may not be a dict (e.g., parseable JSON `123` or `[]`);
        # downstream pact_context.init + .get() raise AttributeError on
        # non-dict input. The outer except below catches any such raise
        # and falls open with suppressOutput, mirroring the SACROSANCT
        # fail-open pattern in bootstrap_gate.py and bootstrap_prompt_gate.py.
        pact_context.init(input_data)
        agent_type = input_data.get("agent_type", "")
        # A separate-process teammate's own process has no PACT context, so its
        # team comes from its session-registry entry; imported here so a
        # failure stays inside this fail-open block.
        from shared.background_work import agent_type_names_a_member, frame_team_and_name

        team_name, _ = frame_team_and_name(input_data)
        # Only a team member gets the block. Its frame carries the member name
        # as agent_type and no agent_name, so that name is both the role label
        # and the self-exclusion key.
        member = agent_type if agent_type_names_a_member(
            agent_type, team_name, agent_id=input_data.get("agent_id")
        ) else ""

        context = None
        if member:
            context = get_peer_context(
                agent_type=agent_type,
                team_name=team_name,
                agent_name=member,
            )
    except Exception:
        # Any exception in the build path → fail-open with suppressOutput.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if context:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": context
            }
        }
        print(json.dumps(output))
    else:
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
