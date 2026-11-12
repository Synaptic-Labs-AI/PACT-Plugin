"""Regression arms for fixtures/tracked_files.py population selection.

The helper's only unsafe branch is the filesystem fallback: it must serve a
gitless export and must refuse when the absence of git hides a real checkout.
"""

import io
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from fixtures.tracked_files import tracked_files, tracked_glob


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
    # The root is made absolute before use, so returned paths are absolute and
    # the injected member stays comparable to them.
    assert tracked_files(relative, ".", "*.md",
                         known_member=str(root / "tracked.md")) == [
                             root / "tracked.md"]


def test_relative_root_below_a_checkout_still_refuses(tmp_path, monkeypatch):
    """A relative root's parents stop at the process cwd, so the .git marker
    walk must see the real checkout root: the root is made absolute first."""
    root = _repo(tmp_path / "repo")
    sub = root / "sub"
    sub.mkdir()
    (sub / "untracked.md").write_text("x\n", encoding="utf-8")
    _git_absent(monkeypatch)
    monkeypatch.chdir(sub)
    with pytest.raises(AssertionError, match="refusing the filesystem fallback"):
        tracked_files(Path("."), ".", "*.md")


def test_broken_git_dir_with_git_present_refuses(tmp_path):
    """git spawns but cannot answer for a broken .git directory: the marker
    still gates the fallback, so the population refuses rather than widening
    to untracked files."""
    root = tmp_path / "broken"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "a.md").write_text("x\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="refusing the filesystem fallback"):
        tracked_files(root, "agents", "*.md")


def test_unreadable_index_refuses_to_widen(tmp_path):
    """rev-parse succeeds but ls-files errors on a corrupt index: the
    population is refused, never widened to the filesystem."""
    root = _repo(tmp_path / "repo")
    (root / "untracked.md").write_text("u\n", encoding="utf-8")
    (root / ".git" / "index").write_bytes(b"\x00corrupt")
    with pytest.raises(AssertionError, match="Refusing to widen"):
        tracked_files(root, ".", "*.md")


def test_copy_inside_an_unrelated_repository_uses_the_fallback(tmp_path):
    """The supported install shape: a checkout-free copy with no .git inside a
    dotfiles-style repo. git ls-files succeeds against the OUTER index and
    finds nothing under the copy, which must not read as an empty checkout."""
    home = tmp_path / "home"
    subprocess.run(["git", "init", "-q", str(home)], check=True,
                   capture_output=True)
    copy = home / ".claude" / "plugins" / "cache" / "PACT" / "4.8.34"
    (copy / "agents").mkdir(parents=True)
    (copy / "agents" / "x.md").write_text("x\n", encoding="utf-8")
    assert tracked_files(copy, "agents", "*.md") == [copy / "agents" / "x.md"]


def test_tracked_subdirectory_keeps_its_index_answer(tmp_path):
    """Only a root the enclosing repository tracks NOTHING under may fall back.
    A tracked subdirectory whose query matches nothing stays an empty index
    population, so an untracked needle cannot join."""
    root = _repo(tmp_path / "repo")
    sub = root / "sub"
    sub.mkdir()
    (sub / "tracked.md").write_text("t\n", encoding="utf-8")
    (sub / "untracked.py").write_text("u\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "sub/tracked.md"],
                   check=True, capture_output=True)
    with pytest.raises(AssertionError, match="empty tracked population"):
        tracked_files(sub, ".", "*.py")


def test_missing_root_is_an_assertion_error(tmp_path):
    with pytest.raises(AssertionError, match="not a directory"):
        tracked_files(tmp_path / "missing", ".", "*.md")


def test_gitless_export_locates_a_bare_double_star_population(tmp_path):
    """Path.glob("**") yields only directories, so bare ** must use rglob.

    A real ``git archive`` export, not a monkeypatched missing binary: git is
    present and positively reports no repository, which is the measured shape.
    """
    source = _repo(tmp_path / "repo")
    (source / "pkg" / "sub").mkdir(parents=True)
    (source / "pkg" / "a.md").write_text("a\n", encoding="utf-8")
    (source / "pkg" / "sub" / "b.md").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "-A"],
                   check=True, capture_output=True)
    tree = subprocess.run(["git", "-C", str(source), "write-tree"],
                          check=True, capture_output=True, text=True).stdout.strip()
    archive = subprocess.run(
        ["git", "-C", str(source), "archive", "--format=tar", tree],
        check=True, capture_output=True).stdout
    export = tmp_path / "export"
    export.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as payload:
        payload.extractall(export, filter="data")
    assert not (export / ".git").exists()
    assert tracked_glob(export, "pkg/**") == [
        export / "pkg" / "a.md", export / "pkg" / "sub" / "b.md"]


def test_non_utf8_tracked_filename_is_enumerated(tmp_path):
    root = _repo(tmp_path / "repo")
    name = os.fsdecode(b"caf\xe9.md")
    (root / name).write_bytes(b"x")
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   check=True, capture_output=True)
    assert root / name in tracked_files(root, ".", "*.md")


def test_known_member_control_does_not_follow_symlinks(tmp_path):
    """An untracked symlink to a tracked file must not satisfy the control."""
    root = _repo(tmp_path / "repo")
    (root / "link.md").symlink_to("tracked.md")
    with pytest.raises(AssertionError, match="known member missing"):
        tracked_files(root, ".", "*.md", known_member="link.md")


def test_literal_directory_with_glob_characters_is_not_a_pattern(tmp_path):
    root = tmp_path / "repo"
    (root / "star*dir").mkdir(parents=True)
    (root / "starsXdir").mkdir()
    (root / "star*dir" / "a.md").write_text("a\n", encoding="utf-8")
    (root / "starsXdir" / "b.md").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"],
                   check=True, capture_output=True)
    assert tracked_files(root, "star*dir", "*.md") == [root / "star*dir" / "a.md"]
    assert tracked_files(root, "star*dir") == [root / "star*dir" / "a.md"]


def test_dangling_git_entry_refuses_the_filesystem_fallback(tmp_path, monkeypatch):
    """A broken .git symlink is still a checkout marker, not a gitless export."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").symlink_to(tmp_path / "missing-gitdir")
    (broken / "untracked.md").write_text("x\n", encoding="utf-8")
    _git_absent(monkeypatch)
    with pytest.raises(AssertionError, match="refusing the filesystem fallback"):
        tracked_files(broken, ".", "*.md")


def test_in_root_symlinks_still_enumerate(tmp_path):
    """Containment must not drop legitimate in-root symlinks: only a target
    that resolves outside the export is refused."""
    export = tmp_path / "export"
    real = export / "real"
    real.mkdir(parents=True)
    (real / "a.md").write_text("a\n", encoding="utf-8")
    (export / "alias").symlink_to(real, target_is_directory=True)
    assert tracked_files(export, "alias", "*.md") == [
        export / "alias" / "a.md"]


def test_symlinked_scan_root_must_stay_inside_the_export(tmp_path):
    export = tmp_path / "export"
    outside = tmp_path / "outside"
    export.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n", encoding="utf-8")
    (export / "pkg").symlink_to(outside, target_is_directory=True)
    with pytest.raises(AssertionError, match="escapes the repository"):
        tracked_files(export, "pkg", "*.md")


def test_file_symlink_escaping_the_export_is_refused(tmp_path):
    export = tmp_path / "export"
    outside = tmp_path / "outside"
    export.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n", encoding="utf-8")
    (export / "link.md").symlink_to(outside / "secret.md")
    (export / "a.md").write_text("a\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="outside the repository root"):
        tracked_files(export, ".", "*.md")
