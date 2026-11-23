"""
Location: pact-plugin/tests/test_unset_core_worktree_checkout.py
Summary: A submodule admin dir with `core.worktree` unset records the checkout
git names as its top level, and only that checkout: the comparison is with
the asked checkout itself, not "git named some top level".
Used by: the test suite; no production code imports it.

The resolver asks this only at a `.git` marker's own directory, and for every
layout git makes there (the checkout root, a nested `.git` file, a forged
pointer, a symlinked path, a linked worktree) git names that directory as its
top level, so the two readings agree on production routes. They differ when
the asked directory is not the checkout git names, which the helper's
contract covers.
"""

import os

from fixtures.identity_git import assert_outside_repository, make_repo, run_git

from scripts import project_identity as pi


def _submodule_with_unset_worktree(tmp_path):
    source = make_repo(tmp_path / "source")
    superproject = make_repo(tmp_path / "super")
    run_git(
        "-c", "protocol.file.allow=always", "submodule", "add", "-q", "-f",
        str(source), "mod",
        cwd=superproject,
    )
    admin = superproject / ".git" / "modules" / "mod"
    run_git("--git-dir", str(admin), "config", "--unset", "core.worktree", cwd=superproject)
    return superproject / "mod", admin


def test_git_names_the_marker_directory_in_every_layout_it_makes(tmp_path):
    assert_outside_repository(tmp_path)
    checkout, admin = _submodule_with_unset_worktree(tmp_path)
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(checkout, link)
    worktree = tmp_path / "wt"
    run_git("worktree", "add", "-q", str(worktree), cwd=checkout)

    for directory in (checkout, forged, link, worktree):
        assert pi._git_names_toplevel(directory) is True, directory


def test_a_folder_below_the_checkout_is_not_the_checkout(tmp_path):
    """git, asked below the checkout, names the checkout, not the folder, so
    the admin dir does not record the folder as its checkout."""
    assert_outside_repository(tmp_path)
    checkout, admin = _submodule_with_unset_worktree(tmp_path)
    below = checkout / "below"
    below.mkdir()

    assert pi._git_names_toplevel(below) is False
    assert pi._admin_dir_refusal(admin, below) == pi.FOREIGN_GIT_DIR_DETAIL
    assert pi._admin_dir_refusal(admin, checkout) is None
