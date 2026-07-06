"""
Location: pact-plugin/hooks/shared/pact_harvest.py
Summary: Harvest-domain CLI exposing the two journal-adjacent resolutions the
         pact-handoff-harvest skill needs but session_journal.py does not:
         off-lead session-dir reconstruction and artifact_paths supersede.
Used by: pact-handoff-harvest/SKILL.md (Steps 0 and 3.5), invoked as a direct
         script — python3 {plugin_root}/hooks/shared/pact_harvest.py <subcommand>.
         NOT a lifecycle hook: this file MUST NOT be registered in hooks.json.

Why this file exists: the secretary runs the harvest OFF-LEAD (a teammate
frame), where pact_context.get_session_dir() / read_events() false-return ''
and silently yield 0 events. The skill therefore needs explicit-path,
masked-read-safe entry points. The journal READS (agent_handoff,
variety_assessed) reuse session_journal.py's existing `read` subcommand (DRY —
no read-events mirror here). This file adds only the two pieces session_journal
does not already expose:
  - resolve-session-dir: wraps pact_context.reconstruct_session_dir (Step 0).
  - resolve-artifacts:    wraps session_journal.resolve_latest_artifacts, doing
                          the artifact_paths read then the supersede (Step 3.5).

Subcommand I/O contract (stdout = DATA only, stderr = diagnostics):
  - exit 0: resolved successfully (INCLUDING a legitimately-empty result).
  - exit 2: unresolvable / bad input — the skill's "report the gap and STOP,
            do NOT fall back to a path-less read" trigger. The skill keys this
            branch on the EXIT CODE, never on parsing stdout for emptiness, so
            a stray byte cannot defeat it.
  - exit 1: reserved for an uncaught internal error.
Note: this 0/2/1 contract intentionally differs from session_journal.py's CLI
(its _validate_cli_session_dir returns 1 on bad --session-dir). Here 1 is
reserved for internal errors, so bad input is 2; both are non-zero, so the
skill's `if ! out=$(...); then stop; fi` gate works for either CLI.

Sibling imports use the same lazy dual-import as session_journal.py (package
path first, bare path fallback for the direct-script run where shared/ is
sys.path[0]). Done INSIDE the handlers so tests can monkeypatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package-path import (`from shared.X import Y`) resolvable when this
# file is run as a direct script. In script mode Python puts the script's OWN
# directory (`hooks/shared/`) on sys.path[0], NOT `hooks/`, so `from shared.X`
# would raise ModuleNotFoundError AND the bare `from X` fallback would import a
# sibling (e.g. pact_context) as a TOP-LEVEL module — breaking ITS own
# package-relative imports (`from .session_state import ...`). Putting `hooks/`
# (the parent of this file's `shared/` dir) on sys.path makes `shared` a real
# package, so the sibling's relative imports resolve. Idempotent: skip if
# already present (e.g. when imported as `shared.pact_harvest` from hooks/tests).
_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

# Exit codes (see module docstring for the contract).
_EXIT_OK = 0
_EXIT_INTERNAL_ERROR = 1
_EXIT_UNRESOLVED = 2


def _resolve_session_dir(context_file: str) -> int:
    """Handle `resolve-session-dir`: reconstruct the absolute session dir.

    Reads `project_dir` + `session_id` out of the context file and calls
    pact_context.reconstruct_session_dir (the off-lead, SSOT path resolver).
    On success prints the absolute session_dir (single line) to stdout and
    returns 0. On any failure — missing/unreadable/invalid-JSON context file,
    missing fields, or a falsy '' return from reconstruct_session_dir — prints
    a one-line diagnostic to stderr, prints nothing to stdout, and returns 2.
    """
    # Lazy dual-import (package path for hooks/tests, bare for direct-script).
    try:
        from shared.pact_context import reconstruct_session_dir
    except ImportError:  # pragma: no cover - exercised via direct-script run
        from pact_context import reconstruct_session_dir  # type: ignore[no-redef]

    try:
        raw = Path(context_file).read_text()
    except (OSError, ValueError) as exc:
        # ValueError covers an embedded NUL byte in the path (Path/open raises
        # "embedded null byte"). Unreachable via the CLI (execve strips NUL
        # from argv) but guarded so a NUL/bad path is bad-input (exit 2), never
        # an uncaught exit-1 crash — same handling as the missing-file case.
        print(
            f"pact_harvest: cannot read context file {context_file!r}: {exc}",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED

    try:
        ctx = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"pact_harvest: invalid JSON in context file {context_file!r}: "
            f"{exc}",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED

    if not isinstance(ctx, dict):
        print(
            f"pact_harvest: context file {context_file!r} is not a JSON object",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED

    project_dir = ctx.get("project_dir")
    session_id = ctx.get("session_id")
    session_dir = reconstruct_session_dir(project_dir, session_id)
    if not session_dir:
        print(
            "pact_harvest: could not reconstruct session_dir (missing/empty "
            "project_dir or session_id in context file)",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED

    print(session_dir)
    return _EXIT_OK


def _resolve_artifacts(session_dir: str, feature: str) -> int:
    """Handle `resolve-artifacts`: emit the superseded paths-by-workflow object.

    Reads this session's `artifact_paths` events (explicit-path, masked-read-
    safe) and applies the supersede-by-(workflow, feature)-latest-ts dedup via
    resolve_latest_artifacts. Prints a single-line JSON object
    `{workflow: [abs_path, ...]}` (empty -> `{}`) to stdout and returns 0.
    Returns 2 on an empty, non-absolute, or out-of-tree --session-dir (a
    bad-input stop trigger): the empty/non-absolute checks mirror
    session_journal's read checks (remapped to this CLI's exit-2 contract),
    and the containment check (see `_session_dir_is_contained`) rejects any
    path outside the pact-sessions root so a stray journal under an unrelated
    directory cannot be read.
    """
    rc = _validate_session_dir_arg(session_dir)
    if rc != _EXIT_OK:
        return rc

    if not _session_dir_is_contained(session_dir):
        print(
            "pact_harvest: --session-dir is outside the pact-sessions root",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED

    # Lazy dual-import (package path for hooks/tests, bare for direct-script).
    try:
        from shared.session_journal import (
            read_events_from,
            resolve_latest_artifacts,
        )
    except ImportError:  # pragma: no cover - exercised via direct-script run
        from session_journal import (  # type: ignore[no-redef]
            read_events_from,
            resolve_latest_artifacts,
        )

    events = read_events_from(session_dir, "artifact_paths")
    resolved = resolve_latest_artifacts(events, feature)
    print(json.dumps(resolved, separators=(",", ":")))
    return _EXIT_OK


def _validate_session_dir_arg(session_dir: str) -> int:
    """Validate `--session-dir`, returning 0 ok or 2 (this CLI's bad-input code).

    Mirrors session_journal._validate_cli_session_dir's two checks (non-empty,
    absolute) but returns this CLI's exit-2 bad-input code rather than its
    exit-1 — here 1 is reserved for an uncaught internal error. An empty or
    relative session_dir would otherwise resolve a stray journal under the
    caller's CWD.
    """
    if not session_dir:
        print(
            "pact_harvest: --session-dir must be non-empty",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED
    if not Path(session_dir).is_absolute():
        print(
            "pact_harvest: --session-dir must be an absolute path",
            file=sys.stderr,
        )
        return _EXIT_UNRESOLVED
    return _EXIT_OK


def _session_dir_is_contained(session_dir: str) -> bool:
    """Return True iff `session_dir` resolves UNDER the pact-sessions root.

    Defense-in-depth on top of `_validate_session_dir_arg`: a --session-dir
    outside `<config>/pact-sessions/` has no legitimate journal, so reading one
    there would surface an unrelated `session-journal.jsonl`. The root derives
    from `get_claude_config_dir()` — the env-or-home SSOT in shared/paths.py,
    never a hardcoded `~/.claude` — so it tracks $CLAUDE_CONFIG_DIR. Both the
    root and the candidate are `.resolve()`d (symlink-normalized) and compared
    via `Path.parents` containment, mirroring `_build_session_path`'s own
    traversal guard: a legit `<config>/pact-sessions/<slug>/<id>` (two levels
    under the root) always passes; only an out-of-tree path is rejected.

    Fail-CLOSED (return False -> the caller's exit-2 bad-input path) when the
    root cannot be derived (`get_claude_config_dir()` -> `Path.home()` can
    raise RuntimeError) or either `.resolve()` raises (OSError on a symlink
    loop, ValueError on an embedded NUL): an unverifiable path is treated as
    bad input rather than read blindly. This is theoretical on the live path —
    Step 0 already resolved the same root to PRODUCE the session_dir.
    """
    # Lazy dual-import (package path for hooks/tests, bare for direct-script).
    try:
        from shared.paths import get_claude_config_dir
    except ImportError:  # pragma: no cover - exercised via direct-script run
        from paths import get_claude_config_dir  # type: ignore[no-redef]

    try:
        root = (get_claude_config_dir() / "pact-sessions").resolve()
        candidate = Path(session_dir).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return candidate == root or root in candidate.parents


def main() -> int:
    """CLI entry point for harvest-domain resolutions.

    Subcommands:
        resolve-session-dir — reconstruct the absolute session dir off-lead
            from a pact-session-context.json (Step 0).
        resolve-artifacts   — emit the superseded artifact paths-by-workflow
            object for one feature (Step 3.5).

    Returns:
        0 ok (incl. legitimately-empty), 2 unresolved/bad-input (skill stop
        trigger), 1 reserved for an uncaught internal error.
    """
    parser = argparse.ArgumentParser(
        description="PACT harvest CLI — off-lead session-dir + artifact "
        "supersede resolutions for the pact-handoff-harvest skill.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # --- resolve-session-dir ---
    session_p = sub.add_parser(
        "resolve-session-dir",
        help="Reconstruct the absolute session dir from a context file",
    )
    session_p.add_argument(
        "--context-file",
        required=True,
        help="Absolute path to pact-session-context.json",
    )

    # --- resolve-artifacts ---
    artifacts_p = sub.add_parser(
        "resolve-artifacts",
        help="Emit the superseded artifact paths-by-workflow for a feature",
    )
    artifacts_p.add_argument(
        "--session-dir",
        required=True,
        help="Absolute session directory path",
    )
    artifacts_p.add_argument(
        "--feature",
        required=True,
        help="Feature slug to resolve artifacts for",
    )

    args = parser.parse_args()

    if args.command == "resolve-session-dir":
        return _resolve_session_dir(args.context_file)
    # `sub.required = True` guarantees the only remaining choice is
    # resolve-artifacts; argparse exits 2 before main() runs for any missing or
    # unknown subcommand, so this fall-through is exhaustive. An uncaught
    # exception inside a handler is what yields the `_EXIT_INTERNAL_ERROR` (1)
    # contract — Python's default exit on an unhandled exception.
    return _resolve_artifacts(args.session_dir, args.feature)


if __name__ == "__main__":
    raise SystemExit(main())
