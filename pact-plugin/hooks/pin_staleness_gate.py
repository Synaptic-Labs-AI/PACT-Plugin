#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_staleness_gate.py
Summary: PreToolUse marker-gate that denies Edit/Write on the project
         CLAUDE.md's Pinned Context section when a stale-pins-pending
         marker is present in the session directory.
Used by: hooks.json PreToolUse with matcher \"Edit|Write\"

Phase F defense-in-depth backstop for #492. The SessionStart
additionalContext directive (session_init.py step 4b) is the primary
enforcement; this hook is the secondary guard that fires at the moment
of the Edit/Write call rather than relying on the orchestrator honoring
the directive.

Gate triggers only when ALL hold:
  1. Tool is Edit or Write (enforced by hooks.json matcher)
  2. Target file path resolves to the project CLAUDE.md
  3. Edit locus is within the Pinned Context section (line-bounded)
  4. Stale-pins-pending marker exists in session_dir
  5. Not a teammate session (teammates bypass; CLAUDE.md edits are scoped to the team-lead session)

SACROSANCT: every raisable path is wrapped in try/except that defaults
to allow (exit 0 with suppressOutput). A gate bug must never block a
tool call. Fail-open: missing session_dir, unparseable CLAUDE.md,
unresolvable marker → allow.

Input: JSON from stdin with tool_name, tool_input, session_id, etc.
Output: JSON with hookSpecificOutput.permissionDecision (deny case)
        or {\"suppressOutput\": true} (allow / passthrough)
"""

import json
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared import match_project_claude_md
from pin_caps import parse_pins

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Marker file name written when stale-pins-pending state is detected.
# Placed in session_dir so it is per-session scoped — clears on new
# session, cannot persist across /clear (session_dir is rebuilt per session).
PIN_STALENESS_MARKER_NAME = "pin-staleness-pending"

_DENY_REASON = (
    "Pinned Context edits are gated: stale pins detected. "
    "Run /PACT:pin-memory to archive stale pins before editing "
    "the ## Pinned Context section of CLAUDE.md."
)

_GATED_TOOLS = frozenset({"Edit", "Write"})


def _count_pin_comments(text: str) -> int:
    """Count pins using `parse_pins` as the canonical oracle.

    Symmetric-oracle invariant (closes 2 HIGH bypasses): the gate MUST
    count pins using the same parser that enforces the count cap at
    add-time (`pin_caps.parse_pins`). A regex substring count of
    `<!-- pinned:` is asymmetric with `parse_pins`, which:
      (a) recognizes a bare `### Heading` (no date comment) as a Pin,
      (b) tolerates arbitrary whitespace between `<!--` and `pinned:`
          via its `\\s*` patterns (e.g. `<!--  pinned:` double-space),
      (c) matches case-insensitively.
    Substring counts undercount (a) and (b), letting an adversarial ADD
    slip past the ADD-shape gate while still landing in CLAUDE.md as a
    parse_pins-visible pin.

    Opportunistic managed-region bounding (Arch-M3): if `text` contains
    PACT_MANAGED_START/END markers (full CLAUDE.md or Write payload),
    count only within the managed region. This closes the decoy-bypass
    where pin-shaped tokens in user-authored prose or code blocks
    outside the managed region would inflate the count and either
    falsely block (add-shape) or falsely allow (archival).

    If no managed markers are present (fragment from Edit.old_string /
    Edit.new_string), count on the full input — Edit fragments are
    structurally inside the section being mutated, so bounding is
    unnecessary and would miss legitimate pins.

    Fail-open: non-str input returns 0. Any parse_pins failure (should
    not raise by its own contract, but defense-in-depth) returns 0.
    """
    if not isinstance(text, str):
        return 0
    try:
        from shared.claude_md_manager import extract_managed_region
        region_result = extract_managed_region(text)
        if region_result is not None:
            region_text, _ = region_result
            try:
                return len(parse_pins(region_text))
            except Exception:  # noqa: BLE001 — fail-open
                return 0
    except Exception:  # noqa: BLE001 — fail-open to full-text count
        pass
    try:
        return len(parse_pins(text))
    except Exception:  # noqa: BLE001 — fail-open
        return 0


def _is_add_shaped_edit(tool_input: dict, claude_md_path: Path) -> bool:
    """Return True if the Edit/Write adds a net-new pin comment.

    The marker-gate fires only on ADD-shaped edits so the user can still
    ARCHIVE stale pins (reducing pin count) to resolve the condition.
    Archival edits (old_string contains `<!-- pinned:`, new_string does
    not, or count strictly decreases) and refactor edits (pin count
    unchanged) are allowed.

    For Edit tool:
      - ADD: new_count > old_count in the replacement strings
      - ARCHIVE: new_count < old_count  → allow
      - REFACTOR: new_count == old_count → allow (pin body rewrite,
        STALE marker injection, etc.)

    For Write tool (full-file replacement):
      - Compare pin count in new content vs. current on-disk content.
      - ADD: new file has MORE pin comments than current → block
      - Otherwise → allow

    Fail-open: any shape-detection error returns False (allow). This
    preserves the SACROSANCT gate invariant.
    """
    # WHY net-new detection: the gate exists to stop the user adding a 13th
    # pin while stale pins remain. Archival is the REMEDIATION the user is
    # directed to perform — denying it causes a same-session livelock
    # (reviewer-security F1). A substring match on `<!-- pinned:` is
    # symmetric across add and archive shapes, so it cannot distinguish
    # them. A strict count increase is asymmetric by construction: ADD
    # raises the count, ARCHIVE lowers it, REFACTOR leaves it unchanged.
    try:
        if "content" in tool_input:
            # Write tool — diff against current file content.
            new_content = tool_input.get("content", "")
            if not isinstance(new_content, str):
                return False
            try:
                current = claude_md_path.read_text(encoding="utf-8")
            except (IOError, OSError, UnicodeDecodeError):
                # Cannot compare → fail-open.
                return False
            return _count_pin_comments(new_content) > _count_pin_comments(current)

        # Edit tool — compare old_string vs new_string pin counts.
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        return _count_pin_comments(new_string) > _count_pin_comments(old_string)
    except Exception:  # noqa: BLE001 — SACROSANCT fail-open
        return False


def _check_tool_allowed(input_data: dict) -> str | None:
    """Determine whether the tool call should be denied.

    Returns the deny reason string if blocked, or None to allow.
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name not in _GATED_TOOLS:
        return None

    pact_context.init(input_data)

    # Teammate bypass — teammates don't edit project CLAUDE.md
    # (worktree scope rule), so this gate is team-lead-only.
    agent_name = pact_context.resolve_agent_name(input_data)
    if agent_name:
        return None

    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    marker_path = Path(session_dir) / PIN_STALENESS_MARKER_NAME
    if not marker_path.exists():
        return None

    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return None

    file_path_str = tool_input.get("file_path", "")
    claude_md_path = match_project_claude_md(file_path_str)
    if claude_md_path is None:
        return None

    # Narrow matcher: block only ADD-shaped edits (net-new pin comment).
    # Archival edits (pin removal) and refactor edits (pin body rewrite)
    # are allowed so the user can resolve the stale-pins condition by
    # running /PACT:pin-memory within the same session. Fix for #492
    # F1 marker livelock.
    if not _is_add_shaped_edit(tool_input, claude_md_path):
        return None

    return _DENY_REASON


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception:
        # SACROSANCT: any exception in gate logic → fail-open.
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
