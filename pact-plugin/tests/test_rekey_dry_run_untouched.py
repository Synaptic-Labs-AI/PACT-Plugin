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


def _store_of(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    create_test_schema(conn)
    for index in range(rows):
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES (?, ?, 'proj', ?, ?)",
            (f"m{index}", "x" * 2000, NOW, NOW),
        )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("shape", ["missing", "read_only", "full"])
def test_a_dry_run_whose_temporary_folder_fails_is_a_named_fault(tmp_path, monkeypatch, capfd, shape):
    """An unusable temporary folder (absent, not writable, or out of room)
    fails the dry run as REKEY_FAILED, exit 3, naming the folder; the store's
    bytes are unchanged, no -wal or -shm appears beside it, and no copy is
    left. RED WHEN the stderr guard refuses the command for want of its own
    temporary file (SYSTEM_ERROR), when the copy's fault is not named, or when
    it is read as a moved store and repeated with locking (sidecars)."""
    import os
    import subprocess
    import sys
    import tempfile

    if shape == "read_only" and os.geteuid() == 0:
        pytest.skip("root writes into a read-only folder")
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    _store_of(store, 300)
    before = store.read_bytes()
    folder = tmp_path / "tmp"
    if shape != "missing":
        folder.mkdir()
    argv = ["project-id", "rekey", "--from=proj", "--to=github.com/me/proj", "--db-path", str(store)]
    if shape == "full":
        # A file-size limit stands in for a full disk: the copy's writes fail
        # with an I/O error, as they do on a filesystem out of room.
        plugin = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.join(plugin, "skills", "pact-memory"), os.path.join(plugin, "hooks")]
        )
        script = (
            "import resource, signal, sys, tempfile\n"
            "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
            "resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))\n"
            "tempfile.tempdir = sys.argv[1]\n"
            "from scripts.cli import main\n"
            "main(sys.argv[2:])\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(folder), *argv],
            capture_output=True, text=True, env=env, cwd=repo,
        )
        code, envelope = result.returncode, json.loads(result.stderr.strip().splitlines()[-1])
    else:
        monkeypatch.chdir(repo)
        if shape == "read_only":
            folder.chmod(0o500)
        saved = tempfile.tempdir
        tempfile.tempdir = str(folder)
        try:
            code, envelope = _cli(argv, capfd)
        finally:
            tempfile.tempdir = saved
            if shape == "read_only":
                folder.chmod(0o700)
    assert code == 3, envelope
    assert envelope["error"] == "REKEY_FAILED", envelope
    assert "temporary copy of the store in " + str(folder) in envelope["message"], envelope
    assert store.read_bytes() == before, "the failed dry run wrote the store file"
    assert sorted(p.name for p in tmp_path.glob("mem.db*")) == ["mem.db"]
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_dry_run_with_no_usable_temporary_folder_is_a_named_fault(tmp_path, monkeypatch, capfd):
    """Python's only candidate folder is read-only, so it finds no usable
    temporary folder at all. The dry run still fails as REKEY_FAILED, exit
    3, saying so, and leaves the store as it was. RED WHEN the failure
    handler asks Python for the folder again (that raises inside the
    handler and the command ends as SYSTEM_ERROR)."""
    import os
    import tempfile

    if os.geteuid() == 0:
        pytest.skip("root writes into a read-only folder")
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    _store_of(store, 3)
    before = store.read_bytes()
    only = tmp_path / "only"
    only.mkdir()
    monkeypatch.chdir(repo)
    argv = ["project-id", "rekey", "--from=proj", "--to=github.com/me/proj", "--db-path", str(store)]
    saved = tempfile.tempdir
    candidates = tempfile._candidate_tempdir_list
    only.chmod(0o500)
    tempfile.tempdir = None
    tempfile._candidate_tempdir_list = lambda: [str(only)]
    try:
        code, envelope = _cli(argv, capfd)
    finally:
        tempfile._candidate_tempdir_list = candidates
        tempfile.tempdir = saved
        only.chmod(0o700)
    assert code == 3, envelope
    assert envelope["error"] == "REKEY_FAILED", envelope
    assert "no usable temporary folder" in envelope["message"], envelope
    assert store.read_bytes() == before
    assert sorted(p.name for p in tmp_path.glob("mem.db*")) == ["mem.db"]


class _FailingBackup:
    """A source connection whose backup fails with a disk I/O error; with
    `store_fails` its every other read fails the same way (a failing disk
    under the store), otherwise it reads through `real`."""

    def __init__(self, real, store_fails):
        self._real = real
        self._store_fails = store_fails

    def backup(self, target, **kwargs):
        from scripts.database import sqlite3 as db_sqlite3

        raise db_sqlite3.OperationalError("disk I/O error")

    def execute(self, *args, **kwargs):
        from scripts.database import sqlite3 as db_sqlite3

        if self._store_fails:
            raise db_sqlite3.OperationalError("disk I/O error")
        return self._real.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        self._real.close()


@pytest.mark.parametrize("side", ["store", "copy"])
def test_a_disk_fault_during_the_copy_is_blamed_on_the_side_that_failed(
    tmp_path, monkeypatch, capfd, side
):
    """The same "disk I/O error" comes from a failing disk under the store
    and from a temporary folder out of room. Only a store that still reads
    whole sends the user to TMPDIR; a store that does not is reported as a
    store fault. RED WHEN every such error blames the copy (store arm) or
    every one blames the store (copy arm), or when the store-side fault is
    retried with locking (leaving -wal/-shm) or does not name the store."""
    from scripts import cli

    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    _store_of(store, 3)
    before = store.read_bytes()
    real_open = cli._read_only_connection
    monkeypatch.setattr(
        cli, "_read_only_connection",
        lambda path: _FailingBackup(real_open(path), side == "store"),
    )
    monkeypatch.chdir(repo)
    argv = ["project-id", "rekey", "--from=proj", "--to=github.com/me/proj", "--db-path", str(store)]
    code, envelope = _cli(argv, capfd)
    assert code == 3, envelope
    assert envelope["error"] == "REKEY_FAILED", envelope
    if side == "store":
        assert "temporary copy" not in envelope["message"], envelope
        assert f"the store {store} failed an integrity check" in envelope["message"], envelope
    else:
        assert "temporary copy of the store in " in envelope["message"], envelope
    assert store.read_bytes() == before
    assert sorted(p.name for p in tmp_path.glob("mem.db*")) == ["mem.db"], "sidecars beside the store"
