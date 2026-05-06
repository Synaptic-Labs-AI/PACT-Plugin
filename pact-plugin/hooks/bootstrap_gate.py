#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_gate.py
Summary: PreToolUse hook that blocks code-editing and agent-dispatch tools
         (Edit, Write, Task, NotebookEdit) until the bootstrap-complete
         marker exists.
Used by: hooks.json PreToolUse hook (no matcher — fires for all hookable tools)

Layer 3 of the four-layer bootstrap gate enforcement (#401). On each tool
call, checks the session-scoped bootstrap-complete marker:
  - Marker exists → suppressOutput (sub-ms fast path)
  - Non-PACT session → suppressOutput (no-op)
  - Teammate → suppressOutput (no-op)
  - Code-editing/agent-dispatch tool (Edit, Write, Task, NotebookEdit) → deny
  - Operational/exploration tool (Read, Glob, Grep, Bash, WebFetch,
    WebSearch, AskUserQuestion, ExitPlanMode, any MCP tool) → allow

Tool classification rationale:
  - Blocked tools are structured code modification (Edit, Write) and agent
    dispatch (Task, NotebookEdit) actions that shouldn't run before
    governance is loaded. The agent-dispatch tool name is `Task` — the
    canonical platform name for sub-agent spawning, confirmed by the
    matcher='Task' entries in hooks.json (PreToolUse team_guard +
    PostToolUse auditor_reminder).
  - Bash is ALLOWED because the bootstrap marker-write mechanism itself is
    a Bash command in bootstrap.md — blocking Bash would create a circular
    dependency where the gate can never self-disable.
  - Exploration tools are read-only and needed for state recovery after
    compaction.
  - MCP tools are always allowed — they're external integrations that may
    be needed for context gathering.
  - Non-hookable tools (Skill, ToolSearch, TaskList/TaskGet/TaskUpdate,
    SendMessage) never reach this hook because they don't fire PreToolUse
    events. Note: TaskList/TaskGet/TaskUpdate are PACT plugin task-system
    tools, distinct from the agent-dispatch `Task` tool that IS blocked.

SACROSANCT: every raisable path is wrapped in try/except that defaults to
allow (exit 0 with suppressOutput). A gate bug must never block a tool call.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {"suppressOutput": true} (allow / passthrough)
"""

import json
import os
import stat
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared import BOOTSTRAP_MARKER_NAME

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Code-editing and agent-dispatch tools blocked until bootstrap completes.
# Bash is intentionally NOT blocked — the marker-write mechanism in
# bootstrap.md is a Bash command, so blocking Bash would prevent the gate
# from ever self-disabling (circular dependency). The agent-dispatch tool
# is `Task` (the canonical platform tool name); cross-evidence in
# hooks.json: PreToolUse team_guard + PostToolUse auditor_reminder both
# use matcher='Task' and fire correctly in production.
_BLOCKED_TOOLS = frozenset({
    "Edit",
    "Write",
    "Task",
    "NotebookEdit",
})

_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Code-editing tools (Edit, Write) and agent dispatch (Task) are blocked "
    "until bootstrap completes. Bash, Read, Glob, Grep are available."
)


def is_marker_set(session_dir: Path | None) -> bool:
    """Public predicate: does a real bootstrap-complete marker exist?

    Returns True iff `<session_dir>/<BOOTSTRAP_MARKER_NAME>` exists as a
    REGULAR FILE (not a symlink, not a directory) AND no ancestor of the
    session_dir is a symlink. False on any of:
      - session_dir is None or falsy
      - marker path is a symlink (S2 defense: planted symlink at the
        marker would otherwise satisfy `Path.exists()` since exists()
        follows symlinks)
      - marker path is a directory or other non-regular file
      - marker path does not exist
      - any ancestor of session_dir is a symlink (S4 defense: a planted
        symlink at e.g. ~/.claude redirecting to attacker-controlled
        directory would otherwise allow attacker to plant a regular-file
        marker satisfying the leaf-only check)
      - any OSError on stat (treated as marker-absent → fail-closed for
        the gate-bypass class; gate stays armed)

    The plan §High-Risk-TDD-Specs Q4 names this as a 7-method TDD target;
    extracting it as a public callable closes the plan-vs-implementation
    gap (architect-review Findings #1 + #14). Callers (current:
    `_check_tool_allowed`; future: any session-end audit, sibling hooks)
    use this single entry point for the safe-marker-check contract.

    Security rationale:
      - S2 (security-engineer-review): `marker_path.exists()` follows
        symlinks → attacker with same-user write access plants a symlink
        at <session_dir>/bootstrap-complete pointing to any existing
        file (e.g., /etc/hostname) → gate falsely satisfied → tool
        block bypassed. Replaced with `os.lstat()` + `stat.S_ISREG()`
        which checks the leaf without following symlinks.
      - S4: leaf-only is_symlink() does not detect ancestor symlinks
        (e.g., ~/.claude itself being a symlink to attacker-controlled
        /tmp/evil/.claude). `Path.resolve(strict=False)` walks every
        ancestor; comparing to the unresolved path detects any
        ancestor-link rewrite.
    """
    if not session_dir:
        return False
    session_dir = Path(session_dir)

    # S4: ancestor-symlink defense. Path.resolve() follows ALL symlinks
    # in the path; if the resolved path differs from the input path
    # (modulo absolute-form), some ancestor was a symlink. strict=False
    # so we don't raise if the marker file itself doesn't exist yet.
    try:
        resolved = session_dir.resolve(strict=False)
    except OSError:
        return False
    if resolved != session_dir.absolute():
        return False

    marker_path = session_dir / BOOTSTRAP_MARKER_NAME

    # S2: lstat (does NOT follow symlinks) + S_ISREG (regular file only).
    # The marker is a sentinel file whose CONTENT is not consumed; the
    # contract is "regular file at this exact path". A symlink at the
    # marker path is rejected even if it points to a regular file
    # elsewhere (the link-target is attacker-chosen).
    try:
        st = os.lstat(str(marker_path))
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether a tool call should be denied.

    Returns the deny reason string if the tool should be blocked, or None
    if the tool call should be allowed through.
    """
    pact_context.init(input_data)

    # Fast path: marker exists (as a regular non-symlink file) → allow
    # everything. See `is_marker_set` for S2/S4 defense rationale.
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    if is_marker_set(Path(session_dir)):
        return None

    # Teammate detection
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    # Lead session, no marker — check tool classification
    tool_name = input_data.get("tool_name", "")

    # MCP tools always allowed (external integrations)
    if isinstance(tool_name, str) and tool_name.startswith("mcp__"):
        return None

    # Blocked implementation tools
    # frozenset membership is type-safe — no isinstance guard needed
    if tool_name in _BLOCKED_TOOLS:
        return _DENY_REASON

    # All other hookable tools (Read, Glob, Grep, Bash, WebFetch, WebSearch,
    # AskUserQuestion, ExitPlanMode) are operational/exploration tools — allow
    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception:
        # Any exception in gate logic → fail-open
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
