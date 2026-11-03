"""
Location: pact-plugin/tests/test_rev_parse_unstripped.py
Summary: Pins that every rev-parse field project_scope reads is UNSTRIPPED,
         so a worktree whose path ends in whitespace keeps that whitespace and
         compares equal to the worktree listing.
Used by: pytest. Exercises hooks/shared/project_scope.py `_rev_parse_path`
         through its callers `_record_worktree_identity` and
         `stays_in_declared_project`, with a real `git init` repository and a
         real `git worktree add` under tmp_path (no remote).

Reverting `_rev_parse_path` to the stripped `_git_output` reader turns every
arm here red: the toplevel loses its trailing space, is not in the listing,
the identity record is never written, and the scope guard refuses the
worktree's own root.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from shared import project_scope
from shared.project_scope import WORKTREE_IDENTITY_FILE

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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
def spaced_worktree(tmp_path, monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(key, raising=False)
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=main)
    worktree = tmp_path / "wt "
    _git("worktree", "add", "-q", str(worktree), "-b", "spaced", cwd=main)
    assert worktree.is_dir() and worktree.name.endswith(" ")
    return main, worktree


def test_the_toplevel_keeps_its_trailing_space(spaced_worktree):
    _main, worktree = spaced_worktree

    assert project_scope._rev_parse_path(worktree, "--show-toplevel") == (
        worktree.resolve()
    )


def test_the_identity_record_is_written_for_a_spaced_worktree(
    spaced_worktree, tmp_path, monkeypatch
):
    main, worktree = spaced_worktree
    config = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    project_scope._record_worktree_identity(SID, str(worktree))

    records = list((config / "pact-sessions").rglob(WORKTREE_IDENTITY_FILE))
    assert len(records) == 1, records
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["worktree"] == str(worktree.resolve())
    assert Path(record["common_dir"]) == (main / ".git").resolve()


def test_the_scope_guard_admits_the_spaced_worktrees_own_root(spaced_worktree):
    """Rule 3: the root of any checkout of the declaration's repository. The
    resolved root is the worktree; its toplevel must compare equal to it."""
    _main, worktree = spaced_worktree
    sub = worktree / "pkg"
    sub.mkdir()

    assert project_scope.stays_in_declared_project(
        sub, worktree, worktree / "CLAUDE.md"
    )
