"""Legacy-gate remediation arms for the memory store's read scope, its
session evidence and the prune ledger.

Each arm names the defect it catches and fails on the code before its fix.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
)
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


# ---------------------------------------------------------------------------
# F1: a folder-name id applies the gate's exclusion arm
# ---------------------------------------------------------------------------


@pytest.fixture
def basename_env(tmp_path, monkeypatch, scopes_clean):
    """A repository with no origin, so its id is its folder name, and a
    same-named checkout elsewhere."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "mine" / NAME)
    other = tmp_path / "theirs" / NAME
    other.mkdir(parents=True)
    (other / "src.py").write_text("x\n")
    (repo / "src.py").write_text("x\n")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return repo, other, tmp_path / "store.db"


def _basename_memory(store: Path) -> PACTMemory:
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id_shape == "basename", memory._project_id
    assert memory._project_id == NAME
    return memory


def test_folder_name_read_excludes_a_bucket_placed_in_another_checkout(
    basename_env, caplog
):
    repo, other, store = basename_env
    _seed(store, NAME, files=[other / "src.py"])
    memory = _basename_memory(store)
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == ()
    assert memory.list(limit=5) == []
    assert memory._legacy_scope_state["excluded"] is True
    assert "NOT in this read scope" in caplog.text


def test_folder_name_read_keeps_a_bucket_placed_here(basename_env):
    """Control: a row under this repository keeps the bucket."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[repo / "src.py", other / "src.py"])
    assert _basename_memory(store)._read_project_scopes() == (NAME,)


def test_folder_name_read_keeps_a_bucket_whose_paths_are_gone(basename_env):
    """A row naming a path that no longer exists is no evidence: a moved
    no-origin repository keeps its own bucket."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[other.parent / "moved-away" / "src.py"])
    assert _basename_memory(store)._read_project_scopes() == (NAME,)


def test_folder_name_save_into_another_checkouts_bucket_is_refused(basename_env):
    repo, other, store = basename_env
    _seed(store, NAME, files=[other / "src.py"])
    memory = _basename_memory(store)
    with pytest.raises(ValueError, match="another checkout"):
        memory.save({"context": "new"})
    with sqlite3.connect(str(store)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_folder_name_save_into_its_own_bucket_is_not_refused(basename_env):
    """Control: the write arm refuses only the excluded bucket."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[repo / "src.py"])
    assert _basename_memory(store)._project_id_for_write() == NAME

