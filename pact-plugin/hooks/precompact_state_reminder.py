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
from pathlib import Path

from shared.error_output import hook_error_json
from shared.task_scanner import scan_all_tasks


# Prefixes that indicate system tasks (not feature tasks)
_SYSTEM_PREFIXES = ("Phase:", "BLOCKER:", "ALERT:", "HALT:")


# ---------------------------------------------------------------------------
# Disk state gathering — all functions fail open (return defaults on error)
# ---------------------------------------------------------------------------


def _gather_task_state(
    tasks_base_dir: str | None = None,
) -> dict:
    """
    Analyze task state from all team task directories.

    Uses shared.task_scanner.scan_all_tasks() for file I/O, then applies
    precompact-specific analysis: status counts, feature/phase detection,
    variety score extraction.

    Returns dict with keys: completed, in_progress, pending, total,
    feature_subject, feature_id, current_phase, variety_score.
    """
    state = {
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "total": 0,
        "feature_subject": None,
        "feature_id": None,
        "current_phase": None,
        "variety_score": None,
    }

    for data in scan_all_tasks(tasks_base_dir):
        status = data.get("status", "pending")
        if status in ("completed", "in_progress", "pending"):
            state[status] += 1
        state["total"] += 1

        subject = data.get("subject", "")
        task_id = str(data.get("id", ""))
        metadata = data.get("metadata") or {}

        # Phase detection: in_progress task with "Phase:" prefix
        if (
            status == "in_progress"
            and subject.startswith("Phase:")
            and state["current_phase"] is None
        ):
            state["current_phase"] = subject

        # Feature task: in_progress without system prefixes
        if (
            status == "in_progress"
            and state["feature_subject"] is None
            and subject
            and not any(subject.startswith(p) for p in _SYSTEM_PREFIXES)
        ):
            state["feature_subject"] = subject
            state["feature_id"] = task_id

            # Variety score from feature task metadata
            variety = metadata.get("variety")
            if variety is not None:
                state["variety_score"] = variety

    return state


def _gather_team_info(
    teams_base_dir: str | None = None,
) -> dict:
    """
    Read team config files for active teammate names and team names.

    Returns dict with keys: teammates (list[str]), team_names (list[str]).
    """
    info = {"teammates": [], "team_names": []}

    if teams_base_dir is None:
        teams_base_dir = str(Path.home() / ".claude" / "teams")

    base = Path(teams_base_dir)
    if not base.exists():
        return info

    try:
        for team_dir in base.iterdir():
            if not team_dir.is_dir():
                continue
            config_path = team_dir / "config.json"
            if not config_path.exists():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Track team name from config or directory name
            team_name = data.get("name", team_dir.name)
            if team_name:
                info["team_names"].append(team_name)

            # config.json has "members" list with "name" fields
            members = data.get("members", [])
            for member in members:
                name = member.get("name", "") if isinstance(member, dict) else ""
                if name:
                    info["teammates"].append(name)
    except OSError:
        pass  # Can't read teams dir — return defaults

    return info


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
    task_state = _gather_task_state(tasks_base_dir)
    team_info = _gather_team_info(teams_base_dir)
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
