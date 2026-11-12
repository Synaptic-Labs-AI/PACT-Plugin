"""
Tests for pact-memory/scripts/graph.py — file relationship tracking and graph queries.

Tests cover:
1. _normalize_path: absolute vs relative, ~ expansion
2. track_file: new file creation, existing file update, project scoping
3. get_file_id: found, not found, project scoping
4. get_file_by_id: found, not found
5. list_tracked_files: all, project filter, limit
6. link_memory_to_file: new link, duplicate link
7. link_memory_to_files: multiple files, partial duplicates
8. link_memory_to_paths: tracks and links
9. get_files_for_memory: linked files, no files
10. get_memories_for_file: linked memories, no memories
11. get_memories_for_files: multiple paths
12. add_file_relation: new relation, duplicate
13. get_file_relations: outgoing, incoming, both, untracked
14. get_related_files: BFS traversal, depth limits, untracked start
15. get_related_files_via_memories: shared memory links, untracked
16. get_file_context: tracked, untracked
17. get_graph_stats: empty, populated, project filter
"""
from unittest.mock import patch

import pytest

from helpers import create_test_schema

# graph.py catches BOTH IntegrityError classes (`_INTEGRITY_ERRORS`: the
# stdlib one and `database.sqlite3`'s, which is pysqlite3 when installed), so
# either connection works. The `db_conn` fixture below uses the stdlib
# connection because that is the smaller dependency; the duplicate-write arms
# ALSO run on the production connection class in
# TestDuplicateWritesOnTheProductionConnection, since a stdlib-only matrix
# could not see a regression that named only one class.
import sqlite3


@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh database with schema, patching ensure_initialized to no-op."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    with patch("scripts.graph.ensure_initialized"):
        yield conn
    conn.close()


def _insert_memory(conn, memory_id, context="test", project_id=None):
    """Insert a memory row directly for test setup."""
    conn.execute(
        "INSERT INTO memories (id, context, project_id) VALUES (?, ?, ?)",
        (memory_id, context, project_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_absolute_path_unchanged(self):
        from scripts.graph import _normalize_path
        result = _normalize_path("/usr/local/bin/python")
        assert result == "/usr/local/bin/python"

    def test_relative_path_stays_relative(self):
        from scripts.graph import _normalize_path
        result = _normalize_path("src/app.py")
        assert result == "src/app.py"

    def test_tilde_expansion(self):
        from scripts.graph import _normalize_path
        result = _normalize_path("~/projects/app.py")
        assert "~" not in result
        assert "projects/app.py" in result


# ---------------------------------------------------------------------------
# track_file
# ---------------------------------------------------------------------------

class TestTrackFile:
    def test_creates_new_file(self, db_conn):
        from scripts.graph import track_file
        file_id = track_file(db_conn, "/src/app.py")
        assert file_id is not None
        assert len(file_id) > 0

    def test_returns_existing_file(self, db_conn):
        from scripts.graph import track_file
        id1 = track_file(db_conn, "/src/app.py")
        id2 = track_file(db_conn, "/src/app.py")
        assert id1 == id2

    def test_updates_last_modified_on_retrack(self, db_conn):
        from scripts.graph import track_file
        track_file(db_conn, "/src/app.py")
        # Set a known old timestamp
        db_conn.execute(
            "UPDATE files SET last_modified = '2020-01-01' WHERE path = '/src/app.py'"
        )
        db_conn.commit()
        track_file(db_conn, "/src/app.py")
        cursor = db_conn.execute("SELECT last_modified FROM files WHERE path = '/src/app.py'")
        row = cursor.fetchone()
        assert row["last_modified"] != "2020-01-01"

    def test_project_scoping(self, db_conn):
        from scripts.graph import track_file
        id1 = track_file(db_conn, "/src/app.py", project_id="p1")
        id2 = track_file(db_conn, "/src/app.py", project_id="p2")
        assert id1 != id2

    def test_none_project_id(self, db_conn):
        from scripts.graph import track_file
        id1 = track_file(db_conn, "/src/app.py", project_id=None)
        id2 = track_file(db_conn, "/src/app.py", project_id=None)
        assert id1 == id2


# ---------------------------------------------------------------------------
# get_file_id
# ---------------------------------------------------------------------------

class TestGetFileId:
    def test_returns_id_for_tracked_file(self, db_conn):
        from scripts.graph import track_file, get_file_id
        expected = track_file(db_conn, "/src/app.py")
        assert get_file_id(db_conn, "/src/app.py") == expected

    def test_returns_none_for_untracked(self, db_conn):
        from scripts.graph import get_file_id
        assert get_file_id(db_conn, "/nonexistent.py") is None

    def test_respects_project_id(self, db_conn):
        from scripts.graph import track_file, get_file_id
        track_file(db_conn, "/src/app.py", project_id="p1")
        assert get_file_id(db_conn, "/src/app.py", project_id="p1") is not None
        assert get_file_id(db_conn, "/src/app.py", project_id="p2") is None


# ---------------------------------------------------------------------------
# get_file_by_id
# ---------------------------------------------------------------------------

class TestGetFileById:
    def test_returns_file_dict(self, db_conn):
        from scripts.graph import track_file, get_file_by_id
        file_id = track_file(db_conn, "/src/app.py")
        result = get_file_by_id(db_conn, file_id)
        assert result is not None
        assert result["path"] == "/src/app.py"

    def test_returns_none_for_missing(self, db_conn):
        from scripts.graph import get_file_by_id
        assert get_file_by_id(db_conn, "nonexistent-id") is None


# ---------------------------------------------------------------------------
# list_tracked_files
# ---------------------------------------------------------------------------

class TestListTrackedFiles:
    def test_lists_all_files(self, db_conn):
        from scripts.graph import track_file, list_tracked_files
        track_file(db_conn, "/src/a.py")
        track_file(db_conn, "/src/b.py")
        result = list_tracked_files(db_conn)
        assert len(result) == 2

    def test_filters_by_project(self, db_conn):
        from scripts.graph import track_file, list_tracked_files
        track_file(db_conn, "/src/a.py", project_id="p1")
        track_file(db_conn, "/src/b.py", project_id="p2")
        result = list_tracked_files(db_conn, project_id="p1")
        assert len(result) == 1
        assert result[0]["path"] == "/src/a.py"

    def test_respects_limit(self, db_conn):
        from scripts.graph import track_file, list_tracked_files
        for i in range(5):
            track_file(db_conn, f"/src/file{i}.py")
        assert len(list_tracked_files(db_conn, limit=3)) == 3

    def test_empty_database(self, db_conn):
        from scripts.graph import list_tracked_files
        assert list_tracked_files(db_conn) == []


# ---------------------------------------------------------------------------
# link_memory_to_file
# ---------------------------------------------------------------------------

class TestLinkMemoryToFile:
    def test_creates_link(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file
        _insert_memory(db_conn, "mem-1")
        file_id = track_file(db_conn, "/src/app.py")
        assert link_memory_to_file(db_conn, "mem-1", file_id) is True

    def test_duplicate_returns_false(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file
        _insert_memory(db_conn, "mem-1")
        file_id = track_file(db_conn, "/src/app.py")
        link_memory_to_file(db_conn, "mem-1", file_id)
        assert link_memory_to_file(db_conn, "mem-1", file_id) is False

    def test_different_relationships_allowed(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file
        _insert_memory(db_conn, "mem-1")
        file_id = track_file(db_conn, "/src/app.py")
        # memory_files PK is (memory_id, file_id), so second insert with
        # different relationship will fail as duplicate
        link_memory_to_file(db_conn, "mem-1", file_id, "modified")
        result = link_memory_to_file(db_conn, "mem-1", file_id, "referenced")
        assert result is False  # PK collision on (memory_id, file_id)


# ---------------------------------------------------------------------------
# link_memory_to_files
# ---------------------------------------------------------------------------

class TestLinkMemoryToFiles:
    def test_links_multiple(self, db_conn):
        from scripts.graph import track_file, link_memory_to_files
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        count = link_memory_to_files(db_conn, "mem-1", [id1, id2])
        assert count == 2

    def test_skips_duplicates(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, link_memory_to_files
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        count = link_memory_to_files(db_conn, "mem-1", [id1, id2])
        assert count == 1  # Only b.py is new

    def test_empty_list(self, db_conn):
        from scripts.graph import link_memory_to_files
        count = link_memory_to_files(db_conn, "mem-1", [])
        assert count == 0


# ---------------------------------------------------------------------------
# link_memory_to_paths
# ---------------------------------------------------------------------------

class TestLinkMemoryToPaths:
    def test_tracks_and_links(self, db_conn):
        from scripts.graph import link_memory_to_paths, get_file_id
        _insert_memory(db_conn, "mem-1")
        count = link_memory_to_paths(db_conn, "mem-1", ["/src/a.py", "/src/b.py"])
        assert count == 2
        assert get_file_id(db_conn, "/src/a.py") is not None
        assert get_file_id(db_conn, "/src/b.py") is not None


# ---------------------------------------------------------------------------
# get_files_for_memory
# ---------------------------------------------------------------------------

class TestGetFilesForMemory:
    def test_returns_linked_files(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_files_for_memory
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-1", id2, "referenced")
        files = get_files_for_memory(db_conn, "mem-1")
        assert len(files) == 2
        paths = {f["path"] for f in files}
        assert "/src/a.py" in paths
        assert "/src/b.py" in paths

    def test_returns_empty_for_no_links(self, db_conn):
        from scripts.graph import get_files_for_memory
        _insert_memory(db_conn, "mem-1")
        assert get_files_for_memory(db_conn, "mem-1") == []


# ---------------------------------------------------------------------------
# get_memories_for_file
# ---------------------------------------------------------------------------

class TestGetMemoriesForFile:
    def test_returns_memory_ids(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_memories_for_file
        _insert_memory(db_conn, "mem-1")
        _insert_memory(db_conn, "mem-2")
        file_id = track_file(db_conn, "/src/app.py")
        link_memory_to_file(db_conn, "mem-1", file_id)
        link_memory_to_file(db_conn, "mem-2", file_id)
        mems = get_memories_for_file(db_conn, file_id)
        assert set(mems) == {"mem-1", "mem-2"}

    def test_returns_empty_for_no_links(self, db_conn):
        from scripts.graph import track_file, get_memories_for_file
        file_id = track_file(db_conn, "/src/app.py")
        assert get_memories_for_file(db_conn, file_id) == []


# ---------------------------------------------------------------------------
# get_memories_for_files
# ---------------------------------------------------------------------------

class TestGetMemoriesForFiles:
    def test_aggregates_across_paths(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_memories_for_files
        _insert_memory(db_conn, "mem-1")
        _insert_memory(db_conn, "mem-2")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-2", id2)
        mems = get_memories_for_files(db_conn, ["/src/a.py", "/src/b.py"])
        assert set(mems) == {"mem-1", "mem-2"}

    def test_skips_untracked_paths(self, db_conn):
        from scripts.graph import get_memories_for_files
        assert get_memories_for_files(db_conn, ["/nonexistent.py"]) == []

    def test_deduplicates_memories(self, db_conn):
        from scripts.graph import track_file, link_memory_to_file, get_memories_for_files
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-1", id2)
        mems = get_memories_for_files(db_conn, ["/src/a.py", "/src/b.py"])
        assert mems == ["mem-1"]

    def test_ids_are_sorted_not_set_ordered(self, db_conn):
        """The ids accumulate in a set, so returning `list(set)` made the
        order change with the process hash seed. A caller that truncates then
        keeps a different subset per run.

        RED WHEN: the return is `list(memory_ids)` — the ids here would have
        to come out in ascending hash order for this to pass.
        """
        from scripts.graph import track_file, link_memory_to_file, get_memories_for_files
        memory_ids = (
            "m-delta", "m-alpha", "m-foxtrot", "m-charlie", "m-bravo", "m-echo",
        )
        for memory_id in memory_ids:
            _insert_memory(db_conn, memory_id)
        file_id = track_file(db_conn, "/src/a.py")
        for memory_id in memory_ids:
            link_memory_to_file(db_conn, memory_id, file_id)
        mems = get_memories_for_files(db_conn, ["/src/a.py"])
        assert mems == sorted(mems), f"set-ordered result: {mems}"
        assert mems == [
            "m-alpha", "m-bravo", "m-charlie", "m-delta", "m-echo", "m-foxtrot",
        ]


# ---------------------------------------------------------------------------
# add_file_relation
# ---------------------------------------------------------------------------

class TestAddFileRelation:
    def test_creates_relation(self, db_conn):
        from scripts.graph import add_file_relation
        result = add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        assert result is True

    def test_duplicate_returns_false(self, db_conn):
        from scripts.graph import add_file_relation
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        result = add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        assert result is False

    def test_different_relationship_type_allowed(self, db_conn):
        from scripts.graph import add_file_relation
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        result = add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "calls")
        assert result is True  # Different relationship = different PK


# ---------------------------------------------------------------------------
# get_file_relations
# ---------------------------------------------------------------------------

class TestGetFileRelations:
    def test_outgoing_relations(self, db_conn):
        from scripts.graph import add_file_relation, get_file_relations
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        result = get_file_relations(db_conn, "/src/app.py", direction="outgoing")
        assert len(result) == 1
        assert result[0]["path"] == "/src/utils.py"
        assert result[0]["direction"] == "outgoing"

    def test_incoming_relations(self, db_conn):
        from scripts.graph import add_file_relation, get_file_relations
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        result = get_file_relations(db_conn, "/src/utils.py", direction="incoming")
        assert len(result) == 1
        assert result[0]["path"] == "/src/app.py"
        assert result[0]["direction"] == "incoming"

    def test_both_directions(self, db_conn):
        from scripts.graph import add_file_relation, get_file_relations
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        add_file_relation(db_conn, "/src/tests.py", "/src/app.py", "tests")
        result = get_file_relations(db_conn, "/src/app.py", direction="both")
        assert len(result) == 2

    def test_untracked_file_returns_empty(self, db_conn):
        from scripts.graph import get_file_relations
        assert get_file_relations(db_conn, "/nonexistent.py") == []


# ---------------------------------------------------------------------------
# get_related_files (BFS traversal)
# ---------------------------------------------------------------------------

class TestGetRelatedFiles:
    def test_finds_direct_neighbors(self, db_conn):
        from scripts.graph import add_file_relation, get_related_files
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        result = get_related_files(db_conn, "/a.py")
        assert "/b.py" in result

    def test_finds_two_hop_neighbors(self, db_conn):
        from scripts.graph import add_file_relation, get_related_files
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        add_file_relation(db_conn, "/b.py", "/c.py", "imports")
        result = get_related_files(db_conn, "/a.py", max_depth=2)
        assert "/b.py" in result
        assert "/c.py" in result

    def test_respects_depth_limit(self, db_conn):
        from scripts.graph import add_file_relation, get_related_files
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        add_file_relation(db_conn, "/b.py", "/c.py", "imports")
        result = get_related_files(db_conn, "/a.py", max_depth=1)
        assert "/b.py" in result
        assert "/c.py" not in result

    def test_untracked_start_returns_empty(self, db_conn):
        from scripts.graph import get_related_files
        assert get_related_files(db_conn, "/nonexistent.py") == []

    def test_no_duplicates(self, db_conn):
        from scripts.graph import add_file_relation, get_related_files
        # Create cycle: a -> b -> a
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        add_file_relation(db_conn, "/b.py", "/a.py", "calls")
        result = get_related_files(db_conn, "/a.py")
        assert result.count("/b.py") == 1
        assert "/a.py" not in result  # Start node excluded

    def test_follows_bidirectional_edges(self, db_conn):
        from scripts.graph import add_file_relation, get_related_files
        # Only a -> b relation, but BFS checks both directions
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        result = get_related_files(db_conn, "/b.py", max_depth=1)
        assert "/a.py" in result

    def test_paths_are_sorted_not_visit_ordered(self, db_conn):
        """The BFS walks sets, so visit order changed with the process hash
        seed and a truncating caller kept a different subset per run.

        RED WHEN: the result is returned as accumulated rather than sorted.
        """
        from scripts.graph import add_file_relation, get_related_files
        add_file_relation(db_conn, "/a.py", "/d.py", "imports")
        add_file_relation(db_conn, "/a.py", "/b.py", "imports")
        add_file_relation(db_conn, "/d.py", "/c.py", "imports")
        add_file_relation(db_conn, "/b.py", "/e.py", "imports")
        result = get_related_files(db_conn, "/a.py", max_depth=2)
        assert sorted(result) == ["/b.py", "/c.py", "/d.py", "/e.py"], (
            f"the traversal did not reach the expected neighbours: {result}"
        )
        assert result == sorted(result), f"visit-ordered result: {result}"


# ---------------------------------------------------------------------------
# get_related_files_via_memories
# ---------------------------------------------------------------------------

class TestGetRelatedFilesViaMemories:
    def test_finds_files_with_shared_memory(self, db_conn):
        from scripts.graph import (
            track_file, link_memory_to_file, get_related_files_via_memories,
        )
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        id2 = track_file(db_conn, "/src/b.py")
        link_memory_to_file(db_conn, "mem-1", id1)
        link_memory_to_file(db_conn, "mem-1", id2)
        result = get_related_files_via_memories(db_conn, "/src/a.py")
        assert "/src/b.py" in result
        assert "/src/a.py" not in result  # Excludes self

    def test_untracked_file_returns_empty(self, db_conn):
        from scripts.graph import get_related_files_via_memories
        assert get_related_files_via_memories(db_conn, "/nonexistent.py") == []

    def test_no_shared_memory_returns_empty(self, db_conn):
        from scripts.graph import track_file, get_related_files_via_memories
        track_file(db_conn, "/src/a.py")
        assert get_related_files_via_memories(db_conn, "/src/a.py") == []

    def test_a_foreign_shared_memory_does_not_link_in_scope_files(self, db_conn):
        """Confirming the RETURNED file is not enough: the memory that joins
        the two files is another project's, so the caller cannot read it, and
        calling the pair "memory-related" invents a relation from a row the
        scope excludes. A path tracked under both buckets makes the returned
        file in-scope, which is normal inside a union.

        RED WHEN: the JOIN does not confirm m.project_id — the first assertion
        then returns ['/src/b.py'].
        """
        from scripts.graph import (
            track_file, link_memory_to_file, get_related_files_via_memories,
        )
        _insert_memory(db_conn, "m-scope", project_id="ours")
        _insert_memory(db_conn, "m-foreign", project_id="theirs")
        start = track_file(db_conn, "/src/a.py", "ours")
        other = track_file(db_conn, "/src/b.py", "ours")
        # The ONLY link between the two in-scope files is a foreign memory.
        link_memory_to_file(db_conn, "m-foreign", start)
        link_memory_to_file(db_conn, "m-foreign", other)
        assert get_related_files_via_memories(
            db_conn, "/src/a.py", "ours"
        ) == [], "a memory outside the scope linked two in-scope files"
        # POSITIVE CONTROL: the same pair, now joined by an in-scope memory.
        link_memory_to_file(db_conn, "m-scope", start)
        link_memory_to_file(db_conn, "m-scope", other)
        assert get_related_files_via_memories(
            db_conn, "/src/a.py", "ours"
        ) == ["/src/b.py"], "the in-scope memory's pair disappeared"
        # `None` keeps `get_file_ids`' meaning at the START: the NULL-project
        # row, not "no filter". A NULL-project pair still resolves — and,
        # because neither scope clause is emitted, so does every other file
        # the same memory links anywhere in the store.
        null_start = track_file(db_conn, "/src/c.py", None)
        null_other = track_file(db_conn, "/src/d.py", None)
        link_memory_to_file(db_conn, "m-scope", null_start)
        link_memory_to_file(db_conn, "m-scope", null_other)
        assert get_related_files_via_memories(
            db_conn, "/src/c.py"
        ) == ["/src/a.py", "/src/b.py", "/src/d.py"]


# ---------------------------------------------------------------------------
# get_file_context
# ---------------------------------------------------------------------------

class TestGetFileContext:
    def test_untracked_file(self, db_conn):
        from scripts.graph import get_file_context
        ctx = get_file_context(db_conn, "/nonexistent.py")
        assert ctx["tracked"] is False
        assert ctx["direct_relations"] == []
        assert ctx["memory_related_files"] == []
        assert ctx["memory_ids"] == []

    def test_tracked_file_with_relations(self, db_conn):
        from scripts.graph import (
            track_file, add_file_relation, link_memory_to_file, get_file_context,
        )
        _insert_memory(db_conn, "mem-1")
        track_file(db_conn, "/src/app.py")
        file_id = track_file(db_conn, "/src/app.py")
        add_file_relation(db_conn, "/src/app.py", "/src/utils.py", "imports")
        link_memory_to_file(db_conn, "mem-1", file_id)
        ctx = get_file_context(db_conn, "/src/app.py")
        assert ctx["tracked"] is True
        assert len(ctx["direct_relations"]) == 1
        assert "mem-1" in ctx["memory_ids"]


# ---------------------------------------------------------------------------
# get_graph_stats
# ---------------------------------------------------------------------------

class TestGetGraphStats:
    def test_empty_graph(self, db_conn):
        from scripts.graph import get_graph_stats
        stats = get_graph_stats(db_conn)
        assert stats["files"] == 0
        assert stats["memories"] == 0
        assert stats["memory_file_links"] == 0
        assert stats["file_relations"] == 0

    def test_populated_graph(self, db_conn):
        from scripts.graph import track_file, add_file_relation, link_memory_to_file, get_graph_stats
        _insert_memory(db_conn, "mem-1")
        id1 = track_file(db_conn, "/src/a.py")
        track_file(db_conn, "/src/b.py")
        add_file_relation(db_conn, "/src/a.py", "/src/b.py", "imports")
        link_memory_to_file(db_conn, "mem-1", id1)
        stats = get_graph_stats(db_conn)
        assert stats["files"] == 2
        assert stats["memories"] == 1
        assert stats["memory_file_links"] == 1
        assert stats["file_relations"] == 1

    def test_project_filter(self, db_conn):
        from scripts.graph import track_file, get_graph_stats
        track_file(db_conn, "/src/a.py", project_id="p1")
        track_file(db_conn, "/src/b.py", project_id="p2")
        _insert_memory(db_conn, "mem-1", project_id="p1")
        stats = get_graph_stats(db_conn, project_id="p1")
        assert stats["files"] == 1
        assert stats["memories"] == 1


# ---------------------------------------------------------------------------
# The production connection class (pysqlite3 when installed)
# ---------------------------------------------------------------------------

class TestDuplicateWritesOnTheProductionConnection:
    """A duplicate write must return False on the CONNECTION CLASS IN USE.

    `database.get_connection` returns pysqlite3 connections when it is
    installed, and `pysqlite3.dbapi2.IntegrityError` is an unrelated class
    from stdlib `sqlite3.IntegrityError`. A `except sqlite3.IntegrityError`
    written against the stdlib missed the duplicate on the production class
    and raised instead of returning False. The stdlib arms above pass either
    way, so they cannot see that regression; these arms use the factory's own
    connection.
    """

    @pytest.fixture
    def prod_conn(self, tmp_path):
        from scripts.database import get_connection, init_schema

        conn = get_connection(tmp_path / "graph_prod.db")
        init_schema(conn)
        conn.commit()
        yield conn
        conn.close()

    def test_the_fixture_is_on_the_production_connection_class(self, prod_conn):
        from scripts import database

        assert isinstance(prod_conn, database.sqlite3.Connection), (
            "the fixture did not receive the class database.get_connection "
            "returns"
        )
        if database.sqlite3 is sqlite3:
            pytest.skip(
                "pysqlite3 is not installed; the stdlib arms above already "
                "exercise this class"
            )
        # The two classes are unrelated, which is why the catch had to name
        # both. If they ever converge this arm is the alarm.
        assert database.sqlite3.IntegrityError is not sqlite3.IntegrityError

    def test_a_duplicate_link_returns_false_on_pysqlite3(self, prod_conn):
        from scripts.database import create_memory
        from scripts.graph import link_memory_to_file, track_file

        create_memory(prod_conn, {"id": "mem-prod", "context": "x",
                                  "project_id": "P"})
        file_id = track_file(prod_conn, "/src/app.py", "P")

        assert link_memory_to_file(prod_conn, "mem-prod", file_id) is True
        assert link_memory_to_file(prod_conn, "mem-prod", file_id) is False, (
            "a duplicate memory->file link raised instead of returning False "
            "on the production connection class"
        )

    def test_a_duplicate_relation_returns_false_on_pysqlite3(self, prod_conn):
        from scripts.graph import add_file_relation

        assert add_file_relation(
            prod_conn, "/src/app.py", "/src/utils.py", "imports"
        ) is True
        assert add_file_relation(
            prod_conn, "/src/app.py", "/src/utils.py", "imports"
        ) is False, (
            "a duplicate file relation raised instead of returning False on "
            "the production connection class"
        )
