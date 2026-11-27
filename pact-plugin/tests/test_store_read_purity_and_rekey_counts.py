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

    @pytest.mark.parametrize("with_store", [False, True])
    def test_a_defaulted_target_equal_to_from_is_a_no_op_success(
        self, identity_env, monkeypatch, capfd, with_store
    ):
        """Catches a defaulted --to that equals --from being refused as a
        canonical-bucket move: it moves nothing, so it is the no-op success
        an explicit equal --to already gets."""
        from scripts.config import resolve_db_path

        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        if with_store:
            resolve_db_path().parent.mkdir(parents=True, exist_ok=True)
            _store_with_row(resolve_db_path(), CANONICAL)
            before = resolve_db_path().read_bytes()
        code, envelope = _cli(
            ["project-id", "rekey", f"--from={CANONICAL}"], capfd
        )
        assert code == 0, envelope
        result = envelope["result"]
        assert (result["noop"], result["target_defaulted"]) == (True, True)
        assert (result["from"], result["to"]) == (CANONICAL, CANONICAL)
        # A no-op reports the bucket's rows, not 0 (nothing moved either way).
        assert result["memories"] == (1 if with_store else 0), result
        if with_store:
            assert resolve_db_path().read_bytes() == before
        else:
            assert not resolve_db_path().exists()

    @pytest.mark.parametrize("with_store", [False, True])
    def test_an_explicit_target_equal_to_a_non_canonical_from_is_a_no_op_success(
        self, identity_env, monkeypatch, capfd, with_store
    ):
        """Catches the canonical-target check refusing `--from=X --to=X` for a
        folder-name X: the target equals the source, so nothing moves and the
        answer is the promised no-op success."""
        from scripts.config import resolve_db_path

        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        if with_store:
            resolve_db_path().parent.mkdir(parents=True, exist_ok=True)
            _store_with_row(resolve_db_path(), "proj")
            before = resolve_db_path().read_bytes()
        code, envelope = _cli(
            ["project-id", "rekey", "--from=proj", "--to=proj", "--apply"], capfd
        )
        assert code == 0, envelope
        result = envelope["result"]
        assert (result["noop"], result["target_defaulted"]) == (True, False)
        assert (result["from"], result["to"]) == ("proj", "proj")
        assert result["memories"] == (1 if with_store else 0), result
        if with_store:
            assert resolve_db_path().read_bytes() == before
        else:
            assert not resolve_db_path().exists()

    def test_a_non_canonical_target_other_than_from_is_still_refused(
        self, identity_env, monkeypatch, capfd
    ):
        """The control: a folder-name --to that differs from --from is bad
        input, refused before anything is opened."""
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        code, envelope = _cli(
            ["project-id", "rekey", "--from=proj", "--to=other"], capfd
        )
        assert (code, envelope["error"]) == (2, "INVALID_INPUT"), envelope

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


class TestACorruptStoreExitsThreeOnEveryVerb:
    """Catches a verb answering a store fault with exit 2, the bad-input
    code: `show` and `--candidates` once did while `rekey` exited 3, and
    `status`, `list`, `search`, `sync` and `save` did through the generic
    handler."""

    @pytest.mark.parametrize("argv", [
        ["project-id", "show"],
        ["project-id", "rekey", "--candidates"],
        ["project-id", "rekey", "--from=proj", f"--to={CANONICAL}"],
        ["status"],
        ["list"],
        ["search", "anything"],
        ["sync", "--claude-md-root", "{repo}"],
        ["save", json.dumps({"context": "store fault"})],
    ], ids=["show", "candidates", "rekey", "status", "list", "search", "sync", "save"])
    def test_exit_three(self, identity_env, monkeypatch, capfd, argv):
        from scripts import memory_api

        memory_api._LEGACY_SCOPE_CACHE.clear()
        repo = make_repo(identity_env / "proj", remote=REMOTE)
        monkeypatch.chdir(repo)
        store = identity_env / "bad.db"
        store.write_bytes(b"not a sqlite store, " * 8)
        argv = [arg.replace("{repo}", str(repo)) for arg in argv]
        code, envelope = _cli([*argv, "--db-path", str(store)], capfd)
        assert code == 3, envelope
        assert "exists but cannot be opened" in envelope["message"]
        assert not (repo / "CLAUDE.md").exists()


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

    # Production code outside tests: every .py under pact-plugin except tests/.
    _HELPER = ("skills/pact-memory/scripts/database.py", "load_sqlite_vec")

    @staticmethod
    def _extension_loading_outside_helper(source: str, rel: str) -> list:
        """Each place `source` touches extension loading outside the helper:
        an `enable_load_extension` or `load_extension` attribute (a call or a
        bound reference), a getattr of either by name, a `<sqlite_vec>.load`
        through any name the module bound to sqlite_vec, and an import of
        `load` from sqlite_vec."""
        import ast

        tree = ast.parse(source)
        allowed = set()
        if rel == TestExtensionLoadingIsTurnedBackOff._HELPER[0]:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.name == TestExtensionLoadingIsTurnedBackOff._HELPER[1]
                ):
                    allowed.update(id(sub) for sub in ast.walk(node))
        vec_names = {"sqlite_vec"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                vec_names.update(
                    alias.asname or alias.name for alias in node.names
                    if alias.name == "sqlite_vec"
                )
        names = {"enable_load_extension", "load_extension"}
        found = []
        for node in ast.walk(tree):
            if id(node) in allowed:
                continue
            if isinstance(node, ast.Attribute) and node.attr in names:
                found.append((rel, node.lineno, node.attr))
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == "load"
                and isinstance(node.value, ast.Name)
                and node.value.id in vec_names
            ):
                found.append((rel, node.lineno, "sqlite_vec.load"))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and any(
                    isinstance(arg, ast.Constant) and arg.value in names
                    for arg in node.args
                )
            ):
                found.append((rel, node.lineno, "getattr"))
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "sqlite_vec"
                and any(alias.name == "load" for alias in node.names)
            ):
                found.append((rel, node.lineno, "from sqlite_vec import load"))
        return found

    def test_the_scanner_finds_each_raw_form(self):
        """Control for the pin below: each form it bans is found outside the
        helper and passes inside it."""
        raw = (
            "import sqlite_vec as sv\n"
            "from sqlite_vec import load\n"
            "def f(conn):\n"
            "    conn.enable_load_extension(True)\n"
            "    sv.load(conn)\n"
            "    g = conn.load_extension\n"
            "    getattr(conn, 'enable_load_extension')(True)\n"
        )
        kinds = sorted(k for _, _, k in self._extension_loading_outside_helper(raw, "x.py"))
        assert kinds == sorted([
            "enable_load_extension", "load_extension", "sqlite_vec.load",
            "getattr", "from sqlite_vec import load",
        ])
        helper = (
            "import sqlite_vec\n"
            "def load_sqlite_vec(conn):\n"
            "    conn.enable_load_extension(True)\n"
            "    sqlite_vec.load(conn)\n"
        )
        assert self._extension_loading_outside_helper(helper, self._HELPER[0]) == []

    def test_only_the_helper_turns_extension_loading_on(self):
        """Catches a sqlite-vec load site that bypasses `load_sqlite_vec`: a
        raw `enable_load_extension(True)` there leaves loading on for the
        rest of the connection, and no other test reaches every site."""
        plugin = Path(__file__).resolve().parents[1]
        files = sorted(
            path for path in plugin.rglob("*.py")
            if "tests" not in path.relative_to(plugin).parts
            and "__pycache__" not in path.parts
        )
        rels = {path.relative_to(plugin).as_posix() for path in files}
        # Fail closed: the walk saw the plugin and the helper itself.
        assert self._HELPER[0] in rels and len(files) > 50, len(files)
        helper_source = (plugin / self._HELPER[0]).read_text(encoding="utf-8")
        assert self._extension_loading_outside_helper(
            helper_source.replace(f"def {self._HELPER[1]}(", "def _renamed("),
            self._HELPER[0],
        ), "the helper's own loading calls must be visible to this scan"
        found = [
            hit
            for path in files
            for hit in self._extension_loading_outside_helper(
                path.read_text(encoding="utf-8"), path.relative_to(plugin).as_posix()
            )
        ]
        assert found == [], found


class TestTheCatchUpOnASessionThatStartsWithARead:
    """A read may add vector rows for memories not yet embedded, and nothing
    else. Catches a read-first session's catch-up failing and spending the
    session marker (no vector table, or a migration due), and a postponed
    catch-up never running."""

    @staticmethod
    def _session(monkeypatch, tmp_path, identity_env):
        """Fresh catch-up state, stub embeddings, a marker under tmp_path,
        and this repository as the working directory. Returns the store path
        and the memory_init module."""
        pytest.importorskip("sqlite_vec")
        from scripts import embedding_catchup, embeddings, memory_init, search
        from scripts.config import resolve_db_path
        from scripts.database import SQLITE_EXTENSIONS_ENABLED

        if not SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 cannot load extensions")
        monkeypatch.chdir(make_repo(identity_env / "proj", remote=REMOTE))
        for name, value in (("_initialized", False), ("_migration_deferred", set()),
                            ("_catchup_deferred", set()), ("_last_catchup_result", None)):
            monkeypatch.setattr(memory_init, name, value, raising=False)
        monkeypatch.setattr(
            memory_init, "_get_embedding_attempted_path", lambda: tmp_path / "marker"
        )

        def fake(text):
            return [0.001 + 0.01 * ((len(text) + i) % 7) for i in range(embeddings.EMBEDDING_DIM)]

        class _Service:
            generate = staticmethod(fake)
            embedding_dimension = embeddings.EMBEDDING_DIM

        monkeypatch.setattr(embedding_catchup, "generate_embedding", fake)
        monkeypatch.setattr(search, "generate_embedding", fake)
        monkeypatch.setattr(embeddings, "get_embedding_service", lambda: _Service())
        store = resolve_db_path()
        store.parent.mkdir(parents=True, exist_ok=True)
        return store, memory_init

    @staticmethod
    def _memories(conn, *rows):
        for memory_id, context in rows:
            conn.execute(
                "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
                "VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (memory_id, context, CANONICAL),
            )
        conn.commit()

    @staticmethod
    def _vectors(store):
        from scripts.database import get_connection, load_sqlite_vec

        conn = get_connection(store)
        try:
            load_sqlite_vec(conn)
            return sorted(
                (row[0], len(row[1]) // 4)
                for row in conn.execute("SELECT memory_id, embedding FROM vec_memories")
            )
        finally:
            conn.close()

    def test_no_vector_table_postpones_to_the_first_write(
        self, monkeypatch, tmp_path, identity_env
    ):
        from scripts import memory_api

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = sqlite3.connect(str(store))
        create_test_schema(conn)
        self._memories(conn, ("a" * 32, "alpha widgets"), ("b" * 32, "zebra crossing"))
        conn.close()
        before = store.read_bytes()

        hits = memory_api.PACTMemory().search("zebra")
        # The read found the memory by keyword, wrote nothing, and kept the
        # marker for the postponed sweep.
        assert "b" * 32 in [hit.id for hit in hits]
        assert store.read_bytes() == before
        assert memory_init.get_embedding_catchup_status() is None
        assert not (tmp_path / "marker").exists()

        memory_init.ensure_memory_ready()
        assert memory_init.get_embedding_catchup_status()["status"] == "ok"
        assert [m for m, _ in self._vectors(store)] == ["a" * 32, "b" * 32]

    def test_a_table_with_an_unembedded_row_is_caught_up_by_the_read(
        self, monkeypatch, tmp_path, identity_env
    ):
        """The exception: the read adds the missing vector, so search finds
        the memory instead of returning only its embedded neighbours."""
        import struct

        from scripts import embeddings, memory_api
        from scripts.database import ensure_initialized, get_connection, load_sqlite_vec

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = get_connection(store)
        ensure_initialized(conn)
        self._memories(conn, ("a" * 32, "alpha widgets"), ("b" * 32, "zebra crossing"))
        load_sqlite_vec(conn)
        conn.execute(
            "INSERT INTO vec_memories (memory_id, project_id, embedding) VALUES (?, ?, ?)",
            ("a" * 32, CANONICAL, struct.pack(f"{embeddings.EMBEDDING_DIM}f",
                                             *[0.5] * embeddings.EMBEDDING_DIM)),
        )
        conn.commit()
        conn.close()
        tables = sqlite3.connect(str(store)).execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()

        hits = memory_api.PACTMemory().search("zebra")
        assert "b" * 32 in [hit.id for hit in hits]
        assert [m for m, _ in self._vectors(store)] == ["a" * 32, "b" * 32]
        assert memory_init.get_embedding_catchup_status()["status"] == "ok"
        # Nothing but vector rows changed: the schema is as it was.
        assert sqlite3.connect(str(store)).execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == tables

    def test_the_read_embeds_no_row_outside_its_scope(
        self, monkeypatch, tmp_path, identity_env
    ):
        """A read on this project catches up its own unembedded row and leaves
        another project's unembedded row alone. RED WHEN the read's catch-up
        sweeps the whole store."""
        import struct

        from scripts import embeddings, memory_api
        from scripts.database import ensure_initialized, get_connection, load_sqlite_vec

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = get_connection(store)
        ensure_initialized(conn)
        self._memories(conn, ("a" * 32, "alpha widgets"), ("b" * 32, "zebra crossing"))
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES (?, 'other zebra', 'github.com/other/repo', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            ("c" * 32,),
        )
        load_sqlite_vec(conn)
        conn.execute(
            "INSERT INTO vec_memories (memory_id, project_id, embedding) VALUES (?, ?, ?)",
            ("a" * 32, CANONICAL, struct.pack(f"{embeddings.EMBEDDING_DIM}f",
                                             *[0.5] * embeddings.EMBEDDING_DIM)),
        )
        conn.commit()
        conn.close()

        memory_api.PACTMemory().search("zebra")
        assert [m for m, _ in self._vectors(store)] == ["a" * 32, "b" * 32]
        # The read spent neither the session marker nor the store-wide
        # sweep: the next call outside the read scope, a save in the same
        # process, embeds the other project's row. RED WHEN the scoped
        # read's catch-up uses up the session's sweep.
        assert not (tmp_path / "marker").exists()
        memory_api.PACTMemory().save({"context": "a later write"})
        assert "c" * 32 in [m for m, _ in self._vectors(store)]
        assert (tmp_path / "marker").exists()

    def test_a_read_honours_a_marker_already_written(
        self, monkeypatch, tmp_path, identity_env
    ):
        """Once the session's sweep has run (its marker exists), a read's
        scoped catch-up does not try again, so a memory whose embedding keeps
        failing is not retried by every read. RED WHEN a read ignores the
        marker."""
        import struct

        from scripts import embeddings, memory_api
        from scripts.database import ensure_initialized, get_connection, load_sqlite_vec

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = get_connection(store)
        ensure_initialized(conn)
        self._memories(conn, ("a" * 32, "alpha widgets"), ("b" * 32, "zebra crossing"))
        load_sqlite_vec(conn)
        conn.execute(
            "INSERT INTO vec_memories (memory_id, project_id, embedding) VALUES (?, ?, ?)",
            ("a" * 32, CANONICAL, struct.pack(f"{embeddings.EMBEDDING_DIM}f",
                                             *[0.5] * embeddings.EMBEDDING_DIM)),
        )
        conn.commit()
        conn.close()
        (tmp_path / "marker").touch()

        memory_api.PACTMemory().search("zebra")
        assert [m for m, _ in self._vectors(store)] == ["a" * 32]

    def test_two_reads_in_a_session_make_one_attempt_and_a_write_still_sweeps(
        self, monkeypatch, tmp_path, identity_env
    ):
        """A read records its own attempt for the session and its scope, so a
        second read process in that session does not try a failing embedding
        again; a write still runs the store-wide sweep and writes the session
        marker. RED WHEN every read retries."""
        import struct

        from scripts import embedding_catchup, embeddings, memory_api
        from scripts.database import ensure_initialized, get_connection, load_sqlite_vec

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = get_connection(store)
        ensure_initialized(conn)
        self._memories(conn, ("a" * 32, "alpha widgets"), ("b" * 32, "zebra crossing"))
        load_sqlite_vec(conn)
        conn.execute(
            "INSERT INTO vec_memories (memory_id, project_id, embedding) VALUES (?, ?, ?)",
            ("a" * 32, CANONICAL, struct.pack(f"{embeddings.EMBEDDING_DIM}f",
                                             *[0.5] * embeddings.EMBEDDING_DIM)),
        )
        conn.commit()
        conn.close()
        tried = []
        monkeypatch.setattr(
            embedding_catchup, "embed_single_memory", lambda mid: (tried.append(mid), False)[1]
        )
        for _process in range(2):
            # A new process: the in-process state is fresh, the /tmp markers persist.
            for name, value in (("_initialized", False), ("_migration_deferred", set()),
                                ("_catchup_deferred", set()), ("_last_catchup_result", None)):
                monkeypatch.setattr(memory_init, name, value)
            memory_api.PACTMemory().search("zebra")
        assert tried == ["b" * 32], tried
        assert not (tmp_path / "marker").exists()
        monkeypatch.setattr(embedding_catchup, "embed_single_memory", lambda mid: (tried.append(mid), True)[1])
        memory_api.PACTMemory().save({"context": "a later write"})
        assert tried[1:] and "b" * 32 in tried[1:], tried
        assert (tmp_path / "marker").exists()

    @pytest.mark.parametrize("old_table_empty", [False, True])
    def test_a_migration_due_postpones_until_after_the_migration(
        self, monkeypatch, tmp_path, identity_env, old_table_empty
    ):
        """An old-dimension table postpones the read's catch-up, and the
        first call outside the read scope migrates it, then catches up.

        RED WHEN an EMPTY table at the old dimension reads as current: the
        read then spends the marker on inserts the table refuses, and the
        migration skips the table."""
        import struct

        from scripts import embeddings, memory_api
        from scripts.database import get_connection, load_sqlite_vec

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = get_connection(store)
        create_test_schema(conn)
        self._memories(conn, ("a" * 32, "alpha widgets"), ("b" * 32, "zebra crossing"))
        load_sqlite_vec(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0(memory_id TEXT PRIMARY KEY, "
            "project_id TEXT PARTITION KEY, embedding float[128])"
        )
        if not old_table_empty:
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding) VALUES (?, ?, ?)",
                ("a" * 32, CANONICAL, struct.pack("128f", *[0.5] * 128)),
            )
        conn.commit()
        conn.close()
        before = store.read_bytes()

        hits = memory_api.PACTMemory().search("zebra")
        assert "b" * 32 in [hit.id for hit in hits]
        assert store.read_bytes() == before
        assert memory_init.get_embedding_catchup_status() is None
        assert not (tmp_path / "marker").exists()

        memory_init.ensure_memory_ready()
        assert memory_init.get_embedding_catchup_status()["status"] == "ok"
        assert self._vectors(store) == [
            ("a" * 32, embeddings.EMBEDDING_DIM), ("b" * 32, embeddings.EMBEDDING_DIM)
        ]


    def test_an_unreadable_dimension_postpones_the_read_and_is_migrated(
        self, monkeypatch, tmp_path, identity_env
    ):
        """A vector table whose dimension can be read neither from its
        declaration nor from a stored vector counts as a migration due.

        RED WHEN an unknown dimension lets the read catch up, or the
        migration skips the table."""
        from scripts import embeddings
        from scripts.database import get_connection, load_sqlite_vec

        store, memory_init = self._session(monkeypatch, tmp_path, identity_env)
        conn = sqlite3.connect(str(store))
        create_test_schema(conn)
        conn.execute(
            "CREATE TABLE vec_memories (memory_id TEXT, project_id TEXT, embedding BLOB)"
        )
        conn.commit()
        conn.close()

        assert memory_init._read_can_catch_up() is False
        migrated = memory_init.maybe_migrate_embeddings()
        assert migrated["message"].startswith("Migrated "), migrated
        conn = get_connection(store)
        try:
            load_sqlite_vec(conn)
            assert memory_init._stored_vector_dimension(conn) == (
                True, embeddings.EMBEDDING_DIM
            )
        finally:
            conn.close()


class TestTheStoredVectorDimension:
    """The dimension comes from the table's declaration; without one it is
    the size of a stored vector; with neither it is unknown (None), which the
    catch-up and the migration read as a migration being due."""

    def _dimension(self, ddl, vector=None):
        from scripts.memory_init import _stored_vector_dimension

        conn = sqlite3.connect(":memory:")
        conn.execute(ddl)
        if vector is not None:
            conn.execute("INSERT INTO vec_memories VALUES ('m', 'p', ?)", (vector,))
        return _stored_vector_dimension(conn)

    def test_no_table(self):
        from scripts.memory_init import _stored_vector_dimension

        assert _stored_vector_dimension(sqlite3.connect(":memory:")) == (False, None)

    def test_a_declared_dimension_wins_over_an_empty_table(self):
        ddl = ("CREATE TABLE vec_memories (memory_id TEXT, project_id TEXT, "
               "embedding float[384])")
        assert self._dimension(ddl) == (True, 384)

    def test_an_undeclared_dimension_is_read_from_a_stored_vector(self):
        import struct

        ddl = "CREATE TABLE vec_memories (memory_id TEXT, project_id TEXT, embedding BLOB)"
        assert self._dimension(ddl, struct.pack("128f", *[0.5] * 128)) == (True, 128)

    def test_with_neither_the_dimension_is_unknown(self):
        ddl = "CREATE TABLE vec_memories (memory_id TEXT, project_id TEXT, embedding BLOB)"
        assert self._dimension(ddl) == (True, None)


class TestTheRegistryRewriteWritesEveryByte:
    """Catches the teammate-registry rewrite keeping whatever one `os.write`
    accepted: a short write left the registry truncated mid-line."""

    @pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
    def test_a_short_write_still_rewrites_every_kept_line(self, tmp_path, monkeypatch):
        from session_end import _prune_registry_dead_teams

        teams = tmp_path / ".claude" / "teams"
        (teams / "pact-live").mkdir(parents=True)
        registry = tmp_path / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
        registry.parent.mkdir(parents=True)
        kept = [json.dumps({"session_id": f"s{i}", "value": f"m{i}@pact-live"})
                for i in range(20)]
        dead = json.dumps({"session_id": "gone", "value": "x@pact-dead"})
        registry.write_text("\n".join([*kept, dead]) + "\n", encoding="utf-8")
        TestThePruneLedgerIsBounded._short_writes(monkeypatch, tmp_path)
        assert _prune_registry_dead_teams(registry_path=registry, teams_dir=teams) == 1
        assert registry.read_text(encoding="utf-8").splitlines() == kept


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
        # A store whose vector table is current: the read's catch-up runs.
        # Postponing it is TestTheCatchUpOnASessionThatStartsWithARead's.
        monkeypatch.setattr(memory_init, "_read_can_catch_up", lambda: True)
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

        with pytest.MonkeyPatch.context() as patch:
            for name in ("write", "ftruncate", "truncate", "fsync", "replace", "rename"):
                patch.setattr(os, name, observed(getattr(os, name)))
            session_end._append_ledger_line(slug, line("trigger"))

        final = memory_api._pruned_session_records(ledger)
        assert ledger.stat().st_size <= session_end._PRUNE_LEDGER_KEEP_BYTES
        assert final[-1]["session_id"] == "trigger"
        assert len(seen) >= 4
        assert min(len(records) for records in seen) >= len(final) - 1
        assert [p.name for p in slug.iterdir()] == ["pruned-sessions.jsonl"]
        assert ledger.stat().st_mode & 0o777 == 0o600

    @staticmethod
    def _short_writes(monkeypatch, under, limit=7):
        """`os.write` writes at most `limit` bytes per call to a file under
        `under`, as a write interrupted by a signal or a full disk may."""
        import os

        real = os.write

        def write(fd, data):
            try:
                target = os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                target = ""
            if target.startswith(str(under)):
                return real(fd, bytes(data[:limit]))
            return real(fd, data)

        monkeypatch.setattr(os, "write", write)

    @pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
    def test_a_short_write_still_appends_the_whole_line(self, tmp_path, monkeypatch):
        """Catches the append writing once and keeping whatever `os.write`
        accepted: a short write left a truncated, unparseable line."""
        import session_end

        slug = tmp_path / "slug"
        slug.mkdir()
        line = json.dumps({"session_id": "short", "project_dir": "/x",
                           "started_at": "2026-01-01T00:00:00+00:00"}) + "\n"
        self._short_writes(monkeypatch, tmp_path)
        session_end._append_ledger_line(slug, line)
        assert (slug / "pruned-sessions.jsonl").read_text() == line

    @pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
    def test_a_short_write_still_cuts_back_to_whole_lines(self, tmp_path, monkeypatch):
        """The cut-back writes its kept window through the same loop: every
        kept line is whole and the new line is last."""
        import session_end
        from scripts import memory_api

        slug = tmp_path / "slug"
        ledger, line = self._ledger_one_line_below_compaction(slug)
        self._short_writes(monkeypatch, tmp_path, limit=4093)
        session_end._append_ledger_line(slug, line("trigger"))
        data = ledger.read_bytes()
        assert data.endswith(line("trigger").encode())
        assert all(json.loads(item) for item in data.splitlines())
        assert memory_api._pruned_session_records(ledger)[-1]["session_id"] == "trigger"

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

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(session_end.fcntl, "flock", flock)
            session_end._append_ledger_line(slug, line("late"))

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

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "replace", refuse)
            session_end._append_ledger_line(slug, line("trigger"))

        assert [p.name for p in slug.iterdir()] == ["pruned-sessions.jsonl"]
        ids = [json.loads(item)["session_id"] for item in ledger.read_bytes().splitlines()]
        assert ids[0] == "s000000"
        assert ids[-1] == "trigger"

    def test_the_next_cut_back_removes_a_crashed_cut_backs_file(self, tmp_path):
        """Catches a crash between creating the cut-back's new file and the
        rename leaving that file in the slug folder for good. Only an exact
        '.pruned-sessions.jsonl.' + 8 lowercase hex regular file goes; the
        ledger, session folders, near-miss names and links stay."""
        import os

        import session_end

        slug = tmp_path / "slug"
        ledger, line = self._ledger_one_line_below_compaction(slug)
        leftover = slug / ".pruned-sessions.jsonl.0a1b2c3d"
        leftover.write_bytes(b'{"session_id": "half"}\n')
        kept = {
            ".pruned-sessions.jsonl.0a1b2c3": b"seven hex",
            ".pruned-sessions.jsonl.0A1B2C3D": b"upper case",
            ".pruned-sessions.jsonl.0a1b2c3d9": b"nine hex",
            ".pruned-sessions.jsonl.0a1b2c3d.bak": b"suffix",
            "pruned-sessions.jsonl.0a1b2c3d": b"no leading dot",
            ".other.jsonl.0a1b2c3d": b"other prefix",
            "notes.txt": b"unrelated",
        }
        for name, data in kept.items():
            (slug / name).write_bytes(data)
        (slug / "session-a").mkdir()
        (slug / ".pruned-sessions.jsonl.deadbeef").mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"target")
        os.symlink(outside, slug / ".pruned-sessions.jsonl.feedface")

        session_end._append_ledger_line(slug, line("trigger"))

        assert ledger.stat().st_size <= session_end._PRUNE_LEDGER_KEEP_BYTES
        assert json.loads(ledger.read_bytes().splitlines()[-1])["session_id"] == "trigger"
        assert not leftover.exists()
        assert {name: (slug / name).read_bytes() for name in kept} == kept
        assert (slug / "session-a").is_dir()
        assert (slug / ".pruned-sessions.jsonl.deadbeef").is_dir()
        assert os.path.islink(slug / ".pruned-sessions.jsonl.feedface")
        assert outside.read_bytes() == b"target"
        assert sorted(p.name for p in slug.iterdir()) == sorted(
            [*kept, "pruned-sessions.jsonl", "session-a",
             ".pruned-sessions.jsonl.deadbeef", ".pruned-sessions.jsonl.feedface"]
        )

    def test_a_cut_back_in_flight_keeps_its_file_from_another_writer(
        self, tmp_path, monkeypatch
    ):
        """Control: the removal runs only under the ledger's lock, so a second
        writer that would cut back too waits while a cut-back's new file is
        being written, and that file is not removed under it. Synchronized on
        the second writer's first refused lock attempt (an event), not on a
        timed wait, and that writer retries until the lock is released, so
        load cannot make it give up and drop its line."""
        import os
        import threading
        import time as real_time
        import types

        import session_end

        slug = tmp_path / "slug"
        ledger, line = self._ledger_one_line_below_compaction(slug)
        main = threading.get_ident()
        real_fsync = os.fsync
        blocked = threading.Event()
        seen = {}

        def sleep(seconds):
            # The bounded lock retry sleeps only after a refused attempt.
            if threading.current_thread().name == "other-writer":
                blocked.set()
            real_time.sleep(min(seconds, 0.01))

        def fsync(fd):
            if threading.get_ident() == main and "scratch" not in seen:
                scratch = [
                    p for p in slug.iterdir() if p.name.startswith(".pruned-sessions.jsonl.")
                ]
                seen["scratch"] = scratch
                other = threading.Thread(
                    target=session_end._append_ledger_line,
                    args=(slug, line("other") * 6000),
                    name="other-writer",
                )
                other.start()
                seen["other_waited"] = blocked.wait(30) and other.is_alive()
                seen["scratch_kept"] = all(p.exists() for p in scratch)
                seen["other"] = other
            return real_fsync(fd)

        clock = types.SimpleNamespace(**{
            name: getattr(real_time, name) for name in dir(real_time) if not name.startswith("_")
        })
        clock.sleep = sleep
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "fsync", fsync)
            patch.setattr(session_end, "time", clock)
            patch.setattr(session_end, "_LEDGER_LOCK_ATTEMPTS", 1_000_000)
            session_end._append_ledger_line(slug, line("trigger"))
            seen["other"].join(30)

        assert len(seen["scratch"]) == 1
        assert seen["other_waited"] is True
        assert seen["scratch_kept"] is True
        assert not seen["other"].is_alive()
        # The second writer's own cut-back ran after the first released the
        # lock: the ledger is its whole lines, ending with its line.
        assert ledger.stat().st_size <= session_end._PRUNE_LEDGER_KEEP_BYTES
        ids = [json.loads(item)["session_id"] for item in ledger.read_bytes().splitlines()]
        assert ids[-1] == "other"
        assert [p.name for p in slug.iterdir()] == ["pruned-sessions.jsonl"]
