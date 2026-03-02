"""
Embedding Catch-up Module

Location: pact-plugin/skills/pact-memory/scripts/embedding_catchup.py

Summary: Handles background embedding recovery for memories that failed to
embed at save time. Provides functions to find unembedded memories and
process them serially with RAM pressure awareness.

Used by:
- session_init.py: Calls embed_pending_memories() on session start
- memory_api.py: May use for manual catch-up operations
"""

import logging
import platform
import struct
import subprocess
from pathlib import Path
from typing import Any, List, Optional

# Configure logging
logger = logging.getLogger(__name__)

# Conditional imports for database operations
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .database import (
    db_connection,
    ensure_initialized,
    get_memory,
    SQLITE_EXTENSIONS_ENABLED,
)
from .embeddings import (
    generate_embedding,
    generate_embedding_text,
    MIN_CATCHUP_RAM_MB,
)


def get_available_ram_mb() -> float:
    """
    Get available RAM in MB.

    Uses psutil if available, otherwise falls back to platform-specific methods.

    Returns:
        Available RAM in megabytes, or -1.0 if unable to determine.
    """
    # Try psutil first (most reliable)
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 * 1024)
    except ImportError:
        pass

    # Fallback for macOS: use vm_stat
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse vm_stat output
                lines = result.stdout.strip().split("\n")
                page_size = 4096  # Default page size on macOS
                free_pages = 0
                speculative_pages = 0

                for line in lines:
                    if "page size of" in line:
                        # Extract page size from first line
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == "of":
                                try:
                                    page_size = int(parts[i + 1])
                                except (IndexError, ValueError):
                                    pass
                    elif "Pages free:" in line:
                        try:
                            free_pages = int(line.split(":")[1].strip().rstrip("."))
                        except (ValueError, IndexError):
                            pass
                    elif "Pages speculative:" in line:
                        try:
                            speculative_pages = int(line.split(":")[1].strip().rstrip("."))
                        except (ValueError, IndexError):
                            pass

                free_bytes = (free_pages + speculative_pages) * page_size
                return free_bytes / (1024 * 1024)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback for Linux: read /proc/meminfo
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        # Value is in kB
                        kb = int(line.split()[1])
                        return kb / 1024
        except (IOError, ValueError, IndexError):
            pass

    # Unable to determine
    return -1.0


def get_unembedded_memories(
    project_id: Optional[str] = None,
    limit: int = 50
) -> List[str]:
    """
    Find memory IDs that exist in memories table but not in vec_memories.

    These are memories that were saved but embedding generation failed or
    was not available at save time.

    Args:
        project_id: Optional project filter.
        limit: Maximum number of IDs to return.

    Returns:
        List of memory_ids that need embedding.
    """
    # Check if extensions are available - no point checking if we can't embed
    if not SQLITE_EXTENSIONS_ENABLED:
        logger.debug("SQLite extensions unavailable, skipping unembedded check")
        return []

    try:
        with db_connection() as conn:
            ensure_initialized(conn)

            # Check if vec_memories table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
            )
            if cursor.fetchone() is None:
                logger.debug("vec_memories table doesn't exist, skipping")
                return []

            # Find memories without embeddings
            query = """
                SELECT m.id
                FROM memories m
                LEFT JOIN vec_memories v ON m.id = v.memory_id
                WHERE v.memory_id IS NULL
            """
            params: List[Any] = []

            if project_id is not None:
                query += " AND m.project_id = ?"
                params.append(project_id)

            query += " ORDER BY m.created_at ASC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            return [row[0] for row in rows]

    except Exception as e:
        logger.debug(f"Error getting unembedded memories: {e}")
        return []


def embed_single_memory(memory_id: str) -> bool:
    """
    Generate and store embedding for a single memory by ID.

    Loads the memory from DB, generates embedding, stores in vec_memories.

    Args:
        memory_id: The memory ID to embed.

    Returns:
        True if successful, False if failed.
    """
    if not SQLITE_EXTENSIONS_ENABLED:
        return False

    try:
        with db_connection() as conn:
            ensure_initialized(conn)

            # Load the memory
            memory_dict = get_memory(conn, memory_id)
            if memory_dict is None:
                logger.debug(f"Memory {memory_id} not found")
                return False

            # Generate text for embedding
            text = generate_embedding_text(memory_dict)
            if not text:
                logger.debug(f"No text to embed for memory {memory_id}")
                return False

            # Generate embedding
            embedding = generate_embedding(text)
            if embedding is None:
                logger.debug(f"Embedding generation failed for {memory_id}")
                return False

            # Store embedding
            try:
                conn.enable_load_extension(True)
                import sqlite_vec
                sqlite_vec.load(conn)

                embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO vec_memories (memory_id, project_id, embedding)
                    VALUES (?, ?, ?)
                    """,
                    (memory_id, memory_dict.get("project_id"), embedding_blob)
                )
                conn.commit()

                logger.debug(f"Embedded memory {memory_id}")
                return True

            except ImportError:
                logger.debug("sqlite-vec not available")
                return False

    except Exception as e:
        logger.debug(f"Error embedding memory {memory_id}: {e}")
        return False


def embed_pending_memories(
    project_id: Optional[str] = None,
    limit: int = 20,
    min_ram_mb: float = MIN_CATCHUP_RAM_MB
) -> dict:
    """
    Process pending embeddings serially. Stops on first failure.

    This is a catch-up mechanism for memories that failed embedding at save time.
    Processes one at a time to reduce memory pressure.

    Args:
        project_id: Optional project filter.
        limit: Max memories to process.
        min_ram_mb: Minimum free RAM required to proceed (default MIN_CATCHUP_RAM_MB).

    Returns:
        dict with keys:
            - processed: int - Number successfully embedded
            - failed: bool - Whether we stopped due to a failure
            - skipped_ram: bool - Whether we skipped due to low RAM
            - error: Optional[str] - Error message if failed
    """
    result = {
        "processed": 0,
        "failed": False,
        "skipped_ram": False,
        "error": None
    }

    # Check RAM before starting
    available_ram = get_available_ram_mb()
    if available_ram >= 0 and available_ram < min_ram_mb:
        logger.debug(
            f"Skipping embedding catch-up: {available_ram:.0f}MB available, "
            f"{min_ram_mb:.0f}MB required"
        )
        result["skipped_ram"] = True
        return result

    # Get unembedded memories
    unembedded = get_unembedded_memories(project_id=project_id, limit=limit)
    if not unembedded:
        return result

    logger.debug(f"Found {len(unembedded)} unembedded memories to process")

    # Process serially, stop on first failure
    for memory_id in unembedded:
        try:
            success = embed_single_memory(memory_id)
            if success:
                result["processed"] += 1
            else:
                result["failed"] = True
                result["error"] = f"Failed to embed memory {memory_id}"
                break
        except Exception as e:
            result["failed"] = True
            result["error"] = str(e)[:100]
            break

    return result
