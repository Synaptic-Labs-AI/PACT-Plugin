#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/compaction_refresh.py
Summary: SessionStart hook that detects post-compaction sessions and injects
         refresh instructions based on the current TaskList.
Used by: Claude Code hooks.json SessionStart hook (after session_init.py)

This hook fires on SessionStart. On source="compact" sessions it reads workflow
state from TaskList (Tasks persist across compaction at ~/.claude/tasks/{sessionId}/).
If no active workflow is in progress, emits suppressOutput.

The Task system is PACT's single source of truth for workflow state.

Input: JSON from stdin with:
  - source: Session start source ("compact" for post-compaction)

Output: JSON with hookSpecificOutput.additionalContext, or suppressOutput.
"""

import json
import sys
from pathlib import Path
from typing import Any

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json
import shared.pact_context as pact_context

# Import shared Task utilities (DRY - used by multiple hooks)
from shared.task_utils import (
    get_task_list,
    find_feature_task,
    find_current_phase,
    find_active_agents,
    find_blockers,
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

    # Next step guidance
    lines.append("")
    if blockers:
        lines.append("Next Step: **Address blockers before proceeding.**")
    elif agents:
        lines.append("Next Step: Monitor active agents via TaskList, then proceed.")
    elif phase:
        lines.append("Next Step: Continue current phase or check agent completion.")
    else:
        lines.append("Next Step: **Check TaskList and ask user how to proceed.**")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

def main():
    """
    Main entry point for the SessionStart refresh hook.

    On source="compact" sessions, reads TaskList (which persists across
    compaction) and emits a refresh message if any task is in_progress.
    Otherwise emits suppressOutput.
    """
    try:
        # Parse input
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        pact_context.init(input_data)
        source = input_data.get("source", "")

        # Only act on post-compaction sessions
        if source != "compact":
            # Not a post-compaction session, no action needed
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Read TaskList (Tasks survive compaction; single source of truth)
        tasks = get_task_list()

        if tasks:
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

        # No tasks, or tasks exist but nothing in_progress — no active workflow
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Never fail the hook - log and exit cleanly
        print(f"Compaction refresh hook warning: {e}", file=sys.stderr)
        print(hook_error_json("compaction_refresh", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
