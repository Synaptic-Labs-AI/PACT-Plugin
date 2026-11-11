"""Strict tracked-file enumeration for test oracles.

The caller chooses whether deleted index entries stay visible. The git index is
the oracle whenever git can answer for this tree. The filesystem fallback is
for gitless exports only — a checkout-free copy nested inside an unrelated
repository counts as one when that repository tracks nothing under it — and it
refuses a ``.git`` entry at or above the root when git is unavailable. A
repository query failure never widens the population to untracked files, and
the fallback never enumerates a path that resolves outside the root.

ACCEPTED BOUNDARY: git cannot tell a checkout-free copy from a directory inside
a tracked repository when that repository has no entry anywhere under the
directory, so such a directory also takes the filesystem fallback. Do not
narrow the test to the work-tree top: a tracked subdirectory must keep its
index answer.
"""

import os
import subprocess
from pathlib import Path, PurePosixPath


def _git(repo_root, *args):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            # Filenames are bytes on POSIX; a strict UTF-8 decode would crash on
            # a non-UTF-8 name instead of enumerating it. surrogateescape keeps
            # the round trip to Path exact.
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except FileNotFoundError as exc:
        if exc.filename == "git":
            return None
        raise


def _outside_repository(repo_root):
    """True only when git positively reports no enclosing repository."""
    probe = _git(repo_root, "rev-parse", "--is-inside-work-tree")
    return (
        probe is None
        or probe.returncode != 0
        and probe.stderr.startswith("fatal: not a git repository ")
    )


def _inside_git_worktree(repo_root):
    """True when a .git entry exists at or above the root.

    An absent git binary and a gitless export are indistinguishable from the
    probe alone; this is the evidence that separates them, so a checkout with
    git stripped from PATH cannot silently fall back to a filesystem walk.
    ``lexists`` rather than ``exists``: a dangling ``.git`` symlink is still a
    checkout marker, and following it would misreport a broken checkout as a
    gitless export.
    """
    return any(os.path.lexists(candidate / ".git")
               for candidate in (repo_root, *repo_root.parents))


def _glob_literal(text):
    """Escape pathspec-glob metacharacters so a literal directory stays literal."""
    for token in ("\\", "*", "?", "["):
        text = text.replace(token, "\\" + token)
    return text


def _pathspec(relative, pattern):
    """The git pathspec for one literal directory, optionally globbed inside it."""
    if pattern is None:
        return f":(literal){relative}"
    return f":(glob){_glob_literal(str(relative))}/{pattern}"


def _toplevel(repo_root):
    """The work-tree root git reports, or None when git cannot name one."""
    probe = _git(repo_root, "rev-parse", "--show-toplevel")
    if probe is None or probe.returncode != 0:
        return None
    reported = probe.stdout.strip()
    return Path(reported) if reported else None


def _copy_inside_unrelated_repository(repo_root):
    """True when git's enclosing repository tracks nothing under this root.

    A checkout-free copy nested inside an unrelated repository (a dotfiles repo
    containing ``~/.claude``) makes ``git ls-files`` succeed against the OUTER
    index instead of reporting no repository. When that index holds no entry
    under the caller's root, the enclosing repository says nothing about this
    tree, so the copy is the documented gitless export. A root that is itself
    the work-tree top, or a tracked subdirectory, keeps its index answer.
    """
    toplevel = _toplevel(repo_root)
    if toplevel is None or toplevel.resolve() == repo_root.resolve():
        return False
    under_root = _git(repo_root, "ls-files", "-z", "--", ":(literal).")
    return (under_root is not None and under_root.returncode == 0
            and not under_root.stdout.strip())


def _filesystem_population(repo_root, root, pattern):
    """Enumerate a gitless export, refusing any path that escapes the root."""
    resolved_root = repo_root.resolve()
    resolved_start = root.resolve()
    if (resolved_start != resolved_root
            and not resolved_start.is_relative_to(resolved_root)):
        raise AssertionError(
            f"the gitless fallback start point escapes the repository: "
            f"{root} resolves to {resolved_start}, outside {resolved_root}")
    # ``Path.glob("**")`` yields only directories, so a bare ``**`` pattern
    # would enumerate nothing and raise as an empty population.
    candidates = (root.rglob("*")
                  if pattern is None or PurePosixPath(pattern) == PurePosixPath("**")
                  else root.glob(pattern))
    files = []
    for path in candidates:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if (resolved != resolved_root
                and not resolved.is_relative_to(resolved_root)):
            raise AssertionError(
                f"the gitless fallback enumerated {path}, which resolves to "
                f"{resolved}, outside the repository root {resolved_root}")
        files.append(path)
    return files


def _tracked_population(repo_root, root, relative, pathspec, pattern,
                        keep_deleted):
    """The git-index population, or the gitless export fallback."""
    result = _git(repo_root, "ls-files", "-z", "--",
                  _pathspec(relative, pattern))
    if result is not None and result.returncode == 0:
        files = [
            root / PurePosixPath(entry).relative_to(relative)
            for entry in result.stdout.split("\0")
            if entry
        ]
        if files or not _copy_inside_unrelated_repository(repo_root):
            if not keep_deleted:
                files = [path for path in files if path.is_file()]
            return files, "git index"
        # The enclosing repository tracks nothing under this root, so its
        # index cannot answer for this tree: the copy is the gitless export.
        return _filesystem_population(repo_root, root, pattern), (
            "gitless filesystem fallback")
    if result is None or _outside_repository(repo_root):
        if _inside_git_worktree(repo_root):
            raise AssertionError(
                f"git is unavailable for {repo_root} but a .git entry exists at or "
                "above it; refusing the filesystem fallback because it cannot tell "
                "tracked from untracked files.")
        return _filesystem_population(repo_root, root, pattern), (
            "gitless filesystem fallback")
    raise AssertionError(
        f"git ls-files failed for {pathspec!r} under {repo_root}: "
        f"status {result.returncode}, stderr {result.stderr.strip()!r}. "
        "Refusing to widen the population with a filesystem fallback."
    )


def tracked_files(repo_root, relative_dir, pattern=None, *, known_member=None,
                  keep_deleted=False, allow_empty=False):
    """Return one nonempty, sorted tracked population or raise AssertionError.

    ``relative_dir`` and ``pattern`` are repository-relative and may not escape
    the root. With no pattern, the whole directory is recursive. Git-index
    entries are retained after deletion only when ``keep_deleted`` is true;
    fallback always enumerates files that currently exist.
    """
    relative = PurePosixPath(relative_dir or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"relative_dir escapes the repository: {relative_dir!r}")
    if pattern is not None:
        glob = PurePosixPath(pattern)
        if glob.is_absolute() or ".." in glob.parts:
            raise ValueError(f"pattern escapes the repository: {pattern!r}")
    pathspec = str(relative) if pattern is None else f"{relative}/{pattern}"
    # Absolute so the .git marker walk and the work-tree comparison see every
    # ancestor: the parents of a relative path stop at the process cwd.
    repo_root = Path(os.path.abspath(repo_root))
    if not repo_root.is_dir():
        raise AssertionError(f"repository root is not a directory: {repo_root}")
    files, source = _tracked_population(
        repo_root, repo_root / Path(relative), relative, pathspec, pattern,
        keep_deleted)
    if not files and not allow_empty:
        raise AssertionError(f"empty tracked population for {pathspec!r} from {source}")
    if known_member is not None:
        member = Path(known_member)
        if not member.is_absolute():
            member = repo_root / member
        # Normalize to an absolute path WITHOUT resolving symlinks: resolve()
        # would let an untracked symlink pointing at a tracked file satisfy the
        # control, and abspath also makes a relative root comparable to an
        # absolute member without touching the filesystem.
        if Path(os.path.abspath(member)) not in {
                Path(os.path.abspath(path)) for path in files}:
            raise AssertionError(f"known member missing: {known_member}")
    return sorted(files)


def tracked_glob(repo, pattern, known_member=None, *, keep_deleted=False,
                 allow_empty=False):
    """Split a repository-relative glob at its first wildcard segment."""
    parts = PurePosixPath(pattern).parts
    split = next(
        (index for index, part in enumerate(parts)
         if any(token in part for token in "*?[")),
        len(parts),
    )
    relative_dir = "/".join(parts[:split]) or "."
    glob = "/".join(parts[split:]) or None
    return tracked_files(
        repo, relative_dir, glob,
        known_member=known_member, keep_deleted=keep_deleted,
        allow_empty=allow_empty,
    )
