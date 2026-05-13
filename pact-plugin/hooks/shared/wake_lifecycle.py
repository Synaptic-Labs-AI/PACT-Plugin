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
   in WAKE_EXCLUDED_AGENT_TYPES (currently EMPTY by design — every
   agentType, including pact-secretary, counts toward the wake tally
   so the lead receives messages from all teammates promptly).
   Resolution happens via `_is_wake_excluded_agent_type(owner,
   team_name)`. WAKE_EXCLUDED_AGENT_TYPES is intentionally a SEPARATE
   constant from SELF_COMPLETE_EXEMPT_AGENT_TYPES so the two policies
   (self-completion exemption vs wake-mechanism count exclusion) can
   diverge without coupling — and they DO diverge today
   (SELF_COMPLETE_EXEMPT_AGENT_TYPES contains pact-secretary;
   WAKE_EXCLUDED_AGENT_TYPES is empty). See the constant docstring in
   shared.intentional_wait for the divergence rationale.
"""

from typing import Any

from shared.intentional_wait import _is_wake_excluded_agent_type
from shared.pact_context import _iter_members, _read_team_lead_agent_id
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


def _owner_is_team_member(owner: Any, team_name: str) -> bool:
    """Return True iff ``owner`` matches some member's ``name`` field in
    the team config.

    Pure function; never raises. Returns False on every error path:
    non-string owner, empty owner string, empty team_name, empty members
    list (config unreadable or members[] missing/empty), or no name
    match. Fail-CLOSED-symmetric internally — the call site
    (``_lifecycle_relevant``) is responsible for the fail-CONSERVATIVE
    short-circuit when the members list is empty (see the inline comment
    block at step 4 of ``_lifecycle_relevant`` for the asymmetry
    rationale).
    """
    if not isinstance(owner, str) or not owner:
        return False
    if not isinstance(team_name, str) or not team_name:
        return False
    members = _iter_members(team_name)
    if not members:
        return False
    for member in members:
        if member.get("name") == owner:
            return True
    return False


def _is_lead_owned(owner: Any, team_name: str) -> bool:
    """Return True iff ``owner`` matches a member of the team config
    whose ``agentId`` equals the team's ``leadAgentId``.

    Pure function; never raises. Returns False on every error path:
    non-string owner, empty owner string, empty team_name, empty
    leadAgentId (config unreadable or field missing), empty members
    list, or no member matches both name and lead-agentId. Fail-CLOSED-
    symmetric internally — the call site (``_lifecycle_relevant``) is
    responsible for the fail-CONSERVATIVE short-circuit when the members
    list is empty (see the inline comment block at step 4 of
    ``_lifecycle_relevant`` for the asymmetry rationale).
    """
    if not isinstance(owner, str) or not owner:
        return False
    if not isinstance(team_name, str) or not team_name:
        return False
    lead_agent_id = _read_team_lead_agent_id(team_name)
    if not lead_agent_id:
        return False
    members = _iter_members(team_name)
    if not members:
        return False
    for member in members:
        if member.get("name") == owner and member.get("agentId") == lead_agent_id:
            return True
    return False


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
        `_is_wake_excluded_agent_type(owner, team_name)`.
        WAKE_EXCLUDED_AGENT_TYPES is currently EMPTY by design — every
        agentType counts toward the wake tally so the lead receives
        messages from all teammates (including secretary) promptly.
        The carve-out call is retained so a future hypothetical wake-
        only-noisy agentType can be added to the constant without
        re-introducing the predicate. Evaluated before the metadata-
        shape check so that a wake-excluded agentType task with
        corrupted metadata is still excluded once the set is non-empty.
      - Teammate-owner check: tasks count ONLY if their owner is a
        team-config member AND not the team-lead. Unowned umbrella
        tasks (created by workflow commands), orphan-owner tasks (owner
        string doesn't match any current member), and team-lead-owned
        tasks (umbrella / feature / phase records) all return False.
        The check is fail-CONSERVATIVE at the call site when the team
        config is unreadable (empty members list short-circuits to
        "count") — see the inline comment block at step 4 for the
        asymmetry rationale.
      - Signal-task pattern: metadata.completion_type == "signal" AND
        metadata.type in {"blocker", "algedonic"}. Applied AFTER the
        teammate-owner check; a teammate-owned signal task passes the
        owner check and is excluded here.
    """
    if not isinstance(task, dict):
        return False

    if task.get("status") not in _ACTIVE_STATUSES:
        return False

    # Wake-excluded agentType carve-out. Hoisted above the metadata
    # shape check so that a wake-excluded agentType task with corrupted
    # metadata is still excluded once the set is non-empty. The
    # owner-shape check inside _is_wake_excluded_agent_type fail-closes
    # on non-string owner.
    #
    # DECOUPLED-CONSTANT DISCIPLINE: WAKE_EXCLUDED_AGENT_TYPES is a
    # SEPARATE constant from SELF_COMPLETE_EXEMPT_AGENT_TYPES, and the
    # two now DIVERGE: SELF_COMPLETE_EXEMPT contains pact-secretary
    # (memory-save tasks self-complete without lead inspection),
    # WAKE_EXCLUDED is empty (every teammate, including secretary,
    # counts toward the wake tally so the lead receives all replies).
    # The constants answer different questions for different consumers
    # — self-completion asks "may this owner self-complete without
    # lead inspection?" and wake-exclusion asks "should this owner's
    # active work fire the lead's inbox-watch Monitor?" The empty
    # WAKE_EXCLUDED set is load-bearing for secretary message coverage
    # — re-adding pact-secretary here would re-introduce the
    # secretary-window failure mode where the Monitor tore down before
    # the lead could read the secretary's reply. DO NOT recouple by
    # re-importing _is_exempt_agent_type here; the divergence is
    # architectural intent, not a transient state.
    owner = task.get("owner")
    if isinstance(owner, str) and _is_wake_excluded_agent_type(owner, team_name):
        return False

    # Teammate-owner check (step 4): tasks count toward the wake tally
    # only when the owner is a non-lead member of the team. Unowned
    # umbrella tasks (created by /PACT:orchestrate, /PACT:comPACT,
    # /PACT:peer-review), orphan-owner tasks, and team-lead-owned tasks
    # all return False here. The predicate flow places this BEFORE the
    # signal-task metadata carve-out (step 6) so a teammate-owned signal
    # task passes step 4 and is excluded at step 6; an unowned/orphan/
    # lead-owned signal task (structurally impossible today, but
    # defended against) is excluded at step 4.
    #
    # Fail-CONSERVATIVE: if the team config is unreadable (members list is
    # empty), skip the owner-check and treat as "count toward tally." The
    # sibling predicates in intentional_wait.py fail-CLOSED on read errors
    # (return False), but the failure mode here inverts the priority:
    # under-arm (silent teardown loss while teammate work is in flight) is
    # unrecoverable; over-arm (extra empty scans) is recoverable on the next
    # state change. The wake-mechanism's purpose — never strand a teammate
    # whose SendMessage needs to wake the lead — is load-bearing here, so we
    # fail toward counting on every config-read failure.
    if team_name:
        members = _iter_members(team_name)
        if members:
            if not isinstance(owner, str) or not owner:
                return False
            if not _owner_is_team_member(owner, team_name):
                return False
            if _is_lead_owned(owner, team_name):
                return False
        # else: members list empty → fail-CONSERVATIVE; fall through.

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
