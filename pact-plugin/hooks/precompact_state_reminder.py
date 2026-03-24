#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/precompact_state_reminder.py
Summary: PreCompact hook that reminds the orchestrator to persist workflow state
         to feature task metadata before context compaction occurs.
Used by: hooks.json PreCompact hook

Emits a systemMessage nudging the orchestrator to save phase status, active
agents, and variety score via TaskUpdate before the context window is compacted.

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin (PreCompact event data)
Output: JSON systemMessage on stdout
"""

import json
import sys

from shared.error_output import hook_error_json

REMINDER_MESSAGE = (
    "Compaction imminent -- persist current phase status, active agents, "
    "and variety score to feature task metadata via TaskUpdate before "
    "context is lost."
)


def main():
    try:
        # Consume stdin (PreCompact may provide transcript_path, etc.)
        try:
            json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            pass

        print(json.dumps({"systemMessage": REMINDER_MESSAGE}))
        sys.exit(0)

    except Exception as e:
        # Fail open -- never block compaction
        print(f"Hook warning (precompact_state_reminder): {e}", file=sys.stderr)
        print(hook_error_json("precompact_state_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
