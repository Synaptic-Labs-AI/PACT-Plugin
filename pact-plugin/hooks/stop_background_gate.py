#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/stop_background_gate.py
Summary: Stop hook. Refuses a turn end, once per job, while background work
         the ending session is responsible for is still running with nothing
         scheduled or flagged to follow it up.
Used by: hooks.json Stop (synchronous). The decision is shared with
         validate_handoff.py (SubagentStop) through shared/turn_end_gate.py.

# livelock-safe: a block is printed at most once per background job id (the
# told-once record in the session folder), and never while stop_hook_active
# is set, so a turn end the agent cannot satisfy is not refused again.

This runs at every turn end of every session with the plugin installed, so
no plugin module is imported until a running job is found.

Input: JSON on stdin; reads hook_event_name, background_tasks, session_crons,
       stop_hook_active, session_id, agent_type and agent_id.
Output: {"decision": "block", "reason": ...} to refuse the stop; nothing when
        no job is running; {"suppressOutput": true} otherwise. Always exit 0.
"""

from __future__ import annotations

import json
import sys

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _has_running_job(input_data: dict) -> bool:
    """turn_end_gate.running_entries's test, without importing the plugin."""
    entries = input_data.get("background_tasks")
    return isinstance(entries, list) and any(
        isinstance(e, dict)
        and e.get("status") == "running"
        and isinstance(e.get("id"), str)
        and e["id"]
        for e in entries
    )


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    if not isinstance(input_data, dict) or input_data.get("hook_event_name") != "Stop":
        return
    if not _has_running_job(input_data):
        return
    try:
        from shared import turn_end_gate
    except Exception:
        print(_SUPPRESS_OUTPUT)
        return
    verdict = turn_end_gate.evaluate(input_data)
    if verdict is not None and verdict.blocks:
        print(json.dumps({"decision": "block", "reason": verdict.reason}))
        turn_end_gate.mark_told(verdict)
    else:
        print(_SUPPRESS_OUTPUT)
    if verdict is not None:
        turn_end_gate.write_trace(verdict)


if __name__ == "__main__":
    main()
