"""
Location: pact-plugin/hooks/shared/task_utils.py
Summary: Shared Task system integration utilities for PACT hooks.
Used by: session_init.py, agent_handoff_emitter.py

This module provides common functions for reading and analyzing Tasks from
the Claude Task system. Tasks are stored at ~/.claude/tasks/{sessionId}/*.json
and survive context compaction, making them the primary state source for
workflow recovery.

Functions:
    get_task_list: Read all tasks from the Task system
    find_feature_task: Identify the main Feature task
    find_current_phase: Find the currently active phase task
    find_active_agents: Find all active agent tasks
    find_blockers: Find blocker/algedonic tasks
    build_post_compaction_checkpoint: Build [POST-COMPACTION CHECKPOINT] message from Task state
    read_task_json: Read the raw task JSON by id + team_name (path-traversal safe)
    read_task_metadata: Read task metadata dict by id + team_name
    read_task_owner: Read task owner string by id + team_name
"""

import json
import os
import re
from pathlib import Path
from typing import Any

from shared.pact_context import get_session_id


def get_task_list() -> list[dict[str, Any]] | None:
    """
    Read TaskList from the Claude Task system.

    Tasks are stored at ~/.claude/tasks/{sessionId}/*.json and survive compaction.
    This function reads directly from the filesystem since hooks cannot call Task tools.

    Returns:
        List of task dicts, or None if tasks unavailable
    """
    session_id = get_session_id()
    # Also check for multi-session task list ID
    task_list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", session_id)

    if not task_list_id:
        return None

    tasks_dir = Path.home() / ".claude" / "tasks" / task_list_id
    if not tasks_dir.exists():
        return None

    tasks = []
    try:
        for task_file in tasks_dir.glob("*.json"):
            try:
                content = task_file.read_text(encoding='utf-8')
                task = json.loads(content)
                tasks.append(task)
            except (IOError, json.JSONDecodeError):
                continue
    except Exception:
        return None

    return tasks if tasks else None


def find_feature_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the main Feature task from the task list.

    Feature tasks are top-level tasks that represent the overall work item.
    They can be identified by:
    - Having no blockedBy (top-level)
    - Subject starting with a verb (e.g., "Implement user auth")
    - OR having phase tasks as children

    Args:
        tasks: List of all tasks

    Returns:
        Feature task dict, or None if not found
    """
    # Feature task is one that blocks others but isn't blocked itself
    # (or has status in_progress at top level)
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            continue

        # Skip if this task is blocked by something
        if task.get("blockedBy"):
            continue

        # Check if it's a feature-like task (not a phase task)
        subject = task.get("subject", "")
        # Phase tasks start with phase names
        phase_prefixes = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:", "Review:")
        if any(subject.startswith(p) for p in phase_prefixes):
            continue

        # This looks like a feature task
        if task.get("status") in ("in_progress", "pending"):
            return task

    return None


def find_current_phase(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Find the currently active phase task.

    Phase tasks follow the pattern: "{PHASE}: {feature-slug}"
    The current phase is the one with status "in_progress".

    Args:
        tasks: List of all tasks

    Returns:
        Phase task dict, or None if not found
    """
    phase_prefixes = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")

    for task in tasks:
        subject = task.get("subject", "")
        if any(subject.startswith(p) for p in phase_prefixes):
            if task.get("status") == "in_progress":
                return task

    return None


def find_active_agents(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find all currently active agent tasks.

    Agent tasks follow the pattern: "{agent-type}: {work-description}"
    and have status "in_progress".

    Args:
        tasks: List of all tasks

    Returns:
        List of agent task dicts
    """
    agent_prefixes = (
        "preparer:",
        "architect:",
        "backend-coder:",
        "frontend-coder:",
        "database-engineer:",
        "devops-engineer:",
        "n8n:",
        "test-engineer:",
        "security-engineer:",
        "qa-engineer:",
        "auditor:",
        "secretary:",
    )

    active = []
    for task in tasks:
        subject = task.get("subject", "").lower()
        if any(subject.startswith(p) for p in agent_prefixes):
            if task.get("status") == "in_progress":
                active.append(task)

    return active


def find_blockers(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Find any blocker or algedonic tasks.

    These are signal tasks created by agents when they hit blockers
    or detect viability threats.

    Args:
        tasks: List of all tasks

    Returns:
        List of blocker/algedonic task dicts
    """
    blockers = []
    for task in tasks:
        metadata = task.get("metadata", {})
        task_type = metadata.get("type", "")
        if task_type in ("blocker", "algedonic"):
            if task.get("status") != "completed":
                blockers.append(task)

    return blockers


def build_post_compaction_checkpoint(
    feature: dict[str, Any] | None,
    phase: dict[str, Any] | None,
    agents: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> str:
    """Build [POST-COMPACTION CHECKPOINT] message from Task state.

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


def read_task_json(
    task_id: str,
    team_name: str | None,
    tasks_base_dir: str | None = None,
) -> dict:
    """
    Read the raw task JSON from disk, safe against path traversal.

    Locates the task file in the team directory first, then falls back to
    the base directory. Hoisted from handoff_gate.py so non-hook callers
    (agent_handoff_emitter.py) can consume the same reader.

    Args:
        task_id: Task identifier. Sanitized to strip `/`, `\\`, and `..`.
        team_name: Team name for scoped task lookup (may be None).
        tasks_base_dir: Override for tasks base directory (testing).

    Returns:
        Full task dict from the JSON file, or empty dict on any failure
        (missing file, malformed JSON, IO error). Fail-open by design —
        callers treat "no task data" as a signal to bypass, not crash.
    """
    if not task_id:
        return {}

    task_id = re.sub(r'[/\\]|\.\.', '', task_id)
    if not task_id:
        return {}

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    base = Path(tasks_base_dir)

    task_dirs = []
    if team_name:
        task_dirs.append(base / team_name)
    task_dirs.append(base)

    for task_dir in task_dirs:
        task_file = task_dir / f"{task_id}.json"
        if task_file.exists():
            try:
                return json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return {}

    return {}


def read_task_metadata(
    task_id: str,
    team_name: str | None,
    tasks_base_dir: str | None = None,
) -> dict:
    """
    Read task metadata dict by id + team_name. Fail-open (empty dict).
    """
    return read_task_json(task_id, team_name, tasks_base_dir).get("metadata", {})


def read_task_owner(
    task_id: str,
    team_name: str | None,
    tasks_base_dir: str | None = None,
) -> str | None:
    """
    Read task owner string by id + team_name.

    Used as fallback when the platform doesn't provide teammate_name in
    hook input (e.g., lead marks task completed on behalf of an agent).
    """
    return read_task_json(task_id, team_name, tasks_base_dir).get("owner")
