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
- is_lead_session(input_data, team_name) -> bool
    Predicate. True iff the current PostToolUse/UserPromptSubmit
    session's `session_id` matches the team's `leadSessionId` from
    team_config.json. Lifted from wake_lifecycle_emitter.py to enable
    reuse by wake_inbox_drain.py (single SSOT). Pure function; never
    raises; silent fail-open (returns False) on missing/empty
    session_id, missing/unsafe team_name, missing config.json,
    malformed JSON, or filesystem error — the teammate session is the
    expected non-lead path so no UI noise on every Task-tool fire.
- _lifecycle_relevant(task, team_name="") -> bool
    Predicate. True iff the task counts toward the active-work tally that
    arms/tears down the wake mechanism.
- _owner_is_known_team_member(owner, team_name, teams_dir=None) -> bool
- _is_lead_owned(owner, team_name, teams_dir=None) -> bool
    Test-contract surface. Thin facades over `_classify_owner`; not
    invoked from `_lifecycle_relevant` at runtime (the predicate
    consumes the consolidated `_OwnerClassification` projection
    directly for single-disk-read efficiency). RETAINED for direct
    test reuse — do NOT delete on the assumption they are dead code.
    See each helper's own docstring and the dedicated coverage in
    `tests/test_wake_lifecycle_teammate_owner_filter.py` for the
    test contract these helpers anchor.

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

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.intentional_wait import WAKE_EXCLUDED_AGENT_TYPES
from shared.intentional_wait import _is_wake_excluded_agent_type  # noqa: F401  # DECOUPLED-CONSTANT pin
from shared.pact_context import _iter_members, _read_team_lead_agent_id
from shared.session_state import is_safe_path_component
from shared.task_utils import iter_team_task_jsons, read_task_json

try:
    from shared import session_journal  # type: ignore
except Exception:  # pragma: no cover - import guard
    session_journal = None  # type: ignore[assignment]

# DECOUPLED-CONSTANT pin: the `_is_wake_excluded_agent_type` import above is
# preserved but unused at runtime after the _classify_owner refactor folded
# its single call site into the consolidated owner-classification path.
# `tests/test_inbox_wake_lifecycle_helper.py::test_helper_imports_shared_helper_from_intentional_wait`
# pins the presence of this exact import line to enforce the DECOUPLED-
# CONSTANT discipline (wake-side helper, NOT the self-completion-side
# `_is_exempt_agent_type`). Keep the import; the test is the contract.

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


@dataclass(frozen=True)
class _OwnerClassification:
    """Projection of the team config relevant to the owner-classification
    decision in ``_lifecycle_relevant``.

    Three fields, derived from ONE read of ``_iter_members(team_name)``
    and ONE read of ``_read_team_lead_agent_id(team_name)``:

    - ``is_known_team_member``: True iff the owner matches some
      member's ``name`` field in the team config.
    - ``is_lead``: True iff the owner matches a member whose ``agentId``
      equals the team's ``leadAgentId``.
    - ``agent_type``: the member's ``agentType`` string when present,
      else None. Used by the wake-excluded-agentType carve-out.
    - ``config_readable``: True iff ``_iter_members`` returned a
      non-empty members list. False signals the count-on-failure
      fall-through path at the call site (members list empty: config
      missing, malformed JSON, or members[] absent) and distinguishes
      it from the orphan case (members non-empty, no name match).

    A frozen dataclass rather than a namedtuple so the field semantics
    are documented inline at the consumer's read site.
    """

    is_known_team_member: bool
    is_lead: bool
    agent_type: str | None
    config_readable: bool


# Dedupe set for the empty-config warning. ONE warn per session per team,
# not per task — the predicate runs on every TaskCreate/TaskUpdate via the
# wake_lifecycle_emitter hook, so per-task warns would flood. Module-level
# set is OK because each hook invocation is a fresh Python process (new
# module state = clean dedupe).
_EMPTY_CONFIG_WARN_TEAMS: set[str] = set()


def _classify_owner(
    owner: Any,
    team_name: str,
    teams_dir: str | None = None,
) -> _OwnerClassification:
    """Read the team config ONCE and project the four fields needed by
    ``_lifecycle_relevant``'s step 3 (wake-excluded agentType) and step 4
    (orphan / lead-owned exclusion).

    Pure function; never raises. Returns an all-False / config_readable=
    False classification on any error path:

    - non-string owner OR empty owner OR non-string team_name OR empty
      team_name → all fields default (no classification can be made).
    - ``_iter_members`` returns ``[]`` (config missing / unreadable /
      malformed / members[] absent) → ``config_readable=False`` so the
      call site can apply its count-on-failure fall-through. The other
      fields stay False / None.
    - members non-empty, no name match → ``is_known_team_member=False``,
      ``is_lead=False``, ``agent_type=None``, ``config_readable=True``.
      The call site uses these flags to exclude the task as an orphan
      owner.

    Single source of truth for the wake-side owner classification.
    Before this projection, ``_lifecycle_relevant`` invoked
    ``_iter_members`` three times per call (once explicitly + once
    inside each of two predicate helpers); now it invokes ``_iter_members``
    once and ``_read_team_lead_agent_id`` once.

    Note on the ``owner: Any`` parameter type: sibling predicates in
    ``intentional_wait.py`` (``_is_exempt_agent_type``,
    ``_is_wake_excluded_agent_type``, ``_is_teachback_exempt_agent_type``)
    use ``owner: str`` because their callers pre-validate the owner
    shape upstream. ``_classify_owner`` IS the validation point — its
    sole runtime caller (``_lifecycle_relevant``, line ~399) passes
    ``task.get("owner")`` directly from the on-disk task JSON without an
    upstream isinstance guard, so the actual contract accepts any
    JSON-readable value. Narrowing the type hint to ``str`` would be a
    type lie that a future type-checker would flag at the call site.
    The internal ``isinstance(owner, str)`` guard below is the
    validation that makes this safe.

    Args:
        owner: Task owner field (raw from ``task.get("owner")`` at the
            call site; may be None, str, int, list, etc.). Internal
            isinstance guard handles non-string shapes.
        team_name: Team name for config path. Empty/non-string returns
            an all-False classification.
        teams_dir: Override teams directory (for testing). Forwarded to
            ``_iter_members`` and ``_read_team_lead_agent_id``. Matches
            the sibling-convention of pact_context.py helpers.
    """
    if not isinstance(team_name, str) or not team_name:
        # Empty team_name disables config classification entirely.
        # config_readable=False here is a stand-in for "no source of
        # truth"; the call site treats it as count-on-failure
        # fall-through identical to the empty-members case.
        return _OwnerClassification(False, False, None, False)

    # Read the config regardless of owner shape so the call site can
    # distinguish "config unreadable → count-on-failure fall-through"
    # from "config readable but owner is non-string / orphan →
    # exclude". A bad-owner task under a readable config is an orphan
    # (excluded), not a config-read failure.
    members = _iter_members(team_name, teams_dir=teams_dir)
    if not members:
        # Empty members list signals the count-on-failure fall-through
        # at the call site (members[] empty ⇒ unreadable for
        # classification purposes). The rationale for that posture
        # lives in _lifecycle_relevant's step-4 elif body where the
        # logic is exercised.
        return _OwnerClassification(False, False, None, False)

    if not isinstance(owner, str) or not owner:
        # Config IS readable, but the owner is missing or non-string.
        # Return is_known=False so the call site excludes the task as
        # an orphan / unowned; config_readable=True signals "the
        # exclusion is intentional, not the count-on-failure path."
        return _OwnerClassification(False, False, None, True)

    lead_agent_id = _read_team_lead_agent_id(team_name, teams_dir=teams_dir)
    is_known = False
    is_lead = False
    agent_type: str | None = None
    for member in members:
        if member.get("name") != owner:
            continue
        is_known = True
        member_agent_id = member.get("agentId")
        if lead_agent_id and member_agent_id == lead_agent_id:
            is_lead = True
        raw_agent_type = member.get("agentType")
        if isinstance(raw_agent_type, str):
            agent_type = raw_agent_type
        # First matching member wins. Duplicate-name configs are
        # malformed; the first match is the canonical record.
        break

    return _OwnerClassification(is_known, is_lead, agent_type, True)


def _owner_is_known_team_member(
    owner: Any,
    team_name: str,
    teams_dir: str | None = None,
) -> bool:
    """Return True iff ``owner`` matches some member's ``name`` field in
    the team config.

    Answers "does the config record a member with this name?" — distinct
    from "is this owner a teammate (not the lead)?", which is the
    conjunction of ``is_known_team_member`` AND ``not is_lead``. Both
    are surfaced via ``_classify_owner``.

    Pure function; never raises. Returns False on every error path:
    non-string owner, empty owner string, empty team_name, empty members
    list (config unreadable or members[] missing/empty), or no name
    match. Fail-CLOSED-symmetric internally — the call site
    (``_lifecycle_relevant``, step 4) handles its own count-on-failure
    posture when the members list is empty; see the inline rationale
    comment inside the step-4 ``elif team_name:`` branch for the
    asymmetry between this helper's fail-CLOSED behavior and the call
    site's fall-through. Step labels match the ``# Step N:`` anchors
    in ``_lifecycle_relevant``.

    Retained as a public-style helper for test reuse and future callers;
    after the ``_classify_owner`` refactor, ``_lifecycle_relevant`` uses
    the consolidated projection directly and no longer calls this
    helper at runtime. The ``owner: Any`` parameter type and ``teams_dir``
    forwarding mirror the ``_classify_owner`` signature; see that
    helper's docstring for the rationale on accepting ``Any``.
    """
    return _classify_owner(owner, team_name, teams_dir=teams_dir).is_known_team_member


def _is_lead_owned(
    owner: Any,
    team_name: str,
    teams_dir: str | None = None,
) -> bool:
    """Return True iff ``owner`` matches a member of the team config
    whose ``agentId`` equals the team's ``leadAgentId``.

    Pure function; never raises. Returns False on every error path:
    non-string owner, empty owner string, empty team_name, empty
    leadAgentId (config unreadable or field missing), empty members
    list, or no member matches both name and lead-agentId. Fail-CLOSED-
    symmetric internally — the call site (``_lifecycle_relevant``,
    step 4) handles its own count-on-failure posture when the members
    list is empty; see the inline rationale comment inside the step-4
    ``elif team_name:`` branch for the asymmetry between this helper's
    fail-CLOSED behavior and the call site's fall-through. Step labels
    match the ``# Step N:`` anchors in ``_lifecycle_relevant``.

    Retained as a public-style helper for test reuse; after the
    ``_classify_owner`` refactor, ``_lifecycle_relevant`` uses the
    consolidated projection directly and no longer calls this helper at
    runtime. The ``owner: Any`` parameter type and ``teams_dir``
    forwarding mirror the ``_classify_owner`` signature; see that
    helper's docstring for the rationale on accepting ``Any``.
    """
    return _classify_owner(owner, team_name, teams_dir=teams_dir).is_lead


def _warn_empty_team_config_once(team_name: str) -> None:
    """Emit a ONE-shot per-team warning when the empty-members fall-
    through fires at ``_lifecycle_relevant``'s step 4.

    The fall-through is operationally recoverable (over-arm = extra
    empty scans) but indicates the team config is unreadable or
    malformed — an observability gap worth surfacing. Without this
    signal, a permanently-unreadable config would silently keep the
    wake mechanism armed for the entire session and only manifest as
    "scan-pending-tasks fires don't drive teardown."

    Pure-after-side-effect; never raises (warnings are best-effort by
    design). Dedupe is per-team module-level — each hook invocation is
    a fresh Python process so the set is empty at start and at most one
    warning per team fires per process. The wake-mechanism hook runs on
    every TaskCreate/TaskUpdate, so per-task warnings would flood; per-
    team-per-process is the tightest natural granularity.

    Routing: prefers ``shared.session_journal.append_event`` with event
    type ``wake_tally_warn`` (unknown event types bypass per-type
    schema validation by design — see ``_REQUIRED_FIELDS_BY_TYPE``
    docstring in session_journal). Falls back to stderr with a
    ``[WAKE-TALLY WARN]`` prefix if the journal is unreachable
    (uninitialized session, import failure, write failure).
    """
    if not isinstance(team_name, str) or not team_name:
        return
    if team_name in _EMPTY_CONFIG_WARN_TEAMS:
        return
    _EMPTY_CONFIG_WARN_TEAMS.add(team_name)

    journal_ok = False
    if session_journal is not None:
        try:
            event = session_journal.make_event(
                "wake_tally_warn",
                team_name=team_name,
                reason="empty_team_config_fail_conservative",
                detail=(
                    "_iter_members returned [] for team_name; step-4 "
                    "owner-check skipped; falling through to count "
                    "(fail-CONSERVATIVE)."
                ),
            )
            journal_ok = bool(session_journal.append_event(event))
        except Exception:
            journal_ok = False

    if not journal_ok:
        try:
            print(
                "[WAKE-TALLY WARN] team_name=%r: _iter_members returned [] "
                "at _lifecycle_relevant step 4; falling through to count "
                "(fail-CONSERVATIVE)."
                % team_name,
                file=sys.stderr,
            )
        except Exception:
            # Pure-never-raises contract: swallow even stderr failures.
            pass


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
        The check applies a count-on-failure posture at the call site
        when the team config is unreadable (empty members list short-
        circuits to "count") — see the inline comment block at step 4
        for the asymmetry rationale.
      - Signal-task pattern: metadata.completion_type == "signal" AND
        metadata.type in {"blocker", "algedonic"}. Applied AFTER the
        teammate-owner check; a teammate-owned signal task passes the
        owner check and is excluded here.
    """
    # Step 1: input shape gate.
    if not isinstance(task, dict):
        return False

    # Step 2: status gate. Only pending / in_progress tasks count.
    if task.get("status") not in _ACTIVE_STATUSES:
        return False

    # Steps 3 + 4 share a single read of the team config via
    # `_classify_owner`. The projection is computed ONCE and the wake-
    # excluded-agentType check (step 3) plus the orphan / lead-owned
    # check (step 4) both consume its fields. Before this refactor,
    # _lifecycle_relevant invoked `_iter_members` three times per call
    # (once explicitly + once inside each of two predicate helpers);
    # now it invokes `_iter_members` once and `_read_team_lead_agent_id`
    # once via `_classify_owner`. The wake-tally posture asymmetry
    # vs the sibling predicates in intentional_wait.py is documented
    # inline at step 4 below (where the executable check lives).
    #
    # DECOUPLED-CONSTANT DISCIPLINE: the agentType check below consults
    # `WAKE_EXCLUDED_AGENT_TYPES` (imported from intentional_wait.py),
    # NOT `SELF_COMPLETE_EXEMPT_AGENT_TYPES`. The two constants answer
    # different questions for different consumers — self-completion
    # asks "may this owner self-complete without lead inspection?" and
    # wake-exclusion asks "should this owner's active work fire the
    # lead's inbox-watch Monitor?" `WAKE_EXCLUDED_AGENT_TYPES` is empty
    # by design (every teammate, including secretary, counts toward
    # the wake tally so the lead receives all replies); the empty set
    # is load-bearing for secretary message coverage. DO NOT recouple
    # the two policies by aliasing the constants or by re-importing
    # `_is_exempt_agent_type` here.
    owner = task.get("owner")
    if not team_name:
        # Empty team_name (default-arg / fixture path) skips both
        # step 3 and step 4 — same fall-through behavior as the empty-
        # members case below. Documented here so a reader investigating
        # "what happens when team_name is empty?" sees the rationale at
        # the call site.
        classification = _OwnerClassification(False, False, None, False)
    else:
        classification = _classify_owner(owner, team_name)

    # Step 3: wake-excluded agentType carve-out. Hoisted above the
    # metadata-shape check (step 5) so that a wake-excluded agentType
    # task with corrupted metadata is still excluded once the
    # WAKE_EXCLUDED_AGENT_TYPES set is non-empty. Currently the set is
    # empty so this check is a no-op for all owners; the call is
    # retained so a future hypothetical wake-only-noisy agentType can
    # be added to the constant without re-introducing the predicate.
    if (
        classification.agent_type is not None
        and classification.agent_type in WAKE_EXCLUDED_AGENT_TYPES
    ):
        return False

    # Step 4: teammate-owner check. Tasks count toward the wake tally
    # only when the owner is a known team member AND not the team-lead.
    # Unowned umbrella tasks (created by /PACT:orchestrate,
    # /PACT:comPACT, /PACT:peer-review), orphan-owner tasks, and
    # team-lead-owned tasks all return False here. The predicate flow
    # places this BEFORE the signal-task metadata carve-out (step 6)
    # so a teammate-owned signal task passes step 4 and is excluded at
    # step 6; an unowned/orphan/lead-owned signal task (structurally
    # impossible today, but defended against) is excluded at step 4.
    # The failure-mode rationale documenting why the unreadable-config
    # branch counts (rather than excluding) lives inside the `elif`
    # body below so a body-only revert deletes both the logic and its
    # justification together.
    if classification.config_readable:
        if not isinstance(owner, str) or not owner:
            return False
        if not classification.is_known_team_member:
            return False
        if classification.is_lead:
            return False
    elif team_name:
        # Fail-CONSERVATIVE: the team config is unreadable (members
        # list is empty), so `_classify_owner` returned
        # `config_readable=False` and BOTH step 3 and step 4 fall
        # through to step 5. The sibling predicates in
        # intentional_wait.py fail-CLOSED on read errors (return False),
        # but the failure mode here inverts the priority: under-arm
        # (silent teardown loss while teammate work is in flight) is
        # unrecoverable; over-arm (extra empty scans) is recoverable on
        # the next state change. The wake-mechanism's purpose — never
        # strand a teammate whose SendMessage needs to wake the lead —
        # is load-bearing here, so we fail toward counting on every
        # config-read failure. The audit-anchor invariant test pins
        # these phrases (Fail-CONSERVATIVE, under-arm, unrecoverable)
        # inside this `elif` body so a future agent-reader deleting
        # step 4 also deletes the rationale rather than leaving an
        # orphan comment.
        #
        # One-shot observability warn per team per process so a
        # permanently-unreadable config doesn't silently keep the wake
        # mechanism armed for the entire session.
        _warn_empty_team_config_once(team_name)

    # Step 5: metadata shape gate.
    metadata = task.get("metadata") or {}
    if not isinstance(metadata, dict):
        # Malformed metadata — conservative: do not silently exempt a real
        # active task on a parse-failed metadata field. Count it.
        return True

    # Step 6: signal-task carve-out (inline-literal mirror of the
    # convention at agent_handoff_emitter.py / task_utils.find_blockers).
    if metadata.get("completion_type") == "signal":
        if metadata.get("type") in _SIGNAL_TASK_TYPES:
            return False

    # Step 7: counted toward active-work tally.
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


def is_lead_session(input_data: Any, team_name: str) -> bool:
    """
    Return True iff the current session is the team's lead session.

    Reads `session_id` from the hook stdin payload and `leadSessionId`
    from ~/.claude/teams/{team_name}/config.json. Single source of truth
    for the wake-mechanism's lead-session locality check; consumed by
    `wake_lifecycle_emitter._decide_directive` (PostToolUse) and by
    `wake_inbox_drain.main` (UserPromptSubmit). Mirrors the Lead-Session
    Guard pattern used by the start-pending-scan / stop-pending-scan
    skill bodies (Layer 1 of the defense-in-depth model); this
    hook-level check is Layer 0, preventing directive emission to
    teammate sessions at the emission source. team_config is the
    single source of truth for lead identity.

    Pure function; never raises. Returns False on missing/empty
    session_id, missing/unsafe team_name, missing config.json,
    malformed JSON, or filesystem error. The teammate session is the
    expected non-lead path; silent fail-open avoids UI noise on every
    teammate hook fire.
    """
    if not isinstance(input_data, dict):
        return False
    raw_session_id = input_data.get("session_id")
    if not isinstance(raw_session_id, str) or not raw_session_id:
        return False
    if not isinstance(team_name, str) or not team_name:
        return False
    if not is_safe_path_component(team_name):
        return False
    try:
        config_path = (
            Path.home() / ".claude" / "teams" / team_name / "config.json"
        )
        if not config_path.exists():
            return False
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    lead_session_id = data.get("leadSessionId")
    if not isinstance(lead_session_id, str) or not lead_session_id:
        return False
    return raw_session_id == lead_session_id
