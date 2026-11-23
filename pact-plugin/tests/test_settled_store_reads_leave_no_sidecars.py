"""
Location: pact-plugin/tests/test_settled_store_reads_leave_no_sidecars.py
Summary: `project-id show` and `sync` read a settled WAL store (no
`memory.db-wal` beside it) through an immutable read-only open, so they leave
no `-wal` or `-shm` behind and the store's bytes unchanged, and they still
read every row: show counts both buckets and sync projects every row of the
read scope.
Used by: the test suite; no production code imports it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from helpers import create_test_schema

REMOTE = "https://github.com/o/proj.git"
CANONICAL = "github.com/o/proj"
STAMP = "2026-11-01T00:00:00+00:00"


@pytest.fixture
def settled(monkeypatch, tmp_path):
    """A checkout and a WAL store holding two canonical rows and one legacy
    row, closed so that no `-wal` or `-shm` remains."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote=REMOTE)
    (repo / "CLAUDE.md").write_text("# P\n\n## Working Memory\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    store = tmp_path / "db" / "store.db"
    store.parent.mkdir()
    conn = sqlite3.connect(str(store))
    create_test_schema(conn)
    for number, project_id in enumerate((CANONICAL, CANONICAL, "proj")):
        conn.execute(
            "INSERT INTO memories (id, context, goal, project_id, created_at, updated_at) "
            "VALUES (?, 'ctx', 'g', ?, ?, ?)",
            (f"{number:032x}", project_id, STAMP, STAMP),
        )
    conn.commit()
    assert conn.execute("PRAGMA journal_mode=wal").fetchone()[0] == "wal"
    conn.close()
    # PRECONDITION, not the subject: the store is WAL and settled.
    assert _sidecars(store) == [], _sidecars(store)
    return repo, store


def _sidecars(store: Path) -> list:
    return sorted(p.name for p in store.parent.iterdir() if p.name != store.name)


def _digest(store: Path) -> str:
    return hashlib.sha256(store.read_bytes()).hexdigest()


def _cli(argv, capfd):
    from scripts.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    assert exit_info.value.code == 0, (captured.out, captured.err)
    return json.loads(captured.out)["result"]


def test_show_leaves_no_sidecar_and_counts_every_row(settled, capfd):
    """RED WHEN show's read-only open of a store with no `-wal` is not
    immutable: SQLite then leaves a 32 KiB `-shm` and an empty `-wal`."""
    _repo, store = settled
    before = _digest(store)
    result = _cli(["project-id", "show", "--db-path", str(store)], capfd)
    assert _sidecars(store) == [], _sidecars(store)
    assert _digest(store) == before
    assert result["legacy_scope"]["canonical_count"] == 2, result
    assert result["legacy_scope"]["memory_count"] == 1, result


def test_sync_leaves_no_sidecar_and_projects_every_row(settled, capfd):
    """RED WHEN sync's read of a store with no `-wal` is not immutable."""
    repo, store = settled
    before = _digest(store)
    result = _cli(["sync", "--claude-md-root", str(repo), "--db-path", str(store)], capfd)
    assert _sidecars(store) == [], _sidecars(store)
    assert _digest(store) == before
    assert result["sync_status"] == "wrote", result
    assert sorted(result["memory_ids"]) == [f"{n:032x}" for n in (0, 1)], result


def test_a_store_with_a_wal_is_read_with_locking(settled, capfd):
    """The control: with a `-wal` present (a writer holds the store open)
    the open is not immutable, and a commit that sits only in the `-wal` is
    read. RED WHEN the immutable open is used with a `-wal` present: SQLite
    then ignores the journal and the uncheckpointed row is missed."""
    _repo, store = settled
    writer = sqlite3.connect(str(store))
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO memories (id, context, goal, project_id, created_at, updated_at) "
            "VALUES (?, 'ctx', 'g', ?, ?, ?)",
            (f"{9:032x}", CANONICAL, STAMP, STAMP),
        )
        writer.commit()
        assert Path(str(store) + "-wal").stat().st_size > 0
        result = _cli(["project-id", "show", "--db-path", str(store)], capfd)
        assert result["legacy_scope"]["canonical_count"] == 3, result
    finally:
        writer.close()
