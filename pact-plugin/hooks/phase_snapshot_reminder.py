#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/phase_snapshot_reminder.py
Summary: PostToolUse hook on TaskUpdate that reminds the orchestrator to save
         organizational state when a PACT phase task is marked completed.
Used by: hooks.json PostToolUse hook (matcher: TaskUpdate)

Checks whether the TaskUpdate is setting a task status to "completed" and
whether the task subject contains a PACT phase keyword (PREPARE:, ARCHITECT:,
CODE:, TEST:). If so, emits a systemMessage reminder to persist organizational
state to feature task metadata.

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin with tool_name, tool_input, tool_output
Output: JSON systemMessage on stdout if phase completion detected, nothing otherwise
"""

import json
import sys

from shared.error_output import hook_error_json

PHASE_KEYWORDS = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")

REMINDER_MESSAGE = (
    "Phase complete -- save organizational state (active agents, phase status, "
    "memory layer status) to feature task metadata."
)


def check_phase_completion(tool_input: dict) -> bool:
    """Determine if a phase task is being marked completed.

    Args:
        tool_input: The TaskUpdate tool's input parameters

    Returns:
        True if this is a phase task being set to completed
    """
    if tool_input.get("status") != "completed":
        return False

    subject = tool_input.get("subject", "")
    return any(keyword in subject for keyword in PHASE_KEYWORDS)


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            sys.exit(0)

        tool_input = input_data.get("tool_input", {})

        if check_phase_completion(tool_input):
            print(json.dumps({"systemMessage": REMINDER_MESSAGE}))

        sys.exit(0)

    except Exception as e:
        # Fail open -- never block tool use
        print(f"Hook warning (phase_snapshot_reminder): {e}", file=sys.stderr)
        print(hook_error_json("phase_snapshot_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
