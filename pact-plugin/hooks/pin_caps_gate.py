#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_caps_gate.py
Summary: PreToolUse hook that enforces pin count / size / embedded-pin / override
         caps on Edit and Write tool calls targeting the project CLAUDE.md.
Used by: hooks.json PreToolUse with matcher "Edit|Write" (registered after
         pin_staleness_gate.py so stale-block deny takes precedence).

Cycle-8 re-architecture (#492): this hook is the primary cap enforcement path.
The CLI (scripts/check_pin_caps.py) is demoted to advisory status (--status,
--list-evictable) in a later commit. Before cycle-8, cap enforcement lived in
the CLI, invoked via a bash heredoc from /PACT:pin-memory — that surface had
shell-scaffolding hardening churn for 7 cycles. Moving enforcement to a hook
eliminates ~60% of that surface by construction.

Gate fires when ALL hold:
  1. Tool is Edit or Write (enforced by hooks.json matcher)
  2. Target file path resolves to the project CLAUDE.md
  3. Not a teammate session (parity with pin_staleness_gate — teammates do
     not edit the project CLAUDE.md per worktree scope rule)
  4. Simulated post-edit state is strictly worse than pre-edit state
     (net-worse predicate — prevents pre-malformed livelock)
  5. OR the candidate body embeds a `### ` heading (count-cap bypass)
  6. OR the override rationale on a new pin is malformed / oversize /
     contains forbidden line terminators

SACROSANCT fail-open contract (exception):
  Normal rule: every raisable path → exit 0 with suppressOutput. A gate bug
  must never block a tool call. failure_log.append_failure writes an entry
  so fail-open bypasses are observable post-hoc (Sec N1).

  ASYMMETRIC EXCEPTION — Write-baseline fail-CLOSED (Sec N7):
    If the tool is Write AND the baseline read of current CLAUDE.md fails
    (file missing, unreadable, or unparseable markers), treat it as a
    fresh-start baseline (empty pin list). Do NOT fail-open to "allow the
    Write blindly" — a corrupted or symlinked-away CLAUDE.md unlocking the
    cap is strictly worse than a legitimate Write being blocked. The user
    sees a deny-reason pointing at the corruption and can remediate.

Cap predicates (all via hooks/pin_caps.py pure helpers, symmetric oracle):
  - count:         post-state len(pins) > PIN_COUNT_CAP
  - size:          any pin.body_chars > PIN_SIZE_CAP without override
  - embedded_pin:  candidate new_body parses as a Pin structure
  - invalid_override: override rationale fails regex / length /
                      line-terminator validation

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {"suppressOutput": true} (allow / passthrough)
"""

import json
import sys
from pathlib import Path
from typing import Optional

import shared.pact_context as pact_context
from shared import (
    file_lock,
    match_project_claude_md,
)
from shared.failure_log import append_failure
import pin_caps
from pin_caps import (
    OVERRIDE_COMMENT_RE,
    OVERRIDE_RATIONALE_MAX,
    apply_edit_and_parse,
    compute_deny_reason,
    evaluate_full_state,
    parse_pins,
)

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

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

_FAIL_BASELINE_READ = "pin_caps_gate_baseline_read"
_FAIL_BASELINE_PARSE = "pin_caps_gate_baseline_parse"
_FAIL_SIMULATE = "pin_caps_gate_simulate"
_FAIL_UNEXPECTED = "pin_caps_gate_unexpected"

_WRITE_BASELINE_DENY_REASON = (
    "Refusing Write: could not read or parse the current CLAUDE.md to "
    "compare caps (treated as fresh-start baseline). Fix the file manually "
    "or restore from git, then retry. This is the one asymmetric "
    "fail-CLOSED path — a corrupt CLAUDE.md must not silently unlock the cap."
)


def _extract_override_rationale(new_fragment: str) -> Optional[str]:
    """Find a pin-size-override rationale in a candidate new-pin fragment.

    Scans each line of `new_fragment` for a full override comment shape
    (`<!-- pinned: DATE, pin-size-override: RATIONALE -->`). Returns the
    captured rationale text, or None if no override comment is present on
    any line. Matches OVERRIDE_COMMENT_RE.fullmatch semantics used by
    parse_pins, so the gate's detection and the parser's detection stay
    symmetric.

    Returns the rationale STRING (post-strip of whitespace) on match, so
    the caller can validate length and forbidden chars. Returns None if
    no override line is present — caller then treats this as "no override
    claimed" rather than "invalid override."
    """
    if not isinstance(new_fragment, str):
        return None
    for line in new_fragment.splitlines():
        candidate = line.strip()
        m = OVERRIDE_COMMENT_RE.fullmatch(candidate)
        if m:
            return m.group(1).strip()
    return None


def _validate_override_rationale(rationale: Optional[str]) -> Optional[str]:
    """Return a deny-reason string if the rationale is invalid, else None.

    A present-but-invalid rationale denies. A None rationale (no override
    line in the fragment at all) returns None — the SIZE predicate will
    still catch a too-large pin body downstream.
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
    # Unreached at runtime under the current call graph: both the gate's
    # `_extract_override_rationale` AND the parser's `parse_pins` use
    # str.splitlines() (pin_caps.py:166, post-#492-F1). splitlines()
    # recognizes \n, \r, U+2028, U+2029, U+0085 (and VT, FF, FS, GS, RS)
    # as line boundaries, so any rationale containing one of those chars
    # is split before OVERRIDE_COMMENT_RE.fullmatch sees it — fullmatch
    # fails, extraction returns None, this validator is called with None
    # and short-circuits at line 135 before reaching this check.
    #
    # Retained for two load-bearing reasons. NOT "defense-in-depth" on
    # the terminator path — that claim is false under the current control
    # flow (see test_splitlines_eats_forbidden_chars_before_validation in
    # test_pin_caps_gate_matrix.py). The real reasons:
    #
    # (1) Fail-loud on future refactor drift. If a future change to
    #     `_extract_override_rationale` replaces splitlines() with a
    #     single-pass regex over the whole fragment, this guard becomes
    #     runtime-live and the gate fails closed on terminator-embedded
    #     rationales instead of silently allowing them. Test
    #     `test_splitlines_eats_forbidden_chars_before_validation` is
    #     the drift detector — it will fail the moment the upstream
    #     invariant breaks, forcing a refactor-time review of THIS block.
    #
    # (2) Derivation anchor for twin-copy drift. `_FORBIDDEN_RATIONALE_CHARS`
    #     is built at module load from `pin_caps._FORBIDDEN_TERMINATOR_TABLE`
    #     so the two char sets never drift (enforced by
    #     test_gate_forbidden_chars_derived_from_parser_table). Removing
    #     the consumer here would require restructuring the anchor; the
    #     net maintenance cost is strictly higher.
    if any(c in rationale for c in _FORBIDDEN_RATIONALE_CHARS):
        return (
            "Override rationale contains a line terminator "
            "(newline, carriage return, or Unicode line separator). "
            "Remove the terminator and retry."
        )
    return None


def _candidate_new_fragment(tool_input: dict) -> str:
    """Return the text to scan for an override-comment line + embedded pin.

    For Write: full content. For Edit: new_string (ignored if non-str).
    """
    if "content" in tool_input:
        content = tool_input.get("content", "")
        return content if isinstance(content, str) else ""
    new_string = tool_input.get("new_string", "")
    return new_string if isinstance(new_string, str) else ""


def _read_baseline(claude_md_path: Path) -> tuple[Optional[str], Optional[str]]:
    """Read current CLAUDE.md under file_lock for TOCTOU defense.

    Returns (content, error_classification). On success: (text, None).
    On I/O failure: (None, _FAIL_BASELINE_READ). file_lock timeout or
    permission error also returns the error branch; the caller decides
    fail-open vs fail-CLOSED based on tool type (Sec N7 asymmetric rule).
    """
    try:
        with file_lock(claude_md_path):
            content = claude_md_path.read_text(encoding="utf-8", errors="replace")
        return content, None
    except (IOError, OSError, TimeoutError, UnicodeDecodeError):
        return None, _FAIL_BASELINE_READ


def _check_tool_allowed(input_data: dict) -> Optional[str]:
    """Determine whether the tool call should be denied.

    Returns the deny reason string if blocked, or None to allow.
    Contract: inner helpers may raise; the outer main() wraps all of
    this in try/except with failure_log + fail-open (except Write-
    baseline parse failure, which is asymmetric fail-CLOSED).
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name not in _GATED_TOOLS:
        return None

    pact_context.init(input_data)

    # Teammate bypass — mirror pin_staleness_gate. Teammates do not edit
    # the project CLAUDE.md; only the team-lead (empty agent_name) is gated.
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None

    file_path_str = tool_input.get("file_path", "")
    claude_md_path = match_project_claude_md(file_path_str)
    if claude_md_path is None:
        return None

    # Override validation on the candidate — done before cap eval so an
    # invalid-override deny surfaces a more actionable message than a
    # plain size-deny on the same pin.
    new_fragment = _candidate_new_fragment(tool_input)
    rationale = _extract_override_rationale(new_fragment)
    invalid_override = _validate_override_rationale(rationale)
    if invalid_override is not None:
        return f"Pin cap violation (invalid override): {invalid_override}"

    # Baseline read + pre-pin parse. Under file_lock for TOCTOU (Sec N3).
    is_write = "content" in tool_input
    baseline_content, baseline_err = _read_baseline(claude_md_path)

    # Early return on baseline read failure — makes the Optional[str]→str
    # narrowing explicit to static analyzers (Pyright) and to human readers.
    # Asymmetric fail-CLOSED (Sec N7): corrupt CLAUDE.md on Write must not
    # unlock the cap. Edit with unreadable baseline fail-opens (can't
    # simulate against nothing).
    if baseline_err is not None or baseline_content is None:
        append_failure(
            classification=baseline_err or _FAIL_BASELINE_READ,
            error=f"read failed for {claude_md_path}",
            source=tool_name,
        )
        return _evaluate_write_as_fresh_start(tool_input) if is_write else None

    # baseline_content is now known to be `str` (narrowed above). Parse
    # pre-state pins from the baseline.
    try:
        pre_pins = _parse_baseline(baseline_content)
    except Exception:  # noqa: BLE001 — bounded fail-open per contract
        append_failure(
            classification=_FAIL_BASELINE_PARSE,
            error=f"parse failed for {claude_md_path}",
            source=tool_name,
        )
        return _evaluate_write_as_fresh_start(tool_input) if is_write else None

    # Simulate post-edit state. Helper raises on malformed tool_input;
    # caller (main) catches and fail-opens.
    try:
        post_pins = apply_edit_and_parse(baseline_content, tool_input)
    except Exception:  # noqa: BLE001 — bounded fail-open per contract
        append_failure(
            classification=_FAIL_SIMULATE,
            error=f"simulate failed for {claude_md_path}",
            source=tool_name,
        )
        return None

    # Net-worse predicate over pre/post pins + embedded-pin check on body.
    # _extract_new_body uses pre/post_pins (when available) to synthesize
    # a smuggle-heading body identically for Write and Edit (#529); see
    # its docstring for the fallback hierarchy.
    new_body = _extract_new_body(
        tool_input, pre_pins=pre_pins, post_pins=post_pins
    )
    return compute_deny_reason(pre_pins, post_pins, new_body=new_body)


def _parse_baseline(content: str):
    """Parse the current CLAUDE.md into a pin list via the bounded parser.

    Section-bounded via staleness._parse_pinned_section so Working Memory
    `### ` subheadings do NOT inflate the count (backend-coder-6 R3).
    """
    from staleness import _parse_pinned_section

    parsed = _parse_pinned_section(content)
    if parsed is None:
        return []
    _, _, pinned_content = parsed
    return parse_pins(pinned_content)


def _evaluate_write_as_fresh_start(tool_input: dict) -> Optional[str]:
    """Asymmetric fail-CLOSED path for Write with unreadable baseline.

    With no readable baseline, we cannot compute net-worse. Instead we
    evaluate the Write's content against an empty pre-state and apply
    the strict post-state predicate. If the Write itself over-caps, we
    deny; if it's clean, we allow (nothing to compare against).
    """
    try:
        # Best-effort simulation against an empty baseline. If apply_edit
        # raises (malformed tool_input), we cannot evaluate → return the
        # generic Write-baseline deny-reason so the user sees why.
        post_pins = apply_edit_and_parse(current_content="", tool_input=tool_input)
    except Exception:  # noqa: BLE001 — bounded
        return _WRITE_BASELINE_DENY_REASON

    violation = evaluate_full_state(post_pins)
    if violation is None:
        # Write produces a state within caps — allow despite the
        # baseline read failure. (The failure_log entry already recorded
        # the read error; no need to block a clean Write.)
        return None

    # Over-cap Write with no baseline → fail-CLOSED with explicit reason.
    return _WRITE_BASELINE_DENY_REASON


def _extract_new_body(
    tool_input: dict,
    pre_pins: Optional[list] = None,
    post_pins: Optional[list] = None,
) -> str:
    """Return the candidate body text for embedded-pin smuggle detection.

    Semantics note: the embedded-pin check exists to catch a new or
    mutated pin whose BODY contains a `### ` heading (smuggling an extra
    pin past the count cap on reload).

    Both Write (full-file replacement, #492 F7 — security-engineer-1
    MEDIUM) and Edit (#529) use the SAME pre-vs-post heading diff on
    parsed pins when pre_pins and post_pins are both available.
    Legitimate CLAUDE.md content contains pin headings by construction,
    so scanning the raw payload (Write: full content; Edit: new_string)
    would reject every legitimate add. The defense operates at the
    PARSED post_pins level:

    Synthesize a synthetic "### H" heading line for each post_pin that
    BOTH (1) is not present in pre_pins by heading AND (2) lacks a
    `date_comment` (i.e., has no `<!-- pinned: ... -->` marker
    immediately preceding its heading). Such pins are the signature of
    a smuggle: parse_pins splits on every `### ` it sees, so an embedded
    `### Smuggled` heading inside another pin's body materializes as a
    separate post_pin but has `date_comment=None` because no dated
    marker preceded it. Legitimately added pins always carry a date
    comment (per PACT pin grammar).

    Concatenating these synthetic headings produces a new_body that
    `compute_deny_reason`'s embedded-pin check (`parse_pins(new_body)`)
    will flag as a pin structure, denying with DENY_REASON_EMBEDDED_PIN.

    Pre-existing smuggled pins (pre_pins containing a `date_comment=None`
    pin from a prior manual edit) are excluded via the heading-in-pre
    check — pre-malformed state never denies (F1 livelock precedent).

    Fallback contract — signature-level defense only. No production path
    reaches the helper with `pre_pins=None` or `post_pins=None`: the sole
    caller `_check_tool_allowed` computes both via `_parse_baseline` and
    `apply_edit_and_parse`, each of which either returns a list or raises
    an exception the caller catches with an early `return None` before
    reaching this helper. The `Optional[list] = None` defaults exist so
    a future direct test probe that omits pins gets a safe, deterministic
    result rather than a TypeError:
      - Edit fallback: fall through to naive `new_string`-as-body check
        (pre-#529 conservative deny shape).
      - Write fallback: return "" (scanning full content with naive logic
        would reject every Write by construction).
    """
    if pre_pins is not None and post_pins is not None:
        pre_headings = {p.heading for p in pre_pins}
        # A smuggle signal: post pin whose heading is NEW AND carries no
        # date-comment marker. Surface it by synthesizing the heading line
        # so compute_deny_reason's parse_pins(new_body) check registers it
        # as an embedded pin structure.
        smuggle_headings = [
            p.heading
            for p in post_pins
            if p.heading not in pre_headings and p.date_comment is None
        ]
        if not smuggle_headings:
            return ""
        # Join with newlines so parse_pins treats each as its own line.
        return "\n".join(f"{h}\nsmuggled body marker\n" for h in smuggle_headings)

    if "content" in tool_input:
        return ""
    new_string = tool_input.get("new_string", "")
    return new_string if isinstance(new_string, str) else ""


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception as exc:  # noqa: BLE001 — SACROSANCT fail-open
        # Unexpected fault → record for post-hoc observability then allow.
        # Note: Write-baseline fail-CLOSED is handled INSIDE _check_tool_allowed
        # as an explicit return, not via this catch-all. Anything reaching
        # this except is truly unexpected and the fail-open is justified.
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

    if deny_reason:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
