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
    fails;
  - an Edit or a Write over a CLAUDE.md that exists but cannot be read is
    allowed with an advisory: without the text before, the change cannot be
    compared with it.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: a deny (hookSpecificOutput.permissionDecision, exit 2), an allow with
        an advisory (hookSpecificOutput.additionalContext, no permissionDecision),
        or {"suppressOutput": true}.
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_allow BEFORE wrapped imports) ─
import json
import sys
from collections import Counter
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
    from pin_caps import OVERRIDE_RATIONALE_MAX
except BaseException as _module_load_error:  # noqa: BLE001 — fail-open catch-all
    _emit_load_failure_allow("module imports", _module_load_error)

_GATED_TOOLS = frozenset({"Edit", "Write"})

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
        return "Override rationale is empty — provide a non-empty reason."
    if len(rationale) > OVERRIDE_RATIONALE_MAX:
        return (
            f"Override rationale is {len(rationale)} chars "
            f"(max: {OVERRIDE_RATIONALE_MAX}). Shorten it."
        )
    return None


def _collapsed(content: str) -> str:
    """A row's content with each whitespace run made one space and the ends
    stripped, so a re-indent, a trailing space or a line-ending rewrite leaves
    it the same."""
    return " ".join(content.split())


def _invalid_override(before: str, after: str) -> Optional[str]:
    """The deny reason for an invalid size override on a pin the change adds
    or edits, else None.

    Read from the fence-aware parse of the text after, on the one row the
    parser attributes to each pin as its comment (the first row `pin_spans`
    gives it), through `pin_caps.override_rationale_text`, so the gate and the
    parser read the same override. An override-shaped row elsewhere in a body,
    or in a fenced example, is body text for both, and a row holding a line
    break `str.splitlines` breaks at is never attributed.

    An override row is checked only when the change added or edited it: its
    text, whitespace collapsed, is looked up among every row of the text
    before, of any kind (prose, code or past an unclosed fence), and a row
    found there is used up and not checked. So an untouched old invalid
    override is not refused for an edit elsewhere in its pin, a rename, a
    move, a line-ending rewrite or a fence-closing Write.
    """
    from pin_caps import override_rationale_text
    from shared.claude_md_markers import State, parse
    from shared.pin_growth import locate_pinned, pin_spans

    doc = parse(after)
    located = locate_pinned(doc)
    if located.state is not State.FOUND:
        return None
    heading, last = located.spans[0]
    rows_before = Counter(_collapsed(line.content) for line in parse(before).lines)
    for first, _end in pin_spans(doc, (heading + 1, last)):
        rationale = override_rationale_text(doc, first)
        if rationale is None:
            continue
        row = _collapsed(doc.lines[first].content)
        if rows_before[row]:
            rows_before[row] -= 1
            continue
        reason = _validate_override_rationale(rationale)
        if reason is not None:
            return reason
    return None


def gate_decision(before: str, tool_name: str, tool_input: dict):
    """The verdict on an Edit or Write of the project CLAUDE.md whose text is
    `before` ("" when no readable file). Pure: no I/O.

    The text after is `shared.edit_simulation.simulate`'s, the edit the tool
    will make: an empty old_string creates or fills a blank file, and a curly
    quote matches its straight form. A malformed payload raises TypeError,
    which the caller's fail-open catch allows. A change that leaves the text
    as it is allows. An invalid size override on a pin the change adds or
    edits denies, cause "override". Otherwise `pin_cap_decision` decides.
    """
    from shared.edit_simulation import simulate
    from shared.pin_growth import PinDecision, pin_cap_decision

    after = simulate(before, tool_name, tool_input)
    if after is None:
        raise TypeError(f"{tool_name} tool_input is not a well-formed {tool_name} payload")
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
    """The decision when the project CLAUDE.md exists but cannot be read: an
    Edit or a Write is allowed with an advisory, because the change cannot be
    compared with a text the hook cannot read."""
    from shared.pin_growth import PinDecision

    return PinDecision(
        "ALLOW_ADVISORY", 0, 0, None, "unreadable",
        f"PACT could not read {claude_md_path}, so the pin cap was not checked for this {tool_name}.",
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
