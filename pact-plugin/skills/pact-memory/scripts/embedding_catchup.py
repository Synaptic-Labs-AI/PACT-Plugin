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

from __future__ import annotations

import logging
import platform
import struct
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any, List, Optional

# Configure logging
logger = logging.getLogger(__name__)

from .config import read_only_scope
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
                # Count free + speculative + inactive pages as available.
                # Inactive pages are reclaimable by the OS on demand.
                lines = result.stdout.strip().split("\n")
                page_size = 4096  # Default page size on macOS
                free_pages = 0
                speculative_pages = 0
                inactive_pages = 0

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
                    elif "Pages inactive:" in line:
                        try:
                            inactive_pages = int(line.split(":")[1].strip().rstrip("."))
                        except (ValueError, IndexError):
                            pass

                free_bytes = (free_pages + speculative_pages + inactive_pages) * page_size
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


class UnembeddedResult(list):
    """The IDs needing embedding, and WHETHER THE QUESTION WAS ANSWERABLE.

    This is a `list`, so every existing use is unchanged: iteration, `len`,
    truthiness, indexing, and a test that patches this function with a plain
    `[]` all keep working. `.reason` is the channel a bare list could not carry.

    THE DEFECT IT CLOSES. Three of this function's four exits return no IDs
    because it COULD NOT LOOK, and one returns none because there are none. All
    four used to be an indistinguishable `[]`, and the caller reads empty as
    nothing-to-do -- so in the degraded state, which is the exact condition the
    recovery sweep exists for, the sweep reported nothing to do precisely when
    it could do nothing.

    SAME SHAPE AS `SyncResult`, DELIBERATELY, AND NOT THE OTHER SIBLING. The
    `_store_embedding` convention returns None-or-value, which here would mean
    None-or-list and would break every caller that iterates. Preserving what
    callers already rely on and adding discrimination beside it is the whole
    pattern; changing the shape they rely on is not.
    """

    # ANSWERED is the only reason for which an empty result means "nothing to do".
    ANSWERED = "answered"
    NO_EXTENSIONS = "extensions_unavailable"
    NO_VECTOR_TABLE = "vector_table_missing"
    QUERY_FAILED = "query_failed"

    def __init__(self, ids=(), reason: str = ANSWERED) -> None:
        super().__init__(ids)
        self.reason = reason

    @property
    def answerable(self) -> bool:
        """True when the emptiness of this list is INFORMATION rather than a gap."""
        return self.reason == self.ANSWERED

    def __repr__(self) -> str:
        return f"UnembeddedResult({list(self)!r}, reason={self.reason!r})"


def get_unembedded_memories(
    project_id: Optional[str] = None,
    limit: int = 50
) -> "UnembeddedResult":
    """
    Find memory IDs that exist in memories table but not in vec_memories.

    These are memories that were saved but embedding generation failed or
    was not available at save time.

    Args:
        project_id: Optional project filter.
        limit: Maximum number of IDs to return.

    Returns:
        An `UnembeddedResult` -- a list of memory_ids that need embedding, plus
        `.reason` saying whether the question could be answered at all.

        AN EMPTY RESULT DOES NOT MEAN THERE IS NOTHING TO DO. It means that
        only when `.reason` is `answered`. The other three reasons report that
        this function could not look: the vector extension is unavailable, the
        vector table does not exist, or the query raised. Read `.answerable`
        before reading emptiness as an answer.
    """
    # Check if extensions are available - no point checking if we can't embed
    if not SQLITE_EXTENSIONS_ENABLED:
        logger.debug("SQLite extensions unavailable, skipping unembedded check")
        return UnembeddedResult(reason=UnembeddedResult.NO_EXTENSIONS)

    try:
        # ON AN EXISTING STORE THIS IS A READ, AND IT MUST WRITE NOTHING. The
        # integrity check runs only after this query finds work, so this
        # query is what a corrupt store meets first: under the read-only
        # scope `ensure_initialized` creates no vector table and runs no
        # migration, and the first embedding's own connection creates the
        # table (`embed_single_memory`). A store with no `memories` table yet
        # is a first use, and is still initialised here as before.
        with db_connection() as conn:
            has_schema = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
            ).fetchone() is not None
            with read_only_scope() if has_schema else nullcontext():
                ensure_initialized(conn)

            # Check if vec_memories table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
            )
            has_vector_table = cursor.fetchone() is not None
            try:
                import sqlite_vec
            except ImportError:
                sqlite_vec = None
            if sqlite_vec is None and not has_vector_table:
                # No table, and no extension to make one with.
                logger.debug("vec_memories table doesn't exist, skipping")
                return UnembeddedResult(
                    reason=UnembeddedResult.NO_VECTOR_TABLE
                )

            # Find memories without embeddings
            # THE JOIN MATCHES THE PARTITION, NOT JUST THE ID. A vector stored
            # under a DIFFERENT project is not this row's embedding: a re-key
            # that could not move it (embedding fault, or a process that
            # cannot open the vector table) leaves the old partition
            # populated, and an id-only join then reports the memory as
            # embedded forever. `IS` is null-safe on purpose so a legacy
            # NULL-project memory still joins its own NULL partition instead
            # of being reported unembedded on every sweep.
            query = """
                SELECT m.id
                FROM memories m
                LEFT JOIN vec_memories v
                    ON m.id = v.memory_id AND v.project_id IS m.project_id
                WHERE v.memory_id IS NULL
            """
            if has_vector_table:
                # A vec0 virtual table: the read needs the extension that
                # `_init_vector_table` loads on a writing connection.
                if sqlite_vec is not None:
                    conn.enable_load_extension(True)
                    sqlite_vec.load(conn)
            else:
                # No vector table yet: every memory is unembedded.
                query = "SELECT m.id FROM memories m WHERE 1"
            params: List[Any] = []

            if project_id is not None:
                query += " AND m.project_id = ?"
                params.append(project_id)

            query += " ORDER BY m.created_at ASC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            # The ONLY exit whose emptiness is an answer.
            return UnembeddedResult(row[0] for row in rows)

    except Exception as e:
        logger.debug(f"Error getting unembedded memories: {e}")
        return UnembeddedResult(reason=UnembeddedResult.QUERY_FAILED)


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
                logger.debug(f"Memory {memory_id!r} not found")
                return False

            # Generate text for embedding
            text = generate_embedding_text(memory_dict)
            if not text:
                logger.debug(f"No text to embed for memory {memory_id!r}")
                return False

            # Generate embedding
            embedding = generate_embedding(text)
            if embedding is None:
                logger.debug(f"Embedding generation failed for {memory_id!r}")
                return False

            # Store embedding
            try:
                conn.enable_load_extension(True)
                import sqlite_vec
                sqlite_vec.load(conn)

                embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
                # DROP A ROW IN ANOTHER PARTITION FIRST. A vec0 primary key is
                # GLOBAL across partitions: a plain INSERT and an OR REPLACE
                # both RAISE `UNIQUE constraint failed` when a row for this
                # memory id exists under a different project (measured), so a
                # replace alone cannot repair a stranded vector — it fails and
                # the sweep reports the memory unembedded forever. DELETE spans
                # partitions, so this is the move.
                conn.execute(
                    "DELETE FROM vec_memories WHERE memory_id = ?",
                    (memory_id,),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO vec_memories (memory_id, project_id, embedding)
                    VALUES (?, ?, ?)
                    """,
                    (memory_id, memory_dict.get("project_id"), embedding_blob)
                )
                conn.commit()

                logger.debug(f"Embedded memory {memory_id!r}")
                return True

            except ImportError:
                logger.debug("sqlite-vec not available")
                return False

    except Exception as e:
        logger.debug(f"Error embedding memory {memory_id!r}: {e}")
        return False


def _store_integrity_fault() -> Optional[str]:
    """Why the bound store must not be written, or None when it may be.

    Runs `PRAGMA quick_check` on a READ-ONLY connection, so the check itself
    writes nothing. A store whose pages are corrupt can still answer the
    pending-work query, and the catch-up's vector inserts would then write
    into it. The check reads the whole file, so it runs only once there is
    work to do, never on the common no-pending path.
    """
    import sqlite3 as stdlib_sqlite3

    from .config import resolve_db_path

    store = resolve_db_path()
    try:
        connection = stdlib_sqlite3.connect(
            Path(store).resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            rows = connection.execute("PRAGMA quick_check").fetchall()
        finally:
            connection.close()
    except stdlib_sqlite3.DatabaseError as exc:
        return f"the store failed its integrity check ({exc}); catch-up skipped"
    if rows != [("ok",)]:
        first = rows[0][0] if rows and rows[0] else "unknown"
        return f"the store failed its integrity check ({first}); catch-up skipped"
    return None


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
        "error": None,
        # None means the outstanding-work question WAS answered. A string names
        # why it could not be, so a caller can tell "no work" from "no answer".
        "unembedded_unknown": None,
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
    # READ THE REASON BEFORE READING THE EMPTINESS. An unanswerable result is
    # empty for a reason that has nothing to do with how much work is
    # outstanding, so reporting it as a clean no-op is the defect: it says
    # "nothing to do" in exactly the state where the sweep can do nothing.
    #
    # `getattr` WITH A True DEFAULT, NOT A BARE ATTRIBUTE READ. A PLAIN list
    # from any other producer keeps its historical meaning -- these ids, and
    # the question was answered -- so nothing that already returns a list
    # changes behaviour or has to learn a new type. Reading the attribute
    # directly made this function reject a plain list, which broke callers the
    # channel was supposed to leave untouched. Preserving what callers already
    # rely on is the point of the shape; a read that only accepts the new type
    # abandons it.
    if not getattr(unembedded, "answerable", True):
        result["unembedded_unknown"] = unembedded.reason
        return result
    if not unembedded:
        return result

    # NO WRITE INTO A CORRUPT STORE. The pending query above can succeed on
    # a store with corrupt pages, and each embedding below inserts vectors.
    fault = _store_integrity_fault()
    if fault is not None:
        result["failed"] = True
        result["error"] = fault
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
