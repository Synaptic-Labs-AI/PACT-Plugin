"""
Location: pact-plugin/tests/test_store_scope_edges.py
Summary: Three edges of the legacy-scope reader and its store gate: the
read-only probe closes a connection it opened when its schema check fails, a
save filed under a payload-supplied id reports that bucket's own prior count,
and a repository at the filesystem root contains its own rows.
Used by: the test suite; no production code imports it.
"""

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from scripts.database import (
    _contained_by_root,
    count_project_memories,
    db_connection,
    ensure_initialized,
)
from scripts.memory_api import PACTMemory

from scripts import memory_api

CREATED_AT = "2026-01-01T00:00:00+00:00"


def test_the_probe_closes_its_connection_when_the_schema_check_fails(tmp_path, monkeypatch):
    """A store that opens but cannot answer the schema check (a corrupt
    file) raises the store fault out of the probe; the connection it opened
    is closed, not left to the garbage collector."""
    store = tmp_path / "corrupt.db"
    store.write_bytes(b"not a database" * 100)
    opened = []
    real_open = memory_api._read_only_connection

    def tracking_open(path):
        connection = real_open(path)
        opened.append(connection)
        return connection

    monkeypatch.setattr(memory_api, "_read_only_connection", tracking_open)
    with pytest.raises(memory_api.StoreFaultError):
        with memory_api._legacy_probe_context(str(store), None):
            pass
    assert len(opened) == 1
    with pytest.raises(memory_api.sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_a_supplied_id_reports_its_own_buckets_prior_count(tmp_path, monkeypatch):
    """The save envelope's `legacy_scope.canonical_count` is the count the
    save FOUND in the bucket it filed under. For a payload-supplied id that
    differs from the instance's scope, that is the supplied bucket's count,
    not the instance's."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "widget", remote="https://github.com/acme/widget.git")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for index in range(3):
            conn.execute(
                "INSERT INTO memories (id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'row', 'github.com/acme/other', 's', ?, ?)",
                (f"other-{index}", CREATED_AT, CREATED_AT),
            )
        conn.commit()
    monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
    monkeypatch.setattr(PACTMemory, "_store_embedding", lambda self, *a, **k: None)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory.project_id == "github.com/acme/widget"

    memory.save({"context": "elsewhere", "project_id": "github.com/acme/other"}, sync_to_claude=False)

    scope = memory.last_project_scope
    assert scope["project_id"] == "github.com/acme/other"
    assert scope["legacy_scope"]["canonical_count"] == 3
    with db_connection(store) as conn:
        assert count_project_memories(conn, "github.com/acme/other") == 4


@pytest.mark.parametrize(
    "row, expected",
    [("/srv/project/a.py", True), ("/a.py", True)],
)
def test_the_filesystem_root_contains_its_rows(row, expected):
    assert _contained_by_root(row, "/", {}, None) is expected
