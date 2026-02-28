"""
Tests for CT-aware memory fields (v3.8.0 — CT Phase 2 Extract, item 2G).

Location: pact-plugin/tests/test_memory_ct_fields.py

Verifies that reasoning_chains, agreements_reached, and disagreements_resolved
fields are stored, retrieved, serialized, and searchable.

Used by: pytest test suite to validate CT field implementation across
database.py, models.py, and working_memory.py.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

# Add paths for imports — scripts/ is a package with __init__.py,
# so we add its parent to sys.path for proper relative imports.
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))

from scripts.database import (
    get_connection,
    init_schema,
    create_memory,
    get_memory,
    update_memory,
    delete_memory,
    search_memories_by_text,
    ensure_initialized,
    _migrate_ct_fields,
    JSON_FIELDS,
)
from scripts.models import MemoryObject, _parse_string_list
from scripts.working_memory import _format_memory_entry, sync_to_claude_md


@pytest.fixture
def db_conn():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        conn = get_connection(db_path)
        init_schema(conn)
        yield conn
        conn.close()


class TestDatabaseCTFields:
    """Test CT fields in database layer."""

    def test_ct_fields_in_json_fields_set(self):
        """New CT fields must be in JSON_FIELDS for serialization."""
        assert "reasoning_chains" in JSON_FIELDS
        assert "agreements_reached" in JSON_FIELDS
        assert "disagreements_resolved" in JSON_FIELDS

    def test_create_memory_with_ct_fields(self, db_conn):
        """Create a memory with all three CT fields and verify round-trip."""
        memory = {
            "context": "Implementing auth module",
            "goal": "Add JWT refresh tokens",
            "reasoning_chains": [
                "Chose Redis because TTL support → needed for token expiry → simpler than DB cleanup jobs"
            ],
            "agreements_reached": [
                "Lead and architect agree: Redis for token blacklist (verified via teachback)"
            ],
            "disagreements_resolved": [
                "Backend wanted session tokens, architect wanted stateless JWT — resolved: stateless JWT with Redis blacklist for revocation"
            ],
        }
        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved is not None
        assert retrieved["reasoning_chains"] == memory["reasoning_chains"]
        assert retrieved["agreements_reached"] == memory["agreements_reached"]
        assert retrieved["disagreements_resolved"] == memory["disagreements_resolved"]

    def test_create_memory_without_ct_fields(self, db_conn):
        """Backwards compat: memories without CT fields still work."""
        memory = {
            "context": "Simple task",
            "goal": "Do the thing",
        }
        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved is not None
        assert retrieved["context"] == "Simple task"
        # CT fields should be None when not provided
        assert retrieved.get("reasoning_chains") is None
        assert retrieved.get("agreements_reached") is None
        assert retrieved.get("disagreements_resolved") is None

    def test_search_includes_ct_fields(self, db_conn):
        """Keyword search should find memories via CT field content."""
        memory = {
            "context": "Working on auth",
            "reasoning_chains": ["Chose bcrypt for password hashing because OWASP recommendation"],
        }
        create_memory(db_conn, memory)

        results = search_memories_by_text(db_conn, "bcrypt")
        assert len(results) >= 1
        assert "bcrypt" in json.dumps(results[0].get("reasoning_chains", ""))


class TestModelCTFields:
    """Test CT fields in MemoryObject model."""

    def test_memory_object_with_ct_fields(self):
        """MemoryObject should accept and round-trip CT fields."""
        data = {
            "id": "test123",
            "context": "Test context",
            "reasoning_chains": ["A because B, which required C"],
            "agreements_reached": ["Verified via teachback: use Redis"],
            "disagreements_resolved": ["Resolved: JWT over sessions"],
        }
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == ["A because B, which required C"]
        assert obj.agreements_reached == ["Verified via teachback: use Redis"]
        assert obj.disagreements_resolved == ["Resolved: JWT over sessions"]

        # Round-trip through to_dict
        d = obj.to_dict()
        assert d["reasoning_chains"] == ["A because B, which required C"]
        assert d["agreements_reached"] == ["Verified via teachback: use Redis"]
        assert d["disagreements_resolved"] == ["Resolved: JWT over sessions"]

    def test_memory_object_without_ct_fields(self):
        """MemoryObject without CT fields defaults to empty lists."""
        data = {"id": "test456", "context": "Minimal"}
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == []
        assert obj.agreements_reached == []
        assert obj.disagreements_resolved == []

    def test_memory_object_searchable_text_includes_ct(self):
        """get_searchable_text should include CT field content."""
        data = {
            "id": "test789",
            "reasoning_chains": ["Chose X because Y"],
            "agreements_reached": ["Teachback confirmed: use pattern Z"],
        }
        obj = MemoryObject.from_dict(data)
        text = obj.get_searchable_text()

        assert "Chose X because Y" in text
        assert "Teachback confirmed" in text

    def test_memory_object_storage_dict_includes_ct(self):
        """to_storage_dict should include CT fields for database storage."""
        data = {
            "id": "testabc",
            "reasoning_chains": ["chain1"],
            "agreements_reached": ["agreement1"],
            "disagreements_resolved": ["resolved1"],
        }
        obj = MemoryObject.from_dict(data)
        storage = obj.to_storage_dict()

        assert storage["reasoning_chains"] == ["chain1"]
        assert storage["agreements_reached"] == ["agreement1"]
        assert storage["disagreements_resolved"] == ["resolved1"]

    def test_memory_object_ct_fields_json_string_parsing(self):
        """CT fields stored as JSON strings should be parsed back to lists."""
        data = {
            "id": "testjson",
            "reasoning_chains": json.dumps(["chain from db"]),
            "agreements_reached": json.dumps(["agreement from db"]),
            "disagreements_resolved": json.dumps(["resolved from db"]),
        }
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == ["chain from db"]
        assert obj.agreements_reached == ["agreement from db"]
        assert obj.disagreements_resolved == ["resolved from db"]


class TestWorkingMemoryFormatting:
    """Test CT fields in working memory display."""

    def test_format_entry_with_ct_fields(self):
        """Working memory entry should display CT fields."""
        memory = {
            "context": "Auth implementation",
            "reasoning_chains": ["Chose bcrypt because OWASP recommends it"],
            "agreements_reached": ["Lead confirmed: Redis for blacklist"],
            "disagreements_resolved": ["JWT won over sessions — stateless preferred"],
        }
        formatted = _format_memory_entry(memory)

        assert "**Reasoning chains**:" in formatted
        assert "bcrypt" in formatted
        assert "**Agreements**:" in formatted
        assert "Redis" in formatted
        assert "**Disagreements resolved**:" in formatted
        assert "JWT" in formatted

    def test_format_entry_without_ct_fields(self):
        """Working memory entry without CT fields omits those lines."""
        memory = {"context": "Simple task"}
        formatted = _format_memory_entry(memory)

        assert "**Reasoning chains**:" not in formatted
        assert "**Agreements**:" not in formatted
        assert "**Disagreements resolved**:" not in formatted
        assert "Simple task" in formatted

    def test_format_entry_ct_fields_as_strings(self):
        """CT fields passed as plain strings should render correctly."""
        memory = {
            "context": "Test",
            "reasoning_chains": "Single chain as string",
            "agreements_reached": "Single agreement as string",
            "disagreements_resolved": "Single resolution as string",
        }
        formatted = _format_memory_entry(memory)

        assert "**Reasoning chains**: Single chain as string" in formatted
        assert "**Agreements**: Single agreement as string" in formatted
        assert "**Disagreements resolved**: Single resolution as string" in formatted

    def test_format_entry_ct_fields_empty_lists(self):
        """Empty list CT fields should be omitted from formatting."""
        memory = {
            "context": "Test",
            "reasoning_chains": [],
            "agreements_reached": [],
            "disagreements_resolved": [],
        }
        formatted = _format_memory_entry(memory)

        assert "**Reasoning chains**:" not in formatted
        assert "**Agreements**:" not in formatted
        assert "**Disagreements resolved**:" not in formatted

    def test_format_entry_ct_fields_multiple_items(self):
        """Multiple CT field items should be joined with commas."""
        memory = {
            "reasoning_chains": ["chain A", "chain B", "chain C"],
            "agreements_reached": ["agree 1", "agree 2"],
        }
        formatted = _format_memory_entry(memory)

        assert "chain A, chain B, chain C" in formatted
        assert "agree 1, agree 2" in formatted


class TestParseStringList:
    """Test the _parse_string_list helper used by all CT fields."""

    def test_none_returns_empty(self):
        assert _parse_string_list(None) == []

    def test_empty_string_returns_empty(self):
        assert _parse_string_list("") == []

    def test_empty_list_returns_empty(self):
        assert _parse_string_list([]) == []

    def test_list_of_strings(self):
        assert _parse_string_list(["a", "b"]) == ["a", "b"]

    def test_list_filters_none_values(self):
        assert _parse_string_list(["a", None, "b"]) == ["a", "b"]

    def test_list_converts_non_strings(self):
        """Non-string items in list are converted to str."""
        assert _parse_string_list([1, 2.5, True]) == ["1", "2.5", "True"]

    def test_json_string_parsed_to_list(self):
        assert _parse_string_list(json.dumps(["x", "y"])) == ["x", "y"]

    def test_plain_string_becomes_single_element_list(self):
        assert _parse_string_list("not json") == ["not json"]

    def test_json_non_list_falls_back_to_single_element(self):
        """JSON string that parses to a non-list (e.g., dict) falls back."""
        assert _parse_string_list(json.dumps({"key": "val"})) == ['{"key": "val"}']

    def test_json_list_with_none_values(self):
        """JSON list containing null values should filter them out."""
        assert _parse_string_list(json.dumps(["a", None, "b"])) == ["a", "b"]

    def test_non_list_non_string_type_returns_empty(self):
        """Integer or other non-list/non-string types return empty list."""
        assert _parse_string_list(42) == []
        assert _parse_string_list(3.14) == []

    def test_zero_returns_empty(self):
        """Falsy numeric values should return empty list."""
        assert _parse_string_list(0) == []

    def test_false_returns_empty(self):
        """Boolean False should return empty list."""
        assert _parse_string_list(False) == []


class TestMigrateCTFields:
    """Test the _migrate_ct_fields database migration."""

    def test_adds_columns_to_existing_db(self):
        """Migration adds CT columns to a database without them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "old_db.db"
            conn = get_connection(db_path)
            # Create a minimal table without CT columns
            conn.execute("""
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    context TEXT,
                    goal TEXT,
                    active_tasks TEXT,
                    lessons_learned TEXT,
                    decisions TEXT,
                    entities TEXT,
                    project_id TEXT,
                    session_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.commit()

            # Run migration
            _migrate_ct_fields(conn)

            # Verify columns exist by inserting data with CT fields
            conn.execute(
                "INSERT INTO memories (id, reasoning_chains, agreements_reached, disagreements_resolved) "
                "VALUES (?, ?, ?, ?)",
                ("test1", '["chain"]', '["agree"]', '["resolved"]')
            )
            conn.commit()

            cursor = conn.execute("SELECT reasoning_chains, agreements_reached, disagreements_resolved FROM memories WHERE id = ?", ("test1",))
            row = cursor.fetchone()
            assert row[0] == '["chain"]'
            assert row[1] == '["agree"]'
            assert row[2] == '["resolved"]'
            conn.close()

    def test_idempotent_migration(self):
        """Running migration twice should not error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_db.db"
            conn = get_connection(db_path)
            init_schema(conn)  # Already has CT columns

            # Running migration again should be safe
            _migrate_ct_fields(conn)
            _migrate_ct_fields(conn)

            # Verify DB still works
            memory_id = create_memory(conn, {"context": "test"})
            assert get_memory(conn, memory_id) is not None
            conn.close()


class TestEnsureInitializedMigration:
    """Test that ensure_initialized runs migration for existing databases."""

    def test_existing_db_gets_migration(self):
        """An existing database without CT columns gets them via ensure_initialized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            conn = get_connection(db_path)
            # Create legacy schema (no CT columns)
            conn.execute("""
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    context TEXT,
                    goal TEXT,
                    active_tasks TEXT,
                    lessons_learned TEXT,
                    decisions TEXT,
                    entities TEXT,
                    project_id TEXT,
                    session_id TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

            # ensure_initialized should detect existing table and run migration
            ensure_initialized(conn)

            # Verify CT columns exist
            conn.execute(
                "INSERT INTO memories (id, reasoning_chains) VALUES (?, ?)",
                ("test1", '["migrated"]')
            )
            conn.commit()

            cursor = conn.execute("SELECT reasoning_chains FROM memories WHERE id = ?", ("test1",))
            assert cursor.fetchone()[0] == '["migrated"]'
            conn.close()


class TestDatabaseCTFieldsEdgeCases:
    """Edge cases for CT fields in the database layer."""

    def test_create_with_empty_ct_lists(self, db_conn):
        """Empty lists should round-trip through database."""
        memory = {
            "context": "Edge case test",
            "reasoning_chains": [],
            "agreements_reached": [],
            "disagreements_resolved": [],
        }
        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved is not None
        assert retrieved["reasoning_chains"] == []
        assert retrieved["agreements_reached"] == []
        assert retrieved["disagreements_resolved"] == []

    def test_create_with_multiple_ct_items(self, db_conn):
        """Multiple items per CT field should round-trip."""
        memory = {
            "reasoning_chains": ["chain 1", "chain 2", "chain 3"],
            "agreements_reached": ["agree A", "agree B"],
            "disagreements_resolved": ["resolved X", "resolved Y"],
        }
        memory_id = create_memory(db_conn, memory)
        retrieved = get_memory(db_conn, memory_id)

        assert retrieved["reasoning_chains"] == ["chain 1", "chain 2", "chain 3"]
        assert retrieved["agreements_reached"] == ["agree A", "agree B"]
        assert retrieved["disagreements_resolved"] == ["resolved X", "resolved Y"]

    def test_update_memory_with_ct_fields(self, db_conn):
        """Updating a memory to add CT fields should work."""
        memory_id = create_memory(db_conn, {"context": "Initial"})

        updated = update_memory(db_conn, memory_id, {
            "reasoning_chains": ["new chain added"],
            "agreements_reached": ["new agreement"],
        })
        assert updated is True

        retrieved = get_memory(db_conn, memory_id)
        assert retrieved["reasoning_chains"] == ["new chain added"]
        assert retrieved["agreements_reached"] == ["new agreement"]

    def test_search_via_agreements_field(self, db_conn):
        """Search should find memories via agreements_reached content."""
        create_memory(db_conn, {
            "context": "Test search",
            "agreements_reached": ["Teachback verified: Redis cache strategy"],
        })

        results = search_memories_by_text(db_conn, "Teachback verified")
        assert len(results) >= 1

    def test_search_via_disagreements_field(self, db_conn):
        """Search should find memories via disagreements_resolved content."""
        create_memory(db_conn, {
            "context": "Test search",
            "disagreements_resolved": ["REST won over GraphQL for simplicity"],
        })

        results = search_memories_by_text(db_conn, "GraphQL")
        assert len(results) >= 1

    def test_search_no_false_positives(self, db_conn):
        """Search for absent terms should return no results."""
        create_memory(db_conn, {
            "context": "Something else",
            "reasoning_chains": ["unrelated chain"],
        })

        results = search_memories_by_text(db_conn, "xyznonexistent42")
        assert len(results) == 0


class TestModelCTFieldsEdgeCases:
    """Edge cases for CT fields in the MemoryObject model."""

    def test_searchable_text_omits_empty_ct_fields(self):
        """get_searchable_text should not include labels for empty CT fields."""
        obj = MemoryObject.from_dict({"id": "test", "context": "Only context"})
        text = obj.get_searchable_text()

        assert "Reasoning:" not in text
        assert "Agreements:" not in text
        assert "Disagreements resolved:" not in text

    def test_searchable_text_includes_disagreements(self):
        """get_searchable_text should include disagreements_resolved."""
        obj = MemoryObject.from_dict({
            "id": "test",
            "disagreements_resolved": ["REST over GraphQL"],
        })
        text = obj.get_searchable_text()

        assert "Disagreements resolved:" in text
        assert "REST over GraphQL" in text

    def test_from_dict_with_none_ct_fields(self):
        """Explicit None for CT fields should produce empty lists."""
        data = {
            "id": "test",
            "reasoning_chains": None,
            "agreements_reached": None,
            "disagreements_resolved": None,
        }
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == []
        assert obj.agreements_reached == []
        assert obj.disagreements_resolved == []

    def test_from_dict_ct_fields_with_mixed_types_in_list(self):
        """List containing mixed types should be converted to strings."""
        data = {
            "id": "test",
            "reasoning_chains": ["string item", 42, True],
        }
        obj = MemoryObject.from_dict(data)

        assert obj.reasoning_chains == ["string item", "42", "True"]

    def test_to_dict_empty_ct_fields_are_empty_lists(self):
        """to_dict for object with no CT data should have empty lists."""
        obj = MemoryObject.from_dict({"id": "test"})
        d = obj.to_dict()

        assert d["reasoning_chains"] == []
        assert d["agreements_reached"] == []
        assert d["disagreements_resolved"] == []

    def test_storage_dict_empty_ct_fields(self):
        """to_storage_dict for object with no CT data should have empty lists."""
        obj = MemoryObject.from_dict({"id": "test"})
        storage = obj.to_storage_dict()

        assert storage["reasoning_chains"] == []
        assert storage["agreements_reached"] == []
        assert storage["disagreements_resolved"] == []


class TestUpdateOverwriteCTFields:
    """Test that CT fields can be overwritten via update_memory."""

    def test_overwrite_ct_fields(self, db_conn):
        """Create a memory with CT fields, update them, verify new values returned."""
        original = {
            "context": "Original context",
            "reasoning_chains": ["original chain"],
            "agreements_reached": ["original agreement"],
            "disagreements_resolved": ["original resolution"],
        }
        memory_id = create_memory(db_conn, original)

        # Overwrite with new values
        updated = update_memory(db_conn, memory_id, {
            "reasoning_chains": ["updated chain A", "updated chain B"],
            "agreements_reached": ["updated agreement"],
            "disagreements_resolved": ["updated resolution"],
        })
        assert updated is True

        retrieved = get_memory(db_conn, memory_id)
        assert retrieved["reasoning_chains"] == ["updated chain A", "updated chain B"]
        assert retrieved["agreements_reached"] == ["updated agreement"]
        assert retrieved["disagreements_resolved"] == ["updated resolution"]
        # Original context should be untouched
        assert retrieved["context"] == "Original context"


class TestFormatEntryFieldOrder:
    """Test that CT field rendering order is correct in _format_memory_entry."""

    def test_ct_field_order_when_all_present(self):
        """reasoning_chains before agreements_reached before disagreements_resolved."""
        memory = {
            "context": "All fields present",
            "lessons_learned": ["lesson one"],
            "reasoning_chains": ["chain one"],
            "agreements_reached": ["agreement one"],
            "disagreements_resolved": ["resolution one"],
        }
        formatted = _format_memory_entry(memory)

        rc_pos = formatted.index("**Reasoning chains**:")
        ag_pos = formatted.index("**Agreements**:")
        dr_pos = formatted.index("**Disagreements resolved**:")

        assert rc_pos < ag_pos, "reasoning_chains should appear before agreements_reached"
        assert ag_pos < dr_pos, "agreements_reached should appear before disagreements_resolved"


class TestContentFieldsSetInAPI:
    """Test that CONTENT_FIELDS in memory_api.py includes CT fields."""

    def test_content_fields_includes_ct_fields(self):
        """The CONTENT_FIELDS set used for embedding regeneration must include CT fields."""
        from scripts.memory_api import CONTENT_FIELDS

        expected_ct_fields = {"reasoning_chains", "agreements_reached", "disagreements_resolved"}
        assert expected_ct_fields.issubset(CONTENT_FIELDS)


class TestDeleteMemoryWithCTFields:
    """Test delete_memory round-trip with CT fields."""

    def test_delete_removes_memory_with_ct_fields(self, db_conn):
        """Create a memory with CT fields, delete it, verify it's gone."""
        memory = {
            "context": "Memory to delete",
            "reasoning_chains": ["chain to delete"],
            "agreements_reached": ["agreement to delete"],
            "disagreements_resolved": ["resolution to delete"],
        }
        memory_id = create_memory(db_conn, memory)

        # Verify it exists
        assert get_memory(db_conn, memory_id) is not None

        # Delete it
        deleted = delete_memory(db_conn, memory_id)
        assert deleted is True

        # Verify it's gone
        assert get_memory(db_conn, memory_id) is None

        # Verify double-delete returns False
        assert delete_memory(db_conn, memory_id) is False


class TestSyncToClaudeMdWithCTFields:
    """End-to-end integration test for sync_to_claude_md with CT-enriched memories."""

    def test_sync_writes_ct_fields_to_claude_md(self, tmp_path):
        """Create a CT-enriched memory, sync to CLAUDE.md, verify CT content appears."""
        from unittest.mock import patch

        # Create a minimal CLAUDE.md with Working Memory section
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n\n"
            "## Working Memory\n"
            "<!-- Auto-managed by pact-memory skill. Last 3 memories shown. "
            "Full history searchable via pact-memory skill. -->\n",
            encoding="utf-8"
        )

        # CT-enriched memory
        memory = {
            "context": "Implementing CT Phase 2 extract",
            "goal": "Add teachback fields to pact-memory",
            "reasoning_chains": ["Redis chosen because TTL support needed for token expiry"],
            "agreements_reached": ["Lead and architect confirmed Redis for blacklist"],
            "disagreements_resolved": ["JWT won over session tokens for statelessness"],
            "lessons_learned": ["Always validate refresh token rotation"],
        }

        # Mock _get_claude_md_path to return our temp file
        with patch("scripts.working_memory._get_claude_md_path", return_value=claude_md):
            result = sync_to_claude_md(memory, memory_id="test-ct-sync")

        assert result is True

        # Read back and verify CT field content appears
        content = claude_md.read_text(encoding="utf-8")

        assert "**Reasoning chains**:" in content
        assert "Redis chosen because TTL" in content
        assert "**Agreements**:" in content
        assert "Lead and architect confirmed Redis" in content
        assert "**Disagreements resolved**:" in content
        assert "JWT won over session tokens" in content
        # Non-CT fields should also be present
        assert "**Context**:" in content
        assert "CT Phase 2 extract" in content
        assert "**Lessons**:" in content
        assert "**Memory ID**: test-ct-sync" in content
