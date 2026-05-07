#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_gate.py
Summary: PreToolUse hook that blocks code-editing and agent-dispatch tools
         (Edit, Write, Agent, NotebookEdit) until the bootstrap-complete
         marker exists.
Used by: hooks.json PreToolUse hook (no matcher — fires for all hookable tools)

Layer 3 of the four-layer bootstrap gate enforcement (#401). On each tool
call, checks the session-scoped bootstrap-complete marker:
  - Marker exists AND its content is a valid stamp → suppressOutput (sub-ms fast path)
  - Non-PACT session → suppressOutput (no-op)
  - Teammate → suppressOutput (no-op)
  - Code-editing/agent-dispatch tool (Edit, Write, Agent, NotebookEdit) → deny
  - Operational/exploration tool (Read, Glob, Grep, Bash, WebFetch,
    WebSearch, AskUserQuestion, ExitPlanMode, any MCP tool) → allow

Tool classification rationale:
  - Blocked tools are structured code modification (Edit, Write) and agent
    dispatch (Agent, NotebookEdit) actions that shouldn't run before
    governance is loaded. The agent-dispatch tool name is `Agent` — the
    canonical Claude Code platform name (verified against
    code.claude.com/docs/en/agent-teams.md and sub-agents.md as of
    2026-05-06; #662). hooks.json matcher='Agent' entries (PreToolUse
    team_guard + PostToolUse auditor_reminder) fire on Agent invocations.
    Earlier `Task` literal in this file (commit 4c286c1f, 2026-05-05)
    was based on a misread of production matchers — those matchers were
    silently NOT firing on spawn events, mistaken for "production
    evidence". Resolved in #662.
  - Bash is ALLOWED because the orchestrator legitimately needs it during
    the bootstrap window — before the marker exists — for git status,
    plugin-version reads, project-state probing, and other read-only
    investigation that the bootstrap ritual itself depends on. The marker
    is now written by the `bootstrap_marker_writer` UserPromptSubmit hook
    (no Bash heredoc), so the historical "blocking Bash would prevent the
    gate from self-disabling" framing no longer applies. Bash bypass is
    defended at the verifier instead: `is_marker_set` checks marker
    CONTENT via a SHA256 fingerprint over (session_id, plugin_root,
    plugin_version, schema_version), so neither `touch bootstrap-complete`
    nor a `Bash`-driven echo of a malformed JSON satisfies the gate.
  - Exploration tools are read-only and needed for state recovery after
    compaction.
  - MCP tools are always allowed — they're external integrations that may
    be needed for context gathering.
  - Non-hookable tools (Skill, ToolSearch, TaskList/TaskGet/TaskUpdate,
    SendMessage) never reach this hook because they don't fire PreToolUse
    events. Note: TaskList/TaskGet/TaskUpdate are PACT plugin task-system
    tools, distinct from the agent-dispatch `Agent` tool that IS blocked.

SACROSANCT (post-#662): module-load failures and runtime gate-logic
exceptions are fail-CLOSED (deny) per #658 defect class. Only malformed
stdin remains fail-OPEN (input-side failure → harness's domain).

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {"suppressOutput": true} (allow / passthrough)
"""

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ───
import json
import os
import sys
from typing import NoReturn


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed deny for module-load or runtime gate-logic failure.

    Mirrors PR #660 ``merge_guard_pre._emit_load_failure_deny``. Uses ONLY
    stdlib (json, sys) so it remains functional even when every wrapped
    import below fails. Audit anchor: hookEventName must be present in any
    deny output.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT bootstrap_gate {stage} failure — blocking for safety. "
                f"{type(error).__name__}: {error}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (bootstrap_gate / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── fail-closed wrapper around cross-package imports + risky module work ─────
try:
    import hmac
    import stat
    from pathlib import Path

    import shared.pact_context as pact_context
    from shared import BOOTSTRAP_MARKER_NAME
    from shared.marker_schema import (
        MARKER_MAX_BYTES,
        MARKER_SCHEMA_VERSION,
        expected_marker_signature,
    )
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Code-editing and agent-dispatch tools blocked until bootstrap completes.
# Bash is intentionally NOT blocked — the orchestrator needs it during the
# bootstrap window for read-only investigation (git status, plugin-version
# reads, project-state probing) that the bootstrap ritual itself depends
# on. Marker write is a hook (#664 bootstrap_marker_writer), no longer a
# Bash heredoc, so the older "blocking Bash would prevent self-disable"
# framing no longer applies. Bypass is defended at the verifier:
# is_marker_set checks marker CONTENT via a SHA256 fingerprint over
# (session_id, plugin_root, plugin_version, schema_version), so neither
# `touch bootstrap-complete` nor a Bash-driven echo of a malformed JSON
# satisfies the gate. The agent-dispatch tool is `Agent` — the canonical
# Claude Code platform name (#662 corrects 4c286c1f's incorrect rename
# direction). hooks.json matcher='Agent' entries fire on Agent invocations.
_BLOCKED_TOOLS = frozenset({
    "Edit",
    "Write",
    "Agent",
    "NotebookEdit",
})

# Marker schema constants (MARKER_SCHEMA_VERSION, MARKER_MAX_BYTES) and
# the signature function (expected_marker_signature) live in
# shared/marker_schema.py — the SSOT shared by producer
# (bootstrap_marker_writer.py) and this verifier. Imported above.

_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Code-editing tools (Edit, Write) and agent dispatch (Agent) are blocked "
    "until bootstrap completes. Bash, Read, Glob, Grep are available."
)


def is_marker_set(session_dir: "Path | None") -> bool:
    """Public predicate: does a properly-stamped bootstrap-complete marker exist?

    Returns True iff `<session_dir>/<BOOTSTRAP_MARKER_NAME>` exists as a
    REGULAR FILE (not a symlink, not a directory) AND no ancestor of the
    session_dir is a symlink AND its content is a valid stamp:
      - file size ≤ ``MARKER_MAX_BYTES``
      - parses as JSON object with EXACTLY keys {"v", "sid", "sig"}
      - ``v`` is integer == ``MARKER_SCHEMA_VERSION``
      - ``sid`` equals ``session_dir.name`` (binds marker to its session)
      - ``sig`` matches ``expected_marker_signature`` via
        ``hmac.compare_digest`` (constant-time compare)

    Returns False on any of:
      - session_dir is None or falsy
      - marker path is a symlink (S2 defense)
      - marker path is a directory or other non-regular file (S2 corollary)
      - any ancestor of session_dir is a symlink (S4 defense)
      - any OSError on stat (treated as marker-absent)
      - marker content fails any of size cap / JSON parse / key set / version /
        sid match / signature match
      - missing plugin context (cannot compute expected signature)

    Security rationale (symlink defenses unchanged from pre-#662;
    content fingerprint added):
      - S2 (security-engineer-review): `marker_path.exists()` follows
        symlinks → attacker plants a symlink at the marker path → gate
        falsely satisfied → tool block bypassed. Defense: `os.lstat()` +
        `stat.S_ISREG()` checks the leaf without following symlinks.
      - S4: leaf-only is_symlink() does not detect ancestor symlinks
        (e.g., ~/.claude itself being a symlink). `Path.resolve(strict=False)`
        walks every ancestor; comparing to the unresolved path detects any
        ancestor-link rewrite.
      - Bash-marker-bypass defense (#662): `Bash("touch <path>/bootstrap-complete")`
        previously defeated the gate because file PRESENCE was the only
        check. The verifier now verifies marker CONTENT bound to
        (session_id, plugin_root, plugin_version, marker_version) — a
        marker-content provenance check that closes the trivial
        Bash-touch bypass.

        The signature is NOT cryptographic provenance. All four
        signature inputs are readable from the same-user filesystem
        (session_id and plugin_root from pact-session-context.json,
        plugin_version from plugin.json, marker_version from
        shared.marker_schema.MARKER_SCHEMA_VERSION), so a same-user attacker
        with Python execution and read access to those files can
        recompute the digest. The signature is a fingerprint that
        raises attacker effort from a one-line `touch` to a multi-line
        script-AND-read sequence and creates a detection surface (the
        digest is deterministic, so a forgery that races plugin-version
        bumps is observable). It is not a MAC; treat any future
        hardening that would require unforgeability as a separate
        threat model.
    """
    if not session_dir:
        return False
    session_dir = Path(session_dir)

    # S4: ancestor-symlink defense. Path.resolve() follows ALL symlinks in
    # the path; if the resolved path differs from the absolute input path,
    # some ancestor was a symlink. strict=False so we don't raise if the
    # marker file itself doesn't exist yet.
    try:
        resolved = session_dir.resolve(strict=False)
    except OSError:
        return False
    if resolved != session_dir.absolute():
        return False

    marker_path = session_dir / BOOTSTRAP_MARKER_NAME

    # S2: lstat (does NOT follow symlinks) + S_ISREG (regular file only).
    try:
        st = os.lstat(str(marker_path))
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False

    # Verify marker CONTENT (#662 — closes the Bash-touch bypass).
    try:
        if st.st_size <= 0 or st.st_size > MARKER_MAX_BYTES:
            return False
        content = marker_path.read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return False
        if set(parsed.keys()) != {"v", "sid", "sig"}:
            return False
        if not isinstance(parsed["v"], int) or parsed["v"] != MARKER_SCHEMA_VERSION:
            return False
        if not isinstance(parsed["sid"], str) or parsed["sid"] != session_dir.name:
            return False
        if not isinstance(parsed["sig"], str):
            return False
        plugin_root = pact_context.get_plugin_root()
        if not plugin_root:
            return False
        plugin_json_path = Path(plugin_root) / ".claude-plugin" / "plugin.json"
        try:
            plugin_version = json.loads(
                plugin_json_path.read_text(encoding="utf-8")
            ).get("version", "")
        except (OSError, ValueError):
            return False
        if not plugin_version:
            return False
        expected = expected_marker_signature(
            parsed["sid"], plugin_root, plugin_version, parsed["v"]
        )
        if not hmac.compare_digest(parsed["sig"], expected):
            return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether a tool call should be denied.

    Returns the deny reason string if the tool should be blocked, or None
    if the tool call should be allowed through.
    """
    pact_context.init(input_data)

    # Fast path: marker exists (as a properly-stamped regular file with
    # valid content fingerprint) → allow everything. See `is_marker_set`
    # for symlink and bypass defense rationale.
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
        # Malformed stdin → fail-OPEN (input-side failure is harness's domain).
        # Cannot evaluate without input; cannot DENY meaningfully.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception as e:
        # Runtime fail-CLOSED — gate-logic exception must DENY (#658
        # sibling defect class). Pre-#662 this path was fail-OPEN.
        _emit_load_failure_deny("runtime", e)

    if deny_reason:
        # hookEventName is required by the harness; missing it silently fails open
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
