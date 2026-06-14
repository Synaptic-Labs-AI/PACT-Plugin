#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/handoff_ordering_gate.py
Summary: PreToolUse hook (matcher="TaskUpdate") that WARNS the lead when a
         TaskUpdate(status="completed") lands on a HANDOFF-expecting task whose
         metadata.handoff is not yet present on disk — the #956 write-after-
         completion ordering mistake. Advisory only (additionalContext); NEVER
         denies.
Used by: hooks.json PreToolUse hook (matcher="TaskUpdate")

This is the NUDGE half of the #956 fix (defense-in-depth). The load-bearing
half is the write-time BACKSTOP in task_lifecycle_gate.py's
`TaskUpdate && status != "completed"` block, which GUARANTEES the agent_handoff
re-emits when handoff is set later. This gate only surfaces an actionable
advisory so the lead can do handoff-then-complete in the clean order; it does
NOT block — a completing TaskUpdate always proceeds.

WHY WARN, NEVER DENY (architect D2): actor attribution is unreliable on
PreToolUse stdin (no agentId; CLAUDE.md "SendMessage is unhookable" corollary),
so a deny on a misjudged case would strand a legitimate completion → livelock on
the completion-authority path, which is worse than the data-loss bug. The
backstop already recovers prevention's full value. So the posture here is
fail-OPEN on EVERY path — including module-load failure: a WARN gate must never
deny, and a crashed PreToolUse hook (exit 1) is treated as non-blocking by the
platform (the fail-open outcome), so on load failure we simply suppress + exit 0
rather than denying like the fail-CLOSED gates (bootstrap_gate / pin_*_gate).

WHY PreToolUse (not PostToolUse): the choice is about advisory TIMING, not
deny power — this gate never denies on EITHER event. PreToolUse surfaces the
nudge in the SAME turn, BEFORE the completion lands, so the lead can choose
handoff-then-complete in the clean order while the decision is still live. A
PostToolUse advisory would arrive after the completion already applied — too
late to reorder. (The backstop, which DOES need the after-state, lives on the
PostToolUse lifecycle gate; this nudge wants the before-state.)

DUAL-MODE: lead-frame-only. The advisory is for the lead performing the
completion; key on pact_context.is_lead (reads agent_type — the only tmux-safe
discriminator; agent_id/team_name are absent on tmux frames). Emit nothing in a
teammate frame.

Input: JSON from stdin with tool_name, tool_input, agent_type, etc.
Output: JSON with hookSpecificOutput.additionalContext (advisory case) or
        {"suppressOutput": true} (allow / passthrough). ALWAYS exit 0.
"""

from __future__ import annotations

# ─── stdlib first (used on the input-side fail-open BEFORE wrapped imports) ─
import json
import sys

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Cap on the stdin read. Real PreToolUse TaskUpdate frames carry a tool_input
# (taskId + small metadata) and stay well under this; an over-cap frame
# truncates mid-JSON → JSONDecodeError → input-side fail-open. Bounds memory
# only; does not reject sub-cap input. Mirrors the gate twins' 8 MB cap.
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


# ─── fail-OPEN wrapper on cross-package imports ────────────────────────────
# A WARN gate must NEVER deny. If an import below raises, we suppress + exit 0
# (fail-open) rather than emitting a deny — unlike the fail-CLOSED deny gates.
# A crashed hook (exit 1) is ALSO non-blocking on PreToolUse, so even an
# un-caught raise degrades to fail-open; the explicit catch keeps the exit code
# clean (0) and the output well-formed.
try:
    import shared.pact_context as pact_context
    from shared.intentional_wait import is_self_complete_exempt
    from shared.task_utils import is_teachback_subject, read_task_json
    _IMPORTS_OK = True
except BaseException:  # noqa: BLE001 — fail-OPEN catch-all (warn gate never denies)
    _IMPORTS_OK = False


def _evaluate(input_data: dict) -> str | None:
    """Return an actionable advisory string when the completing TaskUpdate is
    the #956 ordering mistake, else None.

    The ordering mistake = a lead-frame TaskUpdate(status="completed") on a
    HANDOFF-expecting task whose metadata.handoff is absent BOTH in this update
    (incoming) AND on disk (existing). Pure-ish read; never denies.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name != "TaskUpdate":
        return None  # matcher already scopes this, but be defensive

    # DUAL-MODE: lead frame only. is_lead reads agent_type (structural,
    # mode-agnostic). A teammate frame emits nothing.
    if not pact_context.is_lead(input_data):
        return None

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    if tool_input.get("status") != "completed":
        return None  # only completion transitions

    # Is handoff being set in THIS same TaskUpdate? Then it is a bundled
    # handoff+complete — no race, no warn.
    incoming_metadata = tool_input.get("metadata")
    incoming_handoff = (
        incoming_metadata.get("handoff")
        if isinstance(incoming_metadata, dict)
        else None
    )
    if isinstance(incoming_handoff, dict) and incoming_handoff:
        return None

    # Read CURRENT on-disk task state (PreToolUse: the update has NOT applied
    # yet). team_name resolved via pact_context (init seeds the context path).
    task_id = tool_input.get("taskId", "") or ""
    if not task_id:
        return None
    try:
        pact_context.init(input_data)
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name:
        return None  # no team context → cannot resolve the task → bypass

    task = read_task_json(task_id, team_name)
    if not isinstance(task, dict) or not task:
        return None  # no task data → bypass (fail-open)

    # Handoff already on disk? Then completing is fine — no race.
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    existing_handoff = metadata.get("handoff")
    if isinstance(existing_handoff, dict) and existing_handoff:
        return None

    # HANDOFF-expecting predicate (the SSOT-reuse composition):
    #   exempt(task)            = is_self_complete_exempt(task, team_name)   # secretary + signal-task
    #                             OR is_teachback_subject(subject)            # Task-A
    #   handoff_expecting(task) = owner is a non-empty string (teammate, bare
    #                             name) AND NOT exempt(task)
    owner = task.get("owner") or ""
    if not isinstance(owner, str) or not owner.strip():
        return None  # no owner → not a teammate work task
    subject = task.get("subject") or ""
    # The is_self_complete_exempt arm suppresses the warn for the agent types in
    # SELF_COMPLETE_EXEMPT_AGENT_TYPES (currently the secretary) + signal tasks.
    # If that exempt set GROWS, re-audit this suppression: a newly-exempt type
    # that DOES carry a HANDOFF would silently lose the nudge here. See the
    # is_self_complete_exempt docstring (shared/intentional_wait.py) for the
    # canonical exempt-surface definition.
    if is_self_complete_exempt(task, team_name) or is_teachback_subject(subject):
        return None  # exempt → no handoff expected, no warn

    # HANDOFF-expecting + completing + handoff absent (neither incoming nor on
    # disk) = the #956 ordering mistake. WARN with an ACTIONABLE message.
    return (
        f"PACT handoff_ordering_gate: Task {task_id} ({subject!r}, owner {owner!r}) "
        "is being completed but has no metadata.handoff yet. The agent_handoff "
        "journal event keys on handoff presence at completion time — completing "
        "now risks losing it. EITHER (a) wait for / write the teammate's "
        "metadata.handoff BEFORE marking completed, OR (b) confirm this task is "
        "genuinely handoff-exempt. A write-time backstop will re-emit if handoff "
        "is set later, but the cleanest path is handoff-then-complete."
    )


def main() -> None:
    # Input-side fail-open: an unreadable / oversized / malformed stdin frame
    # suppresses + exits 0 (never blocks the TaskUpdate).
    try:
        input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not _IMPORTS_OK or not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        advisory = _evaluate(input_data)
    except Exception:
        # WARN gate → fail-OPEN on any logic error. A warn gate that bricks
        # completions is worse than the bug it warns about. NEVER deny.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if advisory:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": advisory,  # advisory — NOT permissionDecision
            }
        }))
        sys.exit(0)  # exit 0 — advisory, never deny / exit-2

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
