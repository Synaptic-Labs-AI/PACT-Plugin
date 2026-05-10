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
- has_same_teammate_continuation(completed_task, team_name) -> bool
    Predicate. True iff the just-completed task has at least one task
    in its continuation chain (`blocks` field on disk; falls back to
    `addBlocks` for forward-compat) whose owner equals the completed
    task's owner AND `_lifecycle_relevant` returns True. Consumers
    (the deferred-Teardown branch in
    `wake_lifecycle_emitter._decide_directive`) use this to suppress
    the 1->0 Teardown emit when a same-teammate continuation is staged
    for handoff. Pure function; never raises; fail-closed (returns
    False on every error path so Teardown emits unchanged).
- _lifecycle_relevant(task, team_name="") -> bool
    Predicate. True iff the task counts toward the active-work tally that
    arms/tears down the wake mechanism.

Contract: pure functions; never raise. Filesystem or JSON parse errors
fail-open as "no active tasks" (returns 0 / False), matching the
fail-open module-wide posture of hook code. The wake mechanism is
opportunistic: a transient failure to read tasks degrades to "no Arm
emit," which falls back to baseline idle-poll delivery — strictly
better than crashing the hook.

Carve-out rules:
1. Signal-tasks: metadata.completion_type == "signal" AND
   metadata.type in {"blocker", "algedonic"}. Inline-literal mirror of
   the pattern at agent_handoff_emitter.py / task_utils.find_blockers.
   These self-complete without team-lead-as-completion-gate; they do
   not represent teammate work the wake mechanism needs to surface.
2. Wake-excluded agentTypes: the task owner's team-config agentType is
   in WAKE_EXCLUDED_AGENT_TYPES (currently {pact-secretary}).
   Resolution happens via `_is_wake_excluded_agent_type(owner,
   team_name)`, so a secretary spawned under any name
   (`session-secretary`, etc.) reaches the carve-out as long as the
   team config records its agentType. WAKE_EXCLUDED_AGENT_TYPES is
   intentionally a SEPARATE constant from
   SELF_COMPLETE_EXEMPT_AGENT_TYPES so the two policies (self-
   completion exemption vs wake-mechanism count exclusion) can diverge
   without coupling. See the constant docstring in
   shared.intentional_wait for the semantic rationale.
"""

from typing import Any

from shared.intentional_wait import _is_wake_excluded_agent_type
from shared.task_utils import iter_team_task_jsons, read_task_json

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
      - Wake-excluded agentType: owner's team-config agentType is
        in WAKE_EXCLUDED_AGENT_TYPES, resolved via
        `_is_wake_excluded_agent_type(owner, team_name)`. Evaluated
        before the metadata-shape check so that a wake-excluded
        agentType task with corrupted metadata is still excluded.
        With `team_name=""` (default) this carve-out short-circuits to
        False (fail-closed): the helper's own empty-team_name guard
        returns False BEFORE `_iter_members` is reached, so the
        consumer-level outcome here is "not excluded → count it"
        without any team-config read.
      - Signal-task pattern: metadata.completion_type == "signal" AND
        metadata.type in {"blocker", "algedonic"}.
    """
    if not isinstance(task, dict):
        return False

    if task.get("status") not in _ACTIVE_STATUSES:
        return False

    # Wake-excluded agentType carve-out. Hoisted above the metadata
    # shape check so that a wake-excluded agentType task with corrupted
    # metadata is still excluded. The owner-shape check inside
    # _is_wake_excluded_agent_type fail-closes on non-string owner.
    #
    # DECOUPLED-CONSTANT DISCIPLINE: WAKE_EXCLUDED_AGENT_TYPES is a
    # SEPARATE constant from SELF_COMPLETE_EXEMPT_AGENT_TYPES, even
    # though their membership currently coincides at {"pact-secretary"}.
    # The two constants answer different questions for different
    # consumers — the self-completion exemption asks "may this owner
    # self-complete without lead inspection?" and the wake-mechanism
    # exclusion asks "should this owner's active work fire the lead's
    # inbox-watch Monitor?" Future divergence (an agentType added to
    # one but not the other) is the architectural reason for the
    # separation. DO NOT recouple by re-importing _is_exempt_agent_type
    # here; that would silently re-link the two policies and break the
    # next time they need to diverge.
    owner = task.get("owner")
    if isinstance(owner, str) and _is_wake_excluded_agent_type(owner, team_name):
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


# Defer Teardown when the just-completed task has a same-teammate-owned
# active continuation chain. Two distinct values over emitting Teardown
# and letting a later TaskUpdate(in_progress) re-Arm:
#   1. Cleaner audit trail — the false-positive 1->0 transition signal
#      never fires, so observers don't see a phantom Monitor-down event.
#   2. Zero Monitor-down window — between the eager Teardown and the
#      teammate's claim of the next task, inbound SendMessages would
#      not wake the lead. Deferring Teardown closes this window.
# Reuses `_lifecycle_relevant(blocked_task, team_name)` for unified
# active-status + carve-out semantics, so any future expansion of
# WAKE_EXCLUDED_AGENT_TYPES is handled transparently.
#
# Field-name discipline (load-bearing): the on-disk task .json stores
# the resolved continuation list under `blocks` (a list of task IDs).
# `addBlocks` is the TaskUpdate API parameter (additive verb) — it
# appears in the request payload but is normalized into `blocks` on the
# stored record (and is typically `null` on disk after the merge). This
# predicate reads `blocks` as the load-bearing field and falls back to
# `addBlocks` only as a forward-compat / test-fixture convenience.
# Consumer: wake_lifecycle_emitter._decide_directive deferred-Teardown
# branch.
def has_same_teammate_continuation(completed_task: Any, team_name: str) -> bool:
    """
    Return True iff the just-completed task has at least one task in its
    continuation chain whose owner equals the completed task's owner
    AND `_lifecycle_relevant` returns True.

    Args:
        completed_task: The task dict (as returned by `read_task_json`)
            for the task whose completion triggered the Teardown
            evaluation. May be `{}` or any non-dict on read failure.
        team_name: Team name for scoped task lookup. Threaded through to
            `read_task_json` and `_lifecycle_relevant` so the agentType
            carve-out can resolve via team config.

    Returns:
        True iff a same-teammate active continuation exists. False on
        every error path (non-dict input, missing owner, missing /
        non-list continuation chain, non-string blocked id, read
        failure on blocked task, no matching continuation found).

    Pure function; never raises. Fail-closed (return False) means the
    caller does NOT defer Teardown — the existing 1->0 emit behavior is
    preserved on every failure mode. Teardown is idempotent in the
    skill layer, so a fail-closed over-emit is benign; the alternative
    (fail-open to "defer") would silently suppress legitimate Teardowns
    on parse errors and is the worse failure mode here.

    Field-name precedence: reads `blocks` first (the resolved on-disk
    field; populated after the platform merges any `addBlocks` API
    parameter into the stored record), then falls back to `addBlocks`
    for forward-compat with hypothetical fixtures or platform versions
    that surface the additive parameter directly. Both must be lists of
    task ID strings; non-list values fail-close to False.

    ANY-match semantics: a single same-teammate active continuation in
    the chain is sufficient to defer. If the chain mixes a same-
    teammate active task with signal-tasks or exempt-agentType tasks,
    the same-teammate active match still defers — `_lifecycle_relevant`
    excludes the non-matching entries from consideration.
    """
    try:
        if not isinstance(completed_task, dict):
            return False

        owner = completed_task.get("owner")
        if not isinstance(owner, str) or not owner:
            return False

        blocked_ids = completed_task.get("blocks")
        if not isinstance(blocked_ids, list):
            blocked_ids = completed_task.get("addBlocks")
            if not isinstance(blocked_ids, list):
                return False

        for blocked_id in blocked_ids:
            if not isinstance(blocked_id, str) or not blocked_id:
                continue
            blocked_task = read_task_json(blocked_id, team_name)
            if not isinstance(blocked_task, dict) or not blocked_task:
                continue
            blocked_owner = blocked_task.get("owner")
            if not isinstance(blocked_owner, str) or blocked_owner != owner:
                continue
            if _lifecycle_relevant(blocked_task, team_name):
                return True

        return False
    except Exception:
        return False
