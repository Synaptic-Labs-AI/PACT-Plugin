"""
Location: pact-plugin/tests/test_earlier_canonical_bucket.py
Summary: A canonical bucket of this repository under an earlier id (the origin
renamed, transferred or respelled) is not read, but a read names it once with
the command that moves it into this checkout's id, and `project-id show` gives
it that command. Placement takes only a file row linked by a memory saved after
this checkout's clone (dated), never a file that merely exists here, so an
unrelated bucket sharing a README stays silent, as does one whose rows lie
elsewhere; and a store whose other buckets have no row under this checkout asks
git nothing more.
Used by: pytest; drives PACTMemory._read_project_scopes and the CLI's
`project-id show` on a real repository and store.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo, run_git

from scripts import memory_api
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory

OLD = "github.com/acme/widget"
NEW = "github.com/acme/renamed"
SHARED = "github.com/a/x"
ELSEWHERE = "github.com/other/thing"
LIVE = "github.com/acme/beta"
NESTED = "github.com/acme/lib"
OLD_STAMP = "2020-01-01T00:00:00+00:00"


def _later() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()


def _memory(conn, memory_id, bucket, stamp, path=None):
    conn.execute(
        "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
        "VALUES (?, 'c', ?, ?, ?)",
        (memory_id, bucket, stamp, stamp),
    )
    if path is not None:
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES (?, ?, ?)",
            ("f-" + memory_id, str(path), bucket),
        )
        conn.execute(
            "INSERT INTO memory_files (memory_id, file_id) VALUES (?, ?)",
            (memory_id, "f-" + memory_id),
        )


def _fresh(monkeypatch, repo):
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for name in ("_LEGACY_SCOPE_CACHE", "_EARLIER_CANONICAL", "_EARLIER_CANONICAL_WARNED"):
        getattr(memory_api, name).clear()


@pytest.fixture
def renamed(tmp_path, monkeypatch):
    """A checkout whose origin was renamed after it saved a memory linked to a
    file in it (and one with no file); a bucket of another repository linked
    (in 2020) to the README this checkout also has; one whose row lies
    elsewhere; and a live repository beta whose memory, saved after this
    clone, links a file here while its other memory links its own file."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "widget", remote="https://github.com/acme/widget.git")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (repo / "README").write_text("r\n", encoding="utf-8")
    elsewhere = tmp_path / "other" / "b.py"
    elsewhere.parent.mkdir()
    elsewhere.write_text("y\n", encoding="utf-8")
    beta = make_repo(tmp_path / "beta", remote="https://github.com/acme/beta.git")
    (beta / "own.py").write_text("z\n", encoding="utf-8")
    lib = make_repo(repo / "vendor" / "lib", remote="https://github.com/acme/lib.git")
    (lib / "l.py").write_text("l\n", encoding="utf-8")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        _memory(conn, "m1", OLD, _later(), repo / "src" / "a.py")
        _memory(conn, "m2", SHARED, OLD_STAMP, repo / "README")
        _memory(conn, "m3", ELSEWHERE, _later(), elsewhere)
        _memory(conn, "m1b", OLD, _later())
        _memory(conn, "m4", LIVE, _later(), repo / "src" / "a.py")
        _memory(conn, "m5", LIVE, _later(), beta / "own.py")
        _memory(conn, "m6", NESTED, _later(), repo / "src" / "a.py")
        _memory(conn, "m7", NESTED, _later(), lib / "l.py")
        conn.commit()
    run_git("remote", "set-url", "origin", "git@github.com:Acme/Renamed.git", cwd=repo)
    _fresh(monkeypatch, repo)
    return repo, store


def test_a_renamed_origins_placed_bucket_is_named_with_its_command(renamed, caplog):
    """RED WHEN an earlier canonical bucket placed in this checkout is hidden
    with no notice, or when another repository's bucket is named: one whose
    only row here is a README it shares (linked before this clone), or one
    whose rows lie outside this checkout."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == (NEW,)
        memory._read_project_scopes()
    notices = [r.getMessage() for r in caplog.records if "is not this checkout's id" in r.getMessage()]
    assert len(notices) == 1, notices
    assert f"project bucket {OLD!r} holds 2 memories, 1 linked to files in this checkout" in notices[0], notices
    assert "may be this repository under an earlier id" in notices[0], notices
    assert "transferred" not in notices[0], notices
    assert f"--from={OLD}" in notices[0] and f"--to={NEW}" in notices[0], notices
    assert SHARED not in caplog.text and ELSEWHERE not in caplog.text
    assert LIVE not in caplog.text, "a live repository cross-linking one file here was named"
    assert NESTED not in caplog.text, "a checkout nested in this one was named"


def test_project_id_show_gives_the_earlier_id_a_rekey_command(renamed, capsys):
    from scripts.cli import main

    _repo, store = renamed
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "show", "--db-path", str(store)])
    assert exit_info.value.code == 0
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["result"]["candidates"]}
    assert rows[OLD]["relation"] == "earlier_id", rows
    assert f"--from={OLD}" in rows[OLD]["rekey"] and f"--to={NEW}" in rows[OLD]["rekey"], rows
    for other in (SHARED, ELSEWHERE, LIVE, NESTED):
        assert (rows[other]["relation"], rows[other]["rekey"]) == ("other", None), rows


def test_buckets_with_no_row_under_this_checkout_cost_no_git_call(tmp_path, monkeypatch):
    """Forty other canonical buckets whose rows lie elsewhere, and a live
    repository that cross-links one file here: the scan asks for the gate
    roots (a `git worktree list`) not once. RED WHEN the scan walks every
    canonical bucket with file rows, or one with an existing file outside."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/o/proj.git")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for index in range(40):
            _memory(conn, f"m{index}", f"github.com/o/other{index}", _later(),
                    tmp_path / "elsewhere" / f"{index}.py")
        # A live repository cross-linking one file here: its own file outside
        # rules it out before any git call.
        own = tmp_path / "beta" / "own.py"
        own.parent.mkdir()
        own.write_text("b\n", encoding="utf-8")
        (repo / "x.py").write_text("x\n", encoding="utf-8")
        _memory(conn, "mx", "github.com/o/beta", _later(), repo / "x.py")
        _memory(conn, "my", "github.com/o/beta", _later(), own)
        conn.commit()
    _fresh(monkeypatch, repo)
    asked = []
    real = PACTMemory._legacy_gate_roots
    monkeypatch.setattr(PACTMemory, "_legacy_gate_roots",
                        lambda self, root: asked.append(root) or real(self, root))
    memory = PACTMemory(session_id="s", db_path=store)
    assert memory._earlier_canonical_buckets("github.com/o/proj") == {}
    assert asked == [], asked


def test_a_new_store_stamp_replaces_the_cached_answer(renamed):
    """RED WHEN each store stamp adds a cache entry that is never trimmed."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    memory._read_project_scopes()
    with db_connection(store) as conn:
        _memory(conn, "m9", ELSEWHERE, _later())
        conn.commit()
    memory._read_project_scopes()
    assert len(memory_api._EARLIER_CANONICAL) == 1, list(memory_api._EARLIER_CANONICAL)


def test_a_bucket_with_no_memories_is_not_named(caplog):
    with caplog.at_level(logging.WARNING):
        memory_api._note_earlier_canonical("s.db", OLD, NEW, 0, 0, "cmd")
    assert not caplog.records
