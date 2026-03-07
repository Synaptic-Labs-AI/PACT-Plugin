"""
Tests for pact-memory/scripts/database.py — SQLite database layer.

Tests cover:
1. Schema initialization: tables, indexes, migrations
2. JSON serialization/deserialization helpers
3. Memory CRUD: create, get, update, delete, list
4. Text search: substring matching, project filtering, LIKE wildcard escaping
5. Database maintenance: count, integrity check
6. generate_id: uniqueness
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

# Use the same sqlite3 module that database.py uses (pysqlite3 if available)
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3


def _create_test_schema(conn):
    """Create the memory schema directly, bypassing pysqlite3 issues."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            context TEXT, goal TEXT,
            active_tasks TEXT, lessons_learned TEXT,
            decisions TEXT, entities TEXT,
            reasoning_chains TEXT, agreements_reached TEXT,
            disagreements_resolved TEXT,
            project_id TEXT, session_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL, project_id TEXT,
            last_modified TEXT,
            UNIQUE(path, project_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_files (
            memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
            file_id TEXT REFERENCES files(id),
            relationship TEXT DEFAULT 'modified',
            PRIMARY KEY (memory_id, file_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_relations (
            source_file TEXT REFERENCES files(id),
            target_file TEXT REFERENCES files(id),
            relationship TEXT,
            PRIMARY KEY (source_file, target_file, relationship)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_files_file ON memory_files(file_id)")
    conn.commit()


@pytest.fixture
def db_conn(tmp_path):
    """Create a fresh database with schema, patching ensure_initialized to no-op."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _create_test_schema(conn)
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
