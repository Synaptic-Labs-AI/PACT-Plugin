"""archive_pin's scope guard refuses a document outside the declared project.

Location: pact-plugin/tests/test_archive_scope_guard.py

`archive_pin.resolve_claude_md` takes the session's managed document from
`session_document()` and then, when the declared CLAUDE_PROJECT_DIR holds no
document of its own, asks `stays_in_declared_project` whether the document
belongs to the declared project. The session resolver can only reach the
declared root or the main checkout enclosing it, both in the same
repository, so no real layout makes the guard refuse. The guard is defence in
depth, and the only way to see it refuse is to hand it a resolution the
resolver does not produce.

THAT IS WHY THIS FILE, UNLIKE tests/test_archive_pin_resolver.py, REPLACES
`session_document`. The repositories and the scope predicate stay real
(`git init` under tmp_path, no remote). The admitting half for the real
resolver is `TestSameRepositoryFallthroughIsAllowed` in that file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import archive_pin  # noqa: E402
from shared import claude_md_manager
from shared.claude_md_manager import ManagedDocument
from shared.project_scope import SessionRoot


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        timeout=30, check=True,
    ).stdout


def _repo_with_document(path: Path) -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", ".", cwd=path)
    _git("commit", "-q", "--allow-empty", "-m", "seed", cwd=path)
    doc = path / ".claude" / "CLAUDE.md"
    doc.parent.mkdir()
    doc.write_text("# Project\n", encoding="utf-8")
    return doc


def _resolve_with(monkeypatch, declared: Path, doc: Path):
    """Declare `declared` and make the session resolve to `doc`."""
    base = doc.parent.parent
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
    monkeypatch.chdir(declared)
    monkeypatch.setattr(
        claude_md_manager,
        "session_document",
        lambda *_a, **_k: (
            SessionRoot(declared, True, base, None),
            ManagedDocument(doc, base, True),
        ),
    )
    return archive_pin.resolve_claude_md()


def test_a_document_in_another_repository_is_refused(tmp_path, monkeypatch):
    """Revert check: with the guard removed, the archive returns the other
    repository's document and would archive a pin the invocation never
    named, so this goes red."""
    other_doc = _repo_with_document(tmp_path / "other")
    declared = tmp_path / "declared"
    declared.mkdir()
    _git("init", "-q", ".", cwd=declared)

    with pytest.raises(archive_pin._Unevaluable) as exc:
        _resolve_with(monkeypatch, declared, other_doc)

    assert f"CLAUDE_PROJECT_DIR={declared} contains no CLAUDE.md" in exc.value.reason
    assert str(other_doc) in exc.value.reason


def test_the_same_stand_in_admits_the_main_checkouts_document(tmp_path, monkeypatch):
    """Control for the arm above, through the same replaced resolver: a
    declared subdirectory of a main checkout resolving to that checkout's
    document is the same project and is admitted."""
    main_doc = _repo_with_document(tmp_path / "main")
    declared = tmp_path / "main" / "pkg"
    declared.mkdir()

    path, base = _resolve_with(monkeypatch, declared, main_doc)

    assert (path, base) == (main_doc, main_doc.parent.parent)
