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

import json
import os
import shutil
import subprocess
import sys
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


# -- a corrupt record FIELD is a DECLINE, not a crash ------------------------
#
# The declared-directory NUL is caught at memory_api's boundary, but the
# record's OWN fields are consumed by `record_resolution`, whose os.path calls
# reject a NUL with ValueError. The declaration must be DELETED and the record
# kept: only then is the record consulted at all. A live declaration never
# reaches this branch and would pass on a tree that still crashes.

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def _remove_worktree(main: Path, path: Path) -> None:
    _git("worktree", "remove", "--force", str(path), cwd=main)
    assert not path.exists()


def _in_tree_worktree_removed(tmp_path):
    """An IN-TREE worktree (an out-of-tree one is RECORD_UNRELATED by design)."""
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=repo)
    repo = repo.resolve()
    worktree = repo / ".worktrees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=repo)
    identity = {
        "session_id": SID,
        "declared": os.path.realpath(worktree),
        "worktree": os.path.realpath(worktree),
        "common_dir": str(repo / ".git"),
    }
    _remove_worktree(repo, worktree)
    return repo, worktree, identity


def _write_record(config_root: Path, identity: dict) -> Path:
    record = config_root / "pact-sessions" / "slug" / SID / "worktree-identity.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(identity), encoding="utf-8")
    return record


def _live_reader_env(monkeypatch, config_root: Path) -> None:
    """Let the REAL reader run in-process: defeat the pytest guard and point
    it at the sandboxed config root."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)


def test_a_nul_in_common_dir_declines_instead_of_raising(tmp_path, monkeypatch):
    """A corrupt `common_dir` must DECLINE with the anchor's cause, not crash.

    Red before the fix: the ValueError escapes `record_resolution`, so the
    call raises instead of returning a SessionRoot. The control writes a VALID
    record first and reads it back, which proves the path under test is the
    reader's path; only then is the field corrupted.
    """
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    repo, worktree, identity = _in_tree_worktree_removed(tmp_path)
    config_root = tmp_path / "config"
    record = _write_record(config_root, identity)
    _live_reader_env(monkeypatch, config_root)

    # CONTROL: the record PATH is the reader's path.
    assert project_scope.get_worktree_identity_from_session_record(), (
        "the valid record was not read; the corrupt arm would prove nothing"
    )

    corrupted = dict(identity, common_dir=identity["common_dir"] + "\x00")
    record.write_text(json.dumps(corrupted), encoding="utf-8")
    assert "\x00" in json.loads(record.read_text(encoding="utf-8"))["common_dir"]
    assert not worktree.exists(), (
        "the declaration must be gone for the record to be consulted at all"
    )

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))

    session = session_memory_root()

    assert session == SessionRoot(worktree, True, None, ROOT_CAUSE_MISSING), (
        "a corrupt common_dir changed the outcome instead of declining: "
        f"{session!r}"
    )


def _run_session_init(home: Path, config_root: Path, declared: Path) -> dict:
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("CLAUDE_")
        and not k.startswith("GIT_")
        and k != "PYTEST_CURRENT_TEST"
    }
    env.update(
        HOME=str(home),
        CLAUDE_CONFIG_DIR=str(config_root),
        CLAUDE_CODE_SESSION_ID=SID,
        CLAUDE_PROJECT_DIR=str(declared),
        CLAUDE_PLUGIN_ROOT=str(_HOOKS.parent),
    )
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "session_init.py")],
        input=json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": SID,
            "source": "startup",
            "agent_type": "PACT:pact-orchestrator",
        }),
        capture_output=True, text=True, env=env, cwd=str(home), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_session_init_declines_a_nul_common_dir_instead_of_crashing(
    tmp_path, monkeypatch
):
    """The hook must SKIP cleanly, not fail early with the NUL on stderr.

    The distinction is the arm: a crash produces 'Session team: NOT GENERATED
    (session_init failed early)' or 'Hook warning (session_init): embedded
    null byte', while a decline produces the ordinary skip line.
    """
    home = tmp_path / "home"
    home.mkdir()
    config_root = home / ".claude"
    probe = subprocess.run(
        ["git", "-C", str(home), "rev-parse"],
        capture_output=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    assert probe.returncode != 0, "home sits inside a repository"

    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    _repo, worktree, identity = _in_tree_worktree_removed(tmp_path)
    record = _write_record(config_root, identity)

    # CONTROL: the record PATH is the reader's path (a valid record is read
    # back in-process through the real reader, pointed at the same root).
    _live_reader_env(monkeypatch, config_root)
    assert project_scope.get_worktree_identity_from_session_record(), (
        "the valid record was not read; the child run would prove nothing"
    )

    corrupted = dict(identity, common_dir=identity["common_dir"] + "\x00")
    record.write_text(json.dumps(corrupted), encoding="utf-8")
    assert "\x00" in json.loads(record.read_text(encoding="utf-8"))["common_dir"]
    assert not worktree.exists()

    output = _run_session_init(home, config_root, worktree)
    message = output.get("systemMessage", "")

    assert "Project CLAUDE.md skipped" in message, (
        f"session_init did not decline cleanly: {message[:400]!r}"
    )
    assert "Session team: NOT GENERATED (session_init failed early)" not in message, (
        f"the hook failed early instead of declining: {message[:400]!r}"
    )
    assert "Hook warning (session_init): embedded null byte" not in message, (
        f"the NUL escaped as a hook warning: {message[:400]!r}"
    )
