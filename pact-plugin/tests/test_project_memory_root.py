"""Arms for `project_scope.project_memory_root` (#1653).

A lead's project memory belongs to the MAIN checkout when the session's
project dir is a linked worktree; every other layout -- a main checkout, a
subdirectory of one, a non-git directory, a submodule or `--separate-git-dir`
repository, a worktree of a bare repo, and any git failure -- returns the
input EXACTLY as given (no resolve, no absolutise).

Used by/with: hooks/shared/project_scope.py (`project_memory_root`), consumed
by session_init, claude_md_manager, session_resume, staleness,
bootstrap_prompt_gate, bootstrap_marker_writer, realign_team and
pin_marker_writer.

Every repository lives under tmp_path. The arm set is A1-A8 from
docs/architecture/worktree-lead-claude-md.md §2.
"""
from __future__ import annotations

import os
import subprocess

import pytest

import shared.project_scope as project_scope
from shared.project_scope import project_memory_root


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
    (main / "README").write_text("seed\n")
    _git("add", ".", cwd=main)
    _git("commit", "-qm", "seed", cwd=main)
    return main


def _worktree(main, name):
    path = main / ".worktrees" / name
    _git("worktree", "add", "-q", str(path), "-b", name, cwd=main)
    return path


def test_a1_main_checkout_returns_input(repo):
    assert project_memory_root(repo) == repo


def test_a2_linked_worktree_returns_main_checkout(repo):
    worktree = _worktree(repo, "wt-a2")
    assert project_memory_root(worktree) == repo.resolve()


def test_a3_subdirectory_of_a_worktree_returns_main_checkout(repo):
    worktree = _worktree(repo, "wt-a3")
    subdir = worktree / "pkg"
    subdir.mkdir()
    assert project_memory_root(subdir) == repo.resolve()


def test_a4_non_git_directory_returns_input(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert project_memory_root(plain) == plain


def test_a5a_separate_git_dir_returns_input(tmp_path):
    """A submodule and a `--separate-git-dir` repo both have git-dir ==
    common-dir, so the discriminator never fires. This arm builds the
    separate-git-dir shape, which is the same topology."""
    checkout = tmp_path / "sep-checkout"
    checkout.mkdir()
    _git(
        "init", "-q", f"--separate-git-dir={tmp_path / 'sep.git'}", ".",
        cwd=checkout,
    )
    assert project_memory_root(checkout) == checkout


def test_a5b_worktree_of_a_bare_repo_returns_input(tmp_path):
    """The discriminator fires (git-dir != common-dir) but the candidate is
    not a checkout root, so the verification refuses it."""
    src = tmp_path / "src"
    src.mkdir()
    _git("init", "-q", ".", cwd=src)
    (src / "README").write_text("seed\n")
    _git("add", ".", cwd=src)
    _git("commit", "-qm", "seed", cwd=src)
    bare = tmp_path / "bare.git"
    _git("clone", "-q", "--bare", str(src), str(bare), cwd=tmp_path)
    worktree = tmp_path / "bare-wt"
    _git("worktree", "add", "-q", str(worktree), cwd=bare)
    assert project_memory_root(worktree) == worktree


def test_a6_inherited_git_dir_is_stripped(repo, monkeypatch):
    """An inherited GIT_DIR makes every directory report one repository; the
    helper's git calls run without the location variables."""
    worktree = _worktree(repo, "wt-a6")
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    assert project_memory_root(worktree) == repo.resolve()


def test_a7_git_failure_returns_input(repo, monkeypatch):
    worktree = _worktree(repo, "wt-a7")
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)
    assert project_memory_root(worktree) == worktree


def test_a8_non_worktree_input_is_returned_exactly(tmp_path):
    """No resolve, no absolutise: the comparison the byte-identical guarantee
    makes is a string comparison."""
    given = str(tmp_path / "missing" / ".." / "also-missing")
    assert str(project_memory_root(given)) == given


def test_a9_forged_gitfile_returns_input(repo, tmp_path):
    """SEC-1: a `.git` FILE naming another repo's worktree admin dir makes
    every git answer agree while the directory is not a checkout of it."""
    _worktree(repo, "wt-forged")
    admin = next((repo / ".git" / "worktrees").iterdir())
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n")
    assert project_memory_root(forged) == forged


def test_a9b_forged_gitfile_with_a_relative_path_returns_input(repo, tmp_path):
    """SEC-1, the relative spelling: git resolves the gitfile's path against
    the directory holding it, so the forged layout is identical."""
    _worktree(repo, "wt-forged-rel")
    admin = next((repo / ".git" / "worktrees").iterdir())
    forged = tmp_path / "forged-rel"
    forged.mkdir()
    (forged / ".git").write_text(
        f"gitdir: {os.path.relpath(admin, forged)}\n"
    )
    assert project_memory_root(forged) == forged


def test_a10_a_repo_nested_in_a_worktree_returns_input(repo):
    """A legitimate layout the new listing check must not reject: a nested
    repository is its own main checkout, so it owns its own memory."""
    worktree = _worktree(repo, "wt-nested")
    nested = worktree / "nested"
    nested.mkdir()
    _git("init", "-q", ".", cwd=nested)
    assert project_memory_root(nested) == nested
