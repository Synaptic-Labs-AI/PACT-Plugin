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

Each arm names the defect it would catch. Every store is a scratch file under
pytest's `tmp_path`.
"""

from __future__ import annotations

import pytest
from helpers import create_test_schema
from scripts.database import rekey_project
from scripts.database import sqlite3 as db_sqlite3

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
        """Catches cleanup that replaces the real error.

        `RAISE(ROLLBACK, ...)` makes SQLite end the whole transaction itself,
        so the savepoint the cleanup would roll back to no longer exists. RED
        WHEN the cleanup runs unguarded: the caller then sees "no such
        savepoint" instead of the trigger's error.
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


class TestTheCounters:
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
