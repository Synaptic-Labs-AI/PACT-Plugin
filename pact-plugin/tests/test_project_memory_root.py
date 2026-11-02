"""Arms for the project-memory root classifier, `project_scope.memory_root_of` (#1653).

A lead's project memory belongs to the MAIN checkout when the session's
project dir is a linked worktree; every other layout -- a main checkout, a
subdirectory of one, a non-git directory, a submodule or `--separate-git-dir`
repository, a worktree of a bare repo, and any git failure -- returns the
input EXACTLY as given (no resolve, no absolutise).

Used by/with: hooks/shared/project_scope.py (`memory_root_of`, read here through
the `known_root` / `root_or_input` helpers in tests/helpers.py), consumed
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
from helpers import known_root, root_or_input


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
    assert root_or_input(repo) == repo


def test_a2_linked_worktree_returns_main_checkout(repo):
    worktree = _worktree(repo, "wt-a2")
    assert root_or_input(worktree) == repo.resolve()


def test_a3_subdirectory_of_a_worktree_returns_main_checkout(repo):
    worktree = _worktree(repo, "wt-a3")
    subdir = worktree / "pkg"
    subdir.mkdir()
    assert root_or_input(subdir) == repo.resolve()


def test_a4_non_git_directory_returns_input(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert root_or_input(plain) == plain


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
    assert root_or_input(checkout) == checkout


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
    assert root_or_input(worktree) == worktree


def test_a6_inherited_git_dir_is_stripped(repo, monkeypatch):
    """An inherited GIT_DIR makes every directory report one repository; the
    helper's git calls run without the location variables."""
    worktree = _worktree(repo, "wt-a6")
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    assert root_or_input(worktree) == repo.resolve()


def test_a7_git_failure_returns_input(repo, monkeypatch):
    worktree = _worktree(repo, "wt-a7")
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)
    assert root_or_input(worktree) == worktree


def test_a8_non_worktree_input_is_returned_exactly(tmp_path):
    """No resolve, no absolutise: the comparison the byte-identical guarantee
    makes is a string comparison."""
    given = str(tmp_path / "missing" / ".." / "also-missing")
    assert str(root_or_input(given)) == given


def test_a9_forged_gitfile_returns_input(repo, tmp_path):
    """SEC-1: a `.git` FILE naming another repo's worktree admin dir makes
    every git answer agree while the directory is not a checkout of it."""
    _worktree(repo, "wt-forged")
    admin = next((repo / ".git" / "worktrees").iterdir())
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n")
    assert root_or_input(forged) == forged


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
    assert root_or_input(forged) == forged


def test_a10_a_repo_nested_in_a_worktree_returns_input(repo):
    """NON-REGRESSION GUARD, not a SEC-1 defect pin.

    A legitimate layout the listing check must not reject: a nested repository
    is its own main checkout, so it owns its own memory. It is labelled here
    because it CANNOT redden on the SEC-1 code it sits beside: a nested repo
    has git-dir == common-dir, so the classifier returns `given` at
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
    assert root_or_input(nested) == nested


def test_a11_a_worktree_path_with_a_newline_redirects_to_main(repo, tmp_path):
    """A path containing a newline must survive the worktree listing.

    The line-based porcelain parser splits the record at the newline and
    truncates the path, so the membership guard refuses and the session falls
    back to writing the worktree. `-z` keeps the record whole.
    """
    path = tmp_path / "wt\nnl"
    _git("worktree", "add", "-q", str(path), "-b", "nl", cwd=repo)
    assert root_or_input(path) == repo.resolve()


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
    assert root_or_input(worktree) == repo.resolve()


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

    assert root_or_input(forged_absolute) == forged_absolute
    assert root_or_input(forged_relative) == forged_relative


def test_a12_a_worktree_path_with_a_trailing_space_redirects_to_main(
    repo, tmp_path
):
    """R-3 false rejection: the listing preserves a trailing space, the
    stripped toplevel does not, so a legitimate worktree fell back to itself."""
    path = tmp_path / "wt-space "
    _git("worktree", "add", "-q", str(path), "-b", "space", cwd=repo)
    assert root_or_input(path) == repo.resolve()


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
    assert root_or_input(worktree) == main.resolve()


def test_a13_a_forged_path_one_space_away_is_not_admitted(repo):
    """R-3 false admission: a forged gitfile at the real worktree path plus a
    trailing space strips to a listed entry and returned the victim checkout."""
    real = _worktree(repo, "wt-real-13")
    admin = next((repo / ".git" / "worktrees").iterdir())
    forged = real.parent / (real.name + " ")
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n")
    assert root_or_input(forged) == forged


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
    assert root_or_input(path) == repo.resolve()


def test_a14_a_git_failure_is_reported_unknown(repo, monkeypatch):
    """Item 4: `memory_root_of` reports no root when git cannot run;
    `root_or_input` folds that unknown root back to the input."""
    worktree = _worktree(repo, "wt-unknown")
    monkeypatch.setattr(project_scope, "run_git", lambda *args, **kwargs: None)
    assert known_root(worktree) is None
    assert root_or_input(worktree) == worktree


def test_f1_f_a_missing_directory_is_unknown_while_a_non_git_one_is_known(tmp_path):
    """A deleted project dir and an existing non-git dir both make `git -C`
    exit 128, differing ONLY in stderr. They are different answers: the
    missing one is could-not-run (UNKNOWN), the existing one is a known root.
    Before the fix both were KNOWN, so SessionStart created the missing
    directory and planted `.claude/CLAUDE.md` inside it."""
    missing = tmp_path / "deleted-project"
    present = tmp_path / "present"
    present.mkdir()

    assert not missing.exists()
    assert known_root(missing) is None
    assert known_root(present) == present, (
        "the control shape -- an existing non-git directory -- stopped being "
        "a known root"
    )


def test_a14b_a_non_git_directory_is_a_known_answer(tmp_path):
    """Item 4: "git answered: not a repository" is NOT "git could not answer";
    a plain project directory must keep its writers."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert known_root(plain) == plain


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
    assert root_or_input(checkout) == checkout


# --- F1 (#1653): undecodable path bytes must still classify -----------------
# Four decode sites, each armed below. `os.fsdecode` is how a Linux path with
# arbitrary bytes is carried as str; the helper must decode git's output the
# same way (surrogateescape), never via subprocess's locale decoding.

BAD_NAME = os.fsdecode(b"wt-\xff")


def _bad_worktree(main):
    """A linked worktree whose directory name is not valid UTF-8."""
    path = main / ".worktrees" / BAD_NAME
    _git("worktree", "add", "-q", str(path), "-b", "f1bad", cwd=main)
    return path


def test_f1_a_own_invalid_utf8_path_returns_main(repo):
    """Site 1: `_raw_rev_parse` decodes git's --git-dir/--git-common-dir."""
    worktree = _bad_worktree(repo)
    assert root_or_input(worktree) == repo.resolve()


def test_f1_b_an_ascii_worktree_beside_an_invalid_one_returns_main(repo):
    """Site 2: the worktree LISTING decodes every worktree path, so one bad
    sibling poisons all of them. Different function, different fixture from
    arm A -- an A-only fix leaves this red."""
    _bad_worktree(repo)
    worktree = _worktree(repo, "wt-f1b")
    assert root_or_input(worktree) == repo.resolve()


def test_f1_c_admin_gitdir_fallback_decodes(repo, monkeypatch):
    """Site 3: the admin `gitdir` file fallback, reached when the worktree
    list cannot answer. It reads raw bytes from disk, not subprocess output."""
    _bad_worktree(repo)
    worktree = _worktree(repo, "wt-f1c")
    real_run_git = project_scope.run_git

    def failing_list(args, **kwargs):
        if "worktree" in args and "list" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout=b"", stderr=b"forced failure"
            )
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", failing_list)
    assert root_or_input(worktree) == repo.resolve()


def test_f1_e_git_output_preserves_an_undecodable_path(repo):
    """Site 4: `_git_output` -- the last-resort line parser and
    `same_repository`'s launcher -- returns the path bytes round-tripped."""
    worktree = _bad_worktree(repo)
    out = project_scope._git_output(worktree, "rev-parse", "--show-toplevel")
    assert out is not None
    assert os.fsencode(out) == os.fsencode(str(worktree))


def test_d2_a_relative_admin_gitdir_record_resolves(repo, monkeypatch):
    """The `worktree.useRelativePaths` shape: the admin `gitdir` file holds
    the worktree's `.git` path RELATIVE to the admin dir that holds it. The
    fallback resolves it there; resolving it against the main root instead
    names a path that is not the worktree, and the classifier then refuses a
    legitimate worktree. `tests/test_admin_gitdir_relative_record.py` pins
    the same rule for a worktree outside the main checkout."""
    worktree = _worktree(repo, "d2-relative")
    admin = repo / ".git" / "worktrees" / "d2-relative"
    assert admin.is_dir(), "the admin record for the new worktree is missing"
    record = os.path.relpath(worktree / ".git", admin)
    (admin / "gitdir").write_text(record + "\n", encoding="utf-8")

    real_run_git = project_scope.run_git

    def failing_list(args, **kwargs):
        if "worktree" in args and "list" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout=b"", stderr=b"forced failure"
            )
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", failing_list)

    assert root_or_input(worktree) == repo.resolve(), (
        "the relative admin-gitdir record was not resolved against the main root"
    )


def test_the_admin_gitdir_read_preserves_a_carriage_return_path(repo, monkeypatch):
    """Copilot MEDIUM: `read_text` performs UNIVERSAL-NEWLINE translation, so
    a worktree named `wt\\rname` was read back as `wt\\nname`: the real
    worktree was rejected and a forged directory at the newline spelling
    passed the membership check and redirected writers. The admin file is now
    read as BYTES and filesystem-decoded, dropping only the record terminator.
    """
    worktrees = repo / ".worktrees"
    worktrees.mkdir(exist_ok=True)
    real = worktrees / "wt\rname"
    _git("worktree", "add", "-q", str(real), "-b", "crwt", cwd=repo)
    admin = next((repo / ".git" / "worktrees").iterdir())

    forged = worktrees / "wt\nname"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n")

    # Force the `-z` listing to fail so the admin-gitdir fallback runs.
    real_run_git = project_scope.run_git

    def failing_list(args, **kwargs):
        if "worktree" in args and "list" in args:
            return subprocess.CompletedProcess(
                args, 1, stdout=b"", stderr=b"forced failure"
            )
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(project_scope, "run_git", failing_list)

    assert root_or_input(real) == repo.resolve(), (
        "the real carriage-return worktree was rejected by the fallback"
    )
    assert known_root(forged) is None, (
        "a forged directory at the newline spelling was admitted"
    )


def test_r9_the_z_listing_is_the_only_source_that_keeps_a_newline_path(
    repo, monkeypatch
):
    """THE `-z` ARM, pinned by removing every fallback that could absorb it.

    The admin-gitdir fallback also preserves a newline, and it absorbed every
    existing fixture, so disabling `-z` reddened nothing. Here the fallback is
    unavailable and the path contains a newline: only the NUL-separated
    listing can produce a record that matches the resolved toplevel. Without
    `-z` the line parser splits the path and the membership check fails.
    """
    worktrees = repo / ".worktrees"
    worktrees.mkdir(exist_ok=True)
    real = worktrees / "wt\nname"
    _git("worktree", "add", "-q", str(real), "-b", "r9wt", cwd=repo)

    monkeypatch.setattr(
        project_scope,
        "_worktree_paths_from_admin_gitdirs",
        lambda _checkout: None,
    )

    assert root_or_input(real) == repo.resolve()
