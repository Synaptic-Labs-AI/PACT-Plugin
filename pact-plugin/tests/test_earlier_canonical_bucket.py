"""
Location: pact-plugin/tests/test_earlier_canonical_bucket.py
Summary: A canonical bucket of this repository under an earlier id (the origin
renamed, transferred or respelled) is not read, but a read names it once with
the command that moves it into this checkout's id, and `project-id show` gives
it that command. A canonical bucket whose file rows do not place it in this
checkout stays silent.
Used by: pytest; drives PACTMemory._read_project_scopes and the CLI's
`project-id show` on a real repository and store.
"""

from __future__ import annotations

import json
import logging

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo, run_git

from scripts import memory_api
from scripts.database import db_connection, ensure_initialized
from scripts.memory_api import PACTMemory

OLD = "github.com/acme/widget"
NEW = "github.com/acme/renamed"
STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def renamed(tmp_path, monkeypatch):
    """A checkout whose origin was renamed after it saved one memory with a
    file row in the checkout, plus an unrelated canonical bucket."""
    assert_outside_repository(tmp_path)
    repo = make_repo(tmp_path / "widget", remote="https://github.com/acme/widget.git")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x\n", encoding="utf-8")
    store = tmp_path / "store.db"
    with db_connection(store) as conn:
        ensure_initialized(conn)
        for memory_id, bucket in (("m1", OLD), ("m2", "github.com/other/thing")):
            conn.execute(
                "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
                "VALUES (?, 'c', ?, ?, ?)",
                (memory_id, bucket, STAMP, STAMP),
            )
        conn.execute(
            "INSERT INTO files (id, path, project_id) VALUES ('f1', ?, ?)",
            (str(repo / "src" / "a.py"), OLD),
        )
        conn.commit()
    run_git("remote", "set-url", "origin", "git@github.com:Acme/Renamed.git", cwd=repo)
    monkeypatch.chdir(repo)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for name in ("_LEGACY_SCOPE_CACHE", "_EARLIER_CANONICAL", "_EARLIER_CANONICAL_WARNED"):
        getattr(memory_api, name).clear()
    return repo, store


def test_a_renamed_origins_placed_bucket_is_named_with_its_command(renamed, caplog):
    """RED WHEN an earlier canonical bucket placed in this checkout is
    hidden with no notice and no command."""
    _repo, store = renamed
    memory = PACTMemory(session_id="s", db_path=store)
    with caplog.at_level(logging.WARNING):
        assert memory._read_project_scopes() == (NEW,)
        memory._read_project_scopes()
    notices = [r.getMessage() for r in caplog.records if f"project bucket {OLD!r}" in r.getMessage()]
    assert len(notices) == 1, notices
    assert f"--from={OLD}" in notices[0] and f"--to={NEW}" in notices[0], notices
    assert "github.com/other/thing" not in caplog.text


def test_project_id_show_gives_the_earlier_id_a_rekey_command(renamed, capsys):
    from scripts.cli import main

    _repo, store = renamed
    with pytest.raises(SystemExit) as exit_info:
        main(["project-id", "show", "--db-path", str(store)])
    assert exit_info.value.code == 0
    rows = {row["id"]: row for row in json.loads(capsys.readouterr().out)["result"]["candidates"]}
    assert rows[OLD]["relation"] == "earlier_id", rows
    assert f"--from={OLD}" in rows[OLD]["rekey"] and f"--to={NEW}" in rows[OLD]["rekey"], rows
    assert rows["github.com/other/thing"]["relation"] == "other", rows
    assert rows["github.com/other/thing"]["rekey"] is None, rows
