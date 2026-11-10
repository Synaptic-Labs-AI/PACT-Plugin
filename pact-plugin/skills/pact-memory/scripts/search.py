"""
PACT Memory Search Service

Location: pact-plugin/skills/pact-memory/scripts/search.py

Search functionality combining semantic similarity and graph relationships.
Provides intelligent retrieval of memories based on:
1. Vector similarity (semantic search via embeddings)
2. Graph traversal (related files share context)
3. Keyword matching (fallback when embeddings unavailable)

Used by:
- memory_api.py: High-level search() method
"""

from __future__ import annotations

import importlib.util
import logging
import struct
from typing import Any, Dict, List, Optional, Set, Tuple

# Use the same sqlite3 module as database.py for type consistency
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .database import (
    db_connection,
    ensure_initialized,
    get_memory,
    search_memories_by_text,
    SQLITE_EXTENSIONS_ENABLED
)
from .embeddings import (
    generate_embedding,
    generate_embedding_text,
    check_embedding_availability
)
from .graph import (
    get_file_id,
    get_memories_for_file,
    get_memories_for_files,
    get_related_files,
    get_related_files_via_memories
)
from .models import MemoryObject, memory_from_db_row

# Configure logging
logger = logging.getLogger(__name__)

# Search tuning parameters
DISTANCE_NORMALIZATION_FACTOR = 2.0  # Normalize vector distance to 0-1 score
KEYWORD_POSITION_DECAY = 0.05  # Score reduction per position in keyword results
GRAPH_BOOST_FACTOR = 0.3  # Boost factor for graph-related memories
GRAPH_BASE_SCORE = 0.3  # Base relevance score for graph-connected memories

# A stored-project confirmation can discard stale cross-project hits, so a
# query that fetched exactly `limit` candidates can return fewer than the
# caller asked for. Fetches widen until enough confirmed hits exist or the
# store is exhausted; these caps bound the widening so a store full of stale
# rows cannot turn one search into an unbounded series of queries.
#
# THE TWO CEILINGS DIFFER ON PURPOSE. The vector leg widens one local KNN
# query, so 200 candidates is cheap. The graph leg re-runs
# `graph_boosted_search` per widening step, and that generates an embedding
# for the query each time — a ceiling of 200 there would mean up to eight
# embedding calls for one search. 50 bounds it to a handful.
CONFIRMATION_FETCH_CAP = 200
GRAPH_CONFIRMATION_FETCH_CAP = 50


def _scope_or_none(project_id: Optional[str]) -> Optional[str]:
    """One scope predicate at every public entry: absent and "" both mean ALL.

    The SQL legs have always read the scope by truthiness, while the
    stored-project confirmations read it by `is None`. Without this
    normalisation `""` would run the query unscoped and then filter every hit
    away, turning a whole-store search into an empty one. Normalise ONCE, at
    each public entry; every later test is `is None`.
    """
    return project_id or None


def vector_search(
    conn: sqlite3.Connection,
    query: str,
    project_id: Optional[str] = None,
    limit: int = 10
) -> List[Tuple[str, float]]:
    """
    Perform semantic vector search on memories.

    Uses sqlite-vec for efficient similarity search. Requires:
    1. pysqlite3 (for extension loading)
    2. sqlite-vec (for vector operations)

    Args:
        conn: Active database connection.
        query: Search query text.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of (memory_id, distance) tuples, sorted by similarity.
        Returns empty list if vector search unavailable (falls back to keyword).
    """
    project_id = _scope_or_none(project_id)

    # Check if SQLite extension loading is available
    if not SQLITE_EXTENSIONS_ENABLED:
        logger.debug("Vector search unavailable - SQLite extensions not enabled")
        return []

    # Generate query embedding
    query_embedding = generate_embedding(query)
    if query_embedding is None:
        logger.debug("No embedding available, skipping vector search")
        return []

    try:
        # Enable extension loading (safe because SQLITE_EXTENSIONS_ENABLED is True)
        conn.enable_load_extension(True)
        try:
            import sqlite_vec
            sqlite_vec.load(conn)
        except ImportError:
            logger.debug("sqlite-vec not available for search")
            return []

        # Convert embedding to blob for sqlite-vec
        embedding_blob = struct.pack(f'{len(query_embedding)}f', *query_embedding)

        # Build query with optional project filter
        if project_id is not None:
            sql = """
                SELECT memory_id, distance
                FROM vec_memories
                WHERE embedding MATCH ?
                AND project_id = ?
                ORDER BY distance
                LIMIT ?
            """
            sql_params: Tuple[Any, ...] = (embedding_blob, project_id)
        else:
            sql = """
                SELECT memory_id, distance
                FROM vec_memories
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
            """
            sql_params = (embedding_blob,)

        def _fetch(fetch_limit: int) -> List[Tuple[str, float]]:
            return [
                (row[0], row[1])
                for row in conn.execute(
                    sql, (*sql_params, fetch_limit)
                ).fetchall()
            ]

        rows = _fetch(limit)

        # THE PARTITION IS A HINT; THE ROW'S STORED SCOPE IS THE FACT.
        # A partition row is written by whichever process stored the vector,
        # and a re-key that could not move it — an embedding fault, or a
        # process that cannot open the vector table — leaves the OLD project's
        # partition populated. Returning such a hit hands another project's
        # memory to this project's caller, and nothing downstream re-checks it.
        # Confirm every hit against memories before returning it here, so both
        # callers inherit the guarantee.
        #
        # `project_id is None` KEEPS ITS WHOLE-STORE MEANING: an unscoped
        # search asks for every partition, and filtering there would silently
        # narrow a documented scope.
        if project_id is None or not rows:
            return rows

        # A DISCARDED HIT MUST NOT COST A SLOT. The SQL LIMIT runs before this
        # confirmation, so a stale row inside the top `limit` would otherwise
        # leave the caller short of results the store can supply. Widen the
        # fetch until enough confirmed hits exist, the store is exhausted, or
        # the cap is reached.
        fetch = limit
        while True:
            ids = [memory_id for memory_id, _ in rows]
            placeholders = ",".join("?" * len(ids))
            scoped = {
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM memories WHERE id IN ({placeholders}) "
                    "AND project_id = ?",
                    (*ids, project_id),
                )
            }
            confirmed = [
                (memory_id, distance)
                for memory_id, distance in rows
                if memory_id in scoped
            ]
            if (
                len(confirmed) >= limit
                or len(rows) < fetch
                or fetch >= CONFIRMATION_FETCH_CAP
            ):
                return confirmed[:limit]
            fetch = min(fetch * 2, CONFIRMATION_FETCH_CAP)
            rows = _fetch(fetch)

    except Exception as e:
        error_str = str(e).lower()
        if "dimension" in error_str or "mismatch" in error_str:
            logger.warning(
                f"Vector search failed (dimension mismatch): {e}. "
                "Run ensure_memory_ready() to re-initialize the vector table."
            )
        else:
            logger.debug(f"Vector search failed: {e}")
        return []


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    project_id: Optional[str] = None,
    limit: int = 10
) -> List[str]:
    """
    Perform keyword-based text search.

    Fallback when semantic search is unavailable.

    Args:
        conn: Active database connection.
        query: Search query text.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of memory IDs matching the query.
    """
    results = search_memories_by_text(conn, query, project_id, limit)
    return [r["id"] for r in results]


def semantic_search(
    query: str,
    project_id: Optional[str] = None,
    limit: int = 10
) -> List[str]:
    """
    Perform semantic search returning memory IDs.

    Combines vector similarity with keyword fallback.

    Args:
        query: Search query text.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of memory IDs sorted by relevance.
    """
    project_id = _scope_or_none(project_id)

    with db_connection() as conn:
        ensure_initialized(conn)

        # Try vector search first
        vector_results = vector_search(conn, query, project_id, limit)

        if vector_results:
            return [memory_id for memory_id, _ in vector_results]

        # Fallback to keyword search
        logger.debug("Falling back to keyword search")
        return keyword_search(conn, query, project_id, limit)


def graph_boosted_search(
    conn: sqlite3.Connection,
    query: str,
    current_file: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 10
) -> List[Tuple[str, float]]:
    """
    Search with graph-based relevance boosting.

    When working on a file, memories related to that file
    or its graph neighbors are boosted in ranking.

    Args:
        conn: Active database connection.
        query: Search query text.
        current_file: Optional file path for context boost.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of (memory_id, score) tuples, higher is better.
    """
    project_id = _scope_or_none(project_id)

    ensure_initialized(conn)

    # Get base semantic results
    vector_results = vector_search(conn, query, project_id, limit * 2)

    # Convert to scores (invert distance: lower distance = higher score)
    # Distance is typically 0-2 for normalized vectors
    memory_scores: Dict[str, float] = {}
    for memory_id, distance in vector_results:
        # Convert distance to similarity score (0-1 range)
        score = max(0, 1 - (distance / DISTANCE_NORMALIZATION_FACTOR))
        memory_scores[memory_id] = score

    # If no vector results, fall back to keyword search
    if not memory_scores:
        keyword_results = keyword_search(conn, query, project_id, limit * 2)
        for i, memory_id in enumerate(keyword_results):
            # Give decreasing scores based on position
            memory_scores[memory_id] = 1.0 - (i * KEYWORD_POSITION_DECAY)

    # Apply graph boosting if we have a current file
    if current_file:
        graph_memory_ids = _get_graph_related_memories(
            conn, current_file, project_id
        )

        # Boost scores for graph-related memories
        for memory_id in graph_memory_ids:
            if memory_id in memory_scores:
                # Boost existing score
                memory_scores[memory_id] *= (1 + GRAPH_BOOST_FACTOR)
            else:
                # Add with base graph relevance score
                memory_scores[memory_id] = GRAPH_BASE_SCORE

    # Sort by score (descending) and return top results
    sorted_results = sorted(
        memory_scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    return sorted_results[:limit]


def _get_graph_related_memories(
    conn: sqlite3.Connection,
    file_path: str,
    project_id: Optional[str] = None
) -> Set[str]:
    """
    Get memory IDs related to a file through the graph.

    Includes:
    - Direct memory links to the file
    - Memories linked to graph-related files

    Args:
        conn: Active database connection.
        file_path: File path to find relations for.
        project_id: Optional project filter.

    Returns:
        Set of related memory IDs.
    """
    memory_ids: Set[str] = set()

    # Get memories directly linked to this file
    file_id = get_file_id(conn, file_path, project_id)
    if file_id:
        direct_memories = get_memories_for_file(conn, file_id, project_id)
        memory_ids.update(direct_memories)

    # Get memories linked through file relationships
    related_files = get_related_files(conn, file_path, project_id, max_depth=1)
    related_memories = get_memories_for_files(conn, related_files, project_id)
    memory_ids.update(related_memories)

    # Get memories through shared memory links
    memory_related_files = get_related_files_via_memories(conn, file_path, project_id)
    more_memories = get_memories_for_files(conn, memory_related_files, project_id)
    memory_ids.update(more_memories)

    return memory_ids


def graph_enhanced_search(
    query: str,
    current_file: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 20
) -> List[MemoryObject]:
    """
    Search memories with graph-enhanced ranking.

    Combines semantic similarity with graph relationships to find
    the most relevant memories. When working on a file, related
    memories are boosted in ranking.

    Args:
        query: Search query text.
        current_file: Optional current file for context boosting.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of MemoryObject instances sorted by relevance.
    """
    project_id = _scope_or_none(project_id)

    with db_connection() as conn:
        ensure_initialized(conn)

        # Fetch full memory objects, widening the ranking when the
        # stored-project belt discards stale ids. `graph_boosted_search`
        # truncates to the limit it is given BEFORE this loop can filter, so
        # the widening has to reach back into that call, not only into the
        # slice below.
        fetch = limit
        while True:
            ranked_results = graph_boosted_search(
                conn, query, current_file, project_id, fetch
            )

            memories = []
            for memory_id, score in ranked_results:
                memory_dict = get_memory(conn, memory_id)
                if memory_dict:
                    # BELT, BESIDE THE CHOKE POINT. vector_search already
                    # confirms every hit and the keyword leg filters in SQL,
                    # but the graph-boosted leg adds ids from file links
                    # WITHOUT a project check, and this loop is the last
                    # place an id becomes a returned memory. Confirming here
                    # means no id source can leak a row from another project.
                    # `project_id is None` unscoped.
                    if (
                        project_id is not None
                        and memory_dict.get("project_id") != project_id
                    ):
                        continue
                    # Get associated files
                    from .graph import get_files_for_memory
                    files_data = get_files_for_memory(
                        conn, memory_id, memory_dict.get("project_id")
                    )
                    file_paths = [f["path"] for f in files_data]

                    memory_obj = memory_from_db_row(memory_dict, file_paths)
                    memories.append(memory_obj)

            if (
                project_id is None
                or len(memories) >= limit
                or len(ranked_results) < fetch
                or fetch >= GRAPH_CONFIRMATION_FETCH_CAP
            ):
                return memories[:limit]
            fetch = min(fetch * 2, GRAPH_CONFIRMATION_FETCH_CAP)


def find_similar_memories(
    memory_id: str,
    project_id: Optional[str] = None,
    limit: int = 20
) -> List[MemoryObject]:
    """
    Find memories similar to a given memory.

    Uses the memory's content to search for related memories.

    Args:
        memory_id: ID of the reference memory.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of similar MemoryObject instances (excludes the reference memory).
    """
    with db_connection() as conn:
        ensure_initialized(conn)

        # Get the reference memory
        ref_memory = get_memory(conn, memory_id)
        if not ref_memory:
            return []

        # Generate search text from the reference memory
        search_text = generate_embedding_text(ref_memory)
        if not search_text:
            return []

        # Search for similar memories
        results = graph_enhanced_search(
            search_text,
            current_file=None,
            project_id=project_id,
            limit=limit + 1  # Get one extra to exclude self
        )

        # Filter out the reference memory
        return [m for m in results if m.id != memory_id][:limit]


def search_by_file(
    file_path: str,
    project_id: Optional[str] = None,
    limit: int = 10
) -> List[MemoryObject]:
    """
    Find all memories related to a specific file.

    Returns memories directly linked to the file and memories
    linked through graph relationships.

    Args:
        file_path: File path to search for.
        project_id: Optional project filter.
        limit: Maximum number of results.

    Returns:
        List of related MemoryObject instances.
    """
    project_id = _scope_or_none(project_id)

    with db_connection() as conn:
        ensure_initialized(conn)

        # Get all related memory IDs
        memory_ids = _get_graph_related_memories(conn, file_path, project_id)

        # Fetch memory objects through a widening window. The old
        # `list(memory_ids)[:limit]` truncated BEFORE the stored-project
        # confirmation, so a stale link cost a slot the store could fill; the
        # window grows until enough confirmed memories exist, the relation set
        # is exhausted, or the scope is unscoped. Its natural cap is the size
        # of the set, so a pathological store cannot spin.
        ordered = list(memory_ids)
        memories = []
        window = limit
        index = 0
        while True:
            for memory_id in ordered[index:window]:
                memory_dict = get_memory(conn, memory_id)
                if memory_dict:
                    # THE GRAPH LOOKUP SCOPES THE FILE, NOT THE MEMORY, and
                    # get_memories_for_file now also confirms the memory's
                    # stored project. This is still the last place an id
                    # becomes a returned memory, so a re-keyed row cannot be
                    # served to its old project by any id source.
                    if (
                        project_id is not None
                        and memory_dict.get("project_id") != project_id
                    ):
                        continue
                    from .graph import get_files_for_memory
                    files_data = get_files_for_memory(
                        conn, memory_id, memory_dict.get("project_id")
                    )
                    file_paths = [f["path"] for f in files_data]

                    memory_obj = memory_from_db_row(memory_dict, file_paths)
                    memories.append(memory_obj)

            index = window
            if (
                project_id is None
                or len(memories) >= limit
                or index >= len(ordered)
            ):
                return memories[:limit]
            window = min(window * 2, len(ordered))


def _sqlite_vec_available() -> bool:
    """Report whether sqlite-vec can be IMPORTED.

    A module-level name on purpose, so a test can patch this term at the same
    seam it already patches SQLITE_EXTENSIONS_ENABLED. Called inline, this
    check would be unpatchable, and a test that patches the other two terms
    would silently pass here only because sqlite-vec happens to be installed --
    an environment-dependent result wearing the look of a pinned one.

    IMPORTABILITY, not loadability. The exit this term describes is an
    `except ImportError`; a `sqlite_vec.load(conn)` failure is an incident
    reported as `fault`, not a claim about what this process can do.
    """
    try:
        return importlib.util.find_spec("sqlite_vec") is not None
    except (ImportError, ValueError):
        # ValueError: the name is present in sys.modules but has no spec.
        return False


def get_search_capabilities() -> Dict[str, Any]:
    """
    Get information about available search capabilities.

    Vector storage requires all three:
    1. SQLITE_EXTENSIONS_ENABLED (pysqlite3 installed)
    2. sqlite-vec importable
    3. Embedding generation available (model + backend)

    Returns:
        Dictionary describing search features and their availability.
    """
    embedding_status = check_embedding_availability()

    # Vector storage requires extension loading, sqlite-vec, AND embedding
    # generation. sqlite-vec was missing from this predicate, and its absence
    # made the report FALSE ON BOTH SIDES: vector_search does the same import
    # and returns [] on ImportError, so semantic search answered nothing while
    # this function still called the mode "semantic". One subject, so the term
    # belongs here rather than in a second predicate on the save side.
    vector_storage_available = (
        SQLITE_EXTENSIONS_ENABLED
        and _sqlite_vec_available()
        and embedding_status["available"]
    )

    # Determine active search mode for clear status reporting
    if vector_storage_available:
        search_mode = "semantic"
    else:
        search_mode = "keyword"

    return {
        "semantic_search": vector_storage_available,
        "keyword_search": True,  # Always available
        "graph_boosting": True,  # Always available
        "search_mode": search_mode,
        "sqlite_extensions_enabled": SQLITE_EXTENSIONS_ENABLED,
        "embedding_backend": embedding_status.get("backend"),
        "embedding_dimension": embedding_status.get("embedding_dimension"),
        "details": {
            "pysqlite3_available": SQLITE_EXTENSIONS_ENABLED,
            "embeddings_available": embedding_status["available"],
        }
    }
