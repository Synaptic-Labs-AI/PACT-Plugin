"""
Location: pact-plugin/tests/test_rekey_dry_run_untouched.py
Summary: `project-id rekey` without --apply leaves a settled WAL store's file
byte-for-byte unchanged and creates no `-wal` or `-shm` beside it, while
reporting the same counters an apply then acts on.
Used by: pytest; runs the CLI in process against a real repository and store.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from helpers import create_test_schema

NOW = "2026-01-01T00:00:00+00:00"


def _cli(argv, capfd):
    from scripts.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    stream = captured.out if exit_info.value.code == 0 else captured.err
    return exit_info.value.code, json.loads(stream)


def _settled_wal_store(path, repo):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    create_test_schema(conn)
    conn.execute(
        "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
        "VALUES ('m1', 'x', 'proj', ?, ?)",
        (NOW, NOW),
    )
    conn.execute("INSERT INTO files (id, path, project_id) VALUES ('f1', ?, 'proj')", (str(repo / "a.py"),))
    conn.commit()
    conn.close()


def test_a_dry_run_leaves_a_wal_store_untouched(tmp_path, monkeypatch, capfd):
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    _settled_wal_store(store, repo)
    assert sorted(p.name for p in tmp_path.glob("mem.db*")) == ["mem.db"], "precondition: settled"
    before = store.read_bytes()
    monkeypatch.chdir(repo)
    argv = ["project-id", "rekey", "--from=proj", "--to=github.com/me/proj", "--db-path", str(store)]
    code, envelope = _cli(argv, capfd)
    assert code == 0, envelope
    assert envelope["result"]["dry_run"] is True
    assert envelope["result"]["memories"] == 1
    assert store.read_bytes() == before, "the dry run wrote the store file"
    assert sorted(p.name for p in tmp_path.glob("mem.db*")) == ["mem.db"]

    code, applied = _cli([*argv, "--apply"], capfd)
    assert code == 0, applied
    assert applied["result"]["memories"] == 1
    conn = sqlite3.connect(str(store))
    assert conn.execute("SELECT project_id FROM memories WHERE id = 'm1'").fetchone()[0] == "github.com/me/proj"
    conn.close()
