"""Strict tracked-file enumeration for test oracles.

The caller chooses whether deleted index entries stay visible. Filesystem
fallback is for gitless exports only; a repository query failure never widens
the population to untracked files.
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
    except OSError:
        return None


def _outside_repository(repo_root):
    """True only when git positively reports no enclosing repository."""
    probe = _git(repo_root, "rev-parse", "--is-inside-work-tree")
    return (
        probe is None
        or probe.returncode != 0
        and probe.stderr.startswith("fatal: not a git repository ")
    )


def tracked_files(repo_root, relative_dir, pattern=None, *, known_member=None,
                  keep_deleted=False):
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
    root = repo_root / Path(relative)

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
        source = "git index"
    elif result is None or _outside_repository(repo_root):
        candidates = root.rglob("*") if pattern is None else root.glob(pattern)
        files = [path for path in candidates if path.is_file()]
        source = "gitless filesystem fallback"
    else:
        raise AssertionError(
            f"git ls-files failed for {pathspec!r} under {repo_root}: "
            f"status {result.returncode}, stderr {result.stderr.strip()!r}. "
            "Refusing to widen the population with a filesystem fallback."
        )

    if not files:
        raise AssertionError(f"empty tracked population for {pathspec!r} from {source}")
    if known_member is not None:
        member = Path(known_member)
        if not member.is_absolute():
            member = repo_root / member
        if member not in files:
            raise AssertionError(f"known member missing: {known_member}")
    return sorted(files)


def tracked_glob(repo, pattern, known_member=None, *, keep_deleted=False):
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
    )
