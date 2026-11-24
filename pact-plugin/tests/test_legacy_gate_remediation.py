"""Legacy-gate remediation arms for the memory store's read scope, its
session evidence and the prune ledger.

Each arm names the defect it catches and fails on the code before its fix.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    make_worktree,
    run_git,
    seed_session_evidence,
)
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory
from shared import pact_context

import session_end
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


# ---------------------------------------------------------------------------
# C-3: a removed linked worktree of this repository is not "elsewhere"
# ---------------------------------------------------------------------------


def test_removed_worktree_session_is_unattributed_through_removal_and_reap(
    tmp_path, monkeypatch
):
    """Present: the worktree's session counts here. Removed: it places the
    rows nowhere (never elsewhere). Reaped: the reaper writes no ledger line
    for a vanished directory, so it stays unattributed: no in/out/in."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME)
    worktree = make_worktree(repo, tmp_path / "wt")
    config = tmp_path / "home" / ".claude"
    context = seed_session_evidence(config, worktree, "wts")
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="wts")
    roots = [str(repo.resolve()), str(worktree.resolve())]
    common = (repo / ".git").resolve()

    def census():
        with sqlite3.connect(str(store)) as conn:
            return memory_api._legacy_session_census(conn, NAME, roots, common)

    assert census()["here"] == 1
    run_git("worktree", "remove", "--force", str(worktree), cwd=repo)
    removed = census()
    assert removed["elsewhere"] == 0 and removed["unattributed"] == 1, removed
    assert session_end._pruned_session_line(context.parent) is None
    reaped_folder = context.parent
    for child in reaped_folder.iterdir():
        child.unlink()
    reaped_folder.rmdir()
    reaped = census()
    assert reaped["elsewhere"] == 0 and reaped["unattributed"] == 1, reaped


def test_an_existing_directory_outside_the_roots_is_still_elsewhere(tmp_path):
    """Control: the vanished-directory rule does not blunt the foreign arm."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME)
    other = make_repo(tmp_path / "other" / NAME)
    config = tmp_path / "home" / ".claude"
    seed_session_evidence(config, other, "far")
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="far")
    with sqlite3.connect(str(store)) as conn:
        counts = memory_api._legacy_session_census(
            conn, NAME, [str(repo.resolve())], (repo / ".git").resolve()
        )
    assert counts["elsewhere"] == 1, counts


# ---------------------------------------------------------------------------
# BR-4: the session record is read bounded, regular files only
# ---------------------------------------------------------------------------


def test_session_record_read_refuses_a_fifo_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pact-session-context.json")
    started = time.monotonic()
    assert pact_context.read_session_record(tmp_path) is None
    assert time.monotonic() - started < 5


def test_session_record_read_refuses_an_oversized_file(tmp_path):
    record = {"session_id": "s", "pad": "x" * pact_context.SESSION_CONTEXT_MAX_BYTES}
    (tmp_path / "pact-session-context.json").write_text(json.dumps(record))
    assert pact_context.read_session_record(tmp_path) is None


def test_session_record_read_refuses_a_symlink_and_reads_a_regular_file(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"session_id": "s"}))
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "pact-session-context.json").symlink_to(real)
    assert pact_context.read_session_record(linked) is None
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pact-session-context.json").write_text(json.dumps({"session_id": "s"}))
    assert pact_context.read_session_record(plain) == {"session_id": "s"}


# ---------------------------------------------------------------------------
# BR-5 / C-2: a window that starts on a line boundary keeps its first line
# ---------------------------------------------------------------------------


def _lines(count: int) -> bytes:
    return b"".join(
        json.dumps({"session_id": f"s{index}"}).encode() + b"\n"
        for index in range(count)
    )


def test_ledger_reader_keeps_a_whole_first_line_on_a_boundary(tmp_path, monkeypatch):
    data = _lines(4)
    line = len(data) // 4
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(data)
    monkeypatch.setattr(memory_api, "_PRUNE_LEDGER_TAIL_BYTES", 2 * line)
    ids = [r["session_id"] for r in memory_api._pruned_session_records(ledger)]
    assert ids == ["s2", "s3"]


def test_ledger_reader_drops_a_partial_first_line(tmp_path, monkeypatch):
    """Control: a window starting mid-line still drops the partial line."""
    data = _lines(4)
    line = len(data) // 4
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(data)
    monkeypatch.setattr(memory_api, "_PRUNE_LEDGER_TAIL_BYTES", 2 * line + 3)
    ids = [r["session_id"] for r in memory_api._pruned_session_records(ledger)]
    assert ids == ["s2", "s3"]


def test_ledger_compaction_keeps_a_whole_first_line_on_a_boundary(
    tmp_path, monkeypatch
):
    data = _lines(4)
    line = len(data) // 4
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(data)
    monkeypatch.setattr(session_end, "_PRUNE_LEDGER_KEEP_BYTES", 2 * line)
    fd = os.open(ledger, os.O_RDWR)
    try:
        session_end._compact_ledger(ledger, fd, len(data))
    finally:
        os.close(fd)
    assert ledger.read_bytes() == data[2 * line:]


# ---------------------------------------------------------------------------
# BR-6: a held ledger lock costs the line, never a hung hook
# ---------------------------------------------------------------------------


def test_held_ledger_lock_skips_the_line_within_the_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(session_end, "_LEDGER_LOCK_ATTEMPTS", 3)
    monkeypatch.setattr(session_end, "_LEDGER_LOCK_PAUSE_SECONDS", 0.01)
    ledger = tmp_path / pact_context.PRUNE_LEDGER_NAME
    ledger.write_bytes(b"")
    holder = os.open(ledger, os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    # In a thread, so an unbounded lock fails this test instead of hanging it.
    append = threading.Thread(
        target=session_end._append_ledger_line,
        args=(tmp_path, '{"session_id": "x"}\n'),
    )
    try:
        append.start()
        append.join(5)
        assert not append.is_alive(), "the ledger append waited on the lock"
    finally:
        os.close(holder)
        append.join()
    assert ledger.read_bytes() == b""
    session_end._append_ledger_line(tmp_path, '{"session_id": "x"}\n')
    assert ledger.read_bytes() == b'{"session_id": "x"}\n'


# ---------------------------------------------------------------------------
# C-8: a hot rollback journal blocks the immutable open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_a_journal_beside_the_store_blocks_the_immutable_open(
    tmp_path, monkeypatch, suffix
):
    store = tmp_path / "store.db"
    with sqlite3.connect(str(store)) as conn:
        conn.execute("CREATE TABLE t (x)")
    opened = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append(database)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(memory_api.sqlite3, "connect", spy)
    memory_api._read_only_connection(str(store)).close()
    assert "immutable=1" in opened[-1]
    stamp_without = memory_api._store_stamp(str(store))
    Path(str(store) + suffix).write_bytes(b"")
    memory_api._read_only_connection(str(store)).close()
    assert "immutable=1" not in opened[-1]
    assert memory_api._store_stamp(str(store)) != stamp_without
