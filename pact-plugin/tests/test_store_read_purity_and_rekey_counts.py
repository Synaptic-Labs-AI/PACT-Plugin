"""Store and CLI behaviour a read, a dry run or a rekey must keep.

Location: pact-plugin/tests/test_store_read_purity_and_rekey_counts.py

Covers `scripts/cli.py` (`project-id`), `scripts/database.py`
(`get_connection`, `rekey_project`, `load_sqlite_vec`, keyword search),
`scripts/memory_init.py` (`ensure_memory_ready`), the read verbs of
`scripts/memory_api.py` and the prune-ledger writer in `hooks/session_end.py`.

Each arm names the defect it would catch.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from helpers import create_test_schema

CANONICAL = "github.com/o/proj"
REMOTE = "https://github.com/o/proj.git"


@pytest.fixture
def identity_env(monkeypatch, tmp_path):
    """A tmp root outside any repository, with no ambient declaration."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return tmp_path


def _cli(argv, capfd):
    """Run the CLI in process; return (exit code, parsed envelope)."""
    from scripts.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    stream = captured.out if exit_info.value.code == 0 else captured.err
    return exit_info.value.code, json.loads(stream)


def _store_with_row(path: Path, project_id: str, journal: str = "delete") -> Path:
    conn = sqlite3.connect(str(path))
    create_test_schema(conn)
    conn.execute(
        "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'ctx', ?, "
        "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (project_id,),
    )
    conn.commit()
    conn.execute(f"PRAGMA journal_mode={journal}")
    conn.close()
    return path


def _journal_mode(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


class TestAnAbsentStoreRefusesWhatAPresentStoreRefuses:
    """Catches the absent-store answer running before the target checks: a
    defaulted target for a canonical `--from` reported success with zero
    counters when no store existed, and was refused when one did."""

    def test_a_canonical_from_with_a_defaulted_target_is_refused(
        self, identity_env, monkeypatch, capfd
    ):
        from scripts.config import resolve_db_path

        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        code, envelope = _cli(
            ["project-id", "rekey", "--from=github.com/a/b"], capfd
        )
        assert code == 1, envelope
        assert envelope["error"] == "REKEY_REFUSED"
        assert not resolve_db_path().exists()

    def test_an_explicit_move_still_answers_store_absent(
        self, identity_env, monkeypatch, capfd
    ):
        """The positive control: a move the gates admit is still answered
        without bringing a store into existence."""
        from scripts.config import resolve_db_path

        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        code, envelope = _cli(
            ["project-id", "rekey", "--from=proj", f"--to={CANONICAL}", "--apply"],
            capfd,
        )
        assert code == 0, envelope
        assert envelope["result"]["store_absent"] is True
        assert not resolve_db_path().exists()


class TestADryRunLeavesTheJournalMode:
    """Catches a dry run opened outside the read scope: opening the store
    switched a DELETE-mode store to WAL although the move was rolled back."""

    def test_dry_run_keeps_delete_mode_and_apply_switches(
        self, identity_env, monkeypatch, capfd
    ):
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        store = _store_with_row(identity_env / "store.db", "proj")
        argv = ["project-id", "rekey", "--from=proj", f"--to={CANONICAL}",
                "--db-path", str(store)]
        code, envelope = _cli(argv, capfd)
        assert code == 0, envelope
        assert envelope["result"]["memories"] == 1
        assert _journal_mode(store) == "delete"
        # The apply is a write and may convert the store.
        code, envelope = _cli([*argv, "--apply"], capfd)
        assert code == 0, envelope
        assert _journal_mode(store) == "wal"


class TestACorruptStoreExitsThreeOnEveryProjectIdVerb:
    """Catches `show` and `--candidates` reaching the generic handler's exit
    2 (the bad-input code) while `rekey` exits 3 for the same fault."""

    @pytest.mark.parametrize("argv", [
        ["project-id", "show"],
        ["project-id", "rekey", "--candidates"],
        ["project-id", "rekey", "--from=proj", f"--to={CANONICAL}"],
    ], ids=["show", "candidates", "rekey"])
    def test_exit_three(self, identity_env, monkeypatch, capfd, argv):
        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        store = identity_env / "bad.db"
        store.write_bytes(b"not a sqlite store, " * 8)
        code, envelope = _cli([*argv, "--db-path", str(store)], capfd)
        assert code == 3, envelope
        assert "exists but cannot be opened" in envelope["message"]


def _rekey_store(tmp_path, edges, target_edges=()):
    from scripts.database import get_connection, init_schema

    conn = get_connection(tmp_path / "rekey.db")
    init_schema(conn)
    for name in ("a", "b", "c"):
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, 'proj')",
            ("old_" + name, "/p/" + name),
        )
    for name in ("a", "b"):
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            ("new_" + name, "/p/" + name, CANONICAL),
        )
    conn.executemany("INSERT INTO file_relations VALUES (?, ?, ?)",
                     [*edges, *target_edges])
    conn.commit()
    return conn


class TestEachRepointedRelationCountsOnce:
    """Catches the per-row count: an edge whose two ends both merge into
    existing target rows was counted once for each end."""

    @pytest.mark.parametrize("edges, expected", [
        ([("old_a", "old_b", "imports")], 1),
        ([("old_a", "old_b", "imports"), ("old_b", "old_a", "tests")], 2),
        ([("old_a", "old_a", "imports")], 1),
        ([("old_a", "old_c", "imports")], 1),
    ], ids=["both-ends", "two-edges", "self", "one-end"])
    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry", "apply"])
    def test_count(self, tmp_path, edges, expected, dry_run):
        from scripts.database import rekey_project

        conn = _rekey_store(tmp_path, edges)
        counters = rekey_project(conn, "proj", CANONICAL, dry_run=dry_run)
        assert counters["relations_repointed"] == expected
        assert counters["files_conflicted"] == 2

    def test_a_target_relation_with_another_relationship_is_kept(self, tmp_path):
        """Why there is no `relations_conflicted`: the relation key includes
        the relationship, so a re-pointed edge never displaces one."""
        from scripts.database import rekey_project

        conn = _rekey_store(
            tmp_path,
            [("old_a", "old_b", "imports")],
            [("new_a", "new_b", "tests")],
        )
        rekey_project(conn, "proj", CANONICAL, dry_run=False)
        assert sorted(
            tuple(r) for r in conn.execute("SELECT * FROM file_relations")
        ) == [("new_a", "new_b", "imports"), ("new_a", "new_b", "tests")]


def _extension_loading_enabled(conn) -> bool:
    """True when SQL can still load an extension on `conn`."""
    try:
        conn.execute("SELECT load_extension('/nonexistent/ext')")
    except Exception as exc:  # noqa: BLE001 - the message is the answer
        return "not authorized" not in str(exc)
    return True


class TestExtensionLoadingIsTurnedBackOff:
    """Catches `enable_load_extension(True)` left on after sqlite-vec loads,
    which lets any later SQL on the connection load another extension."""

    def test_the_helper_disables_loading(self, tmp_path):
        pytest.importorskip("sqlite_vec")
        from scripts.database import (
            SQLITE_EXTENSIONS_ENABLED,
            get_connection,
            load_sqlite_vec,
        )

        if not SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 cannot load extensions")
        conn = get_connection(tmp_path / "vec.db")
        load_sqlite_vec(conn)
        assert conn.execute("SELECT vec_version()").fetchone()[0]
        assert not _extension_loading_enabled(conn)

    def test_the_rekey_leaves_loading_disabled(self, tmp_path):
        pytest.importorskip("sqlite_vec")
        from scripts.database import (
            SQLITE_EXTENSIONS_ENABLED,
            ensure_initialized,
            get_connection,
            rekey_project,
        )

        if not SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 cannot load extensions")
        conn = get_connection(tmp_path / "vec.db")
        ensure_initialized(conn)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='vec_memories'"
        ).fetchone() is None:
            pytest.skip("no vector table in this environment")
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES ('m1', 'x', 'proj', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()
        conn = get_connection(tmp_path / "vec.db")
        rekey_project(conn, "proj", CANONICAL, dry_run=False)
        assert not _extension_loading_enabled(conn)


    @pytest.mark.parametrize(
        "path", ["drop-vector", "partition-check", "store-embedding"]
    )
    def test_the_memory_api_vector_paths_leave_loading_disabled(
        self, tmp_path, monkeypatch, path
    ):
        pytest.importorskip("sqlite_vec")
        from scripts import memory_api
        from scripts.database import (
            SQLITE_EXTENSIONS_ENABLED,
            ensure_initialized,
            get_connection,
        )

        if not SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 cannot load extensions")
        conn = get_connection(tmp_path / "vec.db")
        ensure_initialized(conn)
        dim = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='vec_memories'"
        ).fetchone()
        if dim is None:
            pytest.skip("no vector table in this environment")
        width = int(dim[0].split("float[")[1].split("]")[0])
        monkeypatch.setattr(memory_api, "generate_embedding",
                            lambda *a, **k: [0.0] * width)
        # `_store_embedding` calls `_drop_existing_vector` after its own load,
        # and that call's load would turn loading off again and hide a leak
        # at the first site, so it is replaced here.
        api = memory_api.PACTMemory
        if path == "drop-vector":
            api._drop_existing_vector(conn, "m1", commit=False)
        elif path == "partition-check":
            api._vector_partition_is_stale(conn, "m1", "proj")
        else:
            monkeypatch.setattr(api, "_drop_existing_vector",
                                staticmethod(lambda *a, **k: True))
            api(db_path=None)._store_embedding(
                conn, "m1", {"id": "m1", "context": "x", "project_id": "proj"})
        assert not _extension_loading_enabled(conn)


    def test_the_embedding_migration_leaves_loading_disabled(
        self, tmp_path, monkeypatch
    ):
        pytest.importorskip("sqlite_vec")
        from scripts import database, memory_init

        if not database.SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 cannot load extensions")
        conn = database.get_connection(tmp_path / "vec.db")
        database.ensure_initialized(conn)

        class _Held:
            """The migration's connection, kept open after it closes it."""

            def __getattr__(self, name):
                return getattr(conn, name)

            def close(self):
                pass

        held = _Held()
        loads = []
        real_load = database.load_sqlite_vec

        def _spy(connection):
            loads.append(connection)
            real_load(connection)

        monkeypatch.setattr(database, "get_connection", lambda *a, **k: held)
        monkeypatch.setattr(database, "load_sqlite_vec", _spy)
        memory_init.maybe_migrate_embeddings()
        # The migration loads sqlite-vec through the one helper, and loading
        # is off again afterwards.
        assert loads == [held]
        assert not _extension_loading_enabled(conn)


class TestAReadDoesNotCreateAStore:
    """Catches `get_connection` pre-creating the file inside a read scope,
    which left a 0-byte store behind a library read."""

    def test_absent_store_raises_and_stays_absent(self, tmp_path):
        from scripts.config import read_only_scope
        from scripts.database import get_connection
        from scripts.database import sqlite3 as db_sqlite3

        path = tmp_path / "absent.db"
        with read_only_scope(), pytest.raises(db_sqlite3.OperationalError):
            get_connection(path)
        assert not path.exists()
        # Outside the scope the same call still creates it.
        get_connection(path).close()
        assert path.exists()


def _pre_ct_store(path: Path) -> Path:
    """A store from before v3.8.0: no reasoning_chains, agreements_reached
    or disagreements_resolved columns."""
    conn = sqlite3.connect(str(path))
    create_test_schema(conn)
    conn.execute(
        "INSERT INTO memories (id, context, lessons_learned, project_id, "
        "created_at, updated_at) VALUES ('m1', 'old', '[\"about widgets\"]', "
        "'p', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    for col in ("reasoning_chains", "agreements_reached", "disagreements_resolved"):
        conn.execute(f"ALTER TABLE memories DROP COLUMN {col}")
    conn.commit()
    conn.close()
    return path


class TestAPreCtStoreIsReadableWithoutAMigration:
    """Catches the keyword search naming the CT columns: once a read stopped
    migrating, a store from before v3.8.0 failed every search with
    'no such column' until a write ran."""

    def test_keyword_search_finds_the_row_and_adds_no_column(self, tmp_path):
        from scripts.config import read_only_scope
        from scripts.database import get_connection, search_memories_by_text

        path = _pre_ct_store(tmp_path / "old.db")
        with read_only_scope():
            conn = get_connection(path)
            rows = search_memories_by_text(conn, "widgets", "p")
            conn.close()
        assert [r["id"] for r in rows] == ["m1"]
        conn = sqlite3.connect(str(path))
        columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        conn.close()
        assert "reasoning_chains" not in columns


class TestAReadDefersTheEmbeddingMigration:
    """Catches the lazy initializer running the embedding migration (which
    drops and recreates the vector table) on a read verb's first call."""

    def test_migration_waits_for_a_call_outside_the_read_scope(self, monkeypatch):
        from scripts import memory_init
        from scripts.config import read_only_scope

        calls = []
        monkeypatch.setattr(memory_init, "check_and_install_dependencies",
                            lambda: {})
        monkeypatch.setattr(memory_init, "maybe_embed_pending",
                            lambda: calls.append("catch-up") or {})
        monkeypatch.setattr(memory_init, "maybe_migrate_embeddings",
                            lambda: calls.append("migrate") or {})
        memory_init.reset_initialization()
        with read_only_scope():
            memory_init.ensure_memory_ready()
            memory_init.ensure_memory_ready()
        assert calls == ["catch-up"]
        memory_init.ensure_memory_ready()
        memory_init.ensure_memory_ready()
        assert calls == ["catch-up", "migrate"]


class TestEveryReadVerbInitializesInsideTheReadScope:
    """Catches a read verb calling `_ensure_ready()` outside the read scope,
    where the catch-up's initializer adds columns and creates the vector
    table on a store the verb only reads."""

    @pytest.mark.parametrize("verb", [
        lambda m: m.search("ctx"),
        lambda m: m.search_by_file("/p/a"),
        lambda m: m.get("0123456789abcdef0123456789abcdef"),
        lambda m: m.list(),
        lambda m: m.get_status(),
    ], ids=["search", "search_by_file", "get", "list", "get_status"])
    def test_scope_is_active(self, identity_env, monkeypatch, verb):
        from scripts import memory_api
        from scripts.config import read_only_scope_active

        memory_api._LEGACY_SCOPE_CACHE.clear()
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        store = _store_with_row(identity_env / "store.db", CANONICAL)
        seen = []
        monkeypatch.setattr(memory_api, "_ensure_ready",
                            lambda: seen.append(read_only_scope_active()))
        verb(memory_api.PACTMemory(db_path=store))
        assert seen == [True]


class TestThePruneLedgerIsBounded:
    """Catches a ledger that only ever grows while its reader reads the last
    1 MiB: past twice that window the writer keeps the window's whole lines."""

    def test_compaction_keeps_the_newest_whole_lines(self, tmp_path):
        import session_end
        slug = tmp_path / "slug"
        slug.mkdir()
        line = json.dumps({"session_id": "s", "project_dir": "/x",
                           "started_at": "2026-01-01T00:00:00+00:00",
                           "pad": "x" * 200})
        count = (3 * session_end._PRUNE_LEDGER_KEEP_BYTES) // len(line)
        for i in range(count):
            session_end._append_ledger_line(slug, line.replace('"s"', f'"s{i}"') + "\n")
        ledger = slug / "pruned-sessions.jsonl"
        data = ledger.read_bytes()
        assert len(data) <= session_end._PRUNE_LEDGER_COMPACT_BYTES
        lines = data.splitlines()
        assert all(json.loads(item) for item in lines)
        assert json.loads(lines[-1])["session_id"] == f"s{count - 1}"
        assert len(data) >= session_end._PRUNE_LEDGER_KEEP_BYTES // 2

    @staticmethod
    def _ledger_one_line_below_compaction(slug):
        """A ledger one line short of compaction, plus a line that crosses it."""
        import session_end

        def line(session_id):
            return json.dumps({"session_id": session_id, "project_dir": "/x",
                               "started_at": "2026-01-01T00:00:00+00:00",
                               "pad": "x" * 200}) + "\n"

        width = len(line("s000000"))
        count = session_end._PRUNE_LEDGER_COMPACT_BYTES // width
        slug.mkdir()
        ledger = slug / "pruned-sessions.jsonl"
        ledger.write_bytes("".join(line(f"s{i:06d}") for i in range(count)).encode())
        ledger.chmod(0o600)
        return ledger, line

    def test_a_reader_never_sees_an_empty_or_partial_ledger(self, tmp_path, monkeypatch):
        """Catches a cut-back done in place (truncate, then rewrite): a reader
        between the two calls finds no lines. Reads the ledger before and after
        every write, truncate, sync and rename the compaction makes."""
        import os
        import threading

        import session_end
        from scripts import memory_api

        slug = tmp_path / "slug"
        ledger, line = self._ledger_one_line_below_compaction(slug)
        main = threading.get_ident()
        seen = []
        busy = []

        def observed(real):
            def call(*args, **kwargs):
                if busy or threading.get_ident() != main:
                    return real(*args, **kwargs)
                busy.append(True)
                try:
                    seen.append(memory_api._pruned_session_records(ledger))
                    result = real(*args, **kwargs)
                    seen.append(memory_api._pruned_session_records(ledger))
                finally:
                    busy.pop()
                return result
            return call

        for name in ("write", "ftruncate", "truncate", "fsync", "replace", "rename"):
            monkeypatch.setattr(os, name, observed(getattr(os, name)))
        session_end._append_ledger_line(slug, line("trigger"))
        monkeypatch.undo()

        final = memory_api._pruned_session_records(ledger)
        assert ledger.stat().st_size <= session_end._PRUNE_LEDGER_KEEP_BYTES
        assert final[-1]["session_id"] == "trigger"
        assert len(seen) >= 4
        assert min(len(records) for records in seen) >= len(final) - 1
        assert [p.name for p in slug.iterdir()] == ["pruned-sessions.jsonl"]
        assert ledger.stat().st_mode & 0o777 == 0o600

    def test_a_writer_waiting_on_the_old_ledger_appends_to_the_new_one(
        self, tmp_path, monkeypatch
    ):
        """Catches a writer that opened the ledger, then waited for the lock
        while another writer's compaction replaced the file. Without a check of
        the inode after the lock, the late writer appends to the replaced file,
        finds it over the limit, compacts it and renames it over the ledger,
        which drops a line a third writer had appended to the new file."""
        import session_end

        slug = tmp_path / "slug"
        ledger, line = self._ledger_one_line_below_compaction(slug)
        real_flock = session_end.fcntl.flock
        raced = []

        def flock(fd, operation):
            if not raced:
                raced.append(True)
                session_end._append_ledger_line(slug, line("trigger"))
                session_end._append_ledger_line(slug, line("between"))
            return real_flock(fd, operation)

        monkeypatch.setattr(session_end.fcntl, "flock", flock)
        session_end._append_ledger_line(slug, line("late"))
        monkeypatch.undo()

        assert raced
        assert ledger.stat().st_size < session_end._PRUNE_LEDGER_COMPACT_BYTES
        ids = [json.loads(item)["session_id"] for item in ledger.read_bytes().splitlines()]
        assert ids[-3:] == ["trigger", "between", "late"]

    def test_a_failed_cut_back_keeps_the_old_ledger_and_no_scratch_file(
        self, tmp_path, monkeypatch
    ):
        """Catches a compaction that fails part way and leaves its new file
        in the folder, or loses the ledger it was replacing."""
        import os

        import session_end

        slug = tmp_path / "slug"
        ledger, line = self._ledger_one_line_below_compaction(slug)

        def refuse(*args, **kwargs):
            raise OSError("rename refused")

        monkeypatch.setattr(os, "replace", refuse)
        session_end._append_ledger_line(slug, line("trigger"))
        monkeypatch.undo()

        assert [p.name for p in slug.iterdir()] == ["pruned-sessions.jsonl"]
        ids = [json.loads(item)["session_id"] for item in ledger.read_bytes().splitlines()]
        assert ids[0] == "s000000"
        assert ids[-1] == "trigger"
