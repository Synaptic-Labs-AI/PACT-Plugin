"""
Tests for pact-memory/scripts/database.py — SQLite database layer.

Tests cover:
1. Schema initialization: tables, indexes, migrations
2. JSON serialization/deserialization helpers
3. Memory CRUD: create, get, update, delete, list
4. Text search: substring matching, project filtering, LIKE wildcard escaping
5. Database maintenance: count, integrity check
6. generate_id: uniqueness
7. ALLOWED_COLUMNS whitelist: unknown columns filtered by update_memory
8. TOCTOU mitigation: atomic file pre-creation, race handling, WAL sidecar chmod
"""
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import create_test_schema
from scripts import database as scripts_database
from scripts import memory_api
from scripts.config import store_scope
from scripts.database import (
    PrefixTooShortError,
    db_connection,
    ensure_initialized,
    get_memory,
    init_schema,
)
from scripts.graph import get_file_id, track_file
from scripts.memory_api import PACTMemory, ProjectScopeDisagreementError
import scripts.embedding_catchup as embedding_catchup
import scripts.search as memory_search

# Use the same sqlite3 module that database.py uses (pysqlite3 if available)
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh database with schema, patching ensure_initialized to no-op."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_test_schema(conn)
    with patch("scripts.database.ensure_initialized"):
        yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

class TestTimestampColumnsAreRequired:
    """Built through PRODUCTION's own `ensure_initialized`, NOT the fixture.

    `db_conn` and every other schema in this suite is a test-local copy, so
    none of them can see a change to production's CREATE TABLE. This class
    exists because that makes a green suite no evidence about this column.
    """

    def _production_schema(self, tmp_path):
        from scripts.database import ensure_initialized
        conn = sqlite3.connect(str(tmp_path / "prod.db"))
        ensure_initialized(conn)
        return conn

    @pytest.mark.parametrize("column", ["created_at", "updated_at"])
    def test_an_insert_omitting_a_timestamp_is_refused(self, tmp_path, column):
        conn = self._production_schema(tmp_path)
        supplied = "updated_at" if column == "created_at" else "created_at"
        with pytest.raises(sqlite3.IntegrityError, match=f"NOT NULL.*{column}"):
            conn.execute(
                f"INSERT INTO memories (id, {supplied}) VALUES ('x', '2026-01-01')"
            )

    def test_supplying_both_is_accepted(self, tmp_path):
        """Control: the refusal above is about the omission, not the insert."""
        conn = self._production_schema(tmp_path)
        conn.execute(
            "INSERT INTO memories (id, created_at, updated_at)"
            " VALUES ('x', '2026-01-01', '2026-01-01')"
        )
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1


class TestSchemaInit:
    def test_creates_memories_table(self, db_conn):
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        )
        assert cursor.fetchone() is not None

    def test_creates_files_table(self, db_conn):
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        )
        assert cursor.fetchone() is not None

    def test_creates_memory_files_table(self, db_conn):
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_files'"
        )
        assert cursor.fetchone() is not None

    def test_creates_file_relations_table(self, db_conn):
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='file_relations'"
        )
        assert cursor.fetchone() is not None

    def test_creates_indexes(self, db_conn):
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_memories_project'"
        )
        assert cursor.fetchone() is not None


class TestMigrateCTFields:
    def test_adds_ct_columns(self, tmp_path):
        """CT columns should be added to existing DBs without them."""
        from scripts.database import _migrate_ct_fields
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, context TEXT, goal TEXT, created_at TEXT
            )
        """)
        conn.commit()
        _migrate_ct_fields(conn)
        conn.execute("SELECT reasoning_chains, agreements_reached, disagreements_resolved FROM memories")
        conn.close()


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

class TestJsonSerialization:
    def test_serialize_json_fields(self):
        from scripts.database import _serialize_json_fields
        data = {
            "context": "test",
            "active_tasks": [{"task": "T1"}],
            "lessons_learned": ["L1"],
        }
        result = _serialize_json_fields(data)
        assert result["context"] == "test"
        assert isinstance(result["active_tasks"], str)
        assert json.loads(result["active_tasks"]) == [{"task": "T1"}]

    def test_serialize_leaves_none_alone(self):
        from scripts.database import _serialize_json_fields
        result = _serialize_json_fields({"active_tasks": None, "context": "test"})
        assert result["active_tasks"] is None

    def test_serialize_leaves_strings_alone(self):
        from scripts.database import _serialize_json_fields
        result = _serialize_json_fields({"active_tasks": "already a string"})
        assert result["active_tasks"] == "already a string"

    def test_deserialize_json_fields(self):
        from scripts.database import _deserialize_json_fields
        data = {
            "context": "test",
            "active_tasks": json.dumps([{"task": "T1"}]),
            "lessons_learned": json.dumps(["L1"]),
        }
        result = _deserialize_json_fields(data)
        assert result["active_tasks"] == [{"task": "T1"}]
        assert result["lessons_learned"] == ["L1"]

    def test_deserialize_invalid_json_keeps_string(self):
        from scripts.database import _deserialize_json_fields
        result = _deserialize_json_fields({"active_tasks": "not json {"})
        assert result["active_tasks"] == "not json {"

    def test_deserialize_non_string_untouched(self):
        from scripts.database import _deserialize_json_fields
        result = _deserialize_json_fields({"active_tasks": [1, 2, 3]})
        assert result["active_tasks"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Memory CRUD
# ---------------------------------------------------------------------------

class TestCreateMemory:
    def test_creates_and_returns_id(self, db_conn):
        from scripts.database import create_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        assert mem_id is not None
        assert len(mem_id) > 0

    def test_creates_with_custom_id(self, db_conn):
        from scripts.database import create_memory
        mem_id = create_memory(db_conn, {"id": "custom-123", "context": "Test"})
        assert mem_id == "custom-123"

    def test_creates_with_json_fields(self, db_conn):
        """
        Sub-object fields are canonicalized via _canonicalize_dict_item
        on the save path (bug 3 part 2, #374). The round-trip through
        TaskItem.from_dict(strict=True).to_dict() populates default values
        such as status='pending', so the stored shape is the canonical form
        — symmetric with update_memory's merge path.
        """
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {
            "context": "Test",
            "active_tasks": [{"task": "T1"}],
            "lessons_learned": ["L1"],
            "decisions": [{"decision": "D1"}],
        })
        result = get_memory(db_conn, mem_id)
        assert result is not None
        # Canonical TaskItem shape: task + status (status defaulted to 'pending').
        assert result["active_tasks"] == [{"task": "T1", "status": "pending"}]
        assert result["lessons_learned"] == ["L1"]


class TestGetMemory:
    def test_returns_memory(self, db_conn):
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test context"})
        result = get_memory(db_conn, mem_id)
        assert result is not None
        assert result["context"] == "Test context"

    def test_returns_none_for_missing(self, db_conn):
        from scripts.database import get_memory
        assert get_memory(db_conn, "nonexistent") is None


class TestResolveMemoryIdPrefix:
    """Storage-layer tests for resolve_memory_id_prefix."""

    def test_unique_prefix_returns_full_id(self, db_conn):
        from scripts.database import create_memory, resolve_memory_id_prefix
        mem_id = create_memory(
            db_conn,
            {"id": "abc12345" + "0" * 24, "context": "unique"},
        )
        assert resolve_memory_id_prefix(db_conn, "abc12345") == mem_id

    def test_ambiguous_prefix_raises(self, db_conn):
        from scripts.database import (
            create_memory,
            resolve_memory_id_prefix,
            AmbiguousPrefixError,
        )
        id_a = "abcd123" + "1" + "0" * 24
        id_b = "abcd123" + "2" + "0" * 24
        # Inserted in REVERSE lexicographic order to verify ORDER BY id sorts
        # the matches independently of insert order.
        create_memory(db_conn, {"id": id_b, "context": "second match"})
        create_memory(db_conn, {"id": id_a, "context": "first match"})

        with pytest.raises(AmbiguousPrefixError) as exc_info:
            resolve_memory_id_prefix(db_conn, "abcd123")

        # Direct list equality preserves the ORDER BY id contract — sorting
        # both sides would mask a regression that drops the ORDER BY clause.
        match_ids = [m["id"] for m in exc_info.value.matches]
        assert match_ids == [id_a, id_b]
        # Descriptor snippet attached for terse disambiguation
        contexts = {m["id"]: m["context"] for m in exc_info.value.matches}
        assert contexts[id_a] == "first match"
        assert contexts[id_b] == "second match"

    def test_no_match_returns_none(self, db_conn):
        from scripts.database import resolve_memory_id_prefix
        assert resolve_memory_id_prefix(db_conn, "fffffff") is None

    def test_too_short_prefix_raises(self, db_conn):
        from scripts.database import (
            resolve_memory_id_prefix,
            PrefixTooShortError,
        )
        with pytest.raises(PrefixTooShortError) as exc_info:
            resolve_memory_id_prefix(db_conn, "abc123")
        assert exc_info.value.prefix == "abc123"
        assert exc_info.value.minimum == 7

    def test_non_hex_prefix_returns_none(self, db_conn):
        """Non-hex prefix characters (e.g., punctuation) yield no match.

        The range scan operates on a literal `id >= prefix AND id < prefix+'g'`
        bound; a prefix outside the lowercase-hex alphabet falls outside the
        range of any real ID, so the lookup correctly returns None. Guards
        against attempts to inject SQL metacharacters via the prefix arg.
        """
        from scripts.database import create_memory, resolve_memory_id_prefix
        create_memory(db_conn, {"id": "deadbeef" + "0" * 24, "context": "x"})
        # '%' < '0' in ASCII; a prefix starting with '%' falls outside any
        # hex ID's range. Same outcome a literal-meta-char attempt would have
        # produced under the prior LIKE-with-escape implementation.
        assert resolve_memory_id_prefix(db_conn, "%deadbe") is None

    def test_empty_string_prefix_raises_too_short(self, db_conn):
        """Empty input hits the minimum-length gate before the SQL query."""
        from scripts.database import (
            resolve_memory_id_prefix,
            PrefixTooShortError,
        )
        with pytest.raises(PrefixTooShortError) as exc_info:
            resolve_memory_id_prefix(db_conn, "")
        assert exc_info.value.prefix == ""
        assert exc_info.value.minimum == 7

    def test_exactly_min_length_unique_match(self, db_conn):
        """A 7-char prefix (the boundary) resolves when the match is unique."""
        from scripts.database import (
            create_memory,
            resolve_memory_id_prefix,
            MIN_PREFIX_LENGTH,
        )
        mem_id = create_memory(
            db_conn,
            {"id": "feed123" + "0" * 25, "context": "boundary"},
        )
        # Exactly MIN_PREFIX_LENGTH chars — no padding above the floor
        prefix = "feed123"
        assert len(prefix) == MIN_PREFIX_LENGTH
        assert resolve_memory_id_prefix(db_conn, prefix) == mem_id

    def test_exactly_full_length_no_match_returns_none(self, db_conn):
        """A 32-char input bypasses the resolver but still returns None on miss.

        Guards against an off-by-one regression in the `len < MEMORY_ID_LENGTH`
        gate: a 32-char nonexistent ID must not raise, must return None.
        """
        from scripts.database import resolve_memory_id_prefix
        full_length_miss = "f" * 32
        assert resolve_memory_id_prefix(db_conn, full_length_miss) is None

    def test_uppercase_prefix_resolves_to_lowercase_stored_id(self, db_conn):
        """Prefix lookup is case-insensitive; the returned ID is the stored form.

        Memory IDs are produced by secrets.token_hex (lowercase). A user
        typing an uppercase prefix must still resolve to the canonical
        lowercase ID — disambiguation, downstream API calls, and CLI
        envelopes all key off the storage form.
        """
        from scripts.database import create_memory, resolve_memory_id_prefix
        stored_id = "abcd1234" + "0" * 24
        create_memory(db_conn, {"id": stored_id, "context": "case test"})
        resolved = resolve_memory_id_prefix(db_conn, "ABCD1234")
        assert resolved == stored_id

    def test_ambiguous_small_set_not_truncated(self, db_conn):
        """Ambiguous match below the cap reports matches_capped=False, exact total."""
        from scripts.database import (
            create_memory,
            resolve_memory_id_prefix,
            AmbiguousPrefixError,
        )
        ids = [f"smalset{i}" + "0" * 24 for i in range(3)]  # 7-char shared prefix
        for mid in ids:
            create_memory(db_conn, {"id": mid, "context": f"row-{mid[-25]}"})

        with pytest.raises(AmbiguousPrefixError) as exc_info:
            resolve_memory_id_prefix(db_conn, "smalset")

        assert exc_info.value.matches_capped is False
        assert exc_info.value.total_matches == 3
        assert len(exc_info.value.matches) == 3

    def test_ambiguous_pathological_set_truncated_at_cap(self, db_conn):
        """When matches exceed AMBIGUOUS_MATCH_CAP, list is capped + matches_capped=True.

        total_matches reflects the actual COUNT(*) from the storage layer,
        not len(matches). Guards regression where the cap query and the
        count query diverge.
        """
        from scripts.database import (
            create_memory,
            resolve_memory_id_prefix,
            AmbiguousPrefixError,
            AMBIGUOUS_MATCH_CAP,
        )
        # Seed AMBIGUOUS_MATCH_CAP + 1 rows under shared 7-char prefix "patho00"
        seeded = AMBIGUOUS_MATCH_CAP + 1
        for i in range(seeded):
            mid = f"patho00{i:025d}"  # 7-char prefix + 25-char numeric pad = 32
            assert len(mid) == 32
            create_memory(db_conn, {"id": mid, "context": f"row-{i}"})

        with pytest.raises(AmbiguousPrefixError) as exc_info:
            resolve_memory_id_prefix(db_conn, "patho00")

        assert exc_info.value.matches_capped is True
        assert exc_info.value.total_matches == seeded
        assert len(exc_info.value.matches) == AMBIGUOUS_MATCH_CAP

    def test_range_scan_does_not_overmatch_at_boundary(self, db_conn):
        """Range scan must NOT match rows where the next char exceeds the prefix.

        Locks the H2 contract independent of internals (LIKE vs range): a
        prefix `abcdef0` should match `abcdef0...` but NOT `abcdef1...`,
        even though both share the same first 6 chars. A buggy
        upper-bound construction (e.g., open-ended scan, off-by-one cap)
        would over-match here.
        """
        from scripts.database import (
            create_memory,
            resolve_memory_id_prefix,
        )
        id_at_prefix = "abcdef0" + "0" * 25  # matches prefix "abcdef0"
        id_above_prefix = "abcdef1" + "0" * 25  # one char higher at the boundary
        create_memory(db_conn, {"id": id_at_prefix, "context": "in-range"})
        create_memory(db_conn, {"id": id_above_prefix, "context": "above-range"})

        # Unique match — only the in-range row resolves
        assert resolve_memory_id_prefix(db_conn, "abcdef0") == id_at_prefix

    def test_generate_id_alphabet_invariant(self):
        """Every generate_id() output must stay within the lowercase-hex alphabet.

        The resolver's range-scan upper bound is `prefix + _PREFIX_UPPER_BOUND_CHAR`
        (currently 'g' — the smallest ASCII char above 'f'). That bound is
        correct ONLY if generate_id produces strictly lowercase hex [0-9a-f].
        If the alphabet ever broadens (e.g., to base32 or full alphanumeric),
        the upper-bound constant must be revisited; this test fails loudly
        in that case.
        """
        from scripts.database import generate_id, _PREFIX_UPPER_BOUND_CHAR

        # 100 IDs is enough to surface a misconfigured charset without
        # making the test slow; secrets.token_hex is uniform over [0-9a-f].
        for _ in range(100):
            mid = generate_id()
            for ch in mid:
                assert ch < _PREFIX_UPPER_BOUND_CHAR, (
                    f"generate_id produced char {ch!r} (in {mid!r}) outside the "
                    f"strictly-below-{_PREFIX_UPPER_BOUND_CHAR!r} alphabet that "
                    "the range-scan upper bound depends on. If charset broadened "
                    "intentionally, update _PREFIX_UPPER_BOUND_CHAR."
                )

    def test_long_context_marked_truncated_per_match(self, db_conn):
        """Per-match `context_truncated` flag distinguishes long vs short contexts.

        Long contexts (> descriptor_chars) are truncated to descriptor_chars
        and flagged `context_truncated=True`; short contexts pass through
        unchanged with `context_truncated=False`. The flag is per-match,
        not per-list — guards a regression where the flag gets set globally
        based on any-row-truncated.
        """
        from scripts.database import (
            create_memory,
            resolve_memory_id_prefix,
            AmbiguousPrefixError,
        )
        descriptor_chars = 60  # default arg of resolve_memory_id_prefix
        long_id = "trunca0" + "1" + "0" * 24
        short_id = "trunca0" + "2" + "0" * 24
        long_context = "x" * 200
        short_context = "short"
        create_memory(db_conn, {"id": long_id, "context": long_context})
        create_memory(db_conn, {"id": short_id, "context": short_context})

        with pytest.raises(AmbiguousPrefixError) as exc_info:
            resolve_memory_id_prefix(db_conn, "trunca0")

        by_id = {m["id"]: m for m in exc_info.value.matches}
        assert by_id[long_id]["context_truncated"] is True
        assert len(by_id[long_id]["context"]) == descriptor_chars
        assert by_id[long_id]["context"] == "x" * descriptor_chars

        assert by_id[short_id]["context_truncated"] is False
        assert by_id[short_id]["context"] == short_context


class TestUpdateMemory:
    def test_updates_fields(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Old"})
        assert update_memory(db_conn, mem_id, {"context": "New"}) is True
        assert get_memory(db_conn, mem_id)["context"] == "New"

    def test_returns_false_for_missing(self, db_conn):
        from scripts.database import update_memory
        assert update_memory(db_conn, "nonexistent", {"context": "X"}) is False

    def test_updates_json_fields(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        update_memory(db_conn, mem_id, {"lessons_learned": ["New lesson"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["New lesson"]

    def test_ignores_id_and_created_at(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        original = get_memory(db_conn, mem_id)
        update_memory(db_conn, mem_id, {"id": "new-id", "created_at": "2000-01-01"})
        result = get_memory(db_conn, mem_id)
        assert result["id"] == mem_id
        assert result["created_at"] == original["created_at"]


class TestDeleteMemory:
    def test_deletes_existing(self, db_conn):
        from scripts.database import create_memory, delete_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        assert delete_memory(db_conn, mem_id) is True
        assert get_memory(db_conn, mem_id) is None

    def test_returns_false_for_missing(self, db_conn):
        from scripts.database import delete_memory
        assert delete_memory(db_conn, "nonexistent") is False


class TestPACTMemoryDeleteVecTableHandling:
    """Locks the narrowed-except contract on PACTMemory.delete's vec_memories cleanup.

    PACTMemory.delete attempts `DELETE FROM vec_memories WHERE memory_id = ?`
    AFTER deleting the row from the primary memories table. The vec_memories
    table only exists when sqlite-vec is loaded; absence is the legitimate
    common case. A bare `except Exception: pass` here would also swallow
    "database is locked", "disk I/O error", and other surface-or-surface-this
    failures. The narrowed `except sqlite3.OperationalError if "no such table"
    in str(e)` lets the legitimate-FTS-absent path stay silent while
    surfacing every other operational error.
    """

    def test_delete_silently_handles_missing_vec_memories_table(self, tmp_path):
        """Real-DB path: schema has no vec_memories table; delete must succeed."""
        from scripts.memory_api import PACTMemory

        db_path = tmp_path / "vec_absent.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()
        # Schema does NOT include vec_memories — DELETE FROM vec_memories will
        # raise sqlite3.OperationalError("no such table: vec_memories"), which
        # the narrowed except must swallow.

        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"), \
             patch.object(PACTMemory, "_store_embedding", autospec=True):
            memory = PACTMemory(
                project_id="vec-absent-test",
                session_id="s1",
                db_path=db_path,
            )
            mem_id = memory.save({"context": "row to delete"})
            # Delete must succeed despite vec_memories being absent.
            resolved = memory.delete(mem_id)
            assert resolved == mem_id
            # Verify the primary-table delete actually landed.
            assert memory.get(mem_id) is None

    def test_delete_propagates_other_operational_errors(self, tmp_path):
        """Bug-surfacing: non-"no such table" OperationalError must propagate.

        Wraps conn.execute so the vec_memories DELETE raises a controlled
        OperationalError ("database is locked") instead of the natural
        no-such-table error. The narrowed except must NOT swallow this —
        propagating it surfaces real problems (lock contention, disk I/O,
        etc.) instead of silently leaving stale vector entries.
        """
        from scripts.memory_api import PACTMemory
        import scripts.memory_api as memory_api_mod

        db_path = tmp_path / "vec_locked.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()

        from contextlib import contextmanager
        real_db_connection = memory_api_mod.db_connection

        class _ConnProxy:
            """Delegates everything to real_conn, intercepts execute()."""
            def __init__(self, real_conn):
                self._real = real_conn

            def execute(self, sql, *args, **kwargs):
                # Inject a non-"no such table" OperationalError on the
                # vec_memories DELETE only. All other queries (resolver,
                # primary-table delete, ensure_initialized) pass through.
                if "vec_memories" in sql and sql.lstrip().upper().startswith("DELETE"):
                    raise sqlite3.OperationalError("database is locked")
                return self._real.execute(sql, *args, **kwargs)

            def __getattr__(self, name):
                # Delegate any other attribute (commit, close, row_factory, etc.)
                return getattr(self._real, name)

        @contextmanager
        def db_connection_locking_vec(db_path=None):
            # MIRRORS THE REAL SIGNATURE, `db_connection(db_path=None)`, and
            # not one call site. An earlier spelling took a REQUIRED positional
            # named `path`, which held only while every caller passed a path.
            # A double bound to the call site breaks on a call-shape change and
            # reports a TypeError rather than the behaviour under test.
            #
            # ⚠️ DO NOT RENAME THIS PARAMETER TO REMOVE THE SHADOW. It shadows
            # the enclosing `db_path` and that is the correct cost. The name
            # must align with the real function, because a caller may pass the
            # argument BY KEYWORD, and a double with a different parameter name
            # then raises TypeError instead of running the behaviour under
            # test. A rename looks like tidying and reintroduces the exact
            # coupling the paragraph above records. The shadow is inert here:
            # this body uses the parameter alone, and the enclosing name is
            # read only outside this closure.
            with real_db_connection(db_path) as real_conn:
                yield _ConnProxy(real_conn)

        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"), \
             patch.object(PACTMemory, "_store_embedding", autospec=True):
            memory = PACTMemory(
                project_id="vec-locked-test",
                session_id="s1",
                db_path=db_path,
            )
            mem_id = memory.save({"context": "row delete will lock-fail"})

            # Now patch db_connection so the SUBSEQUENT delete uses the
            # locking wrapper. (save() above used the real db_connection.)
            with patch.object(
                memory_api_mod, "db_connection", db_connection_locking_vec
            ):
                with pytest.raises(sqlite3.OperationalError) as exc_info:
                    memory.delete(mem_id)
                assert "database is locked" in str(exc_info.value)
                assert "no such table" not in str(exc_info.value)


class TestListMemories:
    def test_lists_all(self, db_conn):
        from scripts.database import create_memory, list_memories
        create_memory(db_conn, {"context": "A"})
        create_memory(db_conn, {"context": "B"})
        assert len(list_memories(db_conn)) == 2

    def test_filters_by_project(self, db_conn):
        from scripts.database import create_memory, list_memories
        create_memory(db_conn, {"context": "A", "project_id": "p1"})
        create_memory(db_conn, {"context": "B", "project_id": "p2"})
        results = list_memories(db_conn, project_id="p1")
        assert len(results) == 1
        assert results[0]["context"] == "A"

    def test_filters_by_session(self, db_conn):
        from scripts.database import create_memory, list_memories
        create_memory(db_conn, {"context": "A", "session_id": "s1"})
        create_memory(db_conn, {"context": "B", "session_id": "s2"})
        assert len(list_memories(db_conn, session_id="s1")) == 1

    def test_respects_limit(self, db_conn):
        from scripts.database import create_memory, list_memories
        for i in range(5):
            create_memory(db_conn, {"context": f"M{i}"})
        assert len(list_memories(db_conn, limit=2)) == 2

    def test_respects_offset(self, db_conn):
        from scripts.database import create_memory, list_memories
        for i in range(5):
            create_memory(db_conn, {"context": f"M{i}"})
        assert len(list_memories(db_conn, limit=10, offset=3)) == 2


# ---------------------------------------------------------------------------
# Text search
# ---------------------------------------------------------------------------

class TestSearchMemoriesByText:
    def test_finds_by_context(self, db_conn):
        from scripts.database import create_memory, search_memories_by_text
        create_memory(db_conn, {"context": "Working on authentication"})
        create_memory(db_conn, {"context": "Working on payments"})
        assert len(search_memories_by_text(db_conn, "authentication")) == 1

    def test_finds_by_goal(self, db_conn):
        from scripts.database import create_memory, search_memories_by_text
        create_memory(db_conn, {"goal": "Implement OAuth2"})
        assert len(search_memories_by_text(db_conn, "OAuth2")) == 1

    def test_finds_by_lessons(self, db_conn):
        from scripts.database import create_memory, search_memories_by_text
        create_memory(db_conn, {"lessons_learned": ["Always use mocks"]})
        assert len(search_memories_by_text(db_conn, "mocks")) == 1

    def test_filters_by_project(self, db_conn):
        from scripts.database import create_memory, search_memories_by_text
        create_memory(db_conn, {"context": "auth thing", "project_id": "p1"})
        create_memory(db_conn, {"context": "auth other", "project_id": "p2"})
        assert len(search_memories_by_text(db_conn, "auth", project_id="p1")) == 1

    def test_escapes_sql_wildcards(self, db_conn):
        from scripts.database import create_memory, search_memories_by_text
        create_memory(db_conn, {"context": "100% done"})
        create_memory(db_conn, {"context": "not percent"})
        assert len(search_memories_by_text(db_conn, "100%")) == 1

    def test_empty_results(self, db_conn):
        from scripts.database import search_memories_by_text
        assert search_memories_by_text(db_conn, "nonexistent") == []

    def test_records_sharing_a_created_at_come_newest_inserted_first(self, db_conn):
        """This pins the DIRECTION of the `rowid` tiebreak, not its presence.

        MEASURED DETECTION SET, so nobody has to re-run the ablation. Deleting
        `, rowid DESC` does NOT redden this arm: SQLite already emits tied rows
        in reverse rowid order under this plan, so the clause and its absence
        are indistinguishable through the query's public behaviour. Changing it
        to `rowid ASC` DOES redden it. So the clause's role as insurance -- for
        a tie between rows the schema DEFAULT wrote -- stays UNTESTED, and it
        stays untested for a reason internal to this query rather than because
        anyone judged it unnecessary. No OUTPUT arm can cover it while removal
        and presence produce the same rows, which is why the cover is
        TestTheTiebreakReachesTheEngine below: it asserts the clause in the
        statement the engine is handed and so does not need the rows to
        differ.

        THAT IS THE MUTATION WORTH CATCHING, because anyone optimising this
        query's sort has a reason to flip the direction: an index on
        `(project_id, created_at DESC)` still needs a temp b-tree for the last
        term under `rowid DESC` and needs none under `rowid ASC`. DESC is what
        makes a projection match the order the saves went in, so the flip
        trades a correctness property for a sort this query does not need.
        Measured, and narrower than it first looks: an ASCENDING index on
        `(project_id, created_at)` satisfies `created_at DESC, rowid DESC`
        outright with no sort, by scanning backward. So it is the DESC index
        that cannot fully serve `rowid DESC`, not indexes in general.

        The shared stamp is set by direct SQL because that is the only way to
        reach the clause. `create_memory` stamps microsecond ISO and ignores a
        caller's value, so saves cannot tie; in production a tie is reachable
        only from a row the schema DEFAULT wrote.
        """
        from scripts.database import create_memory, search_memories_by_text
        ids = [create_memory(db_conn, {"context": f"tied probe {i}"})
               for i in range(3)]
        db_conn.execute("UPDATE memories SET created_at = '2026-01-01 00:00:00'")
        db_conn.commit()

        # THE TIE IS A PRECONDITION AND IS ASSERTED, NOT ASSUMED. Distinct
        # stamps produce this same expected order under `created_at DESC`
        # alone, so a backdate that silently did nothing would leave the arm
        # green while exercising no tie at all.
        stamps = {r[0] for r in db_conn.execute("SELECT created_at FROM memories")}
        assert len(stamps) == 1, f"the rows must tie; got {sorted(stamps)}"

        found = [r["id"] for r in search_memories_by_text(db_conn, "tied probe")]

        assert found == list(reversed(ids)), (
            "tied rows must come back newest-inserted first; got the order "
            f"{found} for insertion order {ids}"
        )


class TestTheTiebreakReachesTheEngine:
    """Both ordering queries ASK the engine for `rowid DESC`, on any plan.

    PLAN-INDEPENDENT BY CONSTRUCTION: these read the statement the driver is
    handed and never look at rows, so they hold under every plan -- including
    the ascending-index plan on which deleting the tiebreak returns identical
    rows, where no output-based check can see the deletion at all. That is the
    gap these close, and it is why the arms above cannot close it themselves.

    WHAT THIS PROVES: the clause is asked for. WHAT IT DOES NOT: that the
    clause has any effect, or that the ordering is correct. Those remain with
    the output arms, which are not redundant -- they pin what the ordering
    DOES under today's plan, which this cannot.

    ITS OWN CONDITION, stated because removing a contingency must not add a
    quieter one: this pins the clause AS TEXT, whitespace-normalised. A
    deliberate rewrite of the ordering into an equivalent form must update
    these arms; they will redden on such a rewrite though behaviour is right.
    """

    def _order_by_of_the_one_ordering_statement(self, tmp_path, call):
        """The ORDER BY tail of the single ordering statement `call` issued.

        Asserting the count is what makes the result attributable: with two
        sites carrying the identical clause, a check that searched everything
        traced could pass on evidence produced by the other query.
        """
        from scripts.database import ensure_initialized
        conn = sqlite3.connect(str(tmp_path / "traced.db"))
        ensure_initialized(conn)
        seen = []
        conn.set_trace_callback(seen.append)
        call(conn)
        conn.set_trace_callback(None)
        ordered = [" ".join(s.split()) for s in seen if "ORDER BY" in s]
        assert len(ordered) == 1, (
            "this call must issue exactly one ordering statement or the "
            f"assertion cannot be attributed to it; got {len(ordered)}"
        )
        return ordered[0].split("ORDER BY", 1)[1].strip()

    def test_the_paged_list_asks_for_the_tiebreak(self, tmp_path):
        from scripts.database import list_memories
        clause = self._order_by_of_the_one_ordering_statement(
            tmp_path, lambda c: list_memories(c, project_id="p", limit=3)
        )
        assert clause.startswith("created_at DESC, rowid DESC"), clause

    def test_the_text_search_asks_for_the_tiebreak(self, tmp_path):
        from scripts.database import search_memories_by_text
        clause = self._order_by_of_the_one_ordering_statement(
            tmp_path,
            lambda c: search_memories_by_text(c, "probe", project_id="p", limit=3),
        )
        assert clause.startswith("created_at DESC, rowid DESC"), clause


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

class TestMaintenance:
    def test_get_memory_count(self, db_conn):
        from scripts.database import create_memory, get_memory_count
        assert get_memory_count(db_conn) == 0
        create_memory(db_conn, {"context": "A"})
        create_memory(db_conn, {"context": "B"})
        assert get_memory_count(db_conn) == 2

    def test_get_memory_count_by_project(self, db_conn):
        from scripts.database import create_memory, get_memory_count
        create_memory(db_conn, {"context": "A", "project_id": "p1"})
        create_memory(db_conn, {"context": "B", "project_id": "p2"})
        assert get_memory_count(db_conn, project_id="p1") == 1

    def test_check_integrity(self, db_conn):
        from scripts.database import check_integrity
        assert check_integrity(db_conn) is True

    def test_generate_id_uniqueness(self):
        from scripts.database import generate_id
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Bug 2 fix (#374) — unknown column keys raise ValueError instead of silent drop
# ---------------------------------------------------------------------------
#
# Before PR #374's commit 1, update_memory silently filtered unknown dict keys
# via a hardcoded ALLOWED_COLUMNS set. A typo in the payload (e.g. `descrption`
# instead of `description`, or `description` when the real field is `notes`)
# would disappear with no warning — the CLI returned {"ok": true} on silent
# data loss.
#
# New contract: unknown top-level keys raise ValueError BEFORE any DB write,
# with an error message that names the offending key AND the allowed-field set.
# The raise happens outside any transaction so no partial state is left
# behind (atomicity).

class TestUnknownColumnsRaise:
    """Verify update_memory and save_memory (create_memory) raise on unknown keys."""

    def test_update_raises_on_unknown_key(self, db_conn):
        """Unknown key in update payload must raise ValueError (bug 2 fix)."""
        from scripts.database import create_memory, update_memory
        mem_id = create_memory(db_conn, {"context": "Original"})
        with pytest.raises(ValueError, match="Unknown memory field"):
            update_memory(db_conn, mem_id, {"bogus_field": "x"})

    def test_update_raises_on_sql_injection_attempt(self, db_conn):
        """SQL-injection-shaped keys still raise (no silent drop regression)."""
        from scripts.database import create_memory, update_memory
        mem_id = create_memory(db_conn, {"context": "Original"})
        with pytest.raises(ValueError, match="Unknown memory field"):
            update_memory(db_conn, mem_id, {
                "evil_column": "DROP TABLE memories",
                "'; DROP TABLE memories; --": "injection attempt",
            })

    def test_update_error_lists_unknown_and_allowed(self, db_conn):
        """Error message must name the bad keys AND the allowed set."""
        from scripts.database import create_memory, update_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        with pytest.raises(ValueError) as excinfo:
            update_memory(db_conn, mem_id, {"nonexistent_field": "x"})
        msg = str(excinfo.value)
        assert "nonexistent_field" in msg
        assert "Allowed fields" in msg
        # Allowed set must include at least the well-known columns so users
        # can discover what they meant.
        assert "context" in msg
        assert "lessons_learned" in msg

    def test_update_atomicity_on_unknown_key(self, db_conn):
        """
        A rejected update must leave the row unchanged.
        If a payload mixes a valid field (context) with an unknown field (bogus),
        the ValueError must be raised before any write, so the existing row
        keeps its original value.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Original"})
        with pytest.raises(ValueError):
            update_memory(db_conn, mem_id, {"context": "Updated", "bogus": "x"})
        # Row must be unchanged — no partial apply.
        assert get_memory(db_conn, mem_id)["context"] == "Original"

    def test_update_only_valid_keys_succeeds(self, db_conn):
        """Happy path: well-formed updates still work (no regression)."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        assert update_memory(db_conn, mem_id, {"goal": "New goal"}) is True
        assert get_memory(db_conn, mem_id)["goal"] == "New goal"

    def test_save_raises_on_unknown_key(self, db_conn):
        """
        save_memory (create_memory) is symmetric with update_memory.

        Before PR #374, create_memory silently dropped unknown top-level keys
        because _serialize_json_fields preserved them but the INSERT only read
        named columns via data.get(...). Now unknown keys raise ValueError at
        the validation step, before any DB write.
        """
        from scripts.database import create_memory
        with pytest.raises(ValueError, match="Unknown memory field"):
            create_memory(db_conn, {"context": "Test", "bogus_field": "x"})

    def test_save_allows_id_and_created_at(self, db_conn):
        """id and created_at are legal on create (not 'unknown')."""
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {
            "id": "explicit-id",
            "context": "Test",
        })
        assert mem_id == "explicit-id"
        assert get_memory(db_conn, mem_id)["context"] == "Test"

    def test_update_json_fields_additive_on_empty(self, db_conn):
        """
        Updating a memory whose list field is empty simply sets it — no
        duplication surprise from the dedup merge path.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        update_memory(db_conn, mem_id, {"lessons_learned": ["New lesson"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["New lesson"]

    def test_id_and_created_at_excluded_from_update(self, db_conn):
        """id and created_at are silently stripped from update payloads (pre-validation).

        This preserves the historical contract that callers can pass a full
        memory dict (including id/created_at from a prior get()) through
        update_memory without tripping validation. They are NOT persisted.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        original = get_memory(db_conn, mem_id)
        # No ValueError — id/created_at are stripped before validation.
        assert update_memory(db_conn, mem_id, {
            "id": "hijacked-id",
            "created_at": "1970-01-01T00:00:00",
        }) is True
        after = get_memory(db_conn, mem_id)
        assert after["id"] == mem_id
        assert after["created_at"] == original["created_at"]

    def test_update_raises_on_unknown_subobject_key_in_entities(self, db_conn):
        """
        End-to-end: unknown sub-object keys in dict list fields raise
        ValueError via the bug-3 strict-from-dict path that fires inside
        _canonicalize_dict_item during the merge. Row stays unchanged.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "A"})
        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            update_memory(db_conn, mem_id, {
                "entities": [{"name": "Redis", "description": "cache"}],
            })
        # Row unchanged (atomicity — the raise happened outside the transaction).
        assert get_memory(db_conn, mem_id)["context"] == "A"


# ---------------------------------------------------------------------------
# Bug 3 part 2 (#374) — save-path strict-on-ingress for sub-object keys
# ---------------------------------------------------------------------------
#
# The first bug-3 fix wired strict=True into _canonicalize_dict_item but
# only called it from update_memory's merge path. The save ingress
# (create_memory) bypassed it entirely — _serialize_json_fields was a raw
# json.dumps passthrough, so dict-list items with unknown sub-object keys
# (e.g. active_tasks=[{"id":"abc","subject":"foo"}]) were silently stored
# as verbatim junk that read back as empty dataclass instances via the
# lenient read path. Discovered by the post-CODE harvester dogfooding the
# new contract.
#
# Fix: create_memory now runs _canonicalize_dict_item over DICT_LIST_FIELDS
# BEFORE _serialize_json_fields. Validation-before-write invariant applies
# symmetrically to save and update: a raise leaves the DB untouched.


class TestBug3SavePathStrict:
    def test_save_raises_on_unknown_subobject_key_in_entities(self, db_conn):
        """Symmetric with the existing update-path test. The save ingress
        must raise ValueError on unknown Entity sub-object keys."""
        from scripts.database import create_memory
        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            create_memory(db_conn, {
                "context": "A",
                "entities": [{"name": "Redis", "description": "cache"}],
            })

    def test_save_raises_on_unknown_subobject_key_in_active_tasks(self, db_conn):
        """Harvester's exact reproducer: active_tasks with id/subject keys
        instead of task/status/priority must raise ValueError on save."""
        from scripts.database import create_memory
        with pytest.raises(ValueError, match="Unknown keys for TaskItem"):
            create_memory(db_conn, {
                "context": "harvester repro",
                "active_tasks": [
                    {"id": "abc", "subject": "foo"},
                    {"id": "def", "subject": "bar"},
                ],
            })

    def test_save_raises_on_unknown_subobject_key_in_decisions(self, db_conn):
        """Decisions path also strict on save."""
        from scripts.database import create_memory
        with pytest.raises(ValueError, match="Unknown keys for Decision"):
            create_memory(db_conn, {
                "context": "A",
                "decisions": [{"decision": "D1", "bogus_field": "x"}],
            })

    def test_save_atomicity_on_unknown_subobject_key(self, db_conn):
        """A raise during canonicalization must leave the memories table
        unchanged — no partial row inserted. Validation-before-write."""
        from scripts.database import create_memory
        rows_before = db_conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        with pytest.raises(ValueError):
            create_memory(db_conn, {
                "context": "should not persist",
                "entities": [{"name": "X", "bogus": "y"}],
            })
        rows_after = db_conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        assert rows_after == rows_before

    def test_save_canonicalizes_dict_items_on_write(self, db_conn):
        """
        The save path round-trips each dict-list item through its dataclass,
        so the stored shape is canonical — defaults are populated and
        None-vs-missing aliasing is resolved to a single shape. Symmetric
        with update_memory's merge path.
        """
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {
            "context": "canonicalization check",
            "active_tasks": [{"task": "T1"}],
            "entities": [{"name": "Redis"}],
            "decisions": [{"decision": "D1"}],
        })
        result = get_memory(db_conn, mem_id)
        # TaskItem defaults status='pending'
        assert result["active_tasks"] == [{"task": "T1", "status": "pending"}]
        # Entity canonical shape
        assert result["entities"][0]["name"] == "Redis"
        # Decision canonical shape
        assert result["decisions"][0]["decision"] == "D1"

    def test_save_with_string_shorthand_in_list_fields(self, db_conn):
        """
        Regression: string shorthand for list fields (e.g.
        entities=["Redis"]) must still work after canonicalization. The
        _canonicalize_dict_item helper routes str inputs through the
        non-strict cls.from_dict(str) path — bug 3 strictness only applies
        to dict inputs.
        """
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {
            "context": "string shorthand",
            "entities": ["Redis", "Postgres"],
        })
        result = get_memory(db_conn, mem_id)
        names = [e["name"] for e in result["entities"]]
        assert names == ["Redis", "Postgres"]


# ---------------------------------------------------------------------------
# Bug 1 fix (#374) — additive-by-default list merge with content-hash dedup
# ---------------------------------------------------------------------------
#
# Before PR #374, updating a list field replaced it wholesale: passing
# {"lessons_learned": ["c"]} to a memory whose lessons_learned was
# ["a", "b"] would overwrite it to ["c"], silently losing the two existing
# items. The pinned recovery protocol told every session to
# read-merge-write-back, which was a clunky workaround.
#
# New default behavior: list fields append with content-hash dedup.
# Repeat calls are idempotent. The legacy wholesale-replace is available
# via replace=True.

class TestBug1AdditiveListMerge:
    """Additive merge + dedup for list fields. Default behavior."""

    # --- String list fields -----------------------------------------------

    def test_append_string_list_to_existing(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a", "b"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["c"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a", "b", "c"]

    def test_dedup_string_list_overlap(self, db_conn):
        """b already exists — it should NOT be duplicated."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a", "b"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["b", "c"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a", "b", "c"]

    def test_dedup_within_incoming_string_batch(self, db_conn):
        """A caller sending [a, a, b] gets [a, b]."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": []})
        update_memory(db_conn, mem_id, {"lessons_learned": ["a", "a", "b"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a", "b"]

    def test_dedup_strips_whitespace(self, db_conn):
        """'  a  ' and 'a' collide (hash over stripped string)."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["  a  "]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a"]

    def test_repeat_update_is_idempotent(self, db_conn):
        """Calling update twice with the same incoming list produces stable state."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["b", "c"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["b", "c"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a", "b", "c"]

    def test_order_preserved_existing_before_incoming(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["x", "y"]})
        update_memory(db_conn, mem_id, {"lessons_learned": ["a", "b"]})
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["x", "y", "a", "b"]

    # --- Dict list fields -------------------------------------------------

    def test_append_dict_list_preserves_nested_updates(self, db_conn):
        """
        Rationale (architect §2): dedup by `name` alone would collapse
        legitimate nested-field updates. Entity{name:"X",notes:"old"} and
        Entity{name:"X",notes:"new"} are DIFFERENT entries; both preserved.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {
            "entities": [{"name": "Redis", "notes": "old"}],
        })
        update_memory(db_conn, mem_id, {
            "entities": [{"name": "Redis", "notes": "new"}],
        })
        ents = get_memory(db_conn, mem_id)["entities"]
        assert len(ents) == 2
        assert {"name": "Redis", "notes": "old"} in ents
        assert {"name": "Redis", "notes": "new"} in ents

    def test_dedup_dict_list_exact_duplicate(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {
            "entities": [{"name": "Redis", "notes": "n"}],
        })
        update_memory(db_conn, mem_id, {
            "entities": [{"name": "Redis", "notes": "n"}],
        })
        assert len(get_memory(db_conn, mem_id)["entities"]) == 1

    def test_dedup_dict_list_none_vs_missing_optional(self, db_conn):
        """
        {"name": "X"} and {"name": "X", "type": None} must collide. The
        dataclass round-trip in _canonicalize_dict_item drops falsy
        optionals in to_dict(), so both canonicalize to {"name": "X"}.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"entities": [{"name": "X"}]})
        update_memory(db_conn, mem_id, {
            "entities": [{"name": "X", "type": None}],
        })
        assert len(get_memory(db_conn, mem_id)["entities"]) == 1

    def test_dedup_active_tasks(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {
            "active_tasks": [{"task": "T1"}],
        })
        update_memory(db_conn, mem_id, {
            "active_tasks": [{"task": "T1"}, {"task": "T2"}],
        })
        tasks = get_memory(db_conn, mem_id)["active_tasks"]
        assert len(tasks) == 2
        assert {"task": "T1", "status": "pending"} in tasks
        assert {"task": "T2", "status": "pending"} in tasks

    def test_dedup_decisions(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {
            "decisions": [{"decision": "Use Redis", "rationale": "fast"}],
        })
        update_memory(db_conn, mem_id, {
            "decisions": [{"decision": "Use Redis", "rationale": "fast"}],
        })
        assert len(get_memory(db_conn, mem_id)["decisions"]) == 1


class TestBug1ReplaceKwarg:
    """replace=True restores legacy wholesale-replace behavior."""

    def test_replace_clobbers_string_list(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a", "b", "c"]})
        update_memory(
            db_conn, mem_id,
            {"lessons_learned": ["new"]},
            replace=True,
        )
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["new"]

    def test_replace_still_dedups_within_incoming(self, db_conn):
        """replace=True still dedups duplicate items within the new batch."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["old"]})
        update_memory(
            db_conn, mem_id,
            {"lessons_learned": ["a", "a", "b"]},
            replace=True,
        )
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a", "b"]

    def test_replace_clobbers_dict_list(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {
            "entities": [{"name": "OldA"}, {"name": "OldB"}],
        })
        update_memory(
            db_conn, mem_id,
            {"entities": [{"name": "NewOnly"}]},
            replace=True,
        )
        assert get_memory(db_conn, mem_id)["entities"] == [{"name": "NewOnly"}]

    def test_replace_validates_sub_object_keys(self, db_conn):
        """Even under replace, strict from_dict validation still runs."""
        from scripts.database import create_memory, update_memory
        mem_id = create_memory(db_conn, {"entities": [{"name": "X"}]})
        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            update_memory(
                db_conn, mem_id,
                {"entities": [{"name": "X", "bogus": "y"}]},
                replace=True,
            )


class TestBug1ScalarVsListSemantics:
    """Scalar fields still replace even when list fields merge."""

    def test_scalar_fields_replace_not_merge(self, db_conn):
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "old", "goal": "old goal"})
        update_memory(db_conn, mem_id, {"context": "new"})
        result = get_memory(db_conn, mem_id)
        assert result["context"] == "new"
        assert result["goal"] == "old goal"

    def test_mixed_scalar_and_list_update(self, db_conn):
        """One call: scalar replaces, list appends."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {
            "context": "old",
            "lessons_learned": ["a"],
        })
        update_memory(db_conn, mem_id, {
            "context": "new",
            "lessons_learned": ["b"],
        })
        result = get_memory(db_conn, mem_id)
        assert result["context"] == "new"
        assert result["lessons_learned"] == ["a", "b"]


class TestBug1HashStability:
    """Content-hash key properties."""

    def test_hash_stable_across_calls(self):
        from scripts.database import _content_hash
        h1 = _content_hash("lessons_learned", "abc")
        h2 = _content_hash("lessons_learned", "abc")
        assert h1 == h2

    def test_hash_dict_order_independent(self):
        """{a:1, b:2} and {b:2, a:1} must hash to the same key."""
        from scripts.database import _content_hash
        h1 = _content_hash("entities", {"name": "X", "type": "svc"})
        h2 = _content_hash("entities", {"type": "svc", "name": "X"})
        assert h1 == h2

    def test_hash_differs_on_content_change(self):
        from scripts.database import _content_hash
        h1 = _content_hash("lessons_learned", "abc")
        h2 = _content_hash("lessons_learned", "abd")
        assert h1 != h2


class TestBug1TransactionalAtomicity:
    """Rejected updates must leave the row unchanged."""

    def test_sub_object_validation_failure_rolls_back(self, db_conn):
        """
        Passing a mix of a valid list item and an invalid one (unknown
        sub-object key) must raise ValueError WITHOUT writing ANY items
        to the row. Validation runs outside the BEGIN IMMEDIATE so the
        transaction is never started.
        """
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"entities": [{"name": "Existing"}]})
        with pytest.raises(ValueError):
            update_memory(db_conn, mem_id, {
                "entities": [
                    {"name": "Good"},
                    {"name": "Bad", "bogus": "y"},
                ],
            })
        result = get_memory(db_conn, mem_id)
        assert result["entities"] == [{"name": "Existing"}]

    def test_list_type_coercion_failure_raises(self, db_conn):
        """A non-list value for a list field raises ValueError cleanly."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"lessons_learned": ["a"]})
        with pytest.raises(ValueError, match="must be a list"):
            update_memory(db_conn, mem_id, {"lessons_learned": "not a list"})
        # Row unchanged
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["a"]


# ---------------------------------------------------------------------------
# TOCTOU mitigation in get_connection (Item 11)
# ---------------------------------------------------------------------------

class TestTOCTOUMitigation:
    """Verify atomic file pre-creation and WAL sidecar permission hardening."""

    def test_new_db_file_created_with_600(self, tmp_path):
        """New database file should be created with mode 0o600 via O_CREAT|O_EXCL."""
        import stat
        import sqlite3 as stdlib_sqlite3

        db_path = tmp_path / "new.db"
        assert not db_path.exists()

        with patch("scripts.database.sqlite3", stdlib_sqlite3):
            from scripts.database import get_connection
            conn = get_connection(db_path=db_path)
            conn.close()

        assert db_path.exists()
        file_mode = stat.S_IMODE(db_path.stat().st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

    def test_race_condition_file_exists_error_handled(self, tmp_path):
        """FileExistsError during O_CREAT|O_EXCL should be handled gracefully."""
        import sqlite3 as stdlib_sqlite3

        db_path = tmp_path / "race.db"

        # Simulate race: os.open raises FileExistsError
        original_open = os.open
        def mock_open(path, flags, mode=0):
            if "race.db" in str(path) and (flags & os.O_EXCL):
                raise FileExistsError("simulated race condition")
            return original_open(path, flags, mode)

        with patch("scripts.database.sqlite3", stdlib_sqlite3), \
             patch("scripts.database.os.open", side_effect=mock_open):
            from scripts.database import get_connection
            conn = get_connection(db_path=db_path)
            conn.close()

        # Should still succeed via sqlite3.connect fallback
        assert db_path.exists()

    def test_wal_sidecar_permissions_set_on_new_db(self, tmp_path):
        """WAL sidecar files should be chmod'd to 0o600 on new database creation."""
        import stat
        import sqlite3 as stdlib_sqlite3

        db_path = tmp_path / "wal_test.db"

        with patch("scripts.database.sqlite3", stdlib_sqlite3):
            from scripts.database import get_connection
            conn = get_connection(db_path=db_path)
            # Force WAL creation by writing data
            conn.execute("CREATE TABLE test (id TEXT)")
            conn.execute("INSERT INTO test VALUES ('x')")
            conn.commit()
            conn.close()

        # Check sidecar permissions if they exist
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(db_path) + suffix)
            if sidecar.exists():
                mode = stat.S_IMODE(sidecar.stat().st_mode)
                assert mode == 0o600, (
                    f"Sidecar {sidecar.name} should be 0o600, got {oct(mode)}"
                )

    def test_existing_db_skips_toctou(self, tmp_path):
        """Pre-existing database file should not trigger O_CREAT|O_EXCL."""
        import sqlite3 as stdlib_sqlite3

        db_path = tmp_path / "existing.db"
        # Pre-create the file
        db_path.write_bytes(b"")

        call_log = []
        original_open = os.open
        def tracking_open(path, flags, mode=0):
            if "existing.db" in str(path):
                call_log.append(flags)
            return original_open(path, flags, mode)

        with patch("scripts.database.sqlite3", stdlib_sqlite3), \
             patch("scripts.database.os.open", side_effect=tracking_open):
            from scripts.database import get_connection
            conn = get_connection(db_path=db_path)
            conn.close()

        # os.open should NOT have been called with O_EXCL for existing file
        for flags in call_log:
            assert not (flags & os.O_EXCL), "Should not use O_EXCL on existing file"


# ---------------------------------------------------------------------------
# Bug 1 (#374) — COMPREHENSIVE TEST LAYER (TEST phase, beyond smoke tests)
# ---------------------------------------------------------------------------
#
# These tests complement the 46 smoke + unit tests that shipped alongside the
# seven-commit fix. They target coder-flagged uncertainties the smoke layer
# could not cover:
#
#   1. BEGIN IMMEDIATE concurrent-writer race (architect §7.3, MEDIUM)
#   2. Legacy-row graceful degradation on read + update merge (MEDIUM)
#   3. Content-hash stability under non-JSON-serializable values (LOW)
#   4. Validation-before-transaction invariant (architect §1.3, NON-NEGOTIABLE)
#      — strengthened with conn.total_changes instrumentation to prove no
#      write was attempted, not merely rolled back.
#
# Each test class here is self-contained and does NOT use the shared `db_conn`
# fixture where real-database semantics matter (concurrency tests need a real
# on-disk WAL-mode database with `ensure_initialized` actually running, not
# patched out).


class TestBug1ConcurrentWriterRace:
    """
    Architect §7.3 / coder [MEDIUM] uncertainty — verify BEGIN IMMEDIATE
    serialization of two concurrent update_memory calls on the same row.

    Why this test matters: the merge algorithm reads the existing list, appends
    the incoming list, dedups, and writes. If two writers interleave between
    each other's SELECT and UPDATE, one merge can clobber the other. BEGIN
    IMMEDIATE takes the sqlite write lock at BEGIN (not at first write), so
    the second writer blocks until the first commits — guaranteeing each
    merge sees the previous merge's committed state.

    Fixture: real on-disk database with WAL mode, two live connections on
    separate threads, threading.Barrier for simultaneous kick-off. A 5-second
    join timeout prevents runaway hangs.
    """

    def _make_real_db(self, tmp_path):
        """Build a real WAL-mode DB and seed one memory row."""
        from scripts import database as db_mod
        db_path = tmp_path / "concurrent.db"
        # Use the module's own sqlite3 binding (pysqlite3 if present) — no
        # patching, because unittest.mock.patch is NOT thread-safe and
        # cross-thread teardown can corrupt the module state for other tests.
        conn = db_mod.get_connection(db_path=db_path)
        db_mod.init_schema(conn)
        mem_id = db_mod.create_memory(conn, {"lessons_learned": ["seed"]})
        conn.close()
        return db_path, mem_id

    def test_two_writers_both_merges_preserved(self, tmp_path):
        """
        Two threads each add a disjoint pair of lessons. After both finish,
        the row must contain ALL items (seed + 4 added), proving neither
        thread's merge clobbered the other's.
        """
        import threading
        from scripts import database as db_mod

        db_path, mem_id = self._make_real_db(tmp_path)

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def writer(items: list[str]) -> None:
            try:
                conn = db_mod.get_connection(db_path=db_path)
                try:
                    barrier.wait(timeout=5)
                    db_mod.update_memory(
                        conn, mem_id, {"lessons_learned": items},
                    )
                finally:
                    conn.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t_a = threading.Thread(target=writer, args=(["a1", "a2"],))
        t_b = threading.Thread(target=writer, args=(["b1", "b2"],))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        assert not t_a.is_alive(), "writer A stalled (BEGIN IMMEDIATE deadlock?)"
        assert not t_b.is_alive(), "writer B stalled (BEGIN IMMEDIATE deadlock?)"
        assert not errors, f"writers raised: {errors!r}"

        # Reopen and verify union is preserved.
        conn = db_mod.get_connection(db_path=db_path)
        try:
            row = db_mod.get_memory(conn, mem_id)
        finally:
            conn.close()

        lessons = row["lessons_learned"]
        assert "seed" in lessons, "initial seed was clobbered"
        assert "a1" in lessons and "a2" in lessons, f"writer A lost: {lessons}"
        assert "b1" in lessons and "b2" in lessons, f"writer B lost: {lessons}"
        assert len(lessons) == 5, f"expected 5 unique items, got {lessons}"

    def test_repeat_concurrent_idempotent(self, tmp_path):
        """
        Same concurrent test run twice back-to-back must remain idempotent:
        the second round adds zero new items because all hashes collide.
        """
        import threading
        from scripts import database as db_mod

        db_path, mem_id = self._make_real_db(tmp_path)

        def run_round() -> None:
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def writer(items: list[str]) -> None:
                try:
                    conn = db_mod.get_connection(db_path=db_path)
                    try:
                        barrier.wait(timeout=5)
                        db_mod.update_memory(
                            conn, mem_id, {"lessons_learned": items},
                        )
                    finally:
                        conn.close()
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            t_a = threading.Thread(target=writer, args=(["x", "y"],))
            t_b = threading.Thread(target=writer, args=(["y", "z"],))
            t_a.start()
            t_b.start()
            t_a.join(timeout=10)
            t_b.join(timeout=10)
            assert not errors

        run_round()
        run_round()  # Re-run — dedup should keep the list stable.

        conn = db_mod.get_connection(db_path=db_path)
        try:
            lessons = db_mod.get_memory(conn, mem_id)["lessons_learned"]
        finally:
            conn.close()

        # seed + x + y + z (y deduped across writers). Second round adds nothing.
        assert sorted(lessons) == ["seed", "x", "y", "z"], f"got {lessons}"

    def test_three_writer_concurrent_race(self, tmp_path):
        """
        Three threads each add a distinct lesson and release simultaneously
        from a Barrier(3). After all writers finish, the row must contain
        ALL three new items plus the seed — proving union semantics hold
        under deeper write contention than the two-writer case. If
        BEGIN IMMEDIATE serialization were broken, at least one writer's
        merge would clobber another's and the final list would be short.
        """
        import threading
        from scripts import database as db_mod

        db_path, mem_id = self._make_real_db(tmp_path)

        barrier = threading.Barrier(3)
        errors: list[BaseException] = []

        def writer(item: str) -> None:
            try:
                conn = db_mod.get_connection(db_path=db_path)
                try:
                    barrier.wait(timeout=5)
                    db_mod.update_memory(
                        conn, mem_id, {"lessons_learned": [item]},
                    )
                finally:
                    conn.close()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t_a = threading.Thread(target=writer, args=("alpha",))
        t_b = threading.Thread(target=writer, args=("bravo",))
        t_c = threading.Thread(target=writer, args=("charlie",))
        t_a.start()
        t_b.start()
        t_c.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)
        t_c.join(timeout=10)

        assert not t_a.is_alive(), "writer A stalled (BEGIN IMMEDIATE deadlock?)"
        assert not t_b.is_alive(), "writer B stalled (BEGIN IMMEDIATE deadlock?)"
        assert not t_c.is_alive(), "writer C stalled (BEGIN IMMEDIATE deadlock?)"
        assert not errors, f"writers raised: {errors!r}"

        conn = db_mod.get_connection(db_path=db_path)
        try:
            row = db_mod.get_memory(conn, mem_id)
        finally:
            conn.close()

        lessons = row["lessons_learned"]
        assert "seed" in lessons, "initial seed was clobbered"
        assert "alpha" in lessons, f"writer A lost: {lessons}"
        assert "bravo" in lessons, f"writer B lost: {lessons}"
        assert "charlie" in lessons, f"writer C lost: {lessons}"
        assert len(lessons) == 4, (
            f"expected 4 unique items (seed + 3 writers), got {lessons}"
        )


class TestBug1LegacyRowGracefulDegradation:
    """
    Coder [MEDIUM] uncertainty — legacy DB rows with stray sub-object keys
    must remain readable AND remain updateable via the additive merge path.

    - Read path: MemoryObject.from_dict calls Entity.from_dict with strict=False,
      which drops unknown keys and emits a logger.warning.
    - Update merge path: _merge_with_dedup's try/except→repr() fallback fires
      when the existing row contains an un-canonicalizable item, keeping the
      legacy item verbatim rather than raising.
    """

    def test_legacy_row_with_stray_key_still_readable(self, db_conn, caplog):
        """
        Inject a row directly via raw SQL with a stray sub-object key in the
        entities JSON. PACTMemory.get should succeed, the stray key should be
        dropped, and a WARNING log should be emitted.
        """
        import logging
        from scripts.database import get_memory
        from scripts.models import MemoryObject

        # Raw insert bypassing create_memory (which would strict-validate).
        payload_entities = json.dumps(
            [{"name": "LegacyEntity", "legacy_field": "should-be-dropped"}]
        )
        db_conn.execute(
            """
            INSERT INTO memories (id, entities, created_at, updated_at)
            VALUES (?, ?, datetime('now'), datetime('now'))
            """,
            ("legacy-1", payload_entities),
        )
        db_conn.commit()

        # Read path: _deserialize_json_fields runs, then whatever calls
        # MemoryObject.from_dict. get_memory itself returns a raw dict, so
        # exercise the full model path by calling MemoryObject.from_dict.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="scripts.models"):
            raw = get_memory(db_conn, "legacy-1")
            memory_obj = MemoryObject.from_dict(raw)

        assert memory_obj.entities, "entities list empty after legacy read"
        assert memory_obj.entities[0].name == "LegacyEntity"
        assert not hasattr(memory_obj.entities[0], "legacy_field"), (
            "stray key leaked onto Entity instance"
        )
        assert any(
            "legacy_field" in rec.getMessage() or "Dropping unknown keys" in rec.getMessage()
            for rec in caplog.records
        ), f"expected WARNING about stray key, got records: {[r.getMessage() for r in caplog.records]}"

    def test_legacy_row_additive_update_uses_safety_valve(self, db_conn, caplog):
        """
        A legacy row with a genuinely uncanonicalizable sub-object in
        `entities` must still accept additive updates through update_memory.
        The _merge_with_dedup safety valve (try/except → repr() fallback)
        should fire for the legacy item so the merge succeeds and the new
        item is appended.

        Fixture choice (post-R2-5): we use a non-dict, non-str item (a raw
        integer) to defeat BOTH strict AND lenient canonicalization. R2-5
        added a lenient path that drops unknown keys on legacy dicts, so a
        plain stray-key dict no longer triggers the safety valve — the
        lenient path succeeds and no WARNING fires. A non-dict/non-str
        item hits the TypeError branch of _canonicalize_dict_item in
        BOTH modes, forcing the repr() fallback and the WARNING log.
        """
        import logging
        from scripts.database import get_memory, update_memory

        # Inject a legacy row: entities has one non-dict, non-str item
        # (raw integer). This cannot be canonicalized via Entity.from_dict
        # in either strict or lenient mode — _canonicalize_dict_item raises
        # TypeError on non-dict/non-str input, which forces the safety
        # valve repr() fallback in _merge_with_dedup.
        payload_entities = json.dumps(
            [42]
        )
        db_conn.execute(
            """
            INSERT INTO memories (id, entities, created_at, updated_at)
            VALUES (?, ?, datetime('now'), datetime('now'))
            """,
            ("legacy-2", payload_entities),
        )
        db_conn.commit()

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="scripts.database"):
            ok = update_memory(
                db_conn,
                "legacy-2",
                {"entities": [{"name": "NewEntity", "type": "service"}]},
            )

        assert ok is True
        row = get_memory(db_conn, "legacy-2")
        ents = row["entities"]
        # Safety valve: the legacy item must be preserved verbatim because
        # we cannot canonicalize it; the new item is appended.
        assert len(ents) == 2, f"expected 2 entities (1 legacy + 1 new), got {ents}"
        legacy_present = any(e == 42 for e in ents)
        new_present = any(
            isinstance(e, dict) and e.get("name") == "NewEntity" for e in ents
        )
        assert legacy_present, f"legacy item lost during merge: {ents}"
        assert new_present, f"new item not merged: {ents}"
        # Verify the safety-valve WARNING fired with structured field
        # assertions, not just message-text matching. We check the LogRecord's
        # numeric level (levelno) and logger name to confirm the warning came
        # from the database module at WARNING severity — this catches drift
        # where someone downgrades the log to INFO or moves the call site to
        # a different module without realizing the test only inspected text.
        safety_valve_records = [
            rec for rec in caplog.records
            if rec.levelno == logging.WARNING
            and "Legacy" in rec.getMessage()
            and "canonicalization" in rec.getMessage()
        ]
        assert len(safety_valve_records) >= 1, (
            "expected at least one WARNING-level safety-valve log from "
            "_merge_with_dedup, got: "
            f"{[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        rec = safety_valve_records[0]
        assert rec.levelno == logging.WARNING, (
            f"safety-valve log emitted at {rec.levelname}, expected WARNING"
        )
        assert rec.levelname == "WARNING", (
            f"safety-valve levelname is {rec.levelname!r}, expected 'WARNING'"
        )
        # The logger name should carry a meaningful module prefix so operators
        # can filter by source. Accept either the database module or the
        # broader scripts package, but reject root/empty/unrelated loggers.
        assert "database" in rec.name or "scripts" in rec.name, (
            f"safety-valve logger name {rec.name!r} lacks a database/scripts "
            "prefix — check the logger configuration in scripts/database.py"
        )


class TestBug1ContentHashEdgeCases:
    """
    Coder [LOW] uncertainty — the `default=str` fallback in the canonical JSON
    encoder for `_content_hash` must produce stable hex output even when the
    payload contains non-JSON-native types.

    Because Entity/Decision/TaskItem dataclasses only accept string-valued
    fields, the dict-list path canonicalizes through dataclass.to_dict() before
    json.dumps ever sees it — exotic types cannot reach the encoder through
    that path. We instead exercise:
      (a) the string-list hash path with exotic string reprs (stable under
          repeated calls);
      (b) the dict-list path via a stray-key dict that survives lenient
          canonicalization;
      (c) a direct json.dumps call matching the encoder parameters, proving
          default=str is in effect as documented.
    """

    def test_string_list_hash_stable_for_unicode(self):
        from scripts.database import _content_hash
        s = "café — Übung / 日本語 / emoji 🦀"
        assert _content_hash("lessons_learned", s) == _content_hash(
            "lessons_learned", s,
        )

    def test_string_list_hash_stable_for_stringified_datetime(self):
        from datetime import datetime, timezone
        from scripts.database import _content_hash
        dt = datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)
        h1 = _content_hash("lessons_learned", f"event at {dt}")
        h2 = _content_hash("lessons_learned", f"event at {dt}")
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_dict_list_hash_stable_across_python_runs(self):
        """
        Order-independent dict keys + canonical JSON separators should yield
        the same hex on two consecutive calls and the same hex for
        equivalent-shape inputs.
        """
        from scripts.database import _content_hash
        a = {"name": "X", "type": "svc", "notes": "n"}
        b = {"notes": "n", "name": "X", "type": "svc"}
        h_a1 = _content_hash("entities", a)
        h_a2 = _content_hash("entities", a)
        h_b = _content_hash("entities", b)
        assert h_a1 == h_a2
        assert h_a1 == h_b

    def test_canonical_json_default_str_fallback_in_use(self):
        """
        Directly exercise the json.dumps(default=str) contract described in
        architect §2. This is a belt-and-braces check: if someone ever drops
        default=str, a datetime item in the encoder call would raise
        TypeError instead of stringifying.
        """
        from datetime import datetime, timezone
        dt = datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)
        # Mirror the encoder call from _content_hash.
        payload = json.dumps(
            {"when": dt, "name": "X"},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        # Must contain the ISO-8601 string form of dt, not an error or repr.
        assert "2026-04-08" in payload
        assert "name" in payload


class TestBug1ValidationBeforeTransactionInvariant:
    """
    Architect §1.3 NON-NEGOTIABLE — validation must run BEFORE the transaction
    opens. Three properties to verify for every rejected update:

      1. ValueError is raised.
      2. The row is byte-identical to its pre-call state.
      3. conn.total_changes is unchanged post-call, proving no write was even
         attempted (strictly stronger than "the transaction rolled back").
    """

    def _snapshot_row(self, conn, mem_id):
        cursor = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,))
        row = cursor.fetchone()
        assert row is not None
        return tuple(row)  # sqlite3.Row is not directly hashable but indexable

    def test_unknown_top_level_key_no_transaction(self, db_conn):
        from scripts.database import create_memory, update_memory

        mem_id = create_memory(
            db_conn, {"context": "stable", "lessons_learned": ["a", "b"]}
        )
        pre_snapshot = self._snapshot_row(db_conn, mem_id)
        pre_changes = db_conn.total_changes

        with pytest.raises(ValueError, match="Unknown memory field"):
            update_memory(db_conn, mem_id, {"context": "new", "bogus": "x"})

        post_snapshot = self._snapshot_row(db_conn, mem_id)
        post_changes = db_conn.total_changes

        assert post_snapshot == pre_snapshot, (
            "row mutated despite ValueError — validation ran inside transaction?"
        )
        assert post_changes == pre_changes, (
            f"conn.total_changes moved ({pre_changes}→{post_changes}) — a write "
            "was attempted before validation rejected the payload"
        )

    def test_unknown_sub_object_key_no_transaction(self, db_conn):
        from scripts.database import create_memory, update_memory

        mem_id = create_memory(
            db_conn, {"entities": [{"name": "Existing"}]}
        )
        pre_snapshot = self._snapshot_row(db_conn, mem_id)
        pre_changes = db_conn.total_changes

        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            update_memory(
                db_conn,
                mem_id,
                {"entities": [{"name": "New", "bogus": "y"}]},
            )

        post_snapshot = self._snapshot_row(db_conn, mem_id)
        post_changes = db_conn.total_changes

        assert post_snapshot == pre_snapshot, (
            "row mutated despite ValueError — sub-object validation ran "
            "inside transaction?"
        )
        assert post_changes == pre_changes, (
            f"conn.total_changes moved ({pre_changes}→{post_changes}) — a "
            "write was attempted before sub-object validation rejected input"
        )

    def test_non_list_for_list_field_no_transaction(self, db_conn):
        """
        ValueError from _normalize_list_field also runs pre-transaction;
        assert total_changes unchanged here too.
        """
        from scripts.database import create_memory, update_memory

        mem_id = create_memory(db_conn, {"lessons_learned": ["a"]})
        pre_snapshot = self._snapshot_row(db_conn, mem_id)
        pre_changes = db_conn.total_changes

        with pytest.raises(ValueError, match="must be a list"):
            update_memory(
                db_conn, mem_id, {"lessons_learned": "oops not a list"},
            )

        assert self._snapshot_row(db_conn, mem_id) == pre_snapshot
        assert db_conn.total_changes == pre_changes


class TestBug1EmptyListBranches:
    """Empty list-field update branches (#374 R2-3c).

    Two distinct documented branches in update_memory's list-field loop —
    neither had direct coverage in round 1:

    1. replace=True + empty list  → field is CLEARED (load-bearing contract
       at database.py:866-869). This is the "clear this field" semantic.
    2. replace=False + empty list → SHORT-CIRCUIT (F6 at database.py:874-876).
       The loop `continue`s, nothing enters normalized[], and if only that
       key was touched the whole update becomes a no-op that returns True
       without bumping updated_at.
    """

    def _get_updated_at(self, db_conn, mem_id):
        """Read updated_at directly (get_memory strips it via MemoryObject)."""
        row = db_conn.execute(
            "SELECT updated_at FROM memories WHERE id = ?", (mem_id,),
        ).fetchone()
        return row["updated_at"] if row else None

    def test_replace_true_with_empty_list_clears_field(self, db_conn):
        """replace=True + [] clears the field (docstring:866-869)."""
        from scripts.database import create_memory, update_memory, get_memory

        mem_id = create_memory(
            db_conn, {"lessons_learned": ["item1", "item2"]},
        )
        assert get_memory(db_conn, mem_id)["lessons_learned"] == ["item1", "item2"]

        result = update_memory(
            db_conn, mem_id, {"lessons_learned": []}, replace=True,
        )

        assert result is True
        assert get_memory(db_conn, mem_id)["lessons_learned"] == [], (
            "replace=True with empty list must CLEAR the field — this is "
            "the documented 'clear this field' contract at database.py:866-869"
        )

    def test_additive_empty_list_short_circuit(self, db_conn):
        """
        F6 (#374 remediation, database.py:874-876): on the additive path,
        an empty incoming list is a no-op. The loop `continue`s; if nothing
        else was in the payload, normalized stays empty and the function
        returns True without bumping updated_at.
        """
        from scripts.database import create_memory, update_memory, get_memory

        mem_id = create_memory(db_conn, {"lessons_learned": ["item1"]})
        pre_updated_at = self._get_updated_at(db_conn, mem_id)
        pre_changes = db_conn.total_changes

        result = update_memory(db_conn, mem_id, {"lessons_learned": []})

        assert result is True, (
            "empty-list additive no-op must return True (the update is "
            "semantically successful — zero items to add)"
        )
        post = get_memory(db_conn, mem_id)
        assert post["lessons_learned"] == ["item1"], (
            "existing items must be untouched by an empty additive update"
        )
        assert self._get_updated_at(db_conn, mem_id) == pre_updated_at, (
            "updated_at must NOT be bumped — F6 short-circuit skips the "
            "UPDATE statement entirely"
        )
        assert db_conn.total_changes == pre_changes, (
            f"no write should be attempted (total_changes moved "
            f"{pre_changes}→{db_conn.total_changes})"
        )


class TestBug1ContentNoopSuppressesUpdatedAt:
    """Content no-op suppresses updated_at bump (#374 R2-4).

    When an additive update's incoming items are ALL already-present
    duplicates, the content-hash dedup reduces the merge result to the
    existing list. The current round-1 behavior still writes the row
    (bumping updated_at) because the short-circuit only skipped the empty
    incoming list, not the empty-merge-diff case.

    This test expresses the DESIRED behavior after task #10's fix: when
    the additive merge produces no new content, skip the UPDATE entirely
    so updated_at stays stable.

    If task #10 has not yet landed, this test WILL fail. That's expected —
    it's the red-before-green for R2-4.
    """

    def _get_updated_at(self, db_conn, mem_id):
        row = db_conn.execute(
            "SELECT updated_at FROM memories WHERE id = ?", (mem_id,),
        ).fetchone()
        return row["updated_at"] if row else None

    def test_additive_all_duplicates_suppresses_updated_at(self, db_conn):
        """Calling update with an item that already exists is a true no-op."""
        from scripts.database import create_memory, update_memory, get_memory

        mem_id = create_memory(db_conn, {"lessons_learned": ["item1"]})
        pre_updated_at = self._get_updated_at(db_conn, mem_id)

        # Send the same item again — after dedup, merge result == existing.
        result = update_memory(db_conn, mem_id, {"lessons_learned": ["item1"]})

        assert result is True, (
            "semantically successful no-op update must return True"
        )

        post = get_memory(db_conn, mem_id)
        assert post["lessons_learned"] == ["item1"], (
            "no duplication: dedup must collapse the incoming duplicate"
        )

        post_updated_at = self._get_updated_at(db_conn, mem_id)
        assert post_updated_at == pre_updated_at, (
            f"updated_at must NOT be bumped when the additive merge "
            f"produces no new content (pre={pre_updated_at!r}, "
            f"post={post_updated_at!r}). Task #10 should skip the UPDATE "
            "statement when the merged result equals the existing list."
        )


class TestBug1EmbeddingSkipOnIdempotentUpdate:
    """M7 idempotent embedding-skip (#374 R2-3b).

    PACTMemory.update snapshots CONTENT_FIELDS before calling update_memory.
    If the additive merge produces no diff (repeat call with identical
    payload), _content_fields_changed returns False and _store_embedding is
    NOT called a second time.

    Regression-guards commit 2336d2d: "fix(pact-memory): skip embedding
    regen on idempotent updates".
    """

    def test_repeat_update_with_same_payload_skips_embedding_regen(
        self, tmp_path,
    ):
        """Second identical update must NOT call _store_embedding."""
        from scripts.memory_api import PACTMemory

        db_path = tmp_path / "embed_skip_test.db"
        conn = sqlite3.connect(str(db_path))
        create_test_schema(conn)
        conn.close()

        with patch("scripts.memory_api._ensure_ready"), \
             patch("scripts.memory_api.sync_to_claude_md"), \
             patch.object(
                 PACTMemory, "_store_embedding", autospec=True,
             ) as mock_store:
            memory = PACTMemory(
                project_id="test-project",
                session_id="test-session",
                db_path=db_path,
            )

            # Create a memory with CONTENT_FIELDS content. The save() path
            # also calls _store_embedding — record that baseline so we can
            # assert the *update*-side call count.
            memory_id = memory.save({
                "context": "embedding skip test",
                "lessons_learned": ["lesson-A"],
            })
            save_side_calls = mock_store.call_count

            # First update: adds a new item — merge produces a diff, so
            # _store_embedding SHOULD be called.
            memory.update(memory_id, {"lessons_learned": ["lesson-B"]})
            first_update_calls = mock_store.call_count - save_side_calls
            assert first_update_calls == 1, (
                f"first update with new content should regen embedding "
                f"(got {first_update_calls} calls)"
            )

            # Second update with the SAME payload: all items already exist,
            # merge result equals existing, _content_fields_changed returns
            # False, and _store_embedding MUST NOT be called again.
            memory.update(memory_id, {"lessons_learned": ["lesson-B"]})
            second_update_calls = (
                mock_store.call_count - save_side_calls - first_update_calls
            )
            assert second_update_calls == 0, (
                f"repeat update with identical payload must skip embedding "
                f"regen (got {second_update_calls} extra calls — M7 "
                "optimization is not firing)"
            )


# ---------------------------------------------------------------------------
# escape_for_envelope: the rendering rule for caller-supplied text
# ---------------------------------------------------------------------------

class TestEscapeForEnvelope:
    """Unsafe characters become visible markers; safe text is UNCHANGED.

    TWO-SIDED ON PURPOSE. An arm that only asserts a bidi override becomes a
    marker cannot tell escaping from BLINDING: a function that replaced every
    character would pass it. The literal ASCII text "u202e" and ordinary ids
    must come back byte-identical, which is the direction a one-sided arm
    misses. Every character in this table is written as an escape, so the
    test says what it means to a reader.
    """

    @pytest.mark.parametrize("char,marker", [
        ("\u202e", "<U+202E>"),  # RIGHT-TO-LEFT OVERRIDE, the reordering hazard
        ("\x1b", "<U+001B>"),    # ESC, a C0 control
        ("\x85", "<U+0085>"),    # a C1 control
        ("\u2028", "<U+2028>"),  # LINE SEPARATOR (Zl)
        ("\u2029", "<U+2029>"),  # PARAGRAPH SEPARATOR (Zp)
    ])
    def test_an_unsafe_character_becomes_a_visible_marker(self, char, marker):
        from scripts.database import escape_for_envelope
        assert escape_for_envelope(f"a{char}b") == f"a{marker}b"

    def test_a_lone_surrogate_becomes_a_visible_marker(self):
        from scripts.database import escape_for_envelope
        assert escape_for_envelope("a\ud800b") == "a<U+D800>b"

    def test_safe_text_is_unchanged(self):
        from scripts.database import escape_for_envelope
        assert escape_for_envelope("u202e") == "u202e", (
            "the ASCII text that SPELLS a marker was escaped, so the rule "
            "blinds text rather than rendering it inert"
        )
        assert escape_for_envelope("plain-id_42") == "plain-id_42"

    def test_the_too_short_message_renders_the_prefix_inert(self):
        from scripts.database import PrefixTooShortError
        message = str(PrefixTooShortError("a\u202eb"))
        assert "<U+202E>" in message, message
        assert "\u202e" not in message, message


# ---------------------------------------------------------------------------
# #1719 remediation: adopted-identity guard, graph scope, bounded over-fetch
# ---------------------------------------------------------------------------


def _memory_store(tmp_path, name):
    """A store built by the PRODUCTION schema, with the graph tables.

    Built by `init_schema`, not `create_test_schema`: the two schemas differ
    (production declares `created_at`/`updated_at` NOT NULL with no default,
    the test schema defaults them), and a fixture that CLAIMS production
    schema while creating the test one hides the class of write that fails
    only in production. Rows inserted directly carry the timestamps —
    `_insert_memories` supplies them. Any raw INSERT added here must too.

    `init_schema` also attempts the vector table, so this helper does not
    mean "no vector table"; a test that needs its own fixed-dimension vec
    table drops the auto-created one first.
    """
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    conn.commit()
    return db_path, conn


def _insert_memories(conn, rows):
    for memory_id, project in rows:
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            (memory_id, "c", project, "s"),
        )
    conn.commit()


class TestTheDetectedProjectIdIsValidated:
    """The ADOPTED project id is validated at the point of BINDING, not at
    construction (Bugbot finding at ba744f08).

    A detected value comes from CLAUDE_PROJECT_DIR, a session record, the git
    root or the cwd. Refusing it in the constructor gated `get`/`update`/
    `delete` by FULL 32-character id — the identity-independent by-id
    capability — on a value those calls never bind. The rule now mirrors the
    detected session id: by-id commands work, project-scoped reads treat the
    value as unresolvable (empty/zeroed), and a write that would bind it
    refuses with ValueError carrying remedy text.
    """

    _BAD = "proj\udcff"

    def _memory(
        self,
        tmp_path,
        monkeypatch,
        value=_BAD,
        source="CLAUDE_PROJECT_DIR",
        db_path=None,
    ):
        from scripts.memory_api import PACTMemory

        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(lambda: memory_api._DetectedIdentity(value, source, None, None, None)),
        )
        return PACTMemory(
            project_id=None,
            session_id="s",
            db_path=db_path or (tmp_path / "d.db"),
        )

    def test_construction_adopts_the_detected_value(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch)
        assert memory.project_id == self._BAD, (
            "the detected value was silently dropped or altered; it is "
            "adopted AS FOUND and the binding paths guard it themselves"
        )
        assert not (tmp_path / "d.db").exists(), (
            "read-only construction performed store work"
        )

    def test_an_unresolved_detection_is_not_refused(self, tmp_path, monkeypatch):
        memory = self._memory(
            tmp_path, monkeypatch, value=None, source="unresolved"
        )
        assert memory.project_id is None

    def test_a_clean_detected_value_is_adopted(self, tmp_path, monkeypatch):
        memory = self._memory(
            tmp_path, monkeypatch, value="clean-project", source="cwd"
        )
        assert memory.project_id == "clean-project"

    def test_full_id_get_update_delete_work_under_an_unusable_detected_project(
        self, tmp_path, monkeypatch
    ):
        """The by-id capability is identity-independent, full 32-char ids."""
        from scripts.memory_api import PACTMemory

        db_path = tmp_path / "by-id.db"
        seed = PACTMemory(project_id="P", session_id="s", db_path=db_path)
        memory_id = seed.save({"context": "BY-ID"}, sync_to_claude=False)

        memory = self._memory(tmp_path, monkeypatch, db_path=db_path)
        got = memory.get(memory_id)
        assert got is not None and got.id == memory_id, (
            "a full 32-character id was gated by an unusable detected project"
        )
        assert memory.update(
            memory_id, {"context": "BY-ID-UPDATED"}
        ) == memory_id
        assert memory.delete(memory_id) == memory_id

    def test_scoped_reads_decline_to_empty_under_an_unusable_detected_project(
        self, tmp_path, monkeypatch
    ):
        """Unresolvable identity, never a binding and never an exception."""
        from scripts.memory_api import PACTMemory

        db_path = tmp_path / "reads.db"
        seed = PACTMemory(project_id="P", session_id="s", db_path=db_path)
        seed.save({"context": "READS"}, sync_to_claude=False)

        memory = self._memory(tmp_path, monkeypatch, db_path=db_path)
        assert memory.list() == []
        assert memory.search("READS") == []
        assert memory.search_by_file("/repo/x.py") == []
        status = memory.get_status()
        assert status["project_id"] is None
        assert status["memory_count"] == 0
        assert status["graph_stats"]["memories"] == 0

    def test_save_refuses_with_remedy_text_before_any_store_work(
        self, tmp_path, monkeypatch
    ):
        memory = self._memory(tmp_path, monkeypatch)
        with pytest.raises(ValueError) as excinfo:
            memory.save({"context": "X"}, sync_to_claude=False)
        message = str(excinfo.value)
        assert "CLAUDE_PROJECT_DIR" in message, message
        assert "Supply project_id explicitly" in message, message
        assert "\\udcff" in message, (
            "the unusable value was not rendered inert in the message that "
            f"reports it: {message!r}"
        )
        assert "\udcff" not in message, message
        assert not (tmp_path / "d.db").exists(), (
            "the adopted-project refusal performed store work"
        )

    @pytest.mark.parametrize("bad", ["proj\x00X", "proj\x85X"])
    def test_a_control_character_detected_value_is_refused_on_write(
        self, tmp_path, monkeypatch, bad
    ):
        """The CONTROL half of the shared rule on the DETECTION branch.

        `_BAD` above is a lone surrogate, so the surrogate clause is pinned;
        without these cells a regression limiting the detected-value check to
        surrogates would ship green, and the write would store a project id
        no envelope could render raw. A U+0000 (C0) and a U+0085 (C1) cell
        cover both ends of the control clause.
        """
        memory = self._memory(tmp_path, monkeypatch, value=bad)
        with pytest.raises(ValueError) as excinfo:
            memory.save({"context": "X"}, sync_to_claude=False)
        message = str(excinfo.value)
        assert "not a non-empty string free of control characters" in message, message
        assert "Supply project_id explicitly" in message, message
        assert not (tmp_path / "d.db").exists(), (
            "the adopted-project refusal performed store work"
        )

    def test_a_payload_project_bypasses_the_adopted_guard(
        self, tmp_path, monkeypatch
    ):
        """The guard keys on the value that WOULD be bound, not the instance."""
        memory = self._memory(
            tmp_path, monkeypatch, db_path=tmp_path / "payload.db"
        )
        memory_id = memory.save(
            {"context": "WARRANTED", "project_id": "explicit"},
            sync_to_claude=False,
        )
        assert memory.get(memory_id).project_id == "explicit"


class TestGraphContextConfirmsTheStoredProject:
    """A stale link on a project-scoped file row must not leak a memory.

    The file row resolved for (path, project) is the right row, but its links
    can predate a re-key: the memory's OWN stored project is the fact.
    """

    def _fixture(self, tmp_path):
        db_path, conn = _memory_store(tmp_path, "graph-scope.db")
        _insert_memories(conn, [("m-p1", "P1"), ("m-p2", "P2")])
        for file_id, path, project in (
            ("f-a-p2", "/repo/a.py", "P2"),
            # The NULL-project row an unscoped call resolves, so the control
            # arm exercises the same path with the confirmation switched off.
            ("f-a-null", "/repo/a.py", None),
            ("f-related-p1", "/repo/related_p1.py", "P1"),
            ("f-related-p2", "/repo/related_p2.py", "P2"),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for memory_id, file_id in (
            ("m-p1", "f-a-p2"),      # the stale link
            ("m-p2", "f-a-p2"),      # the valid link
            ("m-p1", "f-a-null"),
            ("m-p2", "f-a-null"),
            ("m-p1", "f-related-p1"),
            ("m-p2", "f-related-p2"),
        ):
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id, relationship)"
                " VALUES (?, ?, 'modified')",
                (memory_id, file_id),
            )
        conn.commit()
        return conn

    def test_file_context_confirms_the_memorys_stored_project(self, tmp_path):
        from scripts import graph

        conn = self._fixture(tmp_path)
        with patch("scripts.graph.ensure_initialized"):
            scoped = graph.get_file_context(conn, "/repo/a.py", "P2")
        assert scoped["tracked"] is True
        assert scoped["memory_ids"] == ["m-p2"], (
            "a memory stored under another project was returned for a "
            f"project-scoped file context: {scoped['memory_ids']!r}"
        )

    def test_related_files_are_confirmed_by_their_stored_project(self, tmp_path):
        from scripts import graph

        conn = self._fixture(tmp_path)
        with patch("scripts.graph.ensure_initialized"):
            related = graph.get_related_files_via_memories(
                conn, "/repo/a.py", "P2"
            )
        assert related == ["/repo/related_p2.py"], (
            "a related file row from another project was returned: "
            f"{related!r}"
        )

    def test_an_unscoped_call_still_returns_the_whole_store(self, tmp_path):
        """Control: the confirmation narrows a SCOPED call only."""
        from scripts import graph

        conn = self._fixture(tmp_path)
        with patch("scripts.graph.ensure_initialized"):
            everything = graph.get_file_context(conn, "/repo/a.py", None)
            related = graph.get_related_files_via_memories(
                conn, "/repo/a.py", None
            )
        assert sorted(everything["memory_ids"]) == ["m-p1", "m-p2"]
        # The unscoped row is the NULL-project one, so its shared memories
        # reach every project's files; that breadth IS the control.
        assert sorted(related) == [
            "/repo/a.py", "/repo/related_p1.py", "/repo/related_p2.py",
        ]

    def test_file_relations_do_not_name_another_projects_file(self, tmp_path):
        """A legacy cross-project edge is the one way a scoped relation walk
        can reach another project's file row. `add_file_relation` resolves
        both endpoints under ONE project, so the API cannot create one; a
        backfilled or hand-inserted row can, and the scoped read must not
        hand its path back. The write side is out of scope by ruling."""
        from scripts import graph

        _db_path, conn = _memory_store(tmp_path, "relations-scope.db")
        for file_id, path, project in (
            ("f-a", "/repo/a.py", "P1"),
            ("f-b-p1", "/repo/b.py", "P1"),
            ("f-other-p2", "/repo/other_project.py", "P2"),
            ("f-a-null", "/repo/a.py", None),
            ("f-null-only", "/repo/null.py", None),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for source, target in (
            ("f-a", "f-b-p1"),          # a legitimate same-project edge
            ("f-a", "f-other-p2"),      # the legacy cross-project edge
            ("f-a-null", "f-null-only"),
        ):
            conn.execute(
                "INSERT INTO file_relations"
                " (source_file, target_file, relationship)"
                " VALUES (?, ?, 'imports')",
                (source, target),
            )
        conn.commit()

        with patch("scripts.graph.ensure_initialized"):
            scoped = graph.get_file_relations(conn, "/repo/a.py", "P1")
            unscoped = graph.get_file_relations(conn, "/repo/a.py", None)
        assert [row["path"] for row in scoped] == ["/repo/b.py"], (
            "a scoped relation walk returned another project's file row: "
            f"{[row['path'] for row in scoped]!r}"
        )
        assert [row["path"] for row in unscoped] == ["/repo/null.py"], (
            "the unscoped control was narrowed: "
            f"{[row['path'] for row in unscoped]!r}"
        )

    def test_an_incoming_cross_project_relation_is_not_returned(self, tmp_path):
        """The INCOMING half of the endpoint filter, which had no arm.

        `file_relations` carries no project column, so a legacy/backfilled
        edge can point from another project's file INTO this one. The
        outgoing arm above drives fr.source_file; this one drives
        fr.target_file, and `get_file_context` inherits it because its
        default direction is "both".

        Both directions are measured on ONE fixture. The NULL-project row
        `/repo/a.py` is the unscoped control: with project_id=None the
        resolver picks it up and the same foreign edge IS visible, so the
        scoped [] is the filter's doing rather than an absent fixture row.
        """
        from scripts import graph

        _db_path, conn = _memory_store(tmp_path, "incoming-relation.db")
        for file_id, path, project in (
            ("f-a-p1", "/repo/a.py", "P1"),
            ("f-a-null", "/repo/a.py", None),
            ("f-b-p1", "/repo/b.py", "P1"),
            ("f-other-p2", "/repo/secret_project.py", "P2"),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for source, target in (
            ("f-a-p1", "f-b-p1"),      # the same-project OUTGOING control
            ("f-other-p2", "f-a-p1"),  # the cross-project INCOMING edge
            ("f-other-p2", "f-a-null"),  # its unscoped-control twin
        ):
            conn.execute(
                "INSERT INTO file_relations"
                " (source_file, target_file, relationship)"
                " VALUES (?, ?, 'imports')",
                (source, target),
            )
        conn.commit()

        with patch("scripts.graph.ensure_initialized"):
            incoming = graph.get_file_relations(
                conn, "/repo/a.py", "P1", direction="incoming"
            )
            both = graph.get_file_relations(conn, "/repo/a.py", "P1")
            context = graph.get_file_context(conn, "/repo/a.py", "P1")
            unscoped = graph.get_file_relations(conn, "/repo/a.py", None)

        assert incoming == [], (
            "a cross-project INCOMING edge returned another project's file "
            f"row: {[row['path'] for row in incoming]!r}"
        )
        assert [row["path"] for row in both] == ["/repo/b.py"], (
            "the incoming filter removed the same-project outgoing edge too: "
            f"{[row['path'] for row in both]!r}"
        )
        assert [row["path"] for row in context["direct_relations"]] == [
            "/repo/b.py"
        ], context["direct_relations"]
        # THE UNSCOPED CONTROL: the fixture really does carry the foreign
        # edge, reachable when the NULL-project row is the one resolved.
        assert [row["path"] for row in unscoped] == ["/repo/secret_project.py"], (
            "the unscoped read lost its whole-store meaning: "
            f"{[row['path'] for row in unscoped]!r}"
        )


class TestTheBatchFileReadsConfirmTheStoredProject:
    """The two batch/file-list helpers, each pinned directly.

    `get_memories_for_files` is the batch wrapper `_get_graph_related_memories`
    calls; a reverted pass-through leaves its callers to a belt that may not
    exist. `get_files_for_memory` is the memory-to-files direction: the LINK
    carries no project, so a stale link from an interrupted re-key attaches
    another project's path to a confirmed memory. One memory linked to two
    same-project files and one cross-project file pins all three counts.
    """

    def test_get_memories_for_files_excludes_a_p2_linked_memory(self, tmp_path):
        from scripts import graph

        _db_path, conn = _memory_store(tmp_path, "batch-file-memories.db")
        _insert_memories(conn, [("m-p1", "P1"), ("m-p2", "P2")])
        for file_id, path, project in (
            ("f-a-p1", "/repo/a.py", "P1"),
            ("f-a-null", "/repo/a.py", None),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for memory_id, file_id in (("m-p1", "f-a-p1"), ("m-p2", "f-a-p1"),
                                   ("m-p1", "f-a-null"), ("m-p2", "f-a-null")):
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id, relationship)"
                " VALUES (?, ?, 'modified')",
                (memory_id, file_id),
            )
        conn.commit()

        with patch("scripts.graph.ensure_initialized"):
            scoped = graph.get_memories_for_files(conn, ["/repo/a.py"], "P1")
            unscoped = graph.get_memories_for_files(conn, ["/repo/a.py"], None)

        assert scoped == ["m-p1"], (
            "the batch wrapper passed no project and returned another "
            f"project's memory: {scoped!r}"
        )
        assert set(unscoped) == {"m-p1", "m-p2"}, (
            "the unscoped control was narrowed: "
            f"{unscoped!r}"
        )

    def test_get_files_for_memory_confirms_each_files_stored_project(
        self, tmp_path
    ):
        from scripts import graph

        _db_path, conn = _memory_store(tmp_path, "memory-files-matrix.db")
        _insert_memories(conn, [("m-p1", "P1")])
        for file_id, path, project in (
            ("f-a-p1", "/repo/a.py", "P1"),
            ("f-b-p1", "/repo/b.py", "P1"),
            ("f-c-p2", "/repo/c.py", "P2"),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id, relationship)"
                " VALUES (?, ?, 'modified')",
                ("m-p1", file_id),
            )
        conn.commit()

        with patch("scripts.graph.ensure_initialized"):
            unscoped = graph.get_files_for_memory(conn, "m-p1")
            own = graph.get_files_for_memory(conn, "m-p1", "P1")
            other = graph.get_files_for_memory(conn, "m-p1", "P2")

        assert {f["path"] for f in unscoped} == {
            "/repo/a.py", "/repo/b.py", "/repo/c.py"
        }, unscoped
        assert {f["path"] for f in own} == {"/repo/a.py", "/repo/b.py"}, own
        assert {f["path"] for f in other} == {"/repo/c.py"}, (
            "the filter did not key on the FILE's stored project: "
            f"{[f['path'] for f in other]!r}"
        )

    def test_get_graph_stats_counts_only_rows_visible_to_the_scope(
        self, tmp_path
    ):
        """A legacy cross-project link/relation is visible from NEITHER
        scope, so counting it under either would be false; the unscoped count
        still includes it (whole-store)."""
        from scripts import graph

        _db_path, conn = _memory_store(tmp_path, "stats-scope.db")
        _insert_memories(conn, [("m-p1", "P1"), ("m-z-p1", "P1"),
                                ("m-p2", "P2"), ("m-z-p2", "P2")])
        for file_id, path, project in (
            ("f-p1", "/repo/p1.py", "P1"),
            ("f-p2", "/repo/p2.py", "P2"),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for memory_id, file_id in (
            ("m-p1", "f-p1"), ("m-z-p1", "f-p1"),   # P1's own links
            ("m-p2", "f-p2"), ("m-z-p2", "f-p2"),   # P2's own links
            ("m-p1", "f-p2"),                        # legacy cross-project link
        ):
            conn.execute(
                "INSERT INTO memory_files (memory_id, file_id, relationship)"
                " VALUES (?, ?, 'modified')",
                (memory_id, file_id),
            )
        for source, target in (
            ("f-p1", "f-p1"),  # self-loop, same project (one P1 relation)
            ("f-p2", "f-p2"),  # same project (one P2 relation)
            ("f-p1", "f-p2"),  # legacy cross-project relation
        ):
            conn.execute(
                "INSERT INTO file_relations"
                " (source_file, target_file, relationship)"
                " VALUES (?, ?, 'imports')",
                (source, target),
            )
        conn.commit()

        with patch("scripts.graph.ensure_initialized"):
            p1 = graph.get_graph_stats(conn, "P1")
            p2 = graph.get_graph_stats(conn, "P2")
            whole = graph.get_graph_stats(conn, None)

        assert (p1["memory_file_links"], p1["file_relations"]) == (2, 1), p1
        assert (p2["memory_file_links"], p2["file_relations"]) == (2, 1), p2
        assert (whole["memory_file_links"], whole["file_relations"]) == (5, 3), whole


class TestTheRelationWalkConfirmsEveryNeighbour:
    """`get_related_files`: the START is scoped; a NEIGHBOUR is not, unless
    confirmed.

    `file_relations` has no project column, so a legacy edge can join two
    projects. An unconfirmed neighbour is worse than one leaked path: its id
    is the next hop's cursor, so the walk would also expose the rest of the
    foreign project's graph. The NULL-project start is the unscoped control —
    there the whole-store meaning is intended and still spans projects.
    """

    @staticmethod
    def _fixture(tmp_path, name="relation-walk.db"):
        db_path, conn = _memory_store(tmp_path, name)
        for file_id, path, project in (
            ("f-a-p1", "/A/a.py", "P1"),
            ("f-b-p1", "/A/b.py", "P1"),
            ("f-secret-p2", "/B/secret.py", "P2"),
            ("f-deep-p2", "/B/deep.py", "P2"),
            ("f-a-null", "/A/a.py", None),
        ):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (file_id, path, project),
            )
        for source, target in (
            ("f-a-p1", "f-b-p1"),        # same-project neighbour (control)
            ("f-a-p1", "f-secret-p2"),   # legacy cross-project edge
            ("f-secret-p2", "f-deep-p2"),  # reachable ONLY by traversing it
            ("f-a-null", "f-secret-p2"),   # the unscoped control's edge
        ):
            conn.execute(
                "INSERT INTO file_relations"
                " (source_file, target_file, relationship)"
                " VALUES (?, ?, 'imports')",
                (source, target),
            )
        conn.commit()
        return db_path, conn

    def test_a_cross_project_neighbour_is_neither_returned_nor_traversed(
        self, tmp_path
    ):
        from scripts import graph

        _db_path, conn = self._fixture(tmp_path)
        with patch("scripts.graph.ensure_initialized"):
            depth1 = graph.get_related_files(conn, "/A/a.py", "P1", max_depth=1)
            depth2 = graph.get_related_files(conn, "/A/a.py", "P1", max_depth=2)
            unscoped = graph.get_related_files(conn, "/A/a.py", None, max_depth=1)

        assert depth1 == ["/A/b.py"], (
            "a cross-project neighbour was returned by a scoped walk: "
            f"{depth1!r}"
        )
        assert depth2 == ["/A/b.py"], (
            "the scoped walk TRAVERSED THROUGH the foreign endpoint and "
            f"reached its graph: {depth2!r}"
        )
        assert unscoped == ["/B/secret.py"], (
            "the unscoped walk from a NULL-project start lost its "
            f"whole-store meaning: {unscoped!r}"
        )

    def test_discover_related_inherits_the_neighbour_scope(self, tmp_path):
        from scripts import graph
        from scripts.config import store_scope

        db_path, conn = self._fixture(tmp_path, "discover-walk.db")
        conn.close()
        with store_scope(db_path), patch("scripts.graph.ensure_initialized"):
            related = graph.discover_related("/A/a.py", "P1")
        assert sorted(related) == ["/A/b.py"], related


class TestTrackFileAdoptsARaceLoser:
    """Two writers that both miss the SELECT must converge on one file row.

    The interleaving is forced deterministically: the loser's `generate_id`
    call runs AFTER its SELECT has missed and lets a second connection create
    and commit the row, so the loser's INSERT is the one that conflicts.
    """

    def test_a_lost_create_race_adopts_the_winners_row(self, tmp_path):
        from scripts import graph

        db_path = tmp_path / "track-race.db"
        loser = sqlite3.connect(str(db_path))
        winner = sqlite3.connect(str(db_path))
        for conn in (loser, winner):
            conn.row_factory = sqlite3.Row
            create_test_schema(conn)
            conn.commit()

        path = "/repo/raced.py"
        real_generate_id = graph.generate_id
        seen = {}

        def racing_generate_id():
            if not seen.get("racing"):
                # The other writer's whole track_file runs now, between this
                # call's SELECT (already missed) and its INSERT. The flag is
                # set BEFORE the reentrant call, or that call re-enters here.
                seen["racing"] = True
                seen["winner_id"] = graph.track_file(winner, path, "P")
            return real_generate_id()

        with patch("scripts.graph.ensure_initialized"), patch.object(
            graph, "generate_id", racing_generate_id
        ):
            loser_id = graph.track_file(loser, path, "P")

        assert loser_id == seen["winner_id"], (
            "the racer returned a different file id than the committed row"
        )
        rows = loser.execute(
            "SELECT id FROM files WHERE path = ? AND project_id = ?",
            (path, "P"),
        ).fetchall()
        assert [row["id"] for row in rows] == [seen["winner_id"]]


class TestAnEmptyStringScopeMatchesNoRows:
    """`project_id=""` is an EMPTY scope: no rows, never the whole store.

    `None` stays the whole-store scope, so each arm keeps the `None` call as
    the control that the fixture's rows are reachable at all.
    """

    def _fixture(self, tmp_path):
        # The UNPROJECTED rows: an unscoped call resolves the NULL-project
        # file row, so this is the pair the old whole-store meaning addressed.
        db_path, conn = _memory_store(tmp_path, "empty-scope.db")
        _insert_memories(conn, [("m-null", None)])
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            ("f-a", "/repo/a.py", None),
        )
        conn.execute(
            "INSERT INTO memory_files (memory_id, file_id, relationship)"
            " VALUES (?, ?, 'modified')",
            ("m-null", "f-a"),
        )
        conn.commit()
        return db_path, conn

    def test_search_by_file_treats_an_empty_string_as_no_rows(self, tmp_path):
        from scripts import search
        from scripts.config import store_scope

        db_path, _conn = self._fixture(tmp_path)
        with store_scope(db_path), patch(
            "scripts.search.ensure_initialized"
        ), patch("scripts.graph.ensure_initialized"):
            empty = search.search_by_file("/repo/a.py", project_id="")
            absent = search.search_by_file("/repo/a.py", project_id=None)
        assert empty == []
        assert [memory.id for memory in absent] == ["m-null"]

    def test_vector_search_treats_an_empty_string_as_no_rows(self, tmp_path):
        import struct

        import sqlite_vec
        from scripts import search

        db_path, conn = _memory_store(tmp_path, "empty-scope-vec.db")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        # The production schema already created a vec table at the model's
        # dimension; this arm needs a known 4-dimension one.
        conn.execute("DROP TABLE IF EXISTS vec_memories")
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[4])"
        )
        _insert_memories(conn, [("m-p1", "P1")])
        conn.execute(
            "INSERT INTO vec_memories (memory_id, project_id, embedding)"
            " VALUES (?, ?, ?)",
            ("m-p1", "P1", struct.pack("4f", 1.0, 0.0, 0.0, 0.0)),
        )
        conn.commit()
        with patch.object(
            search, "generate_embedding", lambda text: [1.0, 0.0, 0.0, 0.0]
        ):
            empty = search.vector_search(conn, "q", "", limit=3)
            absent = search.vector_search(conn, "q", None, limit=3)
        assert empty == []
        assert absent == [("m-p1", 0.0)]

    def test_graph_enhanced_search_treats_an_empty_string_as_no_rows(
        self, tmp_path, monkeypatch
    ):
        """The public entry whose ranking leg adds ids without a project check.

        The ranking is stubbed to return a NULL-project memory, so only the
        stored-project belt stands between an empty scope and that row. RED
        WHEN `""` is folded to `None` (the belt is then skipped and the row is
        returned) or the belt is dropped.
        """
        from scripts import search
        from scripts.config import store_scope

        db_path, _conn = self._fixture(tmp_path)

        def fake_boosted(conn, query, current_file, project_id, fetch):
            return [("m-null", 1.0)]

        monkeypatch.setattr(search, "graph_boosted_search", fake_boosted)
        with store_scope(db_path), patch(
            "scripts.search.ensure_initialized"
        ), patch("scripts.graph.ensure_initialized"):
            empty = search.graph_enhanced_search("q", project_id="")
            absent = search.graph_enhanced_search("q", project_id=None)
        assert empty == [], (
            "graph_enhanced_search read the empty scope as the whole store"
        )
        assert [memory.id for memory in absent] == ["m-null"]


class TestTheConfirmationFiltersOverFetch:
    """A discarded stale hit must not leave the caller short of `limit`."""

    def _graph_store(self, tmp_path):
        db_path, conn = _memory_store(tmp_path, "graph-overfetch.db")
        _insert_memories(
            conn,
            [("s0", "P1"), ("s1", "P1"), ("s2", "P1"), ("s3", "P1")]
            + [("f0", "P2"), ("f1", "P2"), ("f2", "P2")],
        )
        return db_path, conn

    def test_search_by_file_widens_past_stale_links(self, tmp_path, monkeypatch):
        from scripts import search
        from scripts.config import store_scope

        db_path, _conn = self._graph_store(tmp_path)
        ordered = ["s0", "s1", "s2", "s3", "f0", "f1", "f2"]
        monkeypatch.setattr(
            search, "_get_graph_related_memories",
            lambda *args, **kwargs: ordered,
        )
        with store_scope(db_path), patch(
            "scripts.search.ensure_initialized"
        ), patch("scripts.graph.ensure_initialized"):
            result = search.search_by_file(
                "/repo/x.py", project_id="P2", limit=3
            )
        assert [memory.id for memory in result] == ["f0", "f1", "f2"], (
            "a stale link shortened the result below the requested limit"
        )

    def test_graph_enhanced_search_widens_its_ranking(self, tmp_path, monkeypatch):
        from scripts import search
        from scripts.config import store_scope

        db_path, _conn = self._graph_store(tmp_path)
        calls = []

        def fake_boosted(conn, query, current_file, project_id, fetch):
            calls.append(fetch)
            ranked = ["s0", "s1", "s2", "s3", "f0", "f1", "f2"]
            return [(memory_id, 1.0) for memory_id in ranked[:fetch]]

        monkeypatch.setattr(search, "graph_boosted_search", fake_boosted)
        with store_scope(db_path), patch("scripts.search.ensure_initialized"):
            result = search.graph_enhanced_search(
                "q", project_id="P2", limit=3
            )
        assert [memory.id for memory in result] == ["f0", "f1", "f2"], (
            "the belt filtered stale ids but the ranking was never widened"
        )
        assert calls == [3, 6, 12], (
            "the widening did not rerun the ranking with a larger limit: "
            f"{calls!r}"
        )

    def test_a_pathological_store_stops_at_the_cap(self, tmp_path, monkeypatch):
        from scripts import search
        from scripts.config import store_scope

        db_path, _conn = self._graph_store(tmp_path)
        calls = []

        def all_stale(conn, query, current_file, project_id, fetch):
            calls.append(fetch)
            return [("s0", 1.0)] * fetch

        monkeypatch.setattr(search, "graph_boosted_search", all_stale)
        with store_scope(db_path), patch("scripts.search.ensure_initialized"):
            result = search.graph_enhanced_search(
                "q", project_id="P2", limit=3
            )
        assert result == []
        # The GRAPH leg's ceiling, not the vector leg's: each step here
        # re-runs graph_boosted_search, which generates an embedding.
        assert calls[-1] == search.GRAPH_CONFIRMATION_FETCH_CAP, calls
        assert search.GRAPH_CONFIRMATION_FETCH_CAP < search.CONFIRMATION_FETCH_CAP
        assert len(calls) < 10, (
            f"the widen loop did not stop at the cap: {calls!r}"
        )

    def test_the_vector_reserve_is_filled_through_stale_partitions(
        self, tmp_path
    ):
        """The SQL LIMIT runs before the confirmation; widen it, not only the
        slice, or a stale top-k still shorts the caller."""
        import struct

        import sqlite_vec
        from scripts import search

        db_path, conn = _memory_store(tmp_path, "vec-overfetch.db")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        # The production schema already created a vec table at the model's
        # dimension; this arm needs a known 4-dimension one.
        conn.execute("DROP TABLE IF EXISTS vec_memories")
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[4])"
        )
        _insert_memories(
            conn,
            # The stale rows' MEMORIES are P1; their VECTOR rows sit in the
            # P2 partition, which is the mismatch the confirmation catches
            # (the query's partition filter cannot see it).
            [(f"s{i}", "P1") for i in range(5)]
            + [(f"v{i}", "P2") for i in range(3)],
        )
        exact = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        farther = struct.pack("4f", 0.9, 0.1, 0.0, 0.0)
        for i in range(5):
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding)"
                " VALUES (?, ?, ?)",
                (f"s{i}", "P2", exact),
            )
        for i in range(3):
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding)"
                " VALUES (?, ?, ?)",
                (f"v{i}", "P2", farther),
            )
        conn.commit()
        with patch.object(
            search, "generate_embedding", lambda text: [1.0, 0.0, 0.0, 0.0]
        ):
            rows = search.vector_search(conn, "q", "P2", limit=3)
        assert sorted(memory_id for memory_id, _ in rows) == ["v0", "v1", "v2"], (
            "the stale top-k consumed the SQL LIMIT and shortened the result"
        )

    def test_the_vector_leg_stops_at_its_cap(self, tmp_path):
        """The VECTOR leg's bound, mirroring the graph-leg cap arm.

        Every top hit is stale (memories in P1, vector rows in P2's
        partition), so the confirmation discards them all and the fetch
        widens: 3, 6, 12, ... up to CONFIRMATION_FETCH_CAP. The recorded
        LIMIT values are the evidence — a store that merely runs out of rows
        would also terminate, so termination alone proves nothing.
        """
        import struct

        import sqlite_vec
        from scripts import search

        db_path, conn = _memory_store(tmp_path, "vec-cap.db")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute("DROP TABLE IF EXISTS vec_memories")
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[4])"
        )
        stale_count = search.CONFIRMATION_FETCH_CAP
        _insert_memories(conn, [(f"s{i}", "P1") for i in range(stale_count)])
        exact = struct.pack("4f", 1.0, 0.0, 0.0, 0.0)
        for i in range(stale_count):
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding)"
                " VALUES (?, ?, ?)",
                (f"s{i}", "P2", exact),
            )
        conn.commit()

        class _RecordingConnection:
            """Delegates everything; records each vector query's LIMIT."""

            def __init__(self, inner):
                self._inner = inner
                self.limits = []

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def execute(self, sql, params=()):
                if "FROM vec_memories" in sql and params:
                    self.limits.append(params[-1])
                return self._inner.execute(sql, params)

        proxy = _RecordingConnection(conn)
        with patch.object(
            search, "generate_embedding", lambda text: [1.0, 0.0, 0.0, 0.0]
        ):
            rows = search.vector_search(proxy, "q", "P2", limit=3)

        assert rows == [], rows
        # THE CLAIMED BOUND, not just the constant's current value: the PR
        # body states 200 for the vector leg, and comparing against
        # `search.CONFIRMATION_FETCH_CAP` alone would still pass if the
        # constant itself were raised.
        assert search.CONFIRMATION_FETCH_CAP == 200, (
            f"the vector leg's claimed bound moved: {search.CONFIRMATION_FETCH_CAP}"
        )
        assert proxy.limits[-1] == 200, (
            "the vector leg did not stop at its own ceiling: "
            f"{proxy.limits!r}"
        )
        assert proxy.limits == sorted(proxy.limits), (
            f"the fetch window shrank instead of widening: {proxy.limits!r}"
        )


# ---------------------------------------------------------------------------
# Identity guards and the re-key cascade (relocated from the worktree-record
# module, whose subject is the record rather than the store).
# ---------------------------------------------------------------------------

class TestUpdatePayloadProjectIdGuard:
    """An update payload cannot de-key or re-key a row (#1688 close-out).

    `update_memory` scalar-replaces `project_id`, and the CLI passes caller
    JSON through verbatim (cmd_update), so `{"project_id": null}` could write
    a NULL key no reader can address. Mirrors the save() F2 family with the
    OPPOSITE null boundary: on update, explicit null IS the hazard and
    omission is the no-op, so the guard keys on presence, not truthiness.
    """

    @staticmethod
    def _row_project(db_path: Path, memory_id: str):
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return row[0] if row else None

    @pytest.mark.parametrize("field", ["project_id", "session_id"])
    @pytest.mark.parametrize(
        "bad",
        [
            None, "", 123,
            "bad\x00id", "bad\nid", "bad\tid", "bad\x1fid",
            "bad\x7fid", "bad\x85id", "bad\x9fid",
            "bad\ud800id",
        ],
    )
    def test_rejects_a_dekeying_or_mis_keying_payload(
        self, tmp_path, field, bad
    ):
        """Null (the de-key), empty, non-str, control-char and lone-surrogate
        values refuse, for BOTH scalar identifiers.

        The control sweep mirrors the save-side F2 family's seven values:
        the update guard's C1/DEL clause is pinned by cells that fail
        without it, not only by save()'s arms. The surrogate cell pins the
        clause that keeps an unencodable value off the utf-8 TEXT binding.
        session_id rides the same guard: `update_memory` scalar-replaces it
        and the CLI passes it through verbatim on the same path.
        """
        db_path = tmp_path / "update-guard.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "UPDATE-GUARD"}, sync_to_claude=False)

        with pytest.raises(ValueError) as excinfo:
            memory.update(memory_id, {field: bad})

        assert str(excinfo.value).startswith(
            f"{field}, when supplied to update, must be a non-empty string "
        ), f"refused for a different reason: {str(excinfo.value)!r}"
        assert self._row_project(db_path, memory_id) == "detected-b", (
            "a refused payload changed the row; the guard must precede the "
            "store write"
        )

    def test_omission_proceeds_with_no_identity_requirement(
        self, tmp_path, monkeypatch
    ):
        """An update not touching project_id needs no identity (subset guard).

        THE INSTANCE MUST HAVE NO IDENTITY, and the first version of this arm
        did not give it one: it built a RESOLVED instance, so it could not
        detect the regression its name describes — adding an identity
        requirement to update() left it green (measured). The row is therefore
        created by a resolved instance and the UPDATE is issued by an
        unresolved one.
        """
        db_path = tmp_path / "update-omission.db"
        seed = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = seed.save({"context": "UPDATE-OMISSION"}, sync_to_claude=False)

        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(lambda: memory_api._DetectedIdentity(None, "unresolved", None, None, None)),
        )
        memory = PACTMemory(session_id="s", db_path=db_path)
        assert memory.project_id is None, (
            "the fixture resolved an identity, so the update below would not "
            "exercise the identity-free path"
        )

        resolved = memory.update(memory_id, {"context": "UPDATE-OMISSION-2"})

        assert resolved == memory_id
        assert self._row_project(db_path, memory_id) == "detected-b"

    def test_a_valid_string_rekeys_the_row(self, tmp_path):
        """An explicit non-empty string is a warranted rekey, still accepted."""
        db_path = tmp_path / "update-warrant.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "UPDATE-WARRANT"}, sync_to_claude=False)

        resolved = memory.update(memory_id, {"project_id": "warranted-c"})

        assert resolved == memory_id
        assert self._row_project(db_path, memory_id) == "warranted-c"

    def test_a_warranted_rekey_cascades_to_every_project_keyed_row(
        self, tmp_path
    ):
        """C-S1: the re-keyed row stays addressable from its new project.

        files rows are keyed (path, project_id) and vec_memories partitions
        by project_id, so a re-key that moved only the memories row would
        strand the file link and the vector in the old project: the row
        would vanish from search_by_file under the new key while remaining
        findable under the old one. The pre-rekey assertion proves the
        fixture live so the moved-state assertions cannot pass vacuously.
        """
        db_path = tmp_path / "cascade.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        old = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = old.save(
            {"context": "CASCADE"}, files=[str(linked)], sync_to_claude=False
        )
        assert [m.id for m in old.search_by_file(str(linked))] == [memory_id], (
            "the live fixture did not link the file; the cascade assertions "
            "below would prove nothing"
        )

        assert old.update(memory_id, {"project_id": "warranted-c"}) == memory_id

        new = PACTMemory(project_id="warranted-c", session_id="s", db_path=db_path)
        old_view = PACTMemory(
            project_id="detected-b", session_id="s", db_path=db_path
        )
        assert [m.id for m in new.search_by_file(str(linked))] == [memory_id], (
            "the re-keyed row is not addressable from its new project"
        )
        assert [m.id for m in old_view.search_by_file(str(linked))] == [], (
            "the re-keyed row is still addressable from its old project"
        )

        # The vec partition followed: no vector remains under the old key.
        # The assertion is UNCONDITIONAL. It used to vanish when sqlite_vec
        # would not load, on the reasoning that a process which cannot embed
        # never wrote a vector — false for a shared store, where another
        # process wrote one and this one merely cannot read it. A store with
        # a vec0 table that this process cannot open is an environment that
        # failed to measure the claim, so it fails loudly instead of
        # reporting a pass. The degraded case has its own arm, with a
        # faithful simulation, in
        # TestAVectorHitIsConfirmedAgainstTheStoredProject.
        with db_connection(db_path) as conn:
            has_vec = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='vec_memories'"
            ).fetchone() is not None
            assert has_vec, (
                "the fixture's store has no vector table, so the partition "
                "claim below was not measured"
            )
            import sqlite_vec

            sqlite_vec.load(conn)
            stale = conn.execute(
                "SELECT COUNT(*) FROM vec_memories "
                "WHERE memory_id = ? AND project_id = ?",
                (memory_id, "detected-b"),
            ).fetchone()[0]
            assert stale == 0, (
                "a vector remains in the old project's partition"
            )

    def test_a_rekeys_links_survive_an_embedding_fault(
        self, tmp_path, monkeypatch
    ):
        """Cycle 4 (Bugbot B1): the links commit BEFORE the embedding call.

        _store_embedding's fault path rolls the connection back. Link
        writes left in that transaction would be undone with it, leaving
        the row addressable only under its OLD key — the state the
        cascade exists to retire. save() commits its links before
        embedding for the same reason; this pins the same ordering on the
        re-key path.

        THE MIGRATION COMMIT IS NEUTRALIZED because it defends this
        ordering only incidentally: `get_memory` -> `ensure_initialized`
        -> `_migrate_ct_fields` ends in `conn.commit()`, which flushes the
        pending link writes as a side effect BEFORE the embedding call.
        Without this isolation the arm passes with OR without the
        cascade's own commit (measured), proving nothing. The status
        assert proves the fault stub ran, so the surviving-links assert
        cannot pass for the wrong reason.
        """
        db_path = tmp_path / "fault.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        old = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = old.save(
            {"context": "FAULT"}, files=[str(linked)], sync_to_claude=False
        )
        assert [m.id for m in old.search_by_file(str(linked))] == [memory_id], (
            "the live fixture did not link the file; the fault arm below "
            "would prove nothing"
        )

        monkeypatch.setattr(
            scripts_database, "_migrate_ct_fields", lambda conn: None
        )

        def _rollback_fault(conn, memory_id, memory):
            conn.rollback()
            return "fault"

        monkeypatch.setattr(
            type(old), "_store_embedding", staticmethod(_rollback_fault)
        )
        assert old.update(memory_id, {"project_id": "warranted-c"}) == memory_id
        assert old.last_embedding_status == "fault", (
            "the fault stub never ran; the surviving-links assertion was "
            "not exercising the seam"
        )

        new = PACTMemory(project_id="warranted-c", session_id="s", db_path=db_path)
        assert [m.id for m in new.search_by_file(str(linked))] == [memory_id], (
            "the re-pointed links were rolled back with the embedding fault"
        )

    def test_a_faulted_content_reembed_does_not_skip_the_cascade_reembed(
        self, tmp_path, monkeypatch
    ):
        """Cycle 5 (Bugbot B2): vector_current means the vector IS current.

        The content path's re-embed can FAULT — its rollback restores the
        original vector and nothing is written — and that must not read as
        'already stored under the new key' to the cascade. A counting
        fault stub pins the skip condition from both sides: a combined
        content+rekey update stores TWICE (content path, then cascade,
        because the fault left the vector not-current) and a content-only
        update stores ONCE (no re-key, no cascade) — so the cascade
        re-store fires on fault and only on fault-supplemented skips.
        """
        calls: list = []

        def _counting_fault(conn, memory_id, memory):
            calls.append(memory_id)
            conn.rollback()
            return "fault"

        monkeypatch.setattr(
            PACTMemory, "_store_embedding", staticmethod(_counting_fault)
        )

        # Combined: content fields AND a warranted re-key in one update.
        db_path = tmp_path / "skip.db"
        combined = PACTMemory(
            project_id="detected-b", session_id="s", db_path=db_path
        )
        memory_id = combined.save(
            {"context": "SKIP-SEED"}, sync_to_claude=False
        )
        calls.clear()
        assert combined.update(
            memory_id, {"context": "SKIP-CHANGED", "project_id": "warranted-c"}
        ) == memory_id
        assert len(calls) == 2, (
            f"a faulted content re-embed must not skip the cascade "
            f"re-embed; store calls: {len(calls)}"
        )
        assert combined.last_embedding_status == "fault"

        # Content-only: the same fault, but no re-key means no cascade and
        # exactly the one content-path call.
        db_path2 = tmp_path / "skip2.db"
        content_only = PACTMemory(
            project_id="detected-b", session_id="s", db_path=db_path2
        )
        memory_id2 = content_only.save(
            {"context": "SKIP2-SEED"}, sync_to_claude=False
        )
        calls.clear()
        assert content_only.update(
            memory_id2, {"context": "SKIP2-CHANGED"}
        ) == memory_id2
        assert len(calls) == 1, (
            f"no re-key means no cascade re-embed; store calls: {len(calls)}"
        )
        assert content_only.last_embedding_status == "fault"

    def test_two_cascades_computing_the_same_link_converge(self, tmp_path):
        """OR IGNORE on the cascade's link insert, pinned by constructing
        the loser's state.

        Two cascades re-pointing this memory at the same (path, project)
        file row compute the same `memory_files` primary key. The winner
        commits; the loser's INSERT then violates (memory_id, file_id). A
        plain INSERT aborts that update AFTER its memories row committed —
        the same class track_file's OR IGNORE exists for. The fixture
        pre-inserts the winning link and re-adds the old one, so the next
        cascade sees exactly the loser's `linked` view: one row still under
        the old project while the target link already exists.
        """
        db_path = tmp_path / "or-ignore.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "OR-IGNORE"}, files=[str(linked)], sync_to_claude=False
        )
        # First cascade: the winning writer's effect.
        assert memory.update(memory_id, {"project_id": "projB"}) == memory_id

        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            old_file_id = conn.execute(
                "SELECT f.id FROM files f WHERE f.path = ? AND f.project_id = ?",
                (str(linked), "projA"),
            ).fetchone()[0]
            # The losing cascade's view: the old link is back, and the
            # winning (memory, projB-file) link already exists.
            conn.execute(
                "INSERT OR IGNORE INTO memory_files"
                " (memory_id, file_id, relationship) VALUES (?, ?, 'modified')",
                (memory_id, old_file_id),
            )
            conn.commit()

        assert memory.update(memory_id, {"project_id": "projB"}) == memory_id

        with db_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT f.project_id FROM files f JOIN memory_files mf"
                " ON mf.file_id = f.id WHERE mf.memory_id = ? ORDER BY f.project_id",
                (memory_id,),
            ).fetchall()
        assert [row[0] for row in rows] == ["projB"], (
            "the duplicate link aborted the cascade or left the old link "
            f"behind: {[row[0] for row in rows]!r}"
        )

    def test_a_poisoned_file_link_is_quarantined_not_fatal(
        self, tmp_path, caplog
    ):
        """N-7 (cascade half): `update_memory` commits the row's NEW project
        before the cascade runs, so a refusal that escaped here would leave
        the update half applied — row moved, link stranded under the old
        project — and re-issuing the update would hit the same row forever.

        The unrepairable link is skipped (left in place, never deleted) and
        reported once; every other link still moves; the row still moves.

        RED WHEN: the cascade lets track_file's ValueError escape (this call
        raises), or it "cleans up" the quarantined link (the anchor assertion
        fails), or the quarantine goes unreported.
        """
        db_path = tmp_path / "poisoned-cascade.db"
        memory = PACTMemory(
            project_id="projA", session_id="s", db_path=db_path
        )
        memory_id = memory.save(
            {"context": "POISONED-LINK"}, sync_to_claude=False
        )
        poisoned = "/tmp/x\x00y.py"
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                ("poisoned-file", poisoned, "projA"),
            )
            conn.execute(
                "INSERT INTO memory_files "
                "(memory_id, file_id, relationship) VALUES (?, ?, 'modified')",
                (memory_id, "poisoned-file"),
            )
            conn.commit()

        with caplog.at_level(logging.WARNING):
            assert memory.update(
                memory_id, {"project_id": "projB"}
            ) == memory_id

        with db_connection(db_path) as conn:
            assert conn.execute(
                "SELECT project_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()[0] == "projB", (
                "the memory row did not move; the cascade fix is beside the "
                "point"
            )
            anchored = [
                tuple(row)
                for row in conn.execute(
                    "SELECT f.id, f.path, f.project_id "
                    "FROM files f JOIN memory_files mf ON mf.file_id = f.id "
                    "WHERE mf.memory_id = ?",
                    (memory_id,),
                )
            ]
        assert anchored == [("poisoned-file", poisoned, "projA")], (
            "the quarantined link was moved or deleted instead of left in "
            f"place: {anchored!r}"
        )
        assert "left 1 file link" in caplog.text, (
            f"the quarantine was not reported: {caplog.text!r}"
        )
        assert memory.last_cascade_skipped == 1, (
            "the counter did not report the quarantined link: "
            f"{memory.last_cascade_skipped}"
        )

        # THE RETRY MUST NOT RAISE EITHER. The stranded link is still there,
        # so the same cascade runs again and must skip it again.
        with caplog.at_level(logging.WARNING):
            assert memory.update(
                memory_id, {"project_id": "projB"}
            ) == memory_id
        assert memory.last_cascade_skipped == 1, (
            "the re-issue did not re-report the stranded link: "
            f"{memory.last_cascade_skipped}"
        )

        # PER CALL, NOT CUMULATIVE: an update that runs no cascade reports 0.
        clean_id = memory.save({"context": "CLEAN"}, sync_to_claude=False)
        assert memory.update(clean_id, {"context": "CLEAN-2"}) == clean_id
        assert memory.last_cascade_skipped == 0, (
            "a clean update inherited an earlier call's skip count: "
            f"{memory.last_cascade_skipped}"
        )

    def test_a_disagreement_refusal_precedes_the_payload_guard(
        self, tmp_path, monkeypatch
    ):
        """F3: ordering pin. The disagreement guard runs first, so a session
        that disagrees refuses on the disagreement whatever the payload
        carries. Flipping the order would change the CLI envelope channel
        (SCOPE_DISAGREEMENT vs ValueError exit 2) with no other symptom."""
        db_path = tmp_path / "ordering.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "ORDERING"}, sync_to_claude=False)

        monkeypatch.setattr(
            memory_api,
            "env_record_project_dir_disagreement",
            lambda: ("/env/project", "/record/project"),
        )
        with pytest.raises(ProjectScopeDisagreementError):
            memory.update(memory_id, {"project_id": None})

    def test_a_refusal_precedes_ensure_ready(self, tmp_path, monkeypatch):
        """F2: a refused payload does no store work.

        `_ensure_ready` may install dependencies, migrate the store and run
        the embedding catch-up before the refusal, so its position is a
        contract the CHANGELOG states and nothing pinned. Both refusals are
        checked: the mapping guard's, and the identity loop's.
        """
        db_path = tmp_path / "ensure-ready.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "ENSURE-READY"}, sync_to_claude=False)

        called = []
        monkeypatch.setattr(memory_api, "_ensure_ready", lambda: called.append(True))

        with pytest.raises(ValueError, match="must be an object mapping"):
            memory.update(memory_id, ["not", "a", "mapping"])
        with pytest.raises(ValueError, match="when supplied to update"):
            memory.update(memory_id, {"project_id": None})

        assert called == [], (
            "a refused payload reached _ensure_ready, which may install, "
            "migrate and catch up before the refusal lands"
        )

    def test_a_payload_refusal_precedes_the_prefix_check(
        self, tmp_path
    ):
        """F3, the other seam: payload validation precedes id resolution.

        The control half proves a short prefix alone takes the
        PrefixTooShortError path, so the payload half — same short prefix,
        bad payload — discriminating ValueError is the guard firing FIRST,
        not the prefix check being unreachable.
        """
        db_path = tmp_path / "prefix-ordering.db"
        memory = PACTMemory(project_id="detected-b", session_id="s", db_path=db_path)
        memory.save({"context": "PREFIX-ORDERING"}, sync_to_claude=False)

        with pytest.raises(PrefixTooShortError):
            memory.update("ab", {"context": "control half"})

        with pytest.raises(ValueError) as excinfo:
            memory.update("ab", {"project_id": None})
        assert str(excinfo.value).startswith(
            "project_id, when supplied to update, must be a non-empty string "
        )


# ---------------------------------------------------------------------------
# #1688 remediation: a re-key's cross-project reads are closed at every surface
#
# Every arm below shares one shape: a memory IS stored under one project while
# a project-keyed surface still carries another, and the assertion is that the
# read the other project issues does not hand the memory back. Each has a
# matched control on the SAME fixture, because "returns []" is also what a
# missing row returns.
# ---------------------------------------------------------------------------

_REKEY_TOKEN = "REKEY-CROSS-PROJECT-TOKEN"


def _vector_partition(db_path: Path, memory_id: str):
    """The project whose vec0 partition holds this memory, or None."""
    with db_connection(db_path) as conn:
        ensure_initialized(conn)
        row = conn.execute(
            "SELECT project_id FROM vec_memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
    return row[0] if row else None


def _saved_with_vector(db_path: Path, project: str) -> str:
    """Save one memory under `project` and prove it stored a real vector.

    The precondition is asserted rather than assumed: an environment without
    an embedding backend would leave every arm below measuring fixture state.
    """
    memory_id = PACTMemory(
        project_id=project, session_id="s", db_path=db_path
    ).save({"context": _REKEY_TOKEN}, sync_to_claude=False)
    partition = _vector_partition(db_path, memory_id)
    assert partition == project, (
        f"the fixture stored no vector under {project!r} (found {partition!r}); "
        "this arm would measure the fixture, not the guard"
    )
    return memory_id


def _degrade(monkeypatch) -> None:
    """Make this process unable to open the vector table, faithfully.

    THE FLAG ALONE IS NOT THE CONDITION. `_drop_existing_vector` imports
    sqlite_vec and loads it without consulting SQLITE_EXTENSIONS_ENABLED, so a
    fixture that patches only the flag still reaches the table and deletes the
    row — three arms measured green for that reason. A process that cannot
    embed cannot load the extension either, so both terms are broken here.
    """
    import sqlite_vec

    def _cannot_load(conn):
        raise RuntimeError("simulated: sqlite-vec cannot load in this process")

    monkeypatch.setattr(sqlite_vec, "load", _cannot_load)
    for module in (memory_api, memory_search, embedding_catchup):
        monkeypatch.setattr(module, "SQLITE_EXTENSIONS_ENABLED", False)


class TestSearchByFileConfirmsTheStoredProject:
    def test_a_stale_link_does_not_serve_another_projects_memory(self, tmp_path):
        """The graph lookup scopes the FILE; the row's own scope decides."""
        db_path = tmp_path / "stale-link.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory_id = PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).save(
            {"context": _REKEY_TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            for project in ("projA", None):
                file_id = track_file(conn, str(linked), project)
                conn.execute(
                    "INSERT OR IGNORE INTO memory_files "
                    "(memory_id, file_id, relationship) VALUES (?, ?, 'modified')",
                    (memory_id, file_id),
                )
            conn.commit()

        stale = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        assert stale.search_by_file(str(linked)) == [], (
            "a stale link on projA's file row served a projB memory to projA"
        )
        # CONTROLS on the same fixture: the memory IS reachable when the
        # filter matches and when no filter is applied, so the [] above is the
        # confirmation's doing rather than a missing row.
        live = PACTMemory(project_id="projB", session_id="s", db_path=db_path)
        assert [m.id for m in live.search_by_file(str(linked))] == [memory_id]
        with store_scope(db_path):
            assert [
                m.id for m in memory_search.search_by_file(str(linked))
            ] == [memory_id], "the unscoped read lost its whole-store meaning"


class TestAVectorHitIsConfirmedAgainstTheStoredProject:
    def test_a_degraded_rekey_leaves_the_row_and_every_read_confirms_it(
        self, tmp_path, monkeypatch
    ):
        """One fixture, three claims: the choke point, the join, the repair."""
        db_path = tmp_path / "degraded.db"
        memory_id = _saved_with_vector(db_path, "projA")

        with monkeypatch.context() as degraded:
            _degrade(degraded)
            assert PACTMemory(
                project_id="projB", session_id="s", db_path=db_path
            ).update(memory_id, {"project_id": "projB"}) == memory_id

        # THE SIMULATION WAS FAITHFUL, and this is the measurement that says
        # so: a process that could still load the extension would have deleted
        # the old row on the cascade's fault path. Patching the flag alone
        # leaves it reachable, which is why that fixture proves nothing.
        assert _vector_partition(db_path, memory_id) == "projA", (
            "the degraded re-key dropped or moved the vector row, so this "
            "fixture did not exercise the no-vector-store path"
        )

        with store_scope(db_path):
            with db_connection(db_path) as conn:
                ensure_initialized(conn)
                assert memory_search.vector_search(
                    conn, _REKEY_TOKEN, "projA"
                ) == [], (
                    "a stranded vector hit was served to the project the "
                    "memory was re-keyed out of"
                )
                assert [
                    hit for hit, _distance in memory_search.vector_search(
                        conn, _REKEY_TOKEN, None
                    )
                ] == [memory_id], "the unscoped read lost its whole-store meaning"
            unembedded = embedding_catchup.get_unembedded_memories()
            assert unembedded.answerable, unembedded.reason
            assert list(unembedded) == [memory_id], (
                "the partition-aware join did not report the stranded row"
            )
            assert embedding_catchup.embed_single_memory(memory_id) is True
        assert _vector_partition(db_path, memory_id) == "projB", (
            "the repair did not move the vector into the memory's partition"
        )


class TestAFaultedCascadeKeepsTheReachableOldRow:
    def test_a_faulted_cascade_keeps_the_row_and_a_reissue_repairs_it(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "fault.db"
        memory_id = _saved_with_vector(db_path, "projA")
        real_store = PACTMemory._store_embedding

        def _fault(conn, mid, memory):
            # The real fault path rolls its own replace back, so the original
            # row is restored; this stub leaves the same end state.
            conn.rollback()
            return "fault"

        monkeypatch.setattr(PACTMemory, "_store_embedding", staticmethod(_fault))
        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id

        assert _vector_partition(db_path, memory_id) == "projA", (
            "the faulted cascade deleted the restored old-partition row; a "
            "re-issued update can no longer repair it"
        )
        # A stranded row is still visible to the partition-aware catch-up,
        # which is the OTHER repair path and must stay open.
        with store_scope(db_path):
            unembedded = embedding_catchup.get_unembedded_memories()
            assert unembedded.answerable, unembedded.reason
            assert list(unembedded) == [memory_id], (
                "the catch-up did not report a stranded cross-partition row"
            )

        # AND the re-issue the cascade's RETRY paragraph promises: the old
        # row is an observed invariant violation, so the same update repairs
        # it without the catch-up.
        monkeypatch.setattr(PACTMemory, "_store_embedding", real_store)
        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id
        assert _vector_partition(db_path, memory_id) == "projB", (
            "re-issuing the same update did not repair a stranded vector"
        )


class TestAnInterruptedCascadeIsFinishedByReissuing:
    def test_the_reissued_update_repairs_and_a_satisfied_one_stores_nothing(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "interrupted.db"
        first = tmp_path / "first.py"
        second = tmp_path / "second.py"
        first.write_text("x = 1\n", encoding="utf-8")
        second.write_text("y = 2\n", encoding="utf-8")
        memory_id = PACTMemory(
            project_id="projA", session_id="s", db_path=db_path
        ).save(
            {"context": _REKEY_TOKEN},
            files=[str(first), str(second)],
            sync_to_claude=False,
        )
        assert _vector_partition(db_path, memory_id) == "projA"

        real_track = memory_api.track_file
        seen = {"n": 0}

        def _raise_on_second(conn, path, project_id=None):
            seen["n"] += 1
            if seen["n"] == 2:
                raise RuntimeError("simulated interruption")
            return real_track(conn, path, project_id)

        monkeypatch.setattr(memory_api, "track_file", _raise_on_second)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            PACTMemory(
                project_id="projB", session_id="s", db_path=db_path
            ).update(memory_id, {"project_id": "projB"})
        monkeypatch.setattr(memory_api, "track_file", real_track)

        # The memories row moved and was committed before the cascade ran, so
        # the re-issued update sees an already-re-keyed row.
        with db_connection(db_path) as conn:
            assert get_memory(conn, memory_id)["project_id"] == "projB"

        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id

        with db_connection(db_path) as conn:
            stale = conn.execute(
                "SELECT COUNT(*) FROM files f JOIN memory_files mf "
                "ON mf.file_id = f.id WHERE mf.memory_id = ? "
                "AND f.project_id IS NOT ?",
                (memory_id, "projB"),
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM files f JOIN memory_files mf "
                "ON mf.file_id = f.id WHERE mf.memory_id = ? "
                "AND f.project_id IS ?",
                (memory_id, "projB"),
            ).fetchone()[0]
        assert (stale, fresh) == (0, 2), (
            f"the re-issued update did not finish the cascade: {stale} old, "
            f"{fresh} new"
        )
        assert _vector_partition(db_path, memory_id) == "projB"

        # THIRD UPDATE: the invariant is satisfied. Zero store calls is the
        # claim, but a zero is also what an update that never reached the
        # cascade produces, so the probes that decide it are spied too.
        probes = []
        real_files = PACTMemory._linked_file_partition_is_stale
        real_vector = PACTMemory._vector_partition_is_stale
        monkeypatch.setattr(
            PACTMemory, "_linked_file_partition_is_stale",
            staticmethod(
                lambda conn, mid, target: (
                    probes.append("files"), real_files(conn, mid, target)
                )[1]
            ),
        )
        monkeypatch.setattr(
            PACTMemory, "_vector_partition_is_stale",
            staticmethod(
                lambda conn, mid, target: (
                    probes.append("vector"), real_vector(conn, mid, target)
                )[1]
            ),
        )
        stores = []
        monkeypatch.setattr(
            PACTMemory, "_store_embedding",
            staticmethod(lambda conn, mid, memory: stores.append(mid) or None),
        )
        # THE CASCADE ITSELF IS COUNTED, not only the store it calls. A
        # cascade entered and doing nothing produces zero store calls too, so
        # the zero below means "not entered", which is the claim.
        cascades = []
        real_cascade = PACTMemory._cascade_rekey

        def _spy_cascade(self, conn, mid, target, *, vector_current):
            cascades.append((mid, target, vector_current))
            return real_cascade(
                self, conn, mid, target, vector_current=vector_current
            )

        monkeypatch.setattr(PACTMemory, "_cascade_rekey", _spy_cascade)

        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id
        assert cascades == [], "a satisfied invariant still entered the cascade"
        assert stores == [], "a satisfied invariant still re-stored the vector"
        assert probes == ["files", "vector"], (
            "the third update never evaluated the invariant, so its zero "
            f"store calls prove nothing; probes={probes!r}"
        )


class TestConstructorValidatesSuppliedIdentifiers:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"project_id": "bad\x00id"},
            {"project_id": "bad\ud800id"},
            {"session_id": "bad\x7fid"},
            {"session_id": 123},
        ],
    )
    def test_a_supplied_unusable_identifier_is_refused(self, tmp_path, kwargs):
        db_path = tmp_path / "constructor.db"
        with pytest.raises(ValueError, match="when supplied"):
            PACTMemory(db_path=db_path, **kwargs)
        assert not db_path.exists(), (
            "the constructor refusal performed store work"
        )

    def test_a_falsy_supplied_identifier_still_auto_detects(
        self, tmp_path, monkeypatch
    ):
        """The guard sits INSIDE the truthy branch, and this is its boundary."""
        monkeypatch.setattr(
            PACTMemory, "_detect_project_identity_with_legacy",
            staticmethod(lambda: memory_api._DetectedIdentity("detected-proj", "cwd", None, None, None)),
        )
        monkeypatch.setattr(
            PACTMemory, "_detect_session_id",
            staticmethod(lambda: "detected-session"),
        )
        memory = PACTMemory(project_id="", session_id="", db_path=tmp_path / "x.db")
        assert memory.project_id == "detected-proj"
        assert memory._project_id_source == "cwd"
        assert memory.session_id == "detected-session"

    def test_a_detected_unusable_session_id_does_not_gate_construction(
        self, tmp_path, monkeypatch
    ):
        """F10: the constructor ADOPTS the detected value; reads must work.

        The refusal lives at the ingress that would BIND it — `save` for the
        write, `list(session_only=True)` for the one read that filters by it.
        Refusing here would turn a listing or diagnostics command into a
        crash over a value it never uses.
        """
        monkeypatch.setattr(
            PACTMemory, "_detect_session_id", staticmethod(lambda: "bad\nid")
        )
        memory = PACTMemory(project_id="ok-proj", db_path=tmp_path / "x.db")
        assert memory.session_id == "bad\nid", (
            "the detected value was silently dropped or altered; it is "
            "adopted AS FOUND and the binding paths guard it themselves"
        )
        assert not (tmp_path / "x.db").exists(), (
            "read-only construction performed store work"
        )


class TestTheWriteIngressGuardsTheAdoptedSessionId:
    """F10: an unusable DETECTED session id refuses where it would BIND.

    The constructor adopts the value so read-only commands can construct at
    all; `save` refuses to write it into a row and `list(session_only=True)`
    refuses to bind it, returning the empty (unusable-value) answer. An
    update writes no adopted session id, so its payload guard — pinned by
    `TestUpdatePayloadProjectIdGuard::test_rejects_a_dekeying_or_mis_keying_payload`
    — is the whole contract there; a supplied constructor id is pinned by
    `TestConstructorValidatesSuppliedIdentifiers::test_a_supplied_unusable_identifier_is_refused`.
    """

    @staticmethod
    def _memory(tmp_path, monkeypatch):
        monkeypatch.setattr(
            PACTMemory, "_detect_session_id", staticmethod(lambda: "bad\nid")
        )
        return PACTMemory(project_id="ok-proj", db_path=tmp_path / "f10.db")

    def test_save_refuses_before_any_store_work(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch)
        with pytest.raises(
            ValueError, match="detected from the session context file"
        ):
            memory.save({"context": "F10"}, sync_to_claude=False)
        assert not (tmp_path / "f10.db").exists(), (
            "the adopted-session refusal performed store work"
        )

    def test_list_session_only_returns_empty_without_binding(
        self, tmp_path, monkeypatch
    ):
        memory = self._memory(tmp_path, monkeypatch)
        assert memory.list(session_only=True) == [], (
            "a read bound an unusable session id instead of returning the "
            "empty (unusable-value) answer"
        )
        assert not (tmp_path / "f10.db").exists(), (
            "the unusable-value read performed store work"
        )


class TestGetStatusFoldsAnUnusableDetectedSessionId:
    """The status envelope FOLDS an unusable id and reports a usable one RAW.

    Two rules, one envelope: `_unusable_identifier` rejects a control
    character or lone surrogate, which no row can carry, so the field folds
    to null; a bidi/format/separator character is ACCEPTED by design, so the
    surviving value is the stored value byte for byte — the caller may feed
    it back as an identity, and the round-trip arms below do exactly that. A
    clean value passes through byte-identical.
    """

    def _memory(self, tmp_path, monkeypatch, session_id, project=None):
        monkeypatch.setattr(
            PACTMemory, "_detect_session_id", staticmethod(lambda: session_id)
        )
        if project is not None:
            monkeypatch.setattr(
                PACTMemory,
                "_detect_project_identity_with_legacy",
                staticmethod(lambda: memory_api._DetectedIdentity(project, "CLAUDE_PROJECT_DIR", None, None, None)),
            )
        return PACTMemory(
            project_id=None if project is not None else "P",
            session_id=None,
            db_path=tmp_path / "status.db",
        )

    def test_an_unusable_detected_session_id_is_reported_as_null(
        self, tmp_path, monkeypatch
    ):
        memory = self._memory(tmp_path, monkeypatch, "bad\udcff")
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["session_id"] is None, (
            "an unusable detected session id reached the envelope: "
            f"{status['session_id']!r}"
        )

    def test_the_zeroed_arm_also_reports_null(self, tmp_path, monkeypatch):
        memory = self._memory(
            tmp_path, monkeypatch, "bad\udcff", project="proj\udcff"
        )
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["project_id"] is None
        assert status["session_id"] is None, (
            "the zeroed arm echoed an unusable session id: "
            f"{status['session_id']!r}"
        )

    def test_a_clean_detected_session_id_is_reported(
        self, tmp_path, monkeypatch
    ):
        """Byte-identical control: the escape changes nothing safe."""
        memory = self._memory(tmp_path, monkeypatch, "clean-session")
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["session_id"] == "clean-session"

    @pytest.mark.parametrize("char", ["\u202e", "\u2028"])
    def test_a_display_unsafe_detected_session_id_is_reported_raw(
        self, tmp_path, monkeypatch, char
    ):
        session_id = f"bad{char}id"
        memory = self._memory(tmp_path, monkeypatch, session_id)
        memory_id = memory.save(
            {"context": "RAW-SESSION"}, sync_to_claude=False
        )
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["session_id"] == session_id, (
            "a display-unsafe detected session id did not reach the payload "
            f"raw: {status['session_id']!r}"
        )
        # ROUND TRIP: the reported session id filters to the saved row.
        again = PACTMemory(
            project_id=memory.project_id,
            session_id=status["session_id"],
            db_path=tmp_path / "status.db",
        )
        with patch("scripts.memory_api._ensure_ready"):
            assert [m.id for m in again.list(session_only=True)] == [memory_id], (
                "the reported session id did not filter to the saved row"
            )

    @pytest.mark.parametrize("char", ["\u202e", "\u2028"])
    def test_a_display_unsafe_detected_project_id_is_reported_raw(
        self, tmp_path, monkeypatch, char
    ):
        project = f"proj{char}X"
        memory = self._memory(
            tmp_path, monkeypatch, "clean-session", project=project
        )
        memory.save({"context": "RAW-PROJECT"}, sync_to_claude=False)
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["project_id"] == project, (
            "a display-unsafe detected project id did not reach the payload "
            f"raw: {status['project_id']!r}"
        )
        # ROUND TRIP: the reported project id scopes to the saved row.
        scoped = PACTMemory(
            project_id=status["project_id"],
            session_id="s",
            db_path=tmp_path / "status.db",
        )
        with patch("scripts.memory_api._ensure_ready"):
            assert scoped.get_status()["memory_count"] == 1, (
                "the reported project id did not scope to the saved row"
            )

    @pytest.mark.parametrize("char", ["\u202e", "\u2028"])
    def test_the_zeroed_arm_reports_a_display_unsafe_session_id_raw(
        self, tmp_path, monkeypatch, char
    ):
        """The zeroed arm takes the same computed value, raw included."""
        session_id = f"bad{char}id"
        memory = self._memory(
            tmp_path, monkeypatch, session_id, project="proj\udcff"
        )
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["project_id"] is None
        assert status["session_id"] == session_id, (
            "the zeroed arm altered the session id: "
            f"{status['session_id']!r}"
        )


class TestTheStatusCountersBindTheRawProjectScope:
    """The envelope carries the raw value; the QUERIES must bind the same.

    `get_status` promises its counters use the raw scope. If they ever bound
    a transformed form, a row stored under a bidi-containing project id would
    stop counting while the payload still reported the id — a silent
    undercount no existing status arm reddens on. The round-trip below is the
    stronger claim: the REPORTED id is the scope that counts the rows.
    """

    def test_a_bidi_project_id_counts_its_rows_and_reports_it_raw(
        self, tmp_path, monkeypatch
    ):
        from scripts.memory_api import PACTMemory

        project = "proj\u202eX"
        db_path, conn = _memory_store(tmp_path, "bidi-count.db")
        _insert_memories(conn, [("m-bidi", project)])
        conn.close()

        monkeypatch.setattr(
            PACTMemory,
            "_detect_project_identity_with_legacy",
            staticmethod(lambda: memory_api._DetectedIdentity(project, "cwd", None, None, None)),
        )
        memory = PACTMemory(
            project_id=None, session_id="s", db_path=db_path
        )
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["project_id"] == project, status["project_id"]
        assert status["memory_count"] >= 1, (
            "the counter did not bind the raw project id and matched no "
            f"rows: {status['memory_count']!r}"
        )
        # ROUND TRIP: the reported id is the scope that counts these rows.
        again = PACTMemory(
            project_id=status["project_id"], session_id="s", db_path=db_path
        )
        with patch("scripts.memory_api._ensure_ready"):
            assert again.get_status()["memory_count"] == status["memory_count"]


class TestTheStatusEnvelopeCarriesTheDbPathRaw:
    """The db path is a STRUCTURED payload value: raw, and it opens.

    A caller that takes `status["db_path"]` and opens it must reach the same
    store; an escaped payload made that path name no file.
    """

    def _memory(self, tmp_path, monkeypatch, detected=None):
        from scripts.memory_api import PACTMemory

        if detected is not None:
            monkeypatch.setattr(
                PACTMemory,
                "_detect_project_identity_with_legacy",
                staticmethod(lambda: memory_api._DetectedIdentity(detected, "cwd", None, None, None)),
            )
            return PACTMemory(
                project_id=None,
                session_id="s",
                db_path=tmp_path / "store\u202eX.db",
            )
        return PACTMemory(
            project_id="P",
            session_id="s",
            db_path=tmp_path / "store\u202eX.db",
        )

    def test_the_normal_arm_reports_the_path_raw_and_it_opens(
        self, tmp_path, monkeypatch
    ):
        memory = self._memory(tmp_path, monkeypatch)
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        expected = str(tmp_path / "store\u202eX.db")
        assert status["db_path"] == expected, status["db_path"]
        assert memory.db_path == tmp_path / "store\u202eX.db", (
            "the programmatic accessor must return the RAW path"
        )
        # ROUND TRIP: the reported path opens the same store. The read must
        # NOT have initialised it (the scoped zero-CREATE rule), so the file
        # opens with NO tables — that emptiness is what proves both halves:
        # the raw path reaches this store, and the read left it uninitialised.
        from scripts.database import get_connection

        conn = get_connection(Path(status["db_path"]))
        try:
            assert conn.execute(
                "SELECT name FROM sqlite_master"
            ).fetchall() == [], (
                "a read initialised the store it reported; on an absent "
                "caller path the zeroed envelope must leave no schema"
            )
        finally:
            conn.close()

    def test_the_zeroed_arm_reports_the_path_raw(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch, detected="proj\udcff")
        with patch("scripts.memory_api._ensure_ready"):
            status = memory.get_status()
        assert status["project_id"] is None
        assert status["db_path"] == str(tmp_path / "store\u202eX.db"), (
            status["db_path"]
        )


class TestTheSaveDisclosureStaysRawOnTheInstance:
    """`last_project_scope` is the programmatic value; the CLI renders it."""

    def test_project_scope_identity_fields_stay_raw(self, tmp_path, monkeypatch):
        from scripts.memory_api import PACTMemory

        bidi = "proj\u202eX"
        monkeypatch.setattr(
            "scripts.memory_api.main_repo_root",
            lambda *args, **kwargs: Path("/repo") / bidi,
        )
        # The patched root is NOT a repository here, so the resolver takes its
        # designed basename branch and the raw spelling flows through. A
        # DECLINE path yields None instead (S2-bk: no folder pseudo-id).
        monkeypatch.setattr(
            "scripts.memory_api.main_repo_common_dir",
            lambda *args, **kwargs: None,
        )
        memory = PACTMemory(
            project_id="P", session_id="s", db_path=tmp_path / "raw.db"
        )
        memory.save(
            {"context": "RAW-SCOPE", "project_id": bidi},
            sync_to_claude=False,
        )
        scope = memory.last_project_scope
        assert scope["project_id"] == bidi
        assert scope["cwd_repo"] == bidi


class TestANonMappingUpdatePayload:
    @pytest.mark.parametrize("payload", [["x"], "x", 5, None, b"x"])
    def test_is_refused_as_invalid_input(self, tmp_path, payload):
        db_path = tmp_path / "non-mapping.db"
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "NON-MAPPING"}, sync_to_claude=False
        )
        with pytest.raises(ValueError, match="must be an object mapping"):
            memory.update(memory_id, payload)
        assert memory.get(memory_id) is not None, (
            "the refusal destroyed or removed the row"
        )


class TestTheSuccessPathDoesNotRestoreTheVector:
    def test_a_content_change_and_a_rekey_store_the_vector_once(
        self, tmp_path, monkeypatch
    ):
        """F4: the skip branch `vector_current` exists for is a real no-op.

        The fixture has NO vector row: a missing row is deliberately not
        stale, so the content path's successful store is what makes the
        cascade skip. With a real row left under the old project the
        partition would legitimately be stale and the cascade must re-store.
        """
        db_path = tmp_path / "once.db"
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save({"context": "ONCE-SEED"}, sync_to_claude=False)
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            conn.execute(
                "DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,)
            )
            conn.commit()

        stores = []
        monkeypatch.setattr(
            PACTMemory, "_store_embedding",
            staticmethod(lambda conn, mid, data: stores.append(mid) or None),
        )
        assert memory.update(
            memory_id, {"context": "ONCE-CHANGED", "project_id": "projB"}
        ) == memory_id
        assert len(stores) == 1, (
            "the cascade re-stored a vector the content path already stored "
            f"under the new key; stores={stores!r}"
        )


# ---------------------------------------------------------------------------
# One arm per operand of the trigger's OR
# ---------------------------------------------------------------------------

class TestTheCascadeTriggersOnEachSoleDisjunct:
    """The trigger is `project_changed or files_stale or vector_stale`.

    An arm in which TWO surfaces are stale proves nothing about either probe
    alone: blinding one leaves the other to fire, so the suite stays green
    (the cold lane measured both mutations). Each arm below therefore records
    the probe PAIR on the triggering call and asserts the opposite operand is
    false, so neither arm can pass through a neighbour.
    """

    @staticmethod
    def _spy(monkeypatch):
        """Record the probe pair and every cascade entry on the next call."""
        probes, entries = [], []
        real_files = PACTMemory._linked_file_partition_is_stale
        real_vector = PACTMemory._vector_partition_is_stale
        real_cascade = PACTMemory._cascade_rekey

        def _files(conn, mid, target):
            value = real_files(conn, mid, target)
            probes.append(("files", value))
            return value

        def _vector(conn, mid, target):
            value = real_vector(conn, mid, target)
            probes.append(("vector", value))
            return value

        def _cascade(self, conn, mid, target, *, vector_current):
            entries.append(mid)
            return real_cascade(
                self, conn, mid, target, vector_current=vector_current
            )

        monkeypatch.setattr(
            PACTMemory, "_linked_file_partition_is_stale", staticmethod(_files)
        )
        monkeypatch.setattr(
            PACTMemory, "_vector_partition_is_stale", staticmethod(_vector)
        )
        monkeypatch.setattr(PACTMemory, "_cascade_rekey", _cascade)
        return probes, entries

    @staticmethod
    def _link_counts(db_path, memory_id, project):
        with db_connection(db_path) as conn:
            stale = conn.execute(
                "SELECT COUNT(*) FROM files f JOIN memory_files mf "
                "ON mf.file_id = f.id WHERE mf.memory_id = ? "
                "AND f.project_id IS NOT ?",
                (memory_id, project),
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM files f JOIN memory_files mf "
                "ON mf.file_id = f.id WHERE mf.memory_id = ? "
                "AND f.project_id IS ?",
                (memory_id, project),
            ).fetchone()[0]
        return stale, fresh

    def test_only_the_file_partition_stale_triggers_the_repair(
        self, tmp_path, monkeypatch
    ):
        """No vector row, links on the old project: ONLY the file probe can fire."""
        db_path = tmp_path / "file-only.db"
        first = tmp_path / "first.py"
        second = tmp_path / "second.py"
        first.write_text("x = 1\n", encoding="utf-8")
        second.write_text("y = 2\n", encoding="utf-8")
        memory_id = PACTMemory(
            project_id="projA", session_id="s", db_path=db_path
        ).save(
            {"context": _REKEY_TOKEN},
            files=[str(first), str(second)],
            sync_to_claude=False,
        )
        assert _vector_partition(db_path, memory_id) == "projA", (
            "fixture: the save stored no vector, so there is nothing to remove "
            "and the vector probe's absence would be accidental"
        )
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            conn.execute(
                "DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,)
            )
            conn.commit()
        assert _vector_partition(db_path, memory_id) is None

        real_track = memory_api.track_file
        seen = {"n": 0}

        def _raise_on_second(conn, path, project_id=None):
            seen["n"] += 1
            if seen["n"] == 2:
                raise RuntimeError("simulated interruption")
            return real_track(conn, path, project_id)

        monkeypatch.setattr(memory_api, "track_file", _raise_on_second)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            PACTMemory(
                project_id="projB", session_id="s", db_path=db_path
            ).update(memory_id, {"project_id": "projB"})
        monkeypatch.setattr(memory_api, "track_file", real_track)

        # THE RETRY'S STATE, proven before the spies are installed: the row is
        # already re-keyed (project_changed False) and the interrupted attempt
        # never reached the vector tail, so there is still no vector row
        # (vector_stale False).
        with db_connection(db_path) as conn:
            assert get_memory(conn, memory_id)["project_id"] == "projB"
        assert _vector_partition(db_path, memory_id) is None
        assert self._link_counts(db_path, memory_id, "projB") == (2, 0)

        probes, entries = self._spy(monkeypatch)
        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id

        assert probes == [("files", True), ("vector", False)], (
            f"the retry was not file-ONLY: probes={probes!r}"
        )
        assert entries == [memory_id], (
            "the file probe fired and nothing else could have, but the cascade "
            f"was not entered; entries={entries!r}"
        )
        assert self._link_counts(db_path, memory_id, "projB") == (0, 2), (
            "the retry did not repair the links"
        )

    def test_only_the_vector_partition_stale_triggers_the_repair(
        self, tmp_path, monkeypatch
    ):
        """Links consistent, vector stranded: ONLY the vector probe can fire."""
        db_path = tmp_path / "vector-only.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": _REKEY_TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        assert memory.update(memory_id, {"project_id": "projB"}) == memory_id
        assert self._link_counts(db_path, memory_id, "projB") == (0, 1)

        # STRAND ONLY THE VECTOR. vec0 refuses an UPDATE that names the
        # partition key, so the move is a delete plus an insert of the same
        # blob.
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            row = conn.execute(
                "SELECT embedding FROM vec_memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            assert row is not None, (
                "fixture: the successful re-key stored no vector, so there is "
                "nothing to strand"
            )
            conn.execute(
                "DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,)
            )
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding) "
                "VALUES (?, ?, ?)",
                (memory_id, "projA", row[0]),
            )
            conn.commit()
        assert _vector_partition(db_path, memory_id) == "projA"

        probes, entries = self._spy(monkeypatch)
        assert PACTMemory(
            project_id="projB", session_id="s", db_path=db_path
        ).update(memory_id, {"project_id": "projB"}) == memory_id

        assert probes == [("files", False), ("vector", True)], (
            f"the retry was not vector-ONLY: probes={probes!r}"
        )
        assert entries == [memory_id], (
            "the vector probe fired and nothing else could have, but the "
            f"cascade was not entered; entries={entries!r}"
        )
        assert _vector_partition(db_path, memory_id) == "projB", (
            "the retry did not re-store the vector under the memory's project"
        )


# ---------------------------------------------------------------------------
# Remediation arms: one per confirmation or clause the review round found
# unarmed. Each names the exact edit that must redden it.
# ---------------------------------------------------------------------------


class TestTheFilePartitionProbeIsNullSafe:
    def test_a_null_keyed_link_is_repaired_by_a_same_project_update(
        self, tmp_path
    ):
        """`IS NOT` counts a NULL-keyed file row as stale for a projected
        target. `!=` evaluates NULL and the retry silently skips the repair."""
        db_path = tmp_path / "null-link.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projB", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": _REKEY_TOKEN}, files=[str(linked)], sync_to_claude=False
        )
        assert [m.id for m in memory.search_by_file(str(linked))] == [memory_id]

        # Simulate an unprojected (legacy) file row sharing the link.
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            file_id = get_file_id(conn, str(linked), "projB")
            assert file_id, "the live save did not link the file row"
            conn.execute(
                "UPDATE files SET project_id = NULL WHERE id = ?", (file_id,)
            )
            conn.commit()
        assert memory.search_by_file(str(linked)) == [], (
            "the NULL-keyed fixture is still addressable; the repair below "
            "would not be measured"
        )
        # Only the FILE probe can fire below: the row already carries projB
        # (project_changed False) and the vector, if any, is under projB.
        assert _vector_partition(db_path, memory_id) in (None, "projB")

        assert memory.update(memory_id, {"project_id": "projB"}) == memory_id
        assert [m.id for m in memory.search_by_file(str(linked))] == [memory_id], (
            "the re-issued update did not repair the NULL-keyed link"
        )


class TestAStaleObservationForcesTheCascadeRestore:
    def test_a_successful_content_store_does_not_skip_a_stale_partition(
        self, tmp_path, monkeypatch
    ):
        """`vector_current and not vector_stale`: a content store that
        succeeded under the new key must not skip a re-store when the
        partition is OBSERVED wrong. Only a concurrent writer produces that
        state, so the observation is stubbed; the update, the cascade and the
        store are the real path and the assertion is on the real call count."""
        db_path = tmp_path / "stale-observation.db"
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "OBSERVED-SEED"}, sync_to_claude=False
        )

        monkeypatch.setattr(
            PACTMemory,
            "_vector_partition_is_stale",
            staticmethod(lambda conn, mid, target: True),
        )
        calls = []
        real_store = PACTMemory._store_embedding

        def _counting(self_, conn, mid, data):
            calls.append(mid)
            return real_store(self_, conn, mid, data)

        monkeypatch.setattr(PACTMemory, "_store_embedding", _counting)

        assert memory.update(
            memory_id, {"context": "OBSERVED-CHANGED", "project_id": "projB"}
        ) == memory_id
        assert memory.last_embedding_status is None, (
            "the real store did not succeed, so vector_current was False and "
            "this arm would pass without the clause it names"
        )
        assert len(calls) == 2, (
            "the content store succeeded (vector_current True) and the "
            "partition was observed stale; the cascade must re-store. "
            f"store calls: {len(calls)}"
        )


class TestTheVectorProbeOnAnUnobservableTable:
    def test_an_unloadable_extension_is_not_stale_and_fires_no_cascade(
        self, tmp_path, monkeypatch
    ):
        """An unobservable table is not evidence of a wrong partition: the
        probe returns False rather than raising, and the cascade does not
        re-store on every update of a process that cannot embed."""
        db_path = tmp_path / "unobservable.db"
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "UNOBSERVABLE"}, sync_to_claude=False
        )

        import sqlite_vec

        def _cannot_load(conn):
            raise RuntimeError("simulated: sqlite-vec cannot load in this process")

        monkeypatch.setattr(sqlite_vec, "load", _cannot_load)

        # THE PROBE ITSELF: False, not a raise.
        with db_connection(db_path) as conn:
            assert PACTMemory._vector_partition_is_stale(
                conn, memory_id, "projB"
            ) is False

        # THE CONSEQUENCE: a same-project update with no content change and no
        # file links has no other stale disjunct, so the probe must not enter
        # the cascade, let alone attempt a store.
        cascades = []
        real_cascade = PACTMemory._cascade_rekey

        def _spy(self_, conn, mid, target, *, vector_current):
            cascades.append(mid)
            return real_cascade(
                self_, conn, mid, target, vector_current=vector_current
            )

        monkeypatch.setattr(PACTMemory, "_cascade_rekey", _spy)
        stores = []
        real_store = PACTMemory._store_embedding

        def _counting(self_, conn, mid, data):
            stores.append(mid)
            return real_store(self_, conn, mid, data)

        monkeypatch.setattr(PACTMemory, "_store_embedding", _counting)

        assert memory.update(memory_id, {"project_id": "projA"}) == memory_id
        assert cascades == [], "an unobservable vector probe entered the cascade"
        assert stores == [], "an unobservable vector probe attempted a re-store"


class TestASessionOnlyUpdateMovesNothingElse:
    def test_a_session_id_value_moves_no_links_or_vectors(self, tmp_path):
        """SKILL.md: a session_id value changes the row's session only. The
        linked file rows and the vector row must be exactly as they were."""
        db_path = tmp_path / "session-only.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "SESSION-ONLY"}, files=[str(linked)], sync_to_claude=False
        )
        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            before_links = conn.execute(
                "SELECT f.path, f.project_id FROM files f "
                "JOIN memory_files mf ON mf.file_id = f.id "
                "WHERE mf.memory_id = ? ORDER BY f.path",
                (memory_id,),
            ).fetchall()
        assert [tuple(row) for row in before_links] == [
            (str(linked), "projA")
        ], before_links
        before_vec = _vector_partition(db_path, memory_id)
        assert before_vec in (None, "projA"), before_vec

        assert memory.update(memory_id, {"session_id": "s2"}) == memory_id

        with db_connection(db_path) as conn:
            ensure_initialized(conn)
            session = conn.execute(
                "SELECT session_id FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()[0]
            after_links = conn.execute(
                "SELECT f.path, f.project_id FROM files f "
                "JOIN memory_files mf ON mf.file_id = f.id "
                "WHERE mf.memory_id = ? ORDER BY f.path",
                (memory_id,),
            ).fetchall()
        assert session == "s2"
        assert [tuple(row) for row in after_links] == [
            tuple(row) for row in before_links
        ], (before_links, after_links)
        assert _vector_partition(db_path, memory_id) == before_vec


# ---------------------------------------------------------------------------
# Fix-round arms: id normalisation, the cascade's observed target, and the
# file list a scoped read attaches after an interrupted re-key.
# ---------------------------------------------------------------------------

class TestACallerSuppliedIdIsStoredLowercase:
    """One representation for an id, at the write ingress.

    `_resolve_id_or_full` lowercases every lookup while `create_memory` used
    to store a caller-supplied id verbatim, so an uppercase id was written
    and then unreachable by get/update/delete. Generated ids are already
    lowercase hex; only supplied strings needed the normalisation.
    """

    def test_an_uppercase_id_is_normalised_and_resolvable_either_case(
        self, tmp_path
    ):
        db_path = tmp_path / "upper-id.db"
        memory = PACTMemory(project_id="P", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "UPPER", "id": "ABC9999"}, sync_to_claude=False
        )
        assert memory_id == "abc9999", memory_id
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE id = ?", ("abc9999",)
            ).fetchone()
        assert row is not None, "the supplied id was not stored lowercase"
        assert memory.get("abc9999") is not None
        assert memory.get("ABC9999") is not None, (
            "an uppercase lookup did not reach the lowercased stored id"
        )


class TestTheCascadeFollowsTheRowsObservedProject:
    """The re-key cascade targets the row AFTER update_memory's commit.

    A concurrent writer can re-key the row between that commit and the
    cascade. Driving links and vector at the REQUESTED target would key them
    to a project the row no longer carries; the re-read makes the committed
    row the truth. The fixture forces the interleaving deterministically by
    wrapping update_memory: the real write runs, then the wrapper writes the
    competing project and commits, exactly as the racing session would.
    """

    def test_a_concurrent_rekey_is_the_target_the_links_follow(
        self, tmp_path, monkeypatch
    ):
        db_path = tmp_path / "observed-target.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "RACE-TARGET"}, files=[str(linked)], sync_to_claude=False
        )

        real_update_memory = memory_api.update_memory

        def racing_update(conn, memory_id_inner, updates, replace=False):
            ok = real_update_memory(
                conn, memory_id_inner, updates, replace=replace
            )
            # The competing session wins the key before the cascade runs.
            conn.execute(
                "UPDATE memories SET project_id = 'projC' WHERE id = ?",
                (memory_id_inner,),
            )
            conn.commit()
            return ok

        monkeypatch.setattr(memory_api, "update_memory", racing_update)
        assert memory.update(memory_id, {"project_id": "projB"}) == memory_id

        with db_connection(db_path) as conn:
            row = get_memory(conn, memory_id)
            links = conn.execute(
                "SELECT f.project_id FROM files f JOIN memory_files mf"
                " ON mf.file_id = f.id WHERE mf.memory_id = ?"
                " ORDER BY f.project_id",
                (memory_id,),
            ).fetchall()
        assert row["project_id"] == "projC", row["project_id"]
        assert [link[0] for link in links] == ["projC"], (
            "the cascade keyed the link to the requested target instead of "
            f"the row's observed project: {[link[0] for link in links]!r}"
        )


class TestAScopedReadExcludesAStaleLinkAfterAnInterruptedRekey:
    """The memory -> files read confirms the MEMORY's stored project.

    An interrupted re-key can leave the memories row on the new project
    while the map/file link still points at the old project's file row. The
    link carries no project, so the file's row decides; without the
    confirmation the old path is attached to a memory the new project owns.
    """

    def test_get_and_list_attach_no_old_project_path(self, tmp_path):
        db_path = tmp_path / "interrupted-read.db"
        linked = tmp_path / "linked.py"
        linked.write_text("x = 1\n", encoding="utf-8")
        memory = PACTMemory(project_id="projA", session_id="s", db_path=db_path)
        memory_id = memory.save(
            {"context": "INTERRUPTED-READ"}, files=[str(linked)],
            sync_to_claude=False,
        )
        # The interrupted state: row moved, link not moved.
        with db_connection(db_path) as conn:
            conn.execute(
                "UPDATE memories SET project_id = 'projB' WHERE id = ?",
                (memory_id,),
            )
            conn.commit()

        new_view = PACTMemory(project_id="projB", session_id="s", db_path=db_path)
        got = new_view.get(memory_id)
        assert got is not None
        assert got.files == [], (
            "get() attached the old project's path to a projB memory: "
            f"{got.files!r}"
        )
        listed = new_view.list()
        assert [m.files for m in listed if m.id == memory_id] == [[]], [
            m.files for m in listed
        ]

class TestTheFixtureStoreIsTheProductionSchema:
    """The `_memory_store` docstring's production claim, measured."""

    def test_an_insert_without_timestamps_is_refused(self, tmp_path):
        """The test schema defaults `created_at`; production declares it NOT
        NULL with no default. A fixture that claims production schema while
        building the test one hides exactly the write that fails only in
        production (cold N1). This arm fails if `_memory_store` ever reverts
        to `create_test_schema`.
        """
        _db_path, conn = _memory_store(tmp_path, "schema-honesty.db")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memories (id, context) VALUES ('no-stamp', 'c')"
            )


class TestACallerSuppliedIdIsHeldToTheIdentifierRule:
    """`create_memory` honours a caller-supplied id, so it validates it.

    Without the guard a control character or lone surrogate reaches the
    `memories.id` TEXT bind; the lone-surrogate case dies there as an
    unhandled UnicodeEncodeError, bypassing the ValueError remedy channel.
    A falsy id keeps its documented `or generate_id()` meaning.
    """

    @pytest.mark.parametrize(
        "bad", ["bad\x00id", "bad\x7fid", "bad\x85id", "bad\ud800id", 123]
    )
    def test_create_memory_refuses_an_unusable_id(self, tmp_path, bad):
        from scripts.database import create_memory

        _db_path, conn = _memory_store(tmp_path, "supplied-id.db")
        with pytest.raises(ValueError, match="id, when supplied"):
            create_memory(conn, {"id": bad, "context": "x",
                                 "project_id": "P"})
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 0, "a refused id still wrote a row"

    def test_an_empty_id_still_generates_one(self, tmp_path):
        from scripts.database import create_memory

        _db_path, conn = _memory_store(tmp_path, "empty-id.db")
        memory_id = create_memory(
            conn, {"id": "", "context": "x", "project_id": "P"}
        )
        assert len(memory_id) == 32, memory_id
        assert memory_id == memory_id.lower()
