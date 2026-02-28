"""
PACT Memory API

Location: pact-plugin/skills/pact-memory/scripts/memory_api.py

High-level API for the PACT Memory skill providing a clean interface
for saving, searching, and managing memories.

This is the primary entry point for agents and hooks to interact
with the memory system.

Used by:
- SKILL.md: Documents API usage for skill invocation
- Hooks: memory_prompt.py for file tracking
- Agents: Direct memory operations during PACT phases

Note: Memory initialization is lazy-loaded on first use via memory_init.py,
eliminating startup cost for non-memory users.
"""

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

# Configure logging
logger = logging.getLogger(__name__)

# Fields whose changes should trigger embedding regeneration.
# Kept as a module-level constant so tests can import and verify it.
CONTENT_FIELDS = {
    "context", "goal", "lessons_learned", "decisions", "entities",
    "reasoning_chains", "agreements_reached", "disagreements_resolved",
}


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
            session_id: Session identifier. Auto-detected from CLAUDE_SESSION_ID if not provided.
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
    def _detect_project_id() -> Optional[str]:
        """
        Detect project ID from environment with multiple fallback strategies.

        Detection order:
        1. CLAUDE_PROJECT_DIR environment variable (original behavior)
        2. Git repository root via 'git rev-parse --git-common-dir' (worktree-safe)
        3. Current working directory basename

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

        # Strategy 3: Current working directory
        try:
            cwd_name = Path.cwd().name
            if cwd_name:
                logger.debug("project_id detected from cwd: %s", cwd_name)
                return cwd_name
        except OSError:
            logger.debug("Failed to detect project_id from cwd")

        return None

    @staticmethod
    def _detect_session_id() -> Optional[str]:
        """Detect session ID from environment."""
        return os.environ.get("CLAUDE_SESSION_ID")

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

    def get(self, memory_id: str) -> Optional[MemoryObject]:
        """
        Get a specific memory by ID.

        Args:
            memory_id: The memory ID.

        Returns:
            MemoryObject if found, None otherwise.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            memory_dict = get_memory(conn, memory_id)
            if memory_dict is None:
                return None

            # Get associated files
            files_data = get_files_for_memory(conn, memory_id)
            file_paths = [f["path"] for f in files_data]

            return memory_from_db_row(memory_dict, file_paths)

    def update(self, memory_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update an existing memory.

        Args:
            memory_id: The memory ID.
            updates: Dictionary of fields to update.

        Returns:
            True if updated, False if memory not found.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            success = update_memory(conn, memory_id, updates)

            if success:
                # Update embedding if content changed
                if any(field in updates for field in CONTENT_FIELDS):
                    memory_dict = get_memory(conn, memory_id)
                    if memory_dict:
                        self._store_embedding(conn, memory_id, memory_dict)

            return success

    def delete(self, memory_id: str) -> bool:
        """
        Delete a memory.

        Args:
            memory_id: The memory ID.

        Returns:
            True if deleted, False if not found.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection(self._db_path) as conn:
            ensure_initialized(conn)

            # Also remove from vector table
            try:
                conn.execute(
                    "DELETE FROM vec_memories WHERE memory_id = ?",
                    (memory_id,)
                )
            except Exception:
                pass  # Vector table might not exist

            return delete_memory(conn, memory_id)

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
