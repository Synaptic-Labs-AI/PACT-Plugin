#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/peer_inject.py
Summary: SubagentStart hook that injects peer teammate list into newly
         spawned PACT agents via additionalContext.
Used by: hooks.json SubagentStart hook (matcher: pact-* agent types)

Replaces the manual pattern of listing peer names in task descriptions.
Agents automatically know who else is on the team.

SACROSANCT: every raisable path in main() is wrapped in try/except that
defaults to passthrough (exit 0 with suppressOutput). A hook bug must
never block a SubagentStart event. Mirrors the fail-open contract
documented in bootstrap_gate.py and bootstrap_prompt_gate.py.

Input: JSON from stdin with agent_id, agent_type
Output: JSON with hookSpecificOutput.additionalContext
"""

from __future__ import annotations

import json
import sys
from pathlib import Path  # noqa: F401  # re-export: corpus patches peer_inject.Path.home

import shared.pact_context as pact_context
from shared.pact_context import get_team_name
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
        # Only accept agent_name here. agent_id is a UUID and team members are
        # registered in the team config under their canonical names, not UUIDs —
        # falling back to agent_id would make the self-exclusion filter in
        # get_peer_context() fail to match anything, and the intended agentType
        # fallback (which excludes ALL peers of the same type) would become
        # unreachable. Leave agent_name empty when absent so get_peer_context's
        # agentType fallback fires as originally designed.
        agent_name = input_data.get("agent_name", "")
        team_name = get_team_name()

        context = get_peer_context(
            agent_type=agent_type,
            team_name=team_name,
            agent_name=agent_name,
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
