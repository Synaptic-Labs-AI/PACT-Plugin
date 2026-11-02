"""
Location: pact-plugin/tests/test_admin_gitdir_relative_record.py
Summary: Pins that the worktree-listing fallback (`<common-dir>/worktrees/*/
         gitdir`, used when `git worktree list -z` is unavailable) resolves a
         RELATIVE record against the admin dir holding it, the form git writes
         under `worktree.useRelativePaths`.
Used by: pytest. Exercises hooks/shared/project_scope.py
         `_worktree_paths_from_admin_gitdirs` directly and through
         `_project_memory_root_cause`, with a real repository and worktree
         under tmp_path (no remote).

The git here predates `worktree.useRelativePaths`, so the relative record is
written by the test in git's shape (the worktree's `.git` relative to the
admin dir). Reverting the resolution base to the main root turns both arms
red: the listing names a path that is not the worktree, and the classifier
fails verification (UNVERIFIED) on a legitimate worktree.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shared import project_scope
from shared.project_scope import ROOT_CAUSE_OK, _project_memory_root_cause


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


def _relative_record_layout(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=main)
    worktree = tmp_path / "trees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    admin = main / ".git" / "worktrees" / "wt"
    record = os.path.relpath(worktree / ".git", admin)
    assert not os.path.isabs(record)
    (admin / "gitdir").write_text(record + "\n", encoding="utf-8")
    return main, worktree


def _without_the_z_listing(monkeypatch):
    real_run_git = project_scope.run_git

    def run_git(args, **kwargs):
        if "worktree" in args and "-z" in args:
            return subprocess.CompletedProcess(args, 129, b"", b"unknown switch")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", run_git)


def test_the_fallback_lists_the_worktree_from_a_relative_record(tmp_path):
    main, worktree = _relative_record_layout(tmp_path)

    paths = project_scope._worktree_paths_from_admin_gitdirs(main)

    assert paths is not None
    assert worktree.resolve() in {Path(p).resolve() for p in paths}


def test_a_worktree_with_a_relative_record_is_known_main(tmp_path, monkeypatch):
    main, worktree = _relative_record_layout(tmp_path)
    _without_the_z_listing(monkeypatch)

    assert _project_memory_root_cause(worktree) == (main.resolve(), ROOT_CAUSE_OK)
