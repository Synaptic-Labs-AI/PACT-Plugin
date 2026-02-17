"""
Location: pact-plugin/hooks/shared/task_utils.py
Summary: Shared Task system integration utilities for PACT hooks.
Used by: compaction_refresh.py, validate_handoff.py, phase_completion.py,
         session_init.py, stop_audit.py

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
"""

import json
import os
from pathlib import Path
from typing import Any


def get_task_list() -> list[dict[str, Any]] | None:
    """
    Read TaskList from the Claude Task system.

    Tasks are stored at ~/.claude/tasks/{sessionId}/*.json and survive compaction.
    This function reads directly from the filesystem since hooks cannot call Task tools.

    Returns:
        List of task dicts, or None if tasks unavailable
    """
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
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
    # Look for tasks with no blockedBy that have children
    task_ids = {t.get("id") for t in tasks if t.get("id")}
    blocked_by_ids = set()
    for task in tasks:
        blocked_by = task.get("blockedBy", [])
        if blocked_by:
            blocked_by_ids.update(blocked_by)

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
        "pact-preparer:",
        "pact-architect:",
        "pact-backend-coder:",
        "pact-frontend-coder:",
        "pact-database-engineer:",
        "pact-devops-engineer:",
        "pact-n8n:",
        "pact-test-engineer:",
        "pact-security-engineer:",
        "pact-qa-engineer:",
        "pact-memory-agent:",
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
