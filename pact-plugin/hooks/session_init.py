#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_init.py
Summary: SessionStart hook that initializes PACT environment.
Used by: Claude Code settings.json SessionStart hook

Performs PACT environment initialization:
0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
0b. Emits a one-time in-process teammateMode notice recommending tmux for unattended runs (startup/resume only)
1. Creates plugin symlinks for @reference resolution
3. Ensures project CLAUDE.md exists with memory sections
3b. One-time migration: wraps existing project CLAUDE.md in PACT_MANAGED boundary (#404)
3d. Strips obsolete PACT_START/PACT_END kernel block from ~/.claude/CLAUDE.md (sunsets before v5.0.0)
4. Checks for stale pinned context entries in project CLAUDE.md (delegated to staleness.py)
5. Generates session-unique PACT team name and reminds orchestrator to create it
5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
6. Checks for in_progress Tasks (resumption context via Task integration)
7. Restores last session snapshot for cross-session continuity
8. Checks for paused work from previous session's /PACT:pause

Note: Plan detection (scanning docs/plans/) was removed from session startup
to reduce latency. Plan detection is deferred to /PACT:orchestrate, which
checks docs/plans/ when it actually needs plan context.

Note: Memory-related initialization (dependency installation, embedding
migration, pending embedding catch-up) is now lazy-loaded on first memory
operation via pact-memory/scripts/memory_init.py. This reduces startup
cost for non-memory users.

Input: JSON from stdin with session context
Output: JSON with `hookSpecificOutput.additionalContext` for status
"""

import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Optional

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# Import shared Task utilities (DRY - used by multiple hooks)
from shared.task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
    build_post_compaction_checkpoint,
)

# Import staleness detection (extracted to staleness.py for maintainability).
# Underscore aliases (_get_project_claude_md_path, _estimate_tokens) and the
# uppercase constants are re-exported here so test_staleness.py can keep
# importing them via `from session_init import ...`. Removing these would
# break the staleness test suite, even though pyright flags them as unused
# inside session_init itself — they form the module's public interface.
from staleness import (  # noqa: F401
    check_pinned_staleness as _staleness_check,
    check_pinned_block_signal as _staleness_block_check,
    PINNED_STALENESS_DAYS,
    PINNED_CONTEXT_TOKEN_BUDGET,
    _get_project_claude_md_path,
    _estimate_tokens,
    _parse_pinned_section,
)
from pin_caps import (  # noqa: F401
    PIN_COUNT_CAP,
    format_slot_status,
    parse_pins,
)

from shared import BOOTSTRAP_MARKER_NAME, SESSION_ID_CONTROL_CHARS_RE, build_session_path
from shared.constants import COMPACT_SUMMARY_PATH
from shared.pact_context import (
    build_context_cache,
    classify_session_role,
    get_session_dir,
    is_lead,
    persist_context,
)
from shared.dispatch_helpers import is_registered_pact_specialist
from shared.session_journal import append_event, make_event
from shared.failure_log import append_failure
from shared.plugin_manifest import format_plugin_banner
from shared.peer_context import get_peer_context, resolve_lead_team_by_pane

# Import extracted modules (decomposed for maintainability per M5 audit finding).
from shared.symlinks import setup_plugin_symlinks
from shared.claude_md_manager import (
    ensure_project_memory_md,
    file_lock,
    migrate_to_managed_structure,
    resolve_project_claude_md_path,
    strip_orphan_kernel_block,
)
from shared.merge_guard_common import (
    TOKEN_DIR,
    cleanup_orphan_tokens as _cleanup_orphan_tokens,
)
from shared.session_resume import (
    update_session_info,
    restore_last_session,
    check_resumption_context,
    check_paused_state,
)


# #864 Phase 1: one-time startup notice recommending tmux for unattended runs
# when the effective teammateMode is not positively "tmux". Emitted via
# system_messages (user-facing) by main() step 0b. Lives HERE (presentation
# layer) rather than in shared/teammate_mode.py (resolution layer) per SRP.
# Pure literal (no interpolation) so tests can pin the exact substring.
_INPROCESS_MODE_NOTICE = (
    "PACT: unattended runs may stall in in-process teammate mode "
    "(the lead can sit idle awaiting a wake that needs a manual nudge). "
    "For hands-off runs, relaunch with `--teammate-mode tmux` for reliable "
    "native delivery, or keep a heartbeat — see reference/unattended-runs.md."
)

# Unknown-role startup warning (#878). The lead-only writes below are gated
# behind is_lead, which keys on the harness-set agent_type field. A session
# launched WITHOUT `--agent` (or with a non-PACT agent_type) carries no
# recognizable role — classify_session_role() returns "unknown" — so its
# session_init silently performs none of the lead-only writes. That is the
# intended fail-toward-teammate direction, but it is invisible to an operator
# who MEANT to launch the orchestrator and forgot the flag. This notice makes
# that case observable. Emitted via system_messages (user-facing) only for the
# "unknown" role; lead and teammate frames never see it. Pure literal so tests
# can pin the exact substring.
_UNKNOWN_ROLE_NOTICE = (
    "PACT: this session has no recognized agent role (no `--agent` flag, or an "
    "unrecognized agent_type), so lead-only session setup was skipped. If you "
    "meant to drive PACT as the orchestrator, relaunch with "
    "`--agent PACT:pact-orchestrator`."
)


def _should_warn_unknown_role(input_data: dict) -> bool:
    """Decide whether the #878 unknown-role startup notice should fire.

    Fires when the frame has NO recognized PACT role:
      classify_session_role == "unknown"  (agent_type absent)
        OR
      agent_type is present AND NOT is_lead AND NOT a recognized specialist.

    The "present-but-unrecognized" arm catches a mis-launched / typo'd
    agent_type (e.g. ``--agent pact-architct``) that the absent-only check
    misses. Recognized = the live ``agents/pact-*.md`` registry (SSOT), tested
    via ``is_registered_pact_specialist``.

    ORDERING IS LOAD-BEARING — do NOT reorder (security-engineer ruling):
    ``is_lead`` is checked BEFORE the registry. ``pact-orchestrator.md`` IS in
    the glob set, so the registry would recognize the unqualified lead spelling
    as a "specialist" — but is_lead short-circuits first, so a genuine lead is
    never mis-bucketed and a registered-lead-spelling edge can't suppress the
    notice for a frame that should get it.

    plugin_root is read from the ENV (``CLAUDE_PLUGIN_ROOT``), NOT the cache:
    this notice fires BEFORE build_context_cache populates the pact_context
    cache, so a cache-backed registry lookup would see an empty plugin_root →
    empty registry → every teammate would false-fire the notice. The env is the
    authoritative pre-cache source (it is the same value the cache later copies).

    SPELLING-SYMMETRY: strip a leading ``PACT:`` before the membership test, so
    a qualified specialist spelling (``PACT:pact-backend-coder``) is recognized
    just as is_lead accepts both qualified and unqualified lead spellings.

    FAIL-OPEN residual: when even the env plugin_root is empty/unresolvable, the
    registry is empty and a present-but-non-lead frame fires the notice. That is
    correct — an install with no resolvable plugin_root is broken, and a
    spurious advisory notice is harmless (the notice never DENIES).
    """
    if classify_session_role(input_data) == "unknown":
        return True
    if is_lead(input_data):
        return False
    agent_type = input_data.get("agent_type")
    if not isinstance(agent_type, str):
        # Present-but-non-string (unhashable/odd) agent_type: not lead, not a
        # resolvable specialist spelling → treat as unrecognized → fire.
        return True
    stripped = agent_type.removeprefix("PACT:")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    return not is_registered_pact_specialist(stripped, plugin_root=plugin_root)


def check_pinned_staleness():
    """
    Thin wrapper around staleness.check_pinned_staleness().

    Resolves the CLAUDE.md path via the module-level _get_project_claude_md_path
    (which tests can patch on session_init) and passes it to the core function.
    """
    path = _get_project_claude_md_path()
    return _staleness_check(claude_md_path=path)


def check_pin_slot_status() -> Optional[str]:
    """Return a Tier-0 slot-status line for additionalContext, or None.

    Builds "Pin slots: N/12 used, K chars remaining on largest pin" via
    pin_caps.format_slot_status. Fail-open: any resolution/read/parse
    error returns None so the SessionStart flow degrades to existing
    behavior rather than DoS.

    Defense-in-depth (Back-M2): the inner branches each handle their own
    failure modes, but the SessionStart hot path cannot afford an
    uncaught exception from a downstream helper (e.g., format_slot_status
    regression, future parser change that raises outside parse_pins).
    Wrap the full body in a blanket try/except — mirrors the sibling
    check_pin_stale_block_directive pattern above.
    """
    try:
        path = _get_project_claude_md_path()
        if path is None:
            return None

        try:
            content = path.read_text(encoding="utf-8")
        except (IOError, OSError, UnicodeDecodeError):
            return None

        parsed = _parse_pinned_section(content)
        if parsed is None:
            # Empty or missing Pinned Context section — surface 0-used state
            # so the orchestrator sees pin headroom from session start.
            return format_slot_status([])

        _, _, pinned_content = parsed
        try:
            pins = parse_pins(pinned_content)
        except Exception:  # noqa: BLE001 — fail-open by construction
            return None

        return format_slot_status(pins)
    except Exception:  # noqa: BLE001 — outer fail-open
        return None


def check_pin_stale_block_directive() -> Optional[str]:
    """Return an unconditional stale-block directive for additionalContext, or None.

    Fires only when check_pinned_block_signal reports positive detection.
    Uses hard-rule instructional voice (MUST) per PACT protocol — the
    directive is architecturally binding via Tier-0 additionalContext
    (survives compaction per plan row 5 / compaction durability model).

    Side effect (Phase F): writes a session-scoped pin-staleness-pending
    marker so pin_staleness_gate.py (PreToolUse) can block later Edit/Write
    on CLAUDE.md Pinned Context. Clears the marker when detection is
    negative so resolved state does not leave the gate armed.
    """
    # Defense-in-depth (Back-M1): _staleness_block_check is fail-open by
    # its own contract, but session_init is on the SessionStart hot path —
    # a regression inside the callee should not propagate out of this
    # surfacing helper. Wrap in fail-open try/except.
    try:
        path = _get_project_claude_md_path()
        signal = _staleness_block_check(claude_md_path=path)
    except Exception:  # noqa: BLE001 — fail-open
        return None

    try:
        # Arch M3: do NOT hoist these imports to module top. pin_staleness_gate
        # itself imports `from pin_caps import parse_pins` at its module top,
        # and session_init already eagerly imports pin_caps. Hoisting here
        # would force pin_staleness_gate to load on every SessionStart even
        # when no stale-block signal fires — wasted work on the hot path.
        # Keeping the import lazy scopes the cost to the post-signal branch.
        from shared.pact_context import get_session_dir
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        session_dir = get_session_dir()
        if session_dir:
            marker = Path(session_dir) / PIN_STALENESS_MARKER_NAME
            if signal is not None:
                marker.parent.mkdir(parents=True, exist_ok=True)
                # Sec-M1: create the marker via os.open with O_NOFOLLOW so
                # a planted symlink at the marker path cannot redirect the
                # creation onto a sensitive file. O_NOFOLLOW is POSIX; fall
                # back to Path.touch on platforms that lack it.
                nofollow = getattr(os, "O_NOFOLLOW", 0)
                flags = os.O_CREAT | os.O_WRONLY | nofollow
                try:
                    fd = os.open(str(marker), flags, 0o600)
                    os.close(fd)
                except OSError:
                    # ELOOP (symlink encountered) or other failure — skip
                    # the marker write rather than fall back unsafely.
                    pass
            elif marker.exists():
                try:
                    marker.unlink()
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 — marker management is best-effort
        pass

    if signal is None:
        return None
    return (
        f"Pinned context: {signal.detail}. "
        f"You MUST run /PACT:pin-memory to archive stale pins before adding new ones."
    )


def check_additional_directories() -> str | None:
    """
    Check if required PACT directories are in additionalDirectories in settings.json.

    Checks for both ~/.claude/teams and ~/.claude/pact-sessions.
    Returns a tip message listing whichever directories are missing,
    or None if all are already present.
    Fail-open: returns None on any error (file missing, malformed JSON, etc.).
    """
    try:
        settings_path = Path.home() / ".claude" / "settings.json"
        if not settings_path.exists():
            return None  # No settings file — nothing to check

        settings = json.loads(settings_path.read_text(encoding="utf-8"))

        additional_dirs = settings.get("permissions", {}).get(
            "additionalDirectories", []
        )
        if not isinstance(additional_dirs, list):
            return None  # Unexpected type — fail-open

        # Resolve all configured paths for comparison
        configured: set[Path] = set()
        for entry in additional_dirs:
            if not isinstance(entry, str):
                continue
            # Expand ~ using Path.home() (not expanduser which bypasses monkeypatch)
            if entry.startswith("~/"):
                expanded = (Path.home() / entry[2:]).resolve()
            else:
                expanded = Path(entry).resolve()
            configured.add(expanded)

        # Check which required directories are missing
        required = {
            "~/.claude/teams": (Path.home() / ".claude" / "teams").resolve(),
            "~/.claude/pact-sessions": (
                Path.home() / ".claude" / "pact-sessions"
            ).resolve(),
        }
        missing = [
            tilde for tilde, resolved in required.items()
            if resolved not in configured
        ]

        if not missing:
            return None  # All required directories configured

        dirs_list = ", ".join(f"`{d}`" for d in missing)
        return (
            f"PACT tip: Add {dirs_list} to `additionalDirectories` in your "
            "~/.claude/settings.json to avoid permission prompts for team and "
            "session file operations."
        )
    except Exception:
        return None  # Fail-open: never block session start


def generate_team_name(input_data: dict[str, Any]) -> str:
    """
    Generate a session-unique PACT team name.

    Uses the first 8 characters of the session_id from the SessionStart hook
    stdin JSON to create a unique team name like "pact-0001639f". Falls back
    to a random 8-character hex suffix if session_id is not in stdin.

    Args:
        input_data: Parsed JSON from stdin (SessionStart hook input)

    Returns:
        Team name string like "pact-0001639f"
    """
    # INVARIANT: all team directory names MUST be produced by this
    # function. Output is lowercase ASCII hex ([a-f0-9-]) prefixed with
    # "pact-" — the session_end reaper's exact-match skip predicate
    # (cleanup_old_teams) and the union skip-set for cleanup_old_tasks
    # rely on this shape. A writer that creates ~/.claude/teams/ dirs
    # with characters outside this charset (uppercase, unicode,
    # separators) could bypass the skip predicate and be reaped on the
    # NEXT session_end.
    raw_id = input_data.get("session_id")
    session_id = str(raw_id) if raw_id else ""
    if session_id:
        suffix = re.sub(r"[^a-f0-9-]", "", session_id[:8]) or secrets.token_hex(4)
    else:
        suffix = secrets.token_hex(4)
    return f"pact-{suffix}"


def _validate_under_pact_sessions(path: str) -> str | None:
    """Reject extracted session paths that escape the pact-sessions root.

    Defense-in-depth against tampered CLAUDE.md content. The Session dir / Resume
    lines are user-editable text, so a malicious or accidentally corrupted file
    could point _extract_prev_session_dir at any filesystem location (e.g.
    /etc, /var, a sibling project's secrets). Callers consume the returned path
    to read journal events; an attacker who controlled the path could exfiltrate
    or trigger reads outside the PACT sessions tree.

    The check calls ``Path.resolve(strict=False)`` on both the candidate AND the
    sessions root so ``..`` segments are collapsed and symlinks followed before
    the containment check. A naive string-prefix comparison against
    ``str(Path(path))`` is NOT sufficient: ``Path()`` normalizes redundant
    slashes but leaves ``..`` segments intact, so ``~/.claude/pact-sessions/../../etc/passwd``
    would textually start with the prefix yet resolve outside the tree once the
    filesystem is asked to dereference it. ``resolve(strict=False)`` does the
    canonicalization explicitly and does NOT require the path to exist.

    The containment check uses ``Path`` comparison semantics
    (``candidate == sessions_root or sessions_root in candidate.parents``)
    instead of string prefix + ``os.sep``. This eliminates the sibling-prefix
    collision class (``pact-sessions-evil`` vs ``pact-sessions``) by design,
    rather than relying on an explicit separator guard.

    Returns the original string on success and None on rejection (silent
    fail-closed — callers already treat None as "no previous session").
    """
    try:
        sessions_root = (Path.home() / ".claude" / "pact-sessions").resolve()
        candidate = Path(path).resolve(strict=False)
        if candidate == sessions_root or sessions_root in candidate.parents:
            return path
    except (TypeError, ValueError, OSError):
        pass
    return None


def _extract_prev_session_dir(project_dir: str) -> str | None:
    """
    Extract the previous session's directory path from the project CLAUDE.md.

    Reads the "## Current Session" block written by update_session_info()
    and extracts the session dir from lines like
    "- Session dir: `~/.claude/pact-sessions/PACT-Plugin/abc12345-...`".

    Honors both supported project CLAUDE.md locations
    ($project_dir/.claude/CLAUDE.md preferred, $project_dir/CLAUDE.md legacy).

    Falls back to deriving the path from the Resume line's session_id +
    project root basename if the Session dir line is absent (backward compat
    with sessions that wrote team name but not session dir).

    Both extracted paths (primary and fallback) are validated against the
    canonical pact-sessions prefix via _validate_under_pact_sessions before
    being returned. Defense-in-depth against tampered CLAUDE.md content.

    This is used to locate the previous session's journal for resume context
    and pause state detection. Returns None if neither CLAUDE.md exists, the
    session dir can't be extracted, or the extracted path is outside the
    pact-sessions tree.

    Args:
        project_dir: CLAUDE_PROJECT_DIR path

    Returns:
        Previous session directory path string, or None if not found
    """
    if not project_dir:
        return None

    try:
        claude_md, source = resolve_project_claude_md_path(project_dir)
        # source == "new_default" means neither location exists -- nothing to read
        if source == "new_default":
            return None

        # Acquire the same sidecar file_lock that update_session_info
        # uses for its read-mutate-write pass. A concurrent write (e.g.,
        # from another session_init invocation racing the WRITE step at
        # L1148) could otherwise produce a torn read here, surfacing as
        # either a corrupted Session-dir match or a fallback-regex hit
        # on a half-written SESSION_START block. The lock serializes
        # against the writer. Re-entrancy is safe: this read at step 5a
        # runs BEFORE update_session_info (step 5b) acquires its own
        # lock. No nesting; fail-open on TimeoutError per file_lock
        # contract.
        try:
            with file_lock(claude_md):
                content = claude_md.read_text(encoding="utf-8")
        except TimeoutError:
            return None

        # Primary: match "- Session dir: `<path>`" in the Current Session block.
        match = re.search(r'- Session dir:\s*`([^`]+)`', content)
        if match:
            raw = match.group(1)
            # Expand ~ to actual home directory
            if raw.startswith("~/"):
                expanded = str(Path.home() / raw[2:])
            else:
                expanded = raw
            return _validate_under_pact_sessions(expanded)

        # The primary regex missed even though CLAUDE.md is on disk. This is
        # usually benign (older sessions wrote only the Resume line, not the
        # Session dir line — handled by the fallback just below), but it is
        # also how a silent format regression would present. Log a one-line
        # stderr warning so future drift in the SESSION_START block surfaces
        # during testing instead of silently degrading to the fallback.
        print(
            "session_init: _extract_prev_session_dir regex failed on existing "
            "CLAUDE.md, falling back to Resume-line; file may have unexpected "
            "format",
            file=sys.stderr,
        )

        # Fallback: derive from Resume line session_id + project root basename.
        # Resume line format: "- Resume: `claude --resume <session_id>`"
        resume_match = re.search(
            r'- Resume:\s*`claude --resume\s+([0-9a-f-]+)`', content
        )
        if resume_match:
            session_id = resume_match.group(1)
            # Use project root basename (not worktree) for slug
            slug = Path(project_dir).name
            derived = str(
                Path.home() / ".claude" / "pact-sessions" / slug / session_id
            )
            return _validate_under_pact_sessions(derived)

    except (IOError, OSError):
        pass
    return None


# Render-hostile characters that, present anywhere in a session_id, render
# the id unsafe for use in single-line textual contexts like the CLAUDE.md
# Resume line. Covers C0 controls (0x00-0x1f, includes \n 0x0a, \r 0x0d),
# DEL (0x7f), NEL (U+0085), LINE SEPARATOR (U+2028), and PARAGRAPH
# SEPARATOR (U+2029) — every character `str.splitlines()` or an LLM
# tokenizer may treat as a line break. A crafted id containing any of
# these (e.g. "\n- Team: malicious") would break out of the Resume line
# and forge a teammate-routing line under the session-managed block,
# causing the next session_init to read a corrupted Resume payload.
# Symmetric with `shared.session_state._RENDER_STRIP_RE` — asymmetric
# strip sets across interpolation sinks become the attacker's entry point.
_SESSION_ID_CONTROL_CHARS_RE = SESSION_ID_CONTROL_CHARS_RE


def _is_unknown_or_missing_session(raw_id: object) -> bool:
    """Return True if the session_id is missing, blank, a sentinel, or contains control chars.

    Single canonical predicate for the malformed-stdin gate. Both the
    persistence call sites at the top of main() (build_context_cache +
    persist_context + append_event) and the CLAUDE.md write at step 5b consult
    this helper so the two gates can never drift. Drift previously allowed
    three corruption classes:

    * Whitespace-only ids (e.g. `"   "`) were truthy and bypassed
      `not raw_id`, leaking through to the context-persist path
      (build_context_cache resolves it, persist_context mkdir's it) as a
      literal directory name.
    * An attacker-supplied `"unknown-foo"` value passed `not raw_id` because
      the string is non-empty, then later passed `startswith("unknown")`
      and was written into CLAUDE.md anyway via a different code path.
    * A session_id containing C0 control characters (newline, CR, NUL,
      etc.) passed all existing non-empty/non-sentinel checks but, when
      interpolated into ``f"- Resume: `claude --resume {session_id}`"``
      by update_session_info, could inject a fake CLAUDE.md line via
      embedded newlines. The unified helper strips C0 controls to close
      this injection path at the session_id entry point.

    The unified helper rejects all of: None, non-strings, empty strings,
    whitespace-only strings, any string already shaped like the
    `unknown-*` sentinel, and any string containing C0 control characters
    or DEL.
    """
    if not raw_id:
        return True
    if not isinstance(raw_id, str):
        return True
    stripped = raw_id.strip()
    if not stripped:
        return True
    if _SESSION_ID_CONTROL_CHARS_RE.search(raw_id):
        return True
    return stripped.startswith("unknown-")


def _build_safety_net_context(team_name: str | None) -> str:
    """
    Build a minimal governance-delivery additionalContext string for the
    exception safety net in main().

    The returned string MUST start with "YOUR PACT ROLE: orchestrator." at byte 0
    (line-anchored), and must include the `Skill("PACT:bootstrap")` invocation
    so the team-lead
    still loads its operating instructions, governance policy, and workflow
    protocols even when main() failed before building the normal
    team-reuse/team-create string.

    This helper is deliberately zero-risk: only string literals and a single
    f-string interpolation of team_name (which is either None or a validated
    team name from generate_team_name). No file I/O, no subprocess, no
    imports that might fail.

    Args:
        team_name: Team name captured before the exception, or None if the
                   exception fired before generate_team_name() ran.

    Returns:
        Minimal additionalContext string suitable for the except-block
        safety net. Leads with "YOUR PACT ROLE: orchestrator." at byte 0.
    """
    prelude = (
        'YOUR PACT ROLE: orchestrator.\n\n'
        'Invoke Skill("PACT:bootstrap") immediately, without waiting for user input. '
        'Do this before anything else. '
        'Do not evaluate whether it is needed. '
        'You must invoke Skill("PACT:bootstrap") on every session start.'
    )
    if team_name:
        return (
            f'{prelude}\n\n'
            f'Session team: `{team_name}` (session_init partially failed — '
            f'check systemMessage for details). '
            f'Run TaskList to check current state.'
        )
    return (
        f'{prelude}\n\n'
        'Session team: NOT GENERATED (session_init failed early — check '
        'systemMessage for details). Call TeamCreate after bootstrap loads.'
    )


def _clear_bootstrap_marker(session_path: Path) -> None:
    """Unlink the bootstrap-complete marker at ``session_path``.

    Scope is intentionally narrow: ONLY the marker file is removed. The
    team config (``~/.claude/teams/{team_name}/config.json``) is NOT
    touched here and persists across ``/clear``. Consequence: the
    ``bootstrap_marker_writer`` UserPromptSubmit hook re-creates the
    marker on the next prompt without orchestrator intervention, because
    the writer's pre-conditions (team config + secretary in members[])
    are still observable on disk.

    Fail-open: any ``OSError`` is swallowed so session init does not
    block on cleanup.
    """
    try:
        (session_path / BOOTSTRAP_MARKER_NAME).unlink(missing_ok=True)
    except OSError:
        pass  # Fail-open: don't block session init for marker cleanup


def main():
    """
    Main entry point for the SessionStart hook.

    Performs PACT environment initialization:
    0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
    0b. Emits a one-time in-process teammateMode notice recommending tmux for unattended runs (startup/resume only)
    1. Creates plugin symlinks for @reference resolution
    3. Ensures project CLAUDE.md exists with memory sections
    3b. One-time migration: wraps existing project CLAUDE.md in PACT_MANAGED boundary (#404)
    3d. Strips obsolete PACT_START/PACT_END kernel block from ~/.claude/CLAUDE.md (sunsets before v5.0.0)
    4. Checks for stale pinned context entries in project CLAUDE.md (delegated to staleness.py)
    5. Generates session-unique PACT team name and reminds orchestrator to create it
    5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
    6. Checks for in_progress Tasks (resumption context via Task integration)
    7. Restores last session snapshot for cross-session continuity
    8. Checks for paused work from previous session's /PACT:pause

    Note: Plan detection (scanning docs/plans/) was removed from session startup
    to reduce latency. Plan detection is deferred to /PACT:orchestrate, which
    checks docs/plans/ when it actually needs plan context.

    Note: Memory-related initialization (dependency installation, embedding
    migration, pending embedding catch-up) is now lazy-loaded on first memory
    operation via pact-memory/scripts/memory_init.py. This reduces startup
    cost for non-memory users.
    """
    # Pre-declare team_name so the outer except block can reference whatever
    # was captured before the exception fired. The assignment inside the try
    # at step 5 (team_name = generate_team_name(...)) rebinds this local; if
    # the exception fires before step 5, team_name stays None and the safety
    # net falls through to the "NOT GENERATED" branch.
    team_name = None
    # Track whether stdin JSON parsing failed, so the R3 malformed-stdin
    # gate below can distinguish "stdin was malformed JSON" from "stdin
    # parsed but session_id was missing/blank". Both paths fall through
    # to the same `unknown-{hex}` sentinel, but the failure_log ring
    # buffer captures them under different classifications so post-hoc
    # debugging can tell them apart.
    stdin_json_error: str | None = None
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            input_data = {}
            stdin_json_error = str(exc)

        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        context_parts = []
        system_messages = []

        # Detect session source: startup, resume, compact, clear
        # Default to "startup" if missing (backwards compat with older Claude Code).
        # Validate against the known set — an unrecognized source is surfaced
        # as "unknown" so it cannot inject arbitrary text into additionalContext.
        # isinstance(str) guard short-circuits the `in _VALID_SOURCES` test for
        # unhashable inputs (list, dict) that would otherwise raise TypeError,
        # bubble to the outer safety-net, and skip the session_start journal
        # write — breaking #414 R2's fail-open contract.
        _VALID_SOURCES = {"startup", "resume", "compact", "clear"}
        raw_source = input_data.get("source", "startup")
        source = (
            raw_source
            if isinstance(raw_source, str) and raw_source in _VALID_SOURCES
            else "unknown"
        )
        is_context_reset = source in ("compact", "clear")
        # Marker deletion uses a narrower guard: only user-initiated clear
        # triggers it. Compact is involuntary (auto-compaction under context
        # pressure) and the orchestrator is still mid-work — wiping the marker
        # on compact re-engages the bootstrap gate mid-task, blocking
        # Edit/Write/Agent when the orchestrator needs them most (#414).
        is_marker_reset = source == "clear"

        # Clean up stale compact-summary from previous sessions.
        # Only "compact" source needs it (just written by postcompact_archive).
        if source != "compact":
            try:
                COMPACT_SUMMARY_PATH.unlink(missing_ok=True)
            except OSError:
                pass  # Fail-open: don't block session init for cleanup

        # Clear bootstrap-complete marker on user-initiated clear only (#414).
        #
        # Cannot use get_session_dir() here because the context module
        # hasn't been initialized yet (build_context_cache() runs at step 5a
        # below). Uses build_session_path() directly — it has its own
        # path traversal guard (Path.parents containment check).
        #
        # Scope: ONLY the marker is removed; team config persists. The
        # writer hook self-heals the marker on the next prompt as long as
        # team config + secretary remain on disk. See _clear_bootstrap_marker.
        if is_marker_reset:
            reset_session_id = input_data.get("session_id", "")
            if reset_session_id and project_dir:
                slug = Path(project_dir).name
                session_path = build_session_path(slug, str(reset_session_id))
                _clear_bootstrap_marker(session_path)

        # 0. Check required PACT dirs are in additionalDirectories (one-time tip)
        # Only check on fresh startup — resumed/compacted sessions already had the check
        if not is_context_reset:
            dirs_tip = check_additional_directories()
            if dirs_tip:
                system_messages.append(dirs_tip)

        # 0b. One-time in-process teammateMode notice (#864 Phase 1, ADDITIVE).
        # Warn that unattended runs may stall in in-process mode and recommend
        # `--teammate-mode tmux`. User-facing recommendation (the model cannot
        # relaunch itself) → system_messages channel, mirroring the step-0
        # additionalDirectories tip.
        #
        # WHEN: emit only on session-LAUNCH events (startup + resume). A
        # resumed session is the walk-away/unattended case worth re-warning,
        # and each launch fires SessionStart exactly once for that source — so
        # NO marker file is needed to stay once-per-launch. `compact` and
        # `clear` are mid-launch context-reset events that CAN re-fire within a
        # single launch; they are SUPPRESSED so the notice is never repeated.
        # An unrecognized source (normalized to "unknown") is also suppressed.
        #
        # Fail-safe: should_emit_inprocess_notice() is total (never raises) and
        # returns True on any read/parse uncertainty. The belt-and-suspenders
        # try/except ALSO emits on any unexpected escape (e.g. an import
        # failure) — "emit on uncertainty" is the protected direction — and it
        # MUST NOT raise out of the SessionStart hot path.
        #
        # ALLOWLIST MAINTENANCE: a future Claude Code launch-like source not in
        # this tuple normalizes to "unknown" (see source-normalization above)
        # and is SUPPRESSED — update this allowlist if such a launch source is
        # added upstream.
        if source in ("startup", "resume"):
            try:
                from shared.teammate_mode import should_emit_inprocess_notice
                if should_emit_inprocess_notice():
                    system_messages.append(_INPROCESS_MODE_NOTICE)
            except Exception:  # noqa: BLE001 — fail-safe → emit; never block init
                system_messages.append(_INPROCESS_MODE_NOTICE)

        # 0c. Unknown-role startup warning (#878). The lead-only writes in
        # steps 5a/5b/8 are gated behind is_lead below; a frame with NO
        # recognized role (no `--agent` flag, OR a present-but-unrecognized /
        # typo'd agent_type) silently performs none of them. Surface that so a
        # mis-launched orchestrator is observable. Conditional emission mirroring
        # the 0b notice shape (NOT a new numbered init step — keeps clear of the
        # module/main() docstring-parity convention). Launch events only
        # (startup/resume): a mid-launch compact/clear context-reset must not
        # re-fire it. The unknown-role decision (incl. the is_lead-first ordering,
        # the live specialist-registry check against env plugin_root, and the
        # PACT:-strip) lives in _should_warn_unknown_role — total (never raises),
        # so no try/except is needed at the call site.
        if source in ("startup", "resume") and _should_warn_unknown_role(input_data):
            system_messages.append(_UNKNOWN_ROLE_NOTICE)

        # 1. Set up plugin symlinks (enables @~/.claude/protocols/pact-plugin/ references)
        # Context resets (compact/clear): symlinks are already set up from original session
        if not is_context_reset:
            symlink_result = setup_plugin_symlinks()
            if symlink_result and "failed" in symlink_result.lower():
                system_messages.append(symlink_result)
            elif symlink_result:
                context_parts.append(symlink_result)

        # 3. Ensure project has CLAUDE.md with memory sections
        project_md_msg = ensure_project_memory_md()
        if project_md_msg:
            if "failed" in project_md_msg.lower() or "skipped" in project_md_msg.lower():
                system_messages.append(project_md_msg)
            else:
                context_parts.append(project_md_msg)

        # 3b. One-time migration: wrap existing project CLAUDE.md in
        # PACT_MANAGED boundary and add PACT_MEMORY markers (#404).
        # Runs after ensure_project_memory_md() so newly created files
        # already have the new structure, and before staleness checks
        # so the staleness parser sees the migrated layout.
        # Idempotent no-op when PACT_MANAGED_START marker is already present.
        migration_msg = migrate_to_managed_structure()
        if migration_msg:
            if "failed" in migration_msg.lower() or "skipped" in migration_msg.lower():
                system_messages.append(migration_msg)
            else:
                context_parts.append(migration_msg)

        # Step 3c retired in v4.2.15 — orphan-stripper sunset; see git log for context.

        # 3d. SUNSET BEFORE v5.0.0: strip the obsolete PACT_START/PACT_END
        # kernel block from ~/.claude/CLAUDE.md (v3.x kernel-in-home-dir
        # architecture; replaced by --agent flag in v4.0). Idempotent no-op
        # once stripped. "Migration skipped: ..." status routes to
        # systemMessages so the user sees malformed-marker warnings.
        kernel_strip_msg = strip_orphan_kernel_block()
        if kernel_strip_msg:
            if "failed" in kernel_strip_msg.lower() or "skipped" in kernel_strip_msg.lower():
                system_messages.append(kernel_strip_msg)
            else:
                context_parts.append(kernel_strip_msg)

        # 3e. Layer 3 (cross-cutting disk hygiene per #797): reap
        # unconsumed merge-authorization tokens older than
        # ORPHAN_TOKEN_MAX_AGE_SECONDS (12x TOKEN_TTL). Secondary trigger
        # — eager cleanup at session start so orphans don't accumulate
        # across long sessions where no dangerous-Bash command is run
        # (the primary trigger in merge_guard_pre.find_valid_token only
        # fires on dangerous-Bash precheck). Fail-open: cleanup_orphan_tokens
        # swallows all OSError paths; this try/except is belt-and-suspenders
        # for any TOKEN_DIR resolution flake.
        try:
            _cleanup_orphan_tokens(TOKEN_DIR)
        except Exception:
            pass  # Fail-open: never block session init for disk hygiene.

        # 4. Check for stale pinned context
        staleness_msg = check_pinned_staleness()
        if staleness_msg:
            if "failed" in staleness_msg.lower() or "skipped" in staleness_msg.lower():
                system_messages.append(staleness_msg)
            else:
                context_parts.append(staleness_msg)

        # 4a. Surface pin slot count (#492). Tier-0 additionalContext —
        # architecturally binding, survives compaction. Fail-open: None
        # when CLAUDE.md cannot be resolved or parsed.
        slot_status_msg = check_pin_slot_status()
        if slot_status_msg:
            context_parts.append(slot_status_msg)

        # 4b. Emit unconditional stale-block directive when stale pin
        # count meets threshold (#492). Never exit-2 — breaks /clear and
        # /resume per plan key-decisions row 6.
        stale_block_msg = check_pin_stale_block_directive()
        if stale_block_msg:
            context_parts.append(stale_block_msg)

        # 4c. Surface plugin manifest diagnostic (#500). Tier-0 additionalContext —
        # total-function banner; always emits, even on read/parse failure.
        # Lets both team-lead and teammate context readers cross-reference
        # worktree edits against the resolved installed-cache root at a
        # glance. Helper is total: no conditional append, no try/except
        # wrapper at the call site.
        context_parts.append(format_plugin_banner())

        # 5. Remind orchestrator to create session-unique PACT team (or reuse on resume)
        team_name = generate_team_name(input_data)

        # 5a. Build the session context FIRST so get_session_dir() works for
        # subsequent journal writes. build_context_cache() populates the _cache
        # immediately (for every frame), enabling append_event() to derive the
        # journal path; persist_context() then writes the file (lead frames only).
        # Defensive substitution: the RA1+RG2 schema validator (commit 2d6448c)
        # rejects empty strings for str-typed required fields, so an empty
        # session_id would cause append_event() to silently drop the
        # session_start event. Substitute a non-empty per-process-unique
        # sentinel so downstream code paths that require a non-empty string
        # (e.g., team name derivation, log formatting) still function.
        # Reachable in production via the malformed-stdin fallback above
        # (input_data = {} on JSONDecodeError); latent otherwise because
        # Claude Code reliably provides session_id.
        #
        # R3 (MEDIUM, 2026-04-06): The sentinel must NOT touch disk. The
        # per-process unique suffix (`unknown-{token_hex(4)}`) means every
        # malformed-stdin session generates a unique path like
        # `~/.claude/pact-sessions/{slug}/unknown-a3f9b2c4/`. session_end's
        # cleanup_old_sessions filters by strict _UUID_PATTERN, which
        # "unknown-*" never matches — so these directories accumulate
        # indefinitely. Gate BOTH persistence call sites (the
        # build_context_cache/persist_context pair and append_event) on
        # session_id_was_missing to prevent the leak. The
        # existing CLAUDE.md guard at step 5b handles its own persistence.
        # The session_start journal anchor event is intentionally dropped on
        # the malformed-stdin path: without a valid session_id, we cannot
        # durably record the session, and creating an orphaned journal file
        # in an unreapable directory is worse than the missing anchor.
        #
        # DESIGN DECISION (2026-04-06, user-authorized): on the malformed-stdin
        # path, BOTH the journal session_start anchor AND the CLAUDE.md
        # Current Session block are intentionally skipped. This reverses the
        # earlier "Finding A" priority that preserved the anchor in the
        # journal for visibility. The reversal was authorized after the
        # trade-off was surfaced explicitly: R3 (silent unbounded disk leak
        # from the unreapable `unknown-{hex}/` directory) is a strictly worse
        # failure mode than Finding A (visible-in-stderr dropped anchor). The
        # two are mutually exclusive because append_event() is what creates
        # the leaked directory in the first place — preserving the anchor
        # IS what causes the leak. The dropped-anchor outcome is observable
        # via the stderr warning emitted below, so the loss of visibility
        # is bounded; the disk leak is not.
        raw_id = input_data.get("session_id")
        # Single canonical predicate (R-1+R-2): rejects None, non-strings,
        # empty strings, whitespace-only strings, and any "unknown-*" sentinel.
        # The CLAUDE.md write gate at step 5b consults the same helper so the
        # two predicates can never drift.
        session_id_was_missing = _is_unknown_or_missing_session(raw_id)
        if not session_id_was_missing:
            session_id = str(raw_id)
        else:
            session_id = f"unknown-{secrets.token_hex(4)}"
            # Issue #399: record this failure in the global ring buffer log
            # BEFORE emitting the stderr warning. The ring buffer is the
            # only observability surface that survives across sessions and
            # aggregates across both team-lead and teammate sessions — stderr
            # output from hooks is not visible to users, and the single-
            # instance safety net only reaches the team-lead's first-message
            # context. Defense in depth: append_failure fails-open
            # internally, but we also wrap the call in its own try/except
            # so a future refactor weakening that contract cannot crash
            # session_init. The classification distinguishes the three
            # main failure kinds so post-hoc analysis can see the shape
            # of the problem.
            # Classification ladder — order matters. Each branch isolates a
            # distinct upstream failure kind so post-hoc diagnosis can tell
            # them apart. The ladder mirrors the branches of
            # _is_unknown_or_missing_session() plus the malformed_json case
            # that funnels through the JSONDecodeError fallback at the top
            # of main(). The control_char_session_id branch must run BEFORE
            # the sentinel check because an attacker could craft an id with
            # an embedded newline + injected directive that would otherwise
            # be classified as a plain sentinel, losing the signal that an
            # injection was attempted.
            if stdin_json_error is not None:
                _classification = "malformed_json"
                _error_detail = stdin_json_error
            elif raw_id is None:
                _classification = "missing_session_id"
                _error_detail = "session_id key absent from stdin payload"
            elif not isinstance(raw_id, str):
                _classification = "non_string_session_id"
                _error_detail = f"session_id was {type(raw_id).__name__}: {raw_id!r}"
            elif not raw_id.strip():
                _classification = "empty_session_id"
                _error_detail = f"session_id was empty/whitespace: {raw_id!r}"
            elif _SESSION_ID_CONTROL_CHARS_RE.search(raw_id):
                # Newlines, NUL, BEL, ESC, DEL, etc. anywhere in the id.
                # Flags the CLAUDE.md routing-marker injection attack class
                # explicitly so failure_log entries identify the smell.
                _classification = "control_char_session_id"
                _error_detail = f"session_id contained C0/DEL control char: {raw_id!r}"
            elif raw_id.strip().startswith("unknown-"):
                # Matches _is_unknown_or_missing_session which uses
                # "unknown-" (with hyphen) to match only the sentinel format
                # "unknown-{hex}" without false-positiving on unrelated ids.
                _classification = "sentinel_session_id"
                _error_detail = f"session_id already an unknown-* sentinel: {raw_id!r}"
            else:
                # Terminal catchall — reached only if a future change to
                # _is_unknown_or_missing_session adds a rejection branch
                # that this ladder does not cover yet.
                _classification = "other"
                _error_detail = f"session_id rejected by predicate: {raw_id!r}"
            try:
                append_failure(
                    classification=_classification,
                    error=_error_detail,
                    cwd=os.getcwd(),
                    source=source,
                )
            except Exception:
                # Belt-and-suspenders: append_failure already fails-open
                # internally, but the R3 gate MUST NEVER raise. Swallow
                # any exception that escapes the ring buffer logic.
                pass
            print(
                f"session_init: missing session_id in stdin payload; "
                f"using fallback {session_id} (no disk persistence)",
                file=sys.stderr,
            )
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        # Lead-role gate (#877). is_lead is total (never raises) and reads only
        # the harness-set agent_type. Computed once and reused for both Class-A
        # writes below so the disk-write split and the journal-anchor gate share
        # one verdict.
        frame_is_lead = is_lead(input_data)
        if not session_id_was_missing:
            try:
                # SEAM (#877): compose the two halves directly. ALWAYS build +
                # cache (every frame gets the in-process context so
                # get_session_dir() and append_event's path-resolution behave
                # identically), then persist to disk ONLY for a lead frame so a
                # teammate/plain frame never clobbers the lead's on-disk
                # session-context file (or creates a phantom session dir).
                # build_context_cache is the sole owner of _cache; persist_context
                # is the is_lead-gated best-effort disk side-effect. See the
                # build_context_cache / persist_context docstrings.
                _ctx_result = build_context_cache(
                    team_name, session_id, project_dir, plugin_root,
                )
                if frame_is_lead and _ctx_result is not None:
                    persist_context(*_ctx_result)
            except Exception as e:
                # Fail-open: context file is best-effort; hooks fall back to empty strings
                print(f"session_init: could not write context file: {e}", file=sys.stderr)

            # Write session_start event to journal (after build_context_cache so
            # path is available). Lead-only (#877): the journal session_start anchor is a
            # lead-only write — a teammate/plain frame would append a phantom
            # anchor to (or create) a session journal it does not own.
            # `source` is the already-normalized value from the `_VALID_SOURCES`
            # check above — one of {startup, resume, compact, clear, unknown}.
            # Persisting it here gives downstream triage direct attribution for
            # marker-wipe and other source-conditioned behavior, instead of
            # forcing triangulation from timing clusters (#414 R2).
            if frame_is_lead:
                append_event(
                    make_event(
                        "session_start",
                        team=team_name,
                        session_id=session_id,
                        project_dir=project_dir,
                        worktree="",  # Not yet created at this point
                        source=source,
                    ),
                )

        try:
            team_config = Path.home() / ".claude" / "teams" / team_name / "config.json"
            team_exists = team_config.exists()
        except OSError:
            # Fail-open: if filesystem check fails, assume fresh session
            team_exists = False

        # Resolve session_dir early so substitution instructions can include it.
        # get_session_dir() works here because build_context_cache() populated _cache above.
        # Suppress session_dir for the unknown-* sentinel so the literal
        # `.../unknown-xxxx/` path never leaks into the substitution instructions
        # block — otherwise the orchestrator would obediently mkdir that path
        # for any command that uses {session_dir}, bypassing the CLAUDE.md guard
        # below.
        session_dir = get_session_dir() if not session_id_was_missing else ""

        # Build context message based on source × team_exists (5 paths)
        # Session placeholder variable substitution instructions tell the orchestrator how to
        # replace {team_name}, {session_dir}, and {plugin_root} in command snippets.
        if session_dir:
            _substitutions = (
                f'Session placeholder variables (substitute before running commands): '
                f'Use the name `{team_name}` wherever {{team_name}} appears in commands. '
                f'Use `{session_dir}` wherever {{session_dir}} appears in commands. '
                f'Use `{plugin_root}` wherever {{plugin_root}} appears in commands.'
            )
        else:
            _substitutions = (
                f'Session placeholder variables (substitute before running commands): '
                f'Use the name `{team_name}` wherever {{team_name}} appears in commands. '
                f'Session dir unavailable (session_id missing from stdin) — '
                f'do not run commands that depend on {{session_dir}} until next clean start. '
                f'Use `{plugin_root}` wherever {{plugin_root}} appears in commands.'
            )
        _team_reuse = (
            f'YOUR PACT ROLE: orchestrator.\n\n'
            f'Invoke Skill("PACT:bootstrap") immediately, without waiting for user input. '
            f'Do this before anything else. '
            f'Do not evaluate whether it is needed. '
            f'You must invoke Skill("PACT:bootstrap") on every session start.\n\n'
            f'Your team is `{team_name}` (existing — resumed session). '
            f'Do not call TeamCreate — the team already exists. '
            f'{_substitutions}'
        )
        _team_create = (
            f'YOUR PACT ROLE: orchestrator.\n\n'
            f'Invoke Skill("PACT:bootstrap") immediately, without waiting for user input. '
            f'Do this before anything else. '
            f'Do not evaluate whether it is needed. '
            f'You must invoke Skill("PACT:bootstrap") on every session start.\n\n'
            f'After bootstrap completes, your next action is: TeamCreate(team_name="{team_name}"). '
            f'Do not read files, explore code, or respond to the user until bootstrap and team creation are complete. '
            f'{_substitutions}'
        )

        # Hoist get_task_list() above the source-branch dispatch so both the
        # compact-branch checkpoint (below) and step 6 resumption (line ~885)
        # consume the SAME `tasks` variable. Before hoisting, the two call
        # sites produced an asymmetric fail-open shape: a raise at the
        # compact-branch site fell through to _build_safety_net_context
        # (directive only, no checkpoint); a raise at step 6 left directive +
        # checkpoint + no-resumption. Single call site means identical
        # fallback shape on either failure.
        #
        # Fail-open layering (defense in depth):
        #   1. Primary: get_task_list() has its own internal try/except
        #      (shared/task_utils.py:50-59) that returns None on any
        #      filesystem or JSON parse error. Callers never see a raise
        #      from a corrupted tasks dir.
        #   2. Belt-and-suspenders: main()'s outer try/except catches
        #      unexpected exceptions in the downstream checkpoint-
        #      construction helpers (find_feature_task, find_current_phase,
        #      find_active_agents, find_blockers, build_post_compaction_
        #      checkpoint) — these do NOT have internal exception guards.
        #      A raise there drops the whole compact branch and falls
        #      through to _build_safety_net_context, which still carries
        #      the bootstrap directive.
        tasks = get_task_list()

        # Family E relocation (#806): a separate-process (e.g. tmux/iterm2)
        # teammate fires its OWN SessionStart — unlike an in-process teammate,
        # which fires SubagentStart (covered by peer_inject). The two surfaces
        # are mode-exclusive (one teammate fires exactly one), so injecting the
        # peer-context body here causes no double-injection. classify_session_role
        # is the fail-safe gate: only a genuine "teammate" frame takes this branch;
        # "lead" AND "unknown"/empty agent_type both fall to the else-branch, which
        # keeps the existing orchestrator-directive ladder UNCHANGED. Emitting the
        # marker-free body (include_role_marker=False) ALSO suppresses the
        # "YOUR PACT ROLE: orchestrator" block for teammate frames — that
        # unconditional orchestrator block was the mis-roling bug (a teammate
        # self-identifying as orchestrator); the role marker is omitted because
        # the spawn prompt already owns the role and session_init lacks agent_name
        # under tmux. This is a CONDITIONAL EMISSION, not a new numbered step.
        if classify_session_role(input_data) == "teammate":
            # O1 fix + Finding-1. team_name above is generate_team_name(input_data)
            # = pact-{this teammate's OWN session hash}, NOT the lead's team — so
            # resolve the lead's team + this teammate's own member name by pane-id
            # match (resolve_lead_team_by_pane reads our pane id from env and
            # matches members[].tmuxPaneId in the team configs the harness writes).
            # On a miss (ambiguous / no pane id / in-process), fall back to the
            # session-derived team_name + stdin agent_name — no worse than pre-fix.
            # The matched member name gives EXACT-name self-exclusion (full peer
            # list, not the agentType-narrowed one).
            # Finding-1 (security): the resolver + get_peer_context now read a LIVE
            # config that could be malformed — wrap the whole resolve→build→insert
            # in a fail-open guard so it degrades to NO injection and NEVER lets an
            # exception reach main()'s outer except → _build_safety_net_context
            # (which would mis-role the teammate as orchestrator). Mirrors
            # peer_inject's fail-open-to-no-injection contract.
            try:
                _resolved = resolve_lead_team_by_pane()
                if _resolved:
                    _tn, _own = _resolved
                else:
                    _tn, _own = team_name, input_data.get("agent_name", "")
                _peer_body = get_peer_context(
                    agent_type=input_data.get("agent_type", ""),
                    team_name=_tn,
                    agent_name=_own,
                    include_role_marker=False,
                )
                if _peer_body:
                    context_parts.insert(0, _peer_body)
            except Exception:
                pass  # fail-open: no injection; never the orchestrator safety-net
        else:
            if source == "compact" and team_exists:
                # Post-compaction: bootstrap directive (in _team_reuse) subsumes
                # "recover state" guidance; keep concrete task-resumption bullets
                # for the orchestrator's next actions after bootstrap.
                context_parts.insert(0, (
                    f'{_team_reuse} '
                    f'After bootstrap, recover session state: '
                    f'(1) Read {COMPACT_SUMMARY_PATH} for prior context, '
                    f'(2) Run TaskList to find in-progress work, '
                    f'(3) TaskGet on in-progress tasks for details. '
                    f"Re-engage secretary: SendMessage(to='secretary', "
                    f"message='Post-compaction: deliver session briefing with current state.')."
                ))
                # Secondary-layer (#444): append POST-COMPACTION CHECKPOINT block
                # when tasks in_progress. Consumes the hoisted `tasks` variable
                # (single source of truth).
                if tasks:
                    _in_progress = [
                        t for t in tasks
                        if t.get("status") == "in_progress"
                    ]
                    if _in_progress:
                        _checkpoint_block = build_post_compaction_checkpoint(
                            feature=find_feature_task(tasks),
                            phase=find_current_phase(tasks),
                            agents=find_active_agents(tasks),
                            blockers=find_blockers(tasks),
                        )
                        context_parts.append(_checkpoint_block)
            elif source == "clear" and team_exists:
                # Context cleared via /clear: no compact-summary, but team and tasks survive
                context_parts.insert(0, (
                    f'{_team_reuse} '
                    f'CONTEXT CLEARED: Your context was cleared via /clear. '
                    f'State recovery: '
                    f'(1) TaskList for current tasks, '
                    f'(2) TaskGet on in-progress tasks. '
                    f"Re-engage secretary: SendMessage(to='secretary', "
                    f"message='Context cleared: deliver fresh briefing with current project state.')."
                ))
            elif source == "resume" and team_exists:
                # Normal resume: model retains context, team exists
                context_parts.insert(0, (
                    f'{_team_reuse} '
                    f'Check session journal for paused state from /PACT:pause.'
                ))
            elif source == "startup" and not team_exists:
                # Fresh session: full initialization
                context_parts.insert(0, _team_create)
            elif team_exists:
                # Anomalous: unexpected source but team exists (e.g., startup + team exists)
                # Reuse team, note the anomaly
                context_parts.insert(0, (
                    f'{_team_reuse} '
                    f'Note: Unexpected session source "{source}" with existing team — '
                    f'reusing team. Run TaskList to check current state.'
                ))
            else:
                # Differentiate the no-team branch by whether `source` is a
                # recognized lifecycle value:
                #   - known source + no team → informational note (recovery
                #     hint stays; no WARNING tone). Legitimate first-session-
                #     after-stale-CLAUDE.md class.
                #   - unknown source + no team → emit WARNING in
                #     additionalContext AND stderr for observability (debug
                #     logs surface the malformed-stdin signal).
                _KNOWN_SOURCES = {"startup", "resume", "compact", "clear"}
                if source in _KNOWN_SOURCES:
                    context_parts.insert(0, (
                        f'{_team_create} '
                        f'Session source "{source}" without team — '
                        f'creating fresh team. Run TaskList to check current state.'
                    ))
                else:
                    print(
                        f"session_init: unknown source value: {source!r}",
                        file=sys.stderr,
                    )
                    context_parts.insert(0, (
                        f'{_team_create} '
                        f'WARNING: Unrecognized session source "{source}" — '
                        f'previous session state may be lost. '
                        f'Check TaskList for recovery context.'
                    ))

        # 5a. Capture the PREVIOUS session's dir from project CLAUDE.md
        # before step 5b overwrites the Current Session block with THIS
        # session's info. READ-BEFORE-WRITE invariant: _extract_prev_session_dir
        # must run before update_session_info, otherwise it reads back the
        # just-written current session dir and silently breaks cross-session
        # resume (step 7) and paused-work detection (step 8).
        prev_session_dir = _extract_prev_session_dir(project_dir)

        # 5b. Write session resume info to project CLAUDE.md
        # (session_dir already resolved above for substitution instructions)
        # Skip the CLAUDE.md write when session_id is an "unknown-*" sentinel
        # (bundle 5 fallback for missing stdin; per-process unique suffix).
        # On the malformed-stdin path BOTH the journal session_start event and
        # the CLAUDE.md Current Session block are skipped (see the gate above
        # around append_event and the rationale at step 5 intro): writing
        # `- Session dir: .../unknown-xxxx/` into CLAUDE.md pollutes state
        # recovery: session_resume.py:199 would feed `.../unknown-xxxx/` into
        # _extract_prev_session_dir, and session_end.py:cleanup_old_sessions
        # filters by _UUID_PATTERN (which "unknown-*" never matches), so the
        # directory would accumulate indefinitely.
        # Lead-only (#877): the CLAUDE.md "## Current Session" block is the true
        # CROSS-PROCESS CLOBBER — a teammate/plain frame writing it overwrites
        # the lead's session block in the shared project file. Gate on is_lead
        # in addition to the existing sentinel guard.
        if frame_is_lead and not _is_unknown_or_missing_session(session_id):
            session_msg = update_session_info(session_id, team_name, session_dir, plugin_root)
            if session_msg:
                if "failed" in session_msg.lower() or "skipped" in session_msg.lower():
                    system_messages.append(session_msg)
                else:
                    context_parts.append(session_msg)

        # 6. Check for in_progress Tasks (resumption context via Task
        # integration). Consumes the hoisted `tasks` variable (single
        # source of truth; #444 post-boundary dedup).
        if tasks:
            resumption_msg = check_resumption_context(tasks)
            if resumption_msg:
                # Blockers are critical - put in system message for visibility
                if "**Blockers:" in resumption_msg:
                    system_messages.append(resumption_msg)
                else:
                    context_parts.append(resumption_msg)

        # 7. Restore last session snapshot for cross-session continuity
        # (prev_session_dir was captured in step 5a, before step 5b overwrote
        # the Current Session block.)
        session_snapshot = restore_last_session(prev_session_dir=prev_session_dir)
        if session_snapshot:
            context_parts.append(session_snapshot)

        # 8. Check for paused work from previous session's /PACT:pause.
        # Lead-only (#877): mechanically this is a READ (it surfaces a
        # resume/paused-work prompt, not a write), but surfacing the lead's
        # paused-work is a lead-only operation — a teammate/plain frame must not
        # receive the lead's resume prompt. Gate the check itself so a non-lead
        # frame does no journal read either.
        if frame_is_lead:
            paused_msg = check_paused_state(prev_session_dir=prev_session_dir)
            if paused_msg:
                context_parts.append(paused_msg)

        # Build output
        output = {}

        if context_parts or system_messages:
            # hookEventName is required by the harness; missing it silently fails open
            output["hookSpecificOutput"] = {
                "hookEventName": "SessionStart",
                "additionalContext": " | ".join(context_parts) if context_parts else "Success"
            }

        if system_messages:
            output["systemMessage"] = " | ".join(system_messages)

        # context_parts is guaranteed non-empty on the happy path: the
        # team-reuse/team-create instruction is always insert(0, ...)'d
        # earlier in main(), so `output["hookSpecificOutput"]` is always
        # populated by this point. The exception safety net at the bottom
        # of main() builds its own output and never falls through here.
        print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        # Safety net: even when main() throws before building the normal
        # output, the team-lead still needs the governance delivery chain.
        # Emit a minimal PACT ROLE marker + bootstrap skill directive in
        # additionalContext, alongside the error in systemMessage. Claude
        # Code's hook-output schema supports both fields in the same JSON.
        print(f"Hook warning (session_init): {str(e)[:200]}", file=sys.stderr)
        safety_net_context = _build_safety_net_context(team_name)
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": safety_net_context,
            },
            "systemMessage": f"PACT hook warning (session_init): {str(e)[:100]}",
        }
        print(json.dumps(output))
        sys.exit(0)


if __name__ == "__main__":
    main()
