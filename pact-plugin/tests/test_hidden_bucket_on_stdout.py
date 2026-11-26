"""
Location: pact-plugin/tests/test_hidden_bucket_on_stdout.py
Summary: `list` and `search` put a hidden old folder-name bucket's
`legacy_scope` record on stdout, beside an unchanged `result`, so a caller
that reads only stdout sees why rows are missing; the stderr notice stays.
Used by: pytest; runs the CLI in process against a real repository and store.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from helpers import create_test_schema

NOW = "2026-01-01T00:00:00+00:00"


def _cli(argv, capfd):
    from scripts.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    return exit_info.value.code, json.loads(captured.out)


@pytest.fixture
def repo_store(tmp_path, monkeypatch):
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    conn = sqlite3.connect(str(store))
    create_test_schema(conn)
    conn.commit()
    conn.close()
    monkeypatch.chdir(repo)
    return store


def _add_old_row(store):
    conn = sqlite3.connect(str(store))
    conn.execute(
        "INSERT INTO memories (id, context, project_id, created_at, updated_at) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'old row', 'proj', ?, ?)",
        (NOW, NOW),
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("verb", [["list"], ["search", "old"]])
def test_a_hidden_bucket_is_named_on_stdout(repo_store, capfd, verb):
    """RED WHEN the hidden bucket reaches only stderr."""
    _add_old_row(repo_store)
    code, envelope = _cli([*verb, "--db-path", str(repo_store)], capfd)
    assert code == 0 and envelope["ok"] is True, envelope
    assert envelope["result"] == [], envelope
    scope = envelope["legacy_scope"]
    assert scope["candidate"] == "proj" and scope["unproven"] is True, scope
    assert scope["memory_count"] == 1 and "project-id rekey" in scope["rekey"], scope


@pytest.mark.parametrize("verb", [["list"], ["search", "old"]])
def test_control_no_hidden_bucket_adds_no_key(repo_store, capfd, verb):
    code, envelope = _cli([*verb, "--db-path", str(repo_store)], capfd)
    assert code == 0 and "legacy_scope" not in envelope, envelope
