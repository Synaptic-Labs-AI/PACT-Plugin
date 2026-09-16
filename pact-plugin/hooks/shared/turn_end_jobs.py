"""
Location: pact-plugin/hooks/shared/turn_end_jobs.py
Summary: Which running `background_tasks` entries count as jobs at a turn end,
         for each role.
Used by: shared/turn_end_gate.py (narrowing by role),
         hooks/stop_background_gate.py (its fast path, loading this file by
         path) and hooks/validate_handoff.py (its fast path).

The sets are allowlists written in the frame's type labels. A type the
platform adds later is not counted, so the gate under-blocks for it; counting
it would refuse a turn end once per entry of a type that may not be work at
all, the way a `teammate` entry is not.

This file imports nothing but the `__future__` directive, so the Stop hook can
load it without importing the `shared` package.
"""

from __future__ import annotations

# The lead's process lists every teammate's and subagent's work too, with no
# owner; only shell launches are attributable, through Layer 1's record.
#
# `subagent` IS DELIBERATELY ABSENT. Counting a subagent entry for the lead needs
# a row the lead owns, and none is written. The lead's candidates are every
# counted job MINUS the recorded ones, so counting subagent entries without such
# a row would refuse the lead over every subagent entry it cannot attribute —
# its own, and every teammate's.
LEAD_JOB_TYPES = frozenset({"shell"})

# A separate-process teammate's list holds only its own process's work.
OWN_PROCESS_JOB_TYPES = frozenset({"shell", "subagent", "monitor", "workflow", "MCP task"})

# An in-process teammate sees the lead process's list; only its recorded shell
# launches can be attributed to it.
IN_PROCESS_TEAMMATE_JOB_TYPES = frozenset({"shell"})

ANY_JOB_TYPES = LEAD_JOB_TYPES | OWN_PROCESS_JOB_TYPES | IN_PROCESS_TEAMMATE_JOB_TYPES


def running_jobs(input_data, job_types=ANY_JOB_TYPES) -> list:
    """Running `background_tasks` entries whose type is in `job_types`.

    An entry counts only as a dict with `status` "running", a non-empty string
    `id` and a `type` in the set. Anything else, including a frame that is not
    a dict or a `background_tasks` that is not a list, gives [].
    """
    if not isinstance(input_data, dict):
        return []
    entries = input_data.get("background_tasks")
    if not isinstance(entries, list):
        return []
    return [
        e for e in entries
        if isinstance(e, dict)
        and e.get("status") == "running"
        and isinstance(e.get("id"), str)
        and e["id"]
        and e.get("type") in job_types
    ]
