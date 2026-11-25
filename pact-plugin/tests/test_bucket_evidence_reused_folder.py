"""
Location: pact-plugin/tests/test_bucket_evidence_reused_folder.py
Summary: A contained file row places a legacy bucket in this checkout only if
its file still exists there, or a memory linked to it was saved after the
repository's `description` last changed. An unrelated repository cloned into
a deleted project's folder therefore does not read (or default-rekey) that
project's bucket. Also pins that bucket_evidence works on a plain connection
with no sqlite3.Row factory.
Used by: pytest; exercises scripts.database.bucket_evidence on real files.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fixtures.identity_git import assert_outside_repository, make_repo

from scripts.database import bucket_evidence, db_connection, ensure_initialized

OLD = "2020-01-01T00:00:00+00:00"


def _seed(store, path, created_at):
    with db_connection(store) as conn:
        ensure_initialized(conn)
        conn.execute(
            "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
            "VALUES ('m1', 'c', 'foo', ?, ?)",
            (created_at, created_at),
        )
        conn.execute("INSERT INTO files (id, path, project_id) VALUES ('f1', ?, 'foo')", (str(path),))
        conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES ('m1', 'f1')")
        conn.commit()


def _evidence(store, repo):
    conn = sqlite3.connect(str(store))  # no Row factory: rows are tuples
    try:
        return bucket_evidence(conn, "foo", [repo], root_common=repo / ".git")
    finally:
        conn.close()


def test_an_unrelated_clone_in_a_deleted_projects_folder_is_not_placed_here(tmp_path):
    """An old project's row under foo/src/a.py, its file gone, and an
    unrelated repository cloned at foo later: the row is vanished, not
    contained. RED WHEN a contained row counts with no file and no memory
    saved after the clone."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/other/foo.git")
    _seed(tmp_path / "m.db", repo / "src" / "a.py", OLD)
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (0, 1), counts


def test_a_re_clone_of_the_same_project_is_placed_by_its_existing_file(tmp_path):
    """Control: the same project cloned again has the file back, so the row
    is contained whatever its date."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/acme/foo.git")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x\n", encoding="utf-8")
    _seed(tmp_path / "m.db", repo / "src" / "a.py", OLD)
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (1, 0), counts


def test_a_deleted_file_saved_after_the_clone_still_places_the_bucket(tmp_path):
    """Control: a file this checkout's own sessions linked and later deleted
    (its memory saved after the repository's description) stays contained."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/acme/foo.git")
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    _seed(tmp_path / "m.db", repo / "src" / "gone.py", later)
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (1, 0), counts
