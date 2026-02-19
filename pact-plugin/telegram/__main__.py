#!/usr/bin/env python3
"""
Location: pact-plugin/telegram/__main__.py
Summary: Entry point for running the pact-telegram MCP server via `python -m telegram`.
Used by: Claude Code MCP registration (.mcp.json) to spawn the server process.

Performs dependency checking before starting the MCP server. If required
dependencies (mcp, httpx) are missing, prints a user-friendly error and
exits with code 1. Otherwise, imports and starts the MCP server via stdio
transport.

Usage:
    python -m telegram
    # Or from plugin root:
    python -m pact-plugin.telegram
"""

import sys


def main() -> None:
    """
    Check dependencies and start the MCP server.

    Validates that required packages (mcp, httpx) are installed before
    attempting to start the server. This avoids confusing ImportError
    tracebacks for users who haven't run the setup command yet.
    """
    from telegram.deps import check_dependencies

    missing = check_dependencies()
    if missing:
        print(
            f"pact-telegram: Missing required dependencies: {', '.join(missing)}\n"
            f"Run the setup command:  /PACT:telegram-setup\n"
            f"Or install manually:    pip install {' '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    from telegram.server import create_server

    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
