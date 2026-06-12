#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/team_guard.py
Summary: PreToolUse hook matching Task — blocks agent dispatch if team_name
         is specified but the team doesn't exist yet.
Used by: hooks.json PreToolUse hook (matcher: Task)

Enforces the "create team before dispatching agents" rule at the platform
level, replacing the prompt-based reminder.

Input: JSON from stdin with tool_input containing Task parameters
Output: JSON with hookSpecificOutput.permissionDecision if blocking
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ─
import json
import sys
from pathlib import Path
from typing import NoReturn


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-closed deny for module-load failure. Mirrors PR #660
    ``merge_guard_pre._emit_load_failure_deny`` / ``dispatch_gate`` analogue.

    Without this, a raise from the cross-package import below would crash the
    hook (exit 1), which the platform treats as a NON-blocking PreToolUse hook
    — the Task tool would PROCEED and the team-existence gate would silently
    FAIL-OPEN. Emitting a deny + exit 2 keeps the gate fail-CLOSED. hookEventName
    MUST be present.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT team_guard {stage} failure — blocking for safety. "
                f"{type(error).__name__}: {error}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (team_guard / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── fail-closed wrapper on cross-package imports ──────────────────────────
# The team-existence check derives the teams dir from the shared
# CLAUDE_CONFIG_DIR resolver. Add hooks/ to sys.path (mirrors the sibling
# PreToolUse hooks), then import under the fail-closed wrapper so a load failure
# DENIES rather than crash-and-fail-open.
try:
    _hooks_dir = Path(__file__).parent
    if str(_hooks_dir) not in sys.path:
        sys.path.insert(0, str(_hooks_dir))

    from shared.paths import get_claude_config_dir
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


def check_team_exists(tool_input: dict, teams_dir: str | None = None) -> str | None:
    """
    Check if the team specified in a Task call exists.

    Args:
        tool_input: The Task tool's input parameters
        teams_dir: Override for teams directory (for testing)

    Returns:
        Error message if team doesn't exist, None if OK
    """
    team_name = tool_input.get("team_name")
    if not team_name:
        return None  # No team_name = not a team dispatch, allow
    team_name = team_name.lower()

    if teams_dir is None:
        teams_dir = str(get_claude_config_dir() / "teams")

    team_config = Path(teams_dir) / team_name / "config.json"
    if not team_config.exists():
        return (
            f"Team '{team_name}' does not exist yet. "
            f"Call TeamCreate(team_name=\"{team_name}\") before dispatching agents."
        )

    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    error = check_team_exists(tool_input)

    if error:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": error,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
