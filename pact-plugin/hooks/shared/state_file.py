"""
Location: pact-plugin/hooks/shared/state_file.py
Summary: The one write path for PACT's small JSON state files: a sidecar lock,
         a temp file, fsync, and an atomic replace. Readers take no lock.
         Every read and write must stay inside a caller-supplied root.
         Also a lock-free single-write append for an append-only log
         (append_line).
Used by: shared/background_work.py (the background-work registry and the
         unflagged idle counter), shared/claude_md_drift.py (the CLAUDE.md
         drift records), shared/pin_credit.py (the pin-cap credit record) and
         shared/cloud_roster.py (the append-only cloud roster, via read_text
         and append_line).

UNIX-ONLY, like the rest of PACT. `fcntl` is imported with no fallback, so no
state file is ever written without a lock.

WRITERS LOCK A SIDECAR, NEVER THE DATA FILE. The data file is replaced on
every write, so a lock taken on it would be held on an inode that a waiter
then overwrites with stale content. `<name>.lock` beside the file is never
replaced, so every writer serialises on the same inode for the whole
read-modify-write.

A READER NEEDS NO LOCK. `os.replace` swaps the name in one step, so a reader
opens either the whole previous file or the whole new one, never a torn one.

CONTAINMENT. The file's directory is resolved with `os.path.realpath` and must
stay inside the resolved `root`, so a symlinked team directory pointing outside
`teams/` is refused rather than followed. Both sides are resolved, so a
symlinked config root, or a team link to another folder inside `teams/`, still
works. Every open then uses the resolved directory, so the check and the open
name the same place. A refused write raises OSError; a refused read raises
FileNotFoundError, which callers already treat as "no file".
Remaining, and adversarial-only: a process swapping a path component between
the resolve and the open can redirect one write. That needs a deliberate race
inside the user's own config root by something already able to write there.

Stdlib only, and nothing from `shared`: a hook that needs a state file pays
for this module and nothing else.
"""

from __future__ import annotations

import fcntl  # Unix-only; PACT supports macOS/Linux.
import os
import time
from pathlib import Path
from typing import Any, Callable, Tuple

FILE_MODE = 0o600
DIR_MODE = 0o700

_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_APPEND_FLAGS = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_LOCK_POLL_SECONDS = 0.02
# How long append_line waits for another appender's lock before giving up
# (it then raises TimeoutError and writes nothing). A holder keeps the lock for
# one small write, so this is only reached when a process holds it on purpose.
_APPEND_LOCK_TIMEOUT_SECONDS = 2.0


def _contained_directory(path: Path, root: Path) -> Path:
    """The resolved directory of `path`, or OSError if it leaves the resolved `root`.

    The directory may equal the root: a file directly inside its root is allowed.
    """
    real_root = os.path.realpath(root)
    real_directory = os.path.realpath(path.parent)
    if os.path.commonpath([real_directory, real_root]) != real_root:
        raise OSError(f"state file {path} resolves outside its root {root}")
    return Path(real_directory)


class TooLarge(ValueError):
    """read_text's `max_bytes` bound was exceeded. Not an OSError, so a caller
    that treats OSError as "absent" does not mistake an over-size file for one."""


def read_text(path: Path, root: Path, *, max_bytes: int | None = None) -> str:
    """Return a state file's text, reading without a lock.

    `root` is the directory the file must stay under; a file whose directory
    resolves outside it reads as absent (FileNotFoundError).

    Opens with O_NOFOLLOW, so a symlink at the path raises OSError rather than
    reading its target. Undecodable bytes are replaced rather than raised, so
    a corrupt file parses as empty and the next write rewrites it clean.
    Raises FileNotFoundError when the file does not exist.

    `max_bytes`, when given, bounds the READ itself: at most max_bytes + 1
    bytes are read from the opened file, and TooLarge is raised when more
    than max_bytes arrive. Bounding the read (not a prior stat) leaves no
    window in which the file can grow past the bound.
    """
    path = Path(path)
    try:
        directory = _contained_directory(path, root)
    except OSError as refused:
        raise FileNotFoundError(str(refused)) from refused
    fd = os.open(str(directory / path.name), _READ_FLAGS)
    with os.fdopen(fd, "rb") as f:
        if max_bytes is None:
            data = f.read()
        else:
            data = f.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise TooLarge(f"over {max_bytes} bytes")
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


def _acquire(lock_fd: int, timeout: float | None) -> None:
    """Take the exclusive lock on `lock_fd`. With no timeout, wait as long as
    another writer holds it. With one, poll without blocking and raise
    TimeoutError, an OSError, once `timeout` seconds pass."""
    if timeout is None:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"state file lock not acquired within {timeout} s") from None
            time.sleep(_LOCK_POLL_SECONDS)


def locked_update(
    path: Path,
    apply: Callable[[str], Tuple[str, bool, Any]],
    root: Path,
    timeout: float | None = None,
) -> Any:
    """Read-modify-write one state file under its sidecar lock. Raises OSError;
    callers catch it and fail open.

    `apply(text) -> (new_text, changed, result)`; `result` is returned. `root`
    is the directory the file must stay under; a file whose directory resolves
    outside it is refused with OSError before anything is opened.

    NO FILE IS CREATED FOR A NO-OP. When the file is absent, `apply` runs on
    empty text first, and if that changes nothing its result is returned
    without creating the file, its directory or its sidecar. Otherwise
    `apply` runs AGAIN under the lock on whatever the file holds by then,
    because another writer may have created it in between, so `apply` must
    be safe to call twice.

    Creates missing directories with mode 0o700 and files with mode 0o600,
    because records carry command text. A symlink at the path raises OSError
    on the read, so it is never followed and its target is never written.

    `timeout` bounds the wait for the sidecar lock, in seconds: past it the
    update raises TimeoutError, an OSError, and writes nothing. None waits for
    as long as another writer holds the lock.
    """
    path = Path(path)
    if not os.path.lexists(path):
        _new_text, changed, result = apply("")
        if not changed:
            return result
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    target = _contained_directory(path, root) / path.name
    lock_fd = os.open(str(target.with_name(target.name + ".lock")), _LOCK_FLAGS, FILE_MODE)
    try:
        _acquire(lock_fd, timeout)
        try:
            new_text, changed, result = apply(_current_text(target))
            if changed:
                _replace(target, new_text)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
    return result


def write_text(path: Path, text: str, root: Path) -> None:
    """Replace a state file's whole content under its sidecar lock. Raises OSError."""
    locked_update(path, lambda _current: (text, True, None), root)


def append_line(path: Path, data: bytes, root: Path, *, max_file_bytes: int) -> bool:
    """Append `data` to an append-only log in ONE os.write.

    For logs whose records are small and independent (each `data` one whole
    line, well under PIPE_BUF), where O_APPEND makes each write land whole and
    in one piece on a local filesystem. The only lock is the brief one the
    cap needs (below); there is no sidecar lock file.

    A TORN TAIL IS SEALED FIRST. When the file is non-empty and its last byte
    is not a newline (a crashed or short earlier write), `data` is written
    with a leading newline in the same single write, so the new record does
    not merge with the torn one and get skipped with it.

    THE CAP IS NEVER CROSSED. Returns False, writing nothing, when the file
    plus this write (including any sealing newline) would exceed
    `max_file_bytes`, so a reader that rejects a file over the same bound
    never sees one. The size check, the torn-tail check and the write run
    under an exclusive flock on the log's own descriptor, so concurrent
    appenders (which all go through this function) cannot each see room and
    together overshoot the cap. Readers take no lock: each record still
    lands in one write. The wait for that lock is bounded
    (_APPEND_LOCK_TIMEOUT_SECONDS); past it, TimeoutError (an OSError) is
    raised and nothing is written. Also False when the write is short.
    Raises OSError when the
    directory resolves outside `root`, when a symlink sits at the path
    (O_NOFOLLOW), or on any I/O error. Never creates a directory: the file's
    directory must already exist. A new file is created with mode 0o600.
    """
    path = Path(path)
    target = _contained_directory(path, root) / path.name
    fd = os.open(str(target), _APPEND_FLAGS, FILE_MODE)
    try:
        # Bounded wait (a holder keeps it for one write); held until os.close
        # below, which releases it. Past the bound: TimeoutError (an OSError).
        _acquire(fd, _APPEND_LOCK_TIMEOUT_SECONDS)
        size = os.fstat(fd).st_size
        if size >= max_file_bytes:
            return False
        if size and data[:1] != b"\n" and os.pread(fd, 1, size - 1) != b"\n":
            data = b"\n" + data
        if size + len(data) > max_file_bytes:
            return False
        return os.write(fd, data) == len(data)
    finally:
        os.close(fd)
