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
from shared.session_journal import append_event, make_event  # noqa: E402
from shared.teachback_example import format_deny_reason  # noqa: E402
from shared.teachback_scan import (  # noqa: E402
    is_exempt_agent,
    scan_teachback_state,
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
        approved = metadata.get("teachback_approved", {}) or {}
        cm = approved.get("conditions_met", {}) or {}
        context["unaddressed"] = cm.get("unaddressed") or []
    elif reason_code == "corrections_pending":
        corrections = metadata.get("teachback_corrections", {}) or {}
        context["corrections_issues"] = corrections.get("issues") or []
        context["corrections_targets"] = corrections.get(
            "request_revisions_on"
        ) or []
    elif reason_code == "invalid_submit":
        # Phase 1: minimal hint; TEST phase adds per-field detail
        context["fail_field"] = "teachback_submit"
        context["fail_error"] = "missing required fields for protocol level"
        context["actual_value"] = "<see teachback_submit metadata>"

    deny_reason = format_deny_reason(reason_code, context, protocol_level)

    telemetry = {
        "reason_code": reason_code,
        "tool_name": tool_name if isinstance(tool_name, str) else "",
        "task_id": task_id,
        "agent_name": agent_name,
    }
    return (deny_reason, telemetry)


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
