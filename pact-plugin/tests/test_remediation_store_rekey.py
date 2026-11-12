"""`rekey_project`'s transaction, schema and counter contract.

Location: pact-plugin/tests/test_remediation_store_rekey.py.

The rekey is the only row-rewriting surface in the memory store
(`skills/pact-memory/scripts/database.py::rekey_project`; the CLI verb is
`project-id rekey` in `cli.py`). These arms pin what it promises a caller:

- it owns its transaction and refuses a connection that already has one;
- it never creates a schema, in a dry run or an apply;
- a failure propagates as itself, never as a cleanup error;
- every counter key is always present, NULL-project rows are reported per
  table, and an empty source bucket is reported as such.

It also pins the identity READ primitives beside it (`count_project_memories`,
`project_bucket_counts`, `bucket_session_ids`, `legacy_candidate_plausibility`
and `graph.count_project_file_paths`): they never run the write-path
initialiser, so a read-only probe cannot attempt DDL through them.

Each arm names the defect it would catch. Every store is a scratch file under
pytest's `tmp_path`.
"""

from __future__ import annotations

import pytest
from helpers import create_test_schema
from scripts.database import (
    bucket_session_ids,
    count_project_memories,
    legacy_candidate_plausibility,
    project_bucket_counts,
    rekey_project,
)
from scripts.database import sqlite3 as db_sqlite3
from scripts.graph import count_project_file_paths

from scripts import database as db
from scripts import graph

CANONICAL = "github.com/syn-labs/pact-plugin"
LEGACY = "PACT-prompt"

COUNTER_KEYS = {
    "from",
    "to",
    "dry_run",
    "memories",
    "files_in_place",
    "files_conflicted",
    "links_repointed",
    "links_conflicted",
    "relations_repointed",
    "vectors_moved",
    "vectors_pending",
    "null_project_memories",
    "null_project_files",
    "from_bucket_empty",
    "schema_absent",
}


def _store(tmp_path, name="rekey.db"):
    """A scratch store with the schema and no rows; returns (path, conn)."""
    path = tmp_path / name
    conn = db_sqlite3.connect(str(path))
    conn.row_factory = db_sqlite3.Row
    create_test_schema(conn)
    return path, conn


def _memory(conn, memory_id, project):
    conn.execute(
        "INSERT INTO memories "
        "(id, context, project_id, session_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 's', '2026-01-01T00:00:00+00:00', "
        "'2026-01-01T00:00:00+00:00')",
        (memory_id, f"context {memory_id}", project),
    )


def _file(conn, file_id, path, project):
    conn.execute(
        "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
        (file_id, path, project),
    )


def _link(conn, memory_id, file_id):
    conn.execute(
        "INSERT INTO memory_files (memory_id, file_id) VALUES (?, ?)",
        (memory_id, file_id),
    )


def _projects(conn):
    return sorted(
        (row[0], row[1])
        for row in conn.execute("SELECT id, project_id FROM memories")
    )


class TestTheRekeyOwnsItsTransaction:
    def test_an_open_caller_transaction_is_refused_and_left_to_the_caller(
        self, tmp_path
    ):
        """Catches a rekey that commits a caller's pending writes.

        The initialiser the rekey used to call committed whatever the caller
        had open, so a caller's later ROLLBACK undid nothing. RED WHEN the
        refusal is removed (the move then commits the caller's row) or the
        refusal comes after a commit.
        """
        path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        conn.commit()

        conn.execute("BEGIN")
        _memory(conn, "caller-row", "other")
        with pytest.raises(ValueError, match="owns its transaction"):
            rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        conn.rollback()
        conn.close()

        reopened = db_sqlite3.connect(str(path))
        try:
            assert _projects(reopened) == [("m0", LEGACY)], (
                "the caller's uncommitted row survived its own ROLLBACK, or "
                "the move ran inside the caller's transaction"
            )
        finally:
            reopened.close()

    @pytest.mark.parametrize("dry_run", [True, False])
    def test_a_dry_run_and_an_apply_both_end_with_no_open_transaction(
        self, tmp_path, dry_run
    ):
        """Catches a rekey that leaves its own transaction open.

        RED WHEN the apply relies on a caller to commit, or the dry run's
        savepoint is not released.
        """
        _path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        conn.commit()
        try:
            rekey_project(conn, LEGACY, CANONICAL, dry_run=dry_run)
            assert not conn.in_transaction
        finally:
            conn.close()


class TestTheRekeyNeverCreatesASchema:
    @pytest.mark.parametrize("dry_run", [True, False])
    def test_a_zero_byte_store_stays_zero_bytes(self, tmp_path, dry_run):
        """Catches a rekey that initialises the store it is planning against.

        Measured before the fix: a default dry run grew a 0-byte store to a
        full 100 KB schema. RED WHEN `ensure_initialized` (or any DDL) runs
        before the schema check.
        """
        path = tmp_path / "empty.db"
        path.write_bytes(b"")
        conn = db_sqlite3.connect(str(path))
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=dry_run)
        finally:
            conn.close()
        assert path.stat().st_size == 0
        assert counters["schema_absent"] is True
        assert counters["from_bucket_empty"] is True
        assert counters["memories"] == 0
        assert set(counters) == COUNTER_KEYS


class TestAFailureKeepsItsOwnError:
    @pytest.mark.parametrize("dry_run", [True, False])
    def test_a_statement_that_rolls_sqlite_back_propagates_as_itself(
        self, tmp_path, dry_run
    ):
        """Catches cleanup that replaces the real error, and a partial move.

        `RAISE(ROLLBACK, ...)` makes SQLite end the whole transaction itself,
        so the savepoint the cleanup would roll back to no longer exists.

        The two cases pin different things. `[True]` (dry run) is RED WHEN
        the outer cleanup runs unguarded: `ROLLBACK TO` the vanished
        savepoint raises "no such savepoint" instead of the trigger's error.
        `[False]` (apply) cannot red on that guard, because
        `conn.rollback()` with no open transaction is a no-op; it pins only
        that the apply's failure propagates as itself and leaves no partial
        move (RED WHEN a memory row stays moved).
        """
        path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        _file(conn, "f0", "src/a.py", LEGACY)
        conn.execute(
            "CREATE TRIGGER refuse_move BEFORE UPDATE ON files "
            "BEGIN SELECT RAISE(ROLLBACK, 'the trigger refused the move'); END"
        )
        conn.commit()
        try:
            with pytest.raises(db_sqlite3.IntegrityError) as excinfo:
                rekey_project(conn, LEGACY, CANONICAL, dry_run=dry_run)
            assert "the trigger refused the move" in str(excinfo.value)
            assert not conn.in_transaction
            assert _projects(conn) == [("m0", LEGACY)], (
                "a failed rekey left a partial move"
            )
        finally:
            conn.close()


    @pytest.mark.parametrize("dry_run", [True, False])
    def test_a_vector_move_that_rolls_sqlite_back_propagates_as_itself(
        self, tmp_path, dry_run
    ):
        """Catches the per-vector cleanup masking an error that ended the move.

        The vector leg wraps each memory in its own savepoint and, on a
        failure, rolls back to it and reports the vector pending. When the
        failure has already made SQLite roll the WHOLE transaction back, that
        savepoint is gone: the move is undone, so the loop must stop and the
        original error must propagate. The connection below issues a real
        `ROLLBACK` and then raises inside the vector INSERT. RED WHEN the
        `if not conn.in_transaction: raise` guard in the vector loop is
        removed: `ROLLBACK TO rekey_vector` then raises "no such savepoint"
        and replaces the injected error.
        """
        sqlite_vec = pytest.importorskip("sqlite_vec")
        if not db.SQLITE_EXTENSIONS_ENABLED:
            pytest.skip("this sqlite3 build cannot load extensions")

        class RollingBackConnection(db_sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("INSERT INTO vec_memories"):
                    super().execute("ROLLBACK")
                    raise db_sqlite3.OperationalError("injected vector failure")
                return super().execute(sql, *args)

        path = tmp_path / "vectors.db"
        conn = db_sqlite3.connect(str(path), factory=RollingBackConnection)
        conn.row_factory = db_sqlite3.Row
        create_test_schema(conn)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute(
            "CREATE VIRTUAL TABLE vec_memories USING vec0("
            "memory_id TEXT PRIMARY KEY, project_id TEXT PARTITION KEY, "
            "embedding float[3])"
        )
        _memory(conn, "m0", LEGACY)
        db_sqlite3.Connection.execute(
            conn,
            "INSERT INTO vec_memories (memory_id, project_id, embedding) "
            "VALUES (?, ?, ?)",
            ("m0", LEGACY, sqlite_vec.serialize_float32([0.1, 0.2, 0.3])),
        )
        conn.commit()
        try:
            with pytest.raises(
                db_sqlite3.OperationalError, match="injected vector failure"
            ):
                rekey_project(conn, LEGACY, CANONICAL, dry_run=dry_run)
            assert not conn.in_transaction
            assert _projects(conn) == [("m0", LEGACY)], (
                "the vector failure ended the transaction, but the move "
                "continued or partly committed"
            )
        finally:
            conn.close()


class TestTheCounters:
    def test_a_no_op_still_reports_the_null_project_rows(self, tmp_path):
        """Catches NULL counts that read 0 on the no-op path.

        `from_id == to_id` returns before any move, and the NULL counts used
        to be taken only after the move, so a no-op reported 0 whatever the
        store held. RED WHEN the counts move back after the no-op return.
        """
        _path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        _memory(conn, "n1", None)
        _file(conn, "fn", "src/null.py", None)
        conn.commit()
        try:
            counters = rekey_project(conn, LEGACY, LEGACY, dry_run=False)
        finally:
            conn.close()
        assert counters["null_project_memories"] == 1
        assert counters["null_project_files"] == 1
        assert counters["memories"] == 0

    def test_every_key_is_present_on_an_apply_and_on_a_no_op(self, tmp_path):
        """Catches a counter that appears only on some outcomes.

        The CLI and SKILL.md read these keys by name. RED WHEN a key is added
        on one path only.
        """
        _path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        conn.commit()
        try:
            noop = rekey_project(conn, LEGACY, LEGACY, dry_run=False)
            applied = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
        finally:
            conn.close()
        assert set(noop) == COUNTER_KEYS
        assert set(applied) == COUNTER_KEYS
        assert applied["schema_absent"] is False

    def test_null_project_memories_and_files_are_counted_separately(
        self, tmp_path
    ):
        """Catches one counter summing two tables.

        Two NULL memories and one NULL file used to report a single `3`,
        which reads as three of either. RED WHEN the two counts are summed
        or swapped.
        """
        _path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        _memory(conn, "n1", None)
        _memory(conn, "n2", None)
        _file(conn, "fn", "src/null.py", None)
        conn.commit()
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=True)
        finally:
            conn.close()
        assert counters["null_project_memories"] == 2
        assert counters["null_project_files"] == 1
        assert "null_project_rows" not in counters

    def test_an_absent_source_bucket_is_reported_empty(self, tmp_path):
        """Catches a typo'd `--from` that reads as a successful move.

        RED WHEN `from_bucket_empty` is missing, or True for a populated
        bucket.
        """
        _path, conn = _store(tmp_path)
        _memory(conn, "m0", LEGACY)
        conn.commit()
        try:
            absent = rekey_project(conn, "no-such-bucket", CANONICAL, dry_run=True)
            present = rekey_project(conn, LEGACY, CANONICAL, dry_run=True)
        finally:
            conn.close()
        assert absent["from_bucket_empty"] is True
        assert absent["memories"] == 0
        assert absent["files_in_place"] == 0
        assert present["from_bucket_empty"] is False

    def test_a_file_only_bucket_is_not_empty(self, tmp_path):
        """Catches an emptiness test that looks at memories alone.

        RED WHEN `from_bucket_empty` ignores file rows.
        """
        _path, conn = _store(tmp_path)
        _file(conn, "f0", "src/a.py", LEGACY)
        conn.commit()
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=True)
        finally:
            conn.close()
        assert counters["from_bucket_empty"] is False
        assert counters["files_in_place"] == 1

    def test_links_repointed_counts_only_links_on_merged_file_rows(
        self, tmp_path
    ):
        """Catches a description of `links_repointed` the counter does not match.

        A file moved in place keeps its links (they follow the file id), so
        only links on a file row MERGED into an existing target row are
        re-pointed. One in-place file with 2 links and one conflicting file
        with 1 link report 1. RED WHEN in-place links are counted.
        """
        _path, conn = _store(tmp_path)
        for memory_id in ("m0", "m1", "m2"):
            _memory(conn, memory_id, LEGACY)
        _file(conn, "in-place", "src/only-legacy.py", LEGACY)
        _link(conn, "m0", "in-place")
        _link(conn, "m1", "in-place")
        _file(conn, "old", "src/both.py", LEGACY)
        _file(conn, "target", "src/both.py", CANONICAL)
        _link(conn, "m2", "old")
        conn.commit()
        try:
            counters = rekey_project(conn, LEGACY, CANONICAL, dry_run=False)
            links = sorted(
                (row[0], row[1])
                for row in conn.execute("SELECT memory_id, file_id FROM memory_files")
            )
        finally:
            conn.close()
        assert counters["files_in_place"] == 1
        assert counters["files_conflicted"] == 1
        assert counters["links_repointed"] == 1
        assert links == [("m0", "in-place"), ("m1", "in-place"), ("m2", "target")]


class TestIdentityReadPrimitivesNeverInitialise:
    """The identity read primitives run against read-only probes, so they must
    never call the write-path initialiser. Their precondition is a present
    schema, and on a schema-less store they raise instead of attempting DDL.
    """

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda conn: count_project_memories(conn, LEGACY), id="count_project_memories"),
            pytest.param(project_bucket_counts, id="project_bucket_counts"),
            pytest.param(lambda conn: bucket_session_ids(conn, LEGACY), id="bucket_session_ids"),
            pytest.param(
                lambda conn: legacy_candidate_plausibility(conn, LEGACY, "/tmp"),
                id="legacy_candidate_plausibility",
            ),
            pytest.param(lambda conn: count_project_file_paths(conn, LEGACY), id="count_project_file_paths"),
        ],
    )
    def test_a_schema_less_store_raises_and_is_not_written(
        self, tmp_path, monkeypatch, call
    ):
        """Catches a primitive that initialises the store it is reading.

        Measured before the fix: `project-id show` printed "Could not create
        vector table" six times and loaded the embedding service six times,
        once per primitive, because each one re-ran `ensure_initialized`.
        RED WHEN a primitive calls the initialiser again: the spy records the
        call, and on this read-only 0-byte store the initialiser's DDL would
        be refused with a READONLY error instead of the missing-table error
        this arm expects.
        """
        initialiser_calls = []
        monkeypatch.setattr(
            db, "ensure_initialized", lambda conn: initialiser_calls.append(conn)
        )
        monkeypatch.setattr(
            graph, "ensure_initialized", lambda conn: initialiser_calls.append(conn)
        )
        path = tmp_path / "empty.db"
        path.write_bytes(b"")
        conn = db_sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = db_sqlite3.Row
        try:
            with pytest.raises(db_sqlite3.OperationalError, match="no such table"):
                call(conn)
        finally:
            conn.close()
        assert initialiser_calls == []
        assert path.stat().st_size == 0


class TestBucketSessionIds:
    def test_every_writing_session_is_listed_newest_first_and_scoped(self, tmp_path):
        """Catches an evidence list that is unscoped, unordered or carries
        sessions no record can be found for.

        RED WHEN another bucket's session appears, a NULL or empty session id
        is returned, a session repeats, or the order is not newest first.
        """
        _path, conn = _store(tmp_path)
        rows = [
            ("a1", LEGACY, "old-session", "2026-01-01T00:00:00+00:00"),
            ("a2", LEGACY, "new-session", "2026-03-01T00:00:00+00:00"),
            ("a3", LEGACY, "old-session", "2026-02-01T00:00:00+00:00"),
            ("a4", LEGACY, None, "2026-04-01T00:00:00+00:00"),
            ("a5", LEGACY, "", "2026-04-02T00:00:00+00:00"),
            ("b1", "other", "foreign-session", "2026-05-01T00:00:00+00:00"),
        ]
        for memory_id, project, session, created in rows:
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'c', ?, ?, ?, ?)",
                (memory_id, project, session, created, created),
            )
        conn.commit()
        try:
            assert bucket_session_ids(conn, LEGACY) == ["new-session", "old-session"]
            assert bucket_session_ids(conn, "no-such-bucket") == []
        finally:
            conn.close()
