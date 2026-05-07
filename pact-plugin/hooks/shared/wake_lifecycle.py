"""
Location: pact-plugin/hooks/shared/wake_lifecycle.py
Summary: Shared helper for inbox-wake lifecycle hooks. Counts active teammate
         tasks under a team, applying carve-out filters that match the lead-only
         completion-authority model (signal-tasks and self-complete-exempt
         agentTypes do not count toward the wake-mechanism's "any active work"
         signal).
Used by: pact-plugin/hooks/wake_lifecycle_emitter.py (PostToolUse hook on
         TaskCreate / TaskUpdate), and
         pact-plugin/hooks/session_init.py (resume-with-active-tasks Arm
         directive emission).

Public surface:
- count_active_tasks(team_name) -> int
    Counts tasks in ~/.claude/tasks/{team_name}/*.json where
    _lifecycle_relevant returns True. `team_name` is threaded through
    to `_lifecycle_relevant` so the agentType carve-out can resolve via
    team config.
- _lifecycle_relevant(task, team_name="") -> bool
    Predicate. True iff the task counts toward the active-work tally that
    arms/tears down the wake mechanism.

Contract: pure functions; never raise. Filesystem or JSON parse errors
fail-open as "no active tasks" (returns 0 / False), matching the
fail-open module-wide posture of hook code. The wake mechanism is
opportunistic: a transient failure to read tasks degrades to "no Arm
emit," which falls back to baseline idle-poll delivery — strictly
better than crashing the hook.

Carve-out rules (match shared.intentional_wait.is_self_complete_exempt
and the inline-literal signal-task pattern at agent_handoff_emitter.py):
1. Signal-tasks: metadata.completion_type == "signal" AND
   metadata.type in {"blocker", "algedonic"}. These self-complete
   without team-lead-as-completion-gate; they do not represent
   teammate work the wake mechanism needs to surface.
2. Self-complete-exempt agentTypes: the task owner's team-config
   agentType is in SELF_COMPLETE_EXEMPT_AGENT_TYPES (currently
   {pact-secretary}). Resolution happens via
   `_is_exempt_agent_type(owner, team_name)`, so a secretary spawned
   under any name (`session-secretary`, etc.) reaches the carve-out as
   long as the team config records its agentType.
"""

from typing import Any

from shared.intentional_wait import _is_exempt_agent_type
from shared.task_utils import iter_team_task_jsons

# Signal-task types — inline literal mirrors the convention at
# agent_handoff_emitter.py:78 and task_utils.find_blockers. The carve-out
# applies identically pre/post status transitions, so this constant is
# stable across the lifecycle pre/post derivation.
_SIGNAL_TASK_TYPES = ("blocker", "algedonic")

# Statuses that count as "active teammate work" — the wake mechanism's
# trigger condition. `completed` and `deleted` do not count; nothing else
# is expected in a healthy task list, but unknown statuses are excluded
# by the positive allowlist (conservative: only count statuses we know
# represent in-flight work).
_ACTIVE_STATUSES = ("pending", "in_progress")


def _lifecycle_relevant(task: Any, team_name: str = "") -> bool:
    """
    Return True iff this task counts toward the active-work tally that
    arms/tears down the wake mechanism.

    Returns False on any malformed input (non-dict task, non-dict
    metadata) — conservative: missing fields cannot exempt a real
    active task, but we cannot positively count an unparseable record.

    Status check: task.status must be in {"pending", "in_progress"}.
    Other statuses (completed, deleted, blocked) do not count.

    Carve-outs (apply only on top of a passing status check):
      - Self-complete-exempt agentType: owner's team-config agentType is
        in SELF_COMPLETE_EXEMPT_AGENT_TYPES, resolved via
        `_is_exempt_agent_type(owner, team_name)`. Evaluated before the
        metadata-shape check so that an exempt-agentType task with
        corrupted metadata is still exempt — symmetric with
        shared.intentional_wait.is_self_complete_exempt. With
        `team_name=""` (default) this carve-out short-circuits to False
        (fail-closed): the upstream `_iter_members` returns `[]` on
        empty team_name, which surfaces here as "not exempt → count it".
      - Signal-task pattern: metadata.completion_type == "signal" AND
        metadata.type in {"blocker", "algedonic"}.
    """
    if not isinstance(task, dict):
        return False

    if task.get("status") not in _ACTIVE_STATUSES:
        return False

    # Self-complete-exempt agentType carve-out. Hoisted above the metadata
    # shape check so that an exempt-agentType task with corrupted metadata
    # is still exempt — symmetric with shared.intentional_wait.
    # is_self_complete_exempt. The owner-shape check inside
    # _is_exempt_agent_type fail-closes on non-string owner.
    owner = task.get("owner")
    if isinstance(owner, str) and _is_exempt_agent_type(owner, team_name):
        return False

    metadata = task.get("metadata") or {}
    if not isinstance(metadata, dict):
        # Malformed metadata — conservative: do not silently exempt a real
        # active task on a parse-failed metadata field. Count it.
        return True

    # Signal-task carve-out (inline-literal mirror).
    if metadata.get("completion_type") == "signal":
        if metadata.get("type") in _SIGNAL_TASK_TYPES:
            return False

    return True


def count_active_tasks(team_name: str) -> int:
    """
    Count lifecycle-relevant tasks under ~/.claude/tasks/{team_name}/.

    Iteration + path-traversal defense (allowlist + symlink-escape) is
    delegated to task_utils.iter_team_task_jsons, which is the single
    source of truth for per-team task-file iteration. Individual
    unreadable / unparseable task files are skipped silently; the count
    reflects only successfully-parsed lifecycle-relevant tasks.

    `team_name` is threaded through to `_lifecycle_relevant` so the
    agentType carve-out can resolve via team config.

    Pure function; never raises. Fail-open as "no active tasks" — the
    wake mechanism degrades to baseline idle-poll on read failure rather
    than crashing the calling hook (livelock-safety > observability).
    """
    return sum(
        1
        for task in iter_team_task_jsons(team_name)
        if _lifecycle_relevant(task, team_name)
    )
