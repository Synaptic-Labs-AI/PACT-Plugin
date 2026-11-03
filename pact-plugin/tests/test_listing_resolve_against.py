"""
Location: pact-plugin/tests/test_listing_resolve_against.py
Summary: Pins `project_scope._listed_worktrees_result`'s `resolve_against`:
         a RELATIVE listed path joins the given directory, never the process
         cwd. No git source yields a relative path today, so this unit arm
         (a stubbed `_worktree_list_paths`) is what keeps the defence live.
Used by: pytest. Exercises hooks/shared/project_scope.py.

Red if `resolve_against` is ignored (the join falls back to the cwd), or if
the default stops being the cwd.
"""
from __future__ import annotations

from shared import project_scope


def _list(monkeypatch, paths):
    monkeypatch.setattr(project_scope, "_worktree_list_paths", lambda checkout: paths)


def test_a_relative_listed_path_joins_resolve_against(tmp_path, monkeypatch):
    main = tmp_path / "main"
    elsewhere = tmp_path / "elsewhere"
    main.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _list(monkeypatch, ["trees/wt", str(main)])

    listed = project_scope._listed_worktrees_result(main, resolve_against=main)

    assert listed == {(main / "trees" / "wt").resolve(), main.resolve()}


def test_without_resolve_against_the_cwd_is_the_base(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _list(monkeypatch, ["trees/wt"])

    listed = project_scope._listed_worktrees_result(tmp_path)

    assert listed == {(elsewhere / "trees" / "wt").resolve()}
