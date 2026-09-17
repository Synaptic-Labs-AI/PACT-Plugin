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
the `shared` package is not imported until a running job of a counted type is
found. Before that, only the importless job filter, shared/turn_end_jobs.py,
is loaded, by file path.

Input: JSON on stdin; reads hook_event_name, background_tasks, session_crons,
       stop_hook_active, session_id, agent_type and agent_id.
Output: {"decision": "block", "reason": ...} to refuse the stop; nothing when
        no job is running; {"suppressOutput": true} otherwise. Always exit 0.
"""

from __future__ import annotations

import json
import sys

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _job_filter():
    """shared/turn_end_jobs.py, loaded by file path so `shared` is not imported."""
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared", "turn_end_jobs.py")
    spec = importlib.util.spec_from_file_location("_turn_end_jobs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    if not isinstance(input_data, dict) or input_data.get("hook_event_name") != "Stop":
        return
    try:
        jobs = _job_filter()
    except Exception:
        return
    if not jobs.running_jobs(input_data):
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
