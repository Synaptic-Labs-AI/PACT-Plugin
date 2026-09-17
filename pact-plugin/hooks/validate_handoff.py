#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/validate_handoff.py
Summary: SubagentStop hook. Validates the prose HANDOFF of Agent-tool PACT
         subagents, and refuses an in-process teammate's turn end over its
         unacknowledged background work (shared/turn_end_gate.py).
Used by: Claude Code hooks.json, as the only SubagentStop hook, so each
         subagent turn end gets one decision.

Validated population: Agent-tool subagents whose agent_type carries a PACT
prefix must complete with proper handoff information (produced, decisions,
next steps) in their transcript text. In-process Agent Teams teammates are
excluded from that check on purpose: a teammate's HANDOFF is its task
metadata, which the task gates validate, and its turn ends are ordinary
idles, so a prose check would refuse every idle. Teammates get only the
background-work check.

Note: Task protocol compliance (status, metadata) is NOT validated here.
Task state may still be in flux at SubagentStop time (agents self-manage
status under Agent Teams, and the team-lead may process output after this hook
fires), so Task state cannot be reliably checked here.

CANONICAL STRUCTURED HANDOFF (do NOT relocate this hook): this hook
validates the PROSE form of a HANDOFF in the agent transcript and is a
legacy convenience for Agent-tool subagents. The STRUCTURED 6-field handoff lives in
`metadata.handoff`, and its PRESENCE is handled lead-side at acceptance-commit
by `_emit_lead_side_agent_handoff` in task_lifecycle_gate.py — its
emit-eligibility short-circuits on an absent handoff — and therefore fires in
BOTH teammate modes (in-process AND separate-process). (A completion-time
advisory branch that once emitted `handoff_missing` / `handoff_schema_invalid`
there was permanently dormant under the bare-owner convention and has been
retired.) This prose check runs for NO teammate: an in-process teammate is
excluded in main (see _is_teammate_frame), and a separate-process (e.g.
tmux/iTerm2) teammate fires its OWN Stop/SessionEnd, never a SubagentStop in
the lead's process. That absence is intentional and acceptable: the lead-side
presence handling above already covers both modes. Do NOT "restore" this prose
check onto a teammate end-of-life surface believing validation was lost — it
was not.

Input: JSON from stdin with `last_assistant_message` (preferred, SDK v2.1.47+),
       `transcript` (fallback), `agent_type` (the role-class gate field, #812),
       `stop_hook_active` (loop guard, see main()), and `session_id` (telemetry
       journal resolution, see main()); the background-work check also reads
       `background_tasks`, `session_crons`, `agent_id`, `agent_transcript_path`
       and `transcript_path`
Output: JSON `{"decision": "block", "reason": ...}` refusing the stop when the
        handoff is missing/low-quality or a teammate's background job is
        unacknowledged, with every reason in one block; `systemMessage` warning instead when
        `stop_hook_active` is set; `{"suppressOutput": true}` on every
        pass/skip path; an internal error prints `hook_error_json`'s
        `systemMessage` instead (fail-open, exit 0)
"""

from __future__ import annotations

import json
import sys
import re

import shared.pact_context as pact_context
from shared.error_output import hook_error_json
from shared.session_journal import append_event, make_event
from shared.turn_end_jobs import running_jobs

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


# Lossless fields — information that would be lost when agent context ends.
# When a structured HANDOFF section is present, these must appear as subsections.
LOSSLESS_FIELDS = {
    "produced": {
        "patterns": [
            r"(?:^|\n)\s*\d*\.?\s*produced\s*:",
        ],
        "description": "Produced",
    },
    "key_decisions": {
        "patterns": [
            r"(?:^|\n)\s*\d*\.?\s*key\s+decisions?\s*:",
        ],
        "description": "Key decisions",
    },
}

# Signal-type completions (e.g., pact-auditor) use audit_summary, not HANDOFF format.
# Matched only in the OPENING of the closing text: a signal completion DECLARES
# itself up front, while dispatch text and protocol prose QUOTE these tokens deep
# in the body — including text denying them, which used to disable the warning.
SIGNAL_DECLARATION_HEAD_CHARS = 200
SIGNAL_COMPLETION_PATTERNS = [
    r"AUDIT\s+SIGNAL",
    r"audit_summary",
    r"completion_type.+signal",
]


# Required handoff elements with their patterns and descriptions
HANDOFF_ELEMENTS = {
    "what_produced": {
        "patterns": [
            r"(?:produced|created|generated|output|implemented|wrote|built|delivered)",
            r"(?:file|document|component|module|function|class|api|endpoint|schema)",
            r"(?:completed|finished|done with)",
        ],
        "description": "what was produced",
    },
    "key_decisions": {
        "patterns": [
            r"(?:decision|chose|selected|opted|rationale|reason|because)",
            r"(?:trade-?off|alternative|approach|strategy|pattern)",
            r"(?:decided to|went with|picked)",
        ],
        "description": "key decisions",
    },
    "next_steps": {
        "patterns": [
            r"(?:next|needs|requires|depends|should|must|recommend)",
            r"(?:follow-?up|remaining|todo|to-?do|action item)",
            r"(?:test engineer|tester|reviewer|next agent|next phase)",
        ],
        "description": "next steps/needs",
    },
}


def declares_signal_completion(transcript: str) -> bool:
    """
    Check if the closing text OPENS by declaring a signal-type completion.

    Reads PROSE, not structured data — unlike `is_signal_task` in
    shared/agent_handoff_marker.py, which reads `metadata["type"]`. Task
    metadata is not available at SubagentStop, so this is a text heuristic and
    its name says so.

    Only the first SIGNAL_DECLARATION_HEAD_CHARS are searched: a signal
    completion declares itself in its opener, while a mention deeper in the
    body is a quotation of dispatch or protocol text and must not suppress the
    refusal.

    Args:
        transcript: The agent's closing text (`last_assistant_message`)

    Returns:
        True if the opener declares a signal-type completion
    """
    head = transcript[:SIGNAL_DECLARATION_HEAD_CHARS]
    for pattern in SIGNAL_COMPLETION_PATTERNS:
        if re.search(pattern, head, re.IGNORECASE):
            return True
    return False


def check_lossless_fields(transcript: str) -> list:
    """
    Check if a structured HANDOFF section contains the lossless fields.

    Lossless fields are information that would be lost when the agent's
    context window ends: what was produced and what decisions were made.

    Args:
        transcript: The agent's complete output/transcript

    Returns:
        List of missing lossless field descriptions (empty if all present)
    """
    missing_lossless = []
    transcript_lower = transcript.lower()

    for field_key, field_info in LOSSLESS_FIELDS.items():
        found = False
        for pattern in field_info["patterns"]:
            if re.search(pattern, transcript_lower):
                found = True
                break
        if not found:
            missing_lossless.append(field_info["description"])

    return missing_lossless


def validate_handoff(transcript: str) -> tuple:
    """
    Check if transcript contains proper handoff elements.

    Args:
        transcript: The agent's complete output/transcript

    Returns:
        Tuple of (is_valid, missing_elements, lossless_missing)
        - is_valid: True if handoff passes validation
        - missing_elements: list of missing element descriptions
        - lossless_missing: list of missing lossless field names (structured path only)
    """
    missing = []

    # First, check for explicit handoff section (indicates structured handoff)
    has_handoff_section = bool(re.search(
        r"(?:##?\s*)?(?:handoff|hand-off|hand off|summary|output|deliverables)[\s:]*\n",
        transcript,
        re.IGNORECASE
    ))

    # If there's an explicit handoff section, validate lossless fields
    if has_handoff_section:
        # Signal-type completions skip lossless validation
        if declares_signal_completion(transcript):
            return True, [], []

        lossless_missing = check_lossless_fields(transcript)
        return True, [], lossless_missing

    # Otherwise, check for implicit handoff elements
    transcript_lower = transcript.lower()

    for element_key, element_info in HANDOFF_ELEMENTS.items():
        found = False
        for pattern in element_info["patterns"]:
            if re.search(pattern, transcript_lower):
                found = True
                break

        if not found:
            missing.append(element_info["description"])

    # Consider valid if at least 2 out of 3 elements are present
    # (some agents may not have explicit decisions if straightforward)
    is_valid = len(missing) <= 1

    return is_valid, missing, []


def is_pact_agent(agent_identifier: str) -> bool:
    """
    Check if the agent is a PACT framework agent (role-class gate).

    Args:
        agent_identifier: The agent's role identifier — under #812 this is the
            harness-set ``agent_type`` (e.g. ``"pact-preparer"``). The ``pact-``
            prefix family below matches ``agent_type`` values directly, so the
            same prefix-check answers the role-class question against the field
            that is actually present at SubagentStop. The namespaced
            spelling (``"PACT:pact-preparer"``) is checked without its ``PACT:``.

    Returns:
        True if this is a PACT agent that should be validated
    """
    if not agent_identifier:
        return False
    if isinstance(agent_identifier, str):
        agent_identifier = pact_context.strip_pact_namespace(agent_identifier)

    pact_prefixes = ["pact-", "PACT-", "pact_", "PACT_"]
    return any(agent_identifier.startswith(prefix) for prefix in pact_prefixes)


def _background_verdict(input_data: dict):
    """The turn-end background-work verdict, or None.

    The gate is imported only when a job of a counted type is running, so an
    ordinary SubagentStop pays nothing for it. Any error yields None, and the
    handoff decision stands on its own.
    """
    if not running_jobs(input_data):
        return None
    try:
        from shared import turn_end_gate

        return turn_end_gate.evaluate(input_data)
    except Exception:
        return None


def _is_teammate_frame(input_data: dict) -> bool:
    """True iff this SubagentStop belongs to an in-process Agent Teams teammate.

    A teammate skips the prose HANDOFF check even when its agent_type carries
    a PACT prefix: its HANDOFF is its task metadata, which the task gates
    validate, and its turn ends are ordinary idles, so a prose check here
    would refuse every idle. Any error yields False, which keeps the check.
    """
    try:
        from shared import turn_end_gate

        pact_context.init(input_data)
        team = pact_context.get_team_name()
        return bool(turn_end_gate.teammate_identity(input_data, team))
    except Exception:
        return False


def _handoff_refusals(agent_type: str, transcript: str) -> tuple:
    """(refusal texts, refusal classes) for a PACT agent's closing message."""
    refusals = []
    refusal_classes = []

    # Skip transcript validation if very short (likely an error case)
    if len(transcript) >= 100:
        is_valid, missing, lossless_missing = validate_handoff(transcript)

        if not is_valid and missing:
            refusal_classes.append("missing_handoff")
            refusals.append(
                f"PACT Handoff Refusal: Agent '{agent_type}' completed without "
                f"proper handoff. Missing: {', '.join(missing)}. "
                "Include in your closing response: what was produced, key "
                "decisions, and next steps."
            )

        if lossless_missing:
            refusal_classes.append("lossless_fields")
            refusals.append(
                f"PACT Lossless Field Refusal: Agent '{agent_type}' HANDOFF "
                f"section is missing: {', '.join(lossless_missing)}. "
                "Add these subsections to the HANDOFF — they preserve "
                "information that would otherwise be lost."
            )

    return refusals, refusal_classes


def main():
    """
    Main entry point for the SubagentStop hook.

    Reads the subagent's stop frame from stdin and makes ONE decision from two
    checks: the prose HANDOFF check for PACT Agent-tool subagents, and the
    background-work check (shared/turn_end_gate.py), which refuses an
    in-process teammate's turn end over its unacknowledged recorded jobs.
    Their reasons are joined into a single `decision: block`, fed back to the
    agent. When `stop_hook_active` is set — the agent is already continuing
    from a stop-hook block — the refusal degrades to a `systemMessage`
    warning so an agent that cannot satisfy the check is not looped forever.
    A degraded HANDOFF refusal also appends a `handoff_refusal_degraded`
    event (fail-open telemetry — a journal failure never blocks the stop).
    Every background verdict is journaled as `background_stop_gate`, and a
    job is marked reported only when a block naming it is printed.
    """
    try:
        # Read input from stdin
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            # No input or invalid JSON - can't validate
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Prefer last_assistant_message (SDK v2.1.47+), fall back to transcript
        transcript = input_data.get("last_assistant_message", "") or input_data.get("transcript", "")
        # #812 role-class gate: key on the harness-set ``agent_type``, NOT
        # ``agent_id``. The prose check below runs only for PACT agents, and
        # never for an in-process teammate (see _is_teammate_frame).
        agent_type = input_data.get("agent_type", "")

        # Evaluated before the PACT-agent gate: an in-process teammate's frame
        # can carry its member name rather than a PACT type, and it still gets
        # the background-work check.
        background = _background_verdict(input_data)

        refusals = []
        refusal_classes = []
        # The teammate test imports the gate, so it runs only where its answer
        # changes the output: for a frame the prose check would otherwise see.
        if is_pact_agent(agent_type) and not _is_teammate_frame(input_data):
            refusals, refusal_classes = _handoff_refusals(agent_type, transcript)

        reasons = list(refusals)
        if background is not None and background.reason:
            reasons.append(background.reason)

        if reasons:
            detail = " | ".join(reasons)
            if input_data.get("stop_hook_active"):
                # Loop guard: the agent is already continuing from a stop-hook
                # block. Refusing again can loop an agent that cannot satisfy
                # the check forever, so degrade to a warning and let the stop
                # land. The stop LANDS on this path — systemMessage is shown
                # to the user, not fed back to the agent — so the framing
                # names the degrade rather than reusing the agent-directed
                # refusal label.
                print(json.dumps({"systemMessage": (
                    "PACT Handoff (refusal degraded by stop_hook_active loop "
                    f"guard — stop allowed): {detail}"
                )}))
                # Telemetry: with refusal as the default, degrade events are
                # the escape hatch and must be observable. Fail-open by
                # construction: pact_context.init no-ops when session_id is
                # absent, append_event returns False on any error, and the
                # try/except covers anything past those guards — telemetry
                # never breaks the exit-0 contract.
                if refusals:
                    try:
                        pact_context.init(input_data)
                        append_event(make_event(
                            "handoff_refusal_degraded",
                            agent_type=agent_type,
                            detail=" | ".join(refusals),
                            classes=refusal_classes,
                        ))
                    except Exception:
                        pass
            else:
                # Platform-recognized SubagentStop refusal shape: top-level
                # decision/reason on stdout with exit 0; reason is fed back to
                # the subagent so it completes the HANDOFF before stopping.
                print(json.dumps({"decision": "block", "reason": detail}))
        else:
            print(_SUPPRESS_OUTPUT)

        if background is not None:
            from shared import turn_end_gate

            # mark_told acts only on a verdict that blocks, and such a verdict
            # always reaches the block print above: under stop_hook_active the
            # gate has already turned it into allow_loop_guard.
            turn_end_gate.mark_told(background)
            turn_end_gate.write_trace(background)

        sys.exit(0)

    except Exception as e:
        # Don't block on errors - just warn
        print(f"Hook warning (validate_handoff): {e}", file=sys.stderr)
        print(hook_error_json("validate_handoff", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
