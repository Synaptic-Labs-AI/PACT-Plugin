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
    search_memories_by_text,
    JSON_FIELDS,
)
from scripts.models import MemoryObject
from scripts.working_memory import _format_memory_entry


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
