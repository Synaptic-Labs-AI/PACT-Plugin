"""
Location: pact-plugin/tests/test_git_metadata_unanswerable.py
Summary: Pins the classifier rule that a git failure on a directory holding
         git metadata (a `.git` entry at or above it) is UNANSWERABLE, never a
         known root equal to the directory.
Used by: pytest. Exercises hooks/shared/project_scope.py
         (`_raw_rev_parse`, `_git_metadata_at_or_above`, `_git_dir_answers`)
         through `_project_memory_root_cause` with real `git init` and
         `git worktree add` repositories under tmp_path (no remote).

Reverting the metadata rule in `_raw_rev_parse` turns the broken-worktree
arms red: the cause becomes "ok" and the root is the worktree itself.
Reverting the `--git-dir` re-probe turns the bare-repo-in-`.git` arm red: its
worktree becomes "unanswerable". Widening the re-probe beyond
`--show-toplevel` turns the `--git-common-dir` shim arm red: the healthy
worktree becomes a KNOWN root equal to itself.

The boundary arms pin that the walk follows git's own discovery path: from
the real directory, stopping before a GIT_CEILING_DIRECTORIES entry (also when
the entry is a symlink) and at a filesystem change unless
GIT_DISCOVERY_ACROSS_FILESYSTEM is true. Dropping any one of those limits
turns its arm red. The filesystem boundary is a unit arm only (patched
st_dev); a real mount inside a repository is not measured end to end.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shared import project_scope
from shared.project_scope import (
    ROOT_CAUSE_OK,
    ROOT_CAUSE_UNANSWERABLE,
    _project_memory_root_cause,
)


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


def _main(tmp_path: Path, name: str = "main") -> Path:
    main = tmp_path / name
    main.mkdir()
    _git("init", "-q", ".", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=main)
    return main


def _no_git_above(path: Path) -> None:
    assert not any(
        (p / ".git").exists() for p in (path, *path.parents)
    ), f"precondition: {path} must have no .git at or above it"


def test_a_moved_main_checkout_makes_its_worktree_unanswerable(tmp_path):
    main = _main(tmp_path)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    main.rename(tmp_path / "moved")

    root, cause = _project_memory_root_cause(worktree)

    assert cause == ROOT_CAUSE_UNANSWERABLE, (root, cause)


def test_a_deleted_admin_dir_makes_its_worktree_unanswerable(tmp_path):
    main = _main(tmp_path)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    shutil.rmtree(main / ".git" / "worktrees" / "wt")

    root, cause = _project_memory_root_cause(worktree)

    assert cause == ROOT_CAUSE_UNANSWERABLE, (root, cause)


def test_an_unreadable_git_entry_fails_closed(tmp_path, monkeypatch):
    """`lstat` raising anything but absence counts as metadata present."""
    directory = tmp_path / "plain"
    directory.mkdir()
    _no_git_above(directory)
    real_lstat = os.lstat

    def denying_lstat(path, *args, **kwargs):
        if Path(path).name == ".git":
            raise PermissionError(13, "denied", str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(project_scope.os, "lstat", denying_lstat)

    assert project_scope._git_metadata_at_or_above(directory) is True


def test_a_plain_directory_with_no_git_above_stays_a_known_root(tmp_path):
    """Control: git exits nonzero and there is no metadata, so it answered."""
    directory = tmp_path / "plain"
    directory.mkdir()
    _no_git_above(directory)

    root, cause = _project_memory_root_cause(directory)

    assert (root, cause) == (directory, ROOT_CAUSE_OK)


def test_a_healthy_worktree_still_resolves_to_main(tmp_path):
    """Control: the rule leaves a working layout unchanged."""
    main = _main(tmp_path)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)

    root, cause = _project_memory_root_cause(worktree)

    assert (root, cause) == (main.resolve(), ROOT_CAUSE_OK)


def test_a_worktree_of_a_bare_repository_kept_in_dot_git_stays_known(tmp_path):
    """The bare repo at `<dir>/.git` refuses `--show-toplevel` (no work tree)
    while `--git-dir` succeeds: a per-flag refusal, not a git failure, so its
    worktree keeps the bare-repository answer (KNOWN, the worktree itself)."""
    source = _main(tmp_path, "source")
    project = tmp_path / "project"
    project.mkdir()
    _git("clone", "-q", "--bare", str(source), str(project / ".git"),
         cwd=tmp_path)
    worktree = project / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt",
         cwd=project / ".git")

    root, cause = _project_memory_root_cause(worktree)

    assert (root, cause) == (worktree, ROOT_CAUSE_OK)


def test_a_common_dir_failure_at_a_healthy_worktree_is_unanswerable(
    tmp_path, monkeypatch
):
    """Only a `--show-toplevel` refusal is re-probed. A git that fails
    `--git-common-dir` alone, while `--git-dir` works, must still be
    could-not-run: re-probing it into an answer gives (None, True), and the
    classifier reads a None common dir as KNOWN = the worktree."""
    main = _main(tmp_path)
    worktree = tmp_path / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    real_git = shutil.which("git")
    assert real_git, "precondition: git on PATH"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "--git-common-dir" ]; then exit 1; fi\n'
        "done\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{os.environ['PATH']}")

    root, cause = _project_memory_root_cause(worktree)

    assert cause == ROOT_CAUSE_UNANSWERABLE, (root, cause)


def _untracked_below(tmp_path: Path) -> tuple[Path, Path]:
    repo = _main(tmp_path, "repo")
    inner = repo / "sub" / "x"
    inner.mkdir(parents=True)
    return repo, inner


def test_a_ceiling_inside_a_healthy_repo_keeps_a_dir_below_it_known(
    tmp_path, monkeypatch
):
    """B1 boundary (a), the auditor's layout: a plain dir `main/sub/proj` in a
    healthy repo with GIT_CEILING_DIRECTORIES=`main/sub`. git stops before the
    ceiling, never reaches `main/.git`, and its refusal is "no repository"."""
    main = _main(tmp_path)
    proj = main / "sub" / "proj"
    proj.mkdir(parents=True)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(main / "sub"))

    root, cause = _project_memory_root_cause(proj)

    assert (root, cause) == (proj, ROOT_CAUSE_OK)


def _git_discovers(start: Path, ceilings: str) -> bool:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CEILING_DIRECTORIES=ceilings, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--git-dir"],
        env=env, capture_output=True, timeout=30, check=False,
    ).returncode == 0


@pytest.mark.parametrize(
    "spelling",
    [
        "{sub}",            # ceiling between the start and the repo
        "{main}",           # ceiling at the repo root
        "{lsub}",           # symlinked entry, resolved by git
        "{empty}{lsub}",    # symlinked entry after an empty one: NOT resolved
        "{lsub}{empty}",    # empty entry after: the symlink is still resolved
        "main/sub",         # relative entry: ignored
        "{proj}",           # the start dir itself: it is still checked
        "{sub_start}",      # start AT a ceiling entry: git climbs past it
    ],
)
def test_the_walk_stops_exactly_where_git_discovery_stops(
    tmp_path, monkeypatch, spelling
):
    """Parity with git itself, MEASURED per spelling: in a healthy repo git
    discovers the repository exactly when the walk meets its `.git`."""
    main = _main(tmp_path)
    proj = main / "sub" / "proj"
    proj.mkdir(parents=True)
    (tmp_path / "lsub").symlink_to(main / "sub", target_is_directory=True)
    start = main / "sub" if spelling == "{sub_start}" else proj
    ceilings = spelling.format(
        sub=main / "sub", main=main, lsub=tmp_path / "lsub", proj=proj,
        sub_start=main / "sub", empty=os.pathsep,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", ceilings)

    assert project_scope._git_metadata_at_or_above(start) is _git_discovers(
        start, ceilings
    )


def test_a_ceiling_entry_that_is_a_symlink_still_stops_the_walk(
    tmp_path, monkeypatch
):
    """B1 boundary (d): the entry names the repo root through a symlink."""
    repo, inner = _untracked_below(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(repo, target_is_directory=True)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(link))

    root, cause = _project_memory_root_cause(inner)

    assert (root, cause) == (inner, ROOT_CAUSE_OK)


def test_a_ceiling_above_a_broken_worktree_leaves_it_unanswerable(
    tmp_path, monkeypatch
):
    """B1 boundary (b): the start directory is always checked, so a broken
    gitfile below a ceiling is still git failing on a checkout."""
    gone = tmp_path / "gone"
    gone.mkdir()
    (gone / ".git").write_text(
        f"gitdir: {tmp_path / 'nowhere' / '.git' / 'worktrees' / 'gone'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    root, cause = _project_memory_root_cause(gone)

    assert cause == ROOT_CAUSE_UNANSWERABLE, (root, cause)


def test_a_symlinked_path_into_a_broken_worktree_walks_the_real_path(tmp_path):
    """git discovers from the REAL directory, so the walk does too. Reached
    through a symlink that lives outside the checkout, a subdirectory of a
    broken-gitfile worktree has no `.git` on its lexical ancestors, but git
    meets the worktree's `.git` and fails on it."""
    gone = tmp_path / "gone"
    (gone / "sub").mkdir(parents=True)
    (gone / ".git").write_text(
        f"gitdir: {tmp_path / 'nowhere' / '.git' / 'worktrees' / 'gone'}\n",
        encoding="utf-8",
    )
    alias = tmp_path / "elsewhere" / "alias"
    alias.parent.mkdir()
    alias.symlink_to(gone / "sub", target_is_directory=True)

    root, cause = _project_memory_root_cause(alias)

    assert cause == ROOT_CAUSE_UNANSWERABLE, (root, cause)


@pytest.mark.parametrize(
    ("across", "expected"),
    [
        ("", False), ("1", True), ("true", True), ("yes", True),
        ("TRUE", True), ("on", True), ("2", True), ("-1", True),
        ("0", False), ("false", False), ("no", False), ("off", False),
    ],
)
def test_a_filesystem_boundary_stops_the_walk_unless_git_crosses_it(
    tmp_path, monkeypatch, across, expected
):
    """B1 boundary (c), UNIT ONLY: the parent holding `.git` reports another
    st_dev, as a filesystem mounted inside a repository would. The mount case
    is NOT measured end to end (it needs a real mount); this pins the walk's
    device rule against git's GIT_DISCOVERY_ACROSS_FILESYSTEM semantics."""
    top = tmp_path / "top"
    (top / ".git").mkdir(parents=True)
    start = top / "mnt" / "x"
    start.mkdir(parents=True)
    top_real = os.path.realpath(top)
    real_stat = os.stat

    class _Stat:
        def __init__(self, result, device):
            self._result = result
            self.st_dev = device

        def __getattr__(self, name):
            return getattr(self._result, name)

    def device_split_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if os.path.realpath(path) == top_real:
            return _Stat(result, result.st_dev + 1)
        return result

    monkeypatch.setattr(project_scope.os, "stat", device_split_stat)
    if across:
        monkeypatch.setenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", across)
    else:
        monkeypatch.delenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", raising=False)
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

    assert project_scope._git_metadata_at_or_above(start) is expected


@pytest.mark.parametrize("flag", ["--git-dir", "--git-common-dir"])
def test_a_git_dir_failure_is_never_rescued_by_the_second_test(
    tmp_path, flag
):
    """On a broken gitfile `--git-dir` itself fails, so no field reads as an
    answer: every rev-parse there reports could-not-run."""
    gone = tmp_path / "gone"
    gone.mkdir()
    (gone / ".git").write_text(
        f"gitdir: {tmp_path / 'nowhere' / '.git' / 'worktrees' / 'gone'}\n",
        encoding="utf-8",
    )

    assert project_scope._raw_rev_parse(gone, flag) == (None, False)


def test_a_parent_that_cannot_be_stated_fails_closed(tmp_path, monkeypatch):
    """The device check stats each parent; an OSError there is "cannot tell",
    which counts as metadata present."""
    start = tmp_path / "a" / "b"
    start.mkdir(parents=True)
    _no_git_above(start)
    blocked = os.path.realpath(tmp_path / "a")
    real_stat = os.stat

    def denying_stat(path, *args, **kwargs):
        if os.path.realpath(path) == blocked:
            raise PermissionError(13, "denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(project_scope.os, "stat", denying_stat)
    monkeypatch.delenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", raising=False)
    monkeypatch.delenv("GIT_CEILING_DIRECTORIES", raising=False)

    assert project_scope._git_metadata_at_or_above(start) is True


@pytest.mark.parametrize(
    "value",
    ["1", "2", "-1", "0x10", "1k", " 2", "true", "TRUE", "yes", "on",
     "0", "00", "false", "no", "off", ""],
)
def test_the_boolean_parse_matches_git_for_every_spelling(value):
    """Parity with git's own boolean parser, MEASURED per value through
    `git config --type=bool`, the parser GIT_DISCOVERY_ACROSS_FILESYSTEM goes
    through: the words, the empty string, and any integer (nonzero true)."""
    git_says = subprocess.run(
        ["git", "-c", f"pact.probe={value}", "config", "--type=bool",
         "pact.probe"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()

    assert project_scope._git_env_bool(value) is (git_says == "true")


def test_an_unset_or_unparseable_boolean(monkeypatch):
    """Unset is false. A value git cannot parse makes git refuse to run, so
    the walk reads it as true: it counts more metadata (fail closed)."""
    assert project_scope._git_env_bool(None) is False
    assert project_scope._git_env_bool("maybe") is True


def test_an_unresolved_ceiling_after_an_empty_entry_reaches_a_broken_gitfile(
    tmp_path, monkeypatch
):
    """MEASURED shape: git does not resolve a symlinked ceiling entry that
    comes after an empty entry, so it walks past it and fails on the broken
    `.git` above. The walk must reach it too (UNANSWERABLE). The same entry
    without the empty one IS resolved and stops both (KNOWN)."""
    broken = tmp_path / "M"
    proj = broken / "sub" / "proj"
    proj.mkdir(parents=True)
    (broken / ".git").write_text(
        f"gitdir: {tmp_path / 'nowhere' / '.git' / 'worktrees' / 'M'}\n",
        encoding="utf-8",
    )
    link = tmp_path / "L"
    link.symlink_to(broken / "sub", target_is_directory=True)

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", f"{os.pathsep}{link}")
    _root, cause = _project_memory_root_cause(proj)
    assert cause == ROOT_CAUSE_UNANSWERABLE

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(link))
    assert _project_memory_root_cause(proj) == (proj, ROOT_CAUSE_OK)
