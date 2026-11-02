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


# ---------------------------------------------------------------------------
# One arm per broken layout: the classifier answers UNANSWERABLE AND a lead
# SessionStart there writes nothing and says why. Each layout is real git
# under tmp_path. Two rows put a `git` shim first on PATH: the exit-1 row
# (git itself never exits 1 for rev-parse at a healthy worktree), and the
# ownership rows, whose shim answers every call that names the protected
# directory (or runs inside it) exactly as git refuses a repository it does
# not trust: "fatal: detected dubious ownership" on stderr and exit 128. That
# makes the ownership rows deterministic on any git. git's own
# GIT_TEST_ASSUME_DIFFERENT_OWNER switch drives two further rows, run only
# where the git under test honours it (a CI runner's git did not).
#
# Revert check for every row: without the metadata rule in `_raw_rev_parse`
# the cause is "ok" and the root is the directory itself, so the classifier
# assertion goes red, and the lead start creates `.claude/CLAUDE.md` in the
# worktree (or the main checkout, for the main row), so the no-write
# assertion goes red too.
# ---------------------------------------------------------------------------

_SKIP_LINE = (
    "Project CLAUDE.md skipped: git could not answer, so the project memory "
    "target is unknown"
)


def _exit_one_git_on_path(tmp_path, monkeypatch):
    shim = tmp_path / "shim"
    shim.mkdir()
    git = shim / "git"
    git.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")


def _dubious_git_on_path(tmp_path, monkeypatch, protected):
    """A `git` that refuses `protected` the way git refuses an untrusted
    repository, and runs the real git for everything else."""
    import shlex

    real = shutil.which("git")
    assert real, "git is not installed"
    shim = tmp_path / "shim"
    shim.mkdir()
    git = shim / "git"
    quoted = shlex.quote(str(protected))
    git.write_text(
        "#!/bin/sh\n"
        f"protected={quoted}\n"
        'case "$PWD" in "$protected"|"$protected"/*) refuse=1 ;; esac\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in "$protected"|"$protected"/*) refuse=1 ;; esac\n'
        "done\n"
        'if [ -n "$refuse" ]; then\n'
        "  echo \"fatal: detected dubious ownership in repository at '$protected'\" >&2\n"
        "  exit 128\n"
        "fi\n"
        f"exec {shlex.quote(real)} \"$@\"\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
    # Control: the shim refuses the protected directory as git does, and is
    # the real git everywhere else -- so a red arm is the refusal, not a
    # broken shim.
    refused = subprocess.run(
        ["git", "-C", str(protected), "rev-parse", "--git-dir"],
        capture_output=True, text=True, timeout=30,
    )
    assert refused.returncode == 128, refused
    assert "detected dubious ownership" in refused.stderr, refused.stderr
    answered = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, timeout=30
    )
    assert answered.returncode == 0 and answered.stdout.startswith("git version")


def _git_honours_the_ownership_switch(tmp_path) -> bool:
    probe = tmp_path / "ownership-probe"
    probe.mkdir()
    _git("init", "-q", ".", cwd=probe)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
    refused = subprocess.run(
        ["git", "-C", str(probe), "rev-parse", "--git-dir"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return refused.returncode != 0 and "dubious ownership" in refused.stderr


def _broken(tmp_path, monkeypatch, row):
    """Build `row` and return (the directory to start in, its main checkout)."""
    main = _main(tmp_path)
    worktree = main / ".worktrees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    if row == "exit_one_stub":
        _exit_one_git_on_path(tmp_path, monkeypatch)
        return worktree, main
    if row == "moved_main":
        outside = tmp_path / "outside"
        _git("worktree", "move", str(worktree), str(outside), cwd=main)
        main = main.rename(tmp_path / "moved")
        return outside, main
    if row == "deleted_admin":
        shutil.rmtree(main / ".git" / "worktrees" / "wt")
        return worktree, main
    if row == "dubious_worktree":
        _dubious_git_on_path(tmp_path, monkeypatch, worktree)
        return worktree, main
    if row == "dubious_main":
        _dubious_git_on_path(tmp_path, monkeypatch, main)
        return main, main
    if row in ("switch_dubious_worktree", "switch_dubious_main"):
        if not _git_honours_the_ownership_switch(tmp_path):
            pytest.skip(
                "the git under test does not honour GIT_TEST_ASSUME_DIFFERENT_OWNER"
            )
        monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        return (worktree if row.endswith("worktree") else main), main
    raise AssertionError(row)


def _lead_start(monkeypatch, project_dir):
    import io
    import json
    from unittest.mock import patch

    import session_init

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    stdin = json.dumps({
        "session_id": "cdcdcdcd-1111-4222-8333-444444444444",
        "source": "startup",
        "agent_type": "PACT:pact-orchestrator",
    })
    with patch("sys.stdin", io.StringIO(stdin)), \
         patch("sys.stdout", new_callable=io.StringIO) as out:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(out.getvalue()).get("systemMessage", "")


_ROWS = [
    "exit_one_stub", "moved_main", "deleted_admin", "dubious_worktree",
    "dubious_main", "switch_dubious_worktree", "switch_dubious_main",
]


@pytest.mark.parametrize("row", [
    "exit_one_stub", "dubious_worktree", "dubious_main",
    "switch_dubious_worktree", "switch_dubious_main",
])
def test_a_broken_layout_is_unanswerable(tmp_path, monkeypatch, row):
    """The rows the arms above do not already build. The moved-main and
    deleted-admin rows have their own classifier arms and appear below only
    for the SessionStart half."""
    start, _main_checkout = _broken(tmp_path, monkeypatch, row)

    root, cause = _project_memory_root_cause(start)

    assert cause == ROOT_CAUSE_UNANSWERABLE, (row, root, cause)


@pytest.mark.parametrize("row", _ROWS)
def test_a_lead_start_in_a_broken_layout_writes_nothing_and_says_why(
    tmp_path, monkeypatch, row
):
    start, main_checkout = _broken(tmp_path, monkeypatch, row)
    monkeypatch.setattr(
        project_scope, "get_worktree_identity_from_session_record", lambda: {}
    )

    message = _lead_start(monkeypatch, start)

    assert _SKIP_LINE in message, (row, message)
    for base in {start, main_checkout}:
        assert not (base / ".claude" / "CLAUDE.md").exists(), (row, base)
        assert not (base / "CLAUDE.md").exists(), (row, base)


def test_the_same_lead_start_in_a_healthy_worktree_creates_mains_document(
    tmp_path, monkeypatch
):
    """Control for the rows above: the start they run really does create a
    document when the root is known, so their no-write assertions are not
    vacuous."""
    main = _main(tmp_path)
    worktree = main / ".worktrees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)

    message = _lead_start(monkeypatch, worktree)

    assert _SKIP_LINE not in message
    assert (main / ".claude" / "CLAUDE.md").exists()
    assert not (worktree / ".claude" / "CLAUDE.md").exists()
