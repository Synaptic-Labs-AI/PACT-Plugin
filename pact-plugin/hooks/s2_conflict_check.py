#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/s2_conflict_check.py
Summary: PreToolUse hook matching Task — checks if a newly dispatched agent's
         scope overlaps with existing agent boundaries in s2-state.json.
Used by: hooks.json PreToolUse hook (matcher: Task)

When the orchestrator dispatches an agent via the Task tool, this hook reads
the S2 state file (.pact/s2-state.json) and checks for overlapping "owns"
scopes between the new agent and already-registered agents.

If overlap is detected, the hook emits a warning message but does NOT block
the dispatch — overlapping scopes may be intentional. The orchestrator sees
the warning and can adjust boundaries if needed.

Graceful degradation: if no s2-state.json exists, or it's malformed, the
hook silently allows the dispatch (no-op).

Input: JSON from stdin with tool_name and tool_input (Task parameters)
Output: JSON with systemMessage warning if overlap detected, suppressOutput otherwise
"""

import json
import os
import sys
import time

# Add hooks directory to path for shared module imports
_hooks_dir = os.path.dirname(os.path.abspath(__file__))
if _hooks_dir not in sys.path:
    sys.path.insert(0, _hooks_dir)

from shared.error_output import hook_error_json
from shared.s2_state import read_s2_state, check_boundary_overlap, _discover_worktree_path

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _extract_agent_name(tool_input: dict) -> str | None:
    """Extract the agent name from Task tool input.

    Agent Teams dispatch uses the 'name' field. Falls back to
    'subagent_type' for compatibility with older dispatch patterns.
    """
    return tool_input.get("name") or tool_input.get("subagent_type")


def check_scope_overlap(tool_input: dict, worktree_path: str | None = None) -> str | None:
    """Check if a dispatched agent's scope overlaps with existing boundaries.

    Args:
        tool_input: The Task tool's input parameters
        worktree_path: Override for worktree path (for testing)

    Returns:
        Warning message if overlap detected, None if no overlap or no state
    """
    # Only care about agent dispatches (must have name or subagent_type)
    agent_name = _extract_agent_name(tool_input)
    if not agent_name:
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

    # Check for overlaps across all boundary pairs
    overlaps = check_boundary_overlap(boundaries)
    if not overlaps:
        return None

    # Filter to overlaps involving the new agent
    relevant_overlaps = [
        o for o in overlaps
        if agent_name in (o["agent_a"], o["agent_b"])
    ]

    if not relevant_overlaps:
        return None

    # Build warning message
    warnings = []
    for overlap in relevant_overlaps:
        other_agent = (
            overlap["agent_b"] if overlap["agent_a"] == agent_name
            else overlap["agent_a"]
        )
        paths = ", ".join(overlap["overlapping_paths"])
        warnings.append(
            f"  - {agent_name} overlaps with {other_agent} on: {paths}"
        )

    return (
        f"S2 Conflict Warning: Agent '{agent_name}' has overlapping 'owns' "
        f"scopes with existing agents:\n"
        + "\n".join(warnings)
        + "\n\nConsider adjusting boundaries or sequencing these agents."
    )


def main():
    """Main entry point for the PreToolUse hook."""
    debug = os.environ.get("PACT_DEBUG", "").lower() in ("1", "true", "yes")
    start_time = time.monotonic() if debug else None

    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tool_input = input_data.get("tool_input", {})

        # Only process Task tool invocations with agent dispatch fields
        if not _extract_agent_name(tool_input):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        warning = check_scope_overlap(tool_input)

        if warning:
            # Warn but allow — overlapping scopes may be intentional
            output = {"systemMessage": warning}
            print(json.dumps(output))
        else:
            print(_SUPPRESS_OUTPUT)

        if debug and start_time is not None:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            print(
                f"s2_conflict_check: {elapsed_ms:.1f}ms",
                file=sys.stderr,
            )

        sys.exit(0)

    except Exception as e:
        # Fail open — never block dispatch due to hook errors
        print(f"Hook warning (s2_conflict_check): {e}", file=sys.stderr)
        print(hook_error_json("s2_conflict_check", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
