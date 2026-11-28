"""
Location: pact-plugin/tests/test_bucket_evidence_reused_folder.py
Summary: A contained file row places a legacy bucket in this checkout only if
a memory linked to it was saved after the repository's `description` last
changed (dated), or its file still exists there. An unrelated repository
cloned into a deleted project's folder therefore does not read (or
default-rekey) that project's bucket by its missing files, and a re-clone
whose one file was renamed upstream stays verified. Pins the accepted
residual: a file both trees share (a README) still places the bucket. Also
pins that bucket_evidence works on a plain connection with no sqlite3.Row
factory.
Used by: pytest; exercises scripts.database.bucket_evidence on real files.
"""

from __future__ import annotations

from scripts.database import sqlite3
import os
from datetime import datetime, timedelta, timezone

from fixtures.identity_git import assert_outside_repository, make_repo

import pytest

from scripts import database
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


def _seed_rows(store, paths, created_at):
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for index, path in enumerate(paths):
            conn.execute(
                "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
                "VALUES (?, 'c', 'foo', ?, ?)",
                (f"m{index}", created_at, created_at),
            )
            conn.execute("INSERT INTO files (id, path, project_id) VALUES (?, ?, 'foo')",
                         (f"f{index}", str(path)))
            conn.execute("INSERT INTO memory_files (memory_id, file_id) VALUES (?, ?)",
                         (f"m{index}", f"f{index}"))
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


def test_accepted_residual_a_shared_file_places_an_unrelated_clone(tmp_path):
    """ACCEPTED RESIDUAL, pinned so the decision is explicit: an old
    project's rows at foo/README and foo/src/a.py, and an unrelated
    repository cloned at foo later that also has a README. The shared README
    is contained, the missing file vanished, so the bucket is placed here,
    as every same-named folder shared it before this change. A rule that
    vetoed existence beside a missing row dropped re-clones whose one file
    was renamed upstream (next test)."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/other/foo.git")
    (repo / "README").write_text("r\n", encoding="utf-8")
    _seed_rows(tmp_path / "m.db", [repo / "README", repo / "src" / "a.py"], OLD)
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (1, 1), counts


def test_a_re_clone_with_one_file_renamed_upstream_stays_verified(tmp_path):
    """Eleven rows, all memories older than the clone; ten files are back and
    one was renamed upstream. RED WHEN one missing, undated row vetoes the
    existing ones (the bucket would read unproven and a defaulted rekey be
    refused)."""
    from scripts.memory_api import decide_bucket

    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/acme/foo.git")
    paths = [repo / f"f{index}.py" for index in range(11)]
    for path in paths[:10]:
        path.write_text("x\n", encoding="utf-8")
    _seed_rows(tmp_path / "m.db", paths, OLD)
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (10, 1), counts
    assert decide_bucket(counts, None).state == "verified"


def test_a_re_clone_with_every_file_back_is_placed_by_existence(tmp_path):
    """Control: the same project cloned again has README and src/a.py back,
    so both undated rows are contained."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/acme/foo.git")
    (repo / "README").write_text("r\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x\n", encoding="utf-8")
    _seed_rows(tmp_path / "m.db", [repo / "README", repo / "src" / "a.py"], OLD)
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (2, 0), counts


def test_no_description_counts_for_a_legacy_bucket_but_not_for_an_earlier_id(tmp_path):
    """No `.git/description`: a deleted file's row is still contained for a
    legacy bucket (documented), but `dated_only` (an earlier canonical id)
    counts it vanished. RED WHEN a missing description dates a row on the
    earlier-id path."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/acme/foo.git")
    os.remove(repo / ".git" / "description")
    _seed(tmp_path / "m.db", repo / "src" / "gone.py", OLD)
    assert _evidence(tmp_path / "m.db", repo)["contained_rows"] == 1
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    try:
        counts = bucket_evidence(conn, "foo", [repo], root_common=repo / ".git", dated_only=True)
    finally:
        conn.close()
    assert (counts["contained_rows"], counts["vanished"]) == (0, 1), counts


def test_rows_in_one_folder_resolve_that_folder_once(tmp_path, monkeypatch):
    """Two hundred rows in one folder: realpath runs a handful of times, not
    once per row (a 5,000-row legacy bucket cost about 270 ms per CLI read
    that way). RED WHEN every row's whole path is resolved."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "foo", remote="https://github.com/acme/foo.git")
    (repo / "src").mkdir()
    paths = [repo / "src" / f"f{index}.py" for index in range(200)]
    for path in paths[::2]:
        path.write_text("x\n", encoding="utf-8")
    _seed_rows(tmp_path / "m.db", paths, OLD)
    calls = []
    real = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda p, *a, **k: calls.append(p) or real(p, *a, **k))
    counts = _evidence(tmp_path / "m.db", repo)
    assert (counts["contained_rows"], counts["vanished"]) == (100, 100), counts
    assert len(calls) < 10, len(calls)


@pytest.mark.parametrize("shape", ["plain", "missing_dir", "file_link", "dir_link", "dotdot"])
def test_folder_resolution_matches_realpath(tmp_path, shape):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(tmp_path / "real" / "a.py")
    (tmp_path / "dirlink").symlink_to(tmp_path / "real")
    path = {
        "plain": tmp_path / "real" / "a.py",
        "missing_dir": tmp_path / "gone" / "deeper" / "b.py",
        "file_link": tmp_path / "link.py",
        "dir_link": tmp_path / "dirlink" / "a.py",
        "dotdot": tmp_path / "real" / ".." / "real" / "a.py",
    }[shape]
    assert database._realpath_by_folder(str(path), {}) == os.path.realpath(str(path))


@pytest.mark.parametrize("path", ["/tmp/a\0b/c.py", "/tmp/a/b\0c.py"], ids=["folder", "file_name"])
def test_folder_resolution_raises_on_nul_like_realpath(path):
    """A NUL anywhere raises ValueError, as realpath does, so the row counts as
    unresolvable. The file-name case is the guard's own: `islink` and
    `lexists` swallow the error there. RED WHEN the guard is removed."""
    with pytest.raises(ValueError):
        os.path.realpath(path)
    with pytest.raises(ValueError):
        database._realpath_by_folder(path, {})
