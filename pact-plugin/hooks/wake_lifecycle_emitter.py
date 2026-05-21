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
- Terminal-status TaskUpdate(status in {completed, deleted}) does
  NOT emit Teardown from this hook. The stop-pending-scan
  directive is emitted via two sibling surfaces:
    * Tier-1 (dominant lead-driven path): TaskCompleted handler
      `hooks/teardown_request_emitter.py` fires on the platform's
      terminal status transition and applies the same predicate
      ladder (1->0 lifecycle-relevant active count + no same-
      teammate-owned active continuation deferral).
    * Tier-2 (carve-out fallback): for self-complete-exempt
      teammate sessions whose terminal TaskUpdate fires in the
      WRONG session for the lead's directive, this hook's
      teammate-Teardown pre-branch writes a `type="teardown"`
      marker to `wake_inbox/`, and the lead-side drain hook
      `hooks/wake_inbox_drain.py` (UserPromptSubmit) consumes it
      and emits the directive on the lead's next prompt.
- On any other tool fire (TaskUpdate with neither a terminal-
  status nor a pending->in_progress transition, TaskCreate when
  the count is zero): no directive emitted.

Lead-Context Guard (Layer 0 — defense-in-depth):
- Every Arm directive emission below the lead-context early-return is
  gated by `is_lead_context`, which classifies the current PostToolUse
  fire as lead-context via the platform-stamped `agent_id` field-
  presence on the hook-stdin payload (absent for lead fires; str for
  in-process teammate fires that inherit the lead's session_id under
  Claude Code's in-process subagent model).
  Teammate sessions never receive a directive via the in-session
  `additionalContext` channel from this hook — hook-level filtering at
  the emission source, correct-by-construction rather than relying on
  the skill body's Lead-Session Guard (Layer 1) as the primary defense.
  The skill-body guard remains as backstop for user-typed manual
  invocation from a teammate session.

Asymmetric Arm vs Teardown signal locality:
- Arm has TWO natural trigger sources with different session locality.
  (a) Teammate self-claim TaskUpdate(status="in_progress") per
  pact-agent-teams §On Start lives in the TEAMMATE session — a symmetric
  lead-session guard starves this signal entirely. (b) Lead-side
  unowned-TaskCreate-then-TaskUpdate(owner) dispatch pattern produces
  no in_progress transition the lead-session sees, so the lead-session
  branch also misses it.
- Teardown has TWO trigger sources too. (a) Lead-driven terminal
  TaskUpdate(status in {completed, deleted}) on a 1->0 transition runs
  in the LEAD session — handled by the Tier-1 sibling hook
  `teardown_request_emitter.py` on TaskCompleted, NOT here. (b)
  Self-complete-exempt teammate terminal TaskUpdate (today: secretary;
  tomorrow any agentType added to SELF_COMPLETE_EXEMPT_AGENT_TYPES)
  runs in the TEAMMATE session — PostToolUse `additionalContext` from
  this hook would target the wrong session. The Tier-2 carve-out path
  is the bridge for that case.
- Both asymmetric signals are realised by branch PLACEMENT: two
  teammate pre-branches (`_maybe_write_teammate_arm_marker` and
  `_maybe_write_teammate_teardown_marker`) sit ABOVE the lead-context
  early-return and write per-marker JSON files to the team's
  `wake_inbox/` directory (the cross-session signal). The
  lead-session Arm branches stay BELOW the lead-session guard. The
  retired PostToolUse Teardown branch is replaced by the Tier-1
  TaskCompleted handler. Lead-side drain + per-type dispatch lives in
  `hooks/wake_inbox_drain.py` (UserPromptSubmit). Together the
  Tier-1 + Tier-2 + drain surfaces cover both Teardown trigger
  sources while preserving the #737 Layer 0 lead-only correctness
  invariant for the directive emission itself.

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
- TaskUpdate(status in {completed, deleted}) — no Teardown emit
  from THIS hook. The 1->0 transition + same-teammate-continuation
  predicate ladder lives in the Tier-1 TaskCompleted handler
  (`teardown_request_emitter.py`). The teammate-Teardown
  pre-branch above writes a Tier-2 carve-out marker for the
  self-complete-exempt case (consumed by `wake_inbox_drain.py`).
- Any other tool fire (TaskUpdate with neither in_progress nor
  terminal status, TaskCreate at post == 0): no-op.

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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure shared package import resolves under the hooks directory.
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import shared.pact_context as pact_context
from shared.intentional_wait import is_self_complete_exempt
from shared.pact_context import get_team_name
from shared.session_state import is_safe_path_component
from shared.task_utils import read_task_json
from shared.tool_response import extract_tool_response
from shared.wake_lifecycle import (
    count_active_tasks,
    is_lead_context,
)
from shared.wake_lifecycle import _classify_owner

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
    'Active teammate work detected. '
    'You MUST invoke Skill("PACT:start-pending-scan") before your next '
    'tool call. This is a non-negotiable lifecycle gate. '
    'Idempotent — no-op if a /PACT:scan-pending-tasks cron is '
    'already registered.'
)

_TEARDOWN_DIRECTIVE = (
    'No active teammate work remaining. '
    'You MUST invoke Skill("PACT:stop-pending-scan") before your next '
    'tool call to delete the /PACT:scan-pending-tasks cron. '
    'This is a non-negotiable lifecycle gate. '
    'Best-effort — tolerates a cron that was already auto-deleted '
    '(7-day expiry) or never registered.'
)

# Tools accepted by _decide_directive. The hooks.json matcher prunes
# other tools at the platform layer; this in-hook check is
# belt-and-suspenders against future matcher widening.
_TASK_MUTATING_TOOLS = ("TaskCreate", "TaskUpdate")


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

    Producer-side path-safety allowlist: extracted task_ids are
    validated against `is_safe_path_component` before return. The
    task_id flows into marker filenames (wake_inbox + Teardown sidecar)
    and journal event bodies. Rejecting path-traversal payloads
    (`"../foo"`, `"/etc/passwd"`, `"foo\x00bar"`) at the extraction
    boundary means every downstream sink inherits sanitization-by-
    construction — preferable to per-sink re-validation which is
    asymmetry-prone (the weakest sink becomes the bypass; see
    `patterns_symmetric_sanitization` security memory). Under the
    platform task system, legitimate task_ids match the allowlist
    `[A-Za-z0-9_-]+` (integer-as-string + alpha suffix patterns
    like "S2", "L4"), so the producer-side rejection has zero
    false-positives against real traffic.
    """
    def _accept(raw: object) -> str | None:
        if not isinstance(raw, str):
            return None
        stripped = raw.strip()
        if not stripped:
            return None
        if not is_safe_path_component(stripped):
            return None
        return stripped

    tool_input = input_data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        tid = _accept(tool_input.get("taskId") or tool_input.get("task_id"))
        if tid is not None:
            return tid

    tool_response = extract_tool_response(input_data)
    if isinstance(tool_response, dict):
        nested_task = tool_response.get("task") or {}
        if isinstance(nested_task, dict):
            tid = _accept(
                nested_task.get("id")
                or nested_task.get("taskId")
                or nested_task.get("task_id")
            )
            if tid is not None:
                return tid

        tid = _accept(
            tool_response.get("id")
            or tool_response.get("taskId")
            or tool_response.get("task_id")
        )
        if tid is not None:
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


# Schema version for inbox marker JSON payloads. Bump on additive OR
# breaking-shape changes; drain side tolerates unknown fields and
# treats malformed JSON as a fail-conservative wake signal. Version 2
# coincides with the additive `type` field (`"arm"` | `"teardown"`)
# introduced for the Tier-2 carve-out path — pre-v2 markers lack the
# field and default to `"arm"` on the drain side, preserving backward
# compatibility for any markers written by an older session. The pin
# sets precedent: future additive changes (e.g., extra trigger tokens,
# routing metadata) bump this in lockstep so the wire-format snapshot
# remains an inspectable forensic field, even though no drain-side
# consumer branches on it today.
_WAKE_INBOX_MARKER_SCHEMA_VERSION = 2

# Trigger sentinel written into the marker payload for forensics. The
# drain side consumes the file PRESENCE, not the field content, but
# the field documents the trigger class for debug/triage.
_TEAMMATE_SELF_CLAIM_TRIGGER = "teammate_self_claim_in_progress"


def _maybe_write_teammate_arm_marker(
    input_data: dict[str, Any],
    team_name: str,
    is_lead: bool | None = None,
) -> None:
    """
    Write a wake-inbox marker iff the 6-clause asymmetric-guard
    predicate ladder holds for this PostToolUse fire.

    `is_lead` is the pre-computed result of
    `is_lead_context(input_data, team_name)`, passed in by
    `_decide_directive` so the predicate evaluates at most ONCE per
    fire (instead of twice: once here at clause 5, once at the lead-
    context outer gate). The check itself is O(1) (compound field-
    presence read on `agent_id` and `teammate_name`; no filesystem
    I/O), so the memoize threading is structural-clarity rather than
    performance-critical.
    When `is_lead is None` (the test direct-invocation path), clause 5
    falls back to computing the check in-helper — preserves behavior
    for callers that don't have the memoized value pre-computed.
    Behavior-preserving: clause 5 still skips the marker write when
    this fire originates from the lead context.

    The predicate ladder evaluates in ORDER below; ALL six clauses must
    hold to write a marker. Order matters: cheap predicates first
    (string/shape checks) before any filesystem I/O; the lead-context
    check is clause 5 so a non-lead match still pays the predicate cost
    but no more (clauses 1-4 are pure dict reads).

      Clause 1 — team_name path-safe: non-empty AND
                 is_safe_path_component(team_name). Required because
                 the marker-write path joins team_name into a path
                 segment; `is_lead_context` itself reads no filesystem
                 so does not enforce this defense.
      Clause 2 — tool_name allowlist: tool_name in TaskCreate|TaskUpdate.
                 Belt-and-suspenders against future hooks.json matcher
                 widening; current matcher already filters.
      Clause 3 — trigger semantics: for TaskUpdate, the fire is a
                 pending->in_progress self-claim transition. For
                 TaskCreate, the just-created task carries a teammate
                 owner-at-create (excludes unowned umbrella TaskCreates).
                 Status-only TaskUpdates (teachback_submit,
                 intentional_wait, metadata-only edits) fail this clause.
      Clause 4 — owner classification: the owner field resolves to a
                 known team member who is NOT the team lead. Defends
                 against hypothetical re-assignment to the lead.
      Clause 5 — NOT lead session: positive teammate-session check.
                 The lead-session counterpart to this path is the
                 existing TaskCreate branch (which fires Arm directly
                 below) and the new lead-side drain hook fallback.
      Clause 6 — task_id present: non-empty string for the marker
                 filename's uniqueness component.

    On all six clauses passing, write the marker via O_CREAT|O_EXCL
    atomic open. On any failure to write (FileExistsError,
    PermissionError, any OSError), silently swallow — the wake mechanism
    is opportunistic; a missed marker degrades to the lead-side B-1
    count-based fallback in `wake_inbox_drain.py`, not a hard failure.

    Pure side-effect helper. Never raises (outer except swallows every
    OSError + Exception class). Returns None unconditionally — caller
    must NOT branch on a return value.
    """
    try:
        # Clause 1: team_name path-safety.
        if not isinstance(team_name, str) or not team_name:
            return
        if not is_safe_path_component(team_name):
            return

        # Clause 2: tool_name allowlist.
        tool_name = input_data.get("tool_name")
        if tool_name not in _TASK_MUTATING_TOOLS:
            return

        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return

        # Clause 3: trigger semantics. For TaskUpdate, require
        # pending->in_progress transition. For TaskCreate, require a
        # non-empty owner-at-create field — unowned umbrella TaskCreates
        # (per /PACT:orchestrate dispatch pattern) fail this clause.
        if tool_name == "TaskUpdate":
            if not _is_pending_to_in_progress_transition(input_data):
                return
            owner = tool_input.get("owner")
        else:  # tool_name == "TaskCreate"
            owner = tool_input.get("owner")
            if not isinstance(owner, str) or not owner:
                return

        # Clause 4: owner classification. Owner must be a known team
        # member AND NOT the lead. Reuses the consolidated owner
        # projection from shared/wake_lifecycle.py.
        if not isinstance(owner, str) or not owner:
            return
        classification = _classify_owner(owner, team_name)
        if not classification.config_readable:
            # Config unreadable: cannot positively classify; fail-open
            # to "skip marker." Lead-side B-1 fallback covers the gap.
            return
        if not classification.is_known_team_member:
            return
        if classification.is_lead:
            return

        # Clause 5: positive teammate-context check. The lead-context
        # branch handles the lead-side Arm path directly below; only
        # teammate-side fires write the cross-session marker. The
        # `is_lead` argument is the memoized result of
        # `is_lead_context(input_data, team_name)` computed once by
        # `_decide_directive` and reused at the outer-gate check below.
        # When `is_lead is None` (direct-invocation path), fall back to
        # computing the check in-helper — the structural pin
        # (lead-context guard on a control-flow line) requires the
        # guard call to appear on a control-flow line.
        if is_lead is None and is_lead_context(input_data, team_name):
            return
        if is_lead:
            return

        # Clause 6: task_id present.
        task_id = _extract_task_id(input_data)
        if not task_id:
            return

        # All six clauses hold — write the marker atomically.
        session_id_raw = input_data.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw:
            return

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        written_at = now.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

        inbox_dir = (
            Path.home()
            / ".claude"
            / "teams"
            / team_name
            / "wake_inbox"
        )
        try:
            inbox_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return

        # Filename: {timestamp}-{session_id}-{task_id}.json — natural
        # lexical order is chronological for the drain side. Path
        # component safety: session_id is platform-generated UUID,
        # task_id is platform-generated short id; both are restricted by
        # the platform's task-id alphabet. Use a defensive replace of
        # path separators AND embedded NUL bytes as belt-and-suspenders.
        # NUL byte handling: a `\x00` in either field would raise
        # `ValueError` from `os.open` (NOT `OSError`); the outer
        # `except Exception` still catches it but the explicit NUL-strip
        # here makes the defense visible at the sanitization step and
        # avoids opening a fd just to have it rejected by the kernel.
        safe_task_id = (
            task_id.replace("/", "_").replace("\\", "_").replace("\x00", "_")
        )
        safe_session_id = (
            session_id_raw.replace("/", "_")
            .replace("\\", "_")
            .replace("\x00", "_")
        )
        marker_filename = (
            f"{timestamp}-{safe_session_id}-{safe_task_id}.json"
        )
        marker_path = inbox_dir / marker_filename

        marker_payload = {
            "schema_version": _WAKE_INBOX_MARKER_SCHEMA_VERSION,
            "type": "arm",
            "written_at": written_at,
            "writer_session_id": session_id_raw,
            "tool_name": tool_name,
            "task_id": task_id,
            "owner": owner,
            "trigger": _TEAMMATE_SELF_CLAIM_TRIGGER,
        }

        # O_CREAT|O_EXCL atomic write. FileExistsError on collision is
        # silently swallowed — another writer already signalled, no
        # second signal needed (collisions are structurally impossible
        # under the {timestamp, session_id, task_id} encoding but
        # O_EXCL is belt-and-suspenders).
        try:
            fd = os.open(
                str(marker_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return
        except OSError:
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(marker_payload, handle)
        except OSError:
            # Write failure after open — best-effort cleanup; the file
            # may be empty but the drain side treats malformed/empty
            # markers as wake signals (fail-conservative).
            try:
                os.unlink(str(marker_path))
            except OSError:
                pass
            return
    except Exception:
        # Outer catch-all preserves the never-raises contract; teammate
        # sessions emit on every Task-tool fire, a raise here would
        # surface as a hook-error UI on every fire.
        return


# Trigger sentinel for Tier-2 teardown markers. Parallel to
# _TEAMMATE_SELF_CLAIM_TRIGGER; documents the carve-out class for
# debug/triage. The drain side consumes the `type` field; this
# field is forensic-only.
_TEAMMATE_SELF_COMPLETE_EXEMPT_TRIGGER = "teammate_self_complete_exempt"


def _maybe_write_teammate_teardown_marker(
    input_data: dict[str, Any],
    team_name: str,
    is_lead: bool | None = None,
) -> None:
    """
    Write a wake-inbox teardown marker iff a teammate-session terminal-
    status TaskUpdate drives the team's lifecycle-relevant active-task
    count to zero AND the just-completed task is is_self_complete_exempt
    (signal-task or memory-save carve-out).

    Sibling to `_maybe_write_teammate_arm_marker` (L391): same call
    shape, same `is_lead` memoization protocol, same predicate-ladder
    discipline (cheap checks before filesystem I/O). The marker shape
    is `{schema_version, type: "teardown", task_id, team_name, owner,
    timestamp_ms, trigger}` — `type` field is the drain-side dispatch
    key (the consumer at wake_inbox_drain.py classifies by this value).

    Tier-2 fallback path covered:
    - Carve-out teammates (today `pact-secretary`; tomorrow any
      agentType added to SELF_COMPLETE_EXEMPT_AGENT_TYPES) call
      TaskUpdate(status="completed") in their OWN session. PostToolUse
      `additionalContext` from a teammate session would target the
      WRONG session for the lead's Teardown directive — the marker
      file is the only correct cross-session bridge.
    - The Tier-1 TaskCompleted handler (`teardown_request_emitter.py`)
      catches the dominant lead-driven path; it cannot fire here
      because the teammate, not the lead, is the TaskCompleted hook
      caller's session.

    Predicate ladder (all six clauses must hold):
      Clause 1 — team_name path-safe: non-empty AND
                 is_safe_path_component(team_name). Mirrors the same
                 defense applied by `_maybe_write_teammate_arm_marker`.
      Clause 2 — tool_name == "TaskUpdate": Teardown trigger only fires
                 on terminal-status TaskUpdate. Belt-and-suspenders
                 against future hooks.json matcher widening.
      Clause 3 — `_is_terminal_status_update(input_data)`: the just-
                 completed task is transitioning to completed/deleted.
                 Status-only TaskUpdates (metadata edits) fail this.
      Clause 4 — `count_active_tasks(team_name) == 0`: 1->0 transition;
                 the team has no remaining lifecycle-relevant work.
      Clause 5 — `is_lead is False`: positive teammate-context check.
                 The lead-side Tier-1 path handles the lead-driven case;
                 this writer is teammate-side only. When `is_lead is
                 None` (test direct-invocation path), fall back to
                 computing `is_lead_context(input_data, team_name)`.
      Clause 6 — task_id present AND `is_self_complete_exempt(task,
                 team_name)`: the carve-out witness. The completed task
                 belongs to an exempt agent type (secretary today) OR
                 is a signal-task (blocker/algedonic). Reads the task
                 JSON to evaluate.

    Pure side-effect helper. Never raises (outer except swallows every
    OSError + Exception class). Returns None unconditionally.
    """
    try:
        # Clause 1: team_name path-safety.
        if not isinstance(team_name, str) or not team_name:
            return
        if not is_safe_path_component(team_name):
            return

        # Clause 2: tool_name == TaskUpdate.
        tool_name = input_data.get("tool_name")
        if tool_name != "TaskUpdate":
            return

        # Clause 3: terminal-status transition.
        if not _is_terminal_status_update(input_data):
            return

        # Clause 4: 1->0 active-task transition.
        # count_active_tasks filters signal-tasks + self-complete-
        # exempt owners via its lifecycle-relevant filter.
        if count_active_tasks(team_name) != 0:
            return

        # Clause 5: positive teammate-context check. The `is_lead`
        # argument is the memoized result computed once by
        # `_decide_directive`; when None (direct-invocation path), fall
        # back to computing the check in-helper. Mirrors clause 5 of
        # `_maybe_write_teammate_arm_marker`.
        if is_lead is None and is_lead_context(input_data, team_name):
            return
        if is_lead:
            return

        # Clause 6: task_id present AND carve-out witness.
        # `is_self_complete_exempt` has two OR-combined surfaces (per
        # shared/intentional_wait.py:410): (1) team-config agentType
        # in SELF_COMPLETE_EXEMPT_AGENT_TYPES (today pact-secretary),
        # (2) signal-task metadata (completion_type="signal" AND
        # type in {blocker, algedonic}). Tier-2 wants surface (1)
        # ONLY — signal-tasks are filtered out of count_active_tasks'
        # lifecycle-relevant tally (per shared/wake_lifecycle.py), so
        # they NEVER drive a real 1->0 transition; emitting a Teardown
        # marker on a signal-task completion would surface a phantom
        # Teardown directive. Excluding signal-tasks here keeps the
        # architecture's §4.4 invariant intact: "signal-tasks don't
        # drive cron". Explicit metadata check (inline-literal mirror
        # of the pattern at agent_handoff_emitter.py:300) excludes
        # them BEFORE the broader is_self_complete_exempt call.
        task_id = _extract_task_id(input_data)
        if not task_id:
            return
        completed_task = read_task_json(task_id, team_name)
        if not isinstance(completed_task, dict) or not completed_task:
            return
        task_metadata = completed_task.get("metadata") or {}
        if isinstance(task_metadata, dict):
            if task_metadata.get("completion_type") == "signal":
                signal_type = task_metadata.get("type")
                if signal_type in ("blocker", "algedonic"):
                    return
        if not is_self_complete_exempt(completed_task, team_name):
            return

        # All six clauses hold — write the marker atomically.
        session_id_raw = input_data.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw:
            return

        owner = completed_task.get("owner")
        if not isinstance(owner, str) or not owner:
            owner = "unknown"

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        timestamp_ms = int(now.timestamp() * 1000)
        written_at = now.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

        inbox_dir = (
            Path.home()
            / ".claude"
            / "teams"
            / team_name
            / "wake_inbox"
        )
        try:
            inbox_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return

        # Filename: {timestamp}-teardown-{session_id}-{task_id}.json —
        # the `-teardown-` infix is forensic; the drain side dispatches
        # on the marker payload's `type` field, not the filename. Path
        # component safety: same NUL + path-separator strip as the Arm
        # marker writer.
        safe_task_id = (
            task_id.replace("/", "_").replace("\\", "_").replace("\x00", "_")
        )
        safe_session_id = (
            session_id_raw.replace("/", "_")
            .replace("\\", "_")
            .replace("\x00", "_")
        )
        marker_filename = (
            f"{timestamp}-teardown-{safe_session_id}-{safe_task_id}.json"
        )
        marker_path = inbox_dir / marker_filename

        marker_payload = {
            "schema_version": _WAKE_INBOX_MARKER_SCHEMA_VERSION,
            "type": "teardown",
            "written_at": written_at,
            "writer_session_id": session_id_raw,
            "tool_name": tool_name,
            "task_id": task_id,
            "team_name": team_name,
            "owner": owner,
            "timestamp_ms": timestamp_ms,
            "trigger": _TEAMMATE_SELF_COMPLETE_EXEMPT_TRIGGER,
        }

        # O_CREAT|O_EXCL atomic write. FileExistsError on collision is
        # silently swallowed — another writer already signalled.
        try:
            fd = os.open(
                str(marker_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return
        except OSError:
            return
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(marker_payload, handle)
        except OSError:
            try:
                os.unlink(str(marker_path))
            except OSError:
                pass
            return
    except Exception:
        # Outer catch-all preserves the never-raises contract; teammate
        # sessions emit on every Task-tool fire, a raise here would
        # surface as a hook-error UI on every fire.
        return


def _decide_directive(input_data: dict[str, Any], team_name: str) -> str | None:
    """
    Return the directive prose to emit, or None for no-op.

    Post-only transition detection:
    - TaskCreate + post >= 1 → Arm.
    - TaskUpdate(status == in_progress) + post >= 1 → Arm. Re-Arm path
      covering cold-start (initial Arm never fired), post-Teardown
      recovery (an earlier Teardown removed the cron entry), mid-
      session resume, and any cron-died-silently edge cases
      categorically. The CronList match in the skill body is the
      single source of idempotency truth — no hook-side pre-state
      proxy.
    - TaskUpdate(status in {completed, deleted}) → no Teardown emit
      from THIS function. Teardown lives in two sibling hooks
      (Tier-1 `teardown_request_emitter.py` on TaskCompleted for
      the dominant lead-driven path; Tier-2 `wake_inbox_drain.py`
      on UserPromptSubmit for the self-complete-exempt teammate
      carve-out, fed by `_maybe_write_teammate_teardown_marker`).
      The terminal-status branch here falls through to None.

    count_active_tasks already filters carve-outs (signal-tasks,
    self-complete-exempt owners), so post >= 1 after a TaskCreate
    means at least one lifecycle-relevant task is active. The Arm
    threshold accepts any positive count; the skill body's
    CronList match handles redundant-emit no-op cheaply. Arm is
    idempotent in the skill layer, so any over-eager emit on edge
    cases is benign.

    Test coverage pins the Arm predicate to the equivalent forms
    >= 1 and > 0: the lower-bound zero-count no-emit case and the
    sequential-first-create emit case together rule out predicates
    above (e.g. > 1) and below (e.g. >= 0).
    """
    # Compute `is_lead_context` ONCE per fire and thread the result
    # through both the teammate-Arm pre-branch (clause 5) and the
    # outer lead-context gate. The check is O(1) (compound field-
    # presence read on `agent_id` and `teammate_name`; no filesystem
    # I/O) so the per-fire cost is negligible, but threading the
    # memoized result keeps the call shape unambiguous at the
    # structural pin and avoids two evaluations of an identical
    # predicate on the same immutable input.
    is_lead = bool(is_lead_context(input_data, team_name))

    # Teammate-Arm pre-branch — runs BEFORE the lead-session early-return
    # so the teammate self-claim signal escapes the symmetric guard via
    # an inbox marker (the cross-session signal). Returns None in all
    # paths: PostToolUse `additionalContext` would target the teammate's
    # session, not the lead's. The lead-side drain hook
    # (`wake_inbox_drain.py`, UserPromptSubmit) consumes the marker on
    # the lead's next prompt.
    _maybe_write_teammate_arm_marker(input_data, team_name, is_lead)

    # Teammate-Teardown pre-branch — sibling to the Arm pre-branch.
    # Covers the Tier-2 carve-out path: when a self-complete-exempt
    # teammate (today secretary, tomorrow any agentType added to
    # SELF_COMPLETE_EXEMPT_AGENT_TYPES) drives a 1->0 transition from
    # its own session, PostToolUse `additionalContext` cannot reach
    # the lead. The marker file is the cross-session bridge; the lead-
    # side drain hook (wake_inbox_drain.py UserPromptSubmit) consumes
    # it and emits the Teardown directive. The dominant lead-driven
    # path is covered by the Tier-1 TaskCompleted handler
    # (teardown_request_emitter.py); this fallback is the carve-out.
    _maybe_write_teammate_teardown_marker(input_data, team_name, is_lead)

    # Outer lead-context gate. Layer 0 of the defense-in-depth model;
    # teammate sessions never reach the directive emission paths below.
    # Uses the memoized `is_lead` from above; semantically identical
    # to `if not is_lead_context(input_data, team_name): return None`.
    if not is_lead:
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

    # Terminal-status TaskUpdate fires fall through to implicit None.
    # The 1->0 Teardown emission lives in two sibling hooks:
    #   - Tier-1: hooks/teardown_request_emitter.py (TaskCompleted) —
    #     dominant lead-driven path; fires on the platform's terminal
    #     status transition with the same predicate ladder
    #     (1->0 transition + same-teammate-continuation deferral).
    #   - Tier-2: hooks/wake_inbox_drain.py (UserPromptSubmit) —
    #     carve-out fallback consuming type="teardown" markers
    #     written by `_maybe_write_teammate_teardown_marker` above
    #     for self-complete-exempt teammate sessions.
    # Cross-session signal correctness: PostToolUse:TaskUpdate
    # additionalContext targets the CURRENT session, which for
    # secretary self-complete (and any future SELF_COMPLETE_EXEMPT
    # agentType) is NOT the lead's session. The marker file is the
    # only correct cross-process bridge for those carve-outs; the
    # TaskCompleted hook is the correct surface for the dominant
    # lead-driven path. Emitting Teardown here too would double-fire
    # against Tier-1 on every lead-driven completion.
    return None


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
