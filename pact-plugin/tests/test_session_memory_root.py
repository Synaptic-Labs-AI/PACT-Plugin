"""
Location: pact-plugin/tests/test_session_memory_root.py
Summary: Pins the one-anchor, one-root API in hooks/shared/project_scope.py:
         `session_anchor` (CLAUDE_PROJECT_DIR, else the session record, else
         the cwd), `memory_root_of` (the pure classifier), `session_memory_root`
         (record recovery for MISSING and UNANSWERABLE roots only, and only
         when the record's checkout CONTAINS the anchor) and
         `enclosing_main_checkout` (the verified main checkout around a main
         subdirectory, never a core.worktree redirect, never the directory
         holding the user-global CLAUDE.md).
Used by: pytest, with real `git init` / `git worktree add` repositories under
         tmp_path (no remote). The session record is supplied by patching
         `project_scope.get_worktree_identity_from_session_record`, so no live
         session folder is read.

Each arm's docstring names the change that turns it red.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shared import project_scope
from shared.project_scope import (
    ROOT_CAUSE_MISSING,
    ROOT_CAUSE_UNANSWERABLE,
    ROOT_CAUSE_UNVERIFIED,
    SessionRoot,
    enclosing_main_checkout,
    memory_root_of,
    session_anchor,
    session_memory_root,
    unknown_root_reason,
    unknown_root_status,
)

SID = "aaaaaaaa-1111-2222-3333-444444444444"


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
def clean_env(monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_CEILING_DIRECTORIES", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
    monkeypatch.setattr(
        project_scope, "get_worktree_identity_from_session_record", lambda: {}
    )
    return monkeypatch


@pytest.fixture
def main(tmp_path, clean_env):
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=repo)
    return repo.resolve()


def _worktree(main: Path, path: Path, name: str) -> Path:
    _git("worktree", "add", "-q", str(path), "-b", name, cwd=main)
    return path


def _identity(declared: Path, worktree: Path, main: Path) -> dict:
    return {
        "session_id": SID,
        "declared": os.path.realpath(declared),
        "worktree": os.path.realpath(worktree),
        "common_dir": str(main / ".git"),
    }


# -- session_anchor --------------------------------------------------------

def test_the_declaration_wins(tmp_path, clean_env):
    clean_env.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "declared"))
    clean_env.setattr(project_scope, "recorded_project_dir", lambda: "/recorded")

    assert session_anchor() == (tmp_path / "declared", True)


def test_the_record_outranks_the_cwd(tmp_path, clean_env):
    """Red if the record rung is dropped or ordered below the cwd."""
    clean_env.chdir(tmp_path)
    clean_env.setattr(project_scope, "recorded_project_dir", lambda: "/recorded")

    assert session_anchor() == (Path("/recorded"), False)


def test_the_cwd_is_the_last_rung(tmp_path, clean_env):
    clean_env.chdir(tmp_path)

    assert session_anchor() == (tmp_path, False)


def _delete_cwd(tmp_path, monkeypatch) -> Path:
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    return gone


def test_a_deleted_cwd_with_no_pwd_has_no_anchor(tmp_path, clean_env):
    """(i) Nothing names the cwd: anchor None, cause MISSING, no path."""
    _delete_cwd(tmp_path, clean_env)
    clean_env.delenv("PWD", raising=False)

    session = session_memory_root()

    assert session == SessionRoot(None, False, None, ROOT_CAUSE_MISSING)
    assert unknown_root_reason(session.cause, session.anchor) == (
        "the working directory no longer exists"
    )
    assert unknown_root_status(
        "Project CLAUDE.md", session.cause, session.anchor, "nothing written."
    ) == (
        "Project CLAUDE.md skipped: the working directory no longer exists, so "
        "the project memory target is unknown; nothing written."
    )


def test_a_stale_pwd_naming_a_live_directory_is_never_the_anchor(
    tmp_path, clean_env
):
    """(ii) A live PWD is not this process's deleted cwd: still None, and the
    live directory is never classified as a KNOWN root."""
    _delete_cwd(tmp_path, clean_env)
    clean_env.setenv("PWD", str(tmp_path))

    assert session_anchor() == (None, False)
    assert session_memory_root() == SessionRoot(
        None, False, None, ROOT_CAUSE_MISSING
    )


def test_a_pwd_naming_the_removed_cwd_is_the_anchor(tmp_path, clean_env):
    """(iii) PWD absolute and gone: it names the deleted cwd."""
    gone = _delete_cwd(tmp_path, clean_env)
    clean_env.setenv("PWD", str(gone))

    assert session_anchor() == (gone, False)
    assert session_memory_root() == SessionRoot(
        gone, False, None, ROOT_CAUSE_MISSING
    )


@pytest.mark.parametrize("cause", [ROOT_CAUSE_UNANSWERABLE, ROOT_CAUSE_UNVERIFIED])
def test_a_missing_anchor_has_only_the_missing_clause(cause):
    with pytest.raises(ValueError):
        unknown_root_reason(cause, None)


# -- memory_root_of ----------------------------------------------------------

def test_the_classifier_maps_a_worktree_to_main(main, tmp_path):
    worktree = _worktree(main, tmp_path / "wt", "wt")

    assert memory_root_of(worktree) == (main, None)


def test_the_classifier_reports_an_unknown_root_with_no_root(tmp_path, clean_env):
    assert memory_root_of(tmp_path / "absent") == (None, ROOT_CAUSE_MISSING)


# -- session_memory_root -----------------------------------------------------

def test_a_known_root_needs_no_record(main, tmp_path, clean_env):
    worktree = _worktree(main, tmp_path / "wt", "wt")
    clean_env.setenv("CLAUDE_PROJECT_DIR", str(worktree))

    assert session_memory_root() == SessionRoot(worktree, True, main, None)


def test_a_removed_worktree_recovers_main_from_a_containing_record(
    main, clean_env
):
    """MISSING + CONTAINED record → main. Red if recovery is dropped."""
    worktree = _worktree(main, main / ".worktrees" / "wt", "wt")
    identity = _identity(worktree, worktree, main)
    _git("worktree", "remove", "--force", str(worktree), cwd=main)
    clean_env.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    clean_env.setattr(
        project_scope, "get_worktree_identity_from_session_record",
        lambda: identity,
    )

    assert session_memory_root() == SessionRoot(worktree, True, main, None)


def test_an_unanswerable_nested_worktree_recovers_main(main, clean_env):
    """UNANSWERABLE (deleted admin dir) + CONTAINED record → main."""
    worktree = _worktree(main, main / ".worktrees" / "wt", "wt")
    identity = _identity(worktree, worktree, main)
    shutil.rmtree(main / ".git" / "worktrees" / "wt")
    assert memory_root_of(worktree) == (None, ROOT_CAUSE_UNANSWERABLE)
    clean_env.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    clean_env.setattr(
        project_scope, "get_worktree_identity_from_session_record",
        lambda: identity,
    )

    assert session_memory_root() == SessionRoot(worktree, True, main, None)


def test_an_out_of_tree_removed_worktree_is_not_recovered(
    main, tmp_path, clean_env
):
    """An UNRELATED record (the checkout does not contain the anchor) keeps
    the MISSING cause."""
    worktree = _worktree(main, tmp_path / "wt", "wt")
    identity = _identity(worktree, worktree, main)
    _git("worktree", "remove", "--force", str(worktree), cwd=main)
    clean_env.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    clean_env.setattr(
        project_scope, "get_worktree_identity_from_session_record",
        lambda: identity,
    )

    assert session_memory_root() == SessionRoot(
        worktree, True, None, ROOT_CAUSE_MISSING
    )


def test_an_unverified_root_is_never_recovered(main, clean_env):
    """A forged gitfile inside main naming a real worktree's admin dir is
    UNVERIFIED; a record that would contain it must not recover main. Red if
    recovery is widened to UNVERIFIED."""
    real = _worktree(main, main / ".worktrees" / "real", "real")
    admin = _git("rev-parse", "--absolute-git-dir", cwd=real).strip()
    forged = main / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
    assert memory_root_of(forged) == (None, ROOT_CAUSE_UNVERIFIED)
    clean_env.setenv("CLAUDE_PROJECT_DIR", str(forged))
    clean_env.setattr(
        project_scope, "get_worktree_identity_from_session_record",
        lambda: _identity(forged, forged, main),
    )

    assert session_memory_root() == SessionRoot(
        forged, True, None, ROOT_CAUSE_UNVERIFIED
    )


# -- enclosing_main_checkout -------------------------------------------------

def test_a_main_subdirectory_is_enclosed_by_main(main):
    sub = main / "pkg" / "deep"
    sub.mkdir(parents=True)

    assert enclosing_main_checkout(sub) == main


def test_the_checkout_root_itself_has_no_enclosing_checkout(main):
    assert enclosing_main_checkout(main) is None


def test_a_worktree_subdirectory_is_not_enclosed(main, tmp_path):
    """Its git dir differs from its common dir: a linked worktree."""
    worktree = _worktree(main, tmp_path / "wt", "wt")
    sub = worktree / "pkg"
    sub.mkdir()

    assert enclosing_main_checkout(sub) is None


def test_a_core_worktree_redirect_is_not_followed(tmp_path, clean_env):
    """U1: a repository at `outer/repo` with core.worktree = `outer`. Asked at
    `outer/repo/sub`, git reports the toplevel `outer`, an ancestor that is
    not a repository itself and so classifies as its own KNOWN root. Only the
    common-dir match refuses it; red if that match is dropped."""
    outer = tmp_path / "outer"
    repo = outer / "repo"
    repo.mkdir(parents=True)
    _git("init", "-q", ".", cwd=repo)
    _git("config", "core.worktree", str(outer), cwd=repo)
    sub = repo / "sub"
    sub.mkdir()
    assert Path(
        _git("rev-parse", "--show-toplevel", cwd=sub).strip()
    ).resolve() == outer.resolve(), "precondition: the redirect is in effect"

    assert enclosing_main_checkout(sub) is None


def test_a_bare_repository_worktree_subdirectory_is_not_enclosed(
    tmp_path, clean_env
):
    """The worktree of a bare repository classifies as its own root, so only
    the git-dir/common-dir equality (a MAIN checkout, not a worktree) refuses
    it; red if that check is dropped."""
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-q", ".", cwd=source)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=source)
    bare = tmp_path / "bare.git"
    _git("clone", "-q", "--bare", str(source), str(bare), cwd=tmp_path)
    worktree = tmp_path / "bw"
    _git("worktree", "add", "-q", str(worktree), "-b", "bw", cwd=bare)
    sub = worktree / "pkg"
    sub.mkdir()

    assert enclosing_main_checkout(sub) is None


def test_a_dotfiles_home_is_never_the_enclosing_checkout(tmp_path, clean_env):
    """N10: $HOME is a git repository. Its `.claude/CLAUDE.md` is user-global
    both when it is the active config root's and, under another config root,
    as the default profile's global file, so $HOME never encloses. The same
    layout in a directory that is not $HOME does enclose. Red if the
    user-global exclusion is dropped (E would be $HOME)."""
    assert Path.home() == tmp_path / "home"
    for checkout in (tmp_path / "home", tmp_path / "dotfiles"):
        checkout.mkdir()
        _git("init", "-q", ".", cwd=checkout)
        _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=checkout)
        (checkout / "projects" / "app").mkdir(parents=True)
    project = tmp_path / "home" / "projects" / "app"
    clean_env.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / ".claude"))

    assert enclosing_main_checkout(project) is None

    clean_env.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    assert enclosing_main_checkout(project) is None

    other = tmp_path / "dotfiles"
    assert enclosing_main_checkout(other / "projects" / "app") == other.resolve()
