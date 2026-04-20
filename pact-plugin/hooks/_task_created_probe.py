#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/_task_created_probe.py
Summary: Ephemeral TaskCreated stdin-shape probe for #401 HIGH #2 uncertainty.
Used by: hooks.json TaskCreated hook (temporary — replaced in Commit #5
         by pact-plugin/hooks/task_schema_validator.py).

#401 architect HIGH #2 resolution (COMMIT-SEQUENCE.md §Commit #0): the
TaskCreated stdin payload shape is inferred from preparer R2's table
(task_id, task_subject, metadata, teammate_name, team_name) but not
empirically verified. Building task_schema_validator.py on inferred shape
risks either an infinite rejection loop (every creation blocked) or a
silent pass-through (gate never fires).

This probe writes the full stdin JSON to stderr on every TaskCreated
event so a subsequent manual TaskCreate reveals the exact field names and
nesting. Observations feed the validator's field-access patterns in
Commit #5.

Lifecycle:
  Commit #0 — this file ships + hooks.json registers it
  Run one or more TaskCreate events to populate stderr observations
  Commit #5 — this file is DELETED; task_schema_validator.py takes its
              hooks.json slot

SACROSANCT fail-open: ANY exception exits 0 with suppressOutput. A probe
bug must never block task creation. The probe itself is side-effect-free
(stderr echo is pure observation).

Input: JSON from stdin — shape to be observed
Output: JSON `{"suppressOutput": true}` on stdout (non-blocking observer)
"""

import json
import sys


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def main() -> None:
    try:
        raw = sys.stdin.read()
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            # Echo raw bytes (truncated) so malformed payloads still surface
            print(
                f"[probe] TaskCreated stdin (non-JSON, {len(raw)} chars): "
                f"{raw[:500]!r}",
                file=sys.stderr,
            )
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Pretty-print observed JSON; top-level keys + metadata keys are the
        # HIGH-value observation for the validator's access patterns.
        top_keys = sorted(data.keys()) if isinstance(data, dict) else []
        metadata_keys = (
            sorted(data.get("metadata", {}).keys())
            if isinstance(data, dict) and isinstance(data.get("metadata"), dict)
            else []
        )
        print(
            "[probe] TaskCreated observed: "
            f"top_keys={top_keys} metadata_keys={metadata_keys}",
            file=sys.stderr,
        )
        print(
            "[probe] TaskCreated full stdin:\n"
            + json.dumps(data, indent=2, sort_keys=True, default=str),
            file=sys.stderr,
        )
    except Exception as e:
        # Never raise; never block creation.
        print(f"[probe] exception: {e}", file=sys.stderr)

    # Always pass through: this is observation, not enforcement.
    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
