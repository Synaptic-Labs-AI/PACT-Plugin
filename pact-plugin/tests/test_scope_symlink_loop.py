"""
Location: pact-plugin/tests/test_scope_symlink_loop.py
Summary: Pins that project_scope's scope predicates REFUSE (return False)
         when `Path.resolve()` raises RuntimeError, which is what Pythons
         before 3.13 raise on a symlink loop; they must never propagate it.
Used by: pytest. Exercises hooks/shared/project_scope.py
         `stays_in_declared_project` and `same_repository`.

The patched arms raise RuntimeError on every interpreter, so they are red on
any Python when the `RuntimeError` catches are reverted. The real-loop arm
reproduces the shape itself; it is red on a revert only where resolve()
still raises on a loop (Python < 3.13), and passes vacuously on 3.13+.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared import project_scope


@pytest.fixture
def loop(tmp_path):
    first = tmp_path / "loop-a"
    second = tmp_path / "loop-b"
    first.symlink_to(second, target_is_directory=True)
    second.symlink_to(first, target_is_directory=True)
    return first


def _raise_on(monkeypatch, victim: Path):
    real_resolve = Path.resolve

    def resolve(self, *args, **kwargs):
        if str(self) == str(victim):
            raise RuntimeError(f"Symlink loop from {self!r}")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)


def test_a_looping_declaration_is_refused(tmp_path, monkeypatch):
    declared = tmp_path / "declared"
    declared.mkdir()
    _raise_on(monkeypatch, declared)

    assert project_scope.stays_in_declared_project(
        declared, tmp_path, tmp_path / "CLAUDE.md"
    ) is False


def test_a_looping_resolved_root_is_refused(tmp_path, monkeypatch):
    declared = tmp_path / "declared"
    declared.mkdir()
    resolved = tmp_path / "resolved"
    resolved.mkdir()
    _raise_on(monkeypatch, resolved)

    assert project_scope.stays_in_declared_project(
        declared, resolved, resolved / "CLAUDE.md"
    ) is False


def test_same_repository_refuses_a_looping_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(
        project_scope, "_rev_parse_path", lambda directory, flag: tmp_path / ".git"
    )
    _raise_on(monkeypatch, base)

    assert project_scope.same_repository(tmp_path, base) is False


def test_a_real_symlink_loop_never_raises(tmp_path, loop):
    """The measured shape: on Python 3.12, `stays_in_declared_project(loop,
    ...)` raised RuntimeError before the fix."""
    assert project_scope.stays_in_declared_project(
        loop, tmp_path, tmp_path / "CLAUDE.md"
    ) is False
    assert project_scope.stays_in_declared_project(
        tmp_path, loop, loop / "CLAUDE.md"
    ) is False
