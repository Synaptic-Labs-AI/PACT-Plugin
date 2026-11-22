"""
Location: pact-plugin/hooks/shared/git_helpers.py
Summary: Narrow subprocess wrapper for git CLI calls from PACT hooks.
Used by: git_commit_check.get_staged_files, get_staged_file_content,
         check_env_file_in_gitignore, and shared/project_scope.py.

Encapsulates try/except + subprocess boilerplate only. Callers own:
- success-path processing (list/string/tuple conversion)
- fail-open default (what to return when run_git returns None)
- return-type shape

Fail-open contract: run_git returns None on TimeoutExpired or
FileNotFoundError. All other exceptions propagate — this is NARROWER
than gh_helpers.run_gh's `except Exception` catch-all. See arch §8
"two-wrapper rationale" for why the git and gh wrappers have
deliberately different exception postures.
"""

from __future__ import annotations

import contextvars
import os
import stat
import subprocess
from contextlib import contextmanager
from typing import Callable, Iterator, Mapping, Optional, Sequence, TypeVar

_Answer = TypeVar("_Answer")

# ONE PASS'S GIT RESULTS: the CompletedProcess, or the exception, each git
# read gave, keyed by the exact question (`_question`): the resolved directory
# git runs in (the `-C` targets applied to the cwd), the rest of the argv, the
# output mode and the environment variables git reads (every GIT_* and LC_*
# one and `_GIT_READS_FROM_ENV`). Set only by `git_results_pass` (which
# `memory_api.detection_pass` enters), so readers that parse the same answer
# differently share one git command and outside a pass every call runs git.
# A ContextVar, so concurrent passes never share one.
PASS_GIT_RESULTS: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "PASS_GIT_RESULTS", default=None
)


# The environment variables, beside every GIT_* and LC_* one, that can change
# what git answers: which git runs, where its global and system config live,
# and its message language. A change to any other variable (a library setting
# its own, mid-command) is not a different git question.
_GIT_READS_FROM_ENV = frozenset({"PATH", "HOME", "XDG_CONFIG_HOME", "PREFIX", "LANG", "LANGUAGE"})


@contextmanager
def git_results_pass() -> Iterator[None]:
    """One pass of `PASS_GIT_RESULTS`: starts empty and ends with the block.
    A pass opened inside another joins it: it sets and resets nothing."""
    if PASS_GIT_RESULTS.get() is not None:
        yield
        return
    token = PASS_GIT_RESULTS.set({})
    try:
        yield
    finally:
        PASS_GIT_RESULTS.reset(token)


def _question(
    argv: Sequence[str],
    text: bool,
    env: Optional[Mapping[str, str]],
    cwd: "Optional[str | os.PathLike[str]]",
) -> Optional[tuple]:
    """The memo key of git command `argv` run in `cwd` (this process's
    directory when None), or None when its directory will not resolve (then
    it is asked, never shared)."""
    rest = list(map(str, argv[1:]))
    try:
        where = os.getcwd() if cwd is None else os.fspath(cwd)
        while len(rest) >= 2 and rest[0] == "-C":
            where = os.path.join(where, rest[1])
            rest = rest[2:]
        where = os.path.realpath(where)
    except (OSError, ValueError):
        return None
    environment = os.environ if env is None else env
    relevant = tuple(sorted(
        (name, value) for name, value in environment.items()
        if name.startswith(("GIT_", "LC_")) or name in _GIT_READS_FROM_ENV
    ))
    return where, tuple(rest), bool(text), relevant


def answered_once(
    argv: Sequence[str],
    run: Callable[[], _Answer],
    *,
    text: bool,
    env: Optional[Mapping[str, str]],
    cwd: "Optional[str | os.PathLike[str]]" = None,
) -> _Answer:
    """`run()`, the caller's own call of git command `argv`, made once per
    `git_results_pass` for each question (`_question`): a later caller asking
    the same question gets the same CompletedProcess, or the same exception
    raised again. `text`, `env` and `cwd` must be the ones `run` passes; a
    `run` that sets no `cwd=` runs in this process's directory and passes
    none here. Outside a pass, `run()` is called every time. Only a READ may
    come through here: a command that changes the repository must run each
    time it is asked."""
    memo = PASS_GIT_RESULTS.get()
    if memo is None:
        return run()
    key = _question(argv, text, env, cwd)
    if key is None:
        return run()
    if key not in memo:
        try:
            memo[key] = (run(), None)
        except Exception as exc:  # the answer is the failure, kept for every later reader
            memo[key] = (None, exc)
    result, failure = memo[key]
    if failure is not None:
        raise failure
    return result


def run_git(
    args: Sequence[str],
    timeout: int = 5,
    text: bool = True,
    env: Optional[Mapping[str, str]] = None,
    *,
    once_per_pass: bool = False,
) -> Optional[subprocess.CompletedProcess]:
    """
    Invoke `git <args...>` with capture_output and a 5-second default timeout.

    Returns the CompletedProcess on any invocation that reaches git (including
    non-zero exits that the caller needs to triage on `returncode`). Returns
    None on TimeoutExpired or FileNotFoundError (fail-open).

    `text=True` by default so stdout/stderr are str, not bytes. Callers that
    need raw bytes (e.g., binary git output) pass `text=False`.

    Args:
        args: git subcommand + flags (without leading "git"). Example:
              ["diff", "--name-only", "--cached"] or ["check-ignore", "-q", ".env"].
        timeout: seconds before subprocess.TimeoutExpired is raised. Default 5s,
                 matching gh_helpers.run_gh / check_pr_state convention.
        text: decode stdout/stderr as UTF-8 if True (default); return bytes if False.
        env: the git process's ENTIRE environment. None (default) inherits this
             process's environment, which is subprocess's own default.
        once_per_pass: a READ that `answered_once` asks once per
             `git_results_pass`; leave False for anything that changes the
             repository.

    Returns:
        CompletedProcess on success-path (regardless of returncode); None on
        (TimeoutExpired, FileNotFoundError). Callers MUST check for None
        before touching result.returncode or result.stdout.
    """
    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=text,
            timeout=timeout,
            env=env,
        )

    try:
        if once_per_pass:
            return answered_once(["git", *args], run, text=text, env=env)
        return run()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# The most git reads of a `.git` file (`read_gitfile_gently` refuses a
# larger one); the same bound is taken for a worktree's admin `gitdir` file.
GIT_FILE_LIMIT = 1 << 20


def read_git_file(path) -> Optional[str]:
    """The text of a `.git` file or a worktree admin `gitdir` file, decoded
    with `os.fsdecode`, or None when it is not a regular file or is larger
    than GIT_FILE_LIMIT. At most GIT_FILE_LIMIT + 1 bytes are read, whatever
    the file's size. Raises what `os.stat` and `os.open` raise.

    Only a regular file is read, as git's `read_gitfile_gently` reads only an
    `S_ISREG` file. A symlink is followed, because git checks the file with
    `stat`; do not add O_NOFOLLOW, or a symlinked gitfile naming another
    checkout's git dir reads as naming nothing and binds that checkout. The
    type is checked before the open, so a FIFO is never opened, and again on
    the open descriptor; O_NONBLOCK keeps that open from waiting on a FIFO
    put in its place between the two checks. Each flag a platform may lack
    (Windows has no O_NONBLOCK) is taken through getattr with 0.
    """
    if not stat.S_ISREG(os.stat(path).st_mode):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    with open(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = handle.read(GIT_FILE_LIMIT + 1)
    return os.fsdecode(data) if len(data) <= GIT_FILE_LIMIT else None


def worktree_admin_gitdir(text: str) -> str:
    """The path a worktree admin dir's `gitdir` file names, read as git
    reads it (`get_linked_worktree`): the whole file, trailing whitespace
    trimmed."""
    return text.rstrip(" \t\n\v\f\r")
