"""
Location: pact-plugin/tests/test_dubious_ownership_decline.py
Summary: A repository git refuses for "dubious ownership" (owned by another
user) declines with that cause and its fix, `git config --global --add
safe.directory <path>`, not the generic "check that git can read it".
GIT_TEST_ASSUME_DIFFERENT_OWNER makes git refuse a repository the test owns.
Used by: pytest; runs the CLI in process against a real repository.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fixtures.identity_git import assert_outside_repository, make_repo
from helpers import create_test_schema


def _cli(argv, capfd, stdin=None, monkeypatch=None):
    import io

    from scripts.cli import main

    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    captured = capfd.readouterr()
    stream = captured.out if exit_info.value.code == 0 else captured.err
    return exit_info.value.code, json.loads(stream), captured.err


@pytest.fixture
def refused_repo(tmp_path, monkeypatch):
    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "proj", remote="https://github.com/me/proj.git")
    store = tmp_path / "mem.db"
    conn = sqlite3.connect(str(store))
    create_test_schema(conn)
    conn.commit()
    conn.close()
    monkeypatch.chdir(repo)
    return repo, store


def test_a_save_names_dubious_ownership_and_its_fix(refused_repo, monkeypatch, capfd):
    """RED WHEN the refusal names only the generic git-cannot-read cause."""
    repo, store = refused_repo
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    code, envelope, _ = _cli(
        ["save", "--stdin", "--db-path", str(store)], capfd,
        stdin=json.dumps({"context": "x"}), monkeypatch=monkeypatch,
    )
    assert code != 0 and envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED", envelope
    message = envelope["message"]
    assert "dubious ownership" in message, message
    assert "git config --global --add safe.directory" in message and str(repo) in message, message
    assert "Check that git is installed" not in message, message


def test_show_names_dubious_ownership_and_its_fix(refused_repo, monkeypatch, capfd, caplog):
    repo, store = refused_repo
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    code, envelope, _ = _cli(["project-id", "show", "--db-path", str(store)], capfd)
    assert code == 0, envelope
    assert envelope["result"]["decline_reason"]["reason"] == "broken_git_marker", envelope
    warning = " ".join(record.getMessage() for record in caplog.records)
    assert "dubious ownership" in warning and "safe.directory" in warning, warning


def test_control_the_same_repository_owned_by_this_user_resolves(refused_repo, capfd):
    repo, store = refused_repo
    code, envelope, _ = _cli(["project-id", "show", "--db-path", str(store)], capfd)
    assert code == 0 and envelope["result"]["project_id"] == "github.com/me/proj", envelope
