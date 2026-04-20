"""
Location: pact-plugin/hooks/shared/teachback_scan.py
Summary: Hoisted scanner for the teachback gate (#401 Commit #7). Scans
         all in_progress tasks owned by an agent, classifies each task's
         teachback state from metadata content-presence, and returns a
         structured aggregate for teachback_gate.py's decision logic.
Used by: hooks/teachback_gate.py (PreToolUse).

Design:
  - **ALL-match semantics**: the gate denies if ANY in_progress task of
    the agent fails. Mirrors teachback_check.py:134-203's convention
    (F9 in the plan). This matters when a teammate is reassigned to
    multiple concurrent tasks — a stale approval on task A cannot
    satisfy the gate for task B.
  - **Single disk pass**: one `Path.iterdir()` + per-file JSON read,
    keyed by team_name. Cross-session safety guaranteed by scoping to
    `~/.claude/tasks/{team_name}/`.
  - **Content-presence inference** (STATE-MACHINE.md invariant #1): the
    scanner does NOT rely on `metadata.teachback_state` being written.
    Presence of `teachback_corrections` → correcting; presence of valid
    `teachback_approved` with `unaddressed==[]` → active; else presence
    of `teachback_submit` → under_review; else pending.
  - **Carve-outs fire first** (TERMINOLOGY-LOCK.md §Carve-out predicate
    order): signal tasks, skipped/stalled/terminated, low-variety tasks
    short-circuit to "pass" before any state classification.
  - **Fail-open**: OS errors / JSON errors / exceptions return a
    `task_count=0, all_active=True` summary so the gate allows by
    default. Mirrors teachback_check.py fail-open pattern.

Exposes:
    scan_teachback_state(agent_name, team_name, tasks_base_dir=None) -> dict
    is_exempt_agent(agent_name) -> bool
    _EXEMPT_AGENTS (frozenset — for drift-test consumption; MUST match
        teachback_check._EXEMPT_AGENTS verbatim)
    _classify_task_state(metadata, protocol_level) -> tuple[reason_code, state]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared import (
    TEACHBACK_BLOCKING_THRESHOLD,
    TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS,
    TEACHBACK_FULL_PROTOCOL_VARIETY,
)


# Exempt-agent frozenset — verbatim mirror of teachback_check._EXEMPT_AGENTS
# (TERMINOLOGY-LOCK.md §Exempt agents; pact-plugin/hooks/teachback_check.py:41-46).
# Drift test in test_teachback_scan.py asserts set-equality with that source.
_EXEMPT_AGENTS: frozenset[str] = frozenset({
    "secretary",
    "pact-secretary",
    "auditor",
    "pact-auditor",
})


# Reason codes returned by _classify_task_state. The empty string means
# the task is in the `active` state (gate allows); any non-empty code
# triggers a deny reason in teachback_gate.
_REASON_MISSING_SUBMIT = "missing_submit"       # T1 — pending
_REASON_INVALID_SUBMIT = "invalid_submit"       # T3 — schema-fail
_REASON_AWAITING_APPROVAL = "awaiting_approval"  # T2 — under_review
_REASON_UNADDRESSED_ITEMS = "unaddressed_items"  # T5 — auto-downgrade
_REASON_CORRECTIONS_PENDING = "corrections_pending"  # T6 — correcting

# Default fail-open summary returned on error / no-task / no-agent paths.
_DEFAULT_SUMMARY: dict[str, Any] = {
    "task_count": 0,
    "first_failing_task_id": "",
    "first_failing_reason": "",
    "first_failing_metadata": {},
    "first_failing_protocol_level": "exempt",
    "all_active": True,
}


def is_exempt_agent(agent_name: str) -> bool:
    """Return True iff agent_name (case-insensitive) is exempt from the
    teachback gate. Secretary has a custom On Start flow; auditor is
    observation-only. Locked in TERMINOLOGY-LOCK.md §Exempt agents."""
    if not isinstance(agent_name, str) or not agent_name:
        return False
    return agent_name.lower() in _EXEMPT_AGENTS


def _protocol_level(variety_total: int, required_scope_items: list | None) -> str:
    """Return 'exempt' | 'simplified' | 'full' per STATE-MACHINE.md §Q2.

    Args:
        variety_total: metadata.variety.total (int; 0 if absent).
        required_scope_items: metadata.required_scope_items list.

    Returns:
        "exempt" iff variety < threshold (7);
        "full"   iff variety >= 9 OR scope_items count >= 2;
        "simplified" otherwise.
    """
    if not isinstance(variety_total, int) or isinstance(variety_total, bool):
        return "exempt"
    if variety_total < TEACHBACK_BLOCKING_THRESHOLD:
        return "exempt"
    if variety_total >= TEACHBACK_FULL_PROTOCOL_VARIETY:
        return "full"
    count = len(required_scope_items) if isinstance(required_scope_items, list) else 0
    if count >= TEACHBACK_FULL_PROTOCOL_SCOPE_ITEMS:
        return "full"
    return "simplified"


def _submit_has_required_structure(submit: Any, protocol_level: str) -> bool:
    """Minimal structural validation of teachback_submit.

    Phase 1 gate body: content-shape checks from CONTENT-SCHEMAS.md are
    exercised by test_teachback_example.py templates, not enforced here.
    This function only checks PRESENCE of the protocol-required fields
    so the gate can distinguish "no submit", "malformed submit", and
    "submit present". Full field-by-field schema validation is TEST
    phase work (it touches citation regex, substring-inequality,
    token-sharing, template-blocklist checks which are out of scope for
    this hook's advisory-mode shape).
    """
    if not isinstance(submit, dict):
        return False

    # Universal minimum: understanding + first_action
    if not isinstance(submit.get("understanding"), str):
        return False
    if not submit.get("understanding", "").strip():
        return False

    first_action = submit.get("first_action")
    if not isinstance(first_action, dict):
        return False
    if not isinstance(first_action.get("action"), str):
        return False

    if protocol_level == "full":
        mlw = submit.get("most_likely_wrong")
        if not isinstance(mlw, dict):
            return False
        if not isinstance(mlw.get("assumption"), str):
            return False
        if not isinstance(mlw.get("consequence"), str):
            return False

        lci = submit.get("least_confident_item")
        if not isinstance(lci, dict):
            return False
        if not isinstance(lci.get("item"), str):
            return False

    return True


def _classify_task_state(
    task_metadata: dict, protocol_level: str
) -> tuple[str, str]:
    """Classify a single task's teachback state from its metadata.

    Precedence (STATE-MACHINE.md §Cooperative-write invariants #2, #3):
      1. corrections present                → correcting (T6)
      2. approved present, valid            → active (T4)
         - if unaddressed non-empty         → correcting auto-downgrade (T5)
         - if invalid structure             → correcting (conservative fallback)
      3. submit present, valid              → under_review (T2)
      4. submit present, invalid            → pending with invalid_submit (T3)
      5. no submit                          → pending (T1)

    Returns (reason_code, state):
      - reason_code == ""  → state == "active" (gate allows)
      - reason_code != ""  → one of the blocking states; gate denies

    The returned state string is one of TEACHBACK_STATES.
    """
    corrections = task_metadata.get("teachback_corrections")
    approved = task_metadata.get("teachback_approved")
    submit = task_metadata.get("teachback_submit")

    # T6 — corrections take precedence
    if isinstance(corrections, dict) and corrections:
        return (_REASON_CORRECTIONS_PENDING, "teachback_correcting")

    # T4/T5 — approved present
    if isinstance(approved, dict) and approved:
        conditions_met = approved.get("conditions_met")
        unaddressed = []
        if isinstance(conditions_met, dict):
            unaddressed = conditions_met.get("unaddressed") or []
        if isinstance(unaddressed, list) and unaddressed:
            # T5 auto-downgrade
            return (_REASON_UNADDRESSED_ITEMS, "teachback_correcting")
        # T4 — active
        return ("", "active")

    # T2/T3 — submit present
    if submit is not None:
        if _submit_has_required_structure(submit, protocol_level):
            return (_REASON_AWAITING_APPROVAL, "teachback_under_review")
        return (_REASON_INVALID_SUBMIT, "teachback_pending")

    # T1 — pending (no submit)
    return (_REASON_MISSING_SUBMIT, "teachback_pending")


def _is_carve_out_task(task_metadata: dict) -> bool:
    """Return True iff the task is in a carve-out (bypass the gate).

    Mirrors TERMINOLOGY-LOCK.md §Carve-out predicate order predicates
    2-5 + 7 (signal-type, completion_type, skipped/stalled/terminated,
    low-variety). Agent-exempt (predicate 6) is handled by the caller
    via is_exempt_agent() before this function is ever reached.
    """
    if not isinstance(task_metadata, dict):
        return True  # malformed metadata → fail-open bypass
    if task_metadata.get("type") in ("blocker", "algedonic"):
        return True
    if task_metadata.get("completion_type") == "signal":
        return True
    if task_metadata.get("skipped") or task_metadata.get("stalled") or task_metadata.get("terminated"):
        return True

    variety = task_metadata.get("variety")
    variety_total = 0
    if isinstance(variety, dict):
        t = variety.get("total")
        if isinstance(t, int) and not isinstance(t, bool):
            variety_total = t
    if variety_total < TEACHBACK_BLOCKING_THRESHOLD:
        return True

    return False


def scan_teachback_state(
    agent_name: str,
    team_name: str,
    tasks_base_dir: str | None = None,
) -> dict:
    """Scan all in_progress tasks owned by `agent_name` and return an
    aggregate teachback-state summary.

    Returns:
        {
            "task_count":                 int,
            "first_failing_task_id":      str (empty if all_active),
            "first_failing_reason":       str (one of _REASON_* or empty),
            "first_failing_metadata":     dict (the failing task's metadata,
                                               for context in deny reason),
            "first_failing_protocol_level": "exempt"|"simplified"|"full",
            "all_active":                 bool (True iff every in_progress
                                               task is in active state),
        }

    On fail-open (can't scan, no agent, no team, exception), returns
    _DEFAULT_SUMMARY (task_count=0, all_active=True) so the gate
    allows.
    """
    if not agent_name or not team_name:
        return dict(_DEFAULT_SUMMARY)

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    task_dir = Path(tasks_base_dir) / team_name
    if not task_dir.exists():
        return dict(_DEFAULT_SUMMARY)

    task_count = 0
    first_failing_task_id = ""
    first_failing_reason = ""
    first_failing_metadata: dict = {}
    first_failing_protocol_level = "exempt"
    all_active = True

    try:
        for task_file in sorted(task_dir.iterdir()):
            if not task_file.name.endswith(".json"):
                continue
            try:
                data = json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("owner") != agent_name:
                continue
            if data.get("status") != "in_progress":
                continue

            task_count += 1

            metadata = data.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}

            if _is_carve_out_task(metadata):
                continue

            variety = metadata.get("variety", {})
            variety_total = 0
            if isinstance(variety, dict):
                t = variety.get("total")
                if isinstance(t, int) and not isinstance(t, bool):
                    variety_total = t
            level = _protocol_level(
                variety_total, metadata.get("required_scope_items")
            )

            reason, _state = _classify_task_state(metadata, level)
            if reason:
                # ALL-match: mark not-all-active; track the FIRST failing
                # task (sorted iteration gives deterministic ordering).
                all_active = False
                if not first_failing_task_id:
                    first_failing_task_id = task_file.stem
                    first_failing_reason = reason
                    first_failing_metadata = metadata
                    first_failing_protocol_level = level
    except OSError:
        return dict(_DEFAULT_SUMMARY)

    return {
        "task_count": task_count,
        "first_failing_task_id": first_failing_task_id,
        "first_failing_reason": first_failing_reason,
        "first_failing_metadata": first_failing_metadata,
        "first_failing_protocol_level": first_failing_protocol_level,
        "all_active": all_active,
    }
