"""
Tests for pact-memory/scripts/models.py — data models for memory objects.

Tests cover:
1. TaskItem: from_dict (dict + string), to_dict, priority handling
2. Decision: from_dict (dict + string), to_dict, rationale/alternatives
3. Entity: from_dict (dict + string), to_dict, type/notes
4. _parse_string_list: None, list, JSON string, plain string, edge cases
5. _parse_datetime: None, datetime obj, ISO string, common formats
6. MemoryObject: from_dict (all field types), to_dict, to_storage_dict,
   get_searchable_text, __repr__, JSON string handling
7. memory_from_db_row: basic conversion, file injection
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'skills', 'pact-memory'))


# ---------------------------------------------------------------------------
# TaskItem
# ---------------------------------------------------------------------------

class TestTaskItem:
    def test_from_dict(self):
        from scripts.models import TaskItem
        t = TaskItem.from_dict({"task": "Write tests", "status": "in_progress", "priority": "high"})
        assert t.task == "Write tests"
        assert t.status == "in_progress"
        assert t.priority == "high"

    def test_from_string(self):
        from scripts.models import TaskItem
        t = TaskItem.from_dict("Write tests")
        assert t.task == "Write tests"
        assert t.status == "pending"
        assert t.priority is None

    def test_to_dict_with_priority(self):
        from scripts.models import TaskItem
        t = TaskItem(task="Do thing", status="completed", priority="low")
        d = t.to_dict()
        assert d == {"task": "Do thing", "status": "completed", "priority": "low"}

    def test_to_dict_without_priority(self):
        from scripts.models import TaskItem
        t = TaskItem(task="Do thing")
        d = t.to_dict()
        assert d == {"task": "Do thing", "status": "pending"}
        assert "priority" not in d

    def test_from_dict_defaults(self):
        from scripts.models import TaskItem
        t = TaskItem.from_dict({})
        assert t.task == ""
        assert t.status == "pending"


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

class TestDecision:
    def test_from_dict(self):
        from scripts.models import Decision
        d = Decision.from_dict({
            "decision": "Use factory pattern",
            "rationale": "Flexibility",
            "alternatives": ["Singleton", "Builder"]
        })
        assert d.decision == "Use factory pattern"
        assert d.rationale == "Flexibility"
        assert d.alternatives == ["Singleton", "Builder"]

    def test_from_string(self):
        from scripts.models import Decision
        d = Decision.from_dict("Use factory pattern")
        assert d.decision == "Use factory pattern"
        assert d.rationale is None

    def test_to_dict_full(self):
        from scripts.models import Decision
        d = Decision(decision="X", rationale="Y", alternatives=["A", "B"])
        result = d.to_dict()
        assert result == {"decision": "X", "rationale": "Y", "alternatives": ["A", "B"]}

    def test_to_dict_minimal(self):
        from scripts.models import Decision
        d = Decision(decision="X")
        result = d.to_dict()
        assert result == {"decision": "X"}
        assert "rationale" not in result
        assert "alternatives" not in result


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class TestEntity:
    def test_from_dict(self):
        from scripts.models import Entity
        e = Entity.from_dict({"name": "AuthService", "type": "service", "notes": "Core auth"})
        assert e.name == "AuthService"
        assert e.type == "service"
        assert e.notes == "Core auth"

    def test_from_string(self):
        from scripts.models import Entity
        e = Entity.from_dict("AuthService")
        assert e.name == "AuthService"
        assert e.type is None

    def test_to_dict_full(self):
        from scripts.models import Entity
        e = Entity(name="X", type="module", notes="note")
        assert e.to_dict() == {"name": "X", "type": "module", "notes": "note"}

    def test_to_dict_minimal(self):
        from scripts.models import Entity
        e = Entity(name="X")
        result = e.to_dict()
        assert result == {"name": "X"}


# ---------------------------------------------------------------------------
# _parse_string_list
# ---------------------------------------------------------------------------

class TestParseStringList:
    def test_none_returns_empty(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list(None) == []

    def test_empty_string_returns_empty(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list("") == []

    def test_list_input(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list(["a", "b"]) == ["a", "b"]

    def test_list_with_none_values(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list(["a", None, "b"]) == ["a", "b"]

    def test_json_array_string(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list('["a", "b"]') == ["a", "b"]

    def test_plain_string(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list("just a string") == ["just a string"]

    def test_json_non_array_string(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list('"quoted"') == ['"quoted"']

    def test_invalid_json_string(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list("{invalid") == ["{invalid"]

    def test_non_string_non_list(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list(42) == []

    def test_list_converts_ints_to_str(self):
        from scripts.models import _parse_string_list
        assert _parse_string_list([1, 2]) == ["1", "2"]


# ---------------------------------------------------------------------------
# _parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_none_returns_none(self):
        from scripts.models import _parse_datetime
        assert _parse_datetime(None) is None

    def test_datetime_passthrough(self):
        from scripts.models import _parse_datetime
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _parse_datetime(dt) is dt

    def test_iso_format(self):
        from scripts.models import _parse_datetime
        result = _parse_datetime("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1

    def test_iso_format_with_z(self):
        from scripts.models import _parse_datetime
        result = _parse_datetime("2024-01-15T10:30:00Z")
        assert result is not None

    def test_datetime_format(self):
        from scripts.models import _parse_datetime
        result = _parse_datetime("2024-01-15 10:30:00")
        assert result is not None

    def test_date_only_format(self):
        from scripts.models import _parse_datetime
        result = _parse_datetime("2024-01-15")
        assert result is not None

    def test_invalid_string_returns_none(self):
        from scripts.models import _parse_datetime
        assert _parse_datetime("not a date") is None

    def test_non_string_non_datetime_returns_none(self):
        from scripts.models import _parse_datetime
        assert _parse_datetime(12345) is None


# ---------------------------------------------------------------------------
# MemoryObject
# ---------------------------------------------------------------------------

class TestMemoryObject:
    def test_from_dict_minimal(self):
        from scripts.models import MemoryObject
        m = MemoryObject.from_dict({"id": "abc", "context": "Working on auth"})
        assert m.id == "abc"
        assert m.context == "Working on auth"
        assert m.active_tasks == []
        assert m.lessons_learned == []

    def test_from_dict_full(self):
        from scripts.models import MemoryObject
        data = {
            "id": "abc",
            "context": "Auth module",
            "goal": "Implement OAuth",
            "active_tasks": [{"task": "Write tests", "status": "pending"}],
            "lessons_learned": ["Use mocks"],
            "decisions": [{"decision": "Factory pattern", "rationale": "Flexibility"}],
            "entities": [{"name": "AuthService", "type": "service"}],
            "reasoning_chains": ["X because Y"],
            "agreements_reached": ["Agreed on API"],
            "disagreements_resolved": ["Resolved caching approach"],
            "files": ["src/auth.py"],
            "project_id": "proj-1",
            "session_id": "sess-1",
            "created_at": "2024-01-15T10:00:00+00:00",
            "updated_at": "2024-01-15T11:00:00+00:00",
        }
        m = MemoryObject.from_dict(data)
        assert m.id == "abc"
        assert len(m.active_tasks) == 1
        assert m.active_tasks[0].task == "Write tests"
        assert m.lessons_learned == ["Use mocks"]
        assert len(m.decisions) == 1
        assert m.decisions[0].rationale == "Flexibility"
        assert len(m.entities) == 1
        assert m.entities[0].type == "service"
        assert m.reasoning_chains == ["X because Y"]
        assert m.files == ["src/auth.py"]

    def test_from_dict_json_string_tasks(self):
        from scripts.models import MemoryObject
        data = {
            "id": "abc",
            "active_tasks": json.dumps([{"task": "Test", "status": "done"}])
        }
        m = MemoryObject.from_dict(data)
        assert len(m.active_tasks) == 1
        assert m.active_tasks[0].task == "Test"

    def test_from_dict_json_string_lessons(self):
        from scripts.models import MemoryObject
        data = {"id": "abc", "lessons_learned": json.dumps(["L1", "L2"])}
        m = MemoryObject.from_dict(data)
        assert m.lessons_learned == ["L1", "L2"]

    def test_from_dict_json_string_decisions(self):
        from scripts.models import MemoryObject
        data = {"id": "abc", "decisions": json.dumps([{"decision": "D1"}])}
        m = MemoryObject.from_dict(data)
        assert len(m.decisions) == 1

    def test_from_dict_json_string_entities(self):
        from scripts.models import MemoryObject
        data = {"id": "abc", "entities": json.dumps([{"name": "E1"}])}
        m = MemoryObject.from_dict(data)
        assert len(m.entities) == 1

    def test_from_dict_plain_string_tasks(self):
        from scripts.models import MemoryObject
        data = {"id": "abc", "active_tasks": "do something"}
        m = MemoryObject.from_dict(data)
        assert len(m.active_tasks) == 1
        assert m.active_tasks[0].task == "do something"

    def test_from_dict_none_values(self):
        from scripts.models import MemoryObject
        data = {
            "id": "abc",
            "active_tasks": [None, {"task": "T"}],
            "decisions": [None],
            "entities": [None],
        }
        m = MemoryObject.from_dict(data)
        assert len(m.active_tasks) == 1
        assert len(m.decisions) == 0
        assert len(m.entities) == 0

    def test_to_dict(self):
        from scripts.models import MemoryObject, TaskItem, Decision
        m = MemoryObject(
            id="abc",
            context="ctx",
            active_tasks=[TaskItem(task="T", status="done")],
            decisions=[Decision(decision="D", rationale="R")],
        )
        d = m.to_dict()
        assert d["id"] == "abc"
        assert d["active_tasks"] == [{"task": "T", "status": "done"}]
        assert d["decisions"] == [{"decision": "D", "rationale": "R"}]

    def test_to_dict_with_datetime(self):
        from scripts.models import MemoryObject
        dt = datetime(2024, 1, 15, tzinfo=timezone.utc)
        m = MemoryObject(id="abc", created_at=dt)
        d = m.to_dict()
        assert "2024-01-15" in d["created_at"]

    def test_to_storage_dict_excludes_files(self):
        from scripts.models import MemoryObject
        m = MemoryObject(id="abc", files=["src/a.py"])
        d = m.to_storage_dict()
        assert "files" not in d

    def test_get_searchable_text_all_fields(self):
        from scripts.models import MemoryObject, TaskItem, Decision, Entity
        m = MemoryObject(
            id="abc",
            context="Auth module",
            goal="Implement OAuth",
            active_tasks=[TaskItem(task="Write tests")],
            lessons_learned=["Use mocks"],
            decisions=[Decision(decision="Factory", rationale="Flexibility")],
            entities=[Entity(name="AuthService", type="service")],
            reasoning_chains=["X because Y"],
            agreements_reached=["Agreed on API"],
            disagreements_resolved=["Resolved caching"],
        )
        text = m.get_searchable_text()
        assert "Context: Auth module" in text
        assert "Goal: Implement OAuth" in text
        assert "Tasks: Write tests" in text
        assert "Lessons: Use mocks" in text
        assert "Decisions: Factory (Flexibility)" in text
        assert "Entities: AuthService (service)" in text
        assert "Reasoning: X because Y" in text
        assert "Agreements: Agreed on API" in text
        assert "Disagreements resolved: Resolved caching" in text

    def test_get_searchable_text_empty(self):
        from scripts.models import MemoryObject
        m = MemoryObject(id="abc")
        assert m.get_searchable_text() == ""

    def test_repr_short(self):
        from scripts.models import MemoryObject
        m = MemoryObject(id="abc", context="Short")
        r = repr(m)
        assert "abc" in r
        assert "Short" in r

    def test_repr_truncates_long_context(self):
        from scripts.models import MemoryObject
        m = MemoryObject(id="abc", context="A" * 100)
        r = repr(m)
        assert "..." in r


# ---------------------------------------------------------------------------
# memory_from_db_row
# ---------------------------------------------------------------------------

class TestMemoryFromDbRow:
    def test_basic_conversion(self):
        from scripts.models import memory_from_db_row
        row = {"id": "abc", "context": "test", "goal": "goal"}
        m = memory_from_db_row(row)
        assert m.id == "abc"
        assert m.context == "test"

    def test_injects_files(self):
        from scripts.models import memory_from_db_row
        row = {"id": "abc", "context": "test"}
        m = memory_from_db_row(row, files=["src/a.py", "src/b.py"])
        assert m.files == ["src/a.py", "src/b.py"]

    def test_no_files_arg(self):
        from scripts.models import memory_from_db_row
        row = {"id": "abc"}
        m = memory_from_db_row(row)
        assert m.files == []


# ---------------------------------------------------------------------------
# Bug 3 fix (#374) — strict-on-write / lenient-on-read key validation
# ---------------------------------------------------------------------------
#
# Before PR #374's commit 2, Entity/Decision/TaskItem.from_dict silently
# dropped unknown sub-object keys via data.get() on a hardcoded field list.
# A payload like `{"name": "X", "description": "Y"}` would construct an
# Entity with name="X" and lose `description` (the real field is `notes`).
#
# New contract:
#   - Write paths call from_dict(..., strict=True): unknown keys raise
#     ValueError with a message naming the bad keys and the allowed set.
#   - Read paths call from_dict(..., strict=False) (the default): unknown
#     keys are dropped with a logger.warning so legacy DB rows with stray
#     keys remain readable.


class TestFromDictStrictMode:
    """Bug 3 — strict=True raises on unknown sub-object keys."""

    def test_entity_strict_raises_on_unknown_key(self):
        from scripts.models import Entity
        with pytest.raises(ValueError, match="Unknown keys for Entity"):
            Entity.from_dict({"name": "Redis", "description": "cache"}, strict=True)

    def test_decision_strict_raises_on_unknown_key(self):
        from scripts.models import Decision
        with pytest.raises(ValueError, match="Unknown keys for Decision"):
            Decision.from_dict(
                {"decision": "Use Redis", "reason": "fast"},
                strict=True,
            )

    def test_taskitem_strict_raises_on_unknown_key(self):
        from scripts.models import TaskItem
        with pytest.raises(ValueError, match="Unknown keys for TaskItem"):
            TaskItem.from_dict(
                {"task": "Write tests", "due": "tomorrow"},
                strict=True,
            )

    def test_strict_error_lists_allowed_keys(self):
        from scripts.models import Entity
        with pytest.raises(ValueError) as excinfo:
            Entity.from_dict({"name": "X", "bogus": "y"}, strict=True)
        msg = str(excinfo.value)
        assert "bogus" in msg
        assert "Allowed keys" in msg
        assert "name" in msg
        assert "type" in msg
        assert "notes" in msg

    def test_strict_error_includes_memory_context(self):
        """memory_id arg surfaces in the error for debugging."""
        from scripts.models import Entity
        with pytest.raises(ValueError, match="in memory mem-123"):
            Entity.from_dict(
                {"name": "X", "bogus": "y"},
                strict=True,
                memory_id="mem-123",
            )

    def test_strict_valid_payload_still_works(self):
        """Happy path: well-formed dict still constructs correctly under strict."""
        from scripts.models import Entity
        e = Entity.from_dict(
            {"name": "Redis", "type": "service", "notes": "in-memory cache"},
            strict=True,
        )
        assert e.name == "Redis"
        assert e.type == "service"
        assert e.notes == "in-memory cache"

    def test_strict_mode_ignored_for_string_input(self):
        """from_dict(str, strict=True) treats the string as the primary field."""
        from scripts.models import Entity
        e = Entity.from_dict("Redis", strict=True)
        assert e.name == "Redis"


class TestFromDictLenientMode:
    """Bug 3 — strict=False (default) drops unknown keys with a warning."""

    def test_entity_lenient_drops_unknown_key(self, caplog):
        from scripts.models import Entity
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        e = Entity.from_dict({"name": "X", "legacy_field": "keep"})
        assert e.name == "X"
        assert e.type is None
        assert e.notes is None
        assert "legacy_field" in caplog.text
        assert "Entity" in caplog.text

    def test_decision_lenient_drops_unknown_key(self, caplog):
        from scripts.models import Decision
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        d = Decision.from_dict({"decision": "Use X", "stray": "value"})
        assert d.decision == "Use X"
        assert "stray" in caplog.text

    def test_taskitem_lenient_drops_unknown_key(self, caplog):
        from scripts.models import TaskItem
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        t = TaskItem.from_dict({"task": "x", "owner": "alice"})
        assert t.task == "x"
        assert "owner" in caplog.text

    def test_lenient_is_default(self, caplog):
        """Calling without the strict kwarg should be lenient."""
        from scripts.models import Entity
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        # No exception — lenient is default
        Entity.from_dict({"name": "X", "foo": "bar"})
        assert "foo" in caplog.text

    def test_memoryobject_read_path_tolerates_legacy_stray_keys(self, caplog):
        """
        End-to-end read path: MemoryObject.from_dict (which processes raw rows
        from the DB) must tolerate legacy rows whose entities/decisions/tasks
        contain stray keys from before PR #374. The stray key should be
        dropped with a warning; the memory must still be readable.
        """
        from scripts.models import MemoryObject
        import logging
        caplog.set_level(logging.WARNING, logger="scripts.models")
        data = {
            "id": "legacy-mem",
            "context": "legacy row",
            "entities": [{"name": "Redis", "legacy_field": "from old schema"}],
            "decisions": [{"decision": "Use Redis", "old_cite": "ADR-1"}],
        }
        m = MemoryObject.from_dict(data)
        assert m.id == "legacy-mem"
        assert m.entities[0].name == "Redis"
        assert m.decisions[0].decision == "Use Redis"
        assert "legacy_field" in caplog.text
        assert "old_cite" in caplog.text


class TestAllowedKeysInvariant:
    """
    Cheap safety net: _ALLOWED_KEYS must match the dataclass fields exactly.

    If a future edit adds a dataclass field without updating _ALLOWED_KEYS,
    strict-mode validation will reject the new field as "unknown" — silently
    re-creating bug 3. This invariant fails loudly at test time instead.
    """

    def test_entity_allowed_keys_matches_fields(self):
        import dataclasses
        from scripts.models import Entity
        field_names = {f.name for f in dataclasses.fields(Entity)}
        assert Entity._ALLOWED_KEYS == field_names

    def test_decision_allowed_keys_matches_fields(self):
        import dataclasses
        from scripts.models import Decision
        field_names = {f.name for f in dataclasses.fields(Decision)}
        assert Decision._ALLOWED_KEYS == field_names

    def test_taskitem_allowed_keys_matches_fields(self):
        import dataclasses
        from scripts.models import TaskItem
        field_names = {f.name for f in dataclasses.fields(TaskItem)}
        assert TaskItem._ALLOWED_KEYS == field_names
