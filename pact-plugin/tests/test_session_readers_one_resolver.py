"""
Location: pact-plugin/tests/test_session_readers_one_resolver.py
Summary: Pins that the session readers and the pin-cap gate's target route
         through the one managed-document resolver:
         `staleness.resolve_session_claude_md_path`,
         `claude_md_manager.match_project_claude_md` and
         `claude_md_manager.gate_target`.
Used by: pytest, with real `git init` / `git worktree add` repositories under
         tmp_path (no remote) and CLAUDE_CONFIG_DIR under tmp_path.

Arms:
  - an UNANSWERABLE root (a worktree whose admin dir was deleted) with a
    document in the worktree AND in main: every reader answers None (B2).
    Red if the declared route of `resolve_session_claude_md_path` fails open
    to the worktree again;
  - a declared main-checkout subdirectory with the document at the checkout
    root: every reader names the root's document (M2). Red if the enclosing
    rung is dropped;
  - a subdirectory holding its own document: every reader names it;
  - a worktree session: the gate never gates the worktree's own copy, and a
    supplied SessionRoot is used instead of a second resolution.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shared import claude_md_manager, project_scope
from shared.claude_md_manager import gate_target, match_project_claude_md
from staleness import resolve_session_claude_md_path


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
def main(tmp_path, monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR",
                "GIT_CEILING_DIRECTORIES", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(project_scope, "recorded_project_dir", lambda: "")
    monkeypatch.setattr(
        project_scope, "get_worktree_identity_from_session_record", lambda: {}
    )
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=repo)
    return repo.resolve()


def test_an_unanswerable_root_names_no_document(main, monkeypatch):
    worktree = main / ".worktrees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    main_doc = _doc(main / ".claude" / "CLAUDE.md")
    worktree_doc = _doc(worktree / ".claude" / "CLAUDE.md")
    shutil.rmtree(main / ".git" / "worktrees" / "wt")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    assert resolve_session_claude_md_path() is None
    assert resolve_session_claude_md_path(project_dir=str(worktree)) is None
    assert match_project_claude_md(str(worktree_doc)) is None
    assert match_project_claude_md(str(main_doc)) is None
    assert gate_target(str(worktree_doc)) is None
    assert gate_target(str(main_doc)) is None


def test_a_subdirectory_session_names_its_checkouts_document(main, monkeypatch):
    doc = _doc(main / ".claude" / "CLAUDE.md")
    sub = main / "pkg"
    sub.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sub))
    monkeypatch.chdir(sub)

    assert resolve_session_claude_md_path() == doc
    assert match_project_claude_md(str(doc)) == doc.resolve()
    gated = gate_target(str(doc))
    assert gated is not None and gated.base == main and gated.before == doc


def test_a_subdirectory_with_its_own_document_names_it(main, monkeypatch):
    _doc(main / ".claude" / "CLAUDE.md")
    sub = main / "pkg"
    own = _doc(sub / ".claude" / "CLAUDE.md")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(sub))
    monkeypatch.chdir(sub)

    assert resolve_session_claude_md_path() == own
    assert match_project_claude_md(str(own)) == own.resolve()
    assert gate_target(str(main / ".claude" / "CLAUDE.md")) is None
    gated = gate_target(str(own))
    assert gated is not None and gated.base == sub


def test_a_worktree_session_never_gates_the_worktree_copy(main, monkeypatch):
    worktree = main / ".worktrees" / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    worktree_doc = _doc(worktree / ".claude" / "CLAUDE.md")
    main_doc = main / ".claude" / "CLAUDE.md"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(worktree))
    monkeypatch.chdir(worktree)

    assert gate_target(str(worktree_doc)) is None
    gated = gate_target(str(main_doc))
    assert gated is not None
    assert gated.target == main_doc and gated.before is None and gated.base == main


def test_a_supplied_session_is_the_only_observation(main, monkeypatch):
    doc = _doc(main / ".claude" / "CLAUDE.md")
    session = project_scope.SessionRoot(main, True, main, None)

    def no_second_resolution():
        raise AssertionError("gate_target resolved the session a second time")

    monkeypatch.setattr(project_scope, "session_memory_root", no_second_resolution)

    gated = gate_target(str(doc), session)

    assert gated == claude_md_manager.GateTarget(doc, doc, main)
