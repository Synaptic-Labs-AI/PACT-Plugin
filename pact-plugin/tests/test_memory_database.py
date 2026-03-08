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
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from helpers import create_test_schema

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

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
        from scripts.database import create_memory, get_memory
        mem_id = create_memory(db_conn, {
            "context": "Test",
            "active_tasks": [{"task": "T1"}],
            "lessons_learned": ["L1"],
            "decisions": [{"decision": "D1"}],
        })
        result = get_memory(db_conn, mem_id)
        assert result is not None
        assert result["active_tasks"] == [{"task": "T1"}]
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
# ALLOWED_COLUMNS whitelist (Item 12)
# ---------------------------------------------------------------------------

class TestAllowedColumnsWhitelist:
    """Verify update_memory filters unknown column keys."""

    def test_unknown_columns_are_filtered(self, db_conn):
        """Unknown dict keys should be silently dropped, not injected into SQL."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Original"})
        # Attempt to update with a mix of valid and invalid keys
        result = update_memory(db_conn, mem_id, {
            "context": "Updated",
            "evil_column": "DROP TABLE memories",
            "'; DROP TABLE memories; --": "injection attempt",
        })
        assert result is True
        mem = get_memory(db_conn, mem_id)
        assert mem["context"] == "Updated"

    def test_only_whitelisted_columns_applied(self, db_conn):
        """Only columns in ALLOWED_COLUMNS should be written."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        update_memory(db_conn, mem_id, {
            "goal": "New goal",
            "nonexistent_field": "should be ignored",
        })
        mem = get_memory(db_conn, mem_id)
        assert mem["goal"] == "New goal"
        # Verify the table still has correct schema (no extra columns)
        cursor = db_conn.execute("PRAGMA table_info(memories)")
        col_names = {row[1] for row in cursor.fetchall()}
        assert "nonexistent_field" not in col_names

    def test_id_and_created_at_excluded(self, db_conn):
        """id and created_at should not be updatable even though they exist."""
        from scripts.database import create_memory, update_memory, get_memory
        mem_id = create_memory(db_conn, {"context": "Test"})
        original = get_memory(db_conn, mem_id)
        update_memory(db_conn, mem_id, {
            "id": "hijacked-id",
            "created_at": "1970-01-01T00:00:00",
        })
        after = get_memory(db_conn, mem_id)
        assert after["id"] == mem_id
        assert after["created_at"] == original["created_at"]


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
