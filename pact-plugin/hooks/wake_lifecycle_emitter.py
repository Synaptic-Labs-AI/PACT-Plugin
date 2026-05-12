#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wake_lifecycle_emitter.py
Summary: PostToolUse hook that emits start-pending-scan/stop-pending-scan
         directives for the cron-based pending-scan command pair on
         first/last active-task transitions.
Used by: hooks.json PostToolUse hook with matcher
         `TaskCreate|TaskUpdate`.

Lifecycle automation:
- On TaskCreate that lands while the team has at least one
  lifecycle-relevant active task, emit a start-pending-scan
  directive instructing the lead to invoke
  Skill("PACT:start-pending-scan"). Idempotency is enforced in
  the skill body (CronList exact-suffix-match check); re-emit on
  every TaskCreate is benign because the skill no-ops if the
  /PACT:scan-pending-tasks cron is already registered.
- On TaskUpdate(status == in_progress) when the team has at least
  one lifecycle-relevant active task, emit a start-pending-scan
  directive. Re-Arm path covering cold-start (initial Arm never
  fired), post-Teardown recovery (eager 1->0 Teardown removed the
  cron entry), mid-session resume, and any cron-died-silently
  edge cases categorically. The CronList match in the skill is the
  single source of idempotency truth — no hook-side state file.
- On TaskUpdate(status in {completed, deleted}) that drives the
  team's lifecycle-relevant active-task count to zero, emit a
  stop-pending-scan directive instructing the lead to invoke
  Skill("PACT:stop-pending-scan"). Both terminal statuses end
  active work and are treated symmetrically. EXCEPTION: when the
  just-completed task has a same-teammate-owned active
  continuation in its `blocks` chain (per
  `has_same_teammate_continuation`, which reads the resolved
  on-disk `blocks` field with `addBlocks` as forward-compat
  fallback), the Teardown is deferred — the teammate is staged to
  claim the next task imminently, so the 1->0 transient is
  suppressed to avoid a phantom cron-down audit signal and a
  brief cron-down window for inbound completion-authority work.
- On any other tool fire (TaskUpdate with neither a terminal-
  status nor a pending->in_progress transition, TaskCreate when
  the count is zero, terminal-status TaskUpdate leaving residual
  active tasks or a same-teammate continuation): no directive
  emitted.

Lead-Session Guard (Layer 0 — defense-in-depth):
- Every directive emission is gated by `_is_lead_session`, which
  verifies that the current PostToolUse session's `session_id`
  matches the team's `leadSessionId` from team_config.json.
  Teammate sessions never receive the Arm/Teardown directives —
  hook-level filtering at the emission source, correct-by-
  construction rather than relying on the skill body's
  Lead-Session Guard (Layer 1) as the primary defense. The skill-
  body guard remains as backstop for user-typed manual invocation
  from a teammate session.

Transition detection (post-only):
- post = count_active_tasks(team_name) — the count AFTER the tool's
  effect is on disk. count_active_tasks already filters out signal-
  tasks and self-complete-exempt agents, so post is the lifecycle-
  relevant count.
- TaskCreate + post >= 1 → emit Arm. The skill body's CronList
  match is the single idempotency layer; the hook emits
  unconditionally on the lifecycle transition, the skill decides
  whether the work needs doing. This is the architectural change
  from the Monitor era (which used a STATE_FILE freshness window
  in the hook to short-circuit redundant directive emits) — under
  cron, the per-emit context-budget cost is small and the
  single-source-of-truth idempotency in the skill body is
  architecturally cleaner.
- TaskUpdate(status == in_progress) + post >= 1 → emit Arm.
  Mirrors the TaskCreate Arm semantics. Predicate is single-source
  on `tool_input.status` per the empirical fixture constraint at
  `tests/fixtures/wake_lifecycle/task_update_production_shape.json`
  (FLAT tool_response, no statusChange.from field).
- TaskUpdate(status in {completed, deleted}) + post == 0 + NO same-
  teammate continuation → emit Teardown. Skill's Teardown is
  idempotent (no-op if no /PACT:scan-pending-tasks cron is
  registered), so over-eager emission on edge cases (terminal-
  status update of a never-counted signal-task while post==0) is
  benign. The same-teammate continuation guard
  (`has_same_teammate_continuation`) defers Teardown when the
  completing task has at least one task in its `blocks` chain (the
  resolved on-disk field; `addBlocks` is the additive TaskUpdate
  API parameter, normalized into `blocks` on the stored record)
  whose owner matches and which passes `_lifecycle_relevant`,
  suppressing the phantom 1->0 transient in canonical Two-Task
  Dispatch handoffs.
- Any other tool fire (TaskUpdate with neither in_progress nor
  terminal status, TaskCreate at post == 0, terminal-status
  TaskUpdate at post > 0, terminal-status TaskUpdate at post == 0
  with same-teammate continuation): no-op.

The Arm threshold (post >= 1) and `session_init.py`'s SessionStart
Arm threshold (active_count > 0) apply the same minimum-positive-count
gate at both Arm sites, so a single mental model covers both surfaces.

Output schema (load-bearing):
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",  # REQUIRED — silent rejection without
    "additionalContext": "<directive prose>"
  }
}
The `hookEventName` field is REQUIRED on PostToolUse outputs.
Empirically verified during the Phase 0 routing probe: missing
`hookEventName` triggers silent schema rejection at the platform layer.

Fail-open invariant: every code path exits 0 with `suppressOutput`
sentinel on parse errors, missing fields, or unexpected exceptions.
The scan mechanism is opportunistic — a crashed lifecycle hook degrades
to "no Arm/Teardown emit," which falls back to baseline idle-poll
delivery. Livelock-safety > observability for hook code emitting on
every Task-tool fire.

Input: JSON from stdin with tool_name, tool_input, tool_response.
Output: hookSpecificOutput with additionalContext on transitions;
        suppressOutput sentinel on no-op paths.
"""

import json
import sys
from pathlib import Path
from typing import Any

# Ensure shared package import resolves under the hooks directory.
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import shared.pact_context as pact_context
from shared.pact_context import get_team_name
from shared.session_state import is_safe_path_component
from shared.task_utils import read_task_json
from shared.tool_response import extract_tool_response
from shared.wake_lifecycle import count_active_tasks, has_same_teammate_continuation

# Suppress the false "hook error" UI surface on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Maximum stdin payload size in bytes. PostToolUse payloads carry
# tool_input + tool_response JSON; even verbose Task-tool fires fit
# comfortably under 1MB. A larger payload is a defense-in-depth
# rejection signal (parser amplification / memory-exhaustion vector).
_MAX_PAYLOAD_BYTES = 1024 * 1024

# Directive prose — verbatim text emitted via additionalContext on
# transitions. Imperative voice; references the canonical command-pair
# slugs `PACT:start-pending-scan` (Arm role) and `PACT:stop-pending-scan`
# (Teardown role); idempotency / best-effort clauses prevent the lead
# from adding their own conditional self-diagnosis (per the
# unconditional-directive discipline: emit identical prose every fire —
# the orchestrator is not authorized to second-guess directive
# applicability). Idempotency is enforced in the skill body via
# CronList exact-suffix-match; there is no hook-side STATE_FILE.
_ARM_DIRECTIVE = (
    'First active teammate task created. '
    'Invoke Skill("PACT:start-pending-scan") before any further teammate '
    'dispatch. Idempotent — no-op if a /PACT:scan-pending-tasks cron is '
    'already registered.'
)

_TEARDOWN_DIRECTIVE = (
    'Last active teammate task completed. '
    'Invoke Skill("PACT:stop-pending-scan") to delete the '
    '/PACT:scan-pending-tasks cron. Best-effort — tolerates a cron that '
    'was already auto-deleted (7-day expiry) or never registered.'
)

# Tools accepted by _decide_directive. The hooks.json matcher prunes
# other tools at the platform layer; this in-hook check is
# belt-and-suspenders against future matcher widening.
_TASK_MUTATING_TOOLS = ("TaskCreate", "TaskUpdate")


def _is_lead_session(input_data: dict[str, Any], team_name: str) -> bool:
    """
    Return True iff the current session is the team's lead session.

    Reads `session_id` from the PostToolUse stdin payload and
    `leadSessionId` from ~/.claude/teams/{team_name}/config.json.
    Mirrors the Lead-Session Guard pattern used by the start-pending-scan /
    stop-pending-scan skill bodies (Layer 1 of the defense-in-depth model);
    this hook-level check is Layer 0, preventing directive emission to
    teammate sessions at the emission source. team_config is the single
    source of truth for lead identity.

    Pure function; never raises. Returns False on missing/empty
    session_id, missing/unsafe team_name, missing config.json, malformed
    JSON, or filesystem error. The teammate session is the expected
    non-lead path; silent fail-open avoids UI noise on every
    teammate Task-tool fire.
    """
    raw_session_id = input_data.get("session_id")
    if not isinstance(raw_session_id, str) or not raw_session_id:
        return False
    if not team_name or not is_safe_path_component(team_name):
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


def _extract_task_id(input_data: dict[str, Any]) -> str | None:
    """
    Pull the task_id out of the PostToolUse payload.

    PostToolUse stdin shape carries the original tool_input under
    "tool_input" and the tool's response under "tool_response".
    TaskCreate's tool_response is nested — the created task is wrapped
    under a "task" key (`tool_response.task.id`) — while TaskUpdate's
    tool_response is flat (`tool_response.id`). Probe in precedence
    order, returning the first match whose value is a string that is
    non-empty after `.strip()`:

      1. tool_input.taskId
      2. tool_input.task_id
      3. tool_response.task.id
      4. tool_response.task.taskId
      5. tool_response.task.task_id
      6. tool_response.id
      7. tool_response.taskId
      8. tool_response.task_id

    WHY the nested `tool_response.task.*` probes precede the flat
    `tool_response.*` probes: production-typical TaskCreate payloads
    are nested (empirically verified via a stdin-logging shim attached
    to a real lead session — the captured fixture is fossilized at
    `tests/fixtures/wake_lifecycle/task_create_production_shape.json`).
    Placing nested probes first means the production-common case hits
    the first matching probe; the flat probes remain as fallback for
    TaskUpdate and for legacy/test fixture shapes.

    Returned values are stripped of leading/trailing whitespace and
    guaranteed non-empty — a whitespace-only id (e.g. `"   "`) would
    propagate to a TaskStop call with a syntactically-valid-but-
    semantically-empty id and fail downstream; rejecting at the
    source is cheaper. Returns None if no probe matches.
    """
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        tid = tool_input.get("taskId") or tool_input.get("task_id")
        if isinstance(tid, str) and tid.strip():
            return tid.strip()

    tool_response = extract_tool_response(input_data)
    if isinstance(tool_response, dict):
        nested_task = tool_response.get("task") or {}
        if isinstance(nested_task, dict):
            tid = (
                nested_task.get("id")
                or nested_task.get("taskId")
                or nested_task.get("task_id")
            )
            if isinstance(tid, str) and tid.strip():
                return tid.strip()

        tid = (
            tool_response.get("id")
            or tool_response.get("taskId")
            or tool_response.get("task_id")
        )
        if isinstance(tid, str) and tid.strip():
            return tid.strip()

    return None


_TERMINAL_STATUSES = ("completed", "deleted")


def _is_terminal_status_update(input_data: dict[str, Any]) -> bool:
    """
    Return True iff this TaskUpdate fired with a status transition into
    a terminal state (completed or deleted). Both terminate active work
    and should trigger a Teardown emit on a 1->0 transition.

    Probes the tool_input.status field (the request) primarily; falls
    back to tool_response.statusChange.to or tool_response.status if
    the platform's response shape carries the new status. Conservative:
    returns False on any ambiguity, so a non-status TaskUpdate cannot
    accidentally trigger a Teardown emit.
    """
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        if tool_input.get("status") in _TERMINAL_STATUSES:
            return True

    tool_response = extract_tool_response(input_data)
    if isinstance(tool_response, dict):
        status_change = tool_response.get("statusChange")
        if isinstance(status_change, dict) and status_change.get("to") in _TERMINAL_STATUSES:
            return True
        if tool_response.get("status") in _TERMINAL_STATUSES:
            return True

    return False


def _is_pending_to_in_progress_transition(input_data: dict[str, Any]) -> bool:
    """
    Return True iff this TaskUpdate fired with a status transition to
    in_progress. Used by the re-Arm branch of `_decide_directive` to
    detect a teammate claiming a task off the queue; the skill body's
    CronList match handles idempotency when a cron is already
    registered, so no hook-side staleness check is needed.

    Single-source probe of `tool_input.status == "in_progress"`. NO
    `tool_response.statusChange.to` fallback and NO flat
    `tool_response.status` fallback: production TaskUpdate payloads
    captured under `tests/fixtures/wake_lifecycle/
    task_update_production_shape.json` are FLAT
    (`tool_response = {id, subject, status, owner}` — no statusChange
    key) and the flat `tool_response.status` field is the post-state,
    identical to `tool_input.status`. Adding either fallback would be
    redundant at best and a regression vector at worst (a future
    platform change that flips `tool_input.status` to optional would
    silently get covered by the redundant probe and mask the breakage).

    Mirrors `_is_terminal_status_update` shape for consistency.
    Conservative: returns False on missing/non-dict tool_input or any
    status value other than the literal string `"in_progress"`.
    """
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        if tool_input.get("status") == "in_progress":
            return True
    return False


# Shared Arm-decision helper. Two branches in `_decide_directive` (the
# TaskCreate Arm branch and the pending->in_progress re-Arm branch) share
# IDENTICAL Arm conditions: at least one lifecycle-relevant active task.
# Only the trigger event differs. Extracting the condition into one
# helper deduplicates the predicate ladder without obscuring it. DO NOT
# inline the condition back into either branch — the two-site
# duplication is the exact pattern the helper is here to prevent
# re-introducing.
#
# Audit: under the Monitor era this helper also gated on a STATE_FILE
# freshness window (`_statefile_is_fresh`) to short-circuit redundant
# directive emits. Under the cron mechanism, idempotency lives in the
# skill body (CronList exact-suffix-match in start-pending-scan.md);
# the hook emits unconditionally on the lifecycle transition. An
# editing LLM tempted to re-introduce a hook-side idempotency cache is
# re-coupling the hook to scan-mechanism state, which violates the
# single-source-of-truth design (CronList is the authoritative armed-
# state bit).
def _arm_or_none(team_name: str) -> str | None:
    """
    Return _ARM_DIRECTIVE iff the conditions for emitting Arm are met:
    at least one lifecycle-relevant active teammate task. Otherwise
    return None.

    Shared between the TaskCreate Arm branch and the pending->in_progress
    re-Arm branch in `_decide_directive` — both branches share identical
    Arm conditions; only the trigger differs. count_active_tasks already
    filters carve-outs (signal-tasks, wake-excluded agentTypes), so
    `>= 1` is the lifecycle-relevant positive count.
    """
    if count_active_tasks(team_name) < 1:
        return None
    return _ARM_DIRECTIVE


def _emit_directive(prose: str) -> None:
    """
    Print the additionalContext output payload with the required
    `hookEventName` field. Caller is responsible for sys.exit(0).
    """
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": prose,
        }
    }
    print(json.dumps(output))


def _decide_directive(input_data: dict[str, Any], team_name: str) -> str | None:
    """
    Return the directive prose to emit, or None for no-op.

    Post-only transition detection:
    - TaskCreate + post >= 1 → Arm.
    - TaskUpdate(status == in_progress) + post >= 1 → Arm. Re-Arm path
      covering cold-start (initial Arm never fired), post-Teardown
      recovery (eager 1->0 Teardown removed the cron entry), mid-session
      resume, and any cron-died-silently edge cases categorically. The
      CronList match in the skill body is the single source of
      idempotency truth — no hook-side pre-state proxy.
    - TaskUpdate(status in {completed, deleted}) + post == 0 + NO same-
      teammate continuation → Teardown.

    count_active_tasks already filters carve-outs (signal-tasks,
    self-complete-exempt owners), so post >= 1 after a TaskCreate
    means at least one lifecycle-relevant task is active, and
    post == 0 after a terminal-status TaskUpdate means the team
    has no remaining lifecycle-relevant work. The Arm threshold
    accepts any positive count; the skill body's CronList match
    handles redundant-emit no-op cheaply. Both Arm and Teardown are
    idempotent in the skill layer, so any over-eager emit on edge
    cases is benign.

    Test coverage pins the Arm predicate to the equivalent forms
    >= 1 and > 0: the lower-bound zero-count no-emit case and the
    sequential-first-create emit case together rule out predicates
    above (e.g. > 1) and below (e.g. >= 0).
    """
    if not _is_lead_session(input_data, team_name):
        return None

    tool_name = input_data.get("tool_name")
    if tool_name not in _TASK_MUTATING_TOOLS:
        return None

    task_id = _extract_task_id(input_data)
    if not task_id:
        return None

    # Defer count_active_tasks (filesystem glob+parse) until after the
    # cheap predicates have selected an actually-relevant tool fire.
    # Metadata-only TaskUpdates (teachback_submit, intentional_wait,
    # handoff, progress, memory_saved) outnumber terminal-status
    # transitions on a typical task lifecycle; gating the I/O on the
    # terminal-status check eliminates ~9k wasted reads/session.
    if tool_name == "TaskCreate":
        # Arm conditions resolved via _arm_or_none(team_name) — shared
        # with the pending->in_progress re-Arm branch below.
        return _arm_or_none(team_name)

    # tool_name == "TaskUpdate"
    # Re-Arm branch on pending->in_progress transition. Categorically
    # covers cold-start (initial Arm never fired), post-Teardown
    # recovery (eager 1->0 Teardown removed the cron entry), and any
    # cron-died-silently edge case. Mirrors the TaskCreate Arm
    # semantics above so a single mental model — "Arm whenever post
    # >= 1" — covers both surfaces. Idempotency lives in the skill
    # body (CronList exact-suffix-match); the hook emits
    # unconditionally on the transition. Empirical anchor:
    # `tests/fixtures/wake_lifecycle/task_update_production_shape.json`
    # fossilizes the FLAT tool_response (no statusChange.from), so the
    # transition predicate consumes `tool_input.status` only.
    if _is_pending_to_in_progress_transition(input_data):
        # Arm conditions resolved via _arm_or_none(team_name) — shared
        # with the TaskCreate Arm branch above.
        return _arm_or_none(team_name)

    if not _is_terminal_status_update(input_data):
        return None
    if count_active_tasks(team_name) != 0:
        return None
    # Defer the 1->0 Teardown when the just-completed task has a same-
    # teammate-owned active continuation in its `blocks` chain. The
    # teammate is staged to claim the next task imminently, so emitting
    # Teardown would (a) surface a phantom cron-down audit signal,
    # and (b) leave a brief cron-down window during which inbound
    # completion-authority work would wait for the next 0->1 transition
    # to re-Arm. `has_same_teammate_continuation` reads `blocks` (the
    # resolved on-disk field) with
    # `addBlocks` as forward-compat fallback (note: `addBlocks` is the
    # additive TaskUpdate API parameter — typically null on disk after
    # the platform merges it into `blocks`; do NOT re-introduce
    # `addBlocks` as the primary read field, that was a silent-inert
    # bug). Reuses `_lifecycle_relevant` for unified active + carve-out
    # semantics, so any future expansion of
    # WAKE_EXCLUDED_AGENT_TYPES is handled transparently. The
    # predicate fail-closes (returns False) on any error path, which
    # preserves the existing Teardown emit behavior on parse failures.
    completed_task = read_task_json(task_id, team_name)
    if has_same_teammate_continuation(completed_task, team_name):
        return None
    return _TEARDOWN_DIRECTIVE


def main() -> None:
    # Outer catch-all preserves the exit-0 fail-open contract against
    # any unexpected exception (malformed task.json, filesystem race,
    # import-time error). The scan mechanism is opportunistic; a crash
    # here would surface as a "hook-error" UI on every Task-tool call,
    # which is the livelock-capable failure shape the categorical
    # standard forbids for any TaskCompleted/TeammateIdle/Stop-class
    # hook.
    try:
        # Bounded read: cap stdin at _MAX_PAYLOAD_BYTES + 1 so we can
        # distinguish "fits under cap" from "exceeds cap" without
        # allocating an unbounded buffer. Reject (suppressOutput) on
        # over-cap input — defense-in-depth against parser amplification.
        try:
            buffer = sys.stdin.read(_MAX_PAYLOAD_BYTES + 1)
        except (IOError, OSError):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        if len(buffer) > _MAX_PAYLOAD_BYTES:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        try:
            input_data = json.loads(buffer)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        if not isinstance(input_data, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        team_name = get_team_name()
        if not team_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        directive = _decide_directive(input_data, team_name)
        if directive is None:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        _emit_directive(directive)
        sys.exit(0)
    except SystemExit:
        # Re-raise — explicit sys.exit(0) calls above are expected
        # control-flow, not errors. Swallowing them would skip the
        # _SUPPRESS_OUTPUT print on no-op paths.
        raise
    except Exception:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
