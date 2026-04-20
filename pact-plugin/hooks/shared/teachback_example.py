"""
Location: pact-plugin/hooks/shared/teachback_example.py
Summary: Deny-reason templates for the teachback gate (#401). Mirrors
         shared/handoff_example.py — str.format() templates with placeholder
         substitution. Imperative-first framing per Q5 resolution (NOT
         "advisory/optional/reminder" passive voice, which misreads as
         non-blocking per PR #329).
Used by: hooks/teachback_gate.py (PreToolUse deny reason builder),
         hooks/task_schema_validator.py (TaskCreated reject reason),
         hooks/teachback_idle_guard.py (TeammateIdle algedonic message).

Templates cover the five deny-reason codes locked in
docs/architecture/teachback-gate/CONTENT-SCHEMAS.md §Deny Reason Shapes:

  - missing_submit       — T1 first-hit: teammate has not written
                           teachback_submit yet
  - invalid_submit       — T3 schema failure: submit present but one or
                           more fields fail validation
  - awaiting_approval    — teachback_under_review: valid submit, waiting
                           on lead to write teachback_approved
  - unaddressed_items    — T5 auto-downgrade: approved written but
                           conditions_met.unaddressed is non-empty
  - corrections_pending  — T6: lead wrote teachback_corrections;
                           teammate must re-emit submit

Framing contract:
  - Every template starts with an imperative verb (Send, Fix, Update,
    Correct, Address, Resubmit). NOT "Reminder", "Note", "Advisory",
    "Consider", "You may want to" — those trigger the non-blocking
    misread.
  - Every template except `awaiting_approval` mentions the Phase 2
    consequence ("Phase 2 will block" or equivalent). `awaiting_approval`
    is post-submit and doesn't need the phase warning.
  - simplified-protocol variant of `missing_submit` shows only the two
    fields the simplified schema requires (`understanding` + `first_action`).
"""

from __future__ import annotations

# Imperative first words approved for deny-reason templates. Drift test
# (test_teachback_example.py) asserts every template's first word is in
# this set.
_IMPERATIVE_FIRST_WORDS = frozenset({
    "Send",
    "Fix",
    "Update",
    "Correct",
    "Address",
    "Resubmit",
})


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
# Braces that need to survive str.format() intact are doubled ({{ → literal {).
# Placeholders are single-braced: {task_id}, {tool_name}, etc.

_MISSING_SUBMIT_FULL_TEMPLATE = (
    'Send a teachback before {tool_name}. Your task at variety {variety_total} '
    '(threshold {threshold}) requires the full teachback gate before code-editing '
    'tools run. Phase 2 will block this tool call.\n'
    '\n'
    'Write this TaskUpdate NOW, adapted to your task:\n'
    '\n'
    'TaskUpdate(taskId="{task_id}", metadata={{"teachback_submit": {{\n'
    '  "understanding": "<what you understand you are building, at least 100 chars>",\n'
    '  "most_likely_wrong": {{\n'
    '    "assumption": "<your stated assumption that could be wrong, at least 40 chars; '
    'must share a term with one of your required_scope_items>",\n'
    '    "consequence": "<what goes wrong if you are wrong, at least 40 chars>"\n'
    '  }},\n'
    '  "least_confident_item": {{\n'
    '    "item": "<named scope item, at least 30 chars>",\n'
    '    "current_plan": "<concrete next step, at least 30 chars>",\n'
    '    "failure_mode": "<what could fail, at least 30 chars>"\n'
    '  }},\n'
    '  "first_action": {{\n'
    '    "action": "<file.py:123 or function_name()>",\n'
    '    "expected_signal": "<observable result, at least 30 chars>"\n'
    '  }}\n'
    '}}}})\n'
    '\n'
    'See pact-teachback skill for the full schema.'
)

_MISSING_SUBMIT_SIMPLIFIED_TEMPLATE = (
    'Send a teachback before {tool_name}. Your task at variety {variety_total} '
    '(threshold {threshold}) requires the simplified teachback gate before '
    'code-editing tools run. Phase 2 will block this tool call.\n'
    '\n'
    'Write this TaskUpdate NOW, adapted to your task:\n'
    '\n'
    'TaskUpdate(taskId="{task_id}", metadata={{"teachback_submit": {{\n'
    '  "understanding": "<what you understand you are building, at least 100 chars>",\n'
    '  "first_action": {{\n'
    '    "action": "<file.py:123 or function_name()>",\n'
    '    "expected_signal": "<observable result, at least 30 chars>"\n'
    '  }}\n'
    '}}}})\n'
    '\n'
    'See pact-teachback skill for the full schema.'
)

_INVALID_SUBMIT_TEMPLATE = (
    'Fix the teachback schema failure and resubmit. Phase 2 will block this {tool_name} '
    'call until the submit validates.\n'
    '\n'
    'Field errors:\n'
    '  - {fail_field}: {fail_error}\n'
    '\n'
    'Your current teachback_submit.{fail_field}:\n'
    '  "{actual_value}"\n'
    '\n'
    'Resubmit via TaskUpdate(taskId="{task_id}", metadata={{"teachback_submit": {{...}}}}).'
)

_AWAITING_APPROVAL_TEMPLATE = (
    'Update from the lead required. No further {tool_name} calls until the lead writes '
    'metadata.teachback_approved (to unblock) or metadata.teachback_corrections '
    '(to request revisions).\n'
    '\n'
    'Your teachback_submit is schema-valid. If the lead appears unresponsive, '
    'the teachback_idle_guard hook will emit an algedonic ALERT after 3 idle events.'
)

_UNADDRESSED_ITEMS_TEMPLATE = (
    'Address the unaddressed scope items before {tool_name}. The lead wrote a '
    'teachback_approved but conditions_met.unaddressed is non-empty — treating '
    'as a correction request. Phase 2 will block this tool call.\n'
    '\n'
    'Unaddressed: {unaddressed}\n'
    '\n'
    'Resubmit via TaskUpdate(taskId="{task_id}", metadata={{"teachback_submit": '
    '{{...re-emit flagged fields...}}}}).'
)

_CORRECTIONS_PENDING_TEMPLATE = (
    'Resubmit your teachback before {tool_name}. The lead wrote teachback_corrections. '
    'Phase 2 will block this tool call until you re-emit the flagged fields.\n'
    '\n'
    'Issues raised:\n'
    '  - {corrections_issues}\n'
    '\n'
    'Fields to revise: {corrections_targets}\n'
    '\n'
    'Update via TaskUpdate(taskId="{task_id}", metadata={{"teachback_submit": {{...}}}}) '
    '(re-emit only the flagged fields; other fields retain prior validity).'
)


_DENY_TEMPLATES: dict[str, str] = {
    "missing_submit": _MISSING_SUBMIT_FULL_TEMPLATE,
    "missing_submit_simplified": _MISSING_SUBMIT_SIMPLIFIED_TEMPLATE,
    "invalid_submit": _INVALID_SUBMIT_TEMPLATE,
    "awaiting_approval": _AWAITING_APPROVAL_TEMPLATE,
    "unaddressed_items": _UNADDRESSED_ITEMS_TEMPLATE,
    "corrections_pending": _CORRECTIONS_PENDING_TEMPLATE,
}


# Default context values keep format() from raising on missing keys. Hooks
# that call format_deny_reason typically populate only the fields relevant
# to the reason_code; all other placeholders resolve to the empty string.
_DEFAULT_CONTEXT: dict[str, object] = {
    "task_id": "",
    "tool_name": "",
    "variety_total": 0,
    "threshold": 7,
    "required_scope_items": [],
    "fail_field": "",
    "fail_error": "",
    "actual_value": "",
    "unaddressed": "",
    "corrections_issues": "",
    "corrections_targets": "",
}


def format_deny_reason(
    reason_code: str,
    context: dict,
    protocol_level: str = "full",
) -> str:
    """Build the deny-reason string for a teachback_gate block/advisory.

    Args:
        reason_code: One of the five keys in _DENY_TEMPLATES (not
            including "missing_submit_simplified" — simplified selection
            is driven by `protocol_level`).
        context: Placeholder values. Missing keys fall back to
            _DEFAULT_CONTEXT. Lists (`unaddressed`, `corrections_issues`,
            `corrections_targets`) may be passed as lists OR
            comma-separated strings; lists are joined with ", " before
            formatting.
        protocol_level: "full" | "simplified". When reason_code is
            "missing_submit" and protocol_level is "simplified", the
            simplified template is selected.

    Returns:
        Multi-line string suitable for
        hookSpecificOutput.permissionDecisionReason or stderr. Returns
        an empty string when reason_code is not in _DENY_TEMPLATES
        (fail-open — callers should never see an empty string in the
        happy path, but this avoids raising).
    """
    key = reason_code
    if reason_code == "missing_submit" and protocol_level == "simplified":
        key = "missing_submit_simplified"

    template = _DENY_TEMPLATES.get(key)
    if template is None:
        return ""

    merged: dict[str, object] = dict(_DEFAULT_CONTEXT)
    merged.update(context or {})

    # Normalize list-shaped fields to comma-separated strings for direct
    # interpolation. The template authors may pass a list from upstream
    # code (e.g., unaddressed from conditions_met.unaddressed) without
    # needing to join at the call site.
    for key_ in ("unaddressed", "corrections_issues", "corrections_targets"):
        value = merged.get(key_)
        if isinstance(value, list):
            merged[key_] = ", ".join(str(v) for v in value)

    try:
        return template.format(**merged)
    except (KeyError, ValueError, IndexError):
        # Fail-open on any template formatting error — return a minimal
        # deny reason rather than raising into the caller.
        return (
            f"Send a teachback before {merged.get('tool_name', 'this tool')}. "
            f"Teachback gate reason: {reason_code}."
        )
