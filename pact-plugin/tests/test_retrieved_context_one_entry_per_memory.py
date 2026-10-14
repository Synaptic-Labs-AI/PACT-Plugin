"""
Location: pact-plugin/tests/test_retrieved_context_one_entry_per_memory.py

Summary: The Retrieved Context sync in skills/pact-memory/scripts/working_memory.py
keeps one entry per memory. Retrieving a memory already in the window moves it
to the top with its new header (time and query) rather than adding a second
copy, which would evict a distinct memory. Entries are compared by their
`**Memory ID**` line, so a memory found by another query is still one memory,
and an entry with no id is never compared.

SAFETY: every row pins CLAUDE_PROJECT_DIR to tmp_path and passes the declared
anchor, so no resolver can reach a real CLAUDE.md.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from scripts import working_memory
from scripts.working_memory import (
    _MEMORY_ID_LABEL,
    _parse_retrieved_context_section,
    sync_retrieved_to_claude_md,
)
from tests import test_pin_marker_writer as _writer_tests


class _FrozenClock(datetime):
    """Every call in one minute, so two syncs of one memory format alike."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 2, 3, 4, tzinfo=tz)


@pytest.fixture
def project(tmp_path, monkeypatch):
    head, tail = _writer_tests.production_head_and_tail()
    root = tmp_path / "project"
    root.mkdir()
    path = root / "CLAUDE.md"
    path.write_text(head + tail, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setattr(working_memory, "datetime", _FrozenClock)
    return root, path


def _retrieve(project, memory_id, query="a query"):
    root, _path = project
    memory = {"context": f"context of {memory_id}", "goal": "a goal"}
    ids = [memory_id] if memory_id else None
    assert sync_retrieved_to_claude_md([memory], query, None, ids, claude_md_root=root)


def _entries(project) -> list[str]:
    parsed = _parse_retrieved_context_section(project[1].read_text(encoding="utf-8"))
    assert isinstance(parsed, tuple) and parsed[1], parsed
    return parsed[3]


def _ids(project) -> list[str]:
    return [line.split(": ", 1)[1] for entry in _entries(project)
            for line in entry.split("\n") if line.startswith(_MEMORY_ID_LABEL)]


def test_the_same_memory_twice_leaves_one_entry_and_the_file_unchanged(project):
    _retrieve(project, "a")
    first = project[1].read_bytes()

    _retrieve(project, "a")

    assert _ids(project) == ["a"]
    assert project[1].read_bytes() == first


def test_a_repeat_moves_to_the_top_with_its_new_header(project):
    _retrieve(project, "a", "first query")
    _retrieve(project, "b")
    _retrieve(project, "a", "third query")

    assert _ids(project) == ["a", "b"]
    assert '**Query**: "third query"' in _entries(project)[0]


def test_a_repeat_in_a_full_window_keeps_three_distinct_memories(project):
    """The repeat is the middle entry. Repeating the oldest would push its
    old copy out of the window anyway, so it could not show the rule."""
    for memory_id in ("a", "b", "c", "b"):
        _retrieve(project, memory_id)

    assert _ids(project) == ["b", "c", "a"]


def test_one_memory_found_by_two_queries_is_one_entry_with_the_second_query(project):
    _retrieve(project, "a", "q1")
    _retrieve(project, "a", "q2")

    entries = _entries(project)
    assert len(entries) == 1
    assert '**Query**: "q2"' in entries[0]


def test_an_entry_without_an_id_is_prepended_as_before(project):
    _retrieve(project, None)
    _retrieve(project, None)

    entries = _entries(project)
    assert len(entries) == 2 and entries[0] == entries[1]
    assert _MEMORY_ID_LABEL not in entries[0]


def test_an_existing_entry_without_an_id_is_kept(project):
    _retrieve(project, None)
    _retrieve(project, "a")
    _retrieve(project, "a")

    entries = _entries(project)
    assert len(entries) == 2
    assert _ids(project) == ["a"] and _MEMORY_ID_LABEL not in entries[1]
