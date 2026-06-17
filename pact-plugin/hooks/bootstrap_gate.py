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
    2026-05-06; #662). hooks.json matcher='Agent' entries (e.g. the
    PreToolUse dispatch_gate) fire on Agent invocations.
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
  - Hookability: only `SendMessage` is verified-unhookable (#897 audit).
    Skill, ToolSearch, and the Task tools HAVE been observed reaching
    PreToolUse (incident evidence #942; HOOK_STDIN_DISCRIMINATORS.md) —
    assume ANY tool name can reach this hook. The degraded-mode
    allowlist (_READ_ONLY_TOOLS) is membership-based precisely so the
    "which tools are hookable" question is moot: an entry for a tool
    that never fires PreToolUse is a harmless dead entry, and
    unknown/future tool names fail safe (deny) automatically.
    Note: TaskList/TaskGet/TaskUpdate are PACT plugin task-system
    tools, distinct from the agent-dispatch `Agent` tool that IS blocked.

SACROSANCT (post-#662, amended #942): module-load failures and runtime
gate-logic exceptions are fail-CLOSED (deny) per #658 defect class —
EXCEPT for verified read-only tools (_READ_ONLY_TOOLS), which are
routed onward WITH an explicit degraded-mode warning at exit 0 so the
failure can be diagnosed (#942): permissionDecision "defer" (normal
permission flow) for local tools, "ask" (explicit user approval) for
outbound WebFetch/WebSearch — degraded mode never emits "allow", so it
is a permission-layer subset by construction. Malformed stdin in the
HEALTHY path remains fail-OPEN (input-side failure → harness's domain);
malformed stdin in the DEGRADED path is fail-CLOSED — an unparseable
frame means the tool name cannot be verified read-only.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {"suppressOutput": true} (allow / passthrough)
"""

from __future__ import annotations

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
    # Guarded rendering BEFORE the deny print: an unguarded hostile/raising
    # __str__ here would suppress the deny output and exit nonzero-non-2 —
    # for PreToolUse that is a non-blocking error and the tool call
    # PROCEEDS (fail-open). The deny must print for any exception; the
    # fallback is a raise-proof constant.
    try:
        error_render = _bounded_error_text(error)
    except BaseException:  # noqa: BLE001 — hostile __str__ must not suppress the deny
        error_render = "<error text unavailable>"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT bootstrap_gate {stage} failure — blocking for safety. "
                f"{error_render}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    # Guarded full-text rendering: a raise here would replace the deliberate
    # exit 2 (blocking) with a traceback exit 1 (non-blocking → fail-open).
    try:
        error_full = f"{error}"
    except BaseException:  # noqa: BLE001 — hostile __str__; keep the exit-2 path
        error_full = "<exception str() raised>"
    try:
        print(
            f"Hook load error (bootstrap_gate / {stage}): {error_full}",
            file=sys.stderr,
        )
    except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
        pass
    sys.exit(2)


# Verified read-only tools recognized in DEGRADED mode (gate cannot
# evaluate). Membership ⇒ warn-without-granting: permissionDecision
# "defer" (normal permission flow) for local tools, "ask" (explicit user
# approval) for outbound _DEGRADED_ASK_TOOLS — NEVER "allow". EVERYTHING
# else ⇒ deny (unknown/future tool names fail safe automatically — do NOT
# enumerate "the hookable set"; entries for tools that never fire
# PreToolUse are harmless dead entries).
# INVARIANT (pinned by test): this set must be disjoint from _BLOCKED_TOOLS
# and every member must be allowed on every healthy-path branch — degraded
# mode can never grant something the healthy gate would deny.
# Deliberate asymmetry (degraded STRICTER than healthy pre-marker mode):
# Bash and mcp__* are allowed on the healthy pre-marker path but DENIED
# here. A healthy gate allows Bash because the rest of the governance
# stack is verifiable and the marker verifier defends bypass; a degraded
# gate cannot distinguish diagnostic Bash from mutating Bash and has no
# operative verifier behind it. The mcp__ prefix carries zero information
# about mutation capability (e.g. computer-use), so no MCP name can be a
# VERIFIED read-only tool.
_READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep",          # pure file read/search
    "ToolSearch", "Skill",           # context loading only (incident-proven denied)
    "TaskList", "TaskGet",           # read-only task views (TaskCreate/TaskUpdate excluded)
    "WebFetch", "WebSearch",         # outbound read (healthy path already allows)
    "AskUserQuestion", "ExitPlanMode",  # user-interaction channel for diagnosis
})


def _read_stdin_tool_name() -> "str | None":
    """stdlib-only stdin parse for the DEGRADED import-stage path. None on
    ANY failure (unparseable JSON, non-dict frame, missing/non-string/empty
    tool_name) — caller treats None as deny (fail-CLOSED: an unverifiable
    frame means the tool name cannot be verified read-only; behavior-
    preserving, since a module-load failure denied before stdin was ever
    read pre-#942)."""
    try:
        data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
        name = data.get("tool_name")
        return name if isinstance(name, str) and name else None
    except Exception:
        return None


# Outbound-network members of _READ_ONLY_TOOLS: under a degraded gate these
# escalate to the user permission prompt ("ask") instead of deferring —
# network traffic under a broken governance gate warrants explicit user
# approval. INVARIANT (pinned by test): subset of _READ_ONLY_TOOLS.
_DEGRADED_ASK_TOOLS = frozenset({"WebFetch", "WebSearch"})

# Cap on exception text interpolated into context-bound output (warning
# strings reaching Claude's context and the user banner). Exception
# messages can embed attacker-influencable content (file contents, paths,
# crafted payloads in tracebacks) — bound + sanitize before interpolation.
# The stderr diagnostic line keeps the full text (debug channel).
_ERROR_TEXT_MAX = 200

# Cap on every stdin read in this hook (primary main() read + the
# degraded import-stage read). Generous: real PreToolUse frames embed
# tool_input payloads and stay well under this; anything larger is not a
# realistic hook frame and must not be slurped unbounded. An over-cap
# frame truncates mid-JSON → JSONDecodeError → the existing except at
# each read site (fail-open suppress on the primary; fail-closed deny on
# the degraded read).
# This cap bounds MEMORY only — it does NOT reject sub-cap input: a frame
# with a valid JSON prefix still parses (harmless — degraded never grants
# allow, primary fails-open).
# VALUE MUST EQUAL task_lifecycle_gate._STDIN_READ_MAX
# (independent module literal — twin-VALUE discipline, like _ERROR_TEXT_MAX).
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


def _bounded_error_text(error: BaseException) -> str:
    """Sanitized, length-bounded rendering of an exception for embedding in
    context-bound warning text: control/non-printable characters become
    spaces, and the result is truncated to _ERROR_TEXT_MAX chars with an
    explicit marker. Full text still goes to stderr at the call site.

    Total over hostile exceptions, structurally: the type name is captured
    first — a metaclass can make __name__ a property that raises (caught;
    falls back to a literal) or return any non-str value, INCLUDING a str
    subclass whose own __str__/__format__ raises. The exact-type check below
    (type(...) is str, which rejects str subclasses too) reduces type_name to
    an EXACT str, whose __format__/__str__ are str's own built-ins and cannot
    be overridden — so neither f-string branch below can raise on type_name
    regardless of the original __name__ value. The only exception-owned code
    left is the message render (error's own __str__), isolated to the main
    branch and guarded by the fallback. The function therefore returns a
    string for ANY exception object."""
    try:
        type_name = type(error).__name__
    except BaseException:  # noqa: BLE001 — hostile metaclass __name__ must not escape
        type_name = "exception"
    # __name__ can also RETURN (not raise) a non-str value — including a str
    # SUBCLASS whose own __str__/__format__ raises, which an isinstance check
    # would wave through. An EXACT-type check (type(...) is str) rejects
    # subclasses too, so type_name is provably an exact str whose formatting
    # uses str's own unpatchable built-ins → both f-string branches below
    # (incl. the fallback, which re-interpolates type_name) cannot raise on it.
    if type(type_name) is not str:
        type_name = "exception"
    try:
        text = f"{type_name}: {error}"
    except BaseException:  # noqa: BLE001 — hostile __str__ must not escape the renderer
        text = f"{type_name}: <exception str() raised>"
    truncated = len(text) > _ERROR_TEXT_MAX
    if truncated:
        # MemoryError-safe by STRUCTURE: bounding first keeps the sanitize
        # join O(cap) not O(n) — a multi-GB input never materializes a
        # sanitized copy; asserted structurally, not via a runtime test.
        text = text[:_ERROR_TEXT_MAX]        # bound BEFORE the O(n) sanitize join
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    if truncated:
        text = text + "...[truncated]"
    return text


def _emit_degraded_warning(stage: str, error: BaseException, tool_name: str) -> NoReturn:
    """Warn-WITHOUT-granting for a verified read-only tool while the gate is
    degraded. Local read-only tools emit permissionDecision="defer" — a
    documented PreToolUse decision value (official hooks docs enum
    allow/deny/ask/defer, re-verified 2026-06-12) that routes the call
    through the NORMAL permission flow; outbound tools
    (_DEGRADED_ASK_TOOLS) emit "ask" so the user explicitly approves
    network traffic under a broken gate. Degraded mode therefore never
    emits "allow" at all — it is a permission-layer SUBSET by
    construction and cannot grant anything the permission system
    wouldn't. MUST exit 0 — stdout JSON is only honored on exit 0
    ('JSON output is only processed on exit 0'; on exit 2 stdout is
    ignored and stderr is fed to Claude). permissionDecisionReason and
    additionalContext carry the same warning; systemMessage is the
    user-visible banner."""
    decision = "ask" if tool_name in _DEGRADED_ASK_TOOLS else "defer"
    routed = (
        "escalated to your explicit approval"
        if decision == "ask"
        else "deferred to the normal permission flow"
    )
    warning = (
        f"PACT bootstrap_gate is DEGRADED ({stage} failure — "
        f"{_bounded_error_text(error)}). Read-only tool '{tool_name}' is "
        f"{routed} so the failure can be diagnosed; nothing is "
        "auto-allowed while the gate is degraded. Mutating tools (Edit, "
        "Write, Agent, NotebookEdit), Bash, and MCP tools remain blocked "
        "fail-closed. Surface this to the user: PACT hooks are failing — "
        "check Python version compatibility and the plugin install under "
        "~/.claude/plugins/cache/. Bootstrap cannot complete until the "
        "hook loads cleanly."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": warning,
            "additionalContext": warning,
        },
        "systemMessage": (
            f"PACT bootstrap_gate degraded ({stage} failure): read-only "
            "tools routed to the normal permission flow with a warning; "
            "mutating tools blocked."
        ),
    }))
    # Guarded full-text rendering: this line runs AFTER the decision JSON
    # printed, but a raise here would exit nonzero — and stdout JSON is only
    # honored on exit 0, voiding the defer/ask decision retroactively.
    try:
        error_full = f"{error}"
    except BaseException:  # noqa: BLE001 — hostile __str__; keep the exit-0 path
        error_full = "<exception str() raised>"
    try:
        print(
            f"Hook degraded-{decision} (bootstrap_gate / {stage}): {tool_name} — {error_full}",
            file=sys.stderr,
        )
    except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
        pass
    sys.exit(0)


def _degraded_decision(stage: str, error: BaseException, tool_name: "str | None") -> NoReturn:
    """Single decision point for BOTH degraded stages (import + runtime).
    Membership ⇒ warn-without-granting (defer / ask, never allow);
    everything else (incl. tool_name=None from unparseable stdin —
    fail-CLOSED) ⇒ the unchanged deny emitter."""
    if tool_name is not None and tool_name in _READ_ONLY_TOOLS:
        _emit_degraded_warning(stage, error, tool_name)
    _emit_load_failure_deny(stage, error)


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
    # Degraded mode (#942): verified read-only tools defer/ask with a
    # warning so the failure can be diagnosed; everything else takes the
    # unchanged fail-closed deny. Stdin is parsed here by stdlib-only code
    # (the healthy path's json.load in main() is never reached on this
    # branch).
    _degraded_decision("module imports", _module_load_error, _read_stdin_tool_name())


_SUPPRESS_OUTPUT = json.dumps({
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "PreToolUse"},
})

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

# Canonical secretary spawn identity. Both strings are load-bearing for the
# carve-out below; any drift silently re-introduces the bootstrap-deadlock
# these constants are here to prevent.
#
# _SECRETARY_NAME mirrors bootstrap_marker_writer._SECRETARY_NAME (the
# producer-side constant at marker_writer.py:103) and the literal at
# commands/bootstrap.md Step 2. Cross-file atomic edits required across
# this file, bootstrap_marker_writer.py, AND commands/bootstrap.md.
#
# _SECRETARY_AGENT_TYPE is the canonical agentType from
# commands/bootstrap.md Step 2 — no producer-side mirror in
# bootstrap_marker_writer (which keys on member name, not agentType).
# Cross-file atomic edits required across this file AND
# commands/bootstrap.md only.
_SECRETARY_NAME = "secretary"
_SECRETARY_AGENT_TYPE = "pact-secretary"

# Marker schema constants (MARKER_SCHEMA_VERSION, MARKER_MAX_BYTES) and
# the signature function (expected_marker_signature) live in
# shared/marker_schema.py — the SSOT shared by producer
# (bootstrap_marker_writer.py) and this verifier. Imported above.

_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"PACT:bootstrap\") first. "
    "Code-editing tools (Edit, Write) and agent dispatch (Agent) are blocked "
    "until bootstrap completes. Bash, Read, Glob, Grep are available."
)


def _is_canonical_secretary_spawn(input_data: dict) -> bool:
    """Audit anchor: canonical secretary spawn carve-out for #789.

    True iff this Agent call is the canonical bootstrap-secretary spawn
    that commands/bootstrap.md Step 2 prescribes. Bindings 1/2/3/5 must all
    match for the carve-out to fire (binding 4 was dropped for #979 — see
    below):

      1. tool_name == "Agent"
      2. tool_input.subagent_type == "pact-secretary" (_SECRETARY_AGENT_TYPE)
      3. tool_input.name == "secretary" (_SECRETARY_NAME, canonical literal)
      4. (DROPPED, #979) formerly tool_input.team_name == get_team_name().
         Claude Code v2.1.178+ ignores the Agent(team_name=) arg, so an
         equality check against it would wrongly DENY the canonical secretary
         spawn once the SSOT moved to the platform's "session-<id8>" name
         (the orchestrator may still pass a stale arg the platform discards).
         The carve-out stays tight via bindings 2/3 (exact subagent_type +
         name literals) and binding 5 (one-shot, gated on the REAL team dir).
      5. NOT _team_has_secretary(get_team_name()) — one-shot semantic; flips
         to False the moment the spawned secretary lands in members[]. Reads
         the REAL session team dir (expected_team), which the empty-team
         fail-closed below guarantees is a non-empty path segment.

    Binding (1) is a hardcoded literal. Bindings (2) and (3) compare against
    module constants, not tool_input-derived values. Binding (5) is a disk
    read of the team config members[]; True after first successful dispatch,
    so the carve-out fires at most once per session. With binding 4 dropped,
    the carve-out reads no tool_input-derived team value — the
    secretary-presence check resolves against the SSOT team dir only.

    On ANY disk-read exception, returns False — caller falls through to
    the existing _BLOCKED_TOOLS deny path so the user sees the canonical
    _DENY_REASON ("PACT bootstrap required...") rather than the
    load-failure variant. Mirrors is_marker_set's silent-on-exception
    style.

    SACROSANCT — local-import discipline: _team_has_secretary is imported
    LOCALLY (function-call time, not module-load time) to break the
    reciprocal cycle with bootstrap_marker_writer, which imports
    is_marker_set from this module at its own top-level. Reciprocal
    top-level import here would deadlock module load and route every
    tool call through the fail-closed deny path.
    """
    try:
        if input_data.get("tool_name") != "Agent":
            return False
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return False
        if tool_input.get("subagent_type") != _SECRETARY_AGENT_TYPE:
            return False
        if tool_input.get("name") != _SECRETARY_NAME:
            return False
        # Binding 4 dropped (#979): no comparison against the platform-ignored
        # tool_input.team_name. The empty-team fail-closed is RETAINED because
        # binding 5 below reads teams/{expected_team}/config.json.
        expected_team = pact_context.get_team_name()
        if not expected_team:
            return False
        # Local-import: reciprocal-cycle prevention. bootstrap_marker_writer
        # imports is_marker_set from this module at its OWN top-level; a
        # reciprocal top-level import here would deadlock module load and
        # silently route every tool call through the fail-closed deny path.
        # See SACROSANCT block in this docstring.
        from bootstrap_marker_writer import _team_has_secretary
        return not _team_has_secretary(expected_team)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        return False


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

    # Lead-role gate (#878, DENY-gate enforcement RESTORATION): only the
    # team-lead's pre-bootstrap tool calls are gated. Migrated from the
    # negative `resolve_agent_name(...) != ""` heuristic — which returned
    # non-empty for BOTH lead spellings (Step-4 prefix-strip), so at v4.4.0 the
    # lead itself took this teammate-bypass branch and the DENY gate was
    # silently DEAD for the lead. is_lead keys on the harness-set agent_type
    # directly, re-enabling lead-side enforcement. is_lead is total (never
    # raises), so the caller's exception-fail-CLOSED path is preserved.
    if not pact_context.is_lead(input_data):
        return None

    # Lead session, no marker — check tool classification
    tool_name = input_data.get("tool_name", "")

    # Canonical secretary spawn carve-out (#789). The bootstrap ritual's
    # Agent(secretary) dispatch is the ONLY action that populates the
    # precondition (`secretary` in team members[]) that the marker writer
    # requires; without this carve-out the gate denies the only dispatch
    # that could clear its own deny condition — bootstrap deadlock. The
    # predicate is one-shot by construction (binding 5 flips False the
    # moment the spawn lands in members[]).
    if _is_canonical_secretary_spawn(input_data):
        return None

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
        input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-OPEN (input-side failure is harness's domain).
        # Cannot evaluate without input; cannot DENY meaningfully.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception as e:
        # Runtime fail-CLOSED — gate-logic exception must DENY (#658
        # sibling defect class; pre-#662 this path was fail-OPEN) — with
        # the #942 degraded-mode carve-out: a runtime gate-logic bug
        # bricks diagnosis identically to an import failure, so it routes
        # through the same _degraded_decision. stdin was already consumed
        # by the json.load above — pass the already-parsed tool_name,
        # never re-read stdin. input_data may be a non-dict JSON value
        # (itself a plausible cause of the exception), so guard the .get;
        # a missing/non-string tool_name denies, same as the import stage.
        _tool = input_data.get("tool_name") if isinstance(input_data, dict) else None
        _degraded_decision(
            "runtime", e, _tool if isinstance(_tool, str) and _tool else None
        )

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
