"""Legacy-gate remediation arms for the memory store's read scope, its
session evidence and the prune ledger.

Each arm names the defect it catches and fails on the code before its fix.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fixtures.identity_git import assert_outside_repository
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory

from scripts import memory_api

NAME = "widget"


def _seed(store: Path, bucket: str, files=(), session_id: str = "s") -> None:
    """One memory in `bucket` written by `session_id`, plus file rows."""
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories "
            "(id, context, project_id, session_id, created_at, updated_at) "
            "VALUES (?, 'ctx', ?, ?, '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
            (f"m-{session_id}-{len(files)}", bucket, session_id),
        )
        for index, path in enumerate(files):
            conn.execute(
                "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
                (f"f{index}", str(path), bucket),
            )
        conn.commit()


@pytest.fixture
def scopes_clean():
    memory_api._LEGACY_SCOPE_CACHE.clear()
    memory_api._EXCLUSION_WARNED.clear()
    yield
    memory_api._LEGACY_SCOPE_CACHE.clear()
    memory_api._EXCLUSION_WARNED.clear()


# ---------------------------------------------------------------------------
# BR-1: the first read on an absent DERIVED store
# ---------------------------------------------------------------------------


def test_first_read_on_absent_derived_store_answers_empty_with_schema(
    tmp_path, monkeypatch
):
    """The catch-up is skipped (as when its session marker is spent), so only
    the read path itself can create the schema. Before the fix the store was
    left with no tables and the read failed."""
    assert_outside_repository(tmp_path)
    root = tmp_path / "derived"
    monkeypatch.setenv("PACT_TEST_MEMORY_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(memory_api, "_ensure_ready", lambda: None)
    memory = PACTMemory(session_id="s", project_id="github.com/acme/widget")
    assert memory.list(limit=5) == []
    store = root / "memory.db"
    with sqlite3.connect(str(store)) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "memories" in tables, sorted(tables)

