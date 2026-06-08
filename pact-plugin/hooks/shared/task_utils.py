"""
Location: pact-plugin/hooks/shared/task_utils.py
Summary: Shared Task system integration utilities for PACT hooks.
Used by: PACT lifecycle/wake hooks (via get_task_list), the lifecycle gate
and handoff machinery (via read_task_json), and dispatch helpers (via
iter_team_task_jsons).

This module provides common functions for reading and analyzing Tasks from
the Claude Task system. Tasks live under one of two storage topologies, keyed
on a structural signal (team_name): a TEAM session stores tasks at
~/.claude/tasks/{team_name}/*.json, a SOLO/no-team session at
~/.claude/tasks/{session_id}/*.json. They survive context compaction, making
them the primary state source for workflow recovery.

Functions:
    get_task_list: Read all tasks from the Task system
    find_feature_task: Identify the main Feature task
    find_current_phase: Find the currently active phase task
    find_active_agents: Find all active agent tasks
    find_blockers: Find blocker/algedonic tasks
    build_post_compaction_checkpoint: Build [POST-COMPACTION CHECKPOINT] message from Task state
    iter_team_task_jsons: Yield parsed task JSONs from a team dir (path-traversal safe)
    read_task_json: Read the raw task JSON by id + team_name (path-traversal safe)
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from shared.pact_context import get_session_id, get_team_name
from shared.session_state import is_safe_path_component
from shared.paths import get_claude_config_dir


def get_task_list(tasks_base_dir: str | None = None) -> list[dict[str, Any]] | None:
    """
    Read TaskList from the Claude Task system.

    Two live storage topologies, keyed on a structural signal (team_name):
      - TEAM session -> tasks under ~/.claude/tasks/{team_name}/   (Agent Teams)
      - SOLO session -> tasks under ~/.claude/tasks/{session_id}/  (no team)
    team_name is mode-independent (resolves identically under in-process and
    tmux), so the team branch needs no teammateMode detection. This function
    reads directly from the filesystem since hooks cannot call Task tools.

    Args:
        tasks_base_dir: Override for the ~/.claude/tasks base dir (testing only;
            production default None resolves the real home path). Mirrors the
            institutional test seam in _read_task_counts / read_task_json.

    Returns:
        List of task dicts, or None if tasks unavailable
    """
    # TEAM branch (the team-dir resolution fix): a team session stores tasks
    # under {team_name}, NOT the bare session_id. Reuse the path-traversal-safe
    # SSOT reader. An empty/absent team dir returns None and MUST NOT fall
    # through to the solo session_id dir: a team session with no tasks yet is
    # still a team session (branch key = "is this a team session?", not "did the
    # team dir have tasks?"). The return lives INSIDE the `if` to guarantee that
    # invariant — control never reaches the solo branch when team_name is truthy.
    team_name = get_team_name()
    if team_name:
        tasks = list(iter_team_task_jsons(team_name, tasks_base_dir=tasks_base_dir))
        return tasks if tasks else None

    # SOLO / no-team branch: the CLAUDE_CODE_TASK_LIST_ID-or-session_id key
    # choice is preserved, and the dir is read by this branch's own direct glob
    # (NOT routed through iter_team_task_jsons) — but with the SAME FIVE
    # path-safety + content-hygiene defenses applied INLINE, in FULL PARITY with
    # the team branch (iter_team_task_jsons): (1) is_safe_path_component on the
    # name (F2 guard below), (2) a resolve/relative_to base-anchor on the dir,
    # (3) a per-file is_symlink skip, (4) a dotfile-prefixed-file skip, and
    # (5) an isinstance(dict) parse guard. The solo branch is therefore NOT
    # raw/unvalidated: it has FULL 5/5 defense parity with the team branch, just
    # implemented inline. Surgical-inline duplication (NOT a shared-helper
    # refactor) is the accepted trade-off to avoid touching the working/tested
    # team path. Only the base root is parameterized (test seam).
    session_id = get_session_id()
    # Also check for multi-session task list ID
    task_list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", session_id)

    if not task_list_id:
        return None

    # F2 (security): task_list_id is USER-CONTROLLED via the
    # CLAUDE_CODE_TASK_LIST_ID env var, then used as a path component. Validate
    # it with the same positive allowlist the team branch already applies to
    # team_name (is_safe_path_component, inside iter_team_task_jsons) so a
    # traversal value ("../secret") cannot escape the tasks base and read *.json
    # outside it. Legitimate task-list IDs (pact-<slug>, UUIDs, multi-session
    # pointers) are single [A-Za-z0-9_-] components and pass unchanged — no solo
    # behavior change for real inputs (the env var must name a ~/.claude/tasks/
    # dir, which the platform generates within that alphabet). Validating BEFORE
    # the path-join also rejects an embedded-NUL value before the .exists() call
    # (which could otherwise raise ValueError on some Python versions). Fail
    # CLOSED (return None) to preserve the None-on-unavailable contract.
    if not is_safe_path_component(task_list_id):
        return None

    base = Path(tasks_base_dir) if tasks_base_dir else (get_claude_config_dir() / "tasks")
    tasks_dir = base / task_list_id

    # R3 (security): the {task_list_id} dir could itself be a SYMLINK that
    # escapes the tasks base. is_safe_path_component (above) rejects '../' in the
    # NAME, but not a safe-NAME dir that resolves out-of-base via a symlink.
    # Mirror the team branch's anchor (iter_team_task_jsons): resolve the dir and
    # assert it stays under the resolved base. A legit (non-symlink) dir resolves
    # to itself and passes unchanged; only an escaping symlink-dir is rejected.
    try:
        tasks_dir.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        return None

    if not tasks_dir.exists():
        return None

    tasks = []
    try:
        for task_file in tasks_dir.glob("*.json"):
            # R4 (hygiene parity): skip dotfile-prefixed JSON. pathlib glob
            # INCLUDES them, but the platform task system never writes them; an
            # attacker dropping one into the user's own tasks dir could otherwise
            # inflate the task count. Mirrors the team branch's dotfile skip.
            if task_file.name.startswith("."):
                continue
            # R3 (security): skip a *.json that is a SYMLINK — it could point
            # outside the tasks base. Mirrors the team branch's per-file
            # is_symlink skip; real task files are regular files, unaffected.
            try:
                if task_file.is_symlink():
                    continue
            except OSError:
                continue
            try:
                content = task_file.read_text(encoding='utf-8')
                task = json.loads(content)
                # R4 (hygiene parity): skip a parse that is not a dict (e.g.
                # [1,2,3]/42/"x" from a malformed-but-valid JSON). Mirrors the
                # team branch's isinstance(dict) yield-guard; downstream readers
                # (find_feature_task etc.) call .get() and need a dict.
                if not isinstance(task, dict):
                    continue
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


def iter_team_task_jsons(
    team_name: str,
    tasks_base_dir: str | None = None,
) -> Iterator[dict]:
    """
    Yield parsed task JSON dicts from ~/.claude/tasks/{team_name}/*.json.

    Path-traversal defense (single source of truth for per-team task
    iteration):
      - team_name validated via is_safe_path_component (positive
        allowlist; rejects empty, non-string, traversal fragments,
        separators, controls, whitespace).
      - tasks_root resolved once; team_dir resolved and asserted under
        tasks_root via relative_to (symlink-escape defense).

    Args:
        team_name: Team identifier; the {team_name} path component.
        tasks_base_dir: Override for the ~/.claude/tasks base dir (testing
            only; production default None resolves the real home path). The
            symlink-escape relative_to assertion still anchors to this base,
            so the path-traversal defense holds for any base. Mirrors the
            institutional test seam in _read_task_counts / read_task_json.

    Pure generator; never raises. Yields nothing on any error
    (unsafe team_name, missing dir, symlink escape, IO error). Individual
    unreadable / unparseable task files are skipped silently. Callers
    treat "no tasks" as fail-open — wake mechanism degrades to baseline
    idle-poll rather than crashing the hook.

    Yields:
        dict per successfully-parsed task JSON file.
    """
    if not is_safe_path_component(team_name):
        return
    try:
        base = Path(tasks_base_dir) if tasks_base_dir else (get_claude_config_dir() / "tasks")
        tasks_root = base.resolve()
    except OSError:
        return
    team_dir = tasks_root / team_name
    try:
        resolved_team_dir = team_dir.resolve()
        resolved_team_dir.relative_to(tasks_root)
    except (OSError, ValueError):
        return  # symlink escape or unreadable path
    if not resolved_team_dir.exists():
        return
    try:
        for task_file in resolved_team_dir.glob("*.json"):
            # Exclude dotfile-prefixed JSON files: pathlib glob includes
            # them, but the platform's task system never writes them.
            # An attacker dropping one into the team's tasks dir could
            # otherwise inflate the active-task count.
            if task_file.name.startswith("."):
                continue
            # Per-file symlink defense: glob() returns symlink entries
            # as their raw paths. Even though resolved_team_dir is
            # asserted under tasks_root, a symlink inside it could
            # resolve outside. Skip symlinks silently — the platform
            # task system writes only regular files.
            try:
                if task_file.is_symlink():
                    continue
            except OSError:
                continue
            try:
                content = task_file.read_text(encoding="utf-8")
                task = json.loads(content)
            except (IOError, OSError, json.JSONDecodeError):
                continue
            if isinstance(task, dict):
                yield task
    except (IOError, OSError):
        return


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
        tasks_base_dir = str(get_claude_config_dir() / "tasks")

    base = Path(tasks_base_dir)

    task_dirs = []
    # Bounded containment parity, guard (1): reject a traversal team_name
    # (mirror iter_team_task_jsons's is_safe_path_component return-on-invalid).
    # An unsafe team_name skips the team dir and falls through to the bare-base
    # read, preserving the fail-open {} contract. Legit pact-<slug>/UUID names
    # are single [A-Za-z0-9_-] components and pass unchanged.
    if team_name and is_safe_path_component(team_name):
        task_dirs.append(base / team_name)
    task_dirs.append(base)

    for task_dir in task_dirs:
        # Bounded containment parity, guard (2): skip a task_dir that resolves
        # OUTSIDE base — e.g. a safe-named team dir that is a SYMLINK escaping
        # the tasks base. Mirror the sibling readers' resolve()+relative_to;
        # fail-open (continue to the bare-base fallback), preserving {}. Only
        # guards (1)+(2) apply to this single-named-file reader; the glob-result
        # hygiene guards (per-file is_symlink / dotfile skip / isinstance(dict))
        # are correctly absent — read_task_json reads ONE named file, never globs.
        try:
            task_dir.resolve().relative_to(base.resolve())
        except (ValueError, OSError):
            continue
        task_file = task_dir / f"{task_id}.json"
        # M2 (security): exists() is INSIDE the try and ValueError is caught.
        # A NUL byte in task_id that slips past the sanitizer raises
        # ValueError('embedded null byte') from the path syscall in exists();
        # catching it degrades to the fail-open {} (same as a missing/malformed
        # file) instead of propagating to a caller's catch-all — e.g. the
        # lifecycle gate, where an uncaught exception skips rule enforcement for
        # the turn (advisory-suppression DoS).
        try:
            if task_file.exists():
                return json.loads(task_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError, ValueError):
            return {}

    return {}
