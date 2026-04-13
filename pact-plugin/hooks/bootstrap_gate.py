#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_gate.py
Summary: PreToolUse hook that blocks code-editing and agent-spawning tools
         (Edit, Write, Agent, NotebookEdit) until the bootstrap-complete
         marker exists.
Used by: hooks.json PreToolUse hook (no matcher — fires for all hookable tools)

Layer 3 of the four-layer bootstrap gate enforcement (#401). On each tool
call, checks the session-scoped bootstrap-complete marker:
  - Marker exists → suppressOutput (sub-ms fast path)
  - Non-PACT session → suppressOutput (no-op)
  - Teammate → suppressOutput (no-op)
  - Code-editing/agent tool (Edit, Write, Agent, NotebookEdit) → deny
  - Operational/exploration tool (Read, Glob, Grep, Bash, WebFetch,
    WebSearch, AskUserQuestion, ExitPlanMode, any MCP tool) → allow

Tool classification rationale:
  - Blocked tools are structured code modification (Edit, Write) and agent
    spawning (Agent, NotebookEdit) actions that shouldn't run before
    governance is loaded.
  - Bash is ALLOWED because the bootstrap marker-write mechanism itself is
    a Bash command in bootstrap.md — blocking Bash would create a circular
    dependency where the gate can never self-disable.
  - Exploration tools are read-only and needed for state recovery after
    compaction.
  - MCP tools are always allowed — they're external integrations that may
    be needed for context gathering.
  - Non-hookable tools (Skill, ToolSearch, Task*, SendMessage) never reach
    this hook because they don't fire PreToolUse events.

SACROSANCT: every raisable path is wrapped in try/except that defaults to
allow (exit 0 with suppressOutput). A gate bug must never block a tool call.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {"suppressOutput": true} (allow / passthrough)
"""

import json
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared import BOOTSTRAP_MARKER_NAME

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Code-editing and agent-spawning tools blocked until bootstrap completes.
# Bash is intentionally NOT blocked — the marker-write mechanism in
# bootstrap.md is a Bash command, so blocking Bash would prevent the gate
# from ever self-disabling (circular dependency).
_BLOCKED_TOOLS = frozenset({
    "Edit",
    "Write",
    "Agent",
    "NotebookEdit",
})

_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Code-editing tools (Edit, Write) and agent spawning (Agent) are blocked "
    "until bootstrap completes. Bash, Read, Glob, Grep are available."
)


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether a tool call should be denied.

    Returns the deny reason string if the tool should be blocked, or None
    if the tool call should be allowed through.
    """
    pact_context.init(input_data)

    # Fast path: marker exists → allow everything
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    marker_path = Path(session_dir) / BOOTSTRAP_MARKER_NAME
    if marker_path.exists():
        return None

    # Teammate detection
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    # Lead session, no marker — check tool classification
    tool_name = input_data.get("tool_name", "")

    # MCP tools always allowed (external integrations)
    if isinstance(tool_name, str) and tool_name.startswith("mcp__"):
        return None

    # Blocked implementation tools
    if tool_name in _BLOCKED_TOOLS:
        return _DENY_REASON

    # All other hookable tools (Read, Glob, Grep, Bash, WebFetch, WebSearch,
    # AskUserQuestion, ExitPlanMode) are operational/exploration tools — allow
    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception:
        # Any exception in gate logic → fail-open
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if deny_reason:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
