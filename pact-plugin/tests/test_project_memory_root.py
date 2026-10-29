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
from shared.project_scope import project_memory_root, project_memory_root_or_none


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
    """NON-REGRESSION GUARD, not a SEC-1 defect pin.

    A legitimate layout the listing check must not reject: a nested repository
    is its own main checkout, so it owns its own memory. It is labelled here
    because it CANNOT redden on the SEC-1 code it sits beside: a nested repo
    has git-dir == common-dir, so `project_memory_root` returns `given` at
    that discriminator before the listing check or the candidate line can
    run. MEASURED: removing the listing check leaves this arm green, and no
    single-site mutation was found that reddens it, because every
    implementation that returns either `given` or the nested candidate yields
    the same answer.

    What it does guard is a future refactor that anchors the listing check on
    the OUTER repository, or computes the candidate from the enclosing
    worktree: then a nested repo would resolve to its parent worktree and
    this arm reddens. A10 and A9/A9b therefore hold different properties --
    do not count this arm as coverage of the gitfile defence.
    """
    worktree = _worktree(repo, "wt-nested")
    nested = worktree / "nested"
    nested.mkdir()
    _git("init", "-q", ".", cwd=nested)
    assert project_memory_root(nested) == nested


def test_a11_a_worktree_path_with_a_newline_redirects_to_main(repo, tmp_path):
    """A path containing a newline must survive the worktree listing.

    The line-based porcelain parser splits the record at the newline and
    truncates the path, so the membership guard refuses and the session falls
    back to writing the worktree. `-z` keeps the record whole.
    """
    path = tmp_path / "wt\nnl"
    _git("worktree", "add", "-q", str(path), "-b", "nl", cwd=repo)
    assert project_memory_root(path) == repo.resolve()


def test_a11b_when_z_is_unsupported_the_line_parser_still_resolves(
    repo, monkeypatch
):
    """The fallback is decided by the `-z` call failing, not by a version
    string: an older git keeps today's line parser, which still resolves an
    ordinary path."""
    worktree = _worktree(repo, "wt-fallback")
    real = project_scope.run_git

    def fake_run_git(args, **kwargs):
        if "-z" in args:
            return subprocess.CompletedProcess(args, 129, "", "unknown option")
        return real(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", fake_run_git)
    assert project_memory_root(worktree) == repo.resolve()


def test_a11c_the_fallback_still_guards_forged_gitfiles(repo, tmp_path, monkeypatch):
    """The -z failure path must still apply the SEC-1 guard, not skip it.

    A fallback that returned an empty set (or skipped the membership test)
    would silently reopen the forged-gitfile hole on every git older than
    2.36. Under the forced failure, BOTH forged spellings must still return
    the started directory -- the line parser is doing the guarding.
    """
    _worktree(repo, "wt-fallback-guard")
    admin = next((repo / ".git" / "worktrees").iterdir())
    forged_absolute = tmp_path / "forged-fallback-abs"
    forged_absolute.mkdir()
    (forged_absolute / ".git").write_text(f"gitdir: {admin}\n")
    forged_relative = tmp_path / "forged-fallback-rel"
    forged_relative.mkdir()
    (forged_relative / ".git").write_text(
        f"gitdir: {os.path.relpath(admin, forged_relative)}\n"
    )

    real = project_scope.run_git

    def fake_run_git(args, **kwargs):
        if "-z" in args:
            return subprocess.CompletedProcess(args, 129, "", "unknown option")
        return real(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", fake_run_git)

    assert project_memory_root(forged_absolute) == forged_absolute
    assert project_memory_root(forged_relative) == forged_relative


def test_a12_a_worktree_path_with_a_trailing_space_redirects_to_main(
    repo, tmp_path
):
    """R-3 false rejection: the listing preserves a trailing space, the
    stripped toplevel does not, so a legitimate worktree fell back to itself."""
    path = tmp_path / "wt-space "
    _git("worktree", "add", "-q", str(path), "-b", "space", cwd=repo)
    assert project_memory_root(path) == repo.resolve()


def test_a12b_a_main_checkout_path_with_a_trailing_space_is_a_valid_root(
    tmp_path,
):
    """R-3 on the candidate side: a main root ending in a space must still
    verify as a checkout root, or every worktree under it falls back."""
    main = tmp_path / "main "
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    (main / "README").write_text("seed\n")
    _git("add", ".", cwd=main)
    _git("commit", "-qm", "seed", cwd=main)
    worktree = main / ".worktrees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "sp-main", cwd=main)
    assert project_memory_root(worktree) == main.resolve()


def test_a13_a_forged_path_one_space_away_is_not_admitted(repo):
    """R-3 false admission: a forged gitfile at the real worktree path plus a
    trailing space strips to a listed entry and returned the victim checkout."""
    real = _worktree(repo, "wt-real-13")
    admin = next((repo / ".git" / "worktrees").iterdir())
    forged = real.parent / (real.name + " ")
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n")
    assert project_memory_root(forged) == forged


def test_a11d_a_newline_path_survives_a_forced_z_failure(repo, tmp_path, monkeypatch):
    """Item 2: when `-z` is unavailable, the admin `gitdir` enumeration still
    recovers a newline-containing path, so it redirects to MAIN.

    The old line-parser fallback truncated the path and fell back to the
    worktree. RED by reverting the admin enumeration to the line parser.
    """
    path = tmp_path / "wt\nnl-fallback"
    _git("worktree", "add", "-q", str(path), "-b", "nlfb", cwd=repo)
    real = project_scope.run_git

    def fake_run_git(args, **kwargs):
        if "-z" in args:
            return subprocess.CompletedProcess(args, 129, "", "unknown option")
        return real(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", fake_run_git)
    assert project_memory_root(path) == repo.resolve()


def test_a14_a_git_failure_is_reported_unknown(repo, monkeypatch):
    """Item 4: `project_memory_root_or_none` returns None when git cannot run,
    while `project_memory_root` keeps its fail-safe input for readers."""
    worktree = _worktree(repo, "wt-unknown")
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)
    assert project_memory_root_or_none(worktree) is None
    assert project_memory_root(worktree) == worktree


def test_a14b_a_non_git_directory_is_a_known_answer(tmp_path):
    """Item 4: "git answered: not a repository" is NOT "git could not answer";
    a plain project directory must keep its writers."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert project_memory_root_or_none(plain) == plain


def test_a15_a_separate_git_dir_with_a_trailing_space_returns_input(tmp_path):
    """Item 5: the git-dir/common-dir reads keep a trailing space on the git
    dir path, compared like with like.

    NON-REGRESSION GUARD, not a RED pin: a separate-git-dir repo has
    git-dir == common-dir and returns the input before the candidate check, so
    the stripped read gave the same answer and no single-site ablation reddens
    this arm on git 2.43. It guards a future change that compares a stripped
    git dir against an unstripped one.
    """
    checkout = tmp_path / "sep-checkout"
    checkout.mkdir()
    _git("init", "-q", f"--separate-git-dir={tmp_path / 'sep.git '}", ".",
         cwd=checkout)
    assert project_memory_root(checkout) == checkout
