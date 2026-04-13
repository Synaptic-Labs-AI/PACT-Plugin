#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_prompt_gate.py
Summary: UserPromptSubmit hook that injects a bootstrap-first instruction
         alongside every user message until the bootstrap-complete marker exists.
Used by: hooks.json UserPromptSubmit hook (no matcher — fires on every prompt)

Layer 2 of the four-layer bootstrap gate enforcement (#401). On each user
message, checks for the session-scoped bootstrap-complete marker file:
  - Marker exists → suppressOutput (zero tokens, sub-ms)
  - No marker + PACT lead session → inject additionalContext instructing bootstrap
  - Non-PACT session (no context file) → no-op passthrough
  - Teammate (resolve_agent_name non-empty) → no-op passthrough

SACROSANCT: every raisable path is wrapped in try/except that defaults to
allow (exit 0 with suppressOutput). A gate bug must never block a user prompt.

Input: JSON from stdin with hook_event_name, session_id, prompt, etc.
Output: JSON with hookSpecificOutput.additionalContext (inject case)
        or {"suppressOutput": true} (fast path / passthrough)
"""

import json
import sys
from pathlib import Path

# Inline shared-module imports inside main() to keep the module importable
# for testing even when shared/ is not on sys.path.

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

_BOOTSTRAP_INSTRUCTION = (
    "REQUIRED: Before responding to this message, invoke "
    'Skill("PACT:bootstrap"). Implementation tools (Edit, Write, Bash, Agent) '
    "are mechanically blocked until bootstrap completes. This loads your "
    "operating instructions, governance policy, and workflow protocols."
)

_MARKER_NAME = "bootstrap-complete"


def _check_bootstrap_needed(input_data: dict) -> str | None:
    """Determine whether a bootstrap instruction should be injected.

    Returns the additionalContext string to inject, or None if the gate
    should be a no-op (marker exists, non-PACT session, or teammate).
    """
    # Import shared modules here so the top-level module remains importable
    # without sys.path manipulation (useful for test fixtures).
    try:
        sys.path.insert(
            0,
            str(Path(__file__).resolve().parent),
        )
        from shared import pact_context
    finally:
        if sys.path and sys.path[0] == str(Path(__file__).resolve().parent):
            sys.path.pop(0)

    # Initialize context (sets session-scoped path from input_data)
    pact_context.init(input_data)

    # Fast path: check marker first (cheapest check, most common case)
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        # No session dir → non-PACT session or uninitialized context → no-op
        return None

    marker_path = Path(session_dir) / _MARKER_NAME
    if marker_path.exists():
        # Bootstrap already done → suppress (zero tokens)
        return None

    # Teammate detection: teammates don't need the lead's bootstrap gate
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    # Lead session, no marker → inject bootstrap instruction
    return _BOOTSTRAP_INSTRUCTION


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-open
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        instruction = _check_bootstrap_needed(input_data)
    except Exception:
        # Any exception in gate logic → fail-open
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if instruction:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": instruction,
            }
        }
        print(json.dumps(output))
    else:
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
