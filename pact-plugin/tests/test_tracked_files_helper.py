"""Regression arms for fixtures/tracked_files.py population selection.

The helper's only unsafe branch is the filesystem fallback: it must serve a
gitless export and must refuse when the absence of git hides a real checkout.
"""

import subprocess

import pytest

from fixtures.tracked_files import tracked_files


def _repo(root):
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / "tracked.md").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.md"],
                   check=True, capture_output=True)
    return root


def _git_absent(monkeypatch):
    real_run = subprocess.run

    def fake_run(command, *args, **kwargs):
        if command and command[0] == "git":
            raise FileNotFoundError(2, "No such file or directory", "git")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_gitless_worktree_refuses_the_filesystem_fallback(tmp_path, monkeypatch):
    root = _repo(tmp_path / "repo")
    (root / "untracked.md").write_text("untracked\n", encoding="utf-8")
    _git_absent(monkeypatch)
    with pytest.raises(AssertionError, match="refusing the filesystem fallback"):
        tracked_files(root, ".", "*.md")


def test_gitless_export_still_uses_the_filesystem_fallback(tmp_path, monkeypatch):
    export = tmp_path / "export"
    export.mkdir()
    (export / "a.md").write_text("a\n", encoding="utf-8")
    _git_absent(monkeypatch)
    assert tracked_files(export, ".", "*.md") == [export / "a.md"]


def test_absolute_known_member_matches_a_relative_root(tmp_path, monkeypatch):
    root = _repo(tmp_path / "relative-repo")
    monkeypatch.chdir(tmp_path)
    relative = root.relative_to(tmp_path)
    assert tracked_files(relative, ".", "*.md",
                         known_member=str(root / "tracked.md")) == [
                             relative / "tracked.md"]


def test_missing_root_is_an_assertion_error(tmp_path):
    with pytest.raises(AssertionError, match="not a directory"):
        tracked_files(tmp_path / "missing", ".", "*.md")