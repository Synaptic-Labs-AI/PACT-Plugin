"""Strict tracked-file enumeration for test oracles.

The caller chooses whether deleted index entries stay visible. Filesystem
fallback is for gitless exports only — it refuses when a ``.git`` entry exists
at or above the root — and a repository query failure never widens the
population to untracked files.
"""

import subprocess
from pathlib import Path, PurePosixPath


def _git(repo_root, *args):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
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
    """
    return any((candidate / ".git").exists()
               for candidate in (repo_root, *repo_root.parents))


def _tracked_population(repo_root, root, relative, pathspec, pattern,
                        keep_deleted):
    """The git-index population, or the gitless export fallback."""
    result = _git(repo_root, "ls-files", "-z", "--",
                  f":(glob){pathspec}" if pattern is not None else pathspec)
    if result is not None and result.returncode == 0:
        files = [
            root / PurePosixPath(entry).relative_to(relative)
            for entry in result.stdout.split("\0")
            if entry
        ]
        if not keep_deleted:
            files = [path for path in files if path.is_file()]
        return files, "git index"
    if result is None or _outside_repository(repo_root):
        if _inside_git_worktree(repo_root):
            raise AssertionError(
                f"git is unavailable for {repo_root} but a .git entry exists at or "
                "above it; refusing the filesystem fallback because it cannot tell "
                "tracked from untracked files.")
        candidates = root.rglob("*") if pattern is None else root.glob(pattern)
        return ([path for path in candidates if path.is_file()],
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
    repo_root = Path(repo_root)
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
        if member.resolve() not in {path.resolve() for path in files}:
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
