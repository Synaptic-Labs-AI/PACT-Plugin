"""The union read scope's normalisation, and one path guard beside it.

Location: pact-plugin/tests/test_remediation_store_scope.py.

`database.normalise_project_scopes` and `search._scope_or_none` normalise the
scope every store, graph and search seam reads
(`skills/pact-memory/scripts/{database,graph,search}.py`). These arms pin:

- a repeated scope id is kept once, so a per-scope loop cannot return one
  memory twice;
- an empty-string scope means NO ROWS at every layer, never the whole store;
- `_harden_sidecars` never treats an empty path as a file in the cwd.

Each arm names the defect it would catch. Every store is a scratch file under
pytest's `tmp_path`.
"""

from __future__ import annotations

import os
import struct
from unittest.mock import patch

import pytest
from helpers import create_test_schema
from scripts.database import normalise_project_scopes
from scripts.database import sqlite3 as db_sqlite3
from scripts.graph import get_graph_stats

from scripts import database as db
from scripts import graph, search

SCOPE = "github.com/syn-labs/pact-plugin"


def _insert_memory(conn, memory_id, project):
    conn.execute(
        "INSERT INTO memories "
        "(id, context, project_id, session_id, created_at, updated_at) "
        "VALUES (?, 'c', ?, 's', '2026-01-01T00:00:00+00:00', "
        "'2026-01-01T00:00:00+00:00')",
        (memory_id, project),
    )


class TestARepeatedScopeIsKeptOnce:
    def test_the_normaliser_dedupes_in_first_seen_order(self):
        """Catches a union that carries an id twice.

        RED WHEN duplicates survive, or the first-seen order changes (the
        order is the per-scope query order).
        """
        assert normalise_project_scopes((SCOPE, "b", SCOPE, "b")) == (SCOPE, "b")

    def test_vector_search_returns_each_memory_once_for_a_repeated_scope(
        self, tmp_path
    ):
        """Catches the per-scope KNN loop querying a repeated scope twice.

        `vector_search` runs one KNN per scope and merges, so `(A, A)` used to
        return every hit twice. RED WHEN the normaliser stops deduplicating.
        """
        sqlite_vec = pytest.importorskip("sqlite_vec")
        if not db.SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 build cannot load extensions")
        conn = db_sqlite3.connect(str(tmp_path / "vec.db"))
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[4])"
        )
        for memory_id, vector in (("m1", (1.0, 0.0, 0.0, 0.0)), ("m2", (0.0, 1.0, 0.0, 0.0))):
            _insert_memory(conn, memory_id, SCOPE)
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding) "
                "VALUES (?, ?, ?)",
                (memory_id, SCOPE, struct.pack("4f", *vector)),
            )
        conn.commit()
        try:
            with patch.object(
                search, "generate_embedding", lambda text: [1.0, 0.0, 0.0, 0.0]
            ):
                hits = search.vector_search(conn, "q", (SCOPE, SCOPE), limit=10)
        finally:
            conn.close()
        assert [memory_id for memory_id, _distance in hits] == ["m1", "m2"]


class TestAnEmptyStringIsNoRowsAtEveryLayer:
    def test_search_store_and_graph_agree_that_an_empty_string_is_empty(
        self, tmp_path, monkeypatch
    ):
        """Catches one input with two meanings across sibling seams.

        `graph.get_graph_stats(conn, "")` and the store layer already answered
        zero for `""`, while `search._scope_or_none("")` returned `None`, the
        whole-store scope. RED WHEN any layer reads `""` as the whole store.
        The `None` calls are the control that the rows are reachable.
        """
        # The schema is built here; the initialiser would also size a vector
        # table from the embedding service, which this arm does not need.
        monkeypatch.setattr(graph, "ensure_initialized", lambda conn: None)
        conn = db_sqlite3.connect(str(tmp_path / "scope.db"))
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        _insert_memory(conn, "m-null", None)
        conn.commit()
        try:
            assert search._scope_or_none("") == ()
            assert normalise_project_scopes("") == ()
            assert get_graph_stats(conn, "")["memories"] == 0
            assert search._scope_or_none(None) is None
            assert get_graph_stats(conn, None)["memories"] == 1
        finally:
            conn.close()


class TestAnEmptyPathIsNoPath:
    def test_init_schema_on_an_in_memory_store_leaves_cwd_sidecars_alone(
        self, tmp_path, monkeypatch
    ):
        """Catches `_harden_sidecars("")` chmodding `./-wal` and `./-shm`.

        `PRAGMA database_list` reports `""` for an in-memory database, and
        `"" + "-wal"` names a file in the current directory. RED WHEN the
        guard tests `is None` instead of emptiness: the two files below are
        then changed to 0600.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(db, "_init_vector_table", lambda conn: False)
        for name in ("-wal", "-shm"):
            (tmp_path / name).write_text("not a sidecar")
            os.chmod(tmp_path / name, 0o644)
        conn = db_sqlite3.connect(":memory:")
        try:
            db.init_schema(conn)
        finally:
            conn.close()
        for name in ("-wal", "-shm"):
            assert (tmp_path / name).stat().st_mode & 0o777 == 0o644, name
