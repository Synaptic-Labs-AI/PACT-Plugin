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
