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
    list [--limit N]     List recent memories (default: 10)
    get <id>             Retrieve a specific memory by ID
    status               Show memory system status
    setup                Initialize/verify memory system
"""

import argparse
import json
import sys
from pathlib import Path

# Path resolution: add the skill root (parent of scripts/) to sys.path
# so that `from scripts import PACTMemory` works regardless of cwd.
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from scripts.memory_api import PACTMemory
from scripts.setup_memory import ensure_initialized, get_setup_status


def _success(result):
    """Print a success JSON envelope to stdout and exit 0."""
    print(json.dumps({"ok": True, "result": result}, indent=2, default=str))
    sys.exit(0)


def _error(error_type, message, exit_code=1):
    """Print an error JSON envelope to stderr and exit with given code."""
    print(
        json.dumps({"ok": False, "error": error_type, "message": message}),
        file=sys.stderr,
    )
    sys.exit(exit_code)


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
    memory_id = memory.save(memory_dict)
    _success({"memory_id": memory_id})


def cmd_search(args, db_path=None):
    """Handle the 'search' subcommand."""
    memory = PACTMemory(db_path=db_path)
    results = memory.search(args.query, limit=args.limit, sync_to_claude=False)
    _success([r.to_dict() for r in results])


def cmd_list(args, db_path=None):
    """Handle the 'list' subcommand."""
    memory = PACTMemory(db_path=db_path)
    results = memory.list(limit=args.limit)
    _success([r.to_dict() for r in results])


def cmd_get(args, db_path=None):
    """Handle the 'get' subcommand."""
    memory = PACTMemory(db_path=db_path)
    result = memory.get(args.memory_id)
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
        "--limit", type=int, default=5, help="Max results (default: 5)"
    )

    # list
    list_parser = subparsers.add_parser(
        "list", help="List recent memories", parents=[parent]
    )
    list_parser.add_argument(
        "--limit", type=int, default=10, help="Max results (default: 10)"
    )

    # get
    get_parser = subparsers.add_parser(
        "get", help="Get a memory by ID", parents=[parent]
    )
    get_parser.add_argument("memory_id", help="Memory ID to retrieve")

    # status
    subparsers.add_parser(
        "status", help="Show memory system status", parents=[parent]
    )

    # setup
    subparsers.add_parser(
        "setup", help="Initialize the memory system", parents=[parent]
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
        _error("SYSTEM_ERROR", str(exc), exit_code=2)


if __name__ == "__main__":
    main()
