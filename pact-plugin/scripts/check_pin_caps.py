#!/usr/bin/env python3
"""
Check Pin Caps CLI Entry (advisory only — cycle-8 demotion)

Location: pact-plugin/scripts/check_pin_caps.py

Summary: Advisory CLI for the PACT pin-caps subsystem. Reports current
slot status and the evictable-pin list so /PACT:prune-memory and status
queries have a structured view of CLAUDE.md's pin state. This CLI does
NOT enforce caps — enforcement lives in hooks/pin_caps_gate.py (cycle-8
re-architecture #492). The curator sees cap violations as PreToolUse
deny decisions, not exit codes from this script.

Usage:
  # Default (implicit --status)
  python3 check_pin_caps.py
  python3 check_pin_caps.py --status
  python3 check_pin_caps.py --list-evictable

All three forms emit the same JSON payload. `--list-evictable` is an
alias carried for documentation clarity when a caller wants only the
eviction list; callers should treat `--status` as the canonical name.

JSON output contract (stdout):
  {
    "allowed": true,                       # always true (advisory only)
    "violation": null,                     # always null (no enforcement)
    "slot_status": "Pin slots: N/12 used, <N> chars remaining on largest pin",
    "evictable_pins": [
      {"index": int, "heading": str, "chars": int,
       "stale": bool, "override": bool}, ...
    ]
  }

Exit codes:
  0 — normal (status query or fail-open degradation)
  2 — RESERVED: NEVER used. argparse's own internal `--help` / validation
      errors exit 2 from inside argparse; we re-raise those but emit no
      other exit-2 path. SACROSANCT fail-open: any read/parse fault
      yields exit 0 with a "Pin slots: unknown (<reason>); proceeding"
      slot_status so the user-facing /PACT:pin-memory command surfaces
      the degradation reason instead of silently allowing.

History: before cycle-8 this CLI was the primary cap enforcer, invoked
via bash heredoc from /PACT:pin-memory. That surface had 7 cycles of
shell-scaffolding hardening (heredoc quoting, nonce delimiters,
argv-injection guards, override rationale in-band validation). Cycle-8
moved enforcement to a PreToolUse hook, eliminating the shell-scaffolding
surface by construction. The CLI retains the read-only status/listing
role because /PACT:prune-memory and diagnostic tooling still need
structured evictable-pin data without firing the hook gate.

Used by:
  - commands/prune-memory.md (cycle-8): reads --status to paginate
    evictable pins into AskUserQuestion options
  - Diagnostic inspection during debugging
  - Test files: tests/test_check_pin_caps.py (advisory-path coverage)
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# Load pin_caps and staleness from hooks/ via importlib spec-loading.
# Historically this script did `sys.path.insert(0, hooks_dir)` which
# prepends to sys.path, risking stdlib shadowing if a future file at
# hooks/types.py / hooks/json.py / hooks/re.py etc. landed — a prepended
# hooks dir would match BEFORE the stdlib, silently redirecting imports.
# importlib spec-loading binds pin_caps + staleness by explicit file
# path, not by name resolution against sys.path.
#
# staleness.py internally imports `from shared.claude_md_manager` and
# `from pin_caps`. We handle both by:
#   (a) loading pin_caps first and registering it in sys.modules so
#       staleness's `from pin_caps` finds it without sys.path lookup;
#   (b) APPENDING hooks_dir to sys.path (not prepending) so the `shared`
#       subpackage resolves but stdlib retains priority on any name
#       collision with a future hooks file.
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.append(str(_HOOKS_DIR))


def _load_hook_module(name: str):
    """Load a module from hooks/ by explicit file path.

    Registers the loaded module in sys.modules under `name` before
    executing so that other modules loaded via this same helper can
    resolve `from {name} import ...` against the already-loaded object.
    """
    module_path = _HOOKS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_pin_caps = _load_hook_module("pin_caps")
_staleness = _load_hook_module("staleness")

format_slot_status = _pin_caps.format_slot_status
parse_pins = _pin_caps.parse_pins
_parse_pinned_section = _staleness._parse_pinned_section
get_project_claude_md_path = _staleness.get_project_claude_md_path


def _build_evictable_pins(pins):
    """Transform parsed pins into the evictable_pins JSON shape.

    Order is presentation order (top-to-bottom in CLAUDE.md). Caller
    (prune-memory.md) paginates 4-at-a-time into AskUserQuestion options.
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
    list and a short reason string. Callers surface the reason in
    slot_status so the user sees "unknown (...)" instead of a fake "0/12".
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


def _emit(slot_status, evictable_pins):
    """Write the advisory JSON payload to stdout.

    Shape preserved from the pre-demotion contract so any callers reading
    `allowed`/`violation` keys continue to parse cleanly — they'll just
    always see `true`/`null` now that enforcement lives in the hook.
    """
    payload = {
        "allowed": True,
        "violation": None,
        "slot_status": slot_status,
        "evictable_pins": evictable_pins,
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _fail_open(reason):
    """Emit an advisory payload with the degradation reason in slot_status.

    Callers see "Pin slots: unknown (<reason>); proceeding" — identical
    to the pre-demotion shape, so /PACT:prune-memory and any diagnostic
    consumers render the same "unknown; proceeding" text on resolution
    failure.
    """
    slot_status = f"Pin slots: unknown ({reason}); proceeding"
    _emit(slot_status=slot_status, evictable_pins=[])
    return 0


def _main_inner(argv=None):
    parser = argparse.ArgumentParser(
        prog="check_pin_caps",
        description=(
            "Advisory-only pin-caps status CLI. Enforcement lives in the "
            "pin_caps_gate PreToolUse hook (cycle-8); this CLI reports "
            "current state and the evictable-pin list."
        ),
    )
    # Both flags are kept for documentation clarity. Semantics are identical
    # — either flag (or no flag at all) emits the same JSON payload. Not
    # mutually-exclusive because there's nothing to conflict on.
    parser.add_argument(
        "--status",
        action="store_true",
        help=(
            "Status-only query (default behavior): emit slot status + "
            "evictable pins."
        ),
    )
    parser.add_argument(
        "--list-evictable",
        action="store_true",
        help=(
            "Alias for --status, for callers that want to signal intent "
            "to consume only the evictable_pins field."
        ),
    )
    # parse_args accepts unknown flags silently via parse_known_args so a
    # caller passing a retired cycle-7 flag (e.g. --new-body, --has-override)
    # does not crash with argparse-exit-2 — the retired flags are ignored
    # and the advisory payload still emits. SACROSANCT fail-open carries to
    # the argv shape: no new exit-2 surface for mistyped or retired flags.
    parser.parse_known_args(argv)

    pins, fail_reason = _resolve_pins()
    if fail_reason is not None:
        return _fail_open(fail_reason)

    slot_status = format_slot_status(pins)
    evictable_pins = _build_evictable_pins(pins)
    _emit(slot_status=slot_status, evictable_pins=evictable_pins)
    return 0


def main(argv=None):
    """Outer fail-open wrapper — SACROSANCT "NEVER exit 2" contract.

    Any uncaught exception from `_main_inner` (including argparse bugs,
    future refactors raising unexpected types, or downstream helper
    regressions) is converted to a fail-open advisory with a diagnostic
    slot_status. This preserves the fail-open invariant under any
    future regression that would otherwise crash with exit 1 or (worse)
    a Python traceback to exit code 2.

    Note: argparse `--help` calls `sys.exit()` directly from inside
    argparse, which raises SystemExit. We explicitly DO NOT catch
    SystemExit — `--help` (exit 0) is argparse-controlled. `parse_known_args`
    means stray flags don't trigger argparse's own exit-2 validation,
    so this branch is practically only `--help`.
    """
    try:
        return _main_inner(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — SACROSANCT fail-open
        return _fail_open(f"internal error: {type(exc).__name__}")


if __name__ == "__main__":
    sys.exit(main())
