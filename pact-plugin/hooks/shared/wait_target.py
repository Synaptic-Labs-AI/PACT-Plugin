"""
Location: pact-plugin/hooks/shared/wait_target.py
Summary: Deliverable predicate registry for the structural wait mechanism.
         ONE evaluation pass of a watched task file, classified into an
         outcome the caller maps onto its exit contract. The registry is the
         keying seam: changing WHAT counts as "the deliverable arrived" is a
         predicate name in the caller's command text, never schema or output
         surgery.
Used by: hooks/pact_wait.py (the watcher CLI: registry lookup + poll loop),
         hooks/task_lifecycle_gate.py and hooks/wait_filler_gate.py (arm
         command emission names these predicates), tests/test_pact_wait.py.

Contract: pure functions; never raise. Every filesystem error path resolves
to an explicit outcome constant — the caller decides loudness, not this
module. Target containment is delegated to the shared task_utils seam
(``task_dir_for``): the team name is validated and joined AS GIVEN with the
symlink-escape anchor, never re-implemented here.

Public surface:
- EVAL_* outcome constants — one evaluation pass, mutually exclusive:
    EVAL_PRESENT            deliverable present (predicate satisfied)
    EVAL_NOT_PRESENT        task file parsed, predicate not satisfied
    EVAL_TARGET_MISSING     team dir unresolvable or task file absent
    EVAL_TARGET_UNREADABLE  task file exists but cannot be read (OSError)
    EVAL_TARGET_CORRUPT     task file read but not a JSON object
    EVAL_UNKNOWN_PREDICATE  predicate_name not in PREDICATES
- handoff_present(task) — `handoff` predicate: metadata.handoff with a
  non-empty `produced` STRING.
- teachback_submit_present(task) — `teachback_submit` predicate.
- PREDICATES — name -> predicate mapping (the registry).
- evaluate_target(team_name, task_id, predicate_name, tasks_base_dir=None)
  — ONE pass: resolve, read, parse, evaluate.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from shared.task_utils import task_dir_for
from shared.paths import get_claude_config_dir


# Outcome constants (plain strings, not an Enum, matching the house style of
# intentional_wait's vocabulary constants). Membership groups in the CLI:
# the first two are "keep polling or declare fired", the rest are terminal.
EVAL_PRESENT = "present"
EVAL_NOT_PRESENT = "not_present"
EVAL_TARGET_MISSING = "target_missing"
EVAL_TARGET_UNREADABLE = "target_unreadable"
EVAL_TARGET_CORRUPT = "target_corrupt"
EVAL_UNKNOWN_PREDICATE = "unknown_predicate"


def handoff_present(task: dict[str, Any]) -> bool:
    """The `handoff` predicate: True iff the task carries a HANDOFF whose
    `produced` field is a non-empty STRING.

    Keying per hooks/shared/handoff_schema.py: `produced` is a REQUIRED
    canonical field and the watch keys on the canonical key only. It does
    NOT resolve the legacy alias spellings that read-side consumers
    (handoff_merge.py, session_resume.py — via resolve_handoff_field)
    accept: a handoff whose `produced` sits under a legacy spelling passes
    lead-side reads yet reads as ABSENT here, so the watch burns its
    deadline to a loud EXPIRED instead of firing. That degraded-loud
    asymmetry is accepted, not hidden. STRING is load-bearing, not pedantry: the
    live failures this mechanism answers were handoffs whose fields were
    JSON ARRAYS — a list-typed `produced` is not a delivered summary, so it
    must read as absent. Membership-grade emptiness ("" only, since a
    non-str already failed the type check) rather than truthiness; the
    strip catches a whitespace-only placeholder.
    """
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    handoff = metadata.get("handoff")
    if not isinstance(handoff, dict):
        return False
    produced = handoff.get("produced")
    return isinstance(produced, str) and produced.strip() != ""


def teachback_submit_present(task: dict[str, Any]) -> bool:
    """The `teachback_submit` predicate: True iff the task carries a
    metadata.teachback_submit object with at least one field.

    Presence of the sub-object is the witness, not its schema conformance:
    a submitted teachback that the lead later rejects REPLACES the object
    (same key), so the watch cannot distinguish accepted from rejected —
    and must not try. Rejection is signalled to the arming lead by the
    wake itself, not by the watcher.
    """
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    teachback = metadata.get("teachback_submit")
    return isinstance(teachback, dict) and len(teachback) > 0


# The registry. Adding a predicate is one function + one entry; the arm
# command then names it. NEVER key a new predicate on a field that is not
# required by its schema module (handoff_schema.py, teachback_schema.py):
# a recommended-only field goes dark on legitimate handoffs and the watch
# silently burns its whole deadline.
PREDICATES: dict[str, Callable[[dict[str, Any]], bool]] = {
    "handoff": handoff_present,
    "teachback_submit": teachback_submit_present,
}


def evaluate_target(
    team_name: str,
    task_id: str,
    predicate_name: str,
    tasks_base_dir: str | None = None,
) -> str:
    """ONE evaluation pass of the watched deliverable; returns an EVAL_*
    outcome. Polling (when to call this again) is the CLI's concern.

    Reads exactly ``tasks/{team_name}/{task_id}.json`` under the tasks base
    (default the harness config dir). task_id is sanitized to the same
    single-component form read_task_json uses, then joined inside the
    containment-validated team dir. The team dir resolves under the RAW
    name only (``task_dir_for``, never folded): a legacy non-lowercase
    team name reads MISSING here while read-side consumers resolve
    folded-first candidates — accepted, production team names are minted
    lowercase.

    Args:
        team_name: The {team_name} path component under the tasks root.
        task_id: Task identifier (the task file's stem).
        predicate_name: A PREDICATES key; anything else resolves to
            EVAL_UNKNOWN_PREDICATE (never raises).
        tasks_base_dir: Override for the tasks base dir (testing only).
    """
    check = PREDICATES.get(predicate_name) if isinstance(predicate_name, str) else None
    if check is None:
        return EVAL_UNKNOWN_PREDICATE
    if not isinstance(task_id, str) or not task_id:
        return EVAL_TARGET_MISSING
    sanitized = re.sub(r"[/\\]|\.\.", "", task_id)
    if not sanitized:
        return EVAL_TARGET_MISSING
    base = tasks_base_dir if tasks_base_dir else str(get_claude_config_dir() / "tasks")
    team_dir = task_dir_for(team_name, tasks_base_dir=base)
    if team_dir is None:
        return EVAL_TARGET_MISSING
    task_file = team_dir / f"{sanitized}.json"
    if not task_file.exists():
        return EVAL_TARGET_MISSING
    try:
        raw = task_file.read_text(encoding="utf-8")
    except OSError:
        return EVAL_TARGET_UNREADABLE
    except ValueError:
        # UnicodeDecodeError is a ValueError: bytes that are not valid
        # UTF-8 are an unusable payload — CORRUPT-class, never a crash
        # (parity with read_task_json's M2 hardening, which catches the
        # same ValueError family).
        return EVAL_TARGET_CORRUPT
    try:
        task = json.loads(raw)
    except json.JSONDecodeError:
        return EVAL_TARGET_CORRUPT
    if not isinstance(task, dict):
        return EVAL_TARGET_CORRUPT
    return EVAL_PRESENT if check(task) else EVAL_NOT_PRESENT
