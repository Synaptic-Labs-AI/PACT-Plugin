#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/precompact_state_reminder.py
Summary: PreCompact hook that gathers mechanical state from disk and emits both
         custom_instructions (for the compaction model) and a systemMessage
         (brain dump instructions for the orchestrator).
Used by: hooks.json PreCompact hook

Reads task files and team config to build a concrete state snapshot:
- Task counts by status (completed, in_progress, pending)
- Active teammate names from team config
- Feature task subject and ID (highest-level non-phase task)
- Current phase (from Phase:-prefixed in_progress tasks)
- Variety score (from feature task metadata)
- Team name(s) from team directories

Emits two fields:
- custom_instructions: Injected into the compaction model to guide preservation
- systemMessage: Brain dump instructions for the orchestrator

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin (PreCompact event data)
Output: JSON with custom_instructions and systemMessage on stdout
"""

import json
import sys

from shared.error_output import hook_error_json
from shared.task_scanner import analyze_task_state, scan_team_members


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def _build_state_summary(
    task_state: dict,
    team_info: dict,
) -> str:
    """Build a human-readable state summary from gathered data."""
    lines = []

    # Task counts
    total = task_state["total"]
    if total > 0:
        lines.append(
            f"Tasks: {task_state['completed']} completed, "
            f"{task_state['in_progress']} in_progress, "
            f"{task_state['pending']} pending "
            f"(total: {total})"
        )
    else:
        lines.append("Tasks: none found on disk")

    # Feature subject
    feature = task_state.get("feature_subject")
    feature_id = task_state.get("feature_id")
    if feature:
        id_str = f" (task #{feature_id})" if feature_id else ""
        lines.append(f"Feature: {feature}{id_str}")

    # Current phase
    phase = task_state.get("current_phase")
    if phase:
        lines.append(f"Current phase: {phase}")

    # Active teammates
    teammates = team_info.get("teammates", [])
    if teammates:
        lines.append(f"Active teammates: {', '.join(teammates)}")
    else:
        lines.append("Active teammates: none found")

    return "\n".join(lines)


def build_custom_instructions(
    task_state: dict,
    team_info: dict,
) -> str:
    """
    Build custom_instructions for the compaction model.

    These tell the compaction model what critical context to preserve.
    """
    lines = ["CRITICAL CONTEXT TO PRESERVE:"]

    feature = task_state.get("feature_subject")
    feature_id = task_state.get("feature_id")
    if feature:
        id_str = f" (task #{feature_id})" if feature_id else ""
        lines.append(f"- Feature: {feature}{id_str}")

    phase = task_state.get("current_phase")
    if phase:
        lines.append(f"- Current phase: {phase}")
    else:
        lines.append("- Current phase: unknown")

    teammates = team_info.get("teammates", [])
    if teammates:
        lines.append(f"- Active agents: {', '.join(teammates)}")
    else:
        lines.append("- Active agents: none found")

    variety = task_state.get("variety_score")
    if variety is not None:
        lines.append(f"- Variety score: {variety}")

    team_names = team_info.get("team_names", [])
    if team_names:
        lines.append(f"- Team name: {', '.join(team_names)}")

    lines.append("Preserve task IDs and agent names exactly.")

    return "\n".join(lines)


BRAIN_DUMP_INSTRUCTIONS = (
    "IMPORTANT — Compaction will erase your context window. "
    "Before it happens, do BOTH of the following:\n"
    "\n"
    "1. TaskCreate a 'Pre-compaction state dump' task and immediately "
    "complete it with metadata containing: current phase, active blockers, "
    "key decisions made this session, and anything NOT captured in the "
    "mechanical state above.\n"
    "\n"
    "2. SendMessage to the secretary: '[lead→secretary] Pre-compaction "
    "brain dump: {your ephemeral context — decisions in flight, "
    "reasoning not yet in task metadata, user preferences discovered "
    "this session}'"
)


def build_hook_output(
    tasks_base_dir: str | None = None,
    teams_base_dir: str | None = None,
) -> dict:
    """
    Build the complete hook output with both custom_instructions and
    systemMessage.

    Returns dict ready for json.dumps().
    """
    task_state = analyze_task_state(tasks_base_dir)
    team_info = scan_team_members(teams_base_dir)
    state_summary = _build_state_summary(task_state, team_info)

    system_message = (
        f"Compaction imminent — mechanical state snapshot:\n"
        f"\n"
        f"{state_summary}\n"
        f"\n"
        f"{BRAIN_DUMP_INSTRUCTIONS}"
    )

    custom_instructions = build_custom_instructions(task_state, team_info)

    return {
        "custom_instructions": custom_instructions,
        "systemMessage": system_message,
    }


def main():
    try:
        # Consume stdin (PreCompact may provide transcript_path, etc.)
        try:
            json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            pass

        output = build_hook_output()
        print(json.dumps(output))
        sys.exit(0)

    except Exception as e:
        # Fail open — never block compaction
        print(f"Hook warning (precompact_state_reminder): {e}", file=sys.stderr)
        print(hook_error_json("precompact_state_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
