"""
Location: pact-plugin/hooks/shared/task_scanner.py
Summary: Shared task and team file scanners for PACT hooks that need cross-team
         state data, plus analysis functions for feature/phase detection.
Used by: precompact_state_reminder.py, postcompact_verify.py

Provides three layers:
1. I/O: scan_all_tasks() and scan_team_members() read disk state
2. Constants: SYSTEM_TASK_PREFIXES for filtering system tasks from feature tasks
3. Analysis: analyze_task_state() detects feature task, phase, and variety score

Unlike task_utils.get_task_list() which is session-scoped (uses pact_context session ID),
these scanners read ALL team directories — needed by compaction hooks that operate
across the full task tree.

Fail-open: all functions return safe defaults on any error.
"""

import json
from pathlib import Path
from typing import Any


# Prefixes that indicate system tasks (not feature tasks).
# Used by both compaction hooks to filter out non-feature tasks when
# identifying the active feature subject.
SYSTEM_TASK_PREFIXES = ("Phase:", "BLOCKER:", "ALERT:", "HALT:")


def scan_all_tasks(
    tasks_base_dir: str | None = None,
) -> list[dict[str, Any]]:
    """
    Scan all team task directories and return parsed task dicts.

    Each returned dict has at least: id, subject, status, metadata.
    The 'id' field falls back to the filename stem if not present in JSON.

    Args:
        tasks_base_dir: Override for ~/.claude/tasks/ (dependency injection
                        for testing). Defaults to ~/.claude/tasks/.

    Returns:
        List of task dicts. Empty list on any error or if no tasks found.
    """
    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    base = Path(tasks_base_dir)
    if not base.exists():
        return []

    tasks = []
    try:
        for team_dir in base.iterdir():
            if not team_dir.is_dir():
                continue
            for task_file in team_dir.iterdir():
                if not task_file.name.endswith(".json"):
                    continue
                try:
                    data = json.loads(task_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                # Normalize: ensure 'id' is always present
                if "id" not in data:
                    data["id"] = task_file.stem

                tasks.append(data)
    except OSError:
        pass  # Fail-open: return whatever we collected so far

    return tasks


def scan_team_members(
    teams_base_dir: str | None = None,
) -> dict[str, Any]:
    """
    Read team config files for active teammate names and team names.

    Scans all team directories under ~/.claude/teams/ and extracts member
    names and team names from config.json files.

    Args:
        teams_base_dir: Override for ~/.claude/teams/ (dependency injection
                        for testing). Defaults to ~/.claude/teams/.

    Returns:
        Dict with keys: teammates (list[str]), team_names (list[str]).
        Empty lists on any error.
    """
    info: dict[str, Any] = {"teammates": [], "team_names": []}

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


def analyze_task_state(
    tasks_base_dir: str | None = None,
) -> dict[str, Any]:
    """
    Analyze task state from all team task directories.

    Uses scan_all_tasks() for file I/O, then applies analysis:
    feature/phase detection via SYSTEM_TASK_PREFIXES filtering,
    status counts, and variety score extraction.

    Args:
        tasks_base_dir: Override for ~/.claude/tasks/ (dependency injection
                        for testing). Defaults to ~/.claude/tasks/.

    Returns:
        Dict with keys: completed, in_progress, pending, total,
        feature_subject, feature_id, current_phase, variety_score.
    """
    state: dict[str, Any] = {
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
            and not any(subject.startswith(p) for p in SYSTEM_TASK_PREFIXES)
        ):
            state["feature_subject"] = subject
            state["feature_id"] = task_id

            # Variety score from feature task metadata
            variety = metadata.get("variety")
            if variety is not None:
                state["variety_score"] = variety

    return state
