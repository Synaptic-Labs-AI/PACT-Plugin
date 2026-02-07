#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/compaction_refresh.py
Summary: SessionStart hook that detects post-compaction sessions and injects refresh instructions.
Used by: Claude Code hooks.json SessionStart hook (after session_init.py)

This hook fires on SessionStart. It checks if the session was triggered by compaction
(source="compact") and if so, reads workflow state from TaskList (which survives compaction)
to build refresh context. If no TaskList is available, falls back to checkpoint file.

The Task system (TaskCreate, TaskUpdate, TaskGet, TaskList) is PACT's single source of truth
for workflow state. Tasks persist across compaction at ~/.claude/tasks/{sessionId}/*.json.

Agent Teams context: Teammates are independent processes that survive compaction
(empirically verified). The refresh message includes team state to help the
orchestrator understand what teammates are still active post-compaction.

Input: JSON from stdin with:
  - source: Session start source ("compact" for post-compaction, others for normal start)

Output: JSON with hookSpecificOutput.additionalContext (refresh instructions if applicable)

Fallback checkpoint location: ~/.claude/pact-refresh/{encoded-path}.json
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

# Add hooks directory to path for refresh and shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# Import checkpoint utilities from refresh package (always available - same directory)
# These are used as fallback when TaskList is unavailable
from refresh.checkpoint_builder import (
    get_checkpoint_path,
    get_encoded_project_path,
    checkpoint_to_refresh_message,
)

# Import shared Task utilities (DRY - used by multiple hooks)
from shared.task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
)

# Import shared Team utilities for Agent Teams context in refresh
from shared.team_utils import (
    derive_team_name,
    get_current_branch,
    team_exists,
    get_team_members,
    find_active_teams,
)


def build_refresh_from_tasks(
    feature: dict[str, Any] | None,
    phase: dict[str, Any] | None,
    agents: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> str:
    """
    Build refresh context message from Task state.

    Generates a concise message describing the workflow state for
    the orchestrator to resume from.

    Args:
        feature: Feature task dict or None
        phase: Current phase task dict or None
        agents: List of active agent tasks
        blockers: List of active blocker tasks

    Returns:
        Formatted refresh message string
    """
    lines = ["[POST-COMPACTION CHECKPOINT]"]
    lines.append("Prior conversation auto-compacted. Resume from Task state below:")

    # Feature context
    if feature:
        feature_subject = feature.get("subject", "unknown feature")
        feature_id = feature.get("id", "")
        if feature_id:
            lines.append(f"Feature: {feature_subject} (id: {feature_id})")
        else:
            lines.append(f"Feature: {feature_subject}")
    else:
        lines.append("Feature: Unable to identify feature task")

    # Phase context
    if phase:
        phase_subject = phase.get("subject", "unknown phase")
        lines.append(f"Current Phase: {phase_subject}")
    else:
        lines.append("Current Phase: None detected")

    # Active agents
    if agents:
        agent_names = [a.get("subject", "unknown") for a in agents]
        lines.append(f"Active Agents ({len(agents)}): {', '.join(agent_names)}")
    else:
        lines.append("Active Agents: None")

    # Blockers (critical info)
    if blockers:
        lines.append("")
        lines.append("**BLOCKERS DETECTED:**")
        for blocker in blockers:
            subj = blocker.get("subject", "unknown blocker")
            meta = blocker.get("metadata", {})
            level = meta.get("level", "")
            if level:
                lines.append(f"  - {level}: {subj}")
            else:
                lines.append(f"  - {subj}")

    # Team state (teammates survive compaction as independent processes)
    _append_team_context(lines)

    # Next step guidance
    lines.append("")
    if blockers:
        lines.append("Next Step: **Address blockers before proceeding.**")
    elif agents:
        lines.append(
            "Next Step: Check on active teammates via SendMessage, "
            "then continue current phase."
        )
    elif phase:
        lines.append("Next Step: Continue current phase or check teammate completion.")
    else:
        lines.append("Next Step: **Check TaskList and ask user how to proceed.**")

    return "\n".join(lines)


def _append_team_context(lines: list[str]) -> None:
    """
    Append Agent Teams context to refresh message lines.

    Teammates are independent processes that survive compaction, so the
    orchestrator needs to know which team and teammates are still active.

    Args:
        lines: List of message lines to append to (mutated in place)
    """
    teams = find_active_teams()
    if not teams:
        return

    for team_name in teams:
        members = get_team_members(team_name)
        active_members = [m for m in members if m.get("status") == "active"]
        if active_members:
            names = [m.get("name", "?") for m in active_members[:6]]
            lines.append(
                f"Team '{team_name}': {len(active_members)} active teammate(s) "
                f"({', '.join(names)})"
                + (f" (+{len(active_members)-6} more)" if len(active_members) > 6 else "")
            )
            lines.append(
                "Note: Teammates survived compaction and remain active. "
                "Use SendMessage to communicate with them."
            )
        else:
            lines.append(f"Team: '{team_name}' (no active teammates)")
    return


# -----------------------------------------------------------------------------
# Checkpoint Fallback (Legacy State Source)
# -----------------------------------------------------------------------------

def read_checkpoint(checkpoint_path: Path) -> dict | None:
    """
    Read and parse the checkpoint file (fallback when Tasks unavailable).

    Args:
        checkpoint_path: Path to the checkpoint file

    Returns:
        Parsed checkpoint data, or None if file doesn't exist or is invalid
    """
    try:
        if not checkpoint_path.exists():
            return None
        content = checkpoint_path.read_text(encoding='utf-8')
        return json.loads(content)
    except (IOError, json.JSONDecodeError):
        return None


def validate_checkpoint(checkpoint: dict, current_session_id: str) -> bool:
    """
    Validate that the checkpoint is applicable to the current session.

    Checks:
    - Session ID matches (compaction preserves session ID)
    - Checkpoint has required fields
    - Version is supported

    Args:
        checkpoint: The checkpoint data
        current_session_id: Current session ID from environment

    Returns:
        True if checkpoint is valid and applicable
    """
    if not checkpoint:
        return False

    # Check version (handle None values)
    version = checkpoint.get("version", "")
    if not version or not version.startswith("1."):
        return False

    # Check session ID matches
    checkpoint_session = checkpoint.get("session_id", "")
    if checkpoint_session != current_session_id:
        return False

    # Check workflow field exists
    if "workflow" not in checkpoint:
        return False

    return True


def build_refresh_message_from_checkpoint(checkpoint: dict) -> str:
    """
    Build the refresh instruction message from checkpoint (fallback).

    Delegates to checkpoint_to_refresh_message from the refresh package.

    Args:
        checkpoint: The validated checkpoint data

    Returns:
        Formatted refresh message string
    """
    return checkpoint_to_refresh_message(checkpoint)


# Alias for backward compatibility with tests
build_refresh_message = build_refresh_message_from_checkpoint


# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

def main():
    """
    Main entry point for the SessionStart refresh hook.

    Strategy:
    1. Primary: Read TaskList directly (Tasks survive compaction)
    2. Fallback: Read checkpoint file if TaskList unavailable

    Checks if this is a post-compaction session and injects refresh instructions
    if an active workflow was in progress.
    """
    try:
        # Parse input
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        source = input_data.get("source", "")

        # Only act on post-compaction sessions
        if source != "compact":
            # Not a post-compaction session, no action needed
            sys.exit(0)

        session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")

        # ---------------------------------------------------------------------
        # Primary: Try TaskList first (Tasks survive compaction)
        # ---------------------------------------------------------------------
        tasks = get_task_list()

        if tasks:
            # Find workflow state from Tasks
            in_progress = [t for t in tasks if t.get("status") == "in_progress"]

            if in_progress:
                feature_task = find_feature_task(tasks)
                current_phase = find_current_phase(tasks)
                active_agents = find_active_agents(tasks)
                blockers = find_blockers(tasks)

                refresh_message = build_refresh_from_tasks(
                    feature=feature_task,
                    phase=current_phase,
                    agents=active_agents,
                    blockers=blockers,
                )

                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": refresh_message
                    }
                }
                print(json.dumps(output))
                sys.exit(0)

            # Tasks exist but nothing in_progress - no active workflow
            sys.exit(0)

        # ---------------------------------------------------------------------
        # Fallback: Read checkpoint file (legacy approach)
        # ---------------------------------------------------------------------
        encoded_path = get_encoded_project_path("")

        if encoded_path == "unknown-project":
            # Cannot determine project, skip refresh
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "Refresh skipped: project path unavailable"
                }
            }))
            sys.exit(0)

        # Read checkpoint
        checkpoint_path = get_checkpoint_path(encoded_path)
        checkpoint = read_checkpoint(checkpoint_path)

        if not checkpoint:
            # No checkpoint file, nothing to recover
            sys.exit(0)

        # Validate checkpoint
        if not validate_checkpoint(checkpoint, session_id):
            # Checkpoint invalid or from different session
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "Refresh skipped: checkpoint validation failed"
                }
            }))
            sys.exit(0)

        # Check if there was an active workflow
        workflow_name = checkpoint.get("workflow", {}).get("name", "none")
        if workflow_name == "none":
            # No active workflow at compaction time
            sys.exit(0)

        # Build and inject refresh instructions from checkpoint
        refresh_message = build_refresh_message_from_checkpoint(checkpoint)

        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": refresh_message
            }
        }

        print(json.dumps(output))
        sys.exit(0)

    except Exception as e:
        # Never fail the hook - log and exit cleanly
        print(f"Compaction refresh hook warning: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
