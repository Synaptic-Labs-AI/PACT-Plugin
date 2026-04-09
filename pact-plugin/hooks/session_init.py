#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_init.py
Summary: SessionStart hook that initializes PACT environment.
Used by: Claude Code settings.json SessionStart hook

Performs:
0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
1. Creates plugin symlinks for @reference resolution
2. Updates ~/.claude/CLAUDE.md (merges/installs slim PACT kernel)
2b. Writes full orchestrator instructions to sidecar file for lead to Read (teammates skip)
3. Ensures project CLAUDE.md exists with memory sections
4. Checks for stale pinned context (delegated to staleness.py)
5. Generates session-unique PACT team name and reminds orchestrator to create it
5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
6. Checks for in_progress Tasks (resumption context via Task integration)
7. Restores last session snapshot
8. Checks for paused work from previous /PACT:pause invocation

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
from typing import Any

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# Import shared Task utilities (DRY - used by multiple hooks)
from shared.task_utils import get_task_list

# Import staleness detection (extracted to staleness.py for maintainability).
# Underscore aliases (_get_project_claude_md_path, _estimate_tokens) and the
# uppercase constants are re-exported here so test_staleness.py can keep
# importing them via `from session_init import ...`. Removing these would
# break the staleness test suite, even though pyright flags them as unused
# inside session_init itself — they form the module's public interface.
from staleness import (  # noqa: F401
    check_pinned_staleness as _staleness_check,
    PINNED_STALENESS_DAYS,
    PINNED_CONTEXT_TOKEN_BUDGET,
    _get_project_claude_md_path,
    _estimate_tokens,
)

from shared.constants import COMPACT_SUMMARY_PATH
from shared.error_output import hook_error_json
from shared.pact_context import get_session_dir, write_context
from shared.session_journal import append_event, make_event

# Import extracted modules (decomposed for maintainability per M5 audit finding).
from shared.symlinks import setup_plugin_symlinks
from shared.claude_md_manager import (
    update_claude_md,
    ensure_project_memory_md,
    resolve_project_claude_md_path,
)
from shared.session_resume import (
    update_session_info,
    restore_last_session,
    check_resumption_context,
    check_paused_state,
)

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Fallback sidecar path when session_dir is unavailable (e.g., malformed stdin)
_ORCHESTRATOR_SIDECAR_FALLBACK = Path.home() / ".claude" / "pact-orchestrator.md"


def _read_full_orchestrator_source() -> str | None:
    """Read the full PACT orchestrator instructions from plugin source.

    Returns the content of pact-plugin/CLAUDE.md (the full orchestrator
    instructions, unchanged from before the kernel split), or None if the
    file is missing or unreadable.
    """
    plugin_root_str = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root_str:
        return None
    source = Path(plugin_root_str) / "CLAUDE.md"
    if not source.exists():
        return None
    try:
        return source.read_text(encoding="utf-8")
    except (IOError, OSError):
        return None


def _write_orchestrator_sidecar() -> tuple[bool, str]:
    """Write full orchestrator instructions to sidecar file for lead to Read.

    Writes to the session directory ({session_dir}/pact-orchestrator.md) to
    avoid race conditions on concurrent starts. Falls back to the shared
    path (~/.claude/pact-orchestrator.md) if session_dir is unavailable.

    The lead reads this file via the Read tool after the hook returns a
    pointer in additionalContext.

    Returns (success, actual_path) tuple — success is True if write succeeded,
    actual_path is the path that was written (or attempted) for use in pointer
    messages.
    """
    content = _read_full_orchestrator_source()
    if not content:
        return False, ""

    # Prefer session-scoped path; fall back to shared path
    session_dir = get_session_dir()
    if session_dir:
        sidecar_path = Path(session_dir) / "pact-orchestrator.md"
    else:
        sidecar_path = _ORCHESTRATOR_SIDECAR_FALLBACK

    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(content, encoding="utf-8")
        os.chmod(str(sidecar_path), 0o600)
        return True, str(sidecar_path)
    except (IOError, OSError) as e:
        print(f"session_init: failed to write orchestrator sidecar: {e}",
              file=sys.stderr)
        return False, str(sidecar_path)


def check_pinned_staleness():
    """
    Thin wrapper around staleness.check_pinned_staleness().

    Resolves the CLAUDE.md path via the module-level _get_project_claude_md_path
    (which tests can patch on session_init) and passes it to the core function.
    """
    path = _get_project_claude_md_path()
    return _staleness_check(claude_md_path=path)


def check_additional_directories() -> str | None:
    """
    Check if ~/.claude/teams is in additionalDirectories in settings.json.

    Returns a tip message if the setting is missing, or None if already present.
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

        # Normalize the target path for comparison
        target = Path.home() / ".claude" / "teams"

        for entry in additional_dirs:
            if not isinstance(entry, str):
                continue
            # Expand ~ using Path.home() (not expanduser which bypasses monkeypatch)
            if entry.startswith("~/"):
                expanded = (Path.home() / entry[2:]).resolve()
            else:
                expanded = Path(entry).resolve()
            if expanded == target.resolve():
                return None  # Already configured

        return (
            "PACT tip: Add `~/.claude/teams` to `additionalDirectories` in your "
            "~/.claude/settings.json to avoid permission prompts for team file "
            "operations."
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
    raw_id = input_data.get("session_id")
    session_id = str(raw_id) if raw_id else ""
    if session_id:
        suffix = session_id[:8]
    else:
        suffix = secrets.token_hex(4)
    return f"pact-{suffix}"


def _extract_prev_session_dir(project_dir: str) -> str | None:
    """
    Extract the previous session's directory path from the project CLAUDE.md.

    Reads the "## Current Session" block written by update_session_info()
    and extracts the session dir from lines like
    "- Session dir: `~/.claude/pact-sessions/PACT-prompt/abc12345-...`".

    Honors both supported project CLAUDE.md locations
    ($project_dir/.claude/CLAUDE.md preferred, $project_dir/CLAUDE.md legacy).

    Falls back to deriving the path from the Resume line's session_id +
    project root basename if the Session dir line is absent (backward compat
    with sessions that wrote team name but not session dir).

    This is used to locate the previous session's journal for resume context
    and pause state detection. Returns None if neither CLAUDE.md exists or
    the session dir can't be extracted.

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

        content = claude_md.read_text(encoding="utf-8")

        # Primary: match "- Session dir: `<path>`" in the Current Session block.
        match = re.search(r'- Session dir:\s*`([^`]+)`', content)
        if match:
            raw = match.group(1)
            # Expand ~ to actual home directory
            if raw.startswith("~/"):
                return str(Path.home() / raw[2:])
            return raw

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
            return str(
                Path.home() / ".claude" / "pact-sessions" / slug / session_id
            )

    except (IOError, OSError):
        pass
    return None


def _is_unknown_or_missing_session(raw_id: object) -> bool:
    """Return True if the session_id is missing, blank, or already a sentinel.

    Single canonical predicate for the malformed-stdin gate. Both the
    persistence call sites at the top of main() (write_context + append_event)
    and the CLAUDE.md write at step 5b consult this helper so the two gates
    can never drift. Drift previously allowed two corruption classes:

    * Whitespace-only ids (e.g. `"   "`) were truthy and bypassed
      `not raw_id`, leaking through to write_context as a literal directory
      name.
    * An attacker-supplied `"unknown-foo"` value passed `not raw_id` because
      the string is non-empty, then later passed `startswith("unknown")`
      and was written into CLAUDE.md anyway via a different code path.

    The unified helper rejects all of: None, non-strings, empty strings,
    whitespace-only strings, and any string already shaped like the
    `unknown-*` sentinel.
    """
    if not raw_id:
        return True
    if not isinstance(raw_id, str):
        return True
    stripped = raw_id.strip()
    if not stripped:
        return True
    return stripped.startswith("unknown")


def main():
    """
    Main entry point for the SessionStart hook.

    Performs PACT environment initialization:
    0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
    1. Creates plugin symlinks for @reference resolution
    2. Updates ~/.claude/CLAUDE.md (merges/installs slim PACT kernel)
    2b. Writes full orchestrator instructions to sidecar for lead to Read (teammates skip)
    3. Ensures project CLAUDE.md exists with memory sections
    4. Checks for stale pinned context entries in project CLAUDE.md
    5. Generates session-unique PACT team name and reminds orchestrator to create it
    6. Checks for in_progress Tasks (resumption context via Task integration)
    7. Restores last session snapshot for cross-session continuity
    8. Checks for paused work from previous session's /PACT:pause

    Memory initialization (dependencies, migrations, embedding catch-up) is
    now lazy-loaded on first memory operation to reduce startup cost for
    non-memory users.
    """
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        context_parts = []
        system_messages = []

        # Lead vs teammate detection: SessionStart fires for teammates with
        # agent_id present in stdin. Lead sessions have no agent_id.
        agent_id = input_data.get("agent_id")
        is_teammate = agent_id is not None

        # Detect session source: startup, resume, compact, clear
        # Default to "startup" if missing (backwards compat with older Claude Code)
        source = input_data.get("source", "startup")
        is_context_reset = source in ("compact", "clear")

        # Clean up stale compact-summary from previous sessions.
        # Only "compact" source needs it (just written by postcompact_verify).
        if source != "compact":
            try:
                COMPACT_SUMMARY_PATH.unlink(missing_ok=True)
            except OSError:
                pass  # Fail-open: don't block session init for cleanup

        # 0. Check if ~/.claude/teams is in additionalDirectories (one-time tip)
        # Only check on fresh startup — resumed/compacted sessions already had the check
        if not is_context_reset:
            teams_tip = check_additional_directories()
            if teams_tip:
                system_messages.append(teams_tip)

        # 1. Set up plugin symlinks (enables @~/.claude/protocols/pact-plugin/ references)
        # Context resets (compact/clear): symlinks are already set up from original session
        if not is_context_reset:
            symlink_result = setup_plugin_symlinks()
            if symlink_result and "failed" in symlink_result.lower():
                system_messages.append(symlink_result)
            elif symlink_result:
                context_parts.append(symlink_result)

        # 2. Updates ~/.claude/CLAUDE.md (merges/installs slim PACT kernel)
        # Context resets (compact/clear): CLAUDE.md is already installed from original session
        if not is_context_reset:
            claude_md_msg = update_claude_md()
            if claude_md_msg:
                if "failed" in claude_md_msg.lower() or "unmanaged" in claude_md_msg.lower():
                    system_messages.append(claude_md_msg)
                else:
                    context_parts.append(claude_md_msg)

        # 2b. Write full orchestrator instructions for lead to Read (teammates skip)
        if not is_teammate and not is_context_reset:
            sidecar_ok, sidecar_path = _write_orchestrator_sidecar()
            if sidecar_ok:
                context_parts.insert(0,
                    f'PACT orchestrator instructions written to {sidecar_path}. '
                    'Read this file NOW to load your full operating instructions. '
                    'Do not proceed until you have read it.'
                )

        # 3. Ensure project has CLAUDE.md with memory sections
        project_md_msg = ensure_project_memory_md()
        if project_md_msg:
            if "failed" in project_md_msg.lower():
                system_messages.append(project_md_msg)
            else:
                context_parts.append(project_md_msg)

        # 4. Check for stale pinned context
        staleness_msg = check_pinned_staleness()
        if staleness_msg:
            if "failed" in staleness_msg.lower():
                system_messages.append(staleness_msg)
            else:
                context_parts.append(staleness_msg)

        # 5. Remind orchestrator to create session-unique PACT team (or reuse on resume)
        team_name = generate_team_name(input_data)

        # 5a. Write session context file FIRST so get_session_dir() works for
        # subsequent journal writes. write_context() populates the _cache
        # immediately, enabling append_event() to derive the journal path.
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
        # indefinitely. Gate BOTH persistence call sites (write_context and
        # append_event) on session_id_was_missing to prevent the leak. The
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
            print(
                f"session_init: missing session_id in stdin payload; "
                f"using fallback {session_id} (no disk persistence)",
                file=sys.stderr,
            )
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        if not session_id_was_missing:
            try:
                write_context(team_name, session_id, project_dir, plugin_root)
            except Exception as e:
                # Fail-open: context file is best-effort; hooks fall back to empty strings
                print(f"session_init: could not write context file: {e}", file=sys.stderr)

            # Write session_start event to journal (after write_context so path is available).
            append_event(
                make_event(
                    "session_start",
                    team=team_name,
                    session_id=session_id,
                    project_dir=project_dir,
                    worktree="",  # Not yet created at this point
                ),
            )

        try:
            team_config = Path.home() / ".claude" / "teams" / team_name / "config.json"
            team_exists = team_config.exists()
        except OSError:
            # Fail-open: if filesystem check fails, assume fresh session
            team_exists = False

        # Resolve session_dir early so substitution instructions can include it.
        # get_session_dir() works here because write_context() populated _cache above.
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
            f'Your team is `{team_name}` (existing — resumed session). '
            f'Do not call TeamCreate — the team already exists. '
            f'{_substitutions}'
        )
        _team_create = (
            f'Your FIRST action must be: TeamCreate(team_name="{team_name}"). '
            f'Do not read files, explore code, or respond to the user until the team is created. '
            f'{_substitutions}'
        )

        # 2b-compact: Re-deliver orchestrator sidecar pointer on context reset (lead only).
        # The sidecar file persists on disk from initial startup; re-write it in case the
        # plugin was updated mid-session, then re-deliver the pointer.
        if is_context_reset and not is_teammate:
            compact_ok, compact_sidecar_path = _write_orchestrator_sidecar()
            if compact_ok:
                context_parts.insert(0,
                    f'POST-COMPACTION: Read {compact_sidecar_path} to reload '
                    'your full PACT orchestrator instructions.'
                )

        if source == "compact" and team_exists:
            # Post-compaction: context window was compacted, guide state recovery
            context_parts.insert(0, (
                f'{_team_reuse} '
                f'POST-COMPACTION: Your context was compacted — recover state: '
                f'(1) Read {COMPACT_SUMMARY_PATH} for prior context, '
                f'(2) Run TaskList to find in-progress work, '
                f'(3) TaskGet on in-progress tasks for details. '
                f"Re-engage secretary: SendMessage(to='secretary', "
                f"message='Post-compaction: deliver session briefing with current state.')."
            ))
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
            # Anomalous: context reset but no team (e.g., compact/clear + no team)
            # or unknown source without team — create team with warning
            context_parts.insert(0, (
                f'{_team_create} '
                f'WARNING: Session source "{source}" but team not found — '
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
        if not _is_unknown_or_missing_session(session_id):
            session_msg = update_session_info(session_id, team_name, session_dir, plugin_root)
            if session_msg:
                if "failed" in session_msg.lower():
                    system_messages.append(session_msg)
                else:
                    context_parts.append(session_msg)

        # 6. Check for in_progress Tasks (resumption context via Task integration)
        tasks = get_task_list()
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

        # 8. Check for paused work from previous session's /PACT:pause
        paused_msg = check_paused_state(prev_session_dir=prev_session_dir)
        if paused_msg:
            context_parts.append(paused_msg)

        # Build output
        output = {}

        if context_parts or system_messages:
            output["hookSpecificOutput"] = {
                "hookEventName": "SessionStart",
                "additionalContext": " | ".join(context_parts) if context_parts else "Success"
            }

        if system_messages:
            output["systemMessage"] = " | ".join(system_messages)

        if output:
            print(json.dumps(output))
        else:
            print(_SUPPRESS_OUTPUT)

        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_init): {e}", file=sys.stderr)
        print(hook_error_json("session_init", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
