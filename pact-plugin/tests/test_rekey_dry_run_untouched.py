"""
Location: pact-plugin/tests/test_rekey_dry_run_untouched.py
Summary: `project-id rekey` without --apply leaves a settled WAL store's file
byte-for-byte unchanged and creates no `-wal` or `-shm` beside it, while
reporting the same counters an apply then acts on. The plan runs on a copy in
a temporary folder (a file, not memory), removed afterwards.
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


def test_a_dry_run_plans_on_a_temporary_file_copy_it_removes(tmp_path, monkeypatch, capfd):
    """The dry run's copy is a file in the system temp folder, not an
    in-memory database, and nothing of it is left behind; the store's bytes
    are unchanged. RED WHEN the copy is held in memory, or left on disk."""
    import hashlib
    import tempfile

    from scripts import database

    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    _settled_wal_store(store, repo)
    digest = hashlib.sha256(store.read_bytes()).hexdigest()
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    seen = []
    real = database.rekey_project

    def spy(conn, *args, **kwargs):
        seen.append(conn.execute("PRAGMA database_list").fetchone()[2])
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(database, "rekey_project", spy)
    monkeypatch.chdir(repo)
    code, envelope = _cli(
        ["project-id", "rekey", "--from=proj", "--to=github.com/me/proj", "--db-path", str(store)],
        capfd,
    )
    assert code == 0, envelope
    assert envelope["result"]["memories"] == 1
    assert len(seen) == 1 and seen[0].startswith(str(temp_root)), seen
    assert list(temp_root.iterdir()) == [], list(temp_root.iterdir())
    assert hashlib.sha256(store.read_bytes()).hexdigest() == digest


def test_the_rekey_help_says_the_dry_run_uses_a_copy():
    """RED WHEN the help says the dry run runs inside a rolled-back savepoint
    (the library's mechanism) rather than on a copy (the CLI's)."""
    import argparse

    from scripts.cli import build_parser

    rekey = None
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            project_id = action.choices["project-id"]
            for inner in project_id._actions:
                if isinstance(inner, argparse._SubParsersAction):
                    rekey = inner.choices["rekey"]
    text = " ".join(rekey.format_help().split())
    assert "on a temporary copy of the store, which is then deleted" in text
    assert "savepoint" not in text
