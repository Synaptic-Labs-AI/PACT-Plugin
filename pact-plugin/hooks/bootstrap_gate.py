#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_gate.py
Summary: PreToolUse hook that blocks code-editing and agent-dispatch tools
         (Edit, Write, Agent, NotebookEdit) until the bootstrap-complete
         marker exists.
Used by: hooks.json PreToolUse hook (no matcher — fires for all hookable tools)

Layer 3 of the four-layer bootstrap gate enforcement (#401). On each tool
call, checks the session-scoped bootstrap-complete marker:
  - Marker exists AND is properly stamped (F24) → suppressOutput (sub-ms fast path)
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
  - Bash is ALLOWED because the bootstrap marker-write mechanism itself is
    a Bash command in bootstrap.md — blocking Bash would create a circular
    dependency where the gate can never self-disable. To prevent F18
    Bash-marker-bypass exploitation, is_marker_set verifies marker CONTENT
    (F24 SHA256 sentinel), not just file presence — `touch bootstrap-complete`
    no longer satisfies the gate.
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


# ─── F25: fail-closed wrapper around cross-package imports + risky module work ─
try:
    import hashlib
    import hmac
    import stat
    from pathlib import Path

    import shared.pact_context as pact_context
    from shared import BOOTSTRAP_MARKER_NAME
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Code-editing and agent-dispatch tools blocked until bootstrap completes.
# Bash is intentionally NOT blocked — the marker-write mechanism in
# bootstrap.md is a Bash command, so blocking Bash would prevent the gate
# from ever self-disabling (circular dependency). To prevent F18
# Bash-marker-bypass exploitation, is_marker_set verifies marker CONTENT
# (F24 SHA256 sentinel), not just file presence. The agent-dispatch tool
# is `Agent` — the canonical Claude Code platform name (#662 corrects
# 4c286c1f's incorrect rename direction). hooks.json matcher='Agent'
# entries fire on Agent invocations.
_BLOCKED_TOOLS = frozenset({
    "Edit",
    "Write",
    "Agent",
    "NotebookEdit",
})

# F24 marker schema version. Bump if marker JSON shape changes; verifier
# rejects unknown versions. Producer (commands/bootstrap.md) must emit a
# matching `v` field.
F24_MARKER_VERSION = 1

# F24 marker file size cap (bytes). The marker JSON is a small fixed schema
# ({v, sid, sig}); a content larger than this is rejected to defend against
# pathological reads.
_F24_MARKER_MAX_BYTES = 256

_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Code-editing tools (Edit, Write) and agent dispatch (Agent) are blocked "
    "until bootstrap completes. Bash, Read, Glob, Grep are available."
)


def _expected_marker_signature(session_id: str, plugin_root: str,
                                plugin_version: str, marker_version: int) -> str:
    """Compute the expected SHA256 marker signature (F24).

    Inputs are joined with `|` separators in a fixed order so the producer
    in commands/bootstrap.md and this verifier compute the same digest:

        sha256(f"{session_id}|{plugin_root}|{plugin_version}|{marker_version}")

    The `marker_version` is part of the digest so a format-version bump
    invalidates pre-bump markers automatically.
    """
    payload = f"{session_id}|{plugin_root}|{plugin_version}|{marker_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_marker_set(session_dir: "Path | None") -> bool:
    """Public predicate: does a properly-stamped bootstrap-complete marker exist?

    Returns True iff `<session_dir>/<BOOTSTRAP_MARKER_NAME>` exists as a
    REGULAR FILE (not a symlink, not a directory) AND no ancestor of the
    session_dir is a symlink AND its content is a valid F24 stamp:
      - file size ≤ ``_F24_MARKER_MAX_BYTES``
      - parses as JSON object with EXACTLY keys {"v", "sid", "sig"}
      - ``v`` is integer == ``F24_MARKER_VERSION``
      - ``sid`` equals ``session_dir.name`` (binds marker to its session)
      - ``sig`` matches ``_expected_marker_signature`` via
        ``hmac.compare_digest`` (constant-time compare)

    Returns False on any of:
      - session_dir is None or falsy
      - marker path is a symlink (S2 defense)
      - marker path is a directory or other non-regular file (S2 corollary)
      - any ancestor of session_dir is a symlink (S4 defense)
      - any OSError on stat (treated as marker-absent)
      - F24 content fails any of size cap / JSON parse / key set / version /
        sid match / signature match
      - missing plugin context (cannot compute expected signature)

    Security rationale (S2 + S4 unchanged from pre-#662; F24 added):
      - S2 (security-engineer-review): `marker_path.exists()` follows
        symlinks → attacker plants a symlink at the marker path → gate
        falsely satisfied → tool block bypassed. Defense: `os.lstat()` +
        `stat.S_ISREG()` checks the leaf without following symlinks.
      - S4: leaf-only is_symlink() does not detect ancestor symlinks
        (e.g., ~/.claude itself being a symlink). `Path.resolve(strict=False)`
        walks every ancestor; comparing to the unresolved path detects any
        ancestor-link rewrite.
      - F18/F24 (#662): `Bash("touch <path>/bootstrap-complete")` previously
        defeated the gate because file PRESENCE was the only check.
        F24 verifies marker CONTENT bound to (session_id, plugin_root,
        plugin_version, marker_version) — a marker-content provenance
        check that closes the trivial Bash-touch bypass.

        F24 is NOT cryptographic provenance. All four signature inputs
        are readable from the same-user filesystem (session_id and
        plugin_root from pact-session-context.json, plugin_version from
        plugin.json, marker_version from this module's
        F24_MARKER_VERSION constant), so a same-user attacker with
        Python execution and read access to those files can recompute
        the digest. F24 is a fingerprint that raises attacker effort
        from a one-line `touch` to a multi-line script-AND-read
        sequence and creates a detection surface (the digest is
        deterministic, so a forgery that races plugin-version bumps
        is observable). It is not a MAC; treat any future hardening
        that would require unforgeability as a separate threat model.
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

    # F24: verify marker CONTENT (#662 — closes F18 Bash-touch bypass).
    try:
        if st.st_size <= 0 or st.st_size > _F24_MARKER_MAX_BYTES:
            return False
        content = marker_path.read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return False
        if set(parsed.keys()) != {"v", "sid", "sig"}:
            return False
        if not isinstance(parsed["v"], int) or parsed["v"] != F24_MARKER_VERSION:
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
        expected = _expected_marker_signature(
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

    # Fast path: marker exists (as a properly-stamped F24 regular file) →
    # allow everything. See `is_marker_set` for S2/S4/F24 defense rationale.
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
        # F25: fail-CLOSED — runtime gate-logic failure must DENY (#658
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
