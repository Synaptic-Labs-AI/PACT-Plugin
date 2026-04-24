"""
PACT Memory API

Location: pact-plugin/skills/pact-memory/scripts/memory_api.py

High-level API for the PACT Memory skill providing a clean interface
for saving, searching, and managing memories.

This is the primary entry point for agents and hooks to interact
with the memory system.

Used by:
- SKILL.md: Documents API usage for skill invocation
- Agents: Direct memory operations during PACT phases

Note: Memory initialization is lazy-loaded on first use via memory_init.py,
eliminating startup cost for non-memory users.
"""

import json
import logging
import os
import struct
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Use the same sqlite3 module as database.py for type consistency
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .database import (
    db_connection,
    create_memory,
    get_memory,
    update_memory,
    delete_memory,
    list_memories,
    ensure_initialized,
    get_db_path,
    generate_id,
    resolve_memory_id_prefix,
    AmbiguousPrefixError,
    PrefixTooShortError,
    MEMORY_ID_LENGTH,
    SQLITE_EXTENSIONS_ENABLED
)
from .embeddings import (
    generate_embedding,
    generate_embedding_text,
    check_embedding_availability
)
from .graph import (
    track_file,
    link_memory_to_paths,
    get_files_for_memory,
    get_memories_for_files
)
from .models import MemoryObject, memory_from_db_row
from .search import (
    graph_enhanced_search,
    semantic_search,
    search_by_file,
    get_search_capabilities
)
from .working_memory import (
    sync_to_claude_md,
    sync_retrieved_to_claude_md,
    WORKING_MEMORY_HEADER,
    WORKING_MEMORY_COMMENT,
    MAX_WORKING_MEMORIES,
    RETRIEVED_CONTEXT_HEADER,
    RETRIEVED_CONTEXT_COMMENT,
    MAX_RETRIEVED_MEMORIES,
    _get_claude_md_path,
    _format_memory_entry,
    _parse_working_memory_section,
)
from .memory_init import ensure_memory_ready
# Dual import: relative (when loaded as package) vs absolute (when tests add scripts/ to sys.path)
try:
    from .pact_session import get_session_id_from_context_file
except ImportError:
    from pact_session import get_session_id_from_context_file

# Configure logging
logger = logging.getLogger(__name__)

# Fields whose changes should trigger embedding regeneration.
# Kept as a module-level constant so tests can import and verify it.
CONTENT_FIELDS = {
    "context", "goal", "lessons_learned", "decisions", "entities",
    "reasoning_chains", "agreements_reached", "disagreements_resolved",
}


def _content_fields_changed(
    before: Dict[str, Any],
    after: Dict[str, Any],
    keys: List[str],
) -> bool:
    """Return True if any of ``keys`` differs between ``before`` and ``after``.

    Used by ``PACTMemory.update`` (M7, #374 remediation) to skip embedding
    regeneration when an additive merge produces no actual change. Values are
    compared via canonical JSON serialization so that dict/list ordering and
    non-JSON-native types (e.g. datetimes) compare deterministically.
    """
    for k in keys:
        if json.dumps(before.get(k), sort_keys=True, default=str) != \
           json.dumps(after.get(k), sort_keys=True, default=str):
            return True
    return False


def _ensure_ready() -> None:
    """
    Ensure the memory system is initialized before database operations.

    This wrapper exists as:
    - A single injection point for all API methods (centralized initialization)
    - A testing seam (can be mocked to skip initialization in tests)
    - An abstraction layer if initialization logic needs to change

    Handles lazily on first use:
    - Dependency installation
    - Embedding migration
    - Pending embedding catch-up

    The initialization only runs once per session.
    """
    ensure_memory_ready()


class PACTMemory:
    """
    High-level interface for PACT Memory operations.

    Provides a clean API for saving, searching, and managing memories
    with automatic project/session detection and file tracking.

    Usage:
        memory = PACTMemory()

        # Save a memory
        memory_id = memory.save({
            "context": "Working on authentication",
            "goal": "Add JWT refresh tokens",
            "lessons_learned": ["Redis INCR is atomic"],
            "decisions": [{"decision": "Use Redis", "rationale": "Fast TTL"}]
        })

        # Search memories
        results = memory.search("authentication tokens")

        # List recent memories
        recent = memory.list(limit=10)
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        db_path: Optional[Path] = None
    ):
        """
        Initialize the PACTMemory API.

        Args:
            project_id: Project identifier. If not provided, auto-detected using
                        (in order): CLAUDE_PROJECT_DIR env var, git repo root,
                        or current working directory basename.
            session_id: Session identifier. Auto-detected from context file if not provided.
            db_path: Custom database path. Uses default if not provided.
        """
        self._project_id = project_id or self._detect_project_id()
        self._session_id = session_id or self._detect_session_id()
        self._db_path = db_path

        # Session file tracking (populated by hooks)
        self._session_files: List[str] = []

        logger.debug(
            f"PACTMemory initialized: project={self._project_id}, session={self._session_id}"
        )

    @staticmethod
    def _find_project_root(start: Path) -> Path:
        """
        Walk UP from `start` looking for a project marker; return the first
        marker-containing directory.

        Markers (any of):
        - `.git` (file or dir — submodules use a file)
        - `.claude/` directory
        - `CLAUDE.md` at either supported location (./ or .claude/)

        If no marker is found walking to the filesystem root, returns `start`
        unchanged (fallback to original CWD-basename behavior).

        Args:
            start: Path to begin the walk from (typically Path.cwd()).

        Returns:
            First ancestor (inclusive of `start`) containing a project marker,
            or `start` if none found.
        """
        try:
            current = start.resolve()
        except (OSError, RuntimeError):
            return start
        for parent in [current] + list(current.parents):
            if (parent / ".git").exists():
                return parent
            if (parent / ".claude").is_dir():
                return parent
            if (parent / "CLAUDE.md").exists():
                return parent
            if (parent / ".claude" / "CLAUDE.md").exists():
                return parent
        return start  # fallback: use original

    @staticmethod
    def _detect_project_id() -> Optional[str]:
        """
        Detect project ID from environment with multiple fallback strategies.

        Detection order:
        1. CLAUDE_PROJECT_DIR environment variable (original behavior)
        2. Git repository root via 'git rev-parse --git-common-dir' (worktree-safe)
        3. Current working directory — walked UP to the nearest project marker
           (.git, .claude/, or CLAUDE.md at either location). This handles the
           case where the user runs the CLI from a subdirectory.

        Returns:
            Project ID string (directory basename), or None if all methods fail.
        """
        # Strategy 1: Environment variable (original behavior)
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            logger.debug("project_id detected from CLAUDE_PROJECT_DIR: %s", Path(project_dir).name)
            return Path(project_dir).name

        # Strategy 2: Git repository root (worktree-safe)
        # Uses --git-common-dir instead of --show-toplevel because the latter
        # returns the worktree path when run inside a worktree, fragmenting
        # project_id across sessions. --git-common-dir always points to the
        # shared .git directory; its parent is the main repo root.
        # NOTE: Twin pattern in working_memory.py (_get_claude_md_path) and
        #       hooks/staleness.py (get_project_claude_md_path) -- keep in sync.
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                git_common_dir = result.stdout.strip()
                repo_root = Path(git_common_dir).resolve().parent
                project_name = repo_root.name
                logger.debug("project_id detected from git root: %s", project_name)
                return project_name
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # git not installed, not a repo, or command timed out
            logger.debug("Git detection failed, falling back to cwd")

        # Strategy 3: Current working directory — walk UP to nearest project marker.
        # Fixes subdirectory invocation (e.g., running CLI from .claude/ or src/
        # would previously return the subdirectory basename as the project_id).
        try:
            cwd_root = PACTMemory._find_project_root(Path.cwd())
            cwd_name = cwd_root.name
            if cwd_name:
                logger.debug("project_id detected from cwd: %s", cwd_name)
                return cwd_name
        except OSError:
            logger.debug("Failed to detect project_id from cwd")

        return None

    @staticmethod
    def _detect_session_id() -> Optional[str]:
        """Detect session ID from PACT context file."""
        return get_session_id_from_context_file() or None

    @property
    def project_id(self) -> Optional[str]:
        """Get the current project ID."""
        return self._project_id

    @property
    def session_id(self) -> Optional[str]:
        """Get the current session ID."""
        return self._session_id

    def track_file(self, path: str) -> None:
        """
        Track a file modified in this session.

        Called by file tracking hooks to accumulate files
        that will be linked to saved memories.

        Args:
            path: File path that was modified.
        """
        if path not in self._session_files:
            self._session_files.append(path)
            logger.debug(f"Tracking file: {path}")

    def get_tracked_files(self) -> List[str]:
        """Get list of files tracked in this session."""
        return self._session_files.copy()

    def clear_tracked_files(self) -> None:
        """Clear the list of tracked files."""
        self._session_files.clear()

    def save(
        self,
        memory: Dict[str, Any],
        files: Optional[List[str]] = None,
        include_tracked: bool = True
    ) -> str:
        """
        Save a memory to the database.

        Automatically:
        - Adds project_id and session_id if not provided
        - Links tracked files from the session
        - Generates and stores embedding for semantic search

        Args:
            memory: Memory dictionary with fields like context, goal,
                    lessons_learned, decisions, entities, active_tasks.
            files: Optional explicit file list to link.
            include_tracked: Include automatically tracked session files.

        Returns:
            The ID of the saved memory.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        # Add project/session context if not provided
        if "project_id" not in memory or memory["project_id"] is None:
            memory["project_id"] = self._project_id
        if "session_id" not in memory or memory["session_id"] is None:
            memory["session_id"] = self._session_id

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            # Create the memory record
            memory_id = create_memory(conn, memory)

            # Collect files to link
            files_to_link = []
            if files:
                files_to_link.extend(files)
            if include_tracked and self._session_files:
                files_to_link.extend(self._session_files)

            # Link files to memory
            if files_to_link:
                link_memory_to_paths(
                    conn, memory_id, files_to_link,
                    self._project_id, "modified"
                )

            # Store embedding for semantic search
            self._store_embedding(conn, memory_id, memory)

            logger.info(f"Saved memory {memory_id} with {len(files_to_link)} files")

        # Verify the save persisted by reading back (before syncing to CLAUDE.md,
        # so we never reference a phantom memory in working memory)
        if memory_id is None:
            raise RuntimeError("Save failed — no memory_id returned")
        verification = self.get(memory_id)
        if verification is None:
            raise RuntimeError(
                f"Save verification failed — memory_id {memory_id} not found after save"
            )

        # Sync to CLAUDE.md working memory (outside db connection context)
        # This is non-critical - failures are logged but don't fail the save
        try:
            sync_to_claude_md(memory, files_to_link if files_to_link else None, memory_id)
        except Exception as e:
            logger.warning(f"Failed to sync to CLAUDE.md: {e}")

        return memory_id

    def _store_embedding(
        self,
        conn: sqlite3.Connection,
        memory_id: str,
        memory: Dict[str, Any]
    ) -> bool:
        """
        Generate and store embedding for a memory.

        Requires SQLITE_EXTENSIONS_ENABLED (pysqlite3-binary) and sqlite-vec.
        If extensions are unavailable, skips embedding storage silently -
        search will fall back to keyword-only mode.

        Args:
            conn: Active database connection.
            memory_id: Memory ID to associate embedding with.
            memory: Memory data for embedding generation.

        Returns:
            True if embedding was stored, False otherwise.
        """
        # Check if SQLite extension loading is available
        if not SQLITE_EXTENSIONS_ENABLED:
            logger.debug(
                "Skipping embedding storage - SQLite extensions unavailable. "
                "Search will use keyword mode."
            )
            return False

        # Generate text for embedding
        text = generate_embedding_text(memory)
        if not text:
            return False

        # Generate embedding
        embedding = generate_embedding(text)
        if embedding is None:
            logger.debug("Embedding generation unavailable, skipping")
            return False

        try:
            # Enable extension loading (safe because SQLITE_EXTENSIONS_ENABLED is True)
            conn.enable_load_extension(True)
            try:
                import sqlite_vec
                sqlite_vec.load(conn)
            except ImportError:
                logger.debug("sqlite-vec not installed, skipping embedding storage")
                return False

            # Convert to blob
            embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)

            # Insert into vector table
            conn.execute(
                """
                INSERT OR REPLACE INTO vec_memories (memory_id, project_id, embedding)
                VALUES (?, ?, ?)
                """,
                (memory_id, memory.get("project_id"), embedding_blob)
            )
            conn.commit()

            logger.debug(f"Stored embedding for memory {memory_id}")
            return True

        except Exception as e:
            logger.debug(f"Failed to store embedding: {e}")
            return False

    def search(
        self,
        query: str,
        current_file: Optional[str] = None,
        limit: int = 5,
        sync_to_claude: bool = True
    ) -> List[MemoryObject]:
        """
        Search memories using semantic similarity and graph relationships.

        Args:
            query: Search query text.
            current_file: Optional current file for context boosting.
            limit: Maximum number of results.
            sync_to_claude: Whether to sync top result to CLAUDE.md Retrieved Context.

        Returns:
            List of matching MemoryObject instances.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        results = graph_enhanced_search(
            query,
            current_file=current_file,
            project_id=self._project_id,
            limit=limit
        )

        # Sync to CLAUDE.md Retrieved Context section
        if sync_to_claude and results:
            try:
                # Convert MemoryObjects to dicts for sync
                memory_dicts = [r.to_dict() for r in results]
                memory_ids = [r.id for r in results]
                # graph_enhanced_search doesn't return scores, so pass None
                sync_retrieved_to_claude_md(memory_dicts, query, None, memory_ids)
            except Exception as e:
                logger.warning(f"Failed to sync retrieved context to CLAUDE.md: {e}")

        return results

    def search_by_file(
        self,
        file_path: str,
        limit: int = 10
    ) -> List[MemoryObject]:
        """
        Find memories related to a specific file.

        Args:
            file_path: File path to search for.
            limit: Maximum number of results.

        Returns:
            List of related MemoryObject instances.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        return search_by_file(file_path, self._project_id, limit)

    def _resolve_id_or_full(
        self, conn, memory_id: str
    ) -> Optional[str]:
        """
        Resolve a caller-supplied ID into a full 32-char memory ID.

        Input is case-folded to lowercase before any branch, so an
        uppercase or mixed-case full ID resolves identically to its
        lowercase form (memory IDs are stored as lowercase hex).
        Full-length input is then returned unchanged (no DB query).
        Shorter input is treated as a prefix and resolved via the
        storage-layer resolver: a unique prefix returns the full ID;
        ambiguity raises AmbiguousPrefixError; too-short raises
        PrefixTooShortError; no match returns None.

        Caller already owns an open `conn` (inside a `db_connection`
        context manager). This helper does not open or close connections.

        Args:
            conn: Active database connection.
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.
                Case-insensitive: uppercase and mixed-case input is
                normalized to lowercase before lookup.

        Returns:
            The full memory ID (lowercase), or None if the prefix matches
            no row.

        Raises:
            PrefixTooShortError: prefix shorter than MIN_PREFIX_LENGTH.
            AmbiguousPrefixError: prefix matches more than one memory.
        """
        memory_id = memory_id.lower()
        if len(memory_id) >= MEMORY_ID_LENGTH:
            return memory_id
        return resolve_memory_id_prefix(conn, memory_id)

    def get(self, memory_id: str) -> Optional[MemoryObject]:
        """
        Get a specific memory by ID or unique prefix.

        Accepts a full 32-char memory ID or a prefix of at least
        MIN_PREFIX_LENGTH characters. A unique prefix resolves to the
        matching memory; ambiguity and too-short input surface as
        exceptions from the storage-layer resolver.

        Args:
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.

        Returns:
            MemoryObject if found, None if no match.

        Raises:
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            resolved = self._resolve_id_or_full(conn, memory_id)
            if resolved is None:
                return None
            memory_id = resolved

            memory_dict = get_memory(conn, memory_id)
            if memory_dict is None:
                return None

            # Get associated files
            files_data = get_files_for_memory(conn, memory_id)
            file_paths = [f["path"] for f in files_data]

            return memory_from_db_row(memory_dict, file_paths)

    def update(
        self,
        memory_id: str,
        updates: Dict[str, Any],
        *,
        replace: bool = False,
    ) -> Optional[str]:
        """
        Update an existing memory by ID or unique prefix.

        Accepts a full 32-char memory ID or a prefix of at least
        MIN_PREFIX_LENGTH characters. A unique prefix resolves to the
        matching memory; ambiguous prefixes are refused (the update is
        rejected via AmbiguousPrefixError so the caller can disambiguate).

        Args:
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.
            updates: Dictionary of fields to update.
            replace: If True, list-valued fields are replaced wholesale
                instead of merged additively (default False = additive merge
                with content-hash dedup).

        Returns:
            The resolved full 32-char memory ID on successful update, or
            None when the input matched no row. Callers that invoked with a
            prefix get the canonical ID back so downstream operations key off
            the storage form.

        Raises:
            ValueError: If updates contains unknown field names, or if any
                dict-list item contains unknown sub-object keys.
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            resolved = self._resolve_id_or_full(conn, memory_id)
            if resolved is None:
                return None
            memory_id = resolved

            # M7 (#374 remediation): snapshot CONTENT_FIELDS before the
            # update so we can detect whether the merge actually changed
            # any embedding-relevant value. update_memory's additive merge
            # is idempotent for repeat calls — without this snapshot we'd
            # regenerate embeddings on every no-op update touching any
            # CONTENT_FIELDS key, even when the merge produced no diff.
            content_keys_in_update = [
                f for f in CONTENT_FIELDS if f in updates
            ]
            before_snapshot: Optional[Dict[str, Any]] = None
            if content_keys_in_update:
                before_snapshot = get_memory(conn, memory_id) or {}

            success = update_memory(conn, memory_id, updates, replace=replace)

            if success and content_keys_in_update:
                memory_dict = get_memory(conn, memory_id)
                if memory_dict and _content_fields_changed(
                    before_snapshot or {}, memory_dict, content_keys_in_update,
                ):
                    self._store_embedding(conn, memory_id, memory_dict)

            return memory_id if success else None

    def delete(self, memory_id: str) -> Optional[str]:
        """
        Delete a memory by ID or unique prefix.

        Accepts a full 32-char memory ID or a prefix of at least
        MIN_PREFIX_LENGTH characters. A unique prefix resolves to the
        matching memory; ambiguous prefixes are refused (the delete is
        rejected via AmbiguousPrefixError so the caller can disambiguate).

        Args:
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.

        Returns:
            The resolved full 32-char memory ID on successful delete, or
            None when the input matched no row. Callers that invoked with a
            prefix get the canonical ID back so downstream operations key off
            the storage form.

        Raises:
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            resolved = self._resolve_id_or_full(conn, memory_id)
            if resolved is None:
                return None
            memory_id = resolved

            # Also remove from vector table. vec_memories is created lazily
            # by the FTS extension; absence is expected when FTS is
            # unavailable, and the "no such table" OperationalError is
            # silently swallowed in that case. All other OperationalErrors
            # (lock contention, corruption, schema violations) and every
            # other exception class must propagate so the caller sees the
            # real failure instead of a silent orphan-vector.
            try:
                conn.execute(
                    "DELETE FROM vec_memories WHERE memory_id = ?",
                    (memory_id,)
                )
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise

            return memory_id if delete_memory(conn, memory_id) else None

    def list(
        self,
        limit: int = 20,
        session_only: bool = False
    ) -> List[MemoryObject]:
        """
        List recent memories.

        Args:
            limit: Maximum number of results.
            session_only: Only return memories from current session.

        Returns:
            List of MemoryObject instances ordered by creation time.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            session_id = self._session_id if session_only else None

            memories_data = list_memories(
                conn,
                project_id=self._project_id,
                session_id=session_id,
                limit=limit
            )

            memories = []
            for memory_dict in memories_data:
                files_data = get_files_for_memory(conn, memory_dict["id"])
                file_paths = [f["path"] for f in files_data]
                memories.append(memory_from_db_row(memory_dict, file_paths))

            return memories

    def get_status(self) -> Dict[str, Any]:
        """
        Get status information about the memory system.

        Returns:
            Dictionary with database stats and capabilities.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        from .database import get_memory_count
        from .graph import get_graph_stats

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            memory_count = get_memory_count(conn, self._project_id)
            graph_stats = get_graph_stats(conn, self._project_id)

        capabilities = get_search_capabilities()

        return {
            "project_id": self._project_id,
            "session_id": self._session_id,
            "memory_count": memory_count,
            "tracked_files_count": len(self._session_files),
            "graph_stats": graph_stats,
            "capabilities": capabilities,
            "db_path": str(get_db_path())
        }


# Module-level singleton for convenience
_lock = threading.Lock()
_instance: Optional[PACTMemory] = None


def get_memory_instance(
    project_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> PACTMemory:
    """
    Get the PACTMemory singleton instance.

    Args:
        project_id: Optional project ID override.
        session_id: Optional session ID override.

    Returns:
        PACTMemory instance.
    """
    global _instance
    with _lock:
        if _instance is None:
            _instance = PACTMemory(project_id, session_id)
    return _instance


def reset_memory_instance() -> None:
    """Reset the singleton instance (useful for testing)."""
    global _instance
    with _lock:
        _instance = None


# Convenience functions for simple usage
def save_memory(memory: Dict[str, Any], **kwargs) -> str:
    """Save a memory using the default instance."""
    return get_memory_instance().save(memory, **kwargs)


def search_memory(query: str, sync_to_claude: bool = True, **kwargs) -> List[MemoryObject]:
    """Search memories using the default instance."""
    return get_memory_instance().search(query, sync_to_claude=sync_to_claude, **kwargs)


def list_memories_simple(limit: int = 20) -> List[MemoryObject]:
    """List recent memories using the default instance."""
    return get_memory_instance().list(limit=limit)
