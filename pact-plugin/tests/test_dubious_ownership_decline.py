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
from scripts.database import sqlite3

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
    assert "the user, not an agent, runs" in message, message
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


def test_a_translated_refusal_is_still_recognised(tmp_path, monkeypatch):
    """git translates "detected dubious ownership" but prints its hint line,
    `git config --global --add safe.directory <path>`, untranslated, so
    detection keys on that line. RED WHEN it keys on the English words."""
    import subprocess

    from scripts import project_identity

    path = bytes(tmp_path)
    german = (
        b"fatal: Unsicherer Besitz im Repository unter '" + path + b"' erkannt\n"
        b"Um eine Ausnahme f\xc3\xbcr dieses Verzeichnis hinzuzuf\xc3\xbcgen, rufen Sie auf:\n\n"
        b"\tgit config --global --add safe.directory " + path + b"\n"
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 128, b"", german)

    monkeypatch.setattr(project_identity.subprocess, "run", fake_run)
    assert project_identity.git_refuses_ownership(tmp_path) is True

    def other_failure(command, **kwargs):
        return subprocess.CompletedProcess(command, 128, b"", b"fatal: kein Git-Repository\n")

    monkeypatch.setattr(project_identity.subprocess, "run", other_failure)
    assert project_identity.git_refuses_ownership(tmp_path) is False

    def echoed_path(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 128, b"",
            b"fatal: not a git repository: /x/dubious ownership/safe.directory/missing\n",
        )

    monkeypatch.setattr(project_identity.subprocess, "run", echoed_path)
    assert project_identity.git_refuses_ownership(tmp_path) is False


def test_a_path_naming_safe_directory_is_not_an_ownership_refusal(tmp_path, monkeypatch, capfd):
    """git echoes a broken gitfile's target, so "safe.directory" in a path is
    not git's hint. RED WHEN detection matches "safe.directory" anywhere in
    stderr instead of git's hint line."""
    from scripts import project_identity

    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").write_text(f"gitdir: {proj / 'safe.directory' / 'missing'}\n")
    store = tmp_path / "mem.db"
    conn = sqlite3.connect(str(store))
    create_test_schema(conn)
    conn.commit()
    conn.close()
    monkeypatch.chdir(proj)
    assert project_identity.git_refuses_ownership(proj) is False
    code, envelope, _ = _cli(
        ["save", "--stdin", "--db-path", str(store)], capfd,
        stdin=json.dumps({"context": "x"}), monkeypatch=monkeypatch,
    )
    assert code != 0 and envelope["error"] == "PROJECT_IDENTITY_UNRESOLVED", envelope
    message = envelope["message"]
    assert "dubious ownership" not in message and "git config --global" not in message, message
    assert "Check that git is installed" in message, message


def test_the_real_refusal_is_recognised_by_its_hint_line(refused_repo, monkeypatch):
    from scripts import project_identity

    repo, _ = refused_repo
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    assert project_identity.git_refuses_ownership(repo) is True


def test_a_hint_line_naming_another_path_is_not_this_refusal(tmp_path, monkeypatch):
    """A directory name holding a newline makes git echo a whole hint line.
    RED WHEN any hint line counts, whatever path it names."""
    import subprocess

    from scripts import project_identity

    assert_outside_repository(tmp_path)
    proj = tmp_path / "a\n\tgit config --global --add safe.directory /elsewhere"
    proj.mkdir(parents=True)
    (proj / ".git").write_text("gitdir: missing\n")
    assert project_identity.git_refuses_ownership(proj) is False

    def names_elsewhere(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 128, b"",
            b"fatal: not a git repository: /a\n\tgit config --global --add safe.directory /elsewhere\n",
        )

    monkeypatch.setattr(project_identity.subprocess, "run", names_elsewhere)
    assert project_identity.git_refuses_ownership(tmp_path) is False


def test_a_quoted_hint_path_still_names_this_repository(tmp_path, monkeypatch):
    """git single-quotes a path holding a space in its hint."""
    from scripts import project_identity

    assert_outside_repository(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = make_repo(tmp_path / "my proj", remote="https://github.com/me/proj.git")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    assert project_identity.git_refuses_ownership(repo) is True
