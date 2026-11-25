"""
Location: pact-plugin/tests/test_legacy_bucket_session_census.py
Summary: A legacy folder-name bucket is read whole or not at all, and a row
written by a session recorded at another checkout keeps it out: for an
unverified bucket (no file row places it) and a verified one (its file rows
lie under this repository) alike. The warning then names how many rows each
side wrote. A row with no session record cannot be placed, so it does not
block a bucket another row vouches for; that limit is pinned too.
Used by: the test suite; no production code imports it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fixtures.identity_git import (
    assert_outside_repository,
    make_repo,
    seed_session_evidence,
)
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory

from scripts import memory_api

REMOTE = "https://github.com/o/proj.git"
CANONICAL = "github.com/o/proj"
CREATED_AT = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def checkout(monkeypatch, tmp_path):
    """A checkout named like its legacy bucket, its store, and its config root."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for name in (
        "_LEGACY_SCOPE_CACHE", "_EXCLUSION_WARNED", "_CASE_VARIANT_WARNED",
        "_UNPROVEN_WARNED", "_SUBMODULE_SUPPRESSED",
    ):
        getattr(memory_api, name).clear()
    repo = make_repo(tmp_path / "proj", remote=REMOTE)
    monkeypatch.chdir(repo)
    return repo, tmp_path / "store.db", tmp_path / "home" / ".claude"


def _rows(store: Path, rows, *, file_path: str = "src/relative.py") -> None:
    """Memories in the `proj` bucket, as (memory id, session id), and one
    file row at `file_path` (relative, so it places nothing, unless absolute)."""
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for memory_id, session_id in rows:
            conn.execute(
                "INSERT INTO memories "
                "(id, context, project_id, session_id, created_at, updated_at) "
                "VALUES (?, 'legacy memory', 'proj', ?, ?, ?)",
                (memory_id, session_id, CREATED_AT, CREATED_AT),
            )
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES ('f1', ?, 'proj')",
            (file_path,),
        )
        conn.commit()


def _read(store: Path, caplog):
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        scopes = memory._read_project_scopes()
    notices = [r.getMessage() for r in caplog.records if "legacy project bucket" in r.getMessage()]
    return scopes, memory._legacy_scope_state, notices


def test_a_row_recorded_at_another_checkout_keeps_an_unverified_bucket_out(checkout, caplog):
    """RED WHEN a session recorded here admits the bucket although another of
    its rows was written by a session recorded at a different directory."""
    repo, store, config = checkout
    _rows(store, [("m1", "here-1"), ("m2", "here-2"), ("m3", "other-1")])
    seed_session_evidence(config, repo, "here-1")
    seed_session_evidence(config, repo, "here-2")
    elsewhere = repo.parent / "delivered" / "proj"
    elsewhere.mkdir(parents=True)
    seed_session_evidence(config, elsewhere, "other-1")
    scopes, state, notices = _read(store, caplog)
    assert scopes == (CANONICAL,), state
    assert state["unproven"] and not state["included"], state
    assert state["unproven_reason"] == "recorded_elsewhere", state
    assert state["sessions"] == {
        "here": 2, "elsewhere": 1, "subfolder": 0, "description_newer": 0, "unattributed": 0,
    }, state
    assert len(notices) == 1, notices
    assert (
        "1 of its memories were written by sessions recorded at another "
        "existing folder (2 by sessions recorded here)"
    ) in notices[0], notices
    assert "project-id rekey" in notices[0], notices


def test_a_row_recorded_at_another_checkout_keeps_a_verified_bucket_out(checkout, caplog):
    """A file row under this repository verifies the bucket, and a row
    written by a session recorded elsewhere still keeps it out. RED WHEN the
    foreign-row check is skipped for a verified bucket."""
    repo, store, config = checkout
    _rows(store, [("m1", None), ("m2", "other-1")],
          file_path=str(repo.resolve() / "src" / "a.py"))
    elsewhere = repo.parent / "delivered" / "proj"
    elsewhere.mkdir(parents=True)
    seed_session_evidence(config, elsewhere, "other-1")
    scopes, state, _notices = _read(store, caplog)
    assert scopes == (CANONICAL,), state
    assert state["unproven"] and not state["verified"], state
    assert state["unproven_reason"] == "recorded_elsewhere", state


def test_a_verified_bucket_with_no_foreign_row_is_read(checkout, caplog):
    """The control for the arm above: the same verified bucket without the
    foreign session's record is read, quietly. Its file row places the whole
    bucket here (a defaulted rekey moves it), so its rows with no recorded
    session get no "nothing shows they are this repository's" notice. RED
    WHEN a verified bucket's unattributed rows are disclosed as unplaced."""
    repo, store, _config = checkout
    _rows(store, [("m1", None), ("m2", "other-1")],
          file_path=str(repo.resolve() / "src" / "a.py"))
    scopes, state, notices = _read(store, caplog)
    assert scopes == (CANONICAL, "proj"), state
    assert state["verified"] and not state["unproven"], state
    assert state["sessions"]["unattributed"] == 2, state
    assert state["rekey"], state
    assert notices == [], notices


def test_a_vouched_bucket_with_no_unattributed_row_reads_quietly(checkout, caplog):
    """The control for the notice: every row's session is recorded here."""
    repo, store, config = checkout
    _rows(store, [("m1", "here-1")])
    seed_session_evidence(config, repo, "here-1")
    scopes, state, notices = _read(store, caplog)
    assert scopes == (CANONICAL, "proj"), state
    assert notices == [], notices


def test_an_unattributed_row_is_read_with_a_notice(checkout, caplog):
    """THE NAMED LIMIT, in the cold review's probe shape: another project's
    row, written by a session with no record, sits in a bucket a session
    recorded here vouches for. Nothing places that row, so the bucket is read
    with it, and the read says so once, with the counts and the rekey
    command. RED WHEN the row blocks the bucket, or is read silently."""
    repo, store, config = checkout
    _rows(store, [("m1", "here-1"), ("m2", "other-sess"), ("m3", None)])
    seed_session_evidence(config, repo, "here-1")
    scopes, state, notices = _read(store, caplog)
    assert scopes == (CANONICAL, "proj"), state
    assert state["session_evidence"] is True and not state["unproven"], state
    assert state["sessions"]["here"] == 1 and state["sessions"]["unattributed"] == 2, state
    assert len(notices) == 1, notices
    assert (
        "is in this read scope because 1 of its memories came from sessions "
        "recorded here, but 2 of its memories came from no recorded session"
    ) in notices[0], notices
    assert "project-id rekey" in notices[0], notices
    _read(store, caplog)
    assert len([r for r in caplog.records if "no recorded session" in r.getMessage()]) == 1
    assert "WHAT THIS CANNOT TELL APART" in memory_api._legacy_session_evidence.__doc__


def test_a_subfolder_launch_names_its_reason(checkout, caplog):
    """RED WHEN a session launched in a subfolder of the repository counts,
    or the warning does not say why it did not."""
    repo, store, config = checkout
    _rows(store, [("m1", "sub-1")])
    sub = repo / "src"
    sub.mkdir()
    seed_session_evidence(config, sub, "sub-1")
    scopes, state, notices = _read(store, caplog)
    assert scopes == (CANONICAL,), state
    assert state["unproven_reason"] == "subfolder_launch", state
    assert len(notices) == 1 and "launched in a subfolder of this repository" in notices[0], notices


def test_no_session_names_its_reason(checkout, caplog):
    repo, store, _config = checkout
    _rows(store, [("m1", "never-recorded")])
    scopes, state, notices = _read(store, caplog)
    assert scopes == (CANONICAL,), state
    assert state["unproven_reason"] == "no_session", state
    assert len(notices) == 1 and "no session launched at the root" in notices[0], notices


def test_a_full_id_reaches_a_row_outside_the_read_scope_and_a_prefix_does_not(checkout):
    """Both halves of the full-id rule: `get`, `update` and `delete` by a full
    32-character id reach a row in an unproven legacy bucket, and by a prefix
    they find nothing, as `list` shows nothing. RED WHEN a full id is bound to
    the read scope, or a prefix escapes it."""
    _repo, store, _config = checkout
    full = "ab" * 16
    _rows(store, [(full, "never-recorded")])
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._read_project_scopes() == (CANONICAL,)
    assert memory._legacy_scope_state["unproven"]
    assert memory.list() == []
    for call in (memory.get, lambda i: memory.update(i, {"context": "x"}), memory.delete):
        assert call(full[:8]) is None
    assert memory.get(full) is not None
    assert memory.update(full, {"context": "edited"}) == full
    assert memory.get(full).context == "edited"
    assert memory.delete(full) == full
    assert memory.get(full) is None


def test_a_session_in_a_checkout_nested_under_the_root_is_elsewhere(checkout, caplog):
    """Below a root is not enough to be a subfolder launch: a separate
    checkout nested inside the root (or every checkout, under a root at `/`)
    owns the sessions launched in it. RED WHEN the census counts such a
    session as a subfolder of the root: its rows then read as this
    repository's own instead of keeping the bucket out."""
    repo, store, config = checkout
    nested = make_repo(repo / "vendor" / "other", remote="https://github.com/o/other.git")
    _rows(store, [("m1", "here-1"), ("m2", "nested-1")])
    seed_session_evidence(config, repo, "here-1")
    seed_session_evidence(config, nested, "nested-1")
    scopes, state, _notices = _read(store, caplog)
    assert scopes == (CANONICAL,), state
    assert state["sessions"]["elsewhere"] == 1 and state["sessions"]["subfolder"] == 0, state
    assert state["unproven_reason"] == "recorded_elsewhere", state


def test_a_session_in_the_roots_own_subfolder_is_a_subfolder_launch(checkout, caplog):
    """The control: a plain folder of the root's own checkout."""
    repo, store, config = checkout
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    _rows(store, [("m1", "sub-1")])
    seed_session_evidence(config, sub, "sub-1")
    _scopes, state, _notices = _read(store, caplog)
    assert state["sessions"]["subfolder"] == 1 and state["sessions"]["elsewhere"] == 0, state


def _worktree_inside(repo: Path) -> Path:
    """A linked worktree placed inside its main checkout, at
    `<repo>/.worktrees/wt`, with a subfolder a session can launch in."""
    from fixtures.identity_git import run_git

    worktree = repo / ".worktrees" / "wt"
    run_git("worktree", "add", "-q", "--detach", str(worktree), cwd=repo)
    (worktree / "sub").mkdir()
    return worktree


def test_a_session_in_a_nested_linked_worktrees_subfolder_is_a_subfolder_launch(checkout, caplog):
    """A linked worktree inside its main checkout is a gate root of its own,
    not a separate checkout nested under the root. RED WHEN a session
    launched in a subfolder of that worktree counts as elsewhere."""
    repo, store, config = checkout
    worktree = _worktree_inside(repo)
    _rows(store, [("m1", "wt-1")])
    seed_session_evidence(config, worktree / "sub", "wt-1")
    _scopes, state, _notices = _read(store, caplog)
    assert state["sessions"]["subfolder"] == 1 and state["sessions"]["elsewhere"] == 0, state
    assert state["unproven_reason"] == "subfolder_launch", state


def test_a_folder_name_repository_keeps_its_bucket_with_a_nested_worktree_session(
    monkeypatch, tmp_path, caplog
):
    """The folder-name side of the same layout: a repository with no origin
    reads and saves into its own bucket when the bucket's rows came from a
    session in a subfolder of its nested linked worktree."""
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for name in ("_LEGACY_SCOPE_CACHE", "_EXCLUSION_WARNED", "_UNPROVEN_WARNED"):
        getattr(memory_api, name).clear()
    repo = make_repo(tmp_path / "proj")
    monkeypatch.chdir(repo)
    store, config = tmp_path / "store.db", tmp_path / "home" / ".claude"
    worktree = _worktree_inside(repo)
    _rows(store, [("m1", "wt-1")])
    seed_session_evidence(config, worktree / "sub", "wt-1")
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._project_id == "proj", memory._project_id
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == ("proj",), memory._legacy_scope_state
    assert memory._project_id_for_write() == "proj"


def test_an_unchanged_sessions_root_is_not_listed_again(checkout, caplog, monkeypatch):
    """The census lists the sessions root once per process while its stat is
    unchanged: a second command, with the verdict cache cleared as a write
    clears it, reuses the listing. A new slug folder changes the root's stat
    and is listed. RED WHEN every uncached verdict lists the root again."""
    import os as os_module

    repo, store, config = checkout
    _rows(store, [("m1", "here-1")])
    seed_session_evidence(config, repo, "here-1")
    memory_api._DIR_LISTINGS.clear()
    listed = []
    real_listdir = os_module.listdir

    def counting_listdir(path="."):
        listed.append(os_module.path.basename(str(path)))
        return real_listdir(path)

    monkeypatch.setattr(memory_api.os, "listdir", counting_listdir)
    _read(store, caplog)
    first = listed.count("pact-sessions")
    memory_api._LEGACY_SCOPE_CACHE.clear()
    _read(store, caplog)
    assert first == 1, listed
    assert listed.count("pact-sessions") == 1, listed
    (config / "pact-sessions" / "another-slug").mkdir()
    memory_api._LEGACY_SCOPE_CACHE.clear()
    _read(store, caplog)
    assert listed.count("pact-sessions") == 2, listed
