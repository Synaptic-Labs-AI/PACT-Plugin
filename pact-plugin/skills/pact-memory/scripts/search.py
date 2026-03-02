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


def vector_search(
    conn: sqlite3.Connection,
    query: str,
    project_id: Optional[str] = None,
    limit: int = 10
) -> List[Tuple[str, float]]:
    """
    Perform semantic vector search on memories.

    Uses sqlite-vec for efficient similarity search. Requires:
    1. pysqlite3-binary (for extension loading)
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
        if project_id:
            sql = """
                SELECT memory_id, distance
                FROM vec_memories
                WHERE embedding MATCH ?
                AND project_id = ?
                ORDER BY distance
                LIMIT ?
            """
            cursor = conn.execute(sql, (embedding_blob, project_id, limit))
        else:
            sql = """
                SELECT memory_id, distance
                FROM vec_memories
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
            """
            cursor = conn.execute(sql, (embedding_blob, limit))

        return [(row[0], row[1]) for row in cursor.fetchall()]

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
        direct_memories = get_memories_for_file(conn, file_id)
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
    limit: int = 5
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
    with db_connection() as conn:
        ensure_initialized(conn)

        # Get ranked memory IDs
        ranked_results = graph_boosted_search(
            conn, query, current_file, project_id, limit
        )

        # Fetch full memory objects
        memories = []
        for memory_id, score in ranked_results:
            memory_dict = get_memory(conn, memory_id)
            if memory_dict:
                # Get associated files
                from .graph import get_files_for_memory
                files_data = get_files_for_memory(conn, memory_id)
                file_paths = [f["path"] for f in files_data]

                memory_obj = memory_from_db_row(memory_dict, file_paths)
                memories.append(memory_obj)

        return memories


def find_similar_memories(
    memory_id: str,
    project_id: Optional[str] = None,
    limit: int = 5
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
    with db_connection() as conn:
        ensure_initialized(conn)

        # Get all related memory IDs
        memory_ids = _get_graph_related_memories(conn, file_path, project_id)

        # Fetch and return memory objects
        memories = []
        for memory_id in list(memory_ids)[:limit]:
            memory_dict = get_memory(conn, memory_id)
            if memory_dict:
                from .graph import get_files_for_memory
                files_data = get_files_for_memory(conn, memory_id)
                file_paths = [f["path"] for f in files_data]

                memory_obj = memory_from_db_row(memory_dict, file_paths)
                memories.append(memory_obj)

        return memories


def get_search_capabilities() -> Dict[str, Any]:
    """
    Get information about available search capabilities.

    Vector storage requires both:
    1. SQLITE_EXTENSIONS_ENABLED (pysqlite3-binary installed)
    2. Embedding generation available (model + backend)

    Returns:
        Dictionary describing search features and their availability.
    """
    embedding_status = check_embedding_availability()

    # Vector storage requires extension loading AND embedding generation
    vector_storage_available = (
        SQLITE_EXTENSIONS_ENABLED and embedding_status["available"]
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
