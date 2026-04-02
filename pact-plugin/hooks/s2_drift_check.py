#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/s2_drift_check.py
Summary: PostToolUse hook matching Edit|Write — detects cross-scope file edits
         and appends drift alerts to s2-state.json.
Used by: hooks.json PostToolUse hook (matcher: Edit|Write, async: true)

When any agent edits or writes a file, this hook checks if that file falls
within another agent's "owns" scope. If so, it appends a drift_alert entry
to the S2 state file, making the boundary violation visible to the orchestrator
and other agents.

This hook is async (non-blocking) — it does NOT gate the Edit/Write operation.
Performance ceiling: <50ms per invocation.

Graceful degradation: if no s2-state.json exists, or it's malformed, the hook
silently exits (no-op).

Input: JSON from stdin with tool_name, tool_input (file_path), tool_output
Output: JSON with suppressOutput (never blocks)
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

# Add hooks directory to path for shared module imports
_hooks_dir = os.path.dirname(os.path.abspath(__file__))
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)

from shared.error_output import hook_error_json
from shared.s2_state import (
    read_s2_state, update_s2_state, file_in_scope, _discover_worktree_path,
    _MAX_DRIFT_ALERTS,
)

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _get_current_agent() -> str:
    """Get the current agent's name from the environment.

    Claude Code sets CLAUDE_CODE_AGENT_NAME for teammate agents.
    Falls back to 'unknown' if not available.
    """
    return os.environ.get("CLAUDE_CODE_AGENT_NAME", "unknown")


def _make_relative_path(file_path: str, worktree_path: str) -> str:
    """Convert an absolute file path to a worktree-relative path.

    Boundary scopes use directory prefixes relative to the project root.
    File paths from Edit/Write are absolute. This converts to relative
    for scope matching.
    """
    if file_path.startswith(worktree_path):
        relative = file_path[len(worktree_path):]
        # Strip leading slash
        if relative.startswith("/"):
            relative = relative[1:]
        return relative
    return file_path


def check_drift(
    file_path: str,
    agent_name: str,
    worktree_path: str | None = None,
) -> list[str] | None:
    """Check if a file edit drifts into another agent's scope.

    Args:
        file_path: Absolute path to the edited file
        agent_name: Name of the agent performing the edit
        worktree_path: Override for worktree path (for testing)

    Returns:
        List of affected agent names if drift detected, None otherwise
    """
    if not file_path:
        return None

    # Discover worktree path if not provided
    if worktree_path is None:
        worktree_path = _discover_worktree_path()
    if not worktree_path:
        return None

    # Read S2 state — graceful degradation if missing/malformed
    state = read_s2_state(worktree_path)
    if state is None:
        return None

    boundaries = state.get("boundaries", {})
    if not boundaries:
        return None

    # Convert to relative path for scope matching
    relative_path = _make_relative_path(file_path, worktree_path)

    # Check if the file falls in any OTHER agent's "owns" scope
    affected_agents = []
    for other_agent, scope in boundaries.items():
        # Skip self — editing your own scope is not drift
        if other_agent == agent_name:
            continue

        owns = scope.get("owns", [])
        if file_in_scope(relative_path, owns):
            affected_agents.append(other_agent)

    return affected_agents if affected_agents else None


def _append_drift_alert(
    worktree_path: str,
    file_path: str,
    agent_name: str,
    affected_agents: list[str],
) -> bool:
    """Append a drift alert to s2-state.json.

    Uses the atomic update_s2_state function to safely append.

    Returns True on success, False on failure.
    """
    alert = {
        "file": file_path,
        "modified_by": agent_name,
        "affects": affected_agents,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    def updater(state: dict) -> dict:
        if "drift_alerts" not in state:
            state["drift_alerts"] = []
        state["drift_alerts"].append(alert)
        # Cap to last N entries to prevent unbounded growth
        if len(state["drift_alerts"]) > _MAX_DRIFT_ALERTS:
            state["drift_alerts"] = state["drift_alerts"][-_MAX_DRIFT_ALERTS:]
        return state

    return update_s2_state(worktree_path, updater)


def main():
    """Main entry point for the PostToolUse hook."""
    debug = os.environ.get("PACT_DEBUG", "").lower() in ("1", "true", "yes")
    start_time = time.monotonic() if debug else None

    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only process Edit and Write tools
        if tool_name not in ("Edit", "Write"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        file_path = tool_input.get("file_path", "")
        if not file_path:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        agent_name = _get_current_agent()
        affected = check_drift(file_path, agent_name)

        if affected:
            # Discover worktree for alert storage
            worktree_path = _discover_worktree_path()
            if worktree_path:
                _append_drift_alert(worktree_path, file_path, agent_name, affected)

        print(_SUPPRESS_OUTPUT)

        if debug and start_time is not None:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            print(
                f"s2_drift_check: {elapsed_ms:.1f}ms",
                file=sys.stderr,
            )

        sys.exit(0)

    except Exception as e:
        # Fail open — never block edits due to hook errors
        print(f"Hook warning (s2_drift_check): {e}", file=sys.stderr)
        print(hook_error_json("s2_drift_check", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
