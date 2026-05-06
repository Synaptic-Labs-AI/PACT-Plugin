#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wake_lifecycle_emitter.py
Summary: PostToolUse hook that emits watch-inbox/unwatch-inbox directives
         for the inbox-wake command pair on first/last active-task transitions.
Used by: hooks.json PostToolUse hook with matcher
         `TaskCreate|TaskUpdate`.

Lifecycle automation:
- On TaskCreate that transitions the team's active-task count from 0 to
  1, emit a watch-inbox directive instructing the lead to invoke
  Skill("PACT:watch-inbox").
- On TaskUpdate(status in {completed, deleted}) that transitions the
  team's active-task count from 1 to 0, emit an unwatch-inbox directive
  instructing the lead to invoke Skill("PACT:unwatch-inbox"). Both
  terminal statuses end active work and are treated symmetrically.
- On any other tool fire (TaskUpdate without a terminal-status
  transition, TaskCreate at non-zero pre-state, terminal-status
  TaskUpdate leaving residual active tasks): no directive emitted.

Transition detection (post-only):
- post = count_active_tasks(team_name) — the count AFTER the tool's
  effect is on disk. count_active_tasks already filters out signal-
  tasks and self-complete-exempt agents, so post is the lifecycle-
  relevant count.
- TaskCreate + post >= 1 → emit Arm. PACT:watch-inbox is idempotent
  (no-op when a valid STATE_FILE is on disk), so emitting on every
  TaskCreate while count is positive is benign and race-immune to
  parallel TaskCreate batches that land multiple task files before
  any PostToolUse hook reads the filesystem.
- TaskUpdate(status in {completed, deleted}) + post == 0 → emit
  Teardown. Skill's Teardown is idempotent (no-op if STATE_FILE absent),
  so over-eager emission on edge cases (terminal-status update of a
  never-counted signal-task while post==0) is benign.
- Any other tool fire (non-status TaskUpdate, TaskCreate at post == 0,
  terminal-status TaskUpdate at post > 0): no-op.

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
The wake mechanism is opportunistic — a crashed lifecycle hook degrades
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
from shared.wake_lifecycle import count_active_tasks

# Suppress the false "hook error" UI surface on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Maximum stdin payload size in bytes. PostToolUse payloads carry
# tool_input + tool_response JSON; even verbose Task-tool fires fit
# comfortably under 1MB. A larger payload is a defense-in-depth
# rejection signal (parser amplification / memory-exhaustion vector).
_MAX_PAYLOAD_BYTES = 1024 * 1024

# Directive prose — verbatim text emitted via additionalContext on
# transitions. Imperative voice; references the canonical command-pair
# slugs `PACT:watch-inbox` (Arm role) and `PACT:unwatch-inbox` (Teardown
# role); idempotency / best-effort clauses prevent the lead from adding
# their own conditional self-diagnosis (#444 unconditional discipline).
_ARM_DIRECTIVE = (
    'First active teammate task created. '
    'Invoke Skill("PACT:watch-inbox") before any further teammate '
    'dispatch. Idempotent — no-op if a valid STATE_FILE is already on disk.'
)

_TEARDOWN_DIRECTIVE = (
    'Last active teammate task completed. '
    'Invoke Skill("PACT:unwatch-inbox") to stop the Monitor and unlink '
    'the STATE_FILE. Best-effort — tolerates a Monitor that died '
    'silently mid-session.'
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
    Mirrors the Lead-Session Guard pattern used by the watch-inbox /
    unwatch-inbox skill bodies; team_config is the single source of
    truth for lead identity.

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
    are nested (per #612 logging-shim capture from session
    pact-56ce3a2a on 2026-05-02). Placing nested probes first means
    the production-common case hits the first matching probe; the
    flat probes remain as fallback for TaskUpdate and for legacy/test
    fixture shapes.

    Returned values are guaranteed non-empty after strip — a
    whitespace-only id (e.g. `"   "`) would propagate to a TaskStop
    call with a syntactically-valid-but-semantically-empty id and
    fail downstream; rejecting at the source is cheaper. Returns
    None if no probe matches.
    """
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        tid = tool_input.get("taskId") or tool_input.get("task_id")
        if isinstance(tid, str) and tid.strip():
            return tid

    tool_response = input_data.get("tool_response") or {}
    if isinstance(tool_response, dict):
        nested_task = tool_response.get("task") or {}
        if isinstance(nested_task, dict):
            tid = (
                nested_task.get("id")
                or nested_task.get("taskId")
                or nested_task.get("task_id")
            )
            if isinstance(tid, str) and tid.strip():
                return tid

        tid = (
            tool_response.get("id")
            or tool_response.get("taskId")
            or tool_response.get("task_id")
        )
        if isinstance(tid, str) and tid.strip():
            return tid

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

    tool_response = input_data.get("tool_response") or {}
    if isinstance(tool_response, dict):
        status_change = tool_response.get("statusChange")
        if isinstance(status_change, dict) and status_change.get("to") in _TERMINAL_STATUSES:
            return True
        if tool_response.get("status") in _TERMINAL_STATUSES:
            return True

    return False


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
    - TaskUpdate(status in {completed, deleted}) + post == 0 → Teardown.

    count_active_tasks already filters carve-outs (signal-tasks,
    self-complete-exempt owners), so post >= 1 after a TaskCreate
    means at least one lifecycle-relevant task is active, and
    post == 0 after a terminal-status TaskUpdate means the team
    has no remaining lifecycle-relevant work. The Arm threshold is
    positive-bound (not strict-equality) so a parallel TaskCreate
    batch that lands N >= 2 task files before any PostToolUse hook
    reads the filesystem still emits Arm; the skill's idempotency
    absorbs the redundant emits on subsequent TaskCreates within
    the same active window. Both Arm and Teardown are idempotent
    in the skill layer, so any over-eager emit on edge cases is
    benign.
    """
    if not _is_lead_session(input_data, team_name):
        return None

    tool_name = input_data.get("tool_name")
    if tool_name not in _TASK_MUTATING_TOOLS:
        return None

    if not _extract_task_id(input_data):
        return None

    # Defer count_active_tasks (filesystem glob+parse) until after the
    # cheap predicates have selected an actually-relevant tool fire.
    # Metadata-only TaskUpdates (teachback_submit, intentional_wait,
    # handoff, progress, memory_saved) outnumber terminal-status
    # transitions on a typical task lifecycle; gating the I/O on the
    # terminal-status check eliminates ~9k wasted reads/session.
    if tool_name == "TaskCreate":
        if count_active_tasks(team_name) >= 1:
            return _ARM_DIRECTIVE
        return None

    # tool_name == "TaskUpdate"
    if not _is_terminal_status_update(input_data):
        return None
    if count_active_tasks(team_name) == 0:
        return _TEARDOWN_DIRECTIVE
    return None


def main() -> None:
    # Outer catch-all preserves the exit-0 fail-open contract against
    # any unexpected exception (malformed task.json, filesystem race,
    # import-time error). The wake mechanism is opportunistic; a crash
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
