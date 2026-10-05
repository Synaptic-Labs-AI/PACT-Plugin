#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_caps_gate.py
Summary: PreToolUse hook that refuses an Edit or Write of the project CLAUDE.md
         only when it adds pins past the cap, grows a pin past the size cap
         without an override, or carries an invalid size override.
Used by: hooks.json PreToolUse with matcher "Edit|Write" (registered after
         pin_staleness_gate.py so stale-block deny takes precedence).

The verdict comes from `shared.pin_growth.pin_cap_decision`, the one decision
the gate and the Bash report share: it compares the file before the change
with the file after it. This hook builds the text after (`gate_decision`),
checks the size override of every pin the change adds or edits, and prints
the verdict.

Gate fires when ALL hold:
  1. Tool is Edit or Write (enforced by hooks.json matcher)
  2. `claude_md_manager.gate_frame` names the frame: the lead, a PACT
     specialist type, or any frame whose session belongs to a PACT team
     (in-process teammates and Agent-tool subagents share the lead's session).
     A plain session and a non-PACT --agent session are not gated. A team
     member's count denial tells it not to change CLAUDE.md by any route and
     to tell the team-lead, instead of naming the pin command.
  3. `claude_md_manager.gate_target` returns a target: the project CLAUDE.md
     the resolver returns once the change exists, so a Write that creates it
     is gated too. The text before is the file the resolver returns now, or
     empty when none resolves.

FAIL-OPEN. An over-block is the worst outcome, so every failure allows:
  - a module-load failure prints a systemMessage saying the gate is not
    checking pin caps, and exits 0;
  - any exception while deciding is recorded with failure_log.append_failure
    and allows;
  - the decision itself allows with an advisory when the Pinned section cannot
    be located, when the check runs past its step budget or timer, or when it
    fails.
  The one refusing failure path: a Write over an existing CLAUDE.md that cannot
  be read is compared with an empty file, so a Write whose own pins are over the
  cap is refused. An Edit over an unreadable file is allowed with an advisory,
  because it cannot be simulated.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: a deny (hookSpecificOutput.permissionDecision, exit 2), an allow with
        an advisory (hookSpecificOutput.additionalContext, no permissionDecision),
        or {"suppressOutput": true}.
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_allow BEFORE wrapped imports) ─
import json
import re
import sys
from pathlib import Path
from typing import NoReturn, Optional

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_allow(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-open for a module-load failure: a broken install must
    not refuse every Edit and Write of every file, so it allows them and says
    on each one that pin caps are not being checked."""
    message = (
        f"PACT pin_caps_gate could not load and is not checking pin caps "
        f"({stage}): {type(error).__name__}: {error}"
    )
    print(json.dumps({"systemMessage": message}))
    print(message, file=sys.stderr)
    sys.exit(0)


# ─── fail-open wrapper on cross-package imports ────────────────────────────
try:
    import shared.pact_context as pact_context
    from shared.claude_md_manager import MEMBER_PIN_INSTRUCTION
    from shared.failure_log import append_failure
    import pin_caps
    from pin_caps import (
        OVERRIDE_COMMENT_RE,
        OVERRIDE_RATIONALE_MAX,
    )
except BaseException as _module_load_error:  # noqa: BLE001 — fail-open catch-all
    _emit_load_failure_allow("module imports", _module_load_error)

_GATED_TOOLS = frozenset({"Edit", "Write"})

# Line-terminator chars refused in an override rationale. DERIVED from
# pin_caps._FORBIDDEN_TERMINATOR_TABLE (the parser-side strip table) at
# module load — single source of truth, cannot drift. Plan invariant #5:
# parser / CLI / hook char sets MUST match; hand-maintained triple-twin
# copies defeat the existing drift-guard test (test_staleness.py:1182)
# which compares parser vs CLI only. A str.maketrans table maps
# ordinal → None (delete-translate shape); chr() on each key recovers
# the single-char string, and join() produces a membership-check string
# compatible with `any(c in rationale for c in _FORBIDDEN_RATIONALE_CHARS)`.
_FORBIDDEN_RATIONALE_CHARS = "".join(
    chr(ordinal) for ordinal in pin_caps._FORBIDDEN_TERMINATOR_TABLE.keys()
)

# One row holding a full override comment, with the whitespace around it that
# today's stripped match tolerated.
_OVERRIDE_ROW = re.compile(
    r"\s*(?:" + OVERRIDE_COMMENT_RE.pattern[2:-2] + r")\s*\Z", OVERRIDE_COMMENT_RE.flags
)
_OVERRIDE_FIELD_LENGTH = len("pin-size-override:")
_COMMENT_CLOSE_LENGTH = len("-->")

_FAIL_BASELINE_READ = "pin_caps_gate_baseline_read"
_FAIL_DECISION = "pin_caps_gate_decision"
_FAIL_UNEXPECTED = "pin_caps_gate_unexpected"


def _validate_override_rationale(rationale: Optional[str]) -> Optional[str]:
    """Return a deny-reason string if the rationale is invalid, else None.

    A present-but-invalid rationale denies. A None rationale (no override
    line at all) returns None — the SIZE predicate will still catch a
    too-large pin body downstream.
    """
    if rationale is None:
        return None
    if not rationale:
        return (
            "Override rationale is empty — provide a non-empty reason "
            "or compress the pin body."
        )
    if len(rationale) > OVERRIDE_RATIONALE_MAX:
        return (
            f"Override rationale is {len(rationale)} chars "
            f"(max: {OVERRIDE_RATIONALE_MAX}). Shorten it or compress "
            "the pin body."
        )
    # Unreached at runtime: the parser ends a row at \r and \n, and the
    # override row pattern cannot match across the other terminators
    # (`test_splitlines_eats_forbidden_chars_before_validation`). Kept so a
    # future extraction that spans rows fails closed on a terminator, and as
    # the consumer of the derived `_FORBIDDEN_RATIONALE_CHARS`.
    if any(c in rationale for c in _FORBIDDEN_RATIONALE_CHARS):
        return (
            "Override rationale contains a line terminator "
            "(newline, carriage return, or Unicode line separator). "
            "Remove the terminator and retry."
        )
    return None


def _invalid_override(before: str, after: str) -> Optional[str]:
    """The deny reason for an invalid size override on a pin the change adds
    or edits, else None.

    Read from the fence-aware parse of the text after: only PROSE rows of pins
    whose text (comment row through body) is not in the text before verbatim.
    So a fenced example of the override syntax is not an override, and an
    unchanged or verbatim-moved pin is not re-checked.
    """
    from shared.claude_md_markers import State, parse
    from shared.pin_growth import locate_pinned, pin_spans

    doc = parse(after)
    located = locate_pinned(doc)
    if located.state is not State.FOUND:
        return None
    heading, last = located.spans[0]
    for first, end in pin_spans(doc, (heading + 1, last)):
        start, stop = doc.offsets(first, end)
        if doc.text[start:stop] in before:
            continue
        for row in doc.find_lines(_OVERRIDE_ROW, (first, end)):
            reason = _validate_override_rationale(_rationale(doc.lines[row].content))
            if reason is not None:
                return reason
    return None


def _rationale(row_content: str) -> str:
    """The rationale of a row `_OVERRIDE_ROW` matched: the text between the
    field name, which follows the comment's first comma (the date field holds
    none), and the closing `-->`, stripped."""
    comment = row_content.strip()
    field = comment[comment.index(",") + 1:].lstrip()
    return field[_OVERRIDE_FIELD_LENGTH:-_COMMENT_CLOSE_LENGTH].strip()


def _simulate(before: str, tool_name: str, tool_input: dict) -> str:
    """The file after the tool runs: a Write's content, or an Edit applied
    with str.replace (every site with replace_all, else the first). An empty
    old_string changes nothing. Raises TypeError on a malformed tool_input."""
    if tool_name == "Write":
        content = tool_input.get("content")
        if not isinstance(content, str):
            raise TypeError(f"Write tool_input.content must be str, got {type(content).__name__}")
        return content
    old_string = tool_input.get("old_string")
    new_string = tool_input.get("new_string")
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        raise TypeError("Edit tool_input.old_string and .new_string must both be str")
    if old_string == "":
        return before
    if tool_input.get("replace_all", False):
        return before.replace(old_string, new_string)
    return before.replace(old_string, new_string, 1)


def gate_decision(before: str, tool_name: str, tool_input: dict):
    """The verdict on an Edit or Write of the project CLAUDE.md whose text is
    `before` ("" when no readable file). Pure: no I/O.

    A change that leaves the text as it is allows: the hook sees old_string
    before the tool's own quote normalisation, so its literal replace can miss.
    An invalid size override on a pin the change adds or edits denies, cause
    "override". Otherwise `pin_cap_decision` decides.
    """
    from shared.pin_growth import PinDecision, pin_cap_decision

    after = _simulate(before, tool_name, tool_input)
    if after == before:
        return PinDecision("ALLOW", 0, 0, None, None, None)
    invalid = _invalid_override(before, after)
    if invalid is not None:
        return PinDecision("DENY", 0, 0, None, "override", f"Pin cap violation (invalid override): {invalid}")
    return pin_cap_decision(before, after)


def _read_baseline(claude_md_path: Path) -> tuple[Optional[str], Optional[str]]:
    """Read the CLAUDE.md before the change, without the writers' lock, with
    undecodable bytes replaced.

    The gate only reads, so it takes no lock: a writer holding it would delay
    the gate up to the lock timeout and then turn the read into a failure,
    which refuses a Write.

    Returns (content, error_classification). On success: (text, None).
    On I/O failure: (None, _FAIL_BASELINE_READ).
    """
    try:
        return claude_md_path.read_text(encoding="utf-8", errors="replace"), None
    except OSError:
        return None, _FAIL_BASELINE_READ


def _unreadable_decision(claude_md_path: Path, tool_name: str, tool_input: dict):
    """The decision when the project CLAUDE.md exists but cannot be read.

    The one refusing failure path: a Write is compared with an empty file, so
    a Write whose own pins are over the cap is refused. An Edit cannot be
    simulated without the text, so it is allowed with an advisory."""
    if tool_name == "Write":
        return gate_decision("", tool_name, tool_input)
    from shared.pin_growth import PinDecision

    return PinDecision(
        "ALLOW_ADVISORY", 0, 0, None, "unreadable",
        f"PACT could not read {claude_md_path}, so the pin cap was not checked for this Edit.",
    )


def _member_reason(decision):
    """A team member's denial: a count denial keeps its violation line and asks
    the team-lead instead of naming the pin command; others are unchanged."""
    if decision.verdict != "DENY" or decision.cause != "count" or not decision.reason:
        return decision
    violation = decision.reason.split(". ", 1)[0].rstrip(".")
    return decision._replace(reason=f"{violation}. {MEMBER_PIN_INSTRUCTION}")


def _gate(input_data: dict):
    """The decision for a frame the gate checks, or None for one it does not."""
    tool_name = input_data.get("tool_name", "")
    if tool_name not in _GATED_TOOLS:
        return None

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None

    # The basename test comes first, so no frame or resolver work runs for any
    # other file.
    file_path = tool_input.get("file_path", "")
    if not isinstance(file_path, str) or Path(file_path).name.casefold() != "claude.md":
        return None

    pact_context.init(input_data)
    from shared.claude_md_manager import gate_frame, gate_target

    frame = gate_frame(input_data)
    if frame is None:
        return None

    target = gate_target(file_path)
    if target is None:
        return None
    decision = _decide(target, tool_name, tool_input)
    return _member_reason(decision) if frame == "member" else decision


def _decide(target, tool_name: str, tool_input: dict):
    """The decision on a change to `target`, compared with the file that
    resolves before it, or with empty text when none does."""
    before = ""
    if target.before is not None:
        before, read_error = _read_baseline(target.before)
        if before is None:
            append_failure(
                classification=read_error or _FAIL_BASELINE_READ,
                error=f"read failed for {target.before}",
                source=tool_name,
            )
            return _unreadable_decision(target.before, tool_name, tool_input)

    decision = gate_decision(before, tool_name, tool_input)
    if decision.cause == "error":
        append_failure(classification=_FAIL_DECISION, error=decision.reason or "", source=tool_name)
    return decision


def _check_tool_allowed(input_data: dict) -> Optional[str]:
    """The deny reason when the gate refuses the call, else None."""
    decision = _gate(input_data)
    if decision is not None and decision.verdict == "DENY":
        return decision.reason
    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        decision = _gate(input_data)
    except Exception as exc:  # noqa: BLE001 — SACROSANCT fail-open
        try:
            append_failure(
                classification=_FAIL_UNEXPECTED,
                error=f"{type(exc).__name__}: {exc}",
                source=str(input_data.get("tool_name", "")),
            )
        except Exception:  # noqa: BLE001 — logging must never cascade
            pass
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if decision is not None and decision.verdict == "DENY":
        # hookEventName is required by the harness; missing it silently fails open
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": decision.reason,
            }
        }))
        sys.exit(2)

    if decision is not None and decision.verdict == "ALLOW_ADVISORY" and decision.reason:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": decision.reason,
            }
        }))
        sys.exit(0)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
