"""
PACT Memory Lazy Initialization Module

Location: pact-plugin/skills/pact-memory/scripts/memory_init.py

Summary: Provides lazy initialization for the PACT memory system. Instead of
running at session start (which penalizes non-memory users), initialization
happens on first actual memory operation.

Handles:
1. Auto-installing dependencies (pysqlite3, sqlite-vec, model2vec)
2. Migrating embeddings when dimension changes (e.g., backend switch)
3. Catch-up embedding for memories that failed embedding at save time

Used by:
- memory_api.py: Calls ensure_memory_ready() before database operations
- Can be called directly for explicit initialization

Thread-safety: Uses threading.Lock for the session-scoped initialization flag.
"""

import logging
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

# Session-scoped initialization state
# Two state mechanisms exist:
# 1. _initialized (in-memory): Controls overall lazy init, reset per process
# 2. Session marker file (in _get_embedding_attempted_path): Controls maybe_embed_pending()
#    specifically, persists across process restarts within the same session
_init_lock = threading.Lock()
_initialized = False


def check_and_install_dependencies() -> dict:
    """
    Check for pact-memory dependencies and auto-install if missing.

    Returns:
        dict with status, installed, and failed packages
    """
    packages = [
        ('pysqlite3', 'pysqlite3'),  # CRITICAL: enables SQLite extension loading
        ('sqlite-vec', 'sqlite_vec'),
        ('model2vec', 'model2vec'),  # Embedding backend
    ]

    missing = []
    installed = []
    failed = []

    # Check what's missing
    for pip_name, import_name in packages:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return {'status': 'ok', 'installed': [], 'failed': []}

    # Attempt installation
    for pkg in missing:
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', pkg],
                capture_output=True,
                timeout=60
            )
            if result.returncode == 0:
                installed.append(pkg)
            else:
                failed.append(pkg)
        except subprocess.TimeoutExpired:
            failed.append(f"{pkg} (timeout)")
        except Exception as e:
            failed.append(f"{pkg} ({str(e)[:20]})")

    status = 'ok' if not failed else ('partial' if installed else 'failed')
    return {'status': status, 'installed': installed, 'failed': failed}


def maybe_migrate_embeddings() -> dict:
    """
    Check if embeddings need migration due to dimension change.

    When switching embedding backends, dimensions may change (e.g., 384->256).
    This function:
    1. Detects dimension mismatch
    2. Drops the old vector table
    3. Re-embeds all existing memories

    Returns:
        dict with status and message
    """
    result = {"status": "ok", "message": None}

    try:
        # Import required modules - we're inside the scripts package now
        try:
            import pysqlite3 as sqlite3
            import sqlite_vec
            from .database import get_connection
            from .embeddings import get_embedding_service, generate_embedding_text, EMBEDDING_DIM
        except ImportError:
            # Distinct status to differentiate from "nothing to migrate"
            return {"status": "skipped_deps", "message": "Dependencies not available"}

        # Get expected dimension
        expected_dim = EMBEDDING_DIM

        # Connect to database
        conn = get_connection()
        sqlite_vec.load(conn)

        # Check if vec_memories table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
        )
        if cursor.fetchone() is None:
            conn.close()
            return result  # No table, nothing to migrate

        # Check actual dimension by examining an embedding
        try:
            row = conn.execute("SELECT embedding FROM vec_memories LIMIT 1").fetchone()
            if row is None:
                conn.close()
                return result  # Empty table, nothing to migrate

            actual_dim = len(row[0]) // 4  # 4 bytes per float
            if actual_dim == expected_dim:
                conn.close()
                return result  # Dimensions match, no migration needed

        except Exception:
            conn.close()
            return result

        # Dimension mismatch detected - need to migrate
        result["status"] = "migrating"
        result["message"] = f"Migrating embeddings: {actual_dim}-dim -> {expected_dim}-dim"

        # Drop old table
        conn.execute("DROP TABLE IF EXISTS vec_memories")
        conn.commit()

        # Recreate with new dimension
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                memory_id TEXT PRIMARY KEY,
                project_id TEXT PARTITION KEY,
                embedding float[{expected_dim}]
            )
        """)
        conn.commit()

        # Re-embed all memories using SELECT * to capture all fields (including CT fields)
        service = get_embedding_service()
        rows = conn.execute("SELECT * FROM memories").fetchall()

        success = 0
        for row in rows:
            try:
                memory_dict = dict(row)
                mem_id = memory_dict["id"]
                embed_text = generate_embedding_text(memory_dict)
                embedding = service.generate(embed_text)

                if embedding:
                    embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
                    conn.execute(
                        "INSERT OR REPLACE INTO vec_memories(memory_id, project_id, embedding) VALUES (?, ?, ?)",
                        (mem_id, memory_dict.get("project_id"), embedding_blob)
                    )
                    success += 1
            except Exception:
                continue

        conn.commit()
        conn.close()

        result["status"] = "ok"
        result["message"] = f"Migrated {success}/{len(rows)} embeddings to {expected_dim}-dim"
        return result

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)[:50]
        return result


def _get_embedding_attempted_path() -> Path:
    """Get path to session-scoped embedding attempt marker file."""
    session_id = os.environ.get("CLAUDE_SESSION_ID", "unknown")
    return Path("/tmp") / f"pact_embedding_attempted_{session_id}"


def maybe_embed_pending() -> dict:
    """
    Check for and process unembedded memories.

    This is a catch-up mechanism for embeddings that failed at save time.

    Features:
    - Session-scoped: Only attempts once per session
    - RAM check: Skips if available RAM is below threshold
    - Fail-fast: Stops on first failure (no retry loops)

    Returns:
        dict with status info (embedded count, skipped reason, etc.)
    """
    result = {"status": "skipped", "message": None}

    # Check if we've already attempted this session
    marker_path = _get_embedding_attempted_path()
    if marker_path.exists():
        result["message"] = "Already attempted this session"
        return result

    # Mark as attempted (do this first to prevent retry on errors)
    try:
        marker_path.touch()
    except OSError:
        result["message"] = "Could not create session marker"
        return result

    try:
        # Import the embedding catch-up function from sibling module
        from .embedding_catchup import embed_pending_memories

        # Process pending embeddings
        embed_result = embed_pending_memories(min_ram_mb=500.0, limit=20)

        if embed_result.get("skipped_ram"):
            result["status"] = "skipped_ram"
            result["message"] = "Low RAM, skipping"
            return result

        processed = embed_result.get("processed", 0)
        if processed > 0:
            result["status"] = "ok"
            result["message"] = f"Embedded {processed} pending memories"
            return result

        if embed_result.get("failed"):
            result["status"] = "partial"
            result["message"] = embed_result.get("error", "Unknown error")
            return result

        # No pending memories to process
        result["status"] = "ok"
        result["message"] = None
        return result

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)[:50]
        return result


def ensure_memory_ready() -> dict:
    """
    Ensure the memory system is fully initialized.

    This is the main entry point for lazy initialization. It runs once per
    session, performing:
    1. Dependency installation (if needed)
    2. Embedding migration (if dimension changed)
    3. Pending embedding catch-up (if any)

    Thread-safe: Multiple calls will only run initialization once.

    Returns:
        dict with initialization results:
            - already_initialized: bool - True if this was a no-op
            - deps: dict - Dependency installation result
            - migration: dict - Migration result
            - embedding: dict - Embedding catch-up result
    """
    global _initialized

    # Fast path: already initialized this session
    if _initialized:
        return {"already_initialized": True}

    with _init_lock:
        # Double-check after acquiring lock
        if _initialized:
            return {"already_initialized": True}

        result = {
            "already_initialized": False,
            "deps": None,
            "migration": None,
            "embedding": None,
        }

        # 1. Check and install dependencies
        deps_result = check_and_install_dependencies()
        result["deps"] = deps_result

        if deps_result.get("installed"):
            logger.info(f"Installed dependencies: {', '.join(deps_result['installed'])}")
        if deps_result.get("failed"):
            logger.warning(f"Failed to install: {', '.join(deps_result['failed'])}")

        # 2. Migrate embeddings if dimension changed
        migration_result = maybe_migrate_embeddings()
        result["migration"] = migration_result

        if migration_result.get("message"):
            logger.info(f"Migration: {migration_result['message']}")

        # 3. Process any unembedded memories
        embedding_result = maybe_embed_pending()
        result["embedding"] = embedding_result

        if embedding_result.get("message") and embedding_result.get("status") == "ok":
            logger.info(f"Embedding catch-up: {embedding_result['message']}")

        # Mark as initialized
        _initialized = True
        logger.debug("Memory system initialized")

        return result


def reset_initialization() -> None:
    """
    Reset the initialization state.

    Useful for testing or when forcing re-initialization.
    Clears both the in-memory flag and the session marker file.
    """
    global _initialized
    with _init_lock:
        _initialized = False
        # Also clear the session marker file so maybe_embed_pending() can run again
        marker_path = _get_embedding_attempted_path()
        marker_path.unlink(missing_ok=True)


def is_initialized() -> bool:
    """
    Check if the memory system has been initialized this session.

    Returns:
        True if ensure_memory_ready() has completed, False otherwise.
    """
    return _initialized
