"""
Location: pact-plugin/hooks/shared/state_file.py
Summary: The one write path for PACT's small JSON state files: a sidecar lock,
         a temp file, fsync, and an atomic replace. Readers take no lock.
Used by: shared/background_work.py (the background-work registry and the
         unflagged idle counter).

UNIX-ONLY, like the rest of PACT. `fcntl` is imported with no fallback, so no
state file is ever written without a lock.

WRITERS LOCK A SIDECAR, NEVER THE DATA FILE. The data file is replaced on
every write, so a lock taken on it would be held on an inode that a waiter
then overwrites with stale content. `<name>.lock` beside the file is never
replaced, so every writer serialises on the same inode for the whole
read-modify-write.

A READER NEEDS NO LOCK. `os.replace` swaps the name in one step, so a reader
opens either the whole previous file or the whole new one, never a torn one.

Stdlib only, and nothing from `shared`: a hook that needs a state file pays
for this module and nothing else.
"""

from __future__ import annotations

import fcntl  # Unix-only; PACT supports macOS/Linux.
import os
from pathlib import Path
from typing import Any, Callable, Tuple

FILE_MODE = 0o600
DIR_MODE = 0o700

_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def read_text(path: Path, root: Path) -> str:
    """Return a state file's text, reading without a lock.

    `root` is the directory the file must stay under.

    Opens with O_NOFOLLOW, so a symlink at the path raises OSError rather than
    reading its target. Undecodable bytes are replaced rather than raised, so
    a corrupt file parses as empty and the next write rewrites it clean.
    Raises FileNotFoundError when the file does not exist.
    """
    fd = os.open(str(path), _READ_FLAGS)
    with os.fdopen(fd, "rb") as f:
        data = f.read()
    return data.decode("utf-8", errors="replace")


def _current_text(path: Path) -> str:
    """The file's text for a writer already holding the sidecar lock; "" if absent."""
    try:
        fd = os.open(str(path), _READ_FLAGS)
    except FileNotFoundError:
        return ""
    with os.fdopen(fd, "rb") as f:
        data = f.read()
    return data.decode("utf-8", errors="replace")


def _replace(path: Path, text: str) -> None:
    """Write `text` to a fresh temp file beside `path`, fsync it, and swap it in.

    On any failure before the swap, the temp file is removed and the exception
    is re-raised, so `path` still holds its previous content.
    """
    temp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(str(temp), _TEMP_FLAGS, FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def locked_update(
    path: Path,
    apply: Callable[[str], Tuple[str, bool, Any]],
    root: Path,
) -> Any:
    """Read-modify-write one state file under its sidecar lock. Raises OSError;
    callers catch it and fail open.

    `apply(text) -> (new_text, changed, result)`; `result` is returned. `root`
    is the directory the file must stay under.

    NO FILE IS CREATED FOR A NO-OP. When the file is absent, `apply` runs on
    empty text first, and if that changes nothing its result is returned
    without creating the file, its directory or its sidecar. Otherwise
    `apply` runs AGAIN under the lock on whatever the file holds by then,
    because another writer may have created it in between, so `apply` must
    be safe to call twice.

    Creates missing directories with mode 0o700 and files with mode 0o600,
    because records carry command text. A symlink at the path raises OSError
    on the read, so it is never followed and its target is never written.
    """
    path = Path(path)
    if not os.path.lexists(path):
        _new_text, changed, result = apply("")
        if not changed:
            return result
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    lock_fd = os.open(str(path.with_name(path.name + ".lock")), _LOCK_FLAGS, FILE_MODE)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            new_text, changed, result = apply(_current_text(path))
            if changed:
                _replace(path, new_text)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
    return result


def write_text(path: Path, text: str, root: Path) -> None:
    """Replace a state file's whole content under its sidecar lock. Raises OSError."""
    locked_update(path, lambda _current: (text, True, None), root)
