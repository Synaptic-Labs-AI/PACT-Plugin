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
    same-named project folder elsewhere (non-git, marked by `.claude`)."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "mine" / NAME)
    other = tmp_path / "theirs" / NAME
    (other / ".claude").mkdir(parents=True)
    (other / "src.py").write_text("x\n")
    (repo / "src.py").write_text("x\n")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return repo, other, tmp_path / "store.db"


def _evidence(store: Path, bucket: str, roots) -> dict:
    """`bucket_evidence` for `bucket` against `roots`."""
    from scripts.database import bucket_evidence

    with sqlite3.connect(str(store)) as conn:
        conn.row_factory = sqlite3.Row
        return bucket_evidence(
            conn, bucket, [str(Path(root).resolve()) for root in roots],
            recorded_dirs=memory_api._recorded_project_dirs,
        )


def _basename_memory(store: Path) -> PACTMemory:
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id_shape == "basename", memory._project_id
    assert memory._project_id == NAME
    return memory


def test_folder_name_read_keeps_a_bucket_placed_here(basename_env):
    """Control: a row under this repository keeps the bucket."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[repo / "src.py"])
    assert _basename_memory(store)._read_project_scopes() == (NAME,)


def test_folder_name_read_keeps_a_bucket_whose_paths_are_gone(basename_env):
    """A row naming a path that no longer exists is no evidence: a moved
    no-origin repository keeps its own bucket."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[other.parent / "moved-away" / "src.py"])
    assert _basename_memory(store)._read_project_scopes() == (NAME,)


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


def test_ledger_compaction_keeps_no_partial_line_when_the_window_has_no_newline(
    tmp_path, monkeypatch
):
    """The window lies inside one line (no newline in it): it keeps nothing.
    RED WHEN a find() of -1 keeps the whole window, a partial line."""
    data = _lines(2) + b"y" * 50
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(data)
    monkeypatch.setattr(session_end, "_PRUNE_LEDGER_KEEP_BYTES", 20)
    fd = os.open(ledger, os.O_RDWR)
    try:
        session_end._compact_ledger(ledger, fd, len(data))
    finally:
        os.close(fd)
    assert ledger.read_bytes() == b""


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


# ---------------------------------------------------------------------------
# CB-1: a bucket shared with another checkout is not admitted whole
# ---------------------------------------------------------------------------

ORIGIN = "https://github.com/Acme/Widget.git"
CANONICAL = "github.com/acme/widget"


@pytest.fixture
def shared_bucket(tmp_path, monkeypatch, scopes_clean):
    """The cold probe: `widget` has an origin, `other/widget` has none, and
    the `widget` folder-name bucket holds one row from each."""
    assert_outside_repository(tmp_path)
    mine = make_repo(tmp_path / NAME, remote=ORIGIN)
    theirs = make_repo(tmp_path / "other" / NAME)
    for repo in (mine, theirs):
        (repo / "src.py").write_text("x\n")
    store = tmp_path / "store.db"
    _seed(store, NAME, files=[theirs / "src.py", mine / "src.py"])
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    memory_api._UNPROVEN_WARNED.clear()
    yield mine, theirs, store
    memory_api._UNPROVEN_WARNED.clear()


def test_a_bucket_with_a_foreign_row_is_not_read_whole(shared_bucket, monkeypatch, caplog):
    mine, theirs, store = shared_bucket
    monkeypatch.chdir(mine)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id == CANONICAL
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == (CANONICAL,)
    state = memory._legacy_scope_state
    assert state["unproven"] and not state["verified"], state
    assert state["unproven_reason"] == "foreign_rows"
    assert state["detail"]["foreign_rows"] == 1
    assert state["detail"]["contained_rows"] == 1
    assert "1 of its file rows lie in another checkout" in caplog.text


def test_a_defaulted_rekey_refuses_the_shared_bucket(shared_bucket, monkeypatch, capfd):
    from scripts.cli import main

    mine, theirs, store = shared_bucket
    monkeypatch.chdir(mine)
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", f"--from={NAME}", "--db-path", str(store)])
    envelope = json.loads(capfd.readouterr().err)
    assert exit_info.value.code == 1, envelope
    assert envelope["error"] == "REKEY_REFUSED"
    assert "1 of its file rows lie in another checkout" in envelope["message"]
    memory_api._LEGACY_SCOPE_CACHE.clear()
    with pytest.raises(SystemExit) as exit_info:
        main([
            "project-id", "rekey", f"--from={NAME}", f"--to={CANONICAL}",
            "--db-path", str(store),
        ])
    assert exit_info.value.code == 0, capfd.readouterr()


def test_a_foreign_row_whose_path_is_gone_is_no_evidence(shared_bucket, monkeypatch):
    """Control: the vanished row leaves the bucket verified and read."""
    mine, theirs, store = shared_bucket
    (theirs / "src.py").unlink()
    monkeypatch.chdir(mine)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._read_project_scopes() == (CANONICAL, NAME)
    assert memory._legacy_scope_state["verified"] is True


# ---------------------------------------------------------------------------
# CS-10 / CS-11: the folder-name save refusal is a refusal with a usable remedy
# ---------------------------------------------------------------------------


def test_the_gate_calls_a_shared_bucket_unverified(shared_bucket):
    """The gate itself: a row here plus an existing row in another checkout is
    not `verified`."""
    from scripts.database import bucket_evidence

    mine, theirs, store = shared_bucket
    with sqlite3.connect(str(store)) as conn:
        conn.row_factory = sqlite3.Row
        detail = bucket_evidence(conn, NAME, [str(mine.resolve())])
    assert detail["foreign_rows"] == 1 and detail["contained_rows"] == 1, detail
    verdict = memory_api.decide_bucket(detail, None)
    assert verdict.state == "unproven" and not verdict.read, verdict


def test_session_evidence_does_not_admit_a_shared_bucket(
    shared_bucket, monkeypatch, tmp_path
):
    """Admission: a session recorded here would admit an unverified bucket,
    but a foreign file row still keeps it out."""
    mine, theirs, store = shared_bucket
    seed_session_evidence(tmp_path / "home" / ".claude", mine, "s")
    monkeypatch.chdir(mine)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._read_project_scopes() == (CANONICAL,)
    assert memory._legacy_scope_state["unproven_reason"] == "foreign_rows"
    # The census is skipped once a foreign file row decides.
    assert memory._legacy_scope_state["sessions"] is None



# ---------------------------------------------------------------------------
# Ownership rule: the two foreign-row rules and the census enclosing folder
# (the matrix, S1-S3 and R1-R4 live in test_legacy_ownership_matrix.py)
# ---------------------------------------------------------------------------

def test_an_enclosing_checkout_is_not_a_rival(tmp_path, monkeypatch, scopes_clean):
    """Rule (a): a `.git` above both the row and the root (`$HOME` dotfiles)
    is this repository's surroundings, so its rows are neutral."""
    assert_outside_repository(tmp_path)
    home = make_repo(tmp_path / "home-repo")
    root = make_repo(home / "code" / NAME, remote=ORIGIN)
    (home / "notes").mkdir()
    (home / "notes" / "n.md").write_text("x\n")
    (root / "src.py").write_text("x\n")
    store = tmp_path / "store.db"
    _seed(store, NAME, files=[home / "notes" / "n.md", root / "src.py"])
    monkeypatch.chdir(root)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._read_project_scopes() == (CANONICAL, NAME)
    assert memory._legacy_scope_state["detail"]["neutral_outside"] == 1


def test_an_unmarked_folder_named_like_the_bucket_is_neutral(basename_env):
    """Rule (b) needs a marker: a bare `~/.config/<B>`-like folder is neutral."""
    repo, other, store = basename_env
    (other / ".claude").rmdir()
    _seed(store, NAME, files=[other / "src.py"])
    evidence = _evidence(store, NAME, [repo])
    assert evidence["foreign_rows"] == 0 and evidence["neutral_outside"] == 1, evidence


def test_a_file_named_like_the_bucket_is_never_a_rival_folder(basename_env):
    """Rule (b) walks strict ancestors only: a tracked FILE named like the
    bucket, even in a marked folder, is neutral."""
    repo, other, store = basename_env
    notes = other.parent / "notes"
    (notes / ".claude").mkdir(parents=True)
    (notes / NAME).write_text("x\n")
    _seed(store, NAME, files=[notes / NAME])
    assert _evidence(store, NAME, [repo])["foreign_rows"] == 0


def test_a_session_folder_enclosing_the_root_is_neutral(tmp_path):
    """Census: a session launched in a folder that encloses the root (`$HOME`)
    is not `elsewhere`."""
    assert_outside_repository(tmp_path)
    root = make_repo(tmp_path / "outer" / NAME)
    seed_session_evidence(tmp_path / "home" / ".claude", tmp_path / "outer", "up")
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="up")
    with sqlite3.connect(str(store)) as conn:
        counts = memory_api._legacy_session_census(
            conn, NAME, [str(root.resolve())], (root / ".git").resolve()
        )
    assert counts["elsewhere"] == 0 and counts["unattributed"] == 1, counts




# ---------------------------------------------------------------------------
# Amendment 2: pre-change names, the gated sources, the census subfolder rule
# and the re-decide on a moved store
# ---------------------------------------------------------------------------


def test_the_pre_change_name_follows_the_old_resolver(tmp_path):
    """A checkout's name, a linked worktree's main name, a separate git dir's
    parent name, a submodule's none, and an unreadable `.git` file unknown."""
    import subprocess

    from scripts.database import UNKNOWN_NAME, pre_change_name

    assert_outside_repository(tmp_path)
    main = make_repo(tmp_path / "proj")
    worktree = make_worktree(main, tmp_path / "proj-feat")
    plain = tmp_path / "plain" / "folder"
    plain.mkdir(parents=True)
    (tmp_path / "gitdirs").mkdir()
    sep = tmp_path / "sep"
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(tmp_path / "gitdirs" / "store"), str(sep)],
        check=True,
    )
    broken = tmp_path / "broken"
    broken.mkdir()
    os.mkfifo(broken / ".git")
    assert pre_change_name(str(main)) == "proj"
    assert pre_change_name(str(worktree.resolve())) == "proj"
    assert pre_change_name(str(plain)) == "folder"
    assert pre_change_name(str(sep)) == "gitdirs"
    assert pre_change_name(str(broken)) is UNKNOWN_NAME
    source = make_repo(tmp_path / "subsrc")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
        str(source), "vendor/lib", cwd=main,
    )
    assert pre_change_name(str(main / "vendor" / "lib")) is None


@pytest.mark.parametrize(
    "source, landing, gated",
    [
        ("git root", False, True),
        ("cwd", False, True),
        ("user setting", False, False),
        ("supplied", False, False),
        ("cwd", True, False),
    ],
)
def test_only_user_chosen_sources_are_ungated(source, landing, gated):
    memory = PACTMemory.__new__(PACTMemory)
    memory._project_id_source = source
    memory._project_home_landing = landing
    assert memory._folder_name_gated() is gated


def test_a_non_git_roots_own_subfolder_session_is_a_subfolder(tmp_path):
    """Census: a non-git project folder never makes its own subfolders
    "elsewhere"; a separate checkout nested inside it still is."""
    assert_outside_repository(tmp_path)
    root = tmp_path / NAME
    (root / "sub").mkdir(parents=True)
    nested = make_repo(root / "vendor" / "other")
    config = tmp_path / "home" / ".claude"
    seed_session_evidence(config, root / "sub", "sub-session")
    seed_session_evidence(config, nested, "nested-session")
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="sub-session")
    _seed(store, NAME, files=["rel.py"], session_id="nested-session")
    with sqlite3.connect(str(store)) as conn:
        counts = memory_api._legacy_session_census(conn, NAME, [str(root.resolve())], None)
    assert counts["subfolder"] == 1 and counts["elsewhere"] == 1, counts


def test_a_tracked_directory_named_like_the_bucket_is_not_its_own_rival(basename_env):
    """Rule (b) starts at the row's parent: a row that IS a marked directory
    named like the bucket does not make itself a rival folder."""
    repo, other, store = basename_env
    tracked = other.parent / "tracked" / NAME
    (tracked / ".claude").mkdir(parents=True)
    _seed(store, NAME, files=[tracked])
    assert _evidence(store, NAME, [repo])["foreign_rows"] == 0


# ---------------------------------------------------------------------------
# Cycle 5: a separate-git-dir checkout's old bucket
# ---------------------------------------------------------------------------


@pytest.fixture
def separate_git_dir(tmp_path, monkeypatch, scopes_clean):
    """Cold S1: `git init --separate-git-dir=store/proj.git work/proj`, no
    remote. The old resolver named it `store`; the new id is `proj`."""
    import subprocess

    assert_outside_repository(tmp_path)
    (tmp_path / "store").mkdir()
    (tmp_path / "work").mkdir()
    work = tmp_path / "work" / "proj"
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(tmp_path / "store" / "proj.git"), str(work)],
        check=True,
    )
    db = tmp_path / "memory.db"
    _seed(db, "store", session_id="unrecorded")
    monkeypatch.chdir(work)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    memory_api._UNPROVEN_WARNED.clear()
    return work, db


def test_a_separate_git_dir_checkout_discloses_its_old_bucket(separate_git_dir, caplog):
    work, db = separate_git_dir
    memory = PACTMemory(session_id="s", db_path=db)
    assert memory._project_id == "proj" and memory._legacy_project_basename == "store"
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == ("proj",)
    state = memory._legacy_scope_state
    assert state is not None and state["candidate"] == "store", state
    assert state["unproven"] and state["unproven_reason"] == "no_session", state
    assert state["memory_count"] == 1
    assert "--from=store" in caplog.text and "--to=proj" in caplog.text


def test_a_separate_git_dir_checkout_can_move_its_old_bucket(separate_git_dir, capfd):
    from scripts.cli import main

    work, db = separate_git_dir
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", "--from=store", "--db-path", str(db)])
    refused = json.loads(capfd.readouterr().err)
    # Its own folder-name id is a target only when named explicitly.
    assert exit_info.value.code == 1 and refused["error"] == "PROJECT_IDENTITY_UNRESOLVED", refused
    memory_api._LEGACY_SCOPE_CACHE.clear()
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", "--from=store", "--to=proj", "--apply", "--db-path", str(db)])
    captured = capfd.readouterr()
    assert exit_info.value.code == 0, captured.err
    moved = json.loads(captured.out)
    assert moved["result"]["memories"] == 1
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT project_id FROM memories"
        ).fetchall() == [("proj",)]


def test_another_folder_name_target_is_still_refused(separate_git_dir, capfd):
    """Control: only the session's own folder-name id is a target."""
    from scripts.cli import main

    work, db = separate_git_dir
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", "--from=store", "--to=other", "--apply", "--db-path", str(db)])
    assert exit_info.value.code == 2, capfd.readouterr()



def test_only_the_old_bucket_moves_into_the_own_folder_name_id(separate_git_dir, capfd):
    """Control: a bucket that is not this session's legacy candidate cannot
    be moved into its folder-name id."""
    from scripts.cli import main

    work, db = separate_git_dir
    _seed(db, "unrelated", session_id="u2")
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", "--from=unrelated", "--to=proj", "--apply", "--db-path", str(db)])
    assert exit_info.value.code == 2, capfd.readouterr()





# ---------------------------------------------------------------------------
# Cycle 6: the scope docstring and the legacy-write refusal envelope
# ---------------------------------------------------------------------------


def test_the_scope_docstring_claims_no_wal_creation():
    doc = PACTMemory._read_project_scopes.__doc__
    assert "create it before stamping" not in doc
    assert "a read-only open is immutable" in doc


@pytest.fixture
def canonical_session(tmp_path, monkeypatch, scopes_clean):
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME, remote=ORIGIN)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return repo, tmp_path / "store.db"


def _cli_err(argv, capfd):
    from scripts.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    return exit_info.value.code, json.loads(capfd.readouterr().err)


def test_a_legacy_bucket_save_is_refused_with_its_own_envelope(canonical_session, capfd):
    repo, store = canonical_session
    _seed(store, CANONICAL)
    code, envelope = _cli_err(
        ["save", json.dumps({"context": "c", "project_id": "folder"}), "--db-path", str(store)],
        capfd,
    )
    assert code == 2, envelope
    assert envelope["error"] == "LEGACY_BUCKET_WRITE_REFUSED", envelope
    assert "allowed_fields" not in envelope and "Note:" not in envelope["message"]
    assert "does not write into a folder-name bucket" in envelope["message"]


def test_a_legacy_bucket_update_is_refused_with_its_own_envelope(canonical_session, capfd):
    repo, store = canonical_session
    _seed(store, CANONICAL)
    row_id = "a" * 32
    with sqlite3.connect(str(store)) as conn:
        conn.execute("UPDATE memories SET id = ?", (row_id,))
    code, envelope = _cli_err(
        ["update", row_id, json.dumps({"project_id": "folder"}), "--db-path", str(store)],
        capfd,
    )
    assert code == 2, envelope
    assert envelope["error"] == "LEGACY_BUCKET_WRITE_REFUSED", envelope
    assert "allowed_fields" not in envelope and "Note:" not in envelope["message"]


@pytest.fixture
def declined_session(tmp_path, monkeypatch, scopes_clean):
    """A checkout whose origin no id can represent: detection declines."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME, remote="https://example.invalid/acme/x.git")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return repo, tmp_path / "store.db"


def test_a_declined_session_save_into_a_folder_name_bucket_is_refused(declined_session, capfd):
    """RED WHEN only a canonical detection guards folder-name buckets: a
    declined checkout becomes a repository session once its origin is fixed,
    and its rows here would be that session's evidence."""
    _repo, store = declined_session
    _seed(store, CANONICAL)
    code, envelope = _cli_err(
        ["save", json.dumps({"context": "c", "project_id": "folder"}), "--db-path", str(store)],
        capfd,
    )
    assert code == 2, envelope
    assert envelope["error"] == "LEGACY_BUCKET_WRITE_REFUSED", envelope
    assert "does not write into a folder-name bucket" in envelope["message"]
    assert "identity declined (origin_unrepresentable)" in envelope["message"]
    assert "None" not in envelope["message"]


@pytest.mark.parametrize("change", [{"project_id": "folder"}, {"session_id": "other"}])
def test_a_declined_session_update_into_a_folder_name_bucket_is_refused(
    declined_session, capfd, change
):
    _repo, store = declined_session
    _seed(store, "folder")
    row_id = "a" * 32
    with sqlite3.connect(str(store)) as conn:
        conn.execute("UPDATE memories SET id = ?", (row_id,))
    code, envelope = _cli_err(
        ["update", row_id, json.dumps(change), "--db-path", str(store)], capfd
    )
    assert code == 2, envelope
    assert envelope["error"] == "LEGACY_BUCKET_WRITE_REFUSED", envelope


def test_a_stale_declaration_in_a_plain_folder_may_save_its_folder_name(
    tmp_path, monkeypatch, scopes_clean, capfd
):
    """CLAUDE_PROJECT_DIR names a deleted directory and the session sits in a
    plain (non-git) folder: no repository declined, so a save into that
    folder's own name is not refused. RED WHEN every named decline, a gone
    declaration included, guards folder-name buckets."""
    from scripts.cli import main

    assert_outside_repository(tmp_path)
    plain = tmp_path / "plainfolder"
    plain.mkdir()
    monkeypatch.chdir(plain)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "gone"))
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
    with pytest.raises(SystemExit) as exit_info:
        main(["save", "--no-sync", json.dumps({"context": "c", "project_id": "plainfolder"}),
              "--db-path", str(store)])
    assert exit_info.value.code in (0, None), capfd.readouterr()
    with sqlite3.connect(str(store)) as conn:
        assert conn.execute("SELECT project_id FROM memories").fetchall() == [("plainfolder",)]


@pytest.mark.parametrize("reason, guarded", [
    (None, False),  # the final unnamed decline: a deleted or nameless cwd
    ("declaration_unresolved", False),  # a declared directory that is gone
    ("origin_unrepresentable", True),  # a live repository that declined
])
def test_only_a_live_repository_decline_guards_folder_name_buckets(tmp_path, reason, guarded):
    memory = PACTMemory(project_id="x", session_id="s", db_path=tmp_path / "s.db")
    memory._project_id, memory._project_id_source = None, "unresolved"
    memory._decline_reason = None if reason is None else {"source": "cwd", "reason": reason}
    assert memory._guards_folder_name_buckets() is guarded


def test_a_declined_session_may_save_under_a_supplied_canonical_id(declined_session, capfd):
    from scripts.cli import main

    _repo, store = declined_session
    with db_connection(store) as conn:
        ensure_initialized(conn)
    with pytest.raises(SystemExit) as exit_info:
        main(["save", "--no-sync", json.dumps({"context": "c", "project_id": CANONICAL}),
              "--db-path", str(store)])
    assert exit_info.value.code in (0, None), capfd.readouterr()
    with sqlite3.connect(str(store)) as conn:
        assert conn.execute("SELECT project_id FROM memories").fetchall() == [(CANONICAL,)]



# ---------------------------------------------------------------------------
# Cycle 7: a bucket read on session evidence alone needs an explicit --to
# ---------------------------------------------------------------------------


def test_a_session_admitted_bucket_needs_an_explicit_target(canonical_session, tmp_path, capfd):
    """One session recorded here plus many rows no session places: the bucket
    is read (`included_unverified`), but a defaulted rekey would move every
    row on that one session's word, so it is refused; an explicit --to moves."""
    from scripts.cli import main

    repo, store = canonical_session
    seed_session_evidence(tmp_path / "home" / ".claude", repo, "here-session")
    _seed(store, NAME, session_id="here-session")
    for index in range(5):
        _seed(store, NAME, session_id=f"unrecorded-{index}")
    code, envelope = _cli_err(["project-id", "rekey", f"--from={NAME}", "--apply", "--db-path", str(store)], capfd)
    assert code == 1 and envelope["error"] == "REKEY_REFUSED", envelope
    assert "session evidence alone" in envelope["message"]
    memory_api._LEGACY_SCOPE_CACHE.clear()
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", f"--from={NAME}", f"--to={CANONICAL}", "--apply", "--db-path", str(store)])
    assert exit_info.value.code == 0, capfd.readouterr().err
    assert json.loads(capfd.readouterr().out)["result"]["memories"] == 6


def test_a_verified_bucket_still_takes_a_defaulted_target(canonical_session, capfd):
    """Control: file rows that place the bucket here keep the default."""
    from scripts.cli import main

    repo, store = canonical_session
    (repo / "a.py").write_text("x\n")
    _seed(store, NAME, files=[repo / "a.py"])
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", f"--from={NAME}", "--db-path", str(store)])
    assert exit_info.value.code == 0, capfd.readouterr().err



def test_a_defaulted_rekey_of_a_files_only_own_bucket_is_refused(canonical_session, capfd):
    """This checkout's own pre-upgrade bucket holding file rows but no
    memories has no legacy candidate (candidates are counted from memories),
    so nothing verified it. A defaulted --to must refuse rather than move
    those rows; with nothing at all left under it the re-run stays a no-op.
    RED WHEN the own-name branch moves unjudged file rows."""
    from scripts.cli import main

    repo, store = canonical_session
    _seed(store, CANONICAL)
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "rekey", f"--from={NAME}", "--apply", "--db-path", str(store)])
    assert exit_info.value.code == 0, capfd.readouterr().err
    capfd.readouterr()
    with db_connection(store) as conn:
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('fx', '/elsewhere/a.py', ?)", (NAME,))
        conn.commit()
    memory_api._LEGACY_SCOPE_CACHE.clear()
    code, envelope = _cli_err(
        ["project-id", "rekey", f"--from={NAME}", "--apply", "--db-path", str(store)], capfd
    )
    assert code == 1 and envelope["error"] == "REKEY_REFUSED", envelope
    assert "holds file rows but no memories" in envelope["message"], envelope
    with sqlite3.connect(str(store)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM files WHERE project_id = ?", (NAME,)).fetchone()[0] == 1


def test_an_applied_rekey_prints_no_advice_to_make_the_move_it_makes(canonical_session, capfd, caplog):
    """An unproven bucket's read notice says "NOT in this read scope ...
    preview the move with". A dry run keeps it; an applied move reads the
    scope quietly, so the advice never prints beside the move's own result.
    RED WHEN --apply reads the scope with its notices on."""
    from scripts.cli import main

    repo, store = canonical_session
    _seed(store, NAME)
    argv = ["project-id", "rekey", f"--from={NAME}", f"--to={CANONICAL}", "--db-path", str(store)]
    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 0, capfd.readouterr().err
    assert any("NOT in this read scope" in r.getMessage() for r in caplog.records), (
        "control: a dry run discloses the unproven bucket"
    )
    memory_api._UNPROVEN_WARNED.clear()
    memory_api._LEGACY_SCOPE_CACHE.clear()
    caplog.clear()
    capfd.readouterr()
    with caplog.at_level(logging.WARNING), pytest.raises(SystemExit) as exit_info:
        main([*argv, "--apply"])
    assert exit_info.value.code == 0, capfd.readouterr().err
    assert json.loads(capfd.readouterr().out)["result"]["memories"] == 1
    assert not any("legacy project bucket" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]


# ---------------------------------------------------------------------------
# Cycle 8: a row in a checkout with another name is a cross-reference
# ---------------------------------------------------------------------------


def test_a_folder_name_project_may_link_a_file_in_another_named_checkout(
    tmp_path, monkeypatch, scopes_clean
):
    """Cold probe 1: non-git `work/notes` saves memories linking its own file
    and `work/other/f.py`, `other` being a separate git repository. A
    checkout named `other` could only have written bucket `other`, so the
    row is a cross-reference: the bucket is read and saves still land."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    notes = tmp_path / "work" / "notes"
    (notes / ".claude").mkdir(parents=True)
    (notes / "a.md").write_text("x\n")
    other = make_repo(tmp_path / "work" / "other")
    (other / "f.py").write_text("x\n")
    store = tmp_path / "store.db"
    _seed(store, "notes", files=[notes / "a.md", other / "f.py"])
    monkeypatch.chdir(notes)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id == "notes"
    assert memory._read_project_scopes() == ("notes",)
    assert len(memory.list(limit=5)) == 1
    assert memory._project_id_for_write() == "notes"


def test_an_upgraded_project_keeps_a_bucket_that_links_another_named_checkout(
    tmp_path, monkeypatch, scopes_clean
):
    """Cold probe 2: no-origin `proj` saved a memory linking `proj/a.md` and
    `other/f.py`; after it gains an origin, its old bucket is verified and
    read, not hidden as foreign."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    proj = make_repo(tmp_path / "proj")
    (proj / "a.md").write_text("x\n")
    other = make_repo(tmp_path / "other")
    (other / "f.py").write_text("x\n")
    store = tmp_path / "store.db"
    _seed(store, "proj", files=[proj / "a.md", other / "f.py"])
    run_git("remote", "add", "origin", "https://github.com/me/proj.git", cwd=proj)
    monkeypatch.chdir(proj)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id == "github.com/me/proj"
    assert memory._read_project_scopes() == ("github.com/me/proj", "proj")
    state = memory._legacy_scope_state
    assert state["verified"] is True, state
    assert state["detail"]["foreign_rows"] == 0 and state["detail"]["neutral_outside"] == 1



# ---------------------------------------------------------------------------
# Cycle 9: a folder-name id's own bucket is not judged; the legacy rule stays
# for canonical sessions
# ---------------------------------------------------------------------------


def test_a_folder_name_id_shares_its_bucket_with_a_same_name_folder(basename_env):
    """No portable origin: the session's own folder-name bucket is read and
    saved into as before this change, even when a same-name project folder
    elsewhere wrote rows there (the documented shared-bucket residual)."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[other / "src.py"])
    memory = _basename_memory(store)
    assert memory._read_project_scopes() == (NAME,)
    assert len(memory.list(limit=5)) == 1
    assert memory._legacy_scope_state is None
    assert memory._project_id_for_write() == NAME


def test_both_same_name_checkouts_read_the_shared_bucket(shared_bucket, monkeypatch):
    """The no-origin `other/widget` reads the bucket it shares with the
    origin repository `widget`, both rows, as before this change."""
    mine, theirs, store = shared_bucket
    monkeypatch.chdir(theirs)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id == NAME
    assert memory._read_project_scopes() == (NAME,)
    assert len(memory.list(limit=5)) == 1
    assert memory._project_id_for_write() == NAME


def test_rule_b_marks_a_project_folder_named_like_the_bucket(basename_env, tmp_path):
    """Rule (b) for a canonical scope's legacy candidate: a non-git folder
    named like the bucket is a rival when `.claude` or a session record
    marks it."""
    repo, other, store = basename_env
    _seed(store, NAME, files=[other / "src.py"])
    marked = _evidence(store, NAME, [repo])
    assert marked["foreign_folder_rows"] == 1 and marked["foreign_checkout_rows"] == 0
    (other / ".claude").rmdir()
    assert _evidence(store, NAME, [repo])["foreign_rows"] == 0
    seed_session_evidence(tmp_path / "home" / ".claude", other, "theirs-session")
    assert _evidence(store, NAME, [repo])["foreign_folder_rows"] == 1


def test_rule_b_sees_a_victim_around_a_nested_hostile_root(tmp_path):
    """A root planted inside the victim's checkout (`victim/vendor/victim`)
    sees the victim's rows as foreign."""
    assert_outside_repository(tmp_path)
    victim = make_repo(tmp_path / NAME)
    (victim / "secret.py").write_text("x\n")
    hostile = make_repo(victim / "vendor" / NAME)
    store = tmp_path / "store.db"
    _seed(store, NAME, files=[victim / "secret.py"])
    assert _evidence(store, NAME, [hostile])["foreign_checkout_rows"] == 1


def test_a_separate_git_dir_checkout_named_like_the_bucket_is_a_rival(
    tmp_path, monkeypatch, scopes_clean
):
    """N-S5: a rival `--separate-git-dir` checkout `rival/widget` (git dir
    under `store/`, so its pre-change name is `store`) still writes bucket
    `widget` under its folder-name id, so its rows are foreign to the
    canonical `widget`'s legacy candidate."""
    import subprocess

    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    mine = make_repo(tmp_path / NAME, remote=ORIGIN)
    (tmp_path / "rival").mkdir()
    (tmp_path / "store").mkdir()
    rival = tmp_path / "rival" / NAME
    subprocess.run(
        ["git", "init", "-q", "--separate-git-dir", str(tmp_path / "store" / "x.git"), str(rival)],
        check=True,
    )
    (rival / "f.py").write_text("x\n")
    store = tmp_path / "store.db"
    _seed(store, NAME, files=[rival / "f.py"])
    assert _evidence(store, NAME, [mine])["foreign_checkout_rows"] == 1
    monkeypatch.chdir(mine)
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._read_project_scopes() == (CANONICAL,)
    assert memory._legacy_scope_state["excluded"] is True


def test_a_moved_store_re_decides_the_verdict(canonical_session, monkeypatch):
    """When the stable read repeats with locking, the cached verdict is
    dropped and the legacy bucket is counted again."""
    repo, store = canonical_session
    (repo / "a.py").write_text("x\n")
    _seed(store, NAME, files=[repo / "a.py"])
    calls = []
    real = memory_api._legacy_session_census

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(memory_api, "_legacy_session_census", counted)
    # Two stamps that differ, neither with a journal: the read "moved".
    stamps = iter([(1, 1, 1, False), (1, 1, 2, False)])
    real_stamp = memory_api._store_stamp
    monkeypatch.setattr(
        memory_api, "_store_stamp", lambda path: next(stamps, None) or real_stamp(path)
    )
    with memory_api.detection_pass():
        memory = PACTMemory(session_id="s", db_path=store)
        assert memory._read_project_scopes() == (CANONICAL, NAME)
    assert len(calls) == 2, calls


# ---------------------------------------------------------------------------
# A resume or heal rewrites started_at; the session is dated by its first start
# ---------------------------------------------------------------------------


_BEFORE_THE_CLONE = "2020-01-01T00:00:00Z"


def _journal(session_dir: Path, first_ts: str) -> None:
    (session_dir / "session-journal.jsonl").write_text(
        json.dumps({"type": "session_start", "ts": first_ts}) + "\n"
        + json.dumps({"type": "later", "ts": "2099-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )


def _root_census(repo: Path, store: Path) -> dict:
    with sqlite3.connect(str(store)) as conn:
        return memory_api._legacy_session_census(
            conn, NAME, [str(repo.resolve())], (repo / ".git").resolve()
        )


def test_a_session_resumed_after_a_re_clone_is_not_evidence(tmp_path):
    """The session first started before this clone's description (its
    journal's first event), and a resume rewrote started_at after it. RED
    WHEN the evidence is dated by the rewritten started_at."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME)
    config = tmp_path / "home" / ".claude"
    context = seed_session_evidence(config, repo, "resumed")
    _journal(context.parent, _BEFORE_THE_CLONE)
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="resumed")
    counts = _root_census(repo, store)
    assert counts["here"] == 0 and counts["description_newer"] == 1, counts


def test_a_session_healed_after_a_re_clone_is_not_evidence(tmp_path, monkeypatch):
    """The heal re-creates the context file through write_context, which
    records the heal time; the journal in the same folder keeps the first
    start. RED WHEN the heal time stands as the session's start."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME)
    config = tmp_path / "home" / ".claude"
    context = seed_session_evidence(config, repo, "healed", started_at=_BEFORE_THE_CLONE)
    _journal(context.parent, _BEFORE_THE_CLONE)
    context.unlink()
    monkeypatch.setattr(pact_context, "_context_path", None)
    monkeypatch.setattr(pact_context, "_cache", None)
    time.sleep(0.01)
    pact_context.write_context("session-healed", "healed", str(repo), "")
    healed = json.loads(context.read_text(encoding="utf-8"))
    assert healed["started_at"] > "2021", healed
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="healed")
    counts = _root_census(repo, store)
    assert counts["here"] == 0 and counts["description_newer"] == 1, counts


def test_a_fresh_session_after_a_re_clone_is_still_evidence(tmp_path):
    """Control: a session whose journal starts after the description (a new
    session, or a /clear, which opens its own folder) still counts."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME)
    config = tmp_path / "home" / ".claude"
    context = seed_session_evidence(config, repo, "fresh")
    started = json.loads(context.read_text(encoding="utf-8"))["started_at"]
    _journal(context.parent, started)
    store = tmp_path / "store.db"
    _seed(store, NAME, session_id="fresh")
    assert _root_census(repo, store)["here"] == 1


def test_the_prune_ledger_line_carries_the_first_start(tmp_path):
    """The reaper copies the session's earliest known start, so a pruned
    resumed session is dated as the live one is. RED WHEN the ledger keeps
    the rewritten started_at."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / NAME)
    config = tmp_path / "home" / ".claude"
    context = seed_session_evidence(config, repo, "pruned")
    _journal(context.parent, _BEFORE_THE_CLONE)
    line = json.loads(session_end._pruned_session_line(context.parent))
    assert line["started_at"] == _BEFORE_THE_CLONE, line


def test_a_journal_that_starts_later_does_not_move_the_start(tmp_path):
    """min() only moves the start earlier: a journal whose first event is
    later than started_at leaves started_at as recorded."""
    (tmp_path / "pact-session-context.json").write_text(
        json.dumps({"session_id": "s", "started_at": "2021-01-01T00:00:00+00:00"})
    )
    _journal(tmp_path, "2022-01-01T00:00:00Z")
    assert pact_context.read_session_record(tmp_path)["started_at"] == "2021-01-01T00:00:00+00:00"
