#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_schema_validator.py
Summary: TaskCreated hook that rejects agent-task creation when required
         variety-related metadata is missing or malformed. Replaces the
         ephemeral _task_created_probe.py from #401 Commit #0.
Used by: hooks.json TaskCreated hook (no matcher — fires for all
         TaskCreate events system-wide; Python-side pass-through
         predicate handles scoping).

REJECT-ONLY per F8 architectural constraint (COMPONENT-DESIGN.md §Hook 2):
hooks cannot call TaskUpdate, so the validator cannot auto-populate
metadata.gates. The orchestrator writes `metadata.gates` at TaskCreate
time; this hook only rejects agent tasks that arrive without the
required variety fields.

DATA DISCIPLINE — DISK-READ-AUTHORITATIVE (not stdin-metadata-based):

The validator reads task metadata from disk via
`shared.task_utils._read_task_json` (hoisted in #401 Commit #4), never
from stdin. Stdin is used only for identifying fields
(`task_id`, `task_subject`, `team_name`). This matches the shipped
PACT-hook convention verbatim — see handoff_gate.py:242-253 and
teammate_idle.py — where stdin is optimization-only and disk is the
authoritative source.

Rationale for the discipline (see full investigation at
docs/investigations/2026-04-20-task-created-stdin-probe.md):

  1. Empirical probe observation of TaskCreated stdin shape was
     ATTEMPTED by backend-coder-1 (copied the Commit #0 probe to the
     installed plugin path at
     ~/.claude/plugins/cache/pact-marketplace/PACT/3.17.13/hooks/,
     triggered TaskCreates, looked for stderr output). Result:
     TaskCreated hook stderr is NOT surfaced to the teammate
     tool-response channel the way PreToolUse / PostToolUse /
     TaskCompleted / TeammateIdle stderr is. Stdin payload shape
     remains unobservable at runtime from a teammate's seat.
  2. Stdin field names for TaskCreated are INFERRED from the rev-repo
     source at /Users/mj/Sites/claude-code-rev/src/utils/hooks.ts:3745-3770
     (executeTaskCreatedHooks + TaskCreatedHookInputSchema):
     `{hook_event_name, task_id, task_subject, task_description,
     teammate_name, team_name}` + base hook fields. Strong inference
     but not empirically confirmed at runtime.
  3. Whether stdin includes `metadata` at all is UNKNOWN. Some
     platform-hook emission sites pass metadata through; others do
     not. Shipped PACT hooks treat metadata-on-stdin as OPTIMIZATION
     ONLY and always disk-read as the enforcement path.
  4. The task IS on disk at hook-time (TaskCreateTool.ts:81-89 calls
     `createTask()` BEFORE `executeTaskCreatedHooks()`, so the JSON
     file is authored first). On blocking exit-2, the platform rolls
     back via `deleteTask()` at TaskCreateTool.ts:109. Disk-read is
     reliable.

Pass-through predicate (_is_agent_dispatch_task): cheap O(1)
stdin + disk-metadata classification — is this TaskCreate worth
schema enforcement? Non-agent tasks (signal, blocker, secretary,
auditor, feature-level, phase-level) short-circuit to allow. The
predicate uses stdin's `task_subject` for prefix matching and the
(already-disk-read) metadata for type/completion_type/lifecycle
carve-outs. Reuses the agent-prefix convention from
shared.task_utils.find_active_agents:143-164 but with strict
lowercase leading-token matching (phase labels like ARCHITECT:
would otherwise collide with the architect: agent prefix under
the ambient lowercased-prefix form).

Validation rules (COMPONENT-DESIGN.md §Hook 2, CONTENT-SCHEMAS.md §D):
    - variety.total missing → reject
    - variety.total < TEACHBACK_BLOCKING_THRESHOLD (7) → pass
      (below-threshold tasks don't require the full metadata shape)
    - variety.{novelty,scope,uncertainty,risk} missing any → reject
    - variety.total >= TEACHBACK_FULL_PROTOCOL_VARIETY (9) AND
      required_scope_items empty/missing → reject
    - Sum-mismatch check is DEFERRED to handoff_gate (#401 Commit #6
      defense-in-depth at completion time)

SACROSANCT fail-open: ANY exception exits 0 with suppressOutput.
Validation failure is NOT an exception — it returns a deny string and
exits 2. Exceptions (IOError, JSONDecodeError, KeyError) allow
creation.

Input: JSON from stdin — shape documented above
Output:
    - stderr: deny message (exit 2)
    - stdout: {"suppressOutput": true} (exit 0)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure hooks dir is on sys.path for shared package imports (matches
# teammate_idle.py convention).
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared import (  # noqa: E402
    TEACHBACK_BLOCKING_THRESHOLD,
    TEACHBACK_FULL_PROTOCOL_VARIETY,
)
from shared.error_output import hook_error_json  # noqa: E402
import shared.pact_context as pact_context  # noqa: E402
from shared.pact_context import get_team_name  # noqa: E402
from shared.task_utils import _read_task_json  # noqa: E402

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Agent-task prefixes that warrant schema enforcement. Mirrors
# shared.task_utils.find_active_agents:142-155 verbatim (minus the
# two exempt signal-agent prefixes below).
_AGENT_PREFIXES: tuple[str, ...] = (
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
)

# Subject prefixes that bypass schema enforcement. secretary has a
# custom On Start flow; auditor is observation-only and uses
# signal-based completion. Locked in TERMINOLOGY-LOCK.md §Exempt agents.
_SIGNAL_AGENT_PREFIXES: tuple[str, ...] = (
    "secretary:",
    "auditor:",
)

# Variety dimension keys that must be present when variety.total >=
# TEACHBACK_BLOCKING_THRESHOLD. Locked in TERMINOLOGY-LOCK.md §Metadata
# field names (variety shape note). Order matches the canonical
# orchestrate.md write shape.
_VARIETY_DIMENSIONS: tuple[str, ...] = ("novelty", "scope", "uncertainty", "risk")


def _is_agent_dispatch_task(input_data: dict, metadata: dict) -> bool:
    """Return True if this TaskCreate warrants schema enforcement.

    Cheap stdin/metadata-only classification. O(1); no disk I/O.
    Separating this from validate_task_schema keeps the hook fast on
    the vast majority of task creations (signal, feature-level,
    phase-level) that shouldn't hit the validator at all.

    Bypass (return False) when:
      - metadata.type is "blocker" or "algedonic" (signal task)
      - metadata.completion_type == "signal" (auditor-style tasks)
      - metadata.skipped / stalled / terminated are truthy
      - task_subject starts with secretary: or auditor:
      - task_subject does NOT start with one of _AGENT_PREFIXES
        (feature-level, phase-level, unlabeled tasks — not agent
        dispatches; outside the teachback-gate domain)

    Args:
        input_data: Parsed TaskCreated stdin payload.
        metadata: Metadata dict read from disk (may be empty if the
            task file doesn't exist yet or was unreadable).

    Returns:
        True iff the TaskCreate should be validated against the
        variety-metadata schema.
    """
    # Signal and sentinel tasks are never agent-dispatch.
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata.get("type") in ("blocker", "algedonic"):
        return False
    if metadata.get("completion_type") == "signal":
        return False
    for flag in ("skipped", "stalled", "terminated"):
        if metadata.get(flag):
            return False

    subject = input_data.get("task_subject", "") or ""
    if not isinstance(subject, str) or not subject:
        return False

    # Phase tasks use ALL-CAPS labels ("PREPARE:", "ARCHITECT:", "CODE:",
    # "TEST:"); agent tasks use lowercase ("architect:", "backend-coder:",
    # ...). The collision between ARCHITECT:/architect: when casefolded
    # would misclassify phase tasks as agent tasks. Distinguish by the
    # leading token's case before the colon: only fully-lowercase leading
    # tokens count as agent dispatches.
    colon_idx = subject.find(":")
    if colon_idx <= 0:
        return False
    leading = subject[:colon_idx]
    if leading != leading.lower():
        # Mixed-case or all-caps leading token → phase task or
        # user-authored subject; never an agent dispatch.
        return False

    prefix_with_colon = f"{leading}:"

    # Exempt signal-agent subjects
    if prefix_with_colon in _SIGNAL_AGENT_PREFIXES:
        return False

    # Only subjects starting with an agent-type prefix are dispatch tasks.
    if prefix_with_colon in _AGENT_PREFIXES:
        return True

    return False


def _variety_missing_dimensions(variety: dict) -> list[str]:
    """Return list of missing dimension keys. Empty list → all present.

    The `total` key is NOT included in this check because the caller
    handles `total` missing as a separate error condition (it's the
    threshold-gating value).
    """
    missing: list[str] = []
    for dim in _VARIETY_DIMENSIONS:
        if dim not in variety or variety.get(dim) is None:
            missing.append(dim)
    return missing


def validate_task_schema(
    task_metadata: dict,
    task_subject: str,
    task_id: str = "",
) -> str | None:
    """Return an error message string (for stderr) or None.

    Validation rules (COMPONENT-DESIGN.md §Hook 2):

      1. variety.total missing → reject (agent task at schema-enforced
         subject needs a variety score).
      2. variety.total < TEACHBACK_BLOCKING_THRESHOLD → pass (below
         the gate threshold; no schema enforcement).
      3. variety.total present and >= THRESHOLD, but any of
         {novelty, scope, uncertainty, risk} missing → reject.
      4. variety.total >= TEACHBACK_FULL_PROTOCOL_VARIETY AND
         required_scope_items empty/missing → reject.

    Sum-mismatch (total != sum(dims)) is deferred to handoff_gate at
    TaskCompleted time (#401 Commit #6 defense-in-depth).

    Args:
        task_metadata: Metadata read from the task JSON file.
        task_subject: Subject line (for error-message context).
        task_id: Task id (for error-message context and TaskUpdate
            remediation hint).

    Returns:
        Error string on failure, None on pass.

    Fail-open: ANY exception returns None (allow creation). Main()
    wraps this call in try/except regardless.
    """
    try:
        variety = task_metadata.get("variety")
        if not isinstance(variety, dict):
            return _reject_missing_variety(task_id, task_subject)

        total = variety.get("total")
        if not isinstance(total, int) or isinstance(total, bool):
            # bool is int subclass — reject explicitly
            return _reject_missing_variety(task_id, task_subject)

        if total < TEACHBACK_BLOCKING_THRESHOLD:
            return None  # below-threshold tasks pass without schema check

        missing_dims = _variety_missing_dimensions(variety)
        if missing_dims:
            return _reject_missing_dimensions(
                task_id, task_subject, total, missing_dims
            )

        if total >= TEACHBACK_FULL_PROTOCOL_VARIETY:
            required_scope_items = task_metadata.get("required_scope_items")
            if not required_scope_items or not isinstance(required_scope_items, list):
                return _reject_missing_scope_items(task_id, task_subject, total)

        return None
    except Exception:
        # Fail-open on any validation-internal error
        return None


def _reject_missing_variety(task_id: str, task_subject: str) -> str:
    """Build reject message for missing/malformed metadata.variety.total."""
    return (
        f"TaskCreate blocked: agent task {task_id!r} ({task_subject!r}) requires "
        f"metadata.variety.total. Add to TaskCreate: "
        f"metadata={{\"variety\": {{\"total\": <int>, \"novelty\": <int>, "
        f"\"scope\": <int>, \"uncertainty\": <int>, \"risk\": <int>}}, "
        f"\"required_scope_items\": [...]}}.\n"
        f"See docs/architecture/teachback-gate/TERMINOLOGY-LOCK.md §Metadata "
        f"field names for the nested variety shape."
    )


def _reject_missing_dimensions(
    task_id: str, task_subject: str, total: int, missing: list[str]
) -> str:
    """Build reject message for incomplete variety dimensions."""
    return (
        f"TaskCreate blocked: agent task {task_id!r} ({task_subject!r}) has "
        f"metadata.variety.total={total} (>= threshold "
        f"{TEACHBACK_BLOCKING_THRESHOLD}) but is missing dimensions: "
        f"{', '.join(missing)}. Include all four dimensions "
        f"(novelty, scope, uncertainty, risk) in metadata.variety so the "
        f"teachback gate can compute protocol tier and validate the "
        f"dimension-sum at completion."
    )


def _reject_missing_scope_items(task_id: str, task_subject: str, total: int) -> str:
    """Build reject message for full-protocol task without required_scope_items."""
    return (
        f"TaskCreate blocked: agent task {task_id!r} ({task_subject!r}) at "
        f"variety {total} (>= full-protocol threshold "
        f"{TEACHBACK_FULL_PROTOCOL_VARIETY}) requires "
        f"metadata.required_scope_items (a non-empty list of named scope "
        f"items the teammate must address in their teachback). Add "
        f"required_scope_items=[\"<item_1>\", \"<item_2>\", ...] at TaskCreate."
    )


def main() -> None:
    try:
        try:
            raw = sys.stdin.read()
            input_data = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        if not isinstance(input_data, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)

        task_id = input_data.get("task_id") or ""
        task_subject = input_data.get("task_subject") or ""
        team_name = (input_data.get("team_name") or get_team_name() or "").lower() or None

        # Disk-read-authoritative: always read metadata from disk via
        # shared.task_utils._read_task_json. Mirrors handoff_gate.py:242
        # + teammate_idle.py convention. Whether TaskCreated stdin
        # includes `metadata` is unconfirmed at runtime (see module
        # docstring for the investigation); disk is the authoritative
        # source regardless. The task file is on disk at hook-time
        # because TaskCreateTool.ts:81-89 calls createTask() BEFORE
        # executeTaskCreatedHooks().
        task_data: dict[str, Any] = {}
        if task_id:
            task_data = _read_task_json(task_id, team_name)
        metadata = task_data.get("metadata") if isinstance(task_data, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}

        if not _is_agent_dispatch_task(input_data, metadata):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        error = validate_task_schema(metadata, task_subject, task_id)
        if error:
            print(error, file=sys.stderr)
            sys.exit(2)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Outer fail-open: any unhandled path allows creation.
        print(f"Hook warning (task_schema_validator): {e}", file=sys.stderr)
        print(hook_error_json("task_schema_validator", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
