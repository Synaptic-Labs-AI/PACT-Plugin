#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/hallucination_gate.py
Summary: PreToolUse hook matching Bash — intercepts destructive Bash calls
         and verifies the authorizing user message exists as a genuine
         `type=user` entry in the session transcript, distinguishing
         genuine user input from orchestrator-hallucinated `Human:` turns.
Used by: hooks.json PreToolUse hook (matcher: Bash) — registered FIRST
         in the Bash chain, before git_commit_check and merge_guard_pre.

Defense layer 5 of the umbrella anti-hallucination defense set. Walks
the transcript JSONL backward over a bounded scan window, tracking the
most recent assistant text block containing the literal `Human:` and the
most recent genuine-shaped `type=user` entry (after envelope-exclusion
filter rejects platform-injected wrappers). Decision tree:

  - No `Human:` emission in scan window → ALLOW
  - Emission present AND no genuine user entry in scan window → DENY
  - Emission line-index > latest genuine user entry line-index → DENY
  - User entry more recent; substring tiers match → ALLOW
  - User entry more recent; all substring tiers miss → WARN (advisory
    additionalContext; temporal-anchor is the primary discriminator)

SACROSANCT failure semantics (mirrors merge_guard_pre / bootstrap_gate):
  - Module-load failure → DENY (fail-CLOSED)
  - Pattern-compile failure → DENY (fail-CLOSED)
  - Runtime gate-logic exception → DENY (fail-CLOSED)
  - Malformed stdin → ALLOW (fail-OPEN; harness contract failure)
  - Missing/unreadable transcript_path → ALLOW (cannot evaluate)

Audit anchor: every DENY output carries `hookEventName: "PreToolUse"`;
the harness silently fails open without it.

Known v1 limitations (see module-load docstring expansion in later
commit + hook docstring referenced from issue tracker):
  - Skill is non-hookable under PreToolUse; gate covers Bash only.
  - Wrapper-class hallucination (fake <system-reminder> emitted as
    assistant text) is out of scope; cryptographic-sentinel defense
    handles that class.
  - Recursive hallucination (same text emitted twice across a genuine
    user message landing in between) is out of scope.
  - AskUserQuestion answer descent into tool_result.content arrays
    deferred to v2; covered for Bash ops via merge_guard token system.

Input: JSON from stdin with tool_name, tool_input, transcript_path, cwd.
Output: JSON with `suppressOutput` (allow), `permissionDecision` (deny),
        or `additionalContext` (warn).
"""

import json
import sys
from typing import NoReturn


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-CLOSED deny for module-load or runtime gate-logic failure.

    Mirrors merge_guard_pre._emit_load_failure_deny and
    bootstrap_gate._emit_load_failure_deny. Uses ONLY stdlib (json, sys)
    so it remains functional even when every wrapped import below has
    failed. Audit anchor: hookEventName must be present in any deny
    output — the harness silently fails open without it.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT hallucination_gate {stage} failure — blocking for "
                f"safety. {type(error).__name__}: {error}. Check hook "
                f"installation and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (hallucination_gate, {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# Fail-CLOSED wrapper around all module-level risky work (cross-package
# imports + regex compilations). If ANY of this load fails (broken
# Python install, missing shared.pact_context, syntax error in
# merge_guard_pre, malformed regex), the harness sees a structured deny
# BEFORE the process exits — instead of an empty stdout that would fail
# open. Handler depends ONLY on stdlib (json, sys) imported above this
# block, so it remains functional even if every cross-package import
# below fails.
try:
    import re
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import shared.pact_context as pact_context  # noqa: E402
    from shared.pact_context import resolve_agent_name  # noqa: E402

    # _strip_non_executable_content lives in merge_guard_pre.py module-
    # level (not yet extracted to shared/merge_guard_common.py as of
    # this CODE-phase check). Import directly from the sibling hook
    # module rather than duplicating the ~170-line strip pipeline.
    from merge_guard_pre import _strip_non_executable_content  # noqa: E402
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module load", _module_load_error)


# Pre-serialized JSON for allow-path output: tells Claude Code UI to
# suppress the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def main() -> NoReturn:
    """Hook entry point. Scaffolding only in this commit — main logic
    lands in a subsequent commit. Current behavior: fail-OPEN ALLOW on
    every invocation so the hook is registration-safe pre-logic.
    """
    try:
        try:
            json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except (SystemExit, KeyboardInterrupt):
        # SystemExit is the success-path exit (sys.exit(0) above);
        # KeyboardInterrupt is operator-initiated. Neither is a
        # gate-logic failure — re-raise so the process exits normally.
        raise
    except Exception as runtime_error:  # noqa: BLE001 — SACROSANCT fail-closed
        _emit_load_failure_deny("runtime", runtime_error)


if __name__ == "__main__":
    main()
