#!/usr/bin/env python3
"""
Check Pin Caps CLI Entry

Location: pact-plugin/scripts/check_pin_caps.py

Summary: Command-line entry point invoked by /PACT:pin-memory before any
pin add or pin edit. Parses the project CLAUDE.md Pinned Context section
via hooks/pin_caps.parse_pins, applies count + size cap predicates, and
emits a JSON decision to stdout.

Usage:
  # Preferred — read pin body from stdin (shell-injection safe):
  printf '%s' "$CANDIDATE_BODY" | python3 check_pin_caps.py --body-from-stdin [--has-override]

  # Legacy — pass pin body as argv (retained for backward compatibility;
  # callers SHOULD migrate to --body-from-stdin to avoid shell-quoting
  # hazards on bodies containing control characters or shell metacharacters):
  python3 check_pin_caps.py --new-body "pin body text" [--has-override]

  # Status-only query (no add under consideration) — emits slot status
  python3 check_pin_caps.py --status

  --new-body, --body-from-stdin, and --status are mutually exclusive.

JSON output contract (stdout):
  {
    "allowed": bool,
    "violation": {"kind": "count|size|stale|embedded_pin|empty|invalid_override",
                  "detail": "...",
                  "offending_pin_chars": int|null, "current_count": int|null}
                 | null,
    "slot_status": "Pin slots: N/12 used, ...",
    "evictable_pins": [
      {"index": int, "heading": str, "chars": int,
       "stale": bool, "override": bool}, ...
    ]
  }

Exit codes:
  0 — add allowed (or status query)
  1 — add refused (cap violation)
  2 — reserved: NEVER used by this CLI. Fail-open: any I/O or parse error
      yields allowed=true with an informational slot_status so the
      pin-memory flow degrades gracefully rather than DoS-ing the user.

Used by:
  - commands/pin-memory.md: bash step before pin add; branches on exit code
  - Test files: tests/test_check_pin_caps.py
"""

import argparse
import json
import sys
from pathlib import Path

# Import from hooks/pin_caps and hooks/staleness (path resolution).
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS_DIR))

from pin_caps import (  # noqa: E402
    check_add_allowed,
    format_slot_status,
    parse_pins,
)
from staleness import (  # noqa: E402
    _parse_pinned_section,
    get_project_claude_md_path,
)


def _build_evictable_pins(pins):
    """Transform parsed pins into the evictable_pins JSON shape.

    Order is presentation order (top-to-bottom in CLAUDE.md). Caller
    (pin-memory.md) paginates 4-at-a-time into AskUserQuestion options.
    """
    evictable = []
    for idx, pin in enumerate(pins):
        heading_text = pin.heading
        if heading_text.startswith("### "):
            heading_text = heading_text[4:]
        evictable.append({
            "index": idx,
            "heading": heading_text,
            "chars": pin.body_chars,
            "stale": pin.is_stale,
            "override": pin.override_rationale is not None,
        })
    return evictable


def _resolve_pins():
    """Resolve CLAUDE.md and return parsed pins, or ([], reason_str) on failure.

    Fail-open: any resolution / read / parse failure yields an empty pin
    list and a short reason string. Callers treat empty + reason as
    "unknown state, allow the add".
    """
    claude_md = get_project_claude_md_path()
    if claude_md is None:
        return [], "claude.md not found"

    try:
        content = claude_md.read_text(encoding="utf-8")
    except (IOError, OSError, UnicodeDecodeError):
        return [], "claude.md unreadable"

    parsed = _parse_pinned_section(content)
    if parsed is None:
        return [], "no pinned section"

    _, _, pinned_content = parsed
    try:
        pins = parse_pins(pinned_content)
    except Exception:  # noqa: BLE001 — fail-open by construction
        return [], "parse error"

    return pins, None


def _emit(
    allowed,
    violation=None,
    slot_status="",
    evictable_pins=None,
):
    payload = {
        "allowed": bool(allowed),
        "violation": None,
        "slot_status": slot_status,
        "evictable_pins": evictable_pins or [],
    }
    if violation is not None:
        payload["violation"] = {
            "kind": violation.kind,
            "detail": violation.detail,
            "offending_pin_chars": violation.offending_pin_chars,
            "current_count": violation.current_count,
        }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _fail_open(reason):
    """Emit an allow decision when we cannot determine cap state.

    Slot status carries the reason so the user-facing command still
    renders something actionable rather than silently allowing.
    """
    slot_status = f"Pin slots: unknown ({reason}); proceeding"
    _emit(allowed=True, violation=None, slot_status=slot_status, evictable_pins=[])
    return 0


def _main_inner(argv=None):
    parser = argparse.ArgumentParser(
        prog="check_pin_caps",
        description="Enforce pin count + size caps on project CLAUDE.md",
    )
    # Mutually-exclusive body sources: shell-safe stdin (preferred),
    # legacy argv (backward compat), or status-only (no body).
    body_group = parser.add_mutually_exclusive_group()
    body_group.add_argument(
        "--new-body",
        default=None,
        help=(
            "Body text of the proposed new pin (triggers cap check). "
            "Legacy argv path — prefer --body-from-stdin for bodies "
            "containing shell metacharacters or control chars."
        ),
    )
    body_group.add_argument(
        "--body-from-stdin",
        action="store_true",
        help=(
            "Read proposed pin body from stdin (preferred). Reads exactly "
            "what the pipe provides; no shell-quoting hazards."
        ),
    )
    body_group.add_argument(
        "--status",
        action="store_true",
        help="Status-only query: emit slot status, no add check",
    )
    parser.add_argument(
        "--has-override",
        action="store_true",
        help="Proposed pin carries a valid pin-size-override rationale",
    )
    args = parser.parse_args(argv)

    # Resolve the new body from stdin if requested. Stdin is a distinct
    # ingestion path: the script consumes sys.stdin.read() and treats it
    # as the pin body only — it is NEVER parsed as argv or evaluated.
    new_body = args.new_body
    if args.body_from_stdin:
        new_body = sys.stdin.read()

    pins, fail_reason = _resolve_pins()
    if fail_reason is not None:
        # Fail-open: unknown state, allow the add rather than block the user.
        # Uniformly emit the "Pin slots: unknown (...); proceeding" line for
        # ALL invocations (including --status) so the user-facing command
        # surfaces the degradation reason instead of silently returning a
        # plausible-looking 0-used status.
        return _fail_open(fail_reason)

    slot_status = format_slot_status(pins)
    evictable_pins = _build_evictable_pins(pins)

    if args.status or new_body is None:
        # Status query or no body provided — emit current state, no check.
        _emit(
            allowed=True,
            violation=None,
            slot_status=slot_status,
            evictable_pins=evictable_pins,
        )
        return 0

    violation = check_add_allowed(
        existing=pins,
        new_body=new_body,
        new_has_override=args.has_override,
    )

    if violation is None:
        _emit(
            allowed=True,
            violation=None,
            slot_status=slot_status,
            evictable_pins=evictable_pins,
        )
        return 0

    _emit(
        allowed=False,
        violation=violation,
        slot_status=slot_status,
        evictable_pins=evictable_pins,
    )
    return 1


def main(argv=None):
    """Outer fail-open wrapper — SACROSANCT "NEVER exit 2" contract.

    Any uncaught exception from `_main_inner` (including argparse bugs,
    future refactors raising unexpected types, or downstream helper
    regressions) is converted to an allow decision with a diagnostic
    slot_status. This preserves the fail-open invariant under any
    future regression that would otherwise crash with exit 1 or (worse)
    a Python traceback to exit code 2.

    Note: argparse `--help` and argparse validation errors call
    `sys.exit()` directly from inside argparse, which raises SystemExit.
    We explicitly DO NOT catch SystemExit — `--help` (exit 0) and
    argparse error (exit 2) are argparse-controlled exits, not runtime
    faults. Re-raising preserves argparse's built-in UX; a genuine
    argparse internal-error crash would surface as a plain Exception
    and be caught by the fail-open branch below.
    """
    try:
        return _main_inner(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — SACROSANCT fail-open
        return _fail_open(f"internal error: {type(exc).__name__}")


if __name__ == "__main__":
    sys.exit(main())
