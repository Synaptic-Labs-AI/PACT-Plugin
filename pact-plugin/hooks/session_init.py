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
5. Generates session-unique PACT team name and writes it to the session context (the platform pre-creates the team)
5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
6. Checks for in_progress Tasks (resumption context via Task integration)
7. Restores last session snapshot for cross-session continuity
8. Checks for paused or refreshed work from a previous /PACT:pause or /PACT:refresh,
   and records session_resumption_surfaced when a resume claim surfaces

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

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# Import shared Task utilities (DRY - used by multiple hooks)
from shared import compaction_owner
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

from shared import (
    BOOTSTRAP_MARKER_NAME,
    SESSION_ID_CONTROL_CHARS_RE,
    build_session_path,
    project_slug,
)
from shared.constants import (
    COMPACT_SUMMARY_ARCHIVE_PREFIX,
    COMPACT_SUMMARY_NAME,
    COMPACT_SUMMARY_ORPHAN_NAME,
    get_compact_summary_path,
)
from shared.pact_context import (
    _is_unknown_or_missing_session,
    _resolve_aligned_team_name,
    build_context_cache,
    classify_session_role,
    generate_team_name,
    get_session_dir,
    get_session_id,
    is_lead,
    persist_context,
    strip_pact_namespace,
)
from shared.dispatch_helpers import is_registered_pact_specialist
from shared.session_journal import append_event, make_event
from shared.failure_log import append_failure
from shared.plugin_manifest import format_plugin_banner
from shared.pact_config import llm_options
from shared.peer_context import get_peer_context
from shared.session_registry import resolve as _registry_resolve
from shared.paths import get_claude_config_dir
from shared import state_file
from shared.project_scope import WORKTREE_IDENTITY_FILE, _rev_parse_path
from shared import backlog_store

# Import extracted modules (decomposed for maintainability per M5 audit finding).
from shared.symlinks import SYMLINKS_VERIFIED_MESSAGE, setup_plugin_symlinks
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
    check_resume_state,
    has_unspent_refresh,
)


# One-time startup notice about unattended-run stalls, emitted when the
# effective teammateMode is not positively "tmux". Emitted via system_messages
# (user-facing) by main() step 0b. Lives HERE (presentation layer) rather than
# in shared/teammate_mode.py (resolution layer) per SRP.
# Pure literal (no interpolation) so tests can pin the exact substring.
#
# THE tmux CLAIM IS SCOPED ON PURPOSE AND MUST STAY SCOPED. An unattended run
# stalls on two independent channels: a teammate wake not being delivered, and
# a background job finishing with nobody listening. Switching teammate mode
# addresses the FIRST ONLY — the second never uses the message path. An earlier
# version of this notice recommended tmux without that bound, so a reader could
# follow it, switch modes, and still stall on the failure the notice appears to
# warn about. Do not restore an unqualified "relaunch with tmux for hands-off
# runs": that sentence is the defect, not a simplification of it.
_INPROCESS_MODE_NOTICE = (
    "PACT: unattended runs may stall in in-process teammate mode "
    "(the lead can sit idle awaiting a wake that needs a manual nudge). "
    "`--teammate-mode tmux` makes teammate wake delivery reliable; it does "
    "NOT cover a background job that finishes with nobody watching "
    "— see reference/unattended-runs.md."
)

# Unknown-role startup warning. The lead-only writes below are gated
# behind is_lead, which keys on the harness-set agent_type field. A session
# launched WITHOUT `--agent` (or with a non-PACT agent_type) carries no
# recognizable role — classify_session_role() returns "unknown" — so its
# session_init silently performs none of the lead-only writes. That is the
# intended fail-toward-teammate direction, but it is invisible to an operator
# who MEANT to launch the orchestrator and forgot the flag. This notice makes
# that case observable. IT RIDES TWO CHANNELS ACROSS THREE EMISSION SITES,
# AND EACH CHANNEL HAS ITS OWN POPULATION, BECAUSE THE GATES USE DIFFERENT
# PREDICATES. Do not state one population for the pair.
#   systemMessage: THE EMISSION NEEDS TWO CONDITIONS TOGETHER. The source
#     must be a launch event (`startup` or `resume`), AND
#     _should_warn_unknown_role must pass. That predicate is the WIDER of
#     the two: it passes for a frame with no recognized role, and ALSO for
#     an agent_type that is present but is not the lead and is not a
#     registered specialist. So a typo such as `--agent pact-architct`
#     classifies as a teammate and reaches this channel ON A LAUNCH, and on
#     a compact or a clear it reaches no channel at all.
#   additionalContext: gated by `frame_role == "unknown"`, which needs an
#     ABSENT agent_type, because a truthy agent_type classifies as a
#     teammate. A typo does NOT reach this channel.
# Pure literal so tests can pin the exact substring.
_UNKNOWN_ROLE_NOTICE = (
    "PACT: this session has no recognized agent role (no `--agent` flag, or an "
    "unrecognized agent_type), so lead-only session setup was skipped. If you "
    "meant to drive PACT as the orchestrator, relaunch with "
    "`--agent PACT:pact-orchestrator`."
)


# Caveat appended to the symlink refresh status when the refresh MOVED
# something. It names the subject of the repair and the one thing the repair
# does NOT reach.
#
# EMITTED ONLY WHEN A LINK MOVED, and that is a correctness property rather
# than a matter of taste. The refresh makes the resolution SURFACE current and
# it does NOT make a LOADED body current, because an agent keeps the body it
# got at spawn. A notice that spoke on a quiet session start would report
# "surface current" almost always, and a reader would take that as "the bodies
# are fresh". ONE-DIRECTIONAL EMISSION removes that reading: the notice emits
# no green, so no green can be misread.
_SYMLINK_REPOINT_NOTICE = (
    "The PACT agent and protocol links now point at the current plugin root. "
    "This does NOT refresh an agent that is live: an agent keeps the body it "
    "got at spawn, and a new spawn gets the current body."
)


def _should_warn_unknown_role(input_data: dict) -> bool:
    """Decide whether the unknown-role startup notice should fire.

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
    stripped = strip_pact_namespace(agent_type)
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
        # THE MARKER NAME COMES FROM `shared.constants`, AND NOT FROM
        # `pin_staleness_gate`. The gate is a fail-CLOSED PreToolUse module: its
        # load wrapper prints a PreToolUse deny and calls `sys.exit(2)`. That
        # posture is correct for a PreToolUse frame and incorrect for this
        # SessionStart one, where a deny payload answers an event that nobody
        # can deny. `shared.constants` has no exit path, and session_init loads
        # `shared` at its module top in any case.
        #
        # THE HOT-PATH ARGUMENT THAT USED TO SIT HERE IS SPENT, AND THE REASON
        # IS RECORDED SO THAT NOBODY RESTORES IT. It said to keep the import
        # lazy so that `pin_staleness_gate` does not load on each SessionStart.
        # This import no longer reaches that module at all, so the cost it
        # named is gone. These imports stay function-local, which keeps them
        # in the post-signal branch.
        from shared.pact_context import get_session_dir
        from shared.constants import PIN_STALENESS_MARKER_NAME
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
    # 🔴 NAME THE COMMAND THAT ARCHIVES. This directive named
    # `/PACT:pin-memory`, which does NOT archive: it ADDS a pin and it sends
    # the user to `/PACT:prune-memory` for removal. THIS IS THE PRIMARY
    # enforcement surface for the stale-pin condition, AND IN AN UNKNOWN FRAME
    # IT IS THE ONLY ONE. This directive is appended when the frame role is not
    # `teammate`, so a lead frame and an unknown frame alike receive it, while
    # `pin_staleness_gate` returns early unless `pact_context.is_lead` holds.
    # THE GATE BACKSTOPS THE LEAD FRAME AND IT DOES NOT REACH AN UNKNOWN ONE,
    # so an incorrect command here reaches a user that nothing refuses later.
    # DO NOT WRITE THAT A BACKSTOP COVERS THIS TEXT. The exclusion of the
    # unknown frame is INCIDENTAL rather than intended, and the repair for it
    # is tracked on its own, because a DENY widened to a population it does not
    # cover needs its own over-block check.
    # The gate carried the same incorrect name and the two were corrected
    # together.
    # BEFORE YOU EDIT THIS STRING, OPEN THE COMMAND FILE AND CONFIRM THE
    # COMMAND ARCHIVES. This text is not evidence about its own subject.
    return (
        f"Pinned context: {signal.detail}. "
        f"You MUST run /PACT:prune-memory to archive stale pins before adding new ones."
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
        settings_path = get_claude_config_dir() / "settings.json"
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

        # Check which required directories are missing. The path DISPLAYED is
        # the same object the membership test ran on — deliberately not a
        # separate display literal. A tilde literal here would name a directory
        # this check never looked at, so on a non-default config root the user
        # would add a path that still does not satisfy the test, and following
        # the tip would not silence the tip.
        required = [
            (get_claude_config_dir() / "teams").resolve(),
            (get_claude_config_dir() / "pact-sessions").resolve(),
        ]
        missing = [path for path in required if path not in configured]

        if not missing:
            return None  # All required directories configured

        dirs_list = ", ".join(f"`{d}`" for d in missing)
        return (
            f"PACT tip: Add {dirs_list} to `additionalDirectories` in your "
            f"`{settings_path}` to avoid permission prompts for team and "
            "session file operations."
        )
    except Exception:
        return None  # Fail-open: never block session start


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
        sessions_root = (get_claude_config_dir() / "pact-sessions").resolve()
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
    the resolved project slug if the Session dir line is absent (backward
    compat with sessions that wrote team name but not session dir) or names
    a directory that is no longer there (the line was written before the
    directory moved to the resolved slug).

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
            validated = _validate_under_pact_sessions(expanded)
            # A validated line naming a directory that is gone falls through
            # to the derivation below; a rejected line still returns None.
            if validated is None or Path(validated).is_dir():
                return validated
        else:
            # The primary regex missed even though CLAUDE.md is on disk. This
            # is usually benign (older sessions wrote only the Resume line,
            # not the Session dir line — handled by the fallback just below),
            # but it is also how a silent format regression would present.
            # Log a one-line stderr warning so future drift in the
            # SESSION_START block surfaces during testing instead of silently
            # degrading to the fallback.
            print(
                "session_init: _extract_prev_session_dir regex failed on "
                "existing CLAUDE.md, falling back to Resume-line; file may "
                "have unexpected format",
                file=sys.stderr,
            )

        # Fallback: derive from Resume line session_id + project root basename.
        # Resume line format: "- Resume: `claude --resume <session_id>`"
        resume_match = re.search(
            r'- Resume:\s*`claude --resume\s+([0-9a-f-]+)`', content
        )
        if resume_match:
            session_id = resume_match.group(1)
            # Same slug derivation and sanitisation as every session path,
            # so the fallback lands on the directory the writers used.
            derived = str(
                build_session_path(project_slug(project_dir), session_id)
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

# _is_unknown_or_missing_session — the single canonical session-id validity
# predicate — now lives in shared.pact_context (imported above), where the
# context self-heal gate consumes it alongside this module's persistence and
# CLAUDE.md-write gates. One definition, three call sites: the gates can
# never drift.


def _build_safety_net_context(
    team_name: str | None, frame_role: str | None = None
) -> str:
    """
    Build a minimal governance-delivery additionalContext string for the
    exception safety net in main().

    The returned string MUST start with the role-appropriate
    "YOUR PACT ROLE: <role>." marker at byte 0 (line-anchored). For a lead /
    unknown / unclassified frame (the default) the marker is
    "YOUR PACT ROLE: orchestrator." and the string includes the
    `Skill("PACT:bootstrap")` invocation so the team-lead still loads its
    operating instructions, governance policy, and workflow protocols even
    when main() failed before building the normal team-identification
    string. For a teammate frame (frame_role == "teammate") the marker is
    "YOUR PACT ROLE: teammate." and the body is a minimal TaskList directive —
    a teammate MUST NOT be handed the orchestrator-only bootstrap directive.

    frame_role is captured in main() BEFORE the risky assembly (alongside
    team_name). THE THREE VALUES AND None ARE FOUR CASES. "teammate" gets the
    teammate marker. "lead" and "unknown" BOTH get the orchestrator marker.
    None means the classifier DID NOT RUN, so nothing is known, and it gets a
    role-free failure note.

    WHY "unknown" KEEPS THE MARKER, AND DO NOT SUPPRESS IT AGAIN WITHOUT
    READING THIS. "unknown" means agent_type was ABSENT, and the classifier
    docstring names what that covers: a non-PACT / no-`--agent` PRIMARY frame.
    That is an ordinary user who typed plain `claude`. Withholding the marker
    withholds the bootstrap directive, so the bootstrap marker is never
    stamped, so bootstrap_gate (PreToolUse, no matcher key, every tool call)
    denies Edit, Write and Agent. THE USER READS THAT AS A TOTAL TOOL-LOAD
    FAILURE, AND IT WAS REPORTED FROM THE FIELD.

    A PRIOR SUPPRESSION HERE CITED A CENSUS, AND THE CENSUS DID NOT MEASURE
    THIS POPULATION. It counted subagent transcripts that received the
    orchestrator instructions. POPULATION, re-measured: 166 files matching
    subagents/*.jsonl for one team session, 13 of which carry a SessionStart
    record, holding 70 such records between them. ALL 70 have
    type == "attachment" and carry the LEAD session id, and the set of
    distinct session ids across the 70 has exactly ONE member. They are the
    LEAD's own hook output, which the platform attaches into the transcripts
    of the sidechains that are live at that moment. Those frames classify
    "lead", so a gate keyed on "unknown" cannot change one byte of them, and
    150 of the 166 files carry no SessionStart record at all. NO SUBAGENT
    FRAME REACHES THIS HOOK.

    THE OPERATOR CUE STILL RIDES ALONG. An "unknown" frame also receives
    _UNKNOWN_ROLE_NOTICE, appended AFTER the marker so the byte-0 contract
    above holds. The notice is ADDITIVE and must never replace the ladder: the
    cost of the notice is a few hundred bytes, and the cost of withholding the
    ladder is a user who cannot use any tool.

    THE None PATH KEEPS THE LADDER TOO, AND THAT RULING REPLACED AN EARLIER
    ONE. The earlier ruling withheld the ladder from an unresolved frame on
    the argument that text claiming a role asserts more than the system knows.
    It priced the cost as a REACTIVE route, because the bootstrap_gate deny
    names its own remedy. THE FIELD REPORT RETIRED THAT PRICE: the reporter
    did not recover through the deny text, he rolled the plugin back.

    AND A FULL REVERT IS A SMALLER DELTA FROM A KNOWN-GOOD SHIPPED ARTIFACT
    THAN A PARTIAL ONE. 4.6.34 had no None branch at all. Leaving None
    suppressed keeps one novel behaviour of which the only justification is
    now discredited, and it makes the drift-robustness argument for this
    revert dishonest, because that argument rests on a return to 4.6.34
    semantics.

    WHAT SURVIVES OF THE OLD ARGUMENT IS THE CUE, NOT THE SUPPRESSION. An
    unresolved frame does NOT receive the unknown-role notice, because that
    notice asserts a classifier result that was never computed. It receives
    its own sentence, which keeps it separable from a resolved-empty frame.

    This helper is deliberately zero-risk: only string literals, a single
    f-string interpolation of team_name (which is either None or a validated
    team name from generate_team_name), and a pure equality branch on
    frame_role. No file I/O, no subprocess, no classify call, no imports that
    might fail — and it never raises.

    Args:
        team_name: Team name captured before the exception, or None if the
                   exception fired before generate_team_name() ran.
        frame_role: Session role ("lead" / "teammate" / "unknown") captured
                    before the exception, or None if the exception fired before
                    the capture. Four cases: "teammate" selects the teammate
                    marker, None selects a role-free note that claims no role
                    and carries no bootstrap directive, and every other value
                    ("lead" and "unknown" today) selects the orchestrator
                    marker, with "unknown" also receiving the operator notice.

    Returns:
        Minimal additionalContext string suitable for the except-block
        safety net. Leads with the role-appropriate "YOUR PACT ROLE: <role>."
        marker at byte 0.
    """
    if frame_role == "teammate":
        # Teammate fail-open: byte-0 teammate marker + a minimal directive to
        # find assigned work. Deliberately NO Skill("PACT:bootstrap") (that is
        # the lead-only governance entrypoint) and NO team_name echo (in a
        # teammate frame team_name is the frame's OWN session-derived name, not
        # the lead's team — echoing it would mislead).
        return (
            'YOUR PACT ROLE: teammate.\n\n'
            'session_init partially failed — check systemMessage for details. '
            'Check TaskList for tasks assigned to you.'
        )
    prelude = (
        'YOUR PACT ROLE: orchestrator.\n\n'
        'Invoke Skill("PACT:bootstrap") immediately, without waiting for user input. '
        'Do this before anything else. '
        'Do not evaluate whether it is needed. '
        'You must invoke Skill("PACT:bootstrap") on every session start.'
    )
    # TWO ROLES REACH THIS LADDER BESIDE "lead", AND EACH GETS ITS OWN CUE
    # APPENDED. Appended, never prepended: the byte-0 marker contract in this
    # docstring is what the line-anchored readers key on.
    #
    # "unknown" is a primary frame launched with no `--agent`, and its cue
    # names that fact, because the classifier established it.
    #
    # None means the classifier DID NOT RUN. THE FRAME BEHIND IT IS THE SAME
    # POPULATION AS EVERY OTHER FRAME, WHICH IS MOSTLY A PRIMARY USER, so
    # withholding the ladder there denies an ordinary user their tools. The
    # earlier ruling withheld it and priced the cost as a REACTIVE route,
    # because the bootstrap_gate deny names its own remedy. THE FIELD REPORT
    # IS EVIDENCE THAT ROUTE DOES NOT WORK: the reporter did not recover
    # through the deny text, he rolled the plugin back.
    #
    # THE ONE HALF OF THE OLD ARGUMENT THAT SURVIVES IS THE CUE, NOT THE
    # SUPPRESSION. An unresolved frame must NOT receive the unknown-role
    # notice, because that notice asserts a classifier result that was never
    # computed. It gets its own sentence instead, which keeps it separable
    # from a resolved-empty frame for anyone who debugs the early window.
    if frame_role == "unknown":
        cue = f'\n\n{_UNKNOWN_ROLE_NOTICE}'
    elif frame_role is None:
        cue = (
            '\n\nNote: session_init failed before the session role was '
            'resolved, so this frame was not classified. If you are not '
            'driving PACT as the orchestrator, ignore the instructions above.'
        )
    else:
        cue = ''
    if team_name:
        return (
            f'{prelude}\n\n'
            f'Session team: `{team_name}` (session_init partially failed — '
            f'check systemMessage for details). '
            f'Run TaskList to check current state.{cue}'
        )
    return (
        f'{prelude}\n\n'
        'Session team: NOT GENERATED (session_init failed early — check '
        f'systemMessage for details). The platform auto-creates the session team.{cue}'
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


def _adopt_old_slug_session_dir(session_id: str, project_dir: str) -> bool:
    """Move a session dir keyed under the UNRESOLVED project basename to the
    slug every session path now derives, so a session launched through a
    symlink keeps its journal across the slug change.

    Runs before any writer creates the new-slug dir. Adopts only when the
    two slugs differ, the old path is a real directory (not a symlink), and
    the new path is absent or an empty directory. An existing non-empty
    directory, file or symlink at the new path is left alone, never
    clobbered or merged: the state there is what every reader already
    trusts, and the old dir stays where the old readers can still find it.
    Fail-open: any OSError leaves both paths as they were. Returns True iff
    the directory moved.
    """
    if not session_id or not project_dir:
        return False
    old_slug = Path(project_dir).name
    if not old_slug:
        return False
    old = build_session_path(old_slug, str(session_id))
    new = build_session_path(project_slug(project_dir), str(session_id))
    if old == new:
        return False
    try:
        if old.is_symlink() or not old.is_dir():
            return False
        if new.is_symlink():
            return False
        if new.exists() and not (new.is_dir() and not any(new.iterdir())):
            return False
        new.parent.mkdir(parents=True, exist_ok=True)
        os.rename(old, new)
    except OSError:
        return False
    print(f"session_init: adopted session dir {old} -> {new}", file=sys.stderr)
    return True


# Root-drained artifact prefix: what _archive_stale_compact_summary names
# the moved ROOT-singleton bytes when it drains them into a session dir.
# Distinct from every real archive name the plugin writes, but KEEPING the
# compact-summary stem so globbing consumers (the legacy-drain tests) still
# find the drained artifact as a compact-summary file. The resume pointer's
# strict stamp shape (_ARCHIVE_STAMP_SHAPE_RE) still never matches it — the
# segment after the stem is "root-drained-", not a timestamp — so drained
# bytes can never be named as this session's own compaction output.
_ROOT_DRAINED_SUMMARY_PREFIX = "compact-summary-root-drained-"


def _stale_summary_destination(session_id: str, project_dir: str) -> Path:
    """Where a stale compact summary goes when this hook clears the path.

    Session-scoped when the session can be identified, which is the normal
    case — but under a DISTINCT root-drained prefix, NOT the archive
    convention the session's own compactions produce: the drained bytes are
    ROOT-singleton bytes (degraded or legacy writes with no attributable
    producer), so the resume pointer's strict-shape selector must never name
    them as if this session had compacted. A distinct prefix makes that
    exclusion structural — the selector's shape admits only plugin-written
    archive names, and this name is not one by construction, no deny-list.
    Falls back to a single fixed-name slot in the sessions root, NEVER a
    timestamped one — see COMPACT_SUMMARY_ORPHAN_NAME for the bound and the
    trade it accepts.

    build_session_path is used rather than get_session_dir() because the
    context cache is not built until later in main(); the bootstrap-marker
    block below resolves a session path the same way, in the same window.
    """
    if session_id and project_dir:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        return build_session_path(project_slug(project_dir), str(session_id)) / (
            f"{_ROOT_DRAINED_SUMMARY_PREFIX}{stamp}.txt"
        )
    return get_compact_summary_path().parent / COMPACT_SUMMARY_ORPHAN_NAME


def _archive_stale_compact_summary(session_id: str, project_dir: str) -> None:
    """Clear the ROOT compact-summary singleton BY MOVING IT, never deleting.

    Since #1504 the writer scopes its file to the session that produced it,
    so the root singleton is fed only by DEGRADED writes (unidentifiable
    frames) and pre-upgrade legacy bytes. This sweep is the one-time drain
    for both: a MOVE empties the root path, so nothing can process the bytes
    twice, and only unattributable writes can ever re-feed it.

    The path must end up clear: the summary is single-use, and a copy left
    there is processed a second time by the next briefing in the same session.
    The bytes must survive: the writer leaves no second copy anywhere, so an
    unlink destroys the only copy of anything the secretary did not archive —
    which is precisely the secretary's documented fallback branch, the one
    case where no archive was ever made. A move satisfies both in ONE
    operation, so it cannot half-fail into the state this repairs.

    Fail-open, and the direction is deliberate: on any OSError the bytes stay
    where they are. That risks a summary processed twice, which costs a
    duplicate paragraph. Clearing the path on a failed move would risk losing
    it, which is unrecoverable.
    """
    try:
        summary = get_compact_summary_path()
        if not summary.exists():
            return
        destination = _stale_summary_destination(session_id, project_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        summary.replace(destination)
    except OSError:
        pass  # Fail-open: keep the bytes; never block session init for cleanup


def _settle_staged_summaries(session_id: str, project_dir: str) -> None:
    """Settle the compaction summaries postcompact_archive staged for this session.

    Runs before either clear below, so a lead summary still waiting to be
    promoted is promoted first and then cleared with the rest. Never raises.
    """
    if not (session_id and project_dir):
        return
    try:
        compaction_owner.settle(
            str(build_session_path(project_slug(project_dir), str(session_id)))
        )
    except Exception:
        pass


def _archive_own_dir_stale_summary(session_id: str, project_dir: str) -> None:
    """Clear THIS session's stale compact summary BY MOVING IT in place.

    Session-scoped twin of _archive_stale_compact_summary (#1504): a
    compaction followed by a context reset leaves the file in the session's
    own directory. Moving it to the timestamped archive convention of
    COMPACT_SUMMARY_ARCHIVE_PREFIX in the SAME directory empties the
    single-use slot and keeps the bytes where its own session can still
    find them.

    Composes build_session_path + COMPACT_SUMMARY_NAME DIRECTLY. The resolver
    (pact_context.resolve_compact_summary_path) is deliberately NOT used: its
    degradation leg retargets the root singleton, which the OTHER clear above
    already serves — routing through it would double-drain one object and
    never touch the other.

    Fail-open, same direction as the root clear: on OSError the bytes stay
    put. A summary processed twice costs a duplicate paragraph; a summary
    lost is unrecoverable.
    """
    if not (session_id and project_dir):
        return
    try:
        summary = (
            build_session_path(project_slug(project_dir), str(session_id))
            / COMPACT_SUMMARY_NAME
        )
        if not summary.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        destination = summary.parent / f"{COMPACT_SUMMARY_ARCHIVE_PREFIX}{stamp}.txt"
        summary.replace(destination)
    except OSError:
        pass  # Fail-open: keep the bytes; never block session init for cleanup


# Strict plugin-written archive shape: compact-summary-YYYY-MM-DDTHH-MM-SS.txt.
# Every archive writer produces exactly this (both clears below via
# datetime.now().strftime("%Y-%m-%dT%H-%M-%S"), the secretary's archive step
# via its instructed <YYYY-MM-DDTHH-MM-SS> rename). Selection admits nothing
# else because the ARCHIVE FILENAME is attacker-controlled bytes reaching a
# render sink: the glob's * matches newlines, so a crafted name embedding a
# forged directive after "compact-summary-x" and a newline would carry its
# bytes into the SessionStart instruction channel (F-SEC-1, PR #1527 review).
# Skipping a nonconforming name only costs the clause — fail-safe, no pointer.
_ARCHIVE_STAMP_SHAPE_RE = re.compile(
    r"compact-summary-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.txt"
)

# First-surface gate (F-ARCH-1): session_start sources that CONSUME a
# surfaced summary. A resume/startup/clear start after a candidate's mtime
# means a later start already had the pointer available — re-naming at every
# subsequent start would re-surface consumed state. compact is the PRODUCER
# (a post-summary compact writes a NEWER summary, it does not consume this
# one), and absent/unknown-source old-era events are non-consuming too: the
# costs are asymmetric (a false suppression kills the pointer's primary
# purpose; a false naming costs one sentence), so ambiguity fails toward
# naming.
_FIRST_SURFACE_CONSUMING_SOURCES = frozenset({"resume", "startup", "clear"})


def _latest_consuming_start_ts(session_dir: str) -> float | None:
    """Epoch seconds of the NEWEST consuming session_start in this session's
    journal, or None when there is none (or the journal is missing,
    unreadable, or has no parseable consuming event — every fail-open path
    returns None, which the gate reads as "not suppressed").

    The ts format is the journal's write format (UTC ISO-8601 with a Z
    suffix); unparseable stamps are skipped, never fatal.
    """
    try:
        from shared.session_journal import read_events_from

        latest: float | None = None
        for event in read_events_from(session_dir, event_type="session_start"):
            event_source = event.get("source")
            if (
                not isinstance(event_source, str)
                or event_source not in _FIRST_SURFACE_CONSUMING_SOURCES
            ):
                continue
            raw_ts = str(event.get("ts", ""))
            try:
                parsed = datetime.fromisoformat(
                    raw_ts.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
            if latest is None or parsed > latest:
                latest = parsed
        return latest
    except Exception:
        # Fail-open: the gate is an upgrade path, never a new raise source.
        return None


def _resume_own_summary_clause(session_dir: str) -> str:
    """One sentence naming this session's own compact summary, or "".

    The resume-limb read-side counterpart of the clears above: the summary
    produced by an earlier compaction of THIS session sits in the session's
    own dir, and the non-compact clears above have usually already MOVED the
    live slot to a timestamped archive by the time the source limbs render —
    so name the canonical file when it still exists, else the NEWEST archive
    by mtime. Read-only: a probe, never a move.

    FIRST-SURFACE GATE (F-ARCH-1): a candidate (canonical or archive,
    uniformly) is SUPPRESSED when a consuming session_start event
    (resume/startup/clear — see _FIRST_SURFACE_CONSUMING_SOURCES) has a ts
    STRICTLY after the candidate's mtime: that later start already had the
    pointer available, so naming again would re-surface consumed state at
    every subsequent start. Among archives, the NEWEST UNSUPPRESSED
    shape-conforming one wins. Fail-open toward naming: a missing/unreadable/
    empty journal is no consuming event (today's behavior). The conservative
    edge this accepts: compact then clear then resume names nothing, because
    the clear consumed.

    Both probes require a regular FILE (is_file): a directory named like
    either slot is not a readable summary, and naming it would send the lead
    to a path whose read can only error. Archives are additionally selected
    by the exact stamp shape (_ARCHIVE_STAMP_SHAPE_RE) — hostile names never
    render rather than rendering stripped, and root-drained artifacts
    (_ROOT_DRAINED_SUMMARY_PREFIX) never match the shape by construction.

    Ownership-neutral on purpose ("from an earlier point of this session"):
    the clause carries information, not an attribution claim, and the
    session-scoped path it names embeds this session's id.

    Fail-open like the clears: on OSError the sentence is "" (no clause) —
    a dropped pointer costs a manual rediscovery, a raised one would break
    session init. Empty session_dir (unknown-* sentinel path) also yields ""
    — there is no own dir to name.
    """
    if not session_dir:
        return ""
    try:
        gate_ts = _latest_consuming_start_ts(session_dir)
        base = Path(session_dir)
        canonical = base / COMPACT_SUMMARY_NAME
        if canonical.is_file() and not (
            gate_ts is not None and gate_ts > canonical.stat().st_mtime
        ):
            target = str(canonical)
        else:
            archives = [
                p for p in base.glob(f"{COMPACT_SUMMARY_ARCHIVE_PREFIX}*.txt")
                if p.is_file() and _ARCHIVE_STAMP_SHAPE_RE.fullmatch(p.name)
                and not (
                    gate_ts is not None
                    and gate_ts > p.stat().st_mtime
                )
            ]
            if not archives:
                return ""
            target = str(max(archives, key=lambda p: p.stat().st_mtime))
    except OSError:
        return ""
    return (
        f' A compact summary from an earlier point of this session is '
        f'available at {target}.'
    )


# ─── PACT Runtime Config injection (SessionStart LLM bridge) ────────────────

# Fixed label table for the injected "PACT Runtime Config" block. This is the
# ONLY source of option names + human labels emitted into the block: the
# composer never interpolates a raw env value or a raw dict key (F1
# prompt-injection defense, mirroring the `source` canonicalization in main()
# that keeps untrusted stdin text out of additionalContext). Tuple order is the
# block's display order.
_PACT_RUNTIME_CONFIG_LABELS = (
    ("PACT_PR_GREEDY_FIX", "PR greedy-fix"),
    ("PACT_AUTONOMOUS_SCOPE_DETECTION", "Autonomous scope detection"),
)


def format_pact_runtime_config(options: dict) -> str:
    """Compose the "PACT Runtime Config" additionalContext block.

    Takes the RESOLVED options dict from ``pact_config.llm_options()`` (bool
    values) and emits a multi-line block whose ONLY variable content is a
    per-option "ON"/"OFF" derived from ``options.get(name) is True``. Option
    names and labels come from the fixed ``_PACT_RUNTIME_CONFIG_LABELS`` table,
    NEVER from the dict keys or from any env string — so a poisoned resolver
    value cannot inject text into the block (F1 canonical-value composition,
    the prompt-injection choke-point).

    The block LEADS with a newline so that, after main() joins ``context_parts``
    with ``" | "``, the ``## `` heading still lands at line-start and renders as
    a header rather than mid-line text.

    Consumers (peer-review.md, orchestrate.md / pact-scope-detection.md)
    pattern-match the literal heading; absence of the block == every option at
    its default (OFF).
    """
    lines = ["", "## PACT Runtime Config (resolved at session start)"]
    for env_name, label in _PACT_RUNTIME_CONFIG_LABELS:
        state = "ON" if options.get(env_name) is True else "OFF"
        lines.append(f"- {label}: {state} ({env_name})")
    return "\n".join(lines)


def check_settings_well_formed() -> Optional[str]:
    """Return a warning if the user settings.json exists but is malformed JSON.

    Claude Code silently drops a malformed settings.json WHOLESALE in headless
    mode — taking the entire ``env`` block with it, so every PACT_* option
    persisted there is silently NOT applied. Surfacing the malformation tells
    the user why their env-block config had no effect. Returns None when the
    file is absent or valid. Total: never raises (it runs on the SessionStart
    hot path, where an uncaught exception would break bootstrap).
    """
    try:
        settings_path = get_claude_config_dir() / "settings.json"
        if not settings_path.exists():
            return None
        try:
            json.loads(settings_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return (
                f"PACT: {settings_path} is not valid JSON — Claude Code "
                "silently ignores a malformed settings.json (including its `env` "
                "block), so any PACT_* options set there are NOT applied. Fix the "
                "JSON syntax to restore them."
            )
        return None
    except Exception:  # noqa: BLE001 — total contract on the SessionStart hot path
        return None


def _persist_project_dir_env(project_dir: str) -> None:
    """Append `export CLAUDE_PROJECT_DIR=<value>` to the platform's CLAUDE_ENV_FILE.

    CLAUDE_ENV_FILE is the platform's sanctioned SessionStart channel for
    persisting env vars into subsequent Bash-tool environments; the plugin has
    no other route to make CLAUDE_PROJECT_DIR ambient for skill-spawned CLIs
    (the platform delivers it to hook processes only). Called from main() only
    when BOTH $CLAUDE_ENV_FILE and $CLAUDE_PROJECT_DIR are present — the
    both-present condition is the structural guard that keeps an env-absent
    frame's cwd-fallback value out of the export. The checks below are the
    defensive layer so the helper stays total when called directly in tests.

    Producer-side shlex.quote: the env file is sourced by a shell, so a path
    containing spaces or `$` must arrive quoted. Dedupe matches the TERMINATED
    full logical line against the raw file text (not splitlines() membership):
    a quoted value can itself contain a newline, and only the raw-text match
    self-matches on re-fire. An unterminated foreign last line gets its
    newline written first, so the export starts on its own line instead of
    gluing onto it.

    Never raises: SessionStart hot path (same total contract as the outer
    safety net). A failed append fails open to the pre-fix status quo —
    OSError AND UnicodeError (a non-UTF-8 env file) both fail open locally
    rather than escaping into main()'s outer net and degrading the frame.
    """
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or not os.environ.get("CLAUDE_PROJECT_DIR"):
        return
    if not os.path.isabs(project_dir):
        return
    line = f"export CLAUDE_PROJECT_DIR={shlex.quote(project_dir)}"
    try:
        try:
            existing = Path(env_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = ""
        if f"{line}\n" in existing:
            return
        payload = line + "\n"
        if existing and not existing.endswith("\n"):
            payload = "\n" + payload
        with open(env_file, "a", encoding="utf-8") as fh:
            fh.write(payload)
    except (OSError, UnicodeError):
        pass


def _record_worktree_identity(session_id: str, project_dir: str) -> None:
    """Record which repository this session's linked worktree belongs to.

    Writes `<session_dir>/worktree-identity.json` when `project_dir` lies inside
    a linked worktree (its git dir and common dir differ), including a
    subdirectory of one. The working-memory write guard reads it back once that
    worktree is removed and git can no longer say which repository the declared
    directory was in. Runs for every role, so a separate-process teammate
    records its own session.

    Fail-open: any error leaves no record.
    """
    try:
        directory = Path(project_dir)
        if not directory.is_dir():
            return
        git_dir = _rev_parse_path(directory, "--git-dir")
        common_dir = _rev_parse_path(directory, "--git-common-dir")
        if git_dir is None or common_dir is None or git_dir == common_dir:
            return
        worktree = _rev_parse_path(directory, "--show-toplevel")
        if worktree is None:
            return
        record = {
            "session_id": session_id,
            "declared": os.path.realpath(project_dir),
            "worktree": str(worktree),
            "common_dir": str(common_dir),
        }
        state_file.write_text(
            build_session_path(project_slug(project_dir), session_id)
            / WORKTREE_IDENTITY_FILE,
            json.dumps(record),
            root=get_claude_config_dir() / "pact-sessions",
        )
    except Exception:
        return


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
    5. Generates session-unique PACT team name and writes it to the session context (the platform pre-creates the team)
    5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
    6. Checks for in_progress Tasks (resumption context via Task integration)
    7. Restores last session snapshot for cross-session continuity
    8. Checks for paused or refreshed work from a previous /PACT:pause or /PACT:refresh

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
    # #888: role captured pre-assembly so the except-block safety net can pick
    # a role-appropriate "YOUR PACT ROLE:" marker. Stays None until the early
    # capture just after the stdin/source parse below; a frame that fails BEFORE
    # that capture keeps None, which selects the orchestrator marker — identical
    # to the pre-#888 behavior. That early-failure window is a KNOWN
    # no-regression default (a teammate failing before the capture is mis-marked
    # orchestrator), not a misroute introduced by this change.
    frame_role = None
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

        # Resolve-once: the env value verbatim when the platform delivers it,
        # else the absolute cwd (os.getcwd() is always absolute and physical —
        # matching the platform's own resolved CLAUDE_PROJECT_DIR). This single
        # value feeds BOTH the session-context record and the env-file export,
        # so the exported == recorded invariant holds by construction. The
        # former "." default is gone: env-absent frames now record the absolute
        # cwd.
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        # Env-file export gate: only frames where the platform delivered BOTH
        # the env-file channel and the project dir append — an env-absent
        # frame never reaches the append, so the cwd fallback is never
        # exported (the structural guard).
        if os.environ.get("CLAUDE_ENV_FILE") and os.environ.get("CLAUDE_PROJECT_DIR"):
            _persist_project_dir_env(project_dir)
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

        # Frame role, captured EARLY — right after the stdin/source parse, where
        # input_data is a PROVEN dict (it survived the .get() calls above; a
        # non-dict would have raised at raw_source and bubbled to the outer
        # safety net). classify_session_role does input_data.get(...) so it is
        # total only on a dict — capturing here, NEVER in the except, preserves
        # the safety net's never-raise contract. One capture serves three sites:
        #   - the lead-only advisory gates below (steps 4/4a/4b): a teammate
        #     frame must not receive lead pin advisories (m2);
        #   - the teammate peer-context branch (the `== "teammate"` gate, m3);
        #   - the role-aware exception safety net (_build_safety_net_context).
        # If the exception fires before this point, frame_role stays None and
        # the safety net emits the orchestrator marker — identical to prior
        # behavior, a KNOWN no-regression default, not a misroute.
        frame_role = classify_session_role(input_data)

        # Clear a stale compact-summary — BY MOVING IT, in BOTH of its homes.
        # Only "compact" source keeps either in place (postcompact_archive just
        # wrote it, and this session is about to read it).
        #
        # The ROOT singleton is the degradation + legacy drain; the session's
        # OWN DIR holds the writer's scoped file (#1504). Two move-not-delete
        # objects, no once-flag: the filesystem is the state, and the MOVE is
        # what empties it. This used to unlink. The path still has to be
        # cleared, for the same reason as before, but the previous code cleared
        # it BY DESTROYING the bytes, and those are the only copy. See
        # _archive_stale_compact_summary and _archive_own_dir_stale_summary.
        # Adopt a session dir written under the unresolved project basename
        # BEFORE any writer below can create the resolved-slug dir.
        _adopt_old_slug_session_dir(input_data.get("session_id", ""), project_dir)

        _settle_staged_summaries(input_data.get("session_id", ""), project_dir)

        if source != "compact":
            _archive_stale_compact_summary(
                input_data.get("session_id", ""), project_dir
            )
            _archive_own_dir_stale_summary(
                input_data.get("session_id", ""), project_dir
            )

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
                slug = project_slug(project_dir)
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

        # 0c. Unknown-role startup warning. The lead-only writes in
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
        #
        # This literal is emitted at TWO other sites: the unknown-role limb of
        # the frame-role gate below, and _build_safety_net_context on the
        # exception path. The gating decision of each site, and its cause, are
        # recorded at that site.
        if source in ("startup", "resume") and _should_warn_unknown_role(input_data):
            system_messages.append(_UNKNOWN_ROLE_NOTICE)

        # 1. Refresh the plugin symlinks (enables @~/.claude/protocols/pact-plugin/
        # references, and resolves an unprefixed agent name to the CURRENT root).
        #
        # NO SOURCE GATE ON THE CALL. It ran behind `if not is_context_reset:` on
        # the assumption that a context reset inherits the links of the original
        # session. THAT PREDICATE ANSWERS EXISTENCE AND THE CALLER ASKS CURRENCY:
        # a link can be present and out of date at one moment. An install that
        # landed mid-launch left each link at the prior root until the next
        # launch, so a compact or a clear now repairs them.
        #
        # THE GATE MOVES TO THE NO-CHANGE MESSAGE, where it is the correct
        # predicate. That message answers "did the user see this before", a
        # repetition question, and the gate answers repetition correctly.
        symlink_result = setup_plugin_symlinks()
        if symlink_result and "failed" in symlink_result.lower():
            system_messages.append(symlink_result)
        elif symlink_result == SYMLINKS_VERIFIED_MESSAGE:
            # Nothing moved. Suppress on a context reset, so a quiet compact
            # gains no new output.
            if not is_context_reset:
                context_parts.append(symlink_result)
        elif symlink_result:
            # A LINK MOVED. Report it on each source, with the caveat beside it.
            context_parts.append(f"{symlink_result}. {_SYMLINK_REPOINT_NOTICE}")

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

        # 4. Check for stale pinned context. The informational surfacing is a
        # lead-oriented pin advisory (m2): suppress it for a teammate frame
        # (which has no pin-management authority — pins live in CLAUDE.md, a
        # lead/orchestrator memory surface), but keep the failed/skipped
        # DIAGNOSTICS on system_messages for every frame. The check CALL and its
        # marker side-effect are unchanged — m2 gates advisory SURFACINGS, not
        # writes (#877 owns write-gating).
        staleness_msg = check_pinned_staleness()
        if staleness_msg:
            if "failed" in staleness_msg.lower() or "skipped" in staleness_msg.lower():
                system_messages.append(staleness_msg)
            elif frame_role != "teammate":
                context_parts.append(staleness_msg)

        # 4a. Surface pin slot count (#492). Tier-0 additionalContext —
        # architecturally binding, survives compaction. Fail-open: None
        # when CLAUDE.md cannot be resolved or parsed. m2: lead-only pin
        # telemetry — not surfaced to a teammate frame.
        slot_status_msg = check_pin_slot_status()
        if slot_status_msg and frame_role != "teammate":
            context_parts.append(slot_status_msg)

        # 4b. Emit unconditional stale-block directive when stale pin
        # count meets threshold (#492). Never exit-2 — breaks /clear and
        # /resume per plan key-decisions row 6. m2: the "/PACT:prune-memory"
        # directive is a lead/orchestrator memory action — not surfaced to a
        # teammate frame (the helper's marker side-effect is unchanged).
        stale_block_msg = check_pin_stale_block_directive()
        if stale_block_msg and frame_role != "teammate":
            context_parts.append(stale_block_msg)

        # 4c. Surface plugin manifest diagnostic (#500). Tier-0 additionalContext —
        # total-function banner; always emits, even on read/parse failure.
        # Lets both team-lead and teammate context readers cross-reference
        # worktree edits against the resolved installed-cache root at a
        # glance. Helper is total: no conditional append, no try/except
        # wrapper at the call site.
        context_parts.append(format_plugin_banner())

        # 4d/4e. PACT runtime config: settings-health self-check + inject the
        # resolved LLM-consumed options. Lead-only via the fail-safe
        # `frame_role != "teammate"` idiom (matching the 4a/4b advisory gates):
        # the consumers (peer-review, orchestrate/pact-scope-detection) are all
        # lead/orchestrator flows; an unknown/solo frame still receives it
        # (harmless if unconsumed). Not enumerated as a numbered step in the
        # docstring — like 4a/4b/4c, it is a context surfacing within the pin/
        # config region, so the module<->main() docstring parity is untouched.
        if frame_role != "teammate":
            # 4d. Warn if settings.json is malformed — Claude Code drops it
            # WHOLESALE in headless mode, silently taking the `env` block (and
            # every PACT_* option) with it. User-facing config-health notice →
            # system_messages. Helper is total.
            settings_warn = check_settings_well_formed()
            if settings_warn:
                system_messages.append(settings_warn)
            # 4e. Inject the resolved "PACT Runtime Config" block so markdown
            # flows (which cannot read env vars) can honor the LLM-consumed
            # options. Tier-0 additionalContext, appended (a config statement,
            # not a top-priority alert). Fail-open: any failure emits no
            # directive — and absence of the block == every option at its
            # default (OFF), the same safe state the consumers already assume.
            try:
                context_parts.append(format_pact_runtime_config(llm_options()))
            except Exception:  # noqa: BLE001 — fail-open: no block == all OFF
                pass

        # 5. Remind orchestrator to identify the session-unique PACT team (platform-provisioned)
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

        # Oscillation convergence (#989 detect-and-align). session_init writes
        # team_name to BOTH the context cache (below) and CLAUDE.md (step 5b)
        # on EVERY SessionStart, including compact/clear re-fires. The
        # bootstrap_marker_writer self-heals to the IDENTITY-MATCHED name per
        # prompt. If session_init kept writing the raw COMPUTED name while the
        # marker writer wrote the aligned name, the two would flip-flop forever
        # whenever they differ (the divergent full-UUID launch case). FIX:
        # resolve the aligned name HERE and persist THAT, so both writers
        # converge on one value. The freshly-computed `team_name` is threaded
        # as the resolver's explicit `default` — critical for cold start: at
        # SessionStart the real team dir is ~38s unborn, so the identity match
        # MISSES and the resolver returns the default; on a first-ever cold
        # start the persisted context is empty, so the computed name (not "")
        # must be the default. Gated on a valid session_id (the sentinel path
        # skips all persistence anyway). Once the dir is born, a later
        # SessionStart (or the marker writer) resolves the aligned name and
        # both writers agree → the per-prompt write-back becomes a true no-op.
        if not session_id_was_missing:
            team_name = _resolve_aligned_team_name(session_id, default=team_name)

        if not session_id_was_missing:
            _record_worktree_identity(session_id, project_dir)

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

        # Resolve session_dir early so substitution instructions can include it.
        # get_session_dir() works here because build_context_cache() populated _cache above.
        # Suppress session_dir for the unknown-* sentinel so the literal
        # `.../unknown-xxxx/` path never leaks into the substitution instructions
        # block — otherwise the orchestrator would obediently mkdir that path
        # for any command that uses {session_dir}, bypassing the CLAUDE.md guard
        # below.
        # HOISTED above the session_start journal append below (F-ARCH-1): the
        # resume-limb summary probe must read the journal BEFORE this run's own
        # anchor lands in it — see the probe comment right after the append.
        session_dir = get_session_dir() if not session_id_was_missing else ""

        # Resume-limb summary probe (first-surface gate, F-ARCH-1): computed
        # HERE — after session_dir resolves, BEFORE the session_start journal
        # append below — as a STRUCTURAL INVARIANT: the journal never contains
        # the current run's anchor at probe time. A post-append probe would
        # compare this run's own anchor ts (now) against every candidate mtime
        # (always older) and suppress permanently; the gate would defeat
        # itself on the very first resume. Gated on source == "resume" only:
        # the clause is consumed by exactly that limb, and other sources pay
        # nothing for the probe.
        resume_summary_clause = (
            _resume_own_summary_clause(session_dir)
            if source == "resume"
            else ""
        )

        if not session_id_was_missing:
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

        # Build context message based on source (post-collapse: the team always
        # exists — the platform pre-creates exactly one team per session).
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
        # Single platform-managed directive for every session source. The
        # platform pre-creates exactly one team per session (Claude Code
        # v2.1.178+), so the team always exists by the time the orchestrator
        # acts — the directive's only job is to name the team and block until
        # bootstrap completes. "(provided by the platform for this session)" is
        # correct for both fresh and resumed sessions, so no team-existence
        # discrimination is needed. The bootstrap-blocking sentence is the
        # universal floor: it aligns this guidance with the bootstrap_gate
        # PreToolUse hook, which already mechanically blocks Edit/Write/Agent
        # until the bootstrap marker is stamped regardless of session source.
        _team_directive = (
            f'YOUR PACT ROLE: orchestrator.\n\n'
            f'Invoke Skill("PACT:bootstrap") immediately, without waiting for user input. '
            f'Do this before anything else. '
            f'Do not evaluate whether it is needed. '
            f'You must invoke Skill("PACT:bootstrap") on every session start.\n\n'
            f'Your team is `{team_name}` (provided by the platform for this session). '
            f'Do not read files, explore code, or respond to the user until bootstrap is complete. '
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
        #      (shared/task_utils.py:52-61) that returns None on any
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
        if frame_role == "teammate":  # m3: reuse the role captured at the early seam (was a recompute)
            # O1 fix + Finding-1. team_name above is the #989-aligned name
            # (identity-matched on this teammate's OWN session_id, defaulting to
            # generate_team_name) — for a tmux teammate this is derived from the
            # teammate's OWN session, NOT the lead's team — so resolve the lead's
            # team + this teammate's own member name from the
            # self-registration registry: the teammate wrote {own session_id →
            # name@team} at its first action, so a self-lookup by our OWN
            # session_id recovers both the @team (the lead's team — the datum a
            # teammate cannot otherwise compute) AND the name (for EXACT-name
            # self-exclusion: the full peer list, not the agentType-narrowed one).
            # On a miss (no registration / in-process / any error), fall back to
            # the session-derived team_name + stdin agent_name — no worse than
            # pre-fix; _registry_resolve never raises.
            # Finding-1 (security): the resolver + get_peer_context now read a LIVE
            # config that could be malformed — wrap the whole resolve→build→insert
            # in a fail-open guard so it degrades to NO injection and NEVER lets an
            # exception reach main()'s outer except → _build_safety_net_context
            # (which would mis-role the teammate as orchestrator). Mirrors
            # peer_inject's fail-open-to-no-injection contract.
            try:
                _resolved = _registry_resolve(get_session_id())
                if _resolved and "@" in _resolved:
                    _own, _, _tn = _resolved.partition("@")
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
            # "lead" AND "unknown" BOTH take this branch, and the "unknown"
            # half is the load-bearing part. DO NOT SPLIT IT OUT AGAIN WITHOUT
            # READING THIS. "unknown" means agent_type was ABSENT, which is a
            # non-PACT / no-`--agent` PRIMARY frame: an ordinary user who typed
            # plain `claude`. Withholding the ladder from that frame withholds
            # the bootstrap directive, so the bootstrap marker is never
            # stamped, so bootstrap_gate (PreToolUse, no matcher key, every
            # tool call) denies Edit, Write and Agent. THE USER READS THAT AS A
            # TOTAL TOOL-LOAD FAILURE, AND IT WAS REPORTED FROM THE FIELD. The
            # `clear` source is the worst cell: `is_marker_reset` above ERASES
            # the marker on that path, so a `clear` frame with no ladder loses
            # the marker AND the instruction that would rebuild it.
            #
            # A PRIOR SUPPRESSION HERE CITED A CENSUS THAT DID NOT MEASURE
            # THIS POPULATION. Re-measured: of 166 files matching
            # subagents/*.jsonl for one team session, 13 carry a SessionStart
            # record, holding 70 records between them. All 70 have
            # type == "attachment" and carry the LEAD session id, and the set
            # of distinct session ids across the 70 has exactly ONE member.
            # They are the LEAD's own hook output, attached by the platform
            # into the transcripts of the live sidechains. Those frames
            # classify "lead", so a gate keyed on "unknown" cannot change one
            # byte of them, and 150 of the 166 files carry no SessionStart
            # record at all. NO SUBAGENT FRAME REACHES THIS HOOK.
            #
            # AND THE ERROR THAT LET IT SHIP WAS A MODEL, NOT A MISSING CHECK,
            # WHICH IS WHY REVIEW DID NOT CATCH IT. The comment above this
            # module's _UNKNOWN_ROLE_NOTICE describes the no-`--agent` frame as
            # an operator "who MEANT to launch the orchestrator and forgot the
            # flag", and the emitted notice says the same. THAT NAMES THE FRAME
            # AFTER A MISTAKE. Nobody wrote down that it is also the ORDINARY
            # way a user starts Claude Code, so a branch that withheld the
            # ladder from it read as harmless to each reviewer, because each
            # reviewer held the same model. A missing check is caught by
            # reading the branch. A wrong model is not.
            #
            # The team always exists (the platform pre-creates it), so the
            # directive is source-agnostic; the per-source branches differ only
            # in the recovery/state guidance appended after it.
            if source == "compact":
                # Post-compaction: bootstrap directive subsumes "recover state"
                # guidance; keep concrete task-resumption bullets for the
                # orchestrator's next actions after bootstrap.
                # Refresh-awareness (presentation-only): an unspent
                # session_refreshed event means /PACT:refresh stopped the
                # teammates before this compact — a SendMessage to a stopped
                # teammate's name silently RESUMES its stale pre-refresh
                # transcript, so the "Re-engage secretary" instruction must
                # be suppressed until bootstrap respawns it. The FULL refresh
                # prompt comes only from check_resume_state at step 8; this
                # signal adjusts wording. When False, every byte below is
                # identical to the non-refresh-aware text (fail-safe default
                # — a non-lead or errored read keeps today's wording).
                refresh_pending = frame_is_lead and has_unspent_refresh(session_dir)
                if refresh_pending:
                    _secretary_clause = (
                        "Teammates were shut down by /PACT:refresh. Do NOT "
                        "message any pre-refresh teammate name (a send "
                        "RESUMES its stale transcript). Run /PACT:bootstrap "
                        "to respawn the secretary first."
                    )
                else:
                    _secretary_clause = (
                        "Re-engage secretary: SendMessage(to='secretary', "
                        "message='Post-compaction: deliver session briefing with current state.')."
                    )
                # The compact-summary path is single-use: the secretary ARCHIVES
                # it after processing, so a lead that reads even slightly later
                # finds nothing there. Naming only the canonical path sends the
                # lead to a location it is expected to vacate. Name the archive
                # too, so the read survives the move without waiting for the
                # briefing to arrive.
                #
                # The READ TARGET tracks the writer (#1504): session_dir present
                # -> the writer scoped the file to this session, so name the
                # session-scoped path. session_dir absent -> the writer DEGRADED
                # to the root singleton, so name the root path: the degenerate
                # branch must follow the degraded write, not the happy path.
                #
                # session_dir is "" when session_id was missing (the ternary at
                # the top of this function). Interpolating it blind would emit an
                # instruction naming an empty directory — no error, just a
                # confidently wrong sentence — so fall back to the briefing.
                if session_dir:
                    _summary_path = Path(session_dir) / COMPACT_SUMMARY_NAME
                    _archive_clause = (
                        f'(if it is gone, the secretary archived it into '
                        f'{session_dir} as compact-summary-<timestamp>.txt)'
                    )
                else:
                    _summary_path = get_compact_summary_path()
                    _archive_clause = (
                        '(if it is gone, the secretary archived it into the '
                        'session directory and names the path in its briefing)'
                    )
                context_parts.insert(0, (
                    f'{_team_directive} '
                    f'After bootstrap, recover session state: '
                    f'(1) Read {_summary_path} for prior context '
                    f'{_archive_clause}, '
                    f'(2) Run TaskList to find in-progress work, '
                    f'(3) read the task files of in-progress tasks for details (TaskGet does not surface metadata). '
                    f'{_secretary_clause}'
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
                        if refresh_pending:
                            # Post-refresh re-label: the compaction was the
                            # user's manual /compact (indistinguishable from
                            # auto-compact downstream — neutral wording), and
                            # the "active" agents are STOPPED pre-refresh
                            # names whose task entries survive. Names still
                            # come from live task data; only labels change.
                            _checkpoint_block = _checkpoint_block.replace(
                                "Prior conversation auto-compacted.",
                                "Context was compacted.",
                                1,
                            ).replace(
                                "Active Agents",
                                "Pre-refresh agents (STOPPED — respawn "
                                "before messaging)",
                                1,
                            )
                        context_parts.append(_checkpoint_block)
            elif source == "clear":
                # Context cleared via /clear: no compact-summary, but team and tasks survive
                context_parts.insert(0, (
                    f'{_team_directive} '
                    f'CONTEXT CLEARED: Your context was cleared via /clear. '
                    f'State recovery: '
                    f'(1) TaskList for current tasks, '
                    f'(2) read the task files of in-progress tasks (TaskGet does not surface metadata). '
                    f"Re-engage secretary: SendMessage(to='secretary', "
                    f"message='Context cleared: deliver fresh briefing with current project state.')."
                ))
            elif source == "resume":
                # Normal resume: model retains context, team exists. A compact
                # summary from an earlier compaction of this session may sit
                # in the session's own dir — usually archived-in-place, since
                # the non-compact clears above run BEFORE this limb renders.
                # The pointer sentence is PRECOMPUTED above (probe hoisted
                # before the session_start journal append — the F-ARCH-1
                # first-surface invariant) and is PROBE-CONDITIONAL (empty
                # string when no unsuppressed summary exists), so the limb
                # stays one contiguous block.
                context_parts.insert(0, (
                    f'{_team_directive} '
                    f'Check session journal for paused state from /PACT:pause.'
                    f'{resume_summary_clause}'
                ))
            elif source == "startup":
                # Fresh session: bare directive (no extra recovery guidance)
                context_parts.insert(0, _team_directive)
            else:
                # Unrecognized lifecycle source value. The team still exists
                # (platform pre-creates it), so there is no "no team" case to
                # handle — only an observability concern: surface the malformed
                # `source` in additionalContext AND on stderr so debug logs
                # capture the unexpected-stdin signal.
                print(
                    f"session_init: unknown source value: {source!r}",
                    file=sys.stderr,
                )
                context_parts.insert(0, (
                    f'{_team_directive} '
                    f'Note: unrecognized session source "{source}". '
                    f'Run TaskList to check current state.'
                ))

            # THE OPERATOR CUE, AND IT IS ADDITIVE. A frame with no agent_type
            # keeps the whole ladder above and ALSO gets told that no role was
            # recognized, so an operator who MEANT to launch the orchestrator
            # and forgot the flag still sees it. INDEX 1, immediately after the
            # ladder each source limb just wrote at index 0: the role message
            # must stay first, because this gate runs LATE and the banner and
            # the pin surfacings are already in context_parts. An append would
            # leave the cue below diagnostics a reader meets first, and an
            # insert at 0 would displace the marker the line-anchored readers
            # key on. NO SOURCE GATE: this limb writes to context_parts, which
            # a frame after a compact does not carry over from before, so a
            # source gate would remove the only copy that reader gets. The
            # sibling emission into system_messages does gate on source,
            # because that one answers a REPETITION question about a reader
            # that remembers.
            if frame_role == "unknown":
                context_parts.insert(1, _UNKNOWN_ROLE_NOTICE)

        # 5a. Capture the PREVIOUS session's dir from project CLAUDE.md
        # before step 5b overwrites the Current Session block with THIS
        # session's info. READ-BEFORE-WRITE invariant: _extract_prev_session_dir
        # must run before update_session_info, otherwise it reads back the
        # just-written current session dir and silently breaks cross-session
        # resume (step 7) and paused/refreshed-work detection (step 8).
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

        # 8. Check for paused or refreshed work from a previous /PACT:pause
        # or /PACT:refresh. ONE unified resolver (check_resume_state) reads
        # both event types and arbitrates newest-wins — never two parallel
        # reader paths at this seam. Lead-only (#877): mechanically this is
        # a READ (it surfaces a resume prompt, not a write), but surfacing
        # the lead's resume claim is a lead-only operation — a teammate/plain
        # frame must not receive the lead's resume prompt. Gate the check
        # itself so a non-lead frame does no journal read either.
        if frame_is_lead:
            resume_msg = check_resume_state(prev_session_dir=prev_session_dir)
            if resume_msg:
                context_parts.append(resume_msg)
                # Record that a resumption claim SURFACED, in THIS session's
                # journal. The secretary reads the event's presence at spawn
                # and skips its Working Memory rebuild for that session.
                #
                # WHAT THE MARKER MEANS IS EXACTLY THAT: A CLAIM SURFACED. It
                # does NOT mean an arc is still running, and wording it that
                # way would be false on branches this very call reaches. A
                # paused claim whose PR is MERGED or CLOSED surfaces — after
                # the `gh` probe inside the interpreter has just established
                # the arc is over — and so does one past its 14-day staleness
                # cutoff. Both are reachable in ordinary operation. (A
                # fieldless refreshed claim surfaces too, because that
                # interpreter is total by signature, but no writer in this
                # tree can produce one: the type's registration requires
                # fields, so the write path refuses it and only a hand-edited
                # or corrupt journal reaches the read path, which does not
                # re-validate. That is the read-path totality this module
                # wants, not a defect.)
                #
                # THE OVER-FIRING IS KEPT DELIBERATELY, ON FAIL DIRECTION. A
                # marker written when the arc is over costs one session of a
                # block that lags the store. A marker NOT written when the arc
                # is live costs the contamination this whole mechanism exists
                # to prevent. The asymmetry decides it, and it decides it the
                # same way whichever branch surfaced.
                #
                # WHAT THE SKIP GUARANTEES, AND WHAT IT DEPENDS ON. It
                # guarantees the block is not rewritten AT THIS BOUNDARY, so
                # the post-resumption cohort reads what the boundary left. It
                # does NOT by itself guarantee that what the boundary left is
                # what the arc's earlier agents read — that holds only while
                # every dispatch reachable before the boundary stays quiet. A
                # site that propagates earlier in the arc contaminates the
                # block before any freeze begins, and freezing then preserves
                # the contamination rather than preventing it. The quiet
                # default is what makes the guarantee whole; this write only
                # holds the boundary.
                #
                # KEYED ON THE VERDICT, NOT ON THE RAW CLAIM EVENTS. Writing
                # this from `session_refreshed` / `session_paused` directly
                # looks equivalent and is smaller, and it silently drops a
                # writer: `check_resume_state` already unifies every producer
                # of a pause-shaped claim, wrap-up's open-PR branch included.
                #
                # NO FIELDS — PRESENCE IS THE WHOLE SIGNAL. The consumer checks
                # only that the event exists. An earlier shape carried the
                # winning claim's type and timestamp; neither had a reader, and
                # `check_resume_state` returns a composed prompt that discards
                # which claim won, so supplying them would have meant either
                # splitting that resolver or re-applying its newest-wins rule
                # here — a second copy of an ordering rule with nothing
                # comparing the copies. The claim events stay in the journal
                # for anyone debugging, so nothing is lost.
                #
                # THE FAIL DIRECTION IS THE UNSAFE ONE AND CANNOT BE MADE SAFE.
                # No marker means the secretary rebuilds, which hands this
                # arc's own conclusions to the agents whose value is reaching
                # conclusions independently. The only safe alternative —
                # refusing to start the session — is worse than the defect. So
                # it is made LOUD instead: never raises, never blocks session
                # start, and says so when the write does not land. A hole that
                # announces itself is recoverable; a silent one is the defect.
                #
                # WHAT LOUD COVERS, AND WHAT IT DOES NOT. It covers a failed
                # marker WRITE, which is the only failure reachable from here.
                # It does not cover a claim that never SURFACES, and that path
                # reaches the identical end state — no marker, a rebuild, and
                # nothing on either channel. `check_resume_state` returns None
                # on its first line when `prev_session_dir` is falsy, so a
                # missing CLAUDE.md, an absent Session dir line whose fallback
                # also fails, or a path rejected by the under-pact-sessions
                # validation all end here silently, having never entered this
                # branch at all. Whether a session with no legitimate
                # predecessor SHOULD announce anything is a noise-versus-signal
                # question — most such sessions are simply the first in a
                # project — and it is deliberately not answered here. What is
                # answered is the narrower claim: this guard is loud about the
                # write, not about the absence.
                # TWO CHANNELS, TWO ACTORS, AND THE SECOND IS NOT A COPY OF
                # THE FIRST. systemMessage reaches the human, who can diagnose
                # this and little else; additionalContext reaches the lead,
                # which is the only actor that can PREVENT the consequence, by
                # carrying the skip to the secretary by hand. So the first is a
                # failure report in the channel every other failure in this
                # hook uses, and the second is a DIRECTIVE in the channel that
                # carries directives. Dropping either silently drops one actor.
                #
                # THE TWO CHANNELS ARE INDEPENDENT AT THIS SITE AND JOINT AT
                # THE EMISSION SITE, which is where the guarantee actually
                # rests. Here both appends sit inside this one guard with no
                # return or raise between them, and neither call can fail:
                # `append_event` is total and `make_event` with no fields
                # cannot raise. But neither list is emitted here. Both render
                # near the end of `main()` — `additionalContext` from
                # `context_parts`, `systemMessage` from `system_messages` —
                # and the cross-session backlog block runs in between. Any
                # exception raised there reaches `main()`'s outer handler,
                # which builds a fresh output from `_build_safety_net_context`
                # and reads NEITHER list. So one raise between this append and
                # that render drops the human's report and the lead's
                # directive together, and the two-actor property becomes one
                # of totality in the code that sits between, not of anything
                # visible from here. No live defect: the intervening calls are
                # total today. But the invariant lives in another file with
                # nothing binding it to this claim, so a future raise between
                # these two points silences both actors at once.
                if not append_event(make_event("session_resumption_surfaced")):
                    system_messages.append(
                        "Resumption marker not recorded: the "
                        "session_resumption_surfaced journal write failed. "
                        "The secretary will rebuild the Working Memory block "
                        "at spawn, so agents spawned into this session may "
                        "read conclusions reached during the arc they are "
                        "resuming."
                    )
                    context_parts.append(
                        "RESUMPTION MARKER MISSING: this session resumes an "
                        "interrupted workstream, but the marker the secretary "
                        "reads at spawn was not recorded. Tell the secretary "
                        "NOT to rebuild the Working Memory block, in its spawn "
                        "dispatch. Without that the block is rebuilt from the "
                        "store, and agents spawned to judge this arc read the "
                        "arc's own conclusions."
                    )

        # Cross-session backlog. Deliberately OUTSIDE the frame_is_lead block:
        # every frame gets the block, and the INDENTATION IS THE WHOLE GATE —
        # one level in would scope it to lead frames that also carry a resume
        # prompt, which reads as correct at the call site and silently emits
        # nothing for everyone else. Column 8, level with `# Build output`.
        #
        # session_block is TOTAL and is the outermost call: it converts every
        # failure into a return value, so nothing here can raise. That is
        # correctness, not style — an exception at this point reaches the
        # handler at the bottom of main(), which discards context_parts
        # wholesale and replaces the entire session-start context with the
        # safety net, taking the plugin banner and the pin surfacings with it.
        # If anything ever escapes, fix the boundary in backlog_store; do NOT
        # add a try/except here.
        #
        # NO CLASSIFICATION HAPPENS HERE. BacklogNotice carries two independent
        # fields, so routing is decided by WHICH field holds the text, never by
        # inspecting the text itself. Do not reintroduce the "failed"/"skipped"
        # substring matcher the sibling status messages use — rewording a
        # message would silently reroute it.
        #
        # Channel asymmetry mirrors _UNKNOWN_ROLE_NOTICE: additionalContext is
        # ungated (a post-compact frame does not carry the earlier context
        # over, so a source gate would remove the only copy that reader gets),
        # while systemMessage answers a repetition question for a reader that
        # remembers, and is gated on the launch sources.
        # THE TRIGGER AND THE TIMESTAMP ARE TWO INDEPENDENT FACTS, and the age
        # line needs both. `is_context_reset` is the trigger; the anchor is only
        # the comparison operand, and it is passed ONLY on a reset.
        # Gating on the anchor's null-ness instead would be silently wrong in
        # both directions: `compact` is absent from
        # _FIRST_SURFACE_CONSUMING_SOURCES, so a compact-only journal yields
        # None and the line would never fire on the PRIMARY re-injection
        # trigger, while every `resume` yields a non-None value and would fire
        # the line where there has been no re-injection at all.
        # A compact-only journal therefore stays silent. That is the narrow
        # accepted case, NOT a defect: _age_line refuses to fabricate a
        # left-hand side, and it self-heals at the next startup/resume/clear.
        notice = backlog_store.session_block(
            project_dir,
            context_anchor=(
                _latest_consuming_start_ts(session_dir) if is_context_reset else None
            ),
        )
        if notice.context:
            context_parts.append(notice.context)
        if notice.alert and source in ("startup", "resume"):
            system_messages.append(notice.alert)

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
        # team-identification instruction is always insert(0, ...)'d
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
        safety_net_context = _build_safety_net_context(team_name, frame_role)
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
