"""
PACT Memory Setup and Initialization

Location: pact-plugin/skills/pact-memory/scripts/setup_memory.py

Handles auto-initialization of the PACT Memory system including:
- Database schema creation
- Dependency checking

Used by:
- Hooks: session_init.py for startup initialization
- CLI: Manual setup commands
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

from .config import PACT_MEMORY_DIR

# Configure logging
logger = logging.getLogger(__name__)


def ensure_directories() -> None:
    """Create required directories if they don't exist."""
    PACT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def check_dependencies() -> Dict[str, Any]:
    """
    Check the status of required dependencies.

    Returns:
        Dictionary with dependency status:
        - sqlite_vec: bool - Is sqlite-vec installed?
        - model2vec: bool - Is model2vec installed?
    """
    status: Dict[str, Any] = {
        "sqlite_vec": False,
        "model2vec": False,
    }

    # Check sqlite-vec (for vector storage)
    try:
        import sqlite_vec
        status["sqlite_vec"] = True
    except ImportError:
        pass

    # Check model2vec (embedding backend)
    try:
        from model2vec import StaticModel
        status["model2vec"] = True
    except ImportError:
        pass

    return status


def ensure_initialized() -> bool:
    """
    Ensure the memory system is fully initialized.

    Performs all setup tasks:
    1. Creates required directories
    2. Initializes database schema

    Returns:
        True if system is ready for use.
    """
    # Create directories
    ensure_directories()

    # Initialize database
    try:
        from .database import initialize_database
        initialize_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return False

    return True


def get_setup_status() -> Dict[str, Any]:
    """
    Get comprehensive setup status.

    Returns:
        Dictionary with full status information.
    """
    deps = check_dependencies()

    return {
        "initialized": PACT_MEMORY_DIR.exists(),
        "dependencies": deps,
        "can_use_semantic_search": deps["model2vec"] and deps["sqlite_vec"],
        "paths": {
            "memory_dir": str(PACT_MEMORY_DIR),
        },
        "recommendations": _get_recommendations(deps)
    }


def _get_recommendations(deps: Dict[str, Any]) -> list:
    """Generate setup recommendations based on current status."""
    recommendations = []

    if not deps["sqlite_vec"]:
        recommendations.append(
            "Install sqlite-vec for vector search: pip install sqlite-vec"
        )

    if not deps["model2vec"]:
        recommendations.append(
            "Install model2vec for embeddings: pip install model2vec"
        )

    return recommendations


def print_setup_status() -> None:
    """Print formatted setup status to console."""
    status = get_setup_status()

    print("\n=== PACT Memory Setup Status ===\n")

    print("Initialization:")
    print(f"  Memory directory: {'OK' if status['initialized'] else 'Missing'}")
    print(f"  Path: {status['paths']['memory_dir']}")

    print("\nDependencies:")
    deps = status["dependencies"]
    print(f"  sqlite-vec: {'Installed' if deps['sqlite_vec'] else 'Not installed'}")
    print(f"  model2vec:  {'Installed' if deps['model2vec'] else 'Not installed'}")

    print("\nCapabilities:")
    print(f"  Semantic search: {'Available' if status['can_use_semantic_search'] else 'Unavailable'}")
    print(f"  Keyword search:  Available")
    print(f"  Graph search:    Available")

    if status["recommendations"]:
        print("\nRecommendations:")
        for rec in status["recommendations"]:
            print(f"  - {rec}")

    print()


def setup_cli() -> None:
    """
    CLI entry point for setup commands.

    Usage:
        python -m pact_memory.scripts.setup_memory [command]

    Commands:
        status  - Show setup status
        init    - Initialize database and directories
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="PACT Memory setup utility"
    )
    parser.add_argument(
        "command",
        choices=["status", "init"],
        default="status",
        nargs="?",
        help="Command to run"
    )

    args = parser.parse_args()

    if args.command == "status":
        print_setup_status()

    elif args.command == "init":
        print("Initializing PACT Memory...")
        if ensure_initialized():
            print("Initialization complete!")
        else:
            print("Initialization failed. Check logs for details.")
            sys.exit(1)


if __name__ == "__main__":
    setup_cli()
