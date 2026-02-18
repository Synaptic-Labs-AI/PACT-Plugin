#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_init.py
Summary: SessionStart hook that initializes PACT environment.
Used by: Claude Code settings.json SessionStart hook

Performs:
1. Creates plugin symlinks for @reference resolution
2. Detects active plans and notifies user
3. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
4. Ensures project CLAUDE.md exists with memory sections
5. Checks for stale pinned context (delegated to staleness.py)
6. Generates session-unique PACT team name and reminds orchestrator to create it
7. Checks for in_progress Tasks (resumption context via Task integration)

Note: Memory-related initialization (dependency installation, embedding
migration, pending embedding catch-up) is now lazy-loaded on first memory
operation via pact-memory/scripts/memory_init.py. This reduces startup
cost for non-memory users.

Input: JSON from stdin with session context
Output: JSON with `hookSpecificOutput.additionalContext` for status
"""

import json
import secrets
import sys
import os
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


def setup_plugin_symlinks() -> str | None:
    """
    Create symlinks for plugin resources to ~/.claude/.

    Creates:
    1. ~/.claude/protocols/pact-plugin/ -> plugin/protocols/
       (enables @~/.claude/protocols/pact-plugin/... references in CLAUDE.md)
    2. ~/.claude/agents/pact-*.md -> plugin/agents/pact-*.md
       (enables non-prefixed agent names like "pact-memory-agent")

    Returns:
        Status message or None if successful
    """
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    if not plugin_root.exists():
        return None

    claude_dir = Path.home() / ".claude"
    messages = []

    # 1. Symlink protocols/ directory
    protocols_src = plugin_root / "protocols"
    if protocols_src.exists():
        protocols_dst = claude_dir / "protocols" / "pact-plugin"
        protocols_dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            if protocols_dst.is_symlink():
                if protocols_dst.resolve() != protocols_src.resolve():
                    protocols_dst.unlink()
                    protocols_dst.symlink_to(protocols_src)
                    messages.append("protocols updated")
            elif not protocols_dst.exists():
                protocols_dst.symlink_to(protocols_src)
                messages.append("protocols linked")
        except OSError as e:
            messages.append(f"protocols failed: {str(e)[:20]}")

    # 2. Symlink individual agent files (enables non-prefixed agent names)
    agents_src = plugin_root / "agents"
    if agents_src.exists():
        agents_dst = claude_dir / "agents"
        agents_dst.mkdir(parents=True, exist_ok=True)

        agents_updated = 0
        agents_created = 0
        for agent_file in agents_src.glob("pact-*.md"):
            dst_file = agents_dst / agent_file.name
            try:
                if dst_file.is_symlink():
                    if dst_file.resolve() != agent_file.resolve():
                        dst_file.unlink()
                        dst_file.symlink_to(agent_file)
                        agents_updated += 1
                elif not dst_file.exists():
                    dst_file.symlink_to(agent_file)
                    agents_created += 1
                # Skip if real file exists (user override)
            except OSError:
                continue

        if agents_created:
            messages.append(f"{agents_created} agents linked")
        if agents_updated:
            messages.append(f"{agents_updated} agents updated")

    if not messages:
        return "PACT symlinks verified"
    return "PACT: " + ", ".join(messages)


def find_active_plans(project_dir: str) -> list:
    """
    Find plans with IN_PROGRESS status or uncompleted items.

    Args:
        project_dir: The project root directory path

    Returns:
        List of plan filenames that appear to be in progress
    """
    plans_dir = Path(project_dir) / "docs" / "plans"
    active_plans = []

    if not plans_dir.is_dir():
        return active_plans

    for plan_file in plans_dir.glob("*-plan.md"):
        try:
            content = plan_file.read_text(encoding='utf-8')
            in_progress_indicators = [
                "Status: IN_PROGRESS",
                "Status: In Progress",
                "status: in_progress",
                "Status: ACTIVE",
                "Status: Active",
            ]

            has_in_progress_status = any(
                indicator in content for indicator in in_progress_indicators
            )
            has_unchecked_items = "[ ] " in content
            is_completed = any(
                status in content for status in [
                    "Status: COMPLETED",
                    "Status: Completed",
                    "Status: DONE",
                    "Status: Done",
                ]
            )

            if has_in_progress_status or (has_unchecked_items and not is_completed):
                active_plans.append(plan_file.name)

        except (IOError, UnicodeDecodeError):
            continue

    return active_plans


def update_claude_md() -> str | None:
    """
    Update ~/.claude/CLAUDE.md with PACT content.

    Automatically merges or updates the PACT Orchestrator prompt in the user's
    CLAUDE.md file. Uses explicit markers to manage the PACT section without
    disturbing other user customizations.

    Strategy:
    1. If file missing -> create with PACT content in markers.
    2. If markers found -> replace content between markers.
    3. If no markers but "PACT Orchestrator" found -> assume manual install, warn.
    4. If no markers and no conflict -> append PACT content with markers.

    Returns:
        Status message or None if no change.
    """
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", ""))
    if not plugin_root.exists():
        return None

    source_file = plugin_root / "CLAUDE.md"
    if not source_file.exists():
        return None

    target_file = Path.home() / ".claude" / "CLAUDE.md"

    START_MARKER = "<!-- PACT_START: Managed by pact-plugin - Do not edit this block -->"
    END_MARKER = "<!-- PACT_END -->"

    try:
        source_content = source_file.read_text(encoding="utf-8")
        wrapped_source = f"{START_MARKER}\n{source_content}\n{END_MARKER}"

        # Case 1: Target doesn't exist
        if not target_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(wrapped_source, encoding="utf-8")
            return "Created CLAUDE.md with PACT Orchestrator"

        target_content = target_file.read_text(encoding="utf-8")

        # Case 2: Markers found - update if changed
        if START_MARKER in target_content and END_MARKER in target_content:
            parts = target_content.split(START_MARKER)
            pre = parts[0]
            # Handle case where multiple markers might exist (take first and last valid)
            # but usually just one block.
            rest = parts[1]
            if END_MARKER in rest:
                post = rest.split(END_MARKER, 1)[1]
                new_full_content = f"{pre}{wrapped_source}{post}"

                if new_full_content != target_content:
                    target_file.write_text(new_full_content, encoding="utf-8")
                    return "PACT Orchestrator updated"
                return None

        # Case 3: No markers but content similar to PACT found
        if "PACT Orchestrator" in target_content:
            # Check if it looks roughly like what we expect, or just leave it
            # Returning a message prompts the user to check it
            return "PACT present but unmanaged (add markers to auto-update)"

        # Case 4: No markers, no specific PACT content -> Append
        # Ensure we append on a new line
        if not target_content.endswith("\n"):
            target_content += "\n"

        new_content = f"{target_content}\n{wrapped_source}"
        target_file.write_text(new_content, encoding="utf-8")
        return "PACT Orchestrator added to CLAUDE.md"

    except Exception as e:
        return f"PACT update failed: {str(e)[:30]}"


def ensure_project_memory_md() -> str | None:
    """
    Ensure project has a CLAUDE.md with memory sections.

    Creates a minimal project-level CLAUDE.md containing only the memory
    sections (Retrieved Context, Working Memory) if one doesn't exist.
    These sections are project-specific and managed by the pact-memory skill.

    If the project already has a CLAUDE.md, this function does nothing
    (preserves existing project configuration).

    Returns:
        Status message or None if no action taken.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    target_file = Path(project_dir) / "CLAUDE.md"

    # Don't overwrite existing project CLAUDE.md
    if target_file.exists():
        return None

    # Create minimal CLAUDE.md with memory sections
    memory_template = """# Project Memory

This file contains project-specific memory managed by the PACT framework.
The global PACT Orchestrator is loaded from `~/.claude/CLAUDE.md`.

## Retrieved Context
<!-- Auto-managed by pact-memory skill. Last 3 retrieved memories shown. -->

## Working Memory
<!-- Auto-managed by pact-memory skill. Last 3 memories shown. Full history searchable via pact-memory skill. -->
"""

    try:
        target_file.write_text(memory_template, encoding="utf-8")
        return "Created project CLAUDE.md with memory sections"
    except Exception as e:
        return f"Project CLAUDE.md failed: {str(e)[:30]}"


def restore_last_session(
    project_slug: str,
    sessions_dir: str | None = None,
) -> str | None:
    """
    Restore the last session snapshot for cross-session continuity.

    Checks if ~/.claude/pact-sessions/{project_slug}/last-session.md exists.
    If found, reads the content, rotates it to last-session.prev.md, and returns
    the content with a header for injection as additionalContext.

    Args:
        project_slug: Project identifier for the session directory
        sessions_dir: Override for sessions base directory (for testing)

    Returns:
        Snapshot content with header if file exists, None otherwise
    """
    if not project_slug:
        return None

    if sessions_dir is None:
        sessions_dir = str(Path.home() / ".claude" / "pact-sessions")

    snapshot_file = Path(sessions_dir) / project_slug / "last-session.md"
    if not snapshot_file.exists():
        return None

    try:
        content = snapshot_file.read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return None

    if not content.strip():
        return None

    # Rotate: move last-session.md to last-session.prev.md
    prev_file = snapshot_file.parent / "last-session.prev.md"
    try:
        # Overwrite any existing prev file
        prev_file.write_text(content, encoding="utf-8")
        snapshot_file.unlink()
    except (IOError, OSError):
        pass  # Best-effort rotation; don't fail the restore

    return (
        "Previous session summary (read-only reference -- not live tasks):\n"
        + content
    )


def check_resumption_context(tasks: list[dict[str, Any]]) -> str | None:
    """
    Check if there are in_progress Tasks indicating work to resume.

    This helps users understand the current state when starting a new session
    with a persistent task list (CLAUDE_CODE_TASK_LIST_ID set).

    Args:
        tasks: List of all tasks

    Returns:
        Status message describing resumption context, or None if nothing to report
    """
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending = [t for t in tasks if t.get("status") == "pending"]
    completed = [t for t in tasks if t.get("status") == "completed"]

    if not in_progress and not pending:
        return None

    # Count by type
    feature_tasks = []
    phase_tasks = []
    agent_tasks = []
    blocker_tasks = []

    for task in in_progress:
        subject = task.get("subject", "")
        metadata = task.get("metadata", {})

        if metadata.get("type") in ("blocker", "algedonic"):
            blocker_tasks.append(task)
        elif any(subject.startswith(p) for p in ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")):
            phase_tasks.append(task)
        elif any(subject.lower().startswith(p) for p in ("pact-",)):
            agent_tasks.append(task)
        else:
            # Assume it's a feature task
            feature_tasks.append(task)

    parts = []

    if feature_tasks:
        names = [t.get("subject", "unknown")[:30] for t in feature_tasks[:2]]
        if len(feature_tasks) > 2:
            parts.append(f"Features: {', '.join(names)} (+{len(feature_tasks)-2} more)")
        else:
            parts.append(f"Features: {', '.join(names)}")

    if phase_tasks:
        phases = [t.get("subject", "").split(":")[0] for t in phase_tasks]
        parts.append(f"Phases: {', '.join(phases)}")

    if agent_tasks:
        parts.append(f"Active agents: {len(agent_tasks)}")

    if blocker_tasks:
        parts.append(f"**Blockers: {len(blocker_tasks)}**")

    if parts:
        summary = f"Resumption context: {' | '.join(parts)}"
        if pending:
            summary += f" ({len(pending)} pending)"
        return summary

    return None


def main():
    """
    Main entry point for the SessionStart hook.

    Performs PACT environment initialization:
    1. Creates plugin symlinks for @reference resolution
    2. Checks for active plans
    3. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
    4. Ensures project CLAUDE.md exists with memory sections
    5. Checks for stale pinned context entries in project CLAUDE.md
    6. Generates session-unique PACT team name and reminds orchestrator to create it
    7. Checks for in_progress Tasks (resumption context via Task integration)
    8. Restores last session snapshot for cross-session continuity

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

        # 2. Check for active plans
        active_plans = find_active_plans(project_dir)
        if active_plans:
            plan_list = ", ".join(active_plans[:3])
            if len(active_plans) > 3:
                plan_list += f" (+{len(active_plans) - 3} more)"
            context_parts.append(f"Active plans: {plan_list}")

        # 3. Updates ~/.claude/CLAUDE.md (merges/installs PACT Orchestrator)
        claude_md_msg = update_claude_md()
        if claude_md_msg:
            if "failed" in claude_md_msg.lower() or "unmanaged" in claude_md_msg.lower():
                system_messages.append(claude_md_msg)
            else:
                context_parts.append(claude_md_msg)

        # 4. Ensure project has CLAUDE.md with memory sections
        project_md_msg = ensure_project_memory_md()
        if project_md_msg:
            if "failed" in project_md_msg.lower():
                system_messages.append(project_md_msg)
            else:
                context_parts.append(project_md_msg)

        # 5. Check for stale pinned context
        staleness_msg = check_pinned_staleness()
        if staleness_msg:
            if "failed" in staleness_msg.lower():
                system_messages.append(staleness_msg)
            else:
                context_parts.append(staleness_msg)

        # 6. Remind orchestrator to create session-unique PACT team
        team_name = generate_team_name(input_data)
        context_parts.insert(0, f'Your FIRST action must be: TeamCreate(team_name="{team_name}"). Do not read files, explore code, or respond to the user until the team is created. Use the name `{team_name}` wherever {{team_name}} appears in commands.')

        # 7. Check for in_progress Tasks (resumption context via Task integration)
        tasks = get_task_list()
        if tasks:
            resumption_msg = check_resumption_context(tasks)
            if resumption_msg:
                # Blockers are critical - put in system message for visibility
                if "**Blockers:" in resumption_msg:
                    system_messages.append(resumption_msg)
                else:
                    context_parts.append(resumption_msg)

        # 8. Restore last session snapshot for cross-session continuity
        project_slug = Path(project_dir).name if project_dir else ""
        session_snapshot = restore_last_session(project_slug=project_slug)
        if session_snapshot:
            context_parts.append(session_snapshot)

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
        sys.exit(0)


if __name__ == "__main__":
    main()
