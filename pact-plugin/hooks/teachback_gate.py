#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teachback_gate.py
Summary: PreToolUse hook that blocks {Edit, Write, Agent, NotebookEdit}
         for teammates whose in_progress task is NOT in the `active`
         teachback state. Advisory mode (Phase 1) in this commit; flip
         to blocking via _TEACHBACK_MODE constant in Commit #14b.
Used by: hooks.json PreToolUse entry (MATCHERLESS; fires AFTER
         bootstrap_gate.py).

ARCHITECTURE (COMPONENT-DESIGN.md Hook 1):
  - Mirrors bootstrap_gate.py:53-103 shape verbatim (same _BLOCKED_TOOLS
    set; same fail-open JSON envelope; same deny-reason decision flow).
  - Bootstrap-gate ordering: bootstrap_gate is registered FIRST in
    hooks.json PreToolUse. If bootstrap marker is absent, bootstrap
    denies before teachback_gate runs. Teachback is meaningless until
    bootstrap completes. test_hooks_json.py
    TestBootstrapBeforeTeachbackGate asserts ordering.
  - PHASE 1 (this commit) — _TEACHBACK_MODE="advisory": deny paths emit
    systemMessage (exit 0) so work continues but observability fires.
    Emits teachback_gate_advisory journal events with
    would_have_blocked=True + reason_code + tool_name for the Phase 2
    readiness diagnostic (scripts/check_teachback_phase2_readiness.py,
    Commit #13).
  - PHASE 2 (Commit #14b) — _TEACHBACK_MODE="blocking": deny paths emit
    hookSpecificOutput.permissionDecision=deny (exit 2) and write
    teachback_gate_blocked events.

SACROSANCT fail-open: ANY exception at ANY layer exits 0 with
suppressOutput. Mirrors bootstrap_gate.py:105-118.

Input: JSON from stdin (PreToolUse payload: tool_name, tool_input,
       session_id, team_name, teammate_name, etc.)
Output:
    Phase 1 deny: {"systemMessage": "<reason>"}, exit 0
    Phase 2 deny: {"hookSpecificOutput": {...deny...}}, exit 2
    Allow:       {"suppressOutput": true}, exit 0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure hooks dir is on sys.path for shared package imports.
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared import (  # noqa: E402
    TEACHBACK_BLOCKING_THRESHOLD,
    TEACHBACK_MODE_ADVISORY,
    TEACHBACK_MODE_BLOCKING,
)
from shared.error_output import hook_error_json  # noqa: E402
import shared.pact_context as pact_context  # noqa: E402
from shared.pact_context import get_team_name, resolve_agent_name  # noqa: E402
from shared.session_journal import append_event, make_event, read_events  # noqa: E402
from shared.teachback_example import format_deny_reason  # noqa: E402
from shared.teachback_scan import (  # noqa: E402
    is_exempt_agent,
    scan_teachback_state,
)
from shared.teachback_validate import (  # noqa: E402
    FieldError,
    validate_approved,
    validate_submit,
)


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


# Phase 1 default: advisory mode. Flip to blocking in Commit #14b once
# scripts/check_teachback_phase2_readiness.py reports zero false-positives
# over >= 2 consecutive variety>=7 workflows (ship condition F10).
_TEACHBACK_MODE: str = TEACHBACK_MODE_ADVISORY


# Blocked-tool set — mirrors bootstrap_gate.py:53-58 verbatim
# (TERMINOLOGY-LOCK.md §Blocked-tool set).
_BLOCKED_TOOLS = frozenset({
    "Edit",
    "Write",
    "Agent",
    "NotebookEdit",
})


def _check_tool_allowed(input_data: dict) -> tuple[str | None, dict]:
    """Determine whether the PreToolUse should be denied.

    Returns (deny_reason_string, context_dict):
      - deny_reason_string is None when the tool call should be allowed.
      - context_dict has the telemetry fields used by main() to build
        journal events (reason_code, tool_name, task_id, agent_name).

    Never raises — caller wraps in try/except for fail-open. Schema/IO
    errors during scan or metadata read are fail-open inside
    scan_teachback_state itself.
    """
    pact_context.init(input_data)

    tool_name = input_data.get("tool_name", "")

    # MCP tools always allowed (matches bootstrap_gate:93-94 convention).
    if isinstance(tool_name, str) and tool_name.startswith("mcp__"):
        return (None, {})

    # Only gate a small hot-path set.
    if tool_name not in _BLOCKED_TOOLS:
        return (None, {})

    agent_name = resolve_agent_name(input_data)
    if not agent_name:
        # Orchestrator or non-PACT context — gate doesn't apply.
        return (None, {})

    # Agent-level exempt (secretary, auditor).
    if is_exempt_agent(agent_name):
        return (None, {})

    team_name = (input_data.get("team_name") or get_team_name() or "").lower()
    if not team_name:
        return (None, {})

    scan = scan_teachback_state(agent_name, team_name)
    if scan["task_count"] == 0:
        # No in_progress task for this agent — nothing to gate.
        return (None, {})

    if scan["all_active"]:
        # R2-A1 fix: every in_progress task passed the scanner's
        # STRUCTURAL classification (T4 active branch). We MUST still
        # run the full content validator on each active task's
        # teachback_approved + teachback_submit — otherwise a lead can
        # rubber-stamp with the minimal shape
        # `{"teachback_approved": {"conditions_met": {"unaddressed": []}}}`
        # and bypass every generation-shaped check (substring-
        # inequality, evidence-substring grounding, addressed-item
        # membership, verdict/match vocabulary, first_action_check
        # citation). If ANY active task fails content validation,
        # upgrade to an invalid_submit deny.
        content_deny = _check_active_tasks_content(
            scan.get("active_tasks") or [],
            agent_name,
            tool_name,
        )
        if content_deny is not None:
            return content_deny

        # All active tasks passed content validation — observability:
        # emit teachback_state_transition(to_state="active",
        # trigger="lead_approve") for each (de-dup'd by task_id per
        # JOURNAL-EVENTS.md §Event 3 semantics). Closes R2-A2: the
        # lead_approve transition was previously write-only dead code
        # in the controlled vocabulary because this emission site was
        # missing.
        for task_id, _metadata, _level in (scan.get("active_tasks") or []):
            try:
                _emit_state_transition_if_changed(
                    task_id=task_id, agent=agent_name, to_state="active",
                )
            except Exception:
                # Fail-open — observability must never block the gate.
                pass

        return (None, {})

    # At least one in_progress task is NOT active — deny.
    reason_code = scan["first_failing_reason"]
    task_id = scan["first_failing_task_id"]
    metadata = scan["first_failing_metadata"] or {}
    protocol_level = scan["first_failing_protocol_level"] or "full"

    variety_total = 0
    variety = metadata.get("variety")
    if isinstance(variety, dict):
        t = variety.get("total")
        if isinstance(t, int) and not isinstance(t, bool):
            variety_total = t

    # Full content-shape validation (Y2) — Phase 1 gate exercises the
    # complete CONTENT-SCHEMAS.md rule surface so the Phase 2 readiness
    # diagnostic produces a meaningful false-positive signal. If the
    # scanner classified a task as awaiting_approval (structurally-valid
    # submit), the full validator may still find per-field errors that
    # upgrade the reason to invalid_submit.
    submit = metadata.get("teachback_submit") if isinstance(metadata, dict) else None
    approved = metadata.get("teachback_approved") if isinstance(metadata, dict) else None

    submit_errors: list[FieldError] = []
    approved_errors: list[FieldError] = []
    try:
        if isinstance(submit, dict):
            submit_errors = validate_submit(
                submit, metadata, protocol_level, agent_name
            )
        if isinstance(approved, dict):
            approved_errors = validate_approved(
                approved, submit, metadata, protocol_level, agent_name
            )
    except Exception:
        # Fail-open on validator-internal exception — scanner's
        # structural classification still drives reason_code.
        submit_errors = []
        approved_errors = []

    first_error: FieldError | None = None
    if reason_code == "awaiting_approval" and submit_errors:
        # Structurally valid but semantically invalid — upgrade reason.
        reason_code = "invalid_submit"
        first_error = submit_errors[0]
    elif reason_code == "invalid_submit" and submit_errors:
        first_error = submit_errors[0]
    elif reason_code == "unaddressed_items" and approved_errors:
        # Invalid approved structure takes precedence over the mere
        # unaddressed_items signal so the lead sees the schema error.
        reason_code = "invalid_submit"
        first_error = approved_errors[0]

    # Build deny-reason string via the shared templates (Commit #3).
    context = {
        "task_id": task_id,
        "tool_name": tool_name,
        "variety_total": variety_total,
        "threshold": TEACHBACK_BLOCKING_THRESHOLD,
        "required_scope_items": metadata.get("required_scope_items") or [],
    }

    # Enrich context for reasons that need extra fields.
    if reason_code == "unaddressed_items":
        approved_dict = metadata.get("teachback_approved", {}) or {}
        cm = approved_dict.get("conditions_met", {}) or {}
        context["unaddressed"] = cm.get("unaddressed") or []
    elif reason_code == "corrections_pending":
        corrections = metadata.get("teachback_corrections", {}) or {}
        context["corrections_issues"] = corrections.get("issues") or []
        context["corrections_targets"] = corrections.get(
            "request_revisions_on"
        ) or []
    elif reason_code == "invalid_submit":
        if first_error is not None:
            context["fail_field"] = first_error.field
            context["fail_error"] = first_error.error
            context["actual_value"] = first_error.actual_value
        else:
            # Fallback when the scanner flagged invalid_submit but the
            # full validator found no per-field error (e.g. submit key
            # is None rather than a dict). Surface a minimal hint.
            context["fail_field"] = "teachback_submit"
            context["fail_error"] = (
                "missing required fields for the {} protocol level"
                .format(protocol_level)
            )
            context["actual_value"] = str(submit)[:200] if submit is not None else ""

    deny_reason = format_deny_reason(reason_code, context, protocol_level)

    # State transition emission (Y1). Derive the inferred to_state from
    # the final reason_code and emit a teachback_state_transition event
    # only if the last-emitted to_state for this task_id in this session
    # was different. Per JOURNAL-EVENTS.md §Event 3 de-dupe rule.
    to_state = _state_from_reason(reason_code)
    try:
        _emit_state_transition_if_changed(
            task_id=task_id, agent=agent_name, to_state=to_state,
        )
    except Exception:
        # Fail-open — observability must never block the gate.
        pass

    telemetry = {
        "reason_code": reason_code,
        "tool_name": tool_name if isinstance(tool_name, str) else "",
        "task_id": task_id,
        "agent_name": agent_name,
    }
    return (deny_reason, telemetry)


def _check_active_tasks_content(
    active_tasks: list,
    agent_name: str,
    tool_name: str,
) -> tuple[str | None, dict] | None:
    """Run full content validation on every structurally-active task.

    R2-A1 fix: `scan["all_active"] == True` guarantees each task passed
    the scanner's T4 structural classification (teachback_approved is
    a dict AND conditions_met is a dict AND unaddressed is empty-list).
    It does NOT run the generation-shaped content rules from
    CONTENT-SCHEMAS.md §B (substring-inequality, evidence-substring
    grounding, addressed-item membership, verdict/match vocabulary,
    first_action_check citation, grounding-shape, template-density).
    This helper closes that gap by iterating every active task and
    running both `validate_submit` (when present at full protocol) and
    `validate_approved`. On the FIRST task with any content error, it
    returns a deny tuple shaped exactly like `_check_tool_allowed` so
    the caller can return directly.

    Args:
        active_tasks: list of (task_id, metadata, protocol_level) tuples
            from `scan["active_tasks"]`.
        agent_name: teammate name (for citation-strictness fallback).
        tool_name: tool being gated (for the deny-reason template).

    Returns:
        - None if every active task passes content validation (caller
          proceeds to emit the active-state transition and allow).
        - (deny_reason_string, telemetry_dict) when any active task
          fails. Matches `_check_tool_allowed`'s return shape exactly.

    Fail-open on validator-internal exception: treat as pass so a
    validator bug cannot block legitimate work (SACROSANCT). Caller
    already wraps in outer try/except for further defense in depth.
    """
    for task_id, metadata, protocol_level in active_tasks:
        if not isinstance(metadata, dict):
            continue

        submit = metadata.get("teachback_submit")
        approved = metadata.get("teachback_approved")

        submit_errors: list[FieldError] = []
        approved_errors: list[FieldError] = []
        try:
            if isinstance(submit, dict):
                submit_errors = validate_submit(
                    submit, metadata, protocol_level, agent_name
                )
            if isinstance(approved, dict):
                approved_errors = validate_approved(
                    approved, submit, metadata, protocol_level, agent_name
                )
        except Exception:
            # Fail-open on validator-internal exception.
            continue

        first_error: FieldError | None = None
        if approved_errors:
            first_error = approved_errors[0]
        elif submit_errors:
            first_error = submit_errors[0]

        if first_error is None:
            # This task passed content validation.
            continue

        # Build an invalid_submit deny response mirroring the shape
        # from _check_tool_allowed's non-active branch. The
        # active-path failure semantically matches invalid_submit
        # (schema / content-shape failure) rather than a distinct
        # "invalid_approved" reason — CONTENT-SCHEMAS.md §Deny Reason
        # Shapes defines only 5 codes, and invalid_submit's template
        # handles per-field errors for both submit AND approved fields.
        variety_total = 0
        variety = metadata.get("variety")
        if isinstance(variety, dict):
            t = variety.get("total")
            if isinstance(t, int) and not isinstance(t, bool):
                variety_total = t

        context = {
            "task_id": task_id,
            "tool_name": tool_name,
            "variety_total": variety_total,
            "threshold": TEACHBACK_BLOCKING_THRESHOLD,
            "required_scope_items": metadata.get("required_scope_items") or [],
            "fail_field": first_error.field,
            "fail_error": first_error.error,
            "actual_value": first_error.actual_value,
        }
        deny_reason = format_deny_reason(
            "invalid_submit", context, protocol_level
        )

        # Emit state_transition per JOURNAL-EVENTS.md §Event 3 — the
        # observed state is teachback_pending because content is
        # semantically invalid even though structurally approved was
        # present. Fail-open.
        try:
            _emit_state_transition_if_changed(
                task_id=task_id, agent=agent_name,
                to_state="teachback_pending",
            )
        except Exception:
            pass

        telemetry = {
            "reason_code": "invalid_submit",
            "tool_name": tool_name if isinstance(tool_name, str) else "",
            "task_id": task_id,
            "agent_name": agent_name,
        }
        return (deny_reason, telemetry)

    return None


# Map reason_code -> state_name for teachback_state_transition emission.
# Locked in STATE-MACHINE.md §Per-Transition Journal Events + aligned
# with shared.teachback_scan._classify_task_state return values.
_REASON_TO_STATE: dict[str, str] = {
    "missing_submit": "teachback_pending",
    "invalid_submit": "teachback_pending",
    "awaiting_approval": "teachback_under_review",
    "unaddressed_items": "teachback_correcting",
    "corrections_pending": "teachback_correcting",
}


def _state_from_reason(reason_code: str) -> str:
    """Return the state_name for a given gate reason_code. Falls back to
    'teachback_pending' on unknown codes (most conservative state)."""
    return _REASON_TO_STATE.get(reason_code, "teachback_pending")


def _emit_state_transition_if_changed(
    task_id: str, agent: str, to_state: str
) -> None:
    """Emit a teachback_state_transition event iff the target state
    differs from the most recent transition observed for this task_id
    in the current session's journal.

    Per JOURNAL-EVENTS.md §Event 3 de-dupe rule: one read per PreToolUse
    invocation (~5ms budget, judged acceptable by architect given
    PreToolUse is human-synchronous). Reads the session journal
    filtered to "teachback_state_transition" events, filters by task_id
    in Python, compares latest to_state, and emits only on change.

    Cross-session behavior: each session starts with an empty transition
    history from its own journal, so the first PreToolUse in a new
    session emits a transition even if the task was already in this
    state at the end of the prior session. That's the intended
    observability — "which transitions happened THIS session" is the
    load-bearing signal for the Phase 2 readiness diagnostic.

    Fail-open on any error (journal read failure, make_event/append_event
    exception, missing session context). Mirrors the advisory-event
    emitter's fail-open pattern.
    """
    try:
        prior = read_events("teachback_state_transition")
    except Exception:
        prior = []

    last_to_state = ""
    if isinstance(prior, list):
        for event in reversed(prior):
            if not isinstance(event, dict):
                continue
            if event.get("task_id") != task_id:
                continue
            candidate = event.get("to_state", "")
            if isinstance(candidate, str):
                last_to_state = candidate
                break

    if last_to_state == to_state:
        return  # de-dupe: no transition observed

    from_state = last_to_state or ""  # empty string means no prior
    trigger = _trigger_for_transition(from_state, to_state)

    event_fields: dict = {
        "task_id": task_id,
        "agent": agent,
        "to_state": to_state,
    }
    if from_state:
        event_fields["from_state"] = from_state
    if trigger:
        event_fields["trigger"] = trigger

    try:
        append_event(make_event("teachback_state_transition", **event_fields))
    except Exception:
        pass


def _trigger_for_transition(from_state: str, to_state: str) -> str:
    """Infer the trigger vocabulary term from the state pair per
    JOURNAL-EVENTS.md §Trigger values controlled vocabulary.

    Returns one of: teammate_submit | lead_approve | lead_correct |
    auto_downgrade | teammate_revise | content_invalid | unknown.
    """
    if from_state == "" and to_state == "teachback_under_review":
        return "teammate_submit"
    if from_state == "teachback_pending" and to_state == "teachback_under_review":
        return "teammate_submit"
    if to_state == "active":
        return "lead_approve"
    if from_state == "teachback_under_review" and to_state == "teachback_correcting":
        # Ambiguous between lead_correct and auto_downgrade from the gate's
        # seat. Bias toward lead_correct (the documented-write case);
        # auto_downgrade is emitted only when the gate observes approved
        # with unaddressed non-empty but absent corrections — caller
        # can override via the signal path if needed.
        return "lead_correct"
    if from_state == "teachback_correcting" and to_state == "teachback_under_review":
        return "teammate_revise"
    if from_state == "active" and to_state == "teachback_pending":
        # R2-A1 active-path content validation failure: scanner classified
        # the task as structurally active, but _check_active_tasks_content
        # found a generation-shape error (substring-inequality, citation,
        # template-density, etc.) and is denying with invalid_submit. The
        # to_state emits at teachback_pending even though from_state=active
        # — this transition is NOT a teammate revise or a lead approve; it
        # is the gate observing that an already-approved teachback fails
        # content validation. M2 (round 3) controlled-vocab expansion.
        return "content_invalid"
    return "unknown"


def _emit_advisory_event(telemetry: dict) -> None:
    """Emit the teachback_gate_advisory journal event (Phase 1).
    Fail-open on any journal error — observability is optional.
    """
    try:
        append_event(
            make_event(
                "teachback_gate_advisory",
                task_id=telemetry.get("task_id", ""),
                agent=telemetry.get("agent_name", ""),
                would_have_blocked=True,
                reason=telemetry.get("reason_code", ""),
                tool_name=telemetry.get("tool_name", ""),
            )
        )
    except Exception:
        pass


def _emit_blocked_event(telemetry: dict) -> None:
    """Emit the teachback_gate_blocked journal event (Phase 2).
    Fail-open on any journal error.
    """
    try:
        append_event(
            make_event(
                "teachback_gate_blocked",
                task_id=telemetry.get("task_id", ""),
                agent=telemetry.get("agent_name", ""),
                reason=telemetry.get("reason_code", ""),
                tool_name=telemetry.get("tool_name", ""),
            )
        )
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason, telemetry = _check_tool_allowed(input_data)
    except Exception as e:
        # SACROSANCT fail-open: any gate-internal exception allows the tool.
        print(f"Hook warning (teachback_gate): {e}", file=sys.stderr)
        print(hook_error_json("teachback_gate", e))
        sys.exit(0)

    if not deny_reason:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Deny branches diverge by mode (Phase 1 advisory vs Phase 2 blocking).
    if _TEACHBACK_MODE == TEACHBACK_MODE_BLOCKING:
        _emit_blocked_event(telemetry)
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    # Default path: advisory mode — emit systemMessage at exit 0.
    _emit_advisory_event(telemetry)
    output = {"systemMessage": deny_reason}
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
