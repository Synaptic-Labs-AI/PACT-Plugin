"""
Location: pact-plugin/tests/test_cwd_repo_home_landing.py
Summary: The process cwd's repository identity (the location-divergence
warning's comparison) lands on HOME as the resolver does: under a HOME that
is a dotfiles repository it is HOME's folder name, never that repository's
id, which no resolver rung would bind.
Used by: pytest; drives PACTMemory._current_repo_identity with real git.
"""

from __future__ import annotations

from fixtures.identity_git import assert_outside_repository, make_repo
from scripts.memory_api import PACTMemory

DOTFILES = "https://github.com/me/dotfiles.git"


# The suite's per-test isolation points `Path.home()` at `tmp_path / "home"`.


def _cwd_identity(monkeypatch, cwd, elsewhere):
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(elsewhere))
    memory = PACTMemory(session_id="s", db_path=elsewhere / "m.db")
    return memory._current_repo_identity()


def test_a_dotfiles_home_gives_its_folder_name(tmp_path, monkeypatch):
    assert_outside_repository(tmp_path)
    home = make_repo(tmp_path / "home", remote=DOTFILES)
    (home / "notes").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _cwd_identity(monkeypatch, home / "notes", elsewhere) == "home"


def test_control_a_repository_that_is_not_home_keeps_its_id(tmp_path, monkeypatch):
    assert_outside_repository(tmp_path)
    (tmp_path / "home").mkdir(exist_ok=True)
    repo = make_repo(tmp_path / "work" / "dotfiles", remote=DOTFILES)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _cwd_identity(monkeypatch, repo, elsewhere) == "github.com/me/dotfiles"
