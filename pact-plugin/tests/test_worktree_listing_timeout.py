"""
Location: pact-plugin/tests/test_worktree_listing_timeout.py
Summary: Pins that the worktree listing stops after `run_git` returns None
         (a timeout or a missing git) instead of trying two more git-backed
         sources, while a git that RAN and refused `-z` still falls back.
Used by: pytest. Exercises hooks/shared/project_scope.py
         `_worktree_list_paths` and `_listed_worktrees_result` with a real
         repository under tmp_path and a spy on `project_scope.run_git`.

Reverting the early return turns the timeout arm red: the spy records three
git calls (the `-z` listing, the admin fallback's rev-parse and the line
listing) where one is expected.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from shared import project_scope


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=main)
    return main


def _spy(monkeypatch, answer):
    calls = []
    real = project_scope.run_git

    def run_git(args, **kwargs):
        calls.append(list(args))
        return answer(args, real, kwargs)

    monkeypatch.setattr(project_scope, "run_git", run_git)
    return calls


def test_a_timeout_on_the_listing_stops_after_one_call(repo, monkeypatch):
    calls = _spy(monkeypatch, lambda args, real, kwargs: None)

    assert project_scope._worktree_list_paths(repo) is None
    assert len(calls) == 1, calls
    assert project_scope._listed_worktrees_result(repo) is None


def test_a_git_that_refuses_z_still_falls_back_to_the_admin_records(
    repo, monkeypatch
):
    """Control: a nonzero exit is git RUNNING, so the next source is tried."""
    def answer(args, real, kwargs):
        if "worktree" in args and "-z" in args:
            return subprocess.CompletedProcess(args, 129, b"", b"unknown switch")
        return real(args, **kwargs)

    calls = _spy(monkeypatch, answer)

    paths = project_scope._worktree_list_paths(repo)

    assert paths is not None
    assert repo.resolve() in {Path(p).resolve() for p in paths}
    assert len(calls) >= 2, calls
