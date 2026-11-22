"""
Location: pact-plugin/tests/test_core_worktree_project_root.py
Summary: With `core.worktree` naming another folder, a start inside the
checkout's `.git` gets the same project-memory root as the checkout folder
itself: the inside-git-dir mapping never makes the two disagree.
Used by: pytest. Real repos, real git, `project_scope.memory_root_of`.
"""

import os
import subprocess

import pytest

from helpers import known_root


def _git(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    result = subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=None if cwd is None else str(cwd), env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("inside", [".git", ".git/hooks"])
def test_a_start_inside_git_gets_the_checkouts_root_under_core_worktree(tmp_path, inside):
    """git answers a top level inside the git dir here (the core.worktree
    folder), so the start is never mapped as one with no top level; the
    project-memory root of `main/<inside>` is the root `main` gets."""
    main = tmp_path / "main"
    tree = tmp_path / "tree"
    tree.mkdir()
    _git("init", "-q", str(main))
    _git("commit", "-q", "--allow-empty", "-m", "c", cwd=main)
    _git("config", "core.worktree", str(tree), cwd=main)
    # Control: git names the core.worktree folder from inside the git dir.
    assert _git("rev-parse", "--show-toplevel", cwd=main / inside).strip() == str(tree.resolve())

    from_checkout = known_root(main)
    assert from_checkout is not None
    assert known_root(main / inside) == from_checkout
