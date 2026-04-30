#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wake_lifecycle_emitter.py
Summary: PostToolUse hook that emits Arm/Teardown directives for the
         inbox-wake skill on first/last active-task transitions.
Used by: hooks.json PostToolUse hook with matcher
         `TaskCreate|TaskUpdate|Task|Agent`.

Lifecycle automation:
- On TaskCreate that transitions the team's active-task count from 0 to
  1, emit an Arm directive instructing the lead to invoke
  Skill("PACT:inbox-wake") + Arm.
- On TaskUpdate(status=completed) that transitions the team's
  active-task count from 1 to 0, emit a Teardown directive instructing
  the lead to invoke Skill("PACT:inbox-wake") + Teardown.
- On any other tool fire (TaskUpdate without status->completed,
  Task/Agent teammate spawn, TaskCreate at non-zero pre-state,
  TaskUpdate(completed) leaving residual active tasks): no directive
  emitted.

Pre/post derivation:
- post = count_active_tasks(team_name) — the count AFTER the tool's
  effect is on disk.
- pre = post adjusted by reverse-applying this tool's effect:
    * TaskCreate: pre = post - 1 if the just-created task is
      _lifecycle_relevant else pre = post.
    * TaskUpdate(status=completed): pre = post + 1 if the just-completed
      task WAS _lifecycle_relevant under its non-completed status else
      pre = post.
- Carve-out filters apply identically pre/post (signal-task type and
  exempt-agent owner do not depend on status), so a task counted at
  pre is still counted at post unless its status changed.

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
from shared.wake_lifecycle import (
    _lifecycle_relevant,
    count_active_tasks,
)

# Suppress the false "hook error" UI surface on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Directive prose — verbatim text emitted via additionalContext on
# transitions. Imperative voice; references the canonical Skill slug
# `PACT:inbox-wake`; idempotency clause prevents the lead from adding
# their own conditional self-diagnosis (#444 unconditional discipline).
_ARM_DIRECTIVE = (
    'First active teammate task created. '
    'Invoke Skill("PACT:inbox-wake") and execute the Arm operation '
    'before any further teammate dispatch. Arm is idempotent — the '
    'skill no-ops if a valid STATE_FILE is already on disk.'
)

_TEARDOWN_DIRECTIVE = (
    'Last active teammate task completed. '
    'Invoke Skill("PACT:inbox-wake") and execute the Teardown operation '
    'to stop the Monitor and unlink the STATE_FILE. Teardown is '
    'best-effort — tolerates a Monitor that died silently mid-session.'
)

# Tools whose PostToolUse fires this hook is registered against. Task
# and Agent are the spawn-tool internal names; they do not change the
# active-task count (the TaskCreate that preceded the spawn already
# accounted for the task), so they fall through to the no-op path.
_TASK_MUTATING_TOOLS = ("TaskCreate", "TaskUpdate")


def _read_task_from_disk(team_name: str, task_id: str) -> dict[str, Any] | None:
    """
    Read a single task JSON from ~/.claude/tasks/{team_name}/{task_id}.json.

    Returns None on any read or parse failure; never raises. Used to
    determine the just-mutated task's lifecycle relevance (pre-mutation
    state for TaskUpdate, post-mutation state for TaskCreate).

    Path-traversal defense: team_name and task_id flow into a filesystem
    join. Both are short string components; this function applies a
    minimal traversal-fragment strip via Path() composition with
    explicit existence check. Upstream callers in this module short-
    circuit on empty / non-string inputs before reaching here.
    """
    if not isinstance(team_name, str) or not team_name:
        return None
    if not isinstance(task_id, str) or not task_id:
        return None
    # Reject obvious traversal attempts in the task_id component. The
    # team_name comes from get_team_name() which is already canonicalized
    # at session_init.py team-creation time; task_id comes from the
    # tool_input/tool_response payloads, which are platform-controlled
    # but defense-in-depth here is cheap.
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        return None

    task_path = Path.home() / ".claude" / "tasks" / team_name / f"{task_id}.json"
    if not task_path.exists():
        return None
    try:
        content = task_path.read_text(encoding="utf-8")
        task = json.loads(content)
    except (IOError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(task, dict):
        return None
    return task


def _extract_task_id(input_data: dict[str, Any]) -> str | None:
    """
    Pull the task_id out of the PostToolUse payload.

    PostToolUse stdin shape carries the original tool_input under
    "tool_input" and the tool's response under "tool_response". Both
    TaskCreate and TaskUpdate accept/return a task with an `id` field.
    Defensively probe both paths; return None if neither yields a
    string id.
    """
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        tid = tool_input.get("taskId") or tool_input.get("task_id")
        if isinstance(tid, str) and tid:
            return tid

    tool_response = input_data.get("tool_response") or {}
    if isinstance(tool_response, dict):
        tid = tool_response.get("id") or tool_response.get("taskId") or tool_response.get("task_id")
        if isinstance(tid, str) and tid:
            return tid

    return None


def _is_status_completed_update(input_data: dict[str, Any]) -> bool:
    """
    Return True iff this TaskUpdate fired with a status->completed
    transition.

    Probes the tool_input.status field (the request) primarily; falls
    back to tool_response.statusChange.to or tool_response.status if
    the platform's response shape carries the new status. Conservative:
    returns False on any ambiguity, so a non-status TaskUpdate cannot
    accidentally trigger a Teardown emit.
    """
    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        if tool_input.get("status") == "completed":
            return True

    tool_response = input_data.get("tool_response") or {}
    if isinstance(tool_response, dict):
        status_change = tool_response.get("statusChange")
        if isinstance(status_change, dict) and status_change.get("to") == "completed":
            return True
        if tool_response.get("status") == "completed":
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

    Drives the pre/post derivation: count_active_tasks is the post
    state; the just-mutated task's lifecycle relevance reverses the
    effect to derive pre. Emit Arm on 0->1, Teardown on 1->0, no-op
    on every other shape.
    """
    tool_name = input_data.get("tool_name")
    if tool_name not in _TASK_MUTATING_TOOLS:
        return None

    task_id = _extract_task_id(input_data)
    if not task_id:
        return None

    post = count_active_tasks(team_name)
    task = _read_task_from_disk(team_name, task_id)
    if task is None:
        return None

    if tool_name == "TaskCreate":
        # The new task is on disk. If it counts as lifecycle_relevant,
        # pre = post - 1; otherwise the create did not change the count.
        if not _lifecycle_relevant(task):
            return None
        pre = post - 1
        if pre == 0 and post == 1:
            return _ARM_DIRECTIVE
        return None

    # tool_name == "TaskUpdate"
    if not _is_status_completed_update(input_data):
        # Non-status TaskUpdate (owner, metadata, subject, etc.).
        return None

    # The task is already at status=completed on disk; lifecycle_relevant
    # currently returns False for it. Reverse-apply: the task WAS
    # lifecycle_relevant if its non-status carve-outs (signal-type,
    # exempt-owner) would have admitted it. Construct a hypothetical
    # pre-completion view: same task with status=in_progress.
    hypothetical_pre = dict(task)
    hypothetical_pre["status"] = "in_progress"
    if not _lifecycle_relevant(hypothetical_pre):
        # The just-completed task would not have counted under any
        # active status; the completion did not change the active count.
        return None
    pre = post + 1
    if pre == 1 and post == 0:
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
        try:
            input_data = json.load(sys.stdin)
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
