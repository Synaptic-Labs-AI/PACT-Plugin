#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_init.py
Summary: SessionStart hook that initializes PACT environment.
Used by: Claude Code settings.json SessionStart hook

Performs:
0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
1. Creates plugin symlinks for @reference resolution
2. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
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
# Public names are get_project_claude_md_path / estimate_tokens in staleness.py.
# Re-exported here with underscore aliases so existing consumers and tests
# that patch "session_init._get_project_claude_md_path" continue to work.
from staleness import (  # noqa: F401
    check_pinned_staleness as _staleness_check,
    PINNED_STALENESS_DAYS,
    PINNED_CONTEXT_TOKEN_BUDGET,
    get_project_claude_md_path,
    estimate_tokens,
    _get_project_claude_md_path,
    _estimate_tokens,
)

from shared.constants import COMPACT_SUMMARY_PATH
from shared.error_output import hook_error_json
from shared.pact_context import write_context
from shared.session_journal import append_event, make_event

# Import extracted modules (decomposed for maintainability per M5 audit finding).
from shared.symlinks import setup_plugin_symlinks
from shared.claude_md_manager import update_claude_md, ensure_project_memory_md
from shared.session_resume import (
    update_session_info,
    restore_last_session,
    check_resumption_context,
    check_paused_state,
)

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


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


def _extract_prev_team_name(project_dir: str) -> str | None:
    """
    Extract the previous session's team name from the project CLAUDE.md.

    Reads the "## Current Session" block written by update_session_info()
    and extracts the team name from lines like "- Team: `pact-abc12345`".

    This is used to locate the previous session's journal for resume context
    and pause state detection. Returns None if CLAUDE.md doesn't exist or
    the team name can't be extracted.

    Args:
        project_dir: CLAUDE_PROJECT_DIR path

    Returns:
        Previous team name string, or None if not found
    """
    if not project_dir:
        return None

    try:
        claude_md = Path(project_dir) / "CLAUDE.md"
        if not claude_md.exists():
            return None

        content = claude_md.read_text(encoding="utf-8")
        # Match "- Team: `pact-XXXXXXXX`" in the Current Session block.
        # Uses [^`]+ (any non-backtick) to be format-agnostic — works even
        # if generate_team_name() changes its suffix format.
        match = re.search(r'- Team:\s*`(pact-[^`]+)`', content)
        if match:
            return match.group(1)
    except (IOError, OSError):
        pass
    return None


def main():
    """
    Main entry point for the SessionStart hook.

    Performs PACT environment initialization:
    0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
    1. Creates plugin symlinks for @reference resolution
    2. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
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

        # 2. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
        # Context resets (compact/clear): CLAUDE.md is already installed from original session
        if not is_context_reset:
            claude_md_msg = update_claude_md()
            if claude_md_msg:
                if "failed" in claude_md_msg.lower() or "unmanaged" in claude_md_msg.lower():
                    system_messages.append(claude_md_msg)
                else:
                    context_parts.append(claude_md_msg)

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

        # Write session_start event to journal (before team existence check).
        # append_event creates the teams directory via mkdir -p if needed.
        raw_session_id = input_data.get("session_id")
        _journal_session_id = str(raw_session_id) if raw_session_id else ""
        append_event(
            make_event(
                "session_start",
                team=team_name,
                session_id=_journal_session_id,
                project_dir=project_dir,
                worktree="",  # Not yet created at this point
            ),
            team_name,
        )

        try:
            team_config = Path.home() / ".claude" / "teams" / team_name / "config.json"
            team_exists = team_config.exists()
        except OSError:
            # Fail-open: if filesystem check fails, assume fresh session
            team_exists = False

        # Build context message based on source × team_exists (5 paths)
        _team_reuse = (
            f'Your team is `{team_name}` (existing — resumed session). '
            f'Do not call TeamCreate — the team already exists. '
            f'Use the name `{team_name}` wherever {{team_name}} appears in commands.'
        )
        _team_create = (
            f'Your FIRST action must be: TeamCreate(team_name="{team_name}"). '
            f'Do not read files, explore code, or respond to the user until the team is created. '
            f'Use the name `{team_name}` wherever {{team_name}} appears in commands.'
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

        # 5a. Write session context file for all subsequent hooks
        raw_id = input_data.get("session_id")
        session_id = str(raw_id) if raw_id else ""
        try:
            write_context(team_name, session_id, project_dir)
        except Exception as e:
            # Fail-open: context file is best-effort; hooks fall back to empty strings
            print(f"session_init: could not write context file: {e}", file=sys.stderr)

        # 5b. Write session resume info to project CLAUDE.md
        if session_id:
            session_msg = update_session_info(session_id, team_name)
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
        # Locate previous session's team name from project CLAUDE.md for journal access
        prev_team = _extract_prev_team_name(project_dir)
        session_snapshot = restore_last_session(prev_team_name=prev_team)
        if session_snapshot:
            context_parts.append(session_snapshot)

        # 8. Check for paused work from previous session's /PACT:pause
        paused_msg = check_paused_state(prev_team_name=prev_team)
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
