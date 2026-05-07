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

SACROSANCT (post-#662 module-load fail-closed retrofit): module-load
failures emit an advisory `additionalContext` block at exit 0 —
UserPromptSubmit cannot
DENY the prompt itself, so the strongest signal we can send is to surface
the load-failure to the LLM via additionalContext so the user is informed
and the orchestrator persona can react. Runtime exceptions in gate logic
remain fail-OPEN (suppressOutput) because injecting bootstrap-required
text on a hook-side bug would mislead a healthy session into rebooting.

Input: JSON from stdin with hook_event_name, session_id, prompt, etc.
Output: JSON with hookSpecificOutput.additionalContext (inject case)
        or {"suppressOutput": true} (fast path / passthrough)
"""

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import sys
from typing import NoReturn


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed advisory for module-load failure.

    UserPromptSubmit cannot DENY the prompt; the strongest available signal
    is `additionalContext` injection. Uses ONLY stdlib (json, sys) so it
    remains functional even when every wrapped import below fails. Audit
    anchor: hookEventName must be present in any structured output.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"PACT bootstrap_prompt_gate {stage} failure — the hook "
                f"could not verify bootstrap state. {type(error).__name__}: "
                f"{error}. Until this is resolved, you should invoke "
                'Skill("PACT:bootstrap") before any code-editing or agent '
                "dispatch action; the companion `bootstrap_gate` PreToolUse "
                "will block those tools fail-closed."
            ),
        }
    }))
    print(
        f"Hook load error (bootstrap_prompt_gate / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ───────────────────────
try:
    from pathlib import Path

    import shared.pact_context as pact_context
    from bootstrap_gate import is_marker_set
    from shared import BOOTSTRAP_MARKER_NAME
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

_BOOTSTRAP_INSTRUCTION_TEMPLATE = (
    "REQUIRED: Before responding to this message, invoke "
    'Skill("PACT:bootstrap"). Code-editing tools (Edit, Write) and agent '
    "dispatch (Agent) are mechanically blocked until bootstrap completes. "
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
    # contract. The helper enforces leaf-symlink, ancestor-symlink, and
    # marker-content fingerprint defenses (post-#662).
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
        # Runtime exception in gate logic → fail-OPEN: injecting
        # bootstrap-required text on a hook-side bug would mislead a healthy
        # session. Module-load failures are handled separately (advisory) by
        # the module-load wrapper above.
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
