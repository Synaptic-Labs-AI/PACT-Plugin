#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_staleness_gate.py
Summary: PreToolUse marker-gate that denies Edit/Write on the project
         CLAUDE.md's Pinned Context section when a stale-pins-pending
         marker is present in the session directory.
Used by: hooks.json PreToolUse with matcher \"Edit|Write\"

Phase F defense-in-depth backstop for #492. The SessionStart
additionalContext directive (session_init.py step 4b) is the primary
enforcement; this hook is the secondary guard that fires at the moment
of the Edit/Write call rather than relying on the orchestrator honoring
the directive.

Gate triggers only when ALL hold:
  1. Tool is Edit or Write (enforced by hooks.json matcher)
  2. Target file path resolves to the project CLAUDE.md
  3. Edit locus is within the Pinned Context section (line-bounded)
  4. Stale-pins-pending marker exists in session_dir
  5. Not a teammate session (teammates bypass per bootstrap_gate precedent)

SACROSANCT: every raisable path is wrapped in try/except that defaults
to allow (exit 0 with suppressOutput). A gate bug must never block a
tool call. Fail-open: missing session_dir, unparseable CLAUDE.md,
unresolvable marker → allow.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {\"suppressOutput\": true} (allow / passthrough)
"""

import json
import sys
from pathlib import Path

import shared.pact_context as pact_context

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Marker file name written when stale-pins-pending state is detected.
# Placed in session_dir so it is per-session scoped — clears on new
# session, cannot persist across /clear per bootstrap_gate precedent.
PIN_STALENESS_MARKER_NAME = "pin-staleness-pending"

_DENY_REASON = (
    "Pinned Context edits are gated: stale pins detected. "
    "Run /PACT:pin-memory to archive stale pins before editing "
    "the ## Pinned Context section of CLAUDE.md."
)

_GATED_TOOLS = frozenset({"Edit", "Write"})


def _is_project_claude_md(file_path_str: str) -> bool:
    """Return True if file_path resolves to the project CLAUDE.md.

    Worktree-safe: imports staleness.get_project_claude_md_path lazily to
    avoid module-level import cost on every Edit/Write call.
    """
    if not file_path_str:
        return False

    try:
        from staleness import get_project_claude_md_path
    except ImportError:
        return False

    project_md = get_project_claude_md_path()
    if project_md is None:
        return False

    try:
        target = Path(file_path_str).resolve()
        canonical = project_md.resolve()
    except (OSError, RuntimeError):
        return False

    return target == canonical


def _edit_touches_pinned_section(tool_input: dict) -> bool:
    """Return True if the Edit/Write target locus overlaps Pinned Context.

    For Write: we treat the whole file as in-scope (the write replaces
    everything, so Pinned Context is necessarily affected if present).

    For Edit: inspect old_string/new_string for a 'Pinned Context'
    substring marker. Conservative: if we can't tell, allow — the
    SessionStart directive is the primary enforcement, this hook only
    catches high-confidence violations.
    """
    if "content" in tool_input:
        # Write tool — full-file replacement
        return True

    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")

    # Conservative substring match. Any edit that mentions Pinned Context
    # boundary markers or a pin comment is treated as in-scope.
    combined = f"{old_string}\n{new_string}"
    markers = (
        "## Pinned Context",
        "<!-- pinned:",
        "<!-- PACT_MEMORY_",
    )
    return any(marker in combined for marker in markers)


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether the tool call should be denied.

    Returns the deny reason string if blocked, or None to allow.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name not in _GATED_TOOLS:
        return None

    pact_context.init(input_data)

    # Teammate bypass — teammates don't edit project CLAUDE.md
    # (worktree scope rule), so this gate is lead-only.
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    marker_path = Path(session_dir) / PIN_STALENESS_MARKER_NAME
    if not marker_path.exists():
        return None

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None

    file_path_str = tool_input.get("file_path", "")
    if not _is_project_claude_md(file_path_str):
        return None

    if not _edit_touches_pinned_section(tool_input):
        return None

    return _DENY_REASON


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception:
        # SACROSANCT: any exception in gate logic → fail-open.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if deny_reason:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
