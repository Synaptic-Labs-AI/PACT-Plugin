#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_gate.py
Summary: PreToolUse hook that blocks implementation tools (Edit, Write, Bash,
         Agent, NotebookEdit) until the bootstrap-complete marker exists.
Used by: hooks.json PreToolUse hook (no matcher — fires for all hookable tools)

Layer 3 of the four-layer bootstrap gate enforcement (#401). On each tool
call, checks the session-scoped bootstrap-complete marker:
  - Marker exists → suppressOutput (sub-ms fast path)
  - Non-PACT session → suppressOutput (no-op)
  - Teammate → suppressOutput (no-op)
  - Implementation tool (Edit, Write, Bash, Agent, NotebookEdit) → deny
  - Exploration/allowed tool (Read, Glob, Grep, WebFetch, WebSearch,
    AskUserQuestion, ExitPlanMode, any MCP tool) → suppressOutput (allow)

Tool classification rationale:
  - Blocked tools are implementation actions that shouldn't run before
    governance is loaded.
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

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Implementation tools blocked until bootstrap completes.
# These are the tools that perform mutations or spawn sub-processes.
_BLOCKED_TOOLS = frozenset({
    "Edit",
    "Write",
    "Bash",
    "Agent",
    "NotebookEdit",
})

_MARKER_NAME = "bootstrap-complete"

_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Implementation tools (Edit, Write, Bash, Agent) are blocked until "
    "bootstrap completes. Exploration tools (Read, Glob, Grep) are available."
)


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether a tool call should be denied.

    Returns the deny reason string if the tool should be blocked, or None
    if the tool call should be allowed through.
    """
    try:
        sys.path.insert(
            0,
            str(Path(__file__).resolve().parent),
        )
        from shared import pact_context
    finally:
        if sys.path and sys.path[0] == str(Path(__file__).resolve().parent):
            sys.path.pop(0)

    pact_context.init(input_data)

    # Fast path: marker exists → allow everything
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    marker_path = Path(session_dir) / _MARKER_NAME
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

    # All other hookable tools (Read, Glob, Grep, WebFetch, WebSearch,
    # AskUserQuestion, ExitPlanMode) are exploration tools — allow
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
