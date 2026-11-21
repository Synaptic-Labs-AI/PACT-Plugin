"""The union read scope's normalisation, and one path guard beside it.

Location: pact-plugin/tests/test_remediation_store_scope.py.

`database.normalise_project_scopes` and `search._scope_or_none` normalise the
scope every store, graph and search seam reads
(`skills/pact-memory/scripts/{database,graph,search}.py`). These arms pin:

- a repeated scope id is kept once, so a per-scope loop cannot return one
  memory twice;
- an empty-string scope means NO ROWS at every layer, never the whole store;
- `_harden_sidecars` never treats an empty path as a file in the cwd.

Each arm names the defect it would catch. Every store is a scratch file under
pytest's `tmp_path`.
"""

from __future__ import annotations

import os
import struct
from unittest.mock import patch

import pytest
from helpers import create_test_schema
from scripts.database import normalise_project_scopes
from scripts.database import sqlite3 as db_sqlite3
from scripts.graph import get_graph_stats

from scripts import database as db
from scripts import graph, search

SCOPE = "github.com/syn-labs/pact-plugin"


def _insert_memory(conn, memory_id, project):
    conn.execute(
        "INSERT INTO memories "
        "(id, context, project_id, session_id, created_at, updated_at) "
        "VALUES (?, 'c', ?, 's', '2026-01-01T00:00:00+00:00', "
        "'2026-01-01T00:00:00+00:00')",
        (memory_id, project),
    )


class TestARepeatedScopeIsKeptOnce:
    def test_the_normaliser_dedupes_in_first_seen_order(self):
        """Catches a union that carries an id twice.

        RED WHEN duplicates survive, or the first-seen order changes (the
        order is the per-scope query order).
        """
        assert normalise_project_scopes((SCOPE, "b", SCOPE, "b")) == (SCOPE, "b")

    def test_vector_search_returns_each_memory_once_for_a_repeated_scope(
        self, tmp_path
    ):
        """Catches the per-scope KNN loop querying a repeated scope twice.

        `vector_search` runs one KNN per scope and merges, so `(A, A)` used to
        return every hit twice. RED WHEN the normaliser stops deduplicating.
        """
        sqlite_vec = pytest.importorskip("sqlite_vec")
        if not db.SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 build cannot load extensions")
        conn = db_sqlite3.connect(str(tmp_path / "vec.db"))
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[4])"
        )
        for memory_id, vector in (("m1", (1.0, 0.0, 0.0, 0.0)), ("m2", (0.0, 1.0, 0.0, 0.0))):
            _insert_memory(conn, memory_id, SCOPE)
            conn.execute(
                "INSERT INTO vec_memories (memory_id, project_id, embedding) "
                "VALUES (?, ?, ?)",
                (memory_id, SCOPE, struct.pack("4f", *vector)),
            )
        conn.commit()
        try:
            with patch.object(
                search, "generate_embedding", lambda text: [1.0, 0.0, 0.0, 0.0]
            ):
                hits = search.vector_search(conn, "q", (SCOPE, SCOPE), limit=10)
        finally:
            conn.close()
        assert [memory_id for memory_id, _distance in hits] == ["m1", "m2"]


class TestAnEmptyStringIsNoRowsAtEveryLayer:
    def test_search_store_and_graph_agree_that_an_empty_string_is_empty(
        self, tmp_path, monkeypatch
    ):
        """Catches one input with two meanings across sibling seams.

        `graph.get_graph_stats(conn, "")` and the store layer already answered
        zero for `""`, while `search._scope_or_none("")` returned `None`, the
        whole-store scope. RED WHEN any layer reads `""` as the whole store.
        The `None` calls are the control that the rows are reachable.
        """
        # The schema is built here; the initialiser would also size a vector
        # table from the embedding service, which this arm does not need.
        monkeypatch.setattr(graph, "ensure_initialized", lambda conn: None)
        conn = db_sqlite3.connect(str(tmp_path / "scope.db"))
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        _insert_memory(conn, "m-null", None)
        conn.commit()
        try:
            assert search._scope_or_none("") == ()
            assert normalise_project_scopes("") == ()
            assert get_graph_stats(conn, "")["memories"] == 0
            assert search._scope_or_none(None) is None
            assert get_graph_stats(conn, None)["memories"] == 1
        finally:
            conn.close()


class TestAnEmptyPathIsNoPath:
    def test_init_schema_on_an_in_memory_store_leaves_cwd_sidecars_alone(
        self, tmp_path, monkeypatch
    ):
        """Catches `_harden_sidecars("")` chmodding `./-wal` and `./-shm`.

        `PRAGMA database_list` reports `""` for an in-memory database, and
        `"" + "-wal"` names a file in the current directory. RED WHEN the
        guard tests `is None` instead of emptiness: the two files below are
        then changed to 0600.
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(db, "_init_vector_table", lambda conn: False)
        for name in ("-wal", "-shm"):
            (tmp_path / name).write_text("not a sidecar")
            os.chmod(tmp_path / name, 0o644)
        conn = db_sqlite3.connect(":memory:")
        try:
            db.init_schema(conn)
        finally:
            conn.close()
        for name in ("-wal", "-shm"):
            assert (tmp_path / name).stat().st_mode & 0o777 == 0o644, name


# ---------------------------------------------------------------------------
# The submodule exemption is anchored on the common dir git verified
# ---------------------------------------------------------------------------

def _gate_store(tmp_path, bucket, path):
    """A store holding one memory and one absolute file row for `bucket`."""
    from scripts.database import db_connection, ensure_initialized

    store = tmp_path / "gate.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _insert_memory(conn, "m1", bucket)
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES ('f1', ?, ?)",
            (str(path), bucket),
        )
        conn.commit()
    return store


def _gate(store, bucket, roots, **kwargs):
    from scripts.database import db_connection, legacy_candidate_plausibility

    with db_connection(store) as conn:
        return legacy_candidate_plausibility(conn, bucket, roots, **kwargs)


def _git(*args, cwd):
    from fixtures.identity_git import run_git

    return run_git(*args, cwd=cwd)


def _git_common_dir(root):
    """The common dir git itself reports for `root`, as the caller passes it."""
    return _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)


def _read_scope(monkeypatch, cwd, store):
    """`_read_project_scopes` from `cwd`, with fresh caches, and its state."""
    from scripts.memory_api import PACTMemory

    from scripts import memory_api

    for name in (
        "_LEGACY_SCOPE_CACHE", "_EXCLUSION_WARNED", "_CASE_VARIANT_WARNED",
        "_UNPROVEN_WARNED", "_SUBMODULE_SUPPRESSED",
    ):
        memo = getattr(memory_api, name, None)
        if memo is not None:
            memo.clear()
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(cwd)
    memory = PACTMemory(session_id="s", db_path=store)
    return memory._read_project_scopes(), memory._legacy_scope_state


def _add_submodule(superproject, source, where="vendor/lib"):
    _git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
        str(source), where, cwd=superproject,
    )


@pytest.fixture
def outside_repo(tmp_path):
    from fixtures.identity_git import assert_outside_repository

    assert_outside_repository(tmp_path)
    return tmp_path


class TestTheSubmoduleExemptionTrustsOnlyGitsCommonDir:
    """A nested `.git` file is the root's own submodule only when it names a
    git dir under the root's common dir AS GIT REPORTS IT, which the caller
    passes as `root_common`; the gate never derives it from the root's files.

    Catches the exemption anchored on a common dir re-derived from the root's
    own `.git` and `commondir` files: that parser accepts shapes git refuses,
    and misses shapes git reads, so it can exempt a nested checkout git would
    never place inside the root, or drop a real submodule's rows.
    """

    def test_a_root_git_refuses_exempts_nothing(self, outside_repo):
        """The root's `.git` reads `gitdir:<path>` with no space after the
        colon. git refuses it ("invalid gitfile format"), while a file parser
        accepts it and takes `<path>/modules/` as the exemption prefix. The
        identity resolver declines this root upstream (no project id), so the
        read scope never reaches the gate with it; the arm calls the gate
        directly, as a caller with no verified common dir does.
        RED WHEN the gate re-derives the common dir from the root's files:
        the nested checkout's row then verifies the bucket."""
        from fixtures.identity_git import make_repo
        from scripts.database import LEGACY_PLAUSIBLE_EXCLUDED

        donor = make_repo(outside_repo / "donor")
        git_dir = donor / ".git"
        root = outside_repo / "proj"
        root.mkdir()
        (root / ".git").write_text(f"gitdir:{git_dir}\n", encoding="utf-8")
        nested = root / "vendor" / "lib"
        nested.mkdir(parents=True)
        (git_dir / "modules" / "lib").mkdir(parents=True)
        (nested / ".git").write_text(
            f"gitdir: {git_dir / 'modules' / 'lib'}\n", encoding="utf-8"
        )
        store = _gate_store(outside_repo, "proj", nested / "x.py")
        outcome, detail = _gate(store, "proj", [root])
        assert outcome == LEGACY_PLAUSIBLE_EXCLUDED, detail

    def test_a_symlinked_git_dir_keeps_its_submodule_rows(
        self, outside_repo, monkeypatch
    ):
        """End to end: the root's `.git` is a SYMLINK to a git dir kept
        elsewhere. git reads it and the resolver resolves the root, but a file
        parser reads the symlinked directory as a gitfile and gets nothing,
        so the superproject's own submodule rows were excluded and its legacy
        bucket dropped from reads. RED WHEN the caller does not pass git's
        common dir, or the gate ignores it."""
        from fixtures.identity_git import make_repo

        stash = make_repo(outside_repo / "tmp", remote="https://github.com/o/sym.git")
        git_dir = outside_repo / "sym" / "store.git"
        git_dir.parent.mkdir()
        (stash / ".git").rename(git_dir)
        root = outside_repo / "sym" / "proj"
        root.mkdir()
        os.symlink(git_dir, root / ".git")
        nested = root / "vendor" / "lib"
        nested.mkdir(parents=True)
        (git_dir / "modules" / "lib").mkdir(parents=True)
        (nested / ".git").write_text(
            f"gitdir: {git_dir / 'modules' / 'lib'}\n", encoding="utf-8"
        )
        store = _gate_store(outside_repo, "sym", nested / "x.py")
        scopes, state = _read_scope(monkeypatch, root, store)
        assert scopes == ("github.com/o/sym", "sym"), state
        assert state["verified"] is True, state

    def test_a_nested_repository_is_never_exempt_even_with_a_common_dir(
        self, outside_repo
    ):
        """Control: a nested `.git` DIRECTORY is a separate checkout whatever
        the root's common dir is. RED WHEN supplying `root_common` widens the
        exemption past gitfiles under the root's modules."""
        from fixtures.identity_git import make_repo
        from scripts.database import LEGACY_PLAUSIBLE_EXCLUDED

        root = make_repo(outside_repo / "proj")
        nested = make_repo(root / "vendor" / "other")
        store = _gate_store(outside_repo, "proj", nested / "x.py")
        outcome, detail = _gate(
            store, "proj", [root], root_common=_git_common_dir(root)
        )
        assert outcome == LEGACY_PLAUSIBLE_EXCLUDED, detail

    def test_the_read_scope_passes_gits_common_dir_for_a_real_submodule(
        self, outside_repo, monkeypatch
    ):
        """End to end: a superproject whose legacy bucket's only absolute row
        sits in its own registered submodule is read as verified. RED WHEN the
        caller stops passing the verified common dir (the gate then fails
        closed and excludes the bucket)."""
        from fixtures.identity_git import make_repo

        source = make_repo(outside_repo / "subsrc")
        superproject = make_repo(
            outside_repo / "super", remote="https://github.com/o/super.git"
        )
        _add_submodule(superproject, source)
        store = _gate_store(outside_repo, "super", superproject / "vendor" / "lib" / "x.py")
        scopes, state = _read_scope(monkeypatch, superproject, store)
        assert scopes == ("github.com/o/super", "super"), state
        assert state["verified"] is True, state


class TestALinkedWorktreesSubmoduleIsItsOwn:
    """git keeps a linked worktree's submodule git dirs in THAT worktree's
    admin dir, `<common>/worktrees/<admin>/modules/`, not `<common>/modules/`.

    Catches the exemption accepting only `<common>/modules/`: a superproject
    row saved inside a submodule of one of its linked worktrees was treated as
    a separate checkout's, so the superproject's own legacy bucket was
    excluded from reads in both the main checkout and the worktree.
    """

    def _superproject_with_worktree_submodule(self, root):
        from fixtures.identity_git import make_repo, make_worktree

        source = make_repo(root / "subsrc")
        superproject = make_repo(root / "super", remote="https://github.com/o/super.git")
        _add_submodule(superproject, source)
        _git("commit", "-q", "-m", "add submodule", cwd=superproject)
        worktree = make_worktree(superproject, root / "wt")
        _git(
            "-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q",
            cwd=worktree,
        )
        gitfile = (worktree / "vendor" / "lib" / ".git").read_text(encoding="utf-8")
        assert "/worktrees/wt/modules/" in gitfile, (
            f"precondition: git placed the submodule in the worktree's admin dir: {gitfile!r}"
        )
        return superproject, worktree

    @pytest.mark.parametrize("cwd_name", ["super", "wt"])
    def test_the_worktree_submodules_rows_stay_the_superprojects(
        self, outside_repo, monkeypatch, cwd_name
    ):
        """RED WHEN the exemption drops its `worktrees/<admin>/modules/`
        branch: the bucket is excluded from both checkouts' reads."""
        _superproject, worktree = self._superproject_with_worktree_submodule(outside_repo)
        store = _gate_store(outside_repo, "super", worktree / "vendor" / "lib" / "x.py")
        scopes, state = _read_scope(monkeypatch, outside_repo / cwd_name, store)
        assert scopes == ("github.com/o/super", "super"), state
        assert state["verified"] is True, state

    def test_another_roots_worktree_modules_do_not_exempt(self, outside_repo):
        """The worktree branch is per root: a gitfile under the MAIN checkout
        naming the worktree's admin modules is not the main checkout's
        submodule. RED WHEN the branch accepts any `worktrees/<name>/modules/`
        without checking that `<name>` is the admin dir of the root it
        guards."""
        from scripts.database import LEGACY_PLAUSIBLE_EXCLUDED

        superproject, _worktree = self._superproject_with_worktree_submodule(outside_repo)
        common = _git_common_dir(superproject)
        stray = superproject / "stray"
        stray.mkdir()
        (stray / ".git").write_text(
            f"gitdir: {common}/worktrees/wt/modules/vendor/lib\n", encoding="utf-8"
        )
        store = _gate_store(outside_repo, "super", stray / "x.py")
        outcome, detail = _gate(store, "super", [superproject], root_common=common)
        assert outcome == LEGACY_PLAUSIBLE_EXCLUDED, detail
