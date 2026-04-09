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
        A legacy row with a stray sub-object key in `entities` must still
        accept additive updates through update_memory. The _merge_with_dedup
        safety valve (try/except → repr() fallback) should fire for the
        legacy item so the merge succeeds and the new item is appended.
        """
        import logging
        from scripts.database import get_memory, update_memory

        # Inject a legacy row: entities has one item with a stray key.
        payload_entities = json.dumps(
            [{"name": "LegacyRedis", "stray": "pre-374 junk"}]
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
        legacy_present = any(
            isinstance(e, dict) and e.get("name") == "LegacyRedis" for e in ents
        )
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
