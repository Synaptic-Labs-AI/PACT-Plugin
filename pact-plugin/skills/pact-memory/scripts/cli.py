"""
PACT Memory CLI Entry Point

Location: pact-plugin/skills/pact-memory/scripts/cli.py

Thin command-line facade over the PACTMemory API. Translates CLI arguments
to PACTMemory method calls and serializes results as JSON. Contains zero
business logic — all intelligence stays in memory_api.py.

Used by:
- SKILL.md: Documents CLI invocation for agents via ${CLAUDE_SKILL_DIR}
- Tests: test_memory_cli.py for unit and subprocess integration tests

Usage:
    python3 cli.py <command> [args] [--options]

Commands:
    save <json>          Save a memory object (or --stdin for piped input)
    search <query>       Semantic search across memories
    list [--limit N]     List recent memories (default: 20)
    get <id|prefix>      Retrieve a memory by full ID or unique prefix (>= 7 chars)
    update <id|prefix> <json>
                         Update an existing memory by full ID or unique prefix
                         (or --stdin for piped input). Ambiguous prefix is refused.
    delete <id|prefix>   Delete a memory by full ID or unique prefix.
                         Ambiguous prefix is refused.
    status               Show memory system status
    setup                Initialize/verify memory system
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

# Path resolution: add the skill root (parent of scripts/) to sys.path
# so that `from scripts import PACTMemory` works regardless of cwd.
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from scripts.database import (
    CALLER_FACING_CREATE_FIELDS,
    CALLER_FACING_UPDATE_FIELDS,
    AmbiguousPrefixError,
    PrefixTooShortError,
)
from scripts.memory_api import PACTMemory
from scripts.setup_memory import ensure_initialized, get_setup_status


def _success(result):
    """Print a success JSON envelope to stdout and exit 0."""
    print(json.dumps({"ok": True, "result": result}, indent=2, default=str))
    sys.exit(0)


def _error(error_type, message, exit_code=1, **extra) -> NoReturn:
    """Print an error JSON envelope to stderr and exit with given code.

    Any extra kwargs are merged into the envelope (e.g. allowed_fields).
    """
    envelope = {"ok": False, "error": error_type, "message": message}
    envelope.update(extra)
    print(json.dumps(envelope), file=sys.stderr)
    sys.exit(exit_code)


def _scrub(msg: str) -> str:
    """
    Replace the user's home directory with '~' in an error message.

    Handles both the raw `~` expansion and the realpath form (which may
    differ on macOS where `/Users/foo` resolves through `/System/Volumes/Data`
    or similar). Guards against an empty/unset HOME — if expanduser returns
    the literal '~', no substitution is applied.

    Applied to caller-visible error envelopes so absolute paths don't leak
    into stderr for callers piping JSON envelopes into logs.
    """
    if not msg:
        return msg
    home = os.path.expanduser("~")
    # Empty HOME → expanduser returns the literal '~'. Don't substitute '~'
    # for '~' (no-op) and don't realpath an empty path.
    if home and home != "~":
        real_home = os.path.realpath(home)
        # Order matters: replace the longer/realpath form first so partial
        # overlaps don't leave a trailing suffix.
        if real_home != home:
            msg = msg.replace(real_home, "~")
        msg = msg.replace(home, "~")
    return msg


def cmd_save(args, db_path=None):
    """Handle the 'save' subcommand."""
    if args.stdin:
        raw = sys.stdin.read()
    elif args.json_data:
        raw = args.json_data
    else:
        _error("MISSING_INPUT", "Provide JSON as argument or use --stdin")

    try:
        memory_dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        _error("INVALID_JSON", f"Failed to parse JSON: {exc}")

    if not isinstance(memory_dict, dict):
        _error("INVALID_INPUT", "JSON input must be an object, not a list or scalar")

    memory = PACTMemory(db_path=db_path)
    try:
        memory_id = memory.save(memory_dict)
    except ValueError as exc:
        _error(
            "ValueError",
            f"{_scrub(str(exc))} (Note: 'id' and 'created_at' are accepted "
            f"on save and stripped before validation.)",
            exit_code=2,
            allowed_fields=sorted(CALLER_FACING_CREATE_FIELDS),
        )
    _success({"memory_id": memory_id})


def cmd_search(args, db_path=None):
    """Handle the 'search' subcommand."""
    memory = PACTMemory(db_path=db_path)
    current_file = getattr(args, "current_file", None)
    results = memory.search(
        args.query, current_file=current_file, limit=args.limit, sync_to_claude=False
    )
    _success([r.to_dict() for r in results])


def cmd_list(args, db_path=None):
    """Handle the 'list' subcommand."""
    memory = PACTMemory(db_path=db_path)
    results = memory.list(limit=args.limit)
    _success([r.to_dict() for r in results])


def cmd_get(args, db_path=None):
    """Handle the 'get' subcommand.

    Accepts a full 32-char memory ID or a unique prefix. Ambiguous prefix
    surfaces as an AMBIGUOUS_PREFIX envelope including a capped match list,
    truncation flag, and total match count.
    """
    memory = PACTMemory(db_path=db_path)
    try:
        result = memory.get(args.memory_id)
    except PrefixTooShortError as exc:
        _error(
            "PREFIX_TOO_SHORT",
            str(exc),
            minimum=exc.minimum,
        )
    except AmbiguousPrefixError as exc:
        # Scrub user HOME from each match's `context` snippet so a memory
        # whose context recorded an absolute path doesn't leak it via the
        # disambiguation envelope. Per-site scrub keeps the redaction
        # obvious; do not centralize into `_error`.
        scrubbed_matches = [
            {**m, "context": _scrub(m["context"])} for m in exc.matches
        ]
        _error(
            "AMBIGUOUS_PREFIX",
            str(exc),
            prefix=exc.prefix,
            matches=scrubbed_matches,
            matches_capped=exc.matches_capped,
            total_matches=exc.total_matches,
        )
    if result is None:
        _error("NOT_FOUND", f"Memory '{args.memory_id}' not found")
    _success(result.to_dict())


def cmd_status(args, db_path=None):
    """Handle the 'status' subcommand."""
    memory = PACTMemory(db_path=db_path)
    status = memory.get_status()
    _success(status)


def cmd_setup(args, db_path=None):
    """Handle the 'setup' subcommand."""
    ok = ensure_initialized(db_path=db_path)
    if ok:
        status = get_setup_status()
        _success({
            "status": "ready",
            "message": "Memory system initialized successfully",
            "details": status,
        })
    else:
        _error("SETUP_FAILED", "Memory system initialization failed", exit_code=2)


def cmd_update(args, db_path=None):
    """Handle the 'update' subcommand.

    Accepts a full 32-char memory ID or a unique prefix. Ambiguous prefix
    refuses the update and surfaces an AMBIGUOUS_PREFIX envelope.
    """
    if args.stdin:
        raw = sys.stdin.read()
    elif args.json_data:
        raw = args.json_data
    else:
        _error("MISSING_INPUT", "Provide JSON as argument or use --stdin")

    try:
        updates = json.loads(raw)
    except json.JSONDecodeError as exc:
        _error("INVALID_JSON", f"Failed to parse JSON: {exc}")

    if not isinstance(updates, dict):
        _error("INVALID_INPUT", "JSON input must be an object, not a list or scalar")

    memory = PACTMemory(db_path=db_path)
    try:
        resolved_id = memory.update(args.memory_id, updates, replace=args.replace)
    except PrefixTooShortError as exc:
        # Order: PrefixTooShortError IS a ValueError; catch it before the
        # field-validation ValueError handler below.
        _error("PREFIX_TOO_SHORT", str(exc), minimum=exc.minimum)
    except AmbiguousPrefixError as exc:
        # Scrub user HOME from each match's `context` snippet so a memory
        # whose context recorded an absolute path doesn't leak it via the
        # disambiguation envelope. Per-site scrub keeps the redaction
        # obvious; do not centralize into `_error`.
        scrubbed_matches = [
            {**m, "context": _scrub(m["context"])} for m in exc.matches
        ]
        _error(
            "AMBIGUOUS_PREFIX",
            str(exc),
            prefix=exc.prefix,
            matches=scrubbed_matches,
            matches_capped=exc.matches_capped,
            total_matches=exc.total_matches,
        )
    except ValueError as exc:
        _error(
            "ValueError",
            f"{_scrub(str(exc))} (Note: 'id' and 'created_at' are stripped "
            f"before update validation.)",
            exit_code=2,
            allowed_fields=sorted(CALLER_FACING_UPDATE_FIELDS),
        )
    if resolved_id is None:
        _error("NOT_FOUND", f"Memory '{args.memory_id}' not found")
    _success({"memory_id": resolved_id})


def cmd_delete(args, db_path=None):
    """Handle the 'delete' subcommand.

    Accepts a full 32-char memory ID or a unique prefix. Ambiguous prefix
    refuses the delete and surfaces an AMBIGUOUS_PREFIX envelope.
    """
    memory = PACTMemory(db_path=db_path)
    try:
        resolved_id = memory.delete(args.memory_id)
    except PrefixTooShortError as exc:
        _error("PREFIX_TOO_SHORT", str(exc), minimum=exc.minimum)
    except AmbiguousPrefixError as exc:
        # Scrub user HOME from each match's `context` snippet so a memory
        # whose context recorded an absolute path doesn't leak it via the
        # disambiguation envelope. Per-site scrub keeps the redaction
        # obvious; do not centralize into `_error`.
        scrubbed_matches = [
            {**m, "context": _scrub(m["context"])} for m in exc.matches
        ]
        _error(
            "AMBIGUOUS_PREFIX",
            str(exc),
            prefix=exc.prefix,
            matches=scrubbed_matches,
            matches_capped=exc.matches_capped,
            total_matches=exc.total_matches,
        )
    if resolved_id is None:
        _error("NOT_FOUND", f"Memory '{args.memory_id}' not found")
    _success({"deleted": True, "memory_id": resolved_id})


def _positive_int(value):
    """Argparse type for positive integers. Rejects zero and negative values."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"--limit must be a positive integer, got {ivalue}")
    return ivalue


def build_parser():
    """Build the argparse parser with all subcommands."""
    # Shared parent parser for the hidden --db-path flag.
    # Using a parent parser lets --db-path appear after any subcommand.
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--db-path",
        help=argparse.SUPPRESS,  # Hidden flag for testing
    )

    parser = argparse.ArgumentParser(
        prog="pact-memory",
        description="PACT Memory CLI — persistent memory for PACT agents",
    )

    subparsers = parser.add_subparsers(dest="command")

    # save
    save_parser = subparsers.add_parser(
        "save", help="Save a memory object", parents=[parent]
    )
    save_parser.add_argument("json_data", nargs="?", help="JSON memory object")
    save_parser.add_argument(
        "--stdin", action="store_true", help="Read JSON from stdin"
    )

    # search
    search_parser = subparsers.add_parser(
        "search", help="Search memories", parents=[parent]
    )
    search_parser.add_argument("query", help="Search query text")
    search_parser.add_argument(
        "--limit", type=_positive_int, default=5, help="Max results (default: 5)"
    )
    search_parser.add_argument(
        "--current-file", help="Current file path for graph-enhanced relevance boosting"
    )

    # list
    list_parser = subparsers.add_parser(
        "list", help="List recent memories", parents=[parent]
    )
    list_parser.add_argument(
        "--limit", type=_positive_int, default=20, help="Max results (default: 20)"
    )

    # get
    get_parser = subparsers.add_parser(
        "get",
        help="Get a memory by full ID or unique prefix (>= 7 chars)",
        description=(
            "Retrieve a memory by its full 32-char ID or a unique prefix of "
            "at least 7 characters. A unique prefix returns the matching "
            "memory; an ambiguous prefix returns an AMBIGUOUS_PREFIX error "
            "with a capped list of matching IDs (matches_capped/"
            "total_matches fields indicate when the cap was applied); "
            "a prefix shorter "
            "than 7 characters returns a PREFIX_TOO_SHORT error; no match "
            "returns NOT_FOUND. Prefix is case-insensitive."
        ),
        parents=[parent],
    )
    get_parser.add_argument(
        "memory_id",
        help="Full 32-char memory ID, or a unique prefix of >= 7 characters",
    )

    # status
    subparsers.add_parser(
        "status", help="Show memory system status", parents=[parent]
    )

    # setup
    subparsers.add_parser(
        "setup", help="Initialize the memory system", parents=[parent]
    )

    # update
    update_parser = subparsers.add_parser(
        "update",
        help="Update a memory by full ID or unique prefix (>= 7 chars)",
        description=(
            "Update an existing memory by its full 32-char ID or a unique "
            "prefix of at least 7 characters. An ambiguous prefix is refused "
            "(AMBIGUOUS_PREFIX error with a capped match list); a prefix "
            "shorter than 7 characters returns PREFIX_TOO_SHORT; no match "
            "returns NOT_FOUND. Prefix is case-insensitive."
        ),
        parents=[parent],
    )
    update_parser.add_argument(
        "memory_id",
        help="Full 32-char memory ID, or a unique prefix of >= 7 characters",
    )
    update_parser.add_argument("json_data", nargs="?", help="JSON with fields to update")
    update_parser.add_argument(
        "--stdin", action="store_true", help="Read JSON from stdin"
    )
    update_parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Replace list-valued fields wholesale instead of merging "
            "additively (default: additive merge with content-hash dedup). "
            "Use when you intentionally want to remove items from a list."
        ),
    )

    # delete
    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete a memory by full ID or unique prefix (>= 7 chars)",
        description=(
            "Delete a memory by its full 32-char ID or a unique prefix of "
            "at least 7 characters. An ambiguous prefix is refused "
            "(AMBIGUOUS_PREFIX error with a capped match list); a prefix "
            "shorter than 7 characters returns PREFIX_TOO_SHORT; no match "
            "returns NOT_FOUND. Prefix is case-insensitive."
        ),
        parents=[parent],
    )
    delete_parser.add_argument(
        "memory_id",
        help="Full 32-char memory ID, or a unique prefix of >= 7 characters",
    )

    return parser


# Dispatch table mapping command names to handler functions
_COMMANDS = {
    "save": cmd_save,
    "search": cmd_search,
    "list": cmd_list,
    "get": cmd_get,
    "status": cmd_status,
    "setup": cmd_setup,
    "update": cmd_update,
    "delete": cmd_delete,
}


def main(argv=None):
    """
    CLI entry point. Parses arguments and dispatches to the appropriate
    command handler.

    Args:
        argv: Optional argument list (defaults to sys.argv[1:]).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        sys.exit(1)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        _error("UNKNOWN_COMMAND", f"Unknown command: {args.command}")

    db_path = Path(args.db_path) if args.db_path else None

    try:
        handler(args, db_path=db_path)
    except SystemExit:
        raise  # Let _success/_error exits propagate
    except Exception as exc:
        # Scrub the user's home directory (both the literal expansion and
        # the realpath form) from the message so absolute paths
        # (e.g. ~/.claude/pact-memory/...) don't leak into stderr for
        # callers piping the JSON envelope into logs.
        _error("SYSTEM_ERROR", _scrub(str(exc)), exit_code=2)


if __name__ == "__main__":
    main()
