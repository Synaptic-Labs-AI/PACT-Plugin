"""
Location: pact-plugin/tests/test_managed_document.py
Summary: Pins the one managed-document resolver in
         hooks/shared/claude_md_manager.py: `resolve_managed_document` probes
         the root's two canonical paths, then (only for a main-checkout
         subdirectory) its verified enclosing checkout, else names the create
         target at the root; `session_document` pairs it with
         `project_scope.session_memory_root` in one observation.
Used by: pytest, with real `git init` repositories under tmp_path (no remote).

Each arm's docstring names the change that turns it red. Nothing here may
reach the user's global CLAUDE.md: CLAUDE_CONFIG_DIR always points under
tmp_path.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from shared import project_scope
from shared.claude_md_manager import (
    ManagedDocument,
    resolve_managed_document,
    session_document,
)
from shared.project_scope import ROOT_CAUSE_MISSING, SessionRoot


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


def _doc(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# doc\n", encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_CEILING_DIRECTORIES", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
    monkeypatch.setattr(
        project_scope, "get_worktree_identity_from_session_record", lambda: {}
    )
    return monkeypatch


@pytest.fixture
def main(tmp_path, env):
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=repo)
    return repo.resolve()


def test_the_preferred_path_wins_at_the_root(main):
    preferred = _doc(main / ".claude" / "CLAUDE.md")
    _doc(main / "CLAUDE.md")

    assert resolve_managed_document(main) == ManagedDocument(preferred, main, True)


def test_the_legacy_path_is_used_when_it_is_the_only_one(main):
    legacy = _doc(main / "CLAUDE.md")

    assert resolve_managed_document(main) == ManagedDocument(legacy, main, True)


def test_a_subdirectory_with_no_document_uses_its_checkouts(main):
    """Rung 2 (M2). Red if the enclosing rung is dropped: the answer becomes
    the subdirectory's create target."""
    doc = _doc(main / ".claude" / "CLAUDE.md")
    sub = main / "pkg"
    sub.mkdir()

    assert resolve_managed_document(sub) == ManagedDocument(doc, main, True)


def test_a_subdirectorys_own_document_wins_over_its_checkouts(main):
    _doc(main / ".claude" / "CLAUDE.md")
    own = _doc(main / "pkg" / ".claude" / "CLAUDE.md")

    assert resolve_managed_document(main / "pkg") == ManagedDocument(
        own, main / "pkg", True
    )


def test_the_create_target_is_always_at_the_root(main):
    """No document anywhere: create at the subdirectory, never at main. Red if
    the create target follows the enclosing checkout."""
    sub = main / "pkg"
    sub.mkdir()

    assert resolve_managed_document(sub) == ManagedDocument(
        sub / ".claude" / "CLAUDE.md", sub, False
    )


def test_a_document_about_to_be_written_counts_as_present(main):
    target = main / "CLAUDE.md"

    assert resolve_managed_document(main, assume_present=target) == (
        ManagedDocument(main / "CLAUDE.md", main, True)
    )


def test_assume_present_reaches_the_enclosing_checkout(main):
    sub = main / "pkg"
    sub.mkdir()
    target = main / ".claude" / "CLAUDE.md"

    assert resolve_managed_document(sub, assume_present=target) == (
        ManagedDocument(target, main, True)
    )


def test_the_cwd_is_never_probed(main, tmp_path, env):
    """A plain directory with no document, while the cwd is a repository
    holding one: the answer is the plain directory's create target. Red if a
    cwd or git fall-through rung comes back."""
    _doc(main / ".claude" / "CLAUDE.md")
    plain = tmp_path / "plain"
    plain.mkdir()
    env.chdir(main)

    assert resolve_managed_document(plain) == ManagedDocument(
        plain / ".claude" / "CLAUDE.md", plain, False
    )


def test_the_user_global_document_is_never_reached(tmp_path, env):
    """N10: $HOME is a git repository holding the user-global
    `$HOME/.claude/CLAUDE.md`; a project under it with no document gets its
    own create target, never the global file."""
    home = tmp_path / "home"
    home.mkdir()
    _git("init", "-q", ".", cwd=home)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=home)
    _doc(home / ".claude" / "CLAUDE.md")
    env.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    project = home / "projects" / "app"
    project.mkdir(parents=True)

    resolved = resolve_managed_document(project)

    assert resolved == ManagedDocument(
        project / ".claude" / "CLAUDE.md", project, False
    )


def test_a_known_session_root_gets_its_document(main, env):
    doc = _doc(main / ".claude" / "CLAUDE.md")
    env.setenv("CLAUDE_PROJECT_DIR", str(main))

    assert session_document() == (
        SessionRoot(main, True, main, None), ManagedDocument(doc, main, True)
    )


def test_an_unknown_session_root_has_no_document(tmp_path, env):
    gone = tmp_path / "gone"
    env.setenv("CLAUDE_PROJECT_DIR", str(gone))

    assert session_document() == (
        SessionRoot(gone, True, None, ROOT_CAUSE_MISSING), None
    )
