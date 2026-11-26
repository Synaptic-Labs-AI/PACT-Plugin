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
import re
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
    raised again. A timeout is never kept: it measures how long that caller
    waited, not what git answers, so the next asker, who may wait longer, runs
    git again. `text`, `env` and `cwd` must be the ones `run` passes; a
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
        except subprocess.TimeoutExpired:
            raise
        except Exception as exc:  # the answer is the failure, kept for every later reader
            memo[key] = (None, (exc, exc.__traceback__))
    result, failure = memo[key]
    if failure is not None:
        # Each raise starts from the traceback git's failure had, so the
        # stored exception does not gather one more frame per asker.
        exc, traceback = failure
        raise exc.with_traceback(traceback)
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
    trimmed as git's `strbuf_rtrim` does (git's `isspace`: space, tab, CR
    and LF; a vertical tab or form feed stays in the path)."""
    return text.rstrip(" \t\n\r")


def gitfile_target(text: str) -> Optional[str]:
    """The git dir a `.git` file names, read as git reads it
    (`read_gitfile_gently`): the whole file must start with `gitdir: `,
    exactly one space included; every trailing newline and carriage return
    is dropped; the rest is the path, verbatim. None when the file does not
    start with `gitdir: `, names no path, or is larger than the 1 MiB git
    reads."""
    if len(os.fsencode(text)) > GIT_FILE_LIMIT or not text.startswith("gitdir: "):
        return None
    return text.rstrip("\r\n")[len("gitdir: "):] or None


_GIT_BOOL_TRUE = frozenset({"true", "yes", "on"})
_GIT_BOOL_FALSE = frozenset({"", "false", "no", "off"})
# git's integer form (`git_parse_signed`): C whitespace, an optional sign,
# then a number `strtoimax(..., 0)` reads in full (0x-hex, 0-octal or
# decimal), then at most one k/m/g unit. Nothing may follow.
_GIT_BOOL_INT = re.compile(
    r"[ \t\n\v\f\r]*(?P<sign>[-+]?)"
    r"(?:0[xX](?P<hex>[0-9a-fA-F]+)|(?P<oct>0[0-7]*)|(?P<dec>[1-9][0-9]*))"
    r"(?P<unit>[kKmMgG]?)"
)
_GIT_BOOL_UNITS = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}
_GIT_INT_MAX = (1 << 31) - 1


def git_config_bool(value: str) -> Optional[bool]:
    """`value` read as git's `git_config_bool` reads it, or None where git
    refuses the value and stops (`fatal: bad boolean config value`).

    The words true/yes/on and false/no/off and the empty string, compared
    ASCII case-insensitively, with no whitespace around them. Otherwise an
    integer, true when nonzero: leading whitespace and a sign are allowed,
    `0x` reads hex and a leading `0` reads octal, one k/m/g unit multiplies,
    trailing text of any kind is refused, and a value whose signed value
    times its unit lies outside a C int (-2^31 through 2^31-1) is refused.
    Git versions differ at exactly -2^31 (git 2.43 refuses it, a newer git
    accepts it); this accepts every value any of them accepts. The caller
    decides what an unknown value means for it.
    """
    if value.isascii() and value.lower() in _GIT_BOOL_TRUE:
        return True
    if value.isascii() and value.lower() in _GIT_BOOL_FALSE:
        return False
    match = _GIT_BOOL_INT.fullmatch(value)
    if match is None:
        return None
    if match["hex"] is not None:
        number = int(match["hex"], 16)
    elif match["oct"] is not None:
        number = int(match["oct"], 8)
    elif len(match["dec"]) > len(str(_GIT_INT_MAX)):
        # More digits than any C int has, and no leading zero (that is the
        # octal form): out of range. Bounded BEFORE `int()`, which raises past
        # 4300 decimal digits; hex and octal have no such limit.
        return None
    else:
        number = int(match["dec"])
    factor = _GIT_BOOL_UNITS[match["unit"].lower()]
    limit = _GIT_INT_MAX + 1 if match["sign"] == "-" else _GIT_INT_MAX
    if number > limit // factor:
        return None
    return number != 0


def git_ceiling_directories(value: Optional[str]) -> set:
    """The `GIT_CEILING_DIRECTORIES` entries git honours, as paths (str) a
    marker walk compares its real-path parents with. THE ONE PARSER for
    every walk, so the walks stop where git stops.

    Split on `os.pathsep`; a relative entry is dropped. Before the first
    EMPTY entry each entry is its real path, and an entry with any missing
    component is dropped. git drops one whose missing component is in the
    middle (measured below) and keeps one whose LAST component is missing,
    but such a directory does not exist, so it is never a parent of the
    directory a walk starts from: dropping it stops no walk git would not. After it each entry is kept AS WRITTEN,
    with one trailing `/` removed and nothing else normalised. Measured on
    git 2.43: after the empty entry `<r>/z/../x`, `<r>/x/.`, `<r>//x` and
    `<r>/x//` do not stop discovery at `<r>/x` while `<r>/x/` does; before
    it `<r>/z/../x` and `<r>//x` do, and `<r>/q/../x` with `q` missing
    does not."""
    ceilings = set()
    resolve = True
    for entry in (value or "").split(os.pathsep):
        if not entry:
            resolve = False
            continue
        if not os.path.isabs(entry):
            continue
        if resolve:
            try:
                ceilings.add(os.path.realpath(entry, strict=True))
            except (OSError, ValueError):
                continue
        else:
            ceilings.add(entry[:-1] if len(entry) > 1 and entry.endswith("/") else entry)
    return ceilings


def git_env_bool(value: Optional[str]) -> bool:
    """A git boolean environment value (`GIT_DISCOVERY_ACROSS_FILESYSTEM`),
    read as git reads it (`git_config_bool`). THE ONE READER for every
    marker walk, so the walks agree.

    Unset is false. A value git cannot parse makes git itself refuse to run,
    and it reads as TRUE here: a walk then crosses the boundary and counts
    more metadata, and every git question it leads to fails and declines,
    the fail-closed direction. Reading it as false would stop a walk short
    and could let a folder read as holding no repository.
    """
    if value is None:
        return False
    parsed = git_config_bool(value)
    return True if parsed is None else parsed
