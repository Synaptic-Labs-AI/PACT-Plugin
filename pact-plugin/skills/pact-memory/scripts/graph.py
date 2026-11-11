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

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from .database import (
    db_connection,
    ensure_initialized,
    generate_id,
    normalise_project_scopes,
    project_scope_predicate,
    sqlite3 as _connection_sqlite3,
)

# A CONNECTION CAN COME FROM EITHER IMPLEMENTATION. `database.get_connection`
# uses pysqlite3 when it is installed and stdlib sqlite3 otherwise, and a
# caller may hand in a stdlib connection. The two IntegrityError classes are
# unrelated, so naming only one silently misses half the valid connections.
_INTEGRITY_ERRORS = (
    sqlite3.IntegrityError,
    _connection_sqlite3.IntegrityError,
)

# Configure logging
logger = logging.getLogger(__name__)


def _scope_sql(
    column: str,
    project_id: Optional[Union[str, Sequence[str]]],
) -> Tuple[str, Tuple[str, ...]]:
    """The scope clause for a graph read, with its bound parameters.

    `None` -> `("", ())`, today's no-filter meaning. A single string or a
    sequence -> `(" AND <column> IN (?,...)", params)`. An EMPTY scope yields
    `(" AND 0", ())` — a clause that matches NO row — never a dropped clause,
    which would return the whole store.

    `column` is a SQL identifier from our own code, never caller input; only
    the values are parameterised.
    """
    scopes = normalise_project_scopes(project_id)
    if scopes is None:
        return "", ()
    predicate, params = project_scope_predicate(column, scopes)
    return " AND " + predicate, params


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

    SINGLE-SCOPE BY RULING — do NOT make this union-aware. A canonical
    memory links a canonical FILE ROW; a path may hold one row per scope
    (`UNIQUE(path, project_id)`), and the rekey merges them. Resolving across
    the read union here would adopt a legacy-owned row and create a
    cross-scope link (canonical memory -> legacy file row), which the
    ownership rule forbids.

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

    # Create new file entry.
    #
    # OR IGNORE, BECAUSE THE SELECT ABOVE IS NOT A LOCK — FOR THE PROJECTED
    # SCOPE ONLY. Two writers can both see no row and both INSERT; with a
    # non-NULL project_id, UNIQUE(path, project_id) makes the second raise
    # IntegrityError, which aborts the caller's whole operation (a save, or a
    # re-key cascade) after its other writes have committed. An ignored insert
    # is not a lost file: the re-SELECT below adopts the row the winner
    # created.
    #
    # THE NULL-PROJECT SCOPE IS NOT PROTECTED: SQLite treats NULLs as distinct
    # in a UNIQUE constraint, so two writers inserting the same path with
    # project_id NULL BOTH succeed and OR IGNORE never fires; a partial unique
    # index would be needed to close that scope, and no in-repo caller writes
    # it (save refuses an unresolved project).
    file_id = generate_id()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO files (id, path, project_id, last_modified)
        VALUES (?, ?, ?, ?)
        """,
        (file_id, normalized_path, project_id, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

    if cursor.rowcount:
        logger.debug(f"Tracked new file: {normalized_path} (ID: {file_id})")
        return file_id

    # LOST THE RACE: another writer created (path, project_id) between our
    # SELECT and this INSERT. Read the winner on THIS connection, which sees
    # the committed row. A conflict that leaves no row to adopt would mean an
    # ignored constraint other than the one this branch is for, so it raises
    # rather than returning an id that was never stored.
    row = conn.execute(
        """
        SELECT id FROM files
        WHERE path = ? AND (project_id = ? OR (project_id IS NULL AND ? IS NULL))
        """,
        (normalized_path, project_id, project_id)
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"track_file: INSERT was ignored but no row exists for "
            f"({normalized_path!r}, {project_id!r})"
        )
    logger.debug(
        f"Tracked existing file after a create race: {normalized_path} "
        f"(ID: {row['id']})"
    )
    return row["id"]


def get_file_ids(
    conn: sqlite3.Connection,
    path: str,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> List[str]:
    """Every file-row id for `path` within the scope.

    A path can carry ONE ROW PER SCOPE (`UNIQUE(path, project_id)`), so the
    single-id `get_file_id` below sees only one bucket's row and only that
    row's links. A union read must resolve them all.

    `None` KEEPS TODAY'S MEANING, which is NOT "no filter": `get_file_id`'s
    null-safe form resolves the NULL-PROJECT row for the path, and the
    unscoped control arms depend on that. The union form resolves every row
    whose stored project is in the scope. An EMPTY scope returns no rows.
    Deterministic order by id.
    """
    ensure_initialized(conn)

    normalized_path = _normalize_path(path)
    scopes = normalise_project_scopes(project_id)
    if scopes is None:
        # DELIBERATELY NOT `_scope_sql`: in this resolver None means the
        # NULL-PROJECT row, not "no clause". Routing it through the builder
        # would return every project's row for the path — the cross-project
        # read this PR exists to close. Four MUST-KEEP arms pin this.
        cursor = conn.execute(
            "SELECT id FROM files WHERE path = ? AND project_id IS NULL "
            "ORDER BY id",
            (normalized_path,)
        )
        return [row["id"] for row in cursor.fetchall()]
    if not scopes:
        return []

    predicate, scope_params = project_scope_predicate("project_id", scopes)
    cursor = conn.execute(
        f"SELECT id FROM files WHERE path = ? AND {predicate} ORDER BY id",
        (normalized_path, *scope_params)
    )

    return [row["id"] for row in cursor.fetchall()]


def get_file_id(
    conn: sqlite3.Connection,
    path: str,
    project_id: Optional[str] = None
) -> Optional[str]:
    """
    Get the ID for a tracked file.

    EXACT `(path, project_id)` — the WRITE-side resolution. Scoped READS
    should use `get_file_ids`, because one path can carry a row per scope.

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
    project_id: Optional[Union[str, Sequence[str]]] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    List all tracked files, optionally filtered by project scope.

    THE LIMIT APPLIES TO THE MERGED RESULT: one statement over the union.
    An empty scope returns no rows, never the store.

    Args:
        conn: Active database connection.
        project_id: Optional project scope: one id or a union.
        limit: Maximum number of results.

    Returns:
        List of file dictionaries.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    scope_sql, scope_params = _scope_sql("project_id", project_id)
    cursor = conn.execute(
        f"""
        SELECT * FROM files
        WHERE 1=1{scope_sql}
        ORDER BY last_modified DESC
        LIMIT ?
        """,
        (*scope_params, limit)
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
    except _INTEGRITY_ERRORS:
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
    memory_id: str,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> List[Dict[str, Any]]:
    """
    Get all files linked to a memory.

    THE LINK DOES NOT CARRY A PROJECT, SO THE FILE ROW MUST: a legacy or
    interrupted re-key can leave a memory linked to another project's file
    row, and an unfiltered walk attaches that row's path to an otherwise
    confirmed memory. When a scope is given, each returned file's OWN stored
    project must be IN that scope — a file row keeps whatever bucket wrote
    it, and the union is how a cross-bucket link stays visible.

    A NULL-project file row is NOT any project's row and never matches a
    scope (NULL is never in the union, and `IN (NULL)` never matches).
    `None` is NO FILTER — the whole-store meaning existing callers rely on.
    An empty scope returns no rows.

    Args:
        conn: Active database connection.
        memory_id: The memory ID.
        project_id: Optional project scope: one id or a union.

    Returns:
        List of file dicts with relationship info.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    scope_sql, scope_params = _scope_sql("f.project_id", project_id)
    cursor = conn.execute(
        f"""
        SELECT f.*, mf.relationship
        FROM files f
        JOIN memory_files mf ON f.id = mf.file_id
        WHERE mf.memory_id = ?{scope_sql}
        ORDER BY mf.relationship, f.path
        """,
        (memory_id, *scope_params)
    )

    return [dict(row) for row in cursor.fetchall()]


def get_memories_for_file(
    conn: sqlite3.Connection,
    file_id: str,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> List[str]:
    """
    Get all memory IDs linked to a file.

    THE FILE ID NAMES A (path, project_id) ROW, BUT THE LINK DOES NOT: a
    legacy or interrupted re-key can leave a memory from another project
    linked to this file, and an id-only walk returns it as if it belonged
    here. When a scope is given, the memory's OWN stored project must be IN
    that scope. A NULL stored project is never in a union and never matches.

    `None` is NO FILTER — the whole-store meaning every existing caller
    relies on. An empty scope returns no rows.

    Args:
        conn: Active database connection.
        file_id: The file ID.
        project_id: Optional project scope: one id or a union.

    Returns:
        List of memory IDs.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    scope_sql, scope_params = _scope_sql("m.project_id", project_id)
    cursor = conn.execute(
        f"""
        SELECT DISTINCT mf.memory_id
        FROM memory_files mf
        JOIN memories m ON m.id = mf.memory_id
        WHERE mf.file_id = ?{scope_sql}
        """,
        (file_id, *scope_params)
    )

    return [row["memory_id"] for row in cursor.fetchall()]


def get_memories_for_files(
    conn: sqlite3.Connection,
    file_paths: List[str],
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> List[str]:
    """
    Get all memory IDs linked to any of the given file paths.

    EVERY SCOPED ROW PER PATH IS RESOLVED, not one: a path can carry a file
    row in each bucket, and a single `get_file_id` would silently miss the
    other bucket's links.

    Args:
        conn: Active database connection.
        file_paths: List of file paths.
        project_id: Optional project scope: one id or a union.

    Returns:
        List of unique memory IDs.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    memory_ids: Set[str] = set()

    for path in file_paths:
        for file_id in get_file_ids(conn, path, project_id):
            # The memories yielded must carry a stored project in the same
            # scope — a link left by a re-key otherwise returns another
            # project's ids here.
            memories = get_memories_for_file(conn, file_id, project_id)
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
    except _INTEGRITY_ERRORS:
        # Relation already exists
        return False


def get_file_relations(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[Union[str, Sequence[str]]] = None,
    direction: str = "both"
) -> List[Dict[str, Any]]:
    """
    Get all relationships for a file.

    EVERY SCOPED ROW FOR THE START PATH IS RESOLVED, because a path can
    carry one file row per bucket and each row owns its own relations.

    Args:
        conn: Active database connection.
        file_path: The file path.
        project_id: Optional project scope: one id or a union.
        direction: 'outgoing', 'incoming', or 'both'.

    Returns:
        List of relation dicts with file info and relationship type.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    file_ids = get_file_ids(conn, file_path, project_id)
    if not file_ids:
        return []

    placeholders = ",".join("?" for _ in file_ids)
    scope_sql, scope_params = _scope_sql("f.project_id", project_id)

    relations = []

    # THE RESOLVED ROWS ARE PROJECT-SCOPED; THE ENDPOINT IS NOT, UNLESS
    # CHECKED. `file_relations` has no project column and the JOIN returns
    # whatever row sits at the other end. `add_file_relation` cannot create a
    # cross-project edge (it resolves both endpoints under ONE project), but a
    # legacy or backfilled row can, and this scoped read would then hand the
    # caller another project's file row. Confirm the ENDPOINT's stored project
    # with the same rule as the other graph reads; `project_id=None` is NO
    # FILTER, and an empty scope has already returned above.
    #
    # SCOPE, RECORDED SO IT IS NOT RE-LITIGATED: this is a READ-side defence.
    # The WRITE side is out of scope for this PR — with no project column, a
    # cross-project edge remains hand-insertable, and that is data corruption
    # owned by the NULL-key/relations backfill arc, not a read-path defect.
    if direction in ("outgoing", "both"):
        cursor = conn.execute(
            f"""
            SELECT f.*, fr.relationship, 'outgoing' as direction
            FROM files f
            JOIN file_relations fr ON f.id = fr.target_file
            WHERE fr.source_file IN ({placeholders}){scope_sql}
            """,
            (*file_ids, *scope_params)
        )
        relations.extend([dict(row) for row in cursor.fetchall()])

    if direction in ("incoming", "both"):
        cursor = conn.execute(
            f"""
            SELECT f.*, fr.relationship, 'incoming' as direction
            FROM files f
            JOIN file_relations fr ON f.id = fr.source_file
            WHERE fr.target_file IN ({placeholders}){scope_sql}
            """,
            (*file_ids, *scope_params)
        )
        relations.extend([dict(row) for row in cursor.fetchall()])

    return relations


def get_related_files(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[Union[str, Sequence[str]]] = None,
    max_depth: int = 2
) -> List[str]:
    """
    Get all files related to a given file through the graph.

    Performs breadth-first traversal up to max_depth hops.

    THE START FILE IS SCOPED; A NEIGHBOUR IS NOT, UNLESS CHECKED.
    `file_relations` has no project column, so a legacy or backfilled edge can
    join two projects. Each neighbour's own stored project must be IN the
    scope, so a scoped walk neither returns nor traverses into another
    project's file row. The START resolves to EVERY scoped row for the path,
    because a path can carry one file row per bucket. `project_id=None`
    applies NO FILTER; an empty scope returns no rows.

    Args:
        conn: Active database connection.
        file_path: Starting file path.
        project_id: Optional project scope: one id or a union.
        max_depth: Maximum traversal depth (default 2).

    Returns:
        List of related file paths.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    file_ids = get_file_ids(conn, file_path, project_id)
    if not file_ids:
        return []

    scope_sql, scope_params = _scope_sql("f.project_id", project_id)

    visited: Set[str] = set(file_ids)
    current_level: Set[str] = set(file_ids)
    related_paths: List[str] = []

    for _ in range(max_depth):
        next_level: Set[str] = set()

        for current_id in current_level:
            # CONFIRM THE NEIGHBOUR'S STORED PROJECT BEFORE USING ITS ID OR
            # PATH: the id is what the next hop traverses from, so an
            # unconfirmed neighbour would also expose the rest of its own
            # graph. An empty scope has already returned above; `None` keeps
            # the unscoped whole-store meaning.
            cursor = conn.execute(
                f"""
                SELECT f.id AS file_id, f.path AS path
                FROM files f
                JOIN (
                    SELECT target_file AS file_id
                    FROM file_relations WHERE source_file = ?
                    UNION
                    SELECT source_file AS file_id
                    FROM file_relations WHERE target_file = ?
                ) rel ON rel.file_id = f.id
                WHERE 1=1{scope_sql}
                """,
                (current_id, current_id, *scope_params)
            )

            for row in cursor.fetchall():
                neighbor_id = row["file_id"]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    next_level.add(neighbor_id)
                    related_paths.append(row["path"])

        current_level = next_level

        if not current_level:
            break

    return related_paths


def get_related_files_via_memories(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> List[str]:
    """
    Get files related through shared memories.

    Two files are related if they are linked to the same memory.

    Args:
        conn: Active database connection.
        file_path: Starting file path.
        project_id: Optional project scope: one id or a union.

    Returns:
        List of related file paths.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    file_ids = get_file_ids(conn, file_path, project_id)
    if not file_ids:
        return []

    placeholders = ",".join("?" for _ in file_ids)
    scope_sql, scope_params = _scope_sql("f.project_id", project_id)

    # Find all files that share a memory with any scoped row of this path.
    #
    # THE SHARED MEMORY CAN BE STALE. A link left on the start row by another
    # project's memory still yields that memory's other files — which are not
    # this project's related files. Confirm each returned file's own stored
    # project; `project_id=None` keeps the whole-store meaning, and an empty
    # scope has already returned above.
    cursor = conn.execute(
        f"""
        SELECT DISTINCT f.path
        FROM files f
        JOIN memory_files mf ON f.id = mf.file_id
        WHERE mf.memory_id IN (
            SELECT memory_id FROM memory_files
            WHERE file_id IN ({placeholders})
        )
        AND f.id NOT IN ({placeholders})
        {scope_sql}
        """,
        (*file_ids, *file_ids, *scope_params)
    )

    return [row["path"] for row in cursor.fetchall()]


# =============================================================================
# Graph Analysis
# =============================================================================

def get_file_context(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> Dict[str, Any]:
    """
    Get comprehensive context for a file from the graph.

    Includes direct relations, related files via memories, and
    associated memory IDs.

    Args:
        conn: Active database connection.
        file_path: The file path.
        project_id: Optional project scope: one id or a union.

    Returns:
        Context dict with relationships, related files, and memory IDs.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return {
            "file_path": file_path,
            "tracked": False,
            "direct_relations": [],
            "memory_related_files": [],
            "memory_ids": []
        }

    file_ids = get_file_ids(conn, file_path, project_id)
    if not file_ids:
        return {
            "file_path": file_path,
            "tracked": False,
            "direct_relations": [],
            "memory_related_files": [],
            "memory_ids": []
        }

    memory_ids: List[str] = []
    for file_id in file_ids:
        # Confirmed against the MEMORY's stored project: the file rows are
        # project-scoped, but their links may predate a re-key.
        for memory_id in get_memories_for_file(conn, file_id, project_id):
            if memory_id not in memory_ids:
                memory_ids.append(memory_id)

    return {
        "file_path": file_path,
        "tracked": True,
        "direct_relations": get_file_relations(conn, file_path, project_id),
        "memory_related_files": get_related_files_via_memories(conn, file_path, project_id),
        "memory_ids": memory_ids
    }


def get_graph_stats(
    conn: sqlite3.Connection,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> Dict[str, int]:
    """
    Get statistics about the graph.

    EVERY COUNTER FOLLOWS THE SAME VISIBILITY RULE when a scope is given: a
    row is counted only when every stored row a scoped read would confirm is
    IN the scope. Files and memories are single-row counts. A memory-file
    link is counted only when BOTH its file row and its memory row are in the
    scope, and a file-file relation only when BOTH endpoints are, because
    that is exactly the set the scoped read surfaces (`get_memories_for_file`,
    `get_file_relations`) can return. A legacy cross-project link or relation
    is therefore counted under NEITHER side — it is invisible from both scoped
    reads and visible only in the unscoped whole-store count.

    NO DOUBLE COUNT ACROSS THE UNION: a link row joins exactly once even when
    both endpoints are in scope, and the memory total is COUNT(DISTINCT id),
    so a memory reached through several files is still one memory.

    AN EMPTY SCOPE IS ALL ZEROS, never the whole store. `None` keeps the
    unscoped whole-store meaning.

    Args:
        conn: Active database connection.
        project_id: Optional project scope: one id or a union.

    Returns:
        Dict with counts for files, memories, and relationships.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return {
            "files": 0,
            "memories": 0,
            "memory_file_links": 0,
            "file_relations": 0,
        }

    stats = {}

    if scopes is None:
        stats["files"] = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        stats["memories"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        stats["memory_file_links"] = conn.execute("SELECT COUNT(*) FROM memory_files").fetchone()[0]
        stats["file_relations"] = conn.execute("SELECT COUNT(*) FROM file_relations").fetchone()[0]
        return stats

    file_pred, file_params = project_scope_predicate("project_id", scopes)
    stats["files"] = conn.execute(
        f"SELECT COUNT(*) FROM files WHERE {file_pred}", file_params
    ).fetchone()[0]

    mem_pred, mem_params = project_scope_predicate("project_id", scopes)
    stats["memories"] = conn.execute(
        f"SELECT COUNT(DISTINCT id) FROM memories WHERE {mem_pred}", mem_params
    ).fetchone()[0]

    f_pred, f_params = project_scope_predicate("f.project_id", scopes)
    m_pred, m_params = project_scope_predicate("m.project_id", scopes)
    stats["memory_file_links"] = conn.execute(
        f"""
        SELECT COUNT(*) FROM memory_files mf
        JOIN files f ON f.id = mf.file_id
        JOIN memories m ON m.id = mf.memory_id
        WHERE {f_pred} AND {m_pred}
        """,
        (*f_params, *m_params)
    ).fetchone()[0]

    sf_pred, sf_params = project_scope_predicate("sf.project_id", scopes)
    tf_pred, tf_params = project_scope_predicate("tf.project_id", scopes)
    stats["file_relations"] = conn.execute(
        f"""
        SELECT COUNT(*) FROM file_relations fr
        JOIN files sf ON sf.id = fr.source_file
        JOIN files tf ON tf.id = fr.target_file
        WHERE {sf_pred} AND {tf_pred}
        """,
        (*sf_params, *tf_params)
    ).fetchone()[0]

    return stats


def count_project_file_paths(
    conn: sqlite3.Connection,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> int:
    """Distinct tracked-file PATHS in the scope.

    `get_graph_stats()["files"]` counts ROWS, and one path can carry a row in
    each bucket of the union; this counts the paths those rows name. The
    disclosure reports both, so a union is never presented as if the two
    buckets shared no files.

    `None` is the whole store; an empty scope is zero.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is None:
        return conn.execute("SELECT COUNT(DISTINCT path) FROM files").fetchone()[0]
    if not scopes:
        return 0
    predicate, params = project_scope_predicate("project_id", scopes)
    return conn.execute(
        f"SELECT COUNT(DISTINCT path) FROM files WHERE {predicate}", params
    ).fetchone()[0]


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
