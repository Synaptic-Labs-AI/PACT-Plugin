#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_prompt_gate.py
Summary: UserPromptSubmit hook that injects a bootstrap-first instruction
         alongside every user message until the bootstrap-complete marker exists.
Used by: hooks.json UserPromptSubmit hook (no matcher — fires on every prompt)

Layer 2 of the four-layer bootstrap gate enforcement (#401). On each user
message, checks for the session-scoped bootstrap-complete marker file:
  - Marker exists → suppressOutput (zero tokens, sub-ms)
  - No marker + PACT team-lead session → inject additionalContext instructing bootstrap
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

import shared.pact_context as pact_context
from bootstrap_gate import is_marker_set
from shared import BOOTSTRAP_MARKER_NAME

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

_BOOTSTRAP_INSTRUCTION_TEMPLATE = (
    "REQUIRED: Before responding to this message, invoke "
    'Skill("PACT:bootstrap"). Code-editing tools (Edit, Write) and agent '
    "dispatch (Task) are mechanically blocked until bootstrap completes. "
    "This loads your operating instructions, governance policy, and "
    "workflow protocols."
    "{session_dir_hint}"
)

_SESSION_DIR_HINT = (
    "\n\nPACT_SESSION_DIR={session_dir}"
)


def _check_bootstrap_needed(input_data: dict) -> str | None:
    """Determine whether a bootstrap instruction should be injected.

    Returns the additionalContext string to inject, or None if the gate
    should be a no-op (marker exists, non-PACT session, or teammate).
    """
    # Initialize context (sets session-scoped path from input_data)
    pact_context.init(input_data)

    # Fast path: check marker first (cheapest check, most common case)
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        # No session dir → non-PACT session or uninitialized context → no-op
        return None

    # Use the same safe-marker-check helper as the sibling
    # bootstrap_gate.py so both enforcement points share one safe-check
    # contract. The helper enforces S2 (planted-symlink-at-marker
    # rejection via os.lstat + S_ISREG) + S4 (ancestor-symlink rejection
    # via Path.resolve containment).
    if is_marker_set(Path(session_dir)):
        # Bootstrap already done → suppress (zero tokens)
        return None

    # Teammate detection: teammates don't need the team-lead's bootstrap gate
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    # Lead session, no marker → inject bootstrap instruction with session dir
    return _BOOTSTRAP_INSTRUCTION_TEMPLATE.format(
        session_dir_hint=_SESSION_DIR_HINT.format(session_dir=session_dir)
    )


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
        # hookEventName is required by the harness; missing it silently fails open
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
