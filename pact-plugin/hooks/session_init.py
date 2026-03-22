#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_init.py
Summary: SessionStart hook that initializes PACT environment.
Used by: Claude Code settings.json SessionStart hook

Performs:
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

from shared.error_output import hook_error_json

# Import extracted modules (decomposed for maintainability per M5 audit finding).
from shared.symlinks import setup_plugin_symlinks
from shared.claude_md_manager import update_claude_md, ensure_project_memory_md
from shared.session_resume import (
    update_session_info,
    restore_last_session,
    check_resumption_context,
    check_paused_state,
)


def check_pinned_staleness():
    """
    Thin wrapper around staleness.check_pinned_staleness().

    Resolves the CLAUDE.md path via the module-level _get_project_claude_md_path
    (which tests can patch on session_init) and passes it to the core function.
    """
    path = _get_project_claude_md_path()
    return _staleness_check(claude_md_path=path)


def generate_team_name(input_data: dict[str, Any]) -> str:
    """
    Generate a session-unique PACT team name.

    Uses the first 8 characters of the session_id from the SessionStart hook
    input (or CLAUDE_SESSION_ID env var) to create a unique team name like
    "pact-0001639f". Falls back to a random 8-character hex suffix if neither
    source provides a session_id.

    Args:
        input_data: Parsed JSON from stdin (SessionStart hook input)

    Returns:
        Team name string like "pact-0001639f"
    """
    raw_id = input_data.get("session_id")
    session_id = str(raw_id) if raw_id else os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        suffix = session_id[:8]
    else:
        suffix = secrets.token_hex(4)
    return f"pact-{suffix}"


def main():
    """
    Main entry point for the SessionStart hook.

    Performs PACT environment initialization:
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

        # 1. Set up plugin symlinks (enables @~/.claude/protocols/pact-plugin/ references)
        symlink_result = setup_plugin_symlinks()
        if symlink_result and "failed" in symlink_result.lower():
            system_messages.append(symlink_result)
        elif symlink_result:
            context_parts.append(symlink_result)

        # 2. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
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
        try:
            team_config = Path.home() / ".claude" / "teams" / team_name / "config.json"
            team_exists = team_config.exists()
        except OSError:
            # Fail-open: if filesystem check fails, assume fresh session
            team_exists = False
        if team_exists:
            context_parts.insert(0, f'Your team is `{team_name}` (existing — resumed session). Do not call TeamCreate — the team already exists. Use the name `{team_name}` wherever {{team_name}} appears in commands.')
        else:
            context_parts.insert(0, f'Your FIRST action must be: TeamCreate(team_name="{team_name}"). Do not read files, explore code, or respond to the user until the team is created. Use the name `{team_name}` wherever {{team_name}} appears in commands.')

        # 5b. Write session resume info to project CLAUDE.md
        raw_id = input_data.get("session_id")
        session_id = str(raw_id) if raw_id else os.environ.get("CLAUDE_SESSION_ID", "")
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
        project_slug = Path(project_dir).name if project_dir else ""
        session_snapshot = restore_last_session(project_slug=project_slug)
        if session_snapshot:
            context_parts.append(session_snapshot)

        # 8. Check for paused work from previous session's /PACT:pause
        paused_msg = check_paused_state(project_slug=project_slug)
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

        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_init): {e}", file=sys.stderr)
        print(hook_error_json("session_init", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
