"""
PACT Memory Graph Service

Location: pact-plugin/skills/pact-memory/scripts/graph.py

File relationship tracking and graph queries for the PACT Memory skill.
Enables tracking of file modifications during sessions and discovering
related files through the memory graph.

Graph Structure:
- Nodes: Files (tracked paths) and Memories (rich context objects)
- Edges:
  - Memory -> File (modified, referenced, created)
  - File -> File (imports, tests, extends, calls)
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .database import (
    db_connection,
    ensure_initialized,
    generate_id
)

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# File Tracking
# =============================================================================

def track_file(
    conn: sqlite3.Connection,
    path: str,
    project_id: Optional[str] = None
) -> str:
    """
    Track a file in the graph, creating a node if it doesn't exist.

    Args:
        conn: Active database connection.
        path: File path (absolute or relative).
        project_id: Optional project identifier for scoping.

    Returns:
        The file ID (existing or newly created).
    """
    ensure_initialized(conn)

    # Normalize path
    normalized_path = _normalize_path(path)

    # Check if file already exists
    cursor = conn.execute(
        """
        SELECT id FROM files
        WHERE path = ? AND (project_id = ? OR (project_id IS NULL AND ? IS NULL))
        """,
        (normalized_path, project_id, project_id)
    )
    row = cursor.fetchone()

    if row:
        # Update last_modified timestamp
        conn.execute(
            "UPDATE files SET last_modified = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row["id"])
        )
        conn.commit()
        return row["id"]

    # Create new file entry
    file_id = generate_id()
    conn.execute(
        """
        INSERT INTO files (id, path, project_id, last_modified)
        VALUES (?, ?, ?, ?)
        """,
        (file_id, normalized_path, project_id, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

    logger.debug(f"Tracked new file: {normalized_path} (ID: {file_id})")
    return file_id


def get_file_id(
    conn: sqlite3.Connection,
    path: str,
    project_id: Optional[str] = None
) -> Optional[str]:
    """
    Get the ID for a tracked file.

    Args:
        conn: Active database connection.
        path: File path.
        project_id: Optional project identifier.

    Returns:
        File ID if found, None otherwise.
    """
    ensure_initialized(conn)

    normalized_path = _normalize_path(path)

    cursor = conn.execute(
        """
        SELECT id FROM files
        WHERE path = ? AND (project_id = ? OR (project_id IS NULL AND ? IS NULL))
        """,
        (normalized_path, project_id, project_id)
    )
    row = cursor.fetchone()

    return row["id"] if row else None


def get_file_by_id(
    conn: sqlite3.Connection,
    file_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get file information by ID.

    Args:
        conn: Active database connection.
        file_id: The file ID.

    Returns:
        File dict if found, None otherwise.
    """
    ensure_initialized(conn)

    cursor = conn.execute(
        "SELECT * FROM files WHERE id = ?",
        (file_id,)
    )
    row = cursor.fetchone()

    return dict(row) if row else None


def list_tracked_files(
    conn: sqlite3.Connection,
    project_id: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    List all tracked files, optionally filtered by project.

    Args:
        conn: Active database connection.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of file dictionaries.
    """
    ensure_initialized(conn)

    if project_id is not None:
        cursor = conn.execute(
            """
            SELECT * FROM files
            WHERE project_id = ?
            ORDER BY last_modified DESC
            LIMIT ?
            """,
            (project_id, limit)
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM files ORDER BY last_modified DESC LIMIT ?",
            (limit,)
        )

    return [dict(row) for row in cursor.fetchall()]


def _normalize_path(path: str) -> str:
    """
    Normalize a file path for consistent storage.

    Args:
        path: File path to normalize.

    Returns:
        Normalized path string.
    """
    # Expand user home directory
    expanded = Path(path).expanduser()

    # If absolute, keep as is; otherwise store relative
    if expanded.is_absolute():
        return str(expanded)
    return str(Path(path))


# =============================================================================
# Memory-File Relationships
# =============================================================================

def link_memory_to_file(
    conn: sqlite3.Connection,
    memory_id: str,
    file_id: str,
    relationship: str = "modified"
) -> bool:
    """
    Create a relationship between a memory and a file.

    Args:
        conn: Active database connection.
        memory_id: The memory ID.
        file_id: The file ID.
        relationship: Type of relationship ('modified', 'referenced', 'created').

    Returns:
        True if link was created, False if it already exists.
    """
    ensure_initialized(conn)

    try:
        conn.execute(
            """
            INSERT INTO memory_files (memory_id, file_id, relationship)
            VALUES (?, ?, ?)
            """,
            (memory_id, file_id, relationship)
        )
        conn.commit()
        logger.debug(f"Linked memory {memory_id} to file {file_id} ({relationship})")
        return True
    except sqlite3.IntegrityError:
        # Link already exists
        return False


def link_memory_to_files(
    conn: sqlite3.Connection,
    memory_id: str,
    file_ids: List[str],
    relationship: str = "modified"
) -> int:
    """
    Link a memory to multiple files.

    Args:
        conn: Active database connection.
        memory_id: The memory ID.
        file_ids: List of file IDs.
        relationship: Type of relationship.

    Returns:
        Number of links created.
    """
    created = 0
    for file_id in file_ids:
        if link_memory_to_file(conn, memory_id, file_id, relationship):
            created += 1
    return created


def link_memory_to_paths(
    conn: sqlite3.Connection,
    memory_id: str,
    paths: List[str],
    project_id: Optional[str] = None,
    relationship: str = "modified"
) -> int:
    """
    Link a memory to files by path, tracking files if needed.

    Convenience function that handles file tracking and linking.

    Args:
        conn: Active database connection.
        memory_id: The memory ID.
        paths: List of file paths.
        project_id: Optional project identifier.
        relationship: Type of relationship.

    Returns:
        Number of links created.
    """
    file_ids = []
    for path in paths:
        file_id = track_file(conn, path, project_id)
        file_ids.append(file_id)

    return link_memory_to_files(conn, memory_id, file_ids, relationship)


def get_files_for_memory(
    conn: sqlite3.Connection,
    memory_id: str
) -> List[Dict[str, Any]]:
    """
    Get all files linked to a memory.

    Args:
        conn: Active database connection.
        memory_id: The memory ID.

    Returns:
        List of file dicts with relationship info.
    """
    ensure_initialized(conn)

    cursor = conn.execute(
        """
        SELECT f.*, mf.relationship
        FROM files f
        JOIN memory_files mf ON f.id = mf.file_id
        WHERE mf.memory_id = ?
        ORDER BY mf.relationship, f.path
        """,
        (memory_id,)
    )

    return [dict(row) for row in cursor.fetchall()]


def get_memories_for_file(
    conn: sqlite3.Connection,
    file_id: str
) -> List[str]:
    """
    Get all memory IDs linked to a file.

    Args:
        conn: Active database connection.
        file_id: The file ID.

    Returns:
        List of memory IDs.
    """
    ensure_initialized(conn)

    cursor = conn.execute(
        """
        SELECT DISTINCT memory_id
        FROM memory_files
        WHERE file_id = ?
        """,
        (file_id,)
    )

    return [row["memory_id"] for row in cursor.fetchall()]


def get_memories_for_files(
    conn: sqlite3.Connection,
    file_paths: List[str],
    project_id: Optional[str] = None
) -> List[str]:
    """
    Get all memory IDs linked to any of the given file paths.

    Args:
        conn: Active database connection.
        file_paths: List of file paths.
        project_id: Optional project identifier.

    Returns:
        List of unique memory IDs.
    """
    ensure_initialized(conn)

    memory_ids: Set[str] = set()

    for path in file_paths:
        file_id = get_file_id(conn, path, project_id)
        if file_id:
            memories = get_memories_for_file(conn, file_id)
            memory_ids.update(memories)

    return list(memory_ids)


# =============================================================================
# File-File Relationships
# =============================================================================

def add_file_relation(
    conn: sqlite3.Connection,
    source_path: str,
    target_path: str,
    relationship: str,
    project_id: Optional[str] = None
) -> bool:
    """
    Add a relationship between two files.

    Args:
        conn: Active database connection.
        source_path: Path of the source file.
        target_path: Path of the target file.
        relationship: Type of relationship ('imports', 'tests', 'extends', 'calls').
        project_id: Optional project identifier.

    Returns:
        True if relation was created, False if it already exists.
    """
    ensure_initialized(conn)

    # Track both files
    source_id = track_file(conn, source_path, project_id)
    target_id = track_file(conn, target_path, project_id)

    try:
        conn.execute(
            """
            INSERT INTO file_relations (source_file, target_file, relationship)
            VALUES (?, ?, ?)
            """,
            (source_id, target_id, relationship)
        )
        conn.commit()
        logger.debug(f"Added file relation: {source_path} -> {target_path} ({relationship})")
        return True
    except sqlite3.IntegrityError:
        # Relation already exists
        return False


def get_file_relations(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[str] = None,
    direction: str = "both"
) -> List[Dict[str, Any]]:
    """
    Get all relationships for a file.

    Args:
        conn: Active database connection.
        file_path: The file path.
        project_id: Optional project identifier.
        direction: 'outgoing', 'incoming', or 'both'.

    Returns:
        List of relation dicts with file info and relationship type.
    """
    ensure_initialized(conn)

    file_id = get_file_id(conn, file_path, project_id)
    if not file_id:
        return []

    relations = []

    if direction in ("outgoing", "both"):
        cursor = conn.execute(
            """
            SELECT f.*, fr.relationship, 'outgoing' as direction
            FROM files f
            JOIN file_relations fr ON f.id = fr.target_file
            WHERE fr.source_file = ?
            """,
            (file_id,)
        )
        relations.extend([dict(row) for row in cursor.fetchall()])

    if direction in ("incoming", "both"):
        cursor = conn.execute(
            """
            SELECT f.*, fr.relationship, 'incoming' as direction
            FROM files f
            JOIN file_relations fr ON f.id = fr.source_file
            WHERE fr.target_file = ?
            """,
            (file_id,)
        )
        relations.extend([dict(row) for row in cursor.fetchall()])

    return relations


def get_related_files(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[str] = None,
    max_depth: int = 2
) -> List[str]:
    """
    Get all files related to a given file through the graph.

    Performs breadth-first traversal up to max_depth hops.

    Args:
        conn: Active database connection.
        file_path: Starting file path.
        project_id: Optional project identifier.
        max_depth: Maximum traversal depth (default 2).

    Returns:
        List of related file paths.
    """
    ensure_initialized(conn)

    file_id = get_file_id(conn, file_path, project_id)
    if not file_id:
        return []

    visited: Set[str] = {file_id}
    current_level: Set[str] = {file_id}
    related_paths: List[str] = []

    for _ in range(max_depth):
        next_level: Set[str] = set()

        for current_id in current_level:
            # Get directly connected files
            cursor = conn.execute(
                """
                SELECT target_file as file_id FROM file_relations WHERE source_file = ?
                UNION
                SELECT source_file as file_id FROM file_relations WHERE target_file = ?
                """,
                (current_id, current_id)
            )

            for row in cursor.fetchall():
                neighbor_id = row["file_id"]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    next_level.add(neighbor_id)

                    # Get the path for this file
                    file_info = get_file_by_id(conn, neighbor_id)
                    if file_info:
                        related_paths.append(file_info["path"])

        current_level = next_level

        if not current_level:
            break

    return related_paths


def get_related_files_via_memories(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[str] = None
) -> List[str]:
    """
    Get files related through shared memories.

    Two files are related if they are linked to the same memory.

    Args:
        conn: Active database connection.
        file_path: Starting file path.
        project_id: Optional project identifier.

    Returns:
        List of related file paths.
    """
    ensure_initialized(conn)

    file_id = get_file_id(conn, file_path, project_id)
    if not file_id:
        return []

    # Find all files that share a memory with this file
    cursor = conn.execute(
        """
        SELECT DISTINCT f.path
        FROM files f
        JOIN memory_files mf ON f.id = mf.file_id
        WHERE mf.memory_id IN (
            SELECT memory_id FROM memory_files WHERE file_id = ?
        )
        AND f.id != ?
        """,
        (file_id, file_id)
    )

    return [row["path"] for row in cursor.fetchall()]


# =============================================================================
# Graph Analysis
# =============================================================================

def get_file_context(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get comprehensive context for a file from the graph.

    Includes direct relations, related files via memories, and
    associated memory IDs.

    Args:
        conn: Active database connection.
        file_path: The file path.
        project_id: Optional project identifier.

    Returns:
        Context dict with relationships, related files, and memory IDs.
    """
    ensure_initialized(conn)

    file_id = get_file_id(conn, file_path, project_id)
    if not file_id:
        return {
            "file_path": file_path,
            "tracked": False,
            "direct_relations": [],
            "memory_related_files": [],
            "memory_ids": []
        }

    return {
        "file_path": file_path,
        "tracked": True,
        "direct_relations": get_file_relations(conn, file_path, project_id),
        "memory_related_files": get_related_files_via_memories(conn, file_path, project_id),
        "memory_ids": get_memories_for_file(conn, file_id)
    }


def get_graph_stats(
    conn: sqlite3.Connection,
    project_id: Optional[str] = None
) -> Dict[str, int]:
    """
    Get statistics about the graph.

    Args:
        conn: Active database connection.
        project_id: Optional project filter.

    Returns:
        Dict with counts for files, memories, and relationships.
    """
    ensure_initialized(conn)

    stats = {}

    # File count
    if project_id:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM files WHERE project_id = ?",
            (project_id,)
        )
    else:
        cursor = conn.execute("SELECT COUNT(*) FROM files")
    stats["files"] = cursor.fetchone()[0]

    # Memory count
    if project_id:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE project_id = ?",
            (project_id,)
        )
    else:
        cursor = conn.execute("SELECT COUNT(*) FROM memories")
    stats["memories"] = cursor.fetchone()[0]

    # Memory-file links
    cursor = conn.execute("SELECT COUNT(*) FROM memory_files")
    stats["memory_file_links"] = cursor.fetchone()[0]

    # File-file relations
    cursor = conn.execute("SELECT COUNT(*) FROM file_relations")
    stats["file_relations"] = cursor.fetchone()[0]

    return stats


# =============================================================================
# Convenience Functions
# =============================================================================

def track_and_link(
    memory_id: str,
    file_paths: List[str],
    project_id: Optional[str] = None,
    relationship: str = "modified"
) -> int:
    """
    Track files and link them to a memory in one operation.

    Convenience function that handles the full workflow.

    Args:
        memory_id: The memory to link to.
        file_paths: List of file paths to track.
        project_id: Optional project identifier.
        relationship: Type of relationship.

    Returns:
        Number of links created.
    """
    with db_connection() as conn:
        return link_memory_to_paths(
            conn, memory_id, file_paths, project_id, relationship
        )


def discover_related(
    file_path: str,
    project_id: Optional[str] = None,
    include_memory_relations: bool = True
) -> List[str]:
    """
    Discover all files related to a given file.

    Combines direct file relations and memory-based relations.

    Args:
        file_path: Starting file path.
        project_id: Optional project identifier.
        include_memory_relations: Include files related via shared memories.

    Returns:
        List of unique related file paths.
    """
    with db_connection() as conn:
        related = set(get_related_files(conn, file_path, project_id))

        if include_memory_relations:
            memory_related = get_related_files_via_memories(conn, file_path, project_id)
            related.update(memory_related)

        return list(related)
