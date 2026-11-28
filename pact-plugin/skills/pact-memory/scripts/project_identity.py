"""
PACT Memory — repository-identity canonicaliser.

Location: pact-plugin/skills/pact-memory/scripts/project_identity.py

Turns a repository's remote URL into the stable project id that pact-memory
keys stored memories by, so one repository resolves to one bucket whatever
directory it was cloned into. Before this module the id was the working
directory's basename, which split one repository across machines and merged
unrelated folders that happened to share a name.

The id is built from the remote URL as written. An ssh host alias
(`git@github-work:o/r`, where `github-work` is a `Host` entry in the ssh
config) stays in the id, because git cannot tell an alias from a host name:
that clone gets `github-work/o/r`, not the `github.com/o/r` an https clone
of the same repository gets. The repository override file
(`<git-common-dir>/pact-project-id`) is the remedy that makes them one id.

Used by:
- memory_api.py — the resolver rungs call `detect_id_for_root`, `read_override`
  and `is_canonical`; nothing here imports memory_api, so the dependency is
  one-way.
- cli.py — `project-id show` reports the detected identity and its provenance.

`database.py` owns the canonical-id grammar (`is_canonical_id`) and the
write-ingress rule (`validate_identifier_for_write`); this module consumes
both at function level and never defines a second copy of either rule.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any, List, NamedTuple, Optional, Tuple
from urllib.parse import quote, unquote

# Accepted remote schemes. The same set `hooks/shared/backlog.py` already
# ships, plus `ssh+git`, an alias git itself still honours (it connects over
# ssh exactly as for `git+ssh`). Two parsers in one repo that disagree about
# which schemes are remotes would be the defect, not the fix. `file` and
# every other scheme is handled as its own outcome rather than being silently
# accepted.
_ACCEPTED_SCHEMES = frozenset({"https", "http", "ssh", "git", "git+ssh", "ssh+git"})

# Schemes that are NOT network remotes: a local path written in URL form. They
# fall through to the basename; every OTHER unsupported scheme DECLINES,
# because a present network remote we cannot represent must not be silently
# downgraded to a folder name.
_LOCAL_SCHEMES = frozenset({"file"})

# The scheme's own default port is dropped so two spellings of one endpoint
# share one id. Non-default ports are preserved: two different servers on one
# host must never merge.
_DEFAULT_PORTS = {
    "https": 443,
    "http": 80,
    "ssh": 22,
    "git": 9418,
    "git+ssh": 22,
    "ssh+git": 22,
}

# `scheme://` — the scheme is matched here and validated against the accepted
# set afterwards, so an unsupported scheme can be classified rather than
# silently treated as a local path.
_SCHEME_PREFIX_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://")

# scp-style `[user@]host:path`, by GIT'S OWN RULE: a value is scp syntax when
# it contains `:` and no `/` precedes the first `:`. The user is optional, so
# `github.com:owner/repo` is the same remote as `git@github.com:owner/repo`.
# The colon is the PATH SEPARATOR in this form and is never a port, so
# `git@host:2222/owner/repo` has path `2222/owner/repo`. An empty path
# (`host:`) still matches, so it classifies as a network remote and DECLINES.
# The one exception, a Windows drive path, is applied by `_scp_match`.
_SCP_FORM_RE = re.compile(
    r"^(?P<user>[^@\s/:\\]+@)?(?P<host>\[[^\]@/\s\\]+\]|[^@:/\s\\]+):(?P<path>.*)$"
)

_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")
# RFC 3986 unreserved characters: a `%XX` naming one of these is decoded.
_UNRESERVED_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
# Characters a segment keeps raw: unreserved plus the RFC 3986 sub-delimiters.
_SEGMENT_RAW_CHARS = _UNRESERVED_CHARS | frozenset("!$&'()*+,;=")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# A hard cap on the override file we are willing to read. The value itself is
# capped at the canonical length, so anything larger is unusable by definition.
_MAX_OVERRIDE_BYTES = 4096

_OVERRIDE_FILENAME = "pact-project-id"

# A hard cap on the user-scope `config.json` read for its `project_id`
# (`read_user_setting`): it holds a few settings, so a larger file is refused.
_MAX_USER_SETTING_BYTES = 1 << 20


class OverrideRead(NamedTuple):
    """One read of `<common_dir>/pact-project-id`.

    `value` is the usable first line, or None. `path` is the file consulted
    (None when it is absent). `reason` is a fixed token naming why a PRESENT
    file was unusable — "not_regular", "not_utf8", "empty", "control_chars",
    "too_long", "not_canonical" or "unreadable". Absence is `value=None,
    path=None`, never a reason. "not_regular" covers a FIFO, a device node
    and a SYMLINK: none is read, because a FIFO in this position would block
    every memory operation that resolves an identity.
    """

    value: Optional[str]
    path: Optional[Path]
    reason: Optional[str]


class OriginRead(NamedTuple):
    """The answer to "what does this repository's origin say?".

    `present`     — git answered with a configured value.
    `absent`      — git answered, no origin is configured, and the
                    repository's own config file holds no include section.
    `include_only` — git answered that no origin is configured, but the
                    repository's own config file holds an include section,
                    so an included file can set the origin on some branch
                    or worktree.
    `config_unread` — git answered that no origin is configured, and the
                    repository's own config file could not be read to look
                    for an include section (larger than the bounded reader
                    takes, or not a regular file).
    `empty_entry` — the url list holds an empty value. Which url git then
                    fetches depends on the git version, so no url in the
                    list is the repository's identity. A whitespace-only
                    value is an ordinary url, not an empty one.
    `not_own_file` — no url comes from the repository's own config file:
                    a url from an included file can differ between its
                    worktrees and branches.
    `unavailable` — git could not answer: missing, timed out, or the
                    repository's configuration could not be read.

    THE SPLIT IS LOAD-BEARING. Collapsing `absent` and `unavailable` into a
    single None makes a broken repository fall back to a basename, which is
    the silent-mis-scope defect this change exists to remove: absent is a
    designed fallback, unavailable is a DECLINE.
    """

    state: str
    value: Optional[str]


class RootIdentity(NamedTuple):
    """The identity detected for one root, with its provenance.

    `project_id` is the id to bind, or None when the root DECLINED.
    `id_shape` is "canonical", "basename" or None. `source` is the rung that
    decided it. `override_ignored` is a structured {path, reason} when a
    PRESENT override file was unusable and resolution continued without it;
    `detect_id_for_root` sets it, and the caller discloses it. Only this
    function reads the file, so a resolver rung that declines before calling
    it, or a home-directory root it never reaches, reports none. Its `path` is
    None (the `reason` is kept) when the checkout's `.git` is a redirect, or
    points at a git directory outside the checkout that is neither a linked
    worktree's nor a submodule's admin dir.
    `decline_reason` is a structured {source, reason, override_path} built by
    `decline_record` for every decline with a named cause (a
    `DECLINE_REASONS` token), so `project-id show` can report it rather than
    leaving the operator with `declined` and no remedy. `source` names the
    rung that declined and is filled in by the caller, which is the only
    layer that knows it. It defaults to None, so the declines without a named
    cause keep their positional shape.
    `detail` is a FIXED sentence (one of this module's `*_DETAIL` constants,
    never user data) naming a narrower cause than the reason token, which the
    resolver appends to its decline warning and refusal text. None otherwise.
    """

    project_id: Optional[str]
    id_shape: Optional[str]
    source: str
    override_ignored: Optional[dict]
    decline_reason: Optional[dict] = None
    detail: Optional[str] = None


# The `detail` for a `.git` marker refused by its target's shape
# (`_redirect_target_refusal`): a directory named `.git`, or a submodule's
# git dir whose `core.worktree` is not the marker's directory, or is unset
# while git, asked there, does not name the marker's directory its top level.
FOREIGN_GIT_DIR_DETAIL = (
    "its `.git` names another directory called `.git`, or a submodule's git "
    "directory that does not record it as that submodule's working tree"
)

# The `detail` for a `.git` marker naming a submodule's git dir whose
# `core.worktree` git could not read.
CORE_WORKTREE_UNREAD_DETAIL = (
    "its `.git` names a submodule's git directory, and git could not read "
    "that directory's core.worktree"
)

# The `detail` for an origin url list holding an empty value.
EMPTY_URL_ENTRY_DETAIL = (
    "its remote.origin.url list holds an empty value, so the url git uses "
    "depends on the git version; remove the empty value"
)

# The `detail` for an origin that does not come from the repository's own
# config file.
NOT_OWN_FILE_ORIGIN_DETAIL = (
    "no remote.origin.url comes from the repository's own config file, and a "
    "url from an included file can differ between worktrees and branches"
)

# The `detail` for a repository whose own config file sets no origin but
# holds an include section.
INCLUDE_ONLY_ORIGIN_DETAIL = (
    "the repository's own config file sets no remote.origin.url but holds an "
    "include section, and an included file can set the origin on one branch "
    "or worktree and not on another"
)

# The `detail` for a repository whose own config file could not be read to
# look for include sections.
UNCHECKED_CONFIG_DETAIL = (
    "the repository's own config file could not be read to check it for "
    "include sections"
)

# The `detail` for a decline whose `.git` points at a git directory outside
# the checkout that is neither a linked worktree's nor a submodule's admin
# dir. Its override file sits in that external directory, which a forged `.git`
# pointer can name as easily as the user's own store, so no path is printed.
EXTERNAL_GIT_DIR_DETAIL = (
    "this checkout's git directory lies outside it, so pinning it is a user "
    "decision: see the pact-memory skill"
)


# The three outcomes the classifier distinguishes. Collapsing any two is the
# likeliest way to write a wrong pin: the basename forms become declines, or
# the decline forms become basenames.
OUTCOME_CANONICAL = "canonical"
OUTCOME_NO_PORTABLE_ORIGIN = "no_portable_origin"
OUTCOME_DECLINE = "decline"

# The named causes a resolution can decline for. `decline_reason` is built
# only by `decline_record`, so a reason outside this set cannot be reported.
DECLINE_REASONS = frozenset(
    {
        "origin_unreadable",
        "origin_unrepresentable",
        "gitfile_redirect",
        "broken_git_marker",
        "no_working_tree",
        "submodule_checkout",
        "declaration_unresolved",
    }
)

ORIGIN_PRESENT = "present"
ORIGIN_ABSENT = "absent"
ORIGIN_UNAVAILABLE = "unavailable"
ORIGIN_EMPTY_ENTRY = "empty_entry"
ORIGIN_NOT_OWN_FILE = "not_own_file"
ORIGIN_INCLUDE_ONLY = "include_only"
ORIGIN_CONFIG_UNREAD = "config_unread"


def _holds_include_section(text: str) -> bool:
    """Whether git config `text` has a section header naming `include` or
    `includeIf`, read as git's own parser reads a header (`git_parse_source`
    and `get_base_var` in git's config.c):

    - a UTF-8 byte order mark at the start of the file is skipped;
    - before a `[`, only git's whitespace is skipped (`isspace` in git's
      ctype.c: space, tab, carriage return and line feed), and a header may
      follow another header on the same line;
    - the section name runs from the `[` to the first `]` or git whitespace,
      compared case-folded, a dotted name by its first part.

    - a line that continues a value (`_value_continues`: the value's last
      character is an unescaped `\\`) is part of that value, not a line of
      its own, so a `[include]` there is no header.

    A header git refuses (`[ include]`, a form feed before the `[`) makes
    git refuse the whole file, so the origin read is already unavailable
    there and the answer here does not matter. A header git reads but does
    not act on (`[include "x"]`, `[include.x]`) counts; that only declines.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    in_value = False
    in_quote = False
    for line in text.split("\n"):
        if in_value:
            in_value, in_quote = _value_continues(line, in_quote)
            continue
        rest = line
        while True:
            rest = rest.lstrip(" \t\r")
            if not rest.startswith("["):
                in_value, in_quote = _line_value_continues(rest)
                break
            name_end = 0
            for index, char in enumerate(rest):
                if index and char in " \t\r]":
                    name_end = index
                    break
            if not name_end:
                break
            if rest[1:name_end].lower().split(".")[0] in ("include", "includeif"):
                return True
            close = _header_end(rest, name_end)
            if close is None:
                break
            rest = rest[close + 1:]
    return False


def _line_value_continues(line: str) -> Tuple[bool, bool]:
    """For a line that is not a section header, whether its value continues
    on the next line, and whether it ends inside a quote. A comment line, or
    a line with no `=`, has no value to continue."""
    if line.startswith((";", "#")) or "=" not in line:
        return False, False
    return _value_continues(line.split("=", 1)[1], False)


def _value_continues(value: str, in_quote: bool) -> Tuple[bool, bool]:
    """Whether a config value's text continues past this line, read as git
    reads a value (`parse_value`): a `\\` takes the next character, so one
    at the very end joins the next line to the value; `"` toggles a quote;
    `;` or `#` outside a quote starts a comment, which never continues.
    Returns `(continues, in_quote)`."""
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            if index + 1 == len(value) or value[index + 1:] == "\r":
                return True, in_quote
            index += 2
            continue
        if char == '"':
            in_quote = not in_quote
        elif char in ";#" and not in_quote:
            return False, False
        index += 1
    return False, False


def _header_end(header: str, name_end: int) -> Optional[int]:
    """The index of the `]` that closes `header`, whose name ends at
    `name_end`, read as git reads a header (`get_extended_base_var`): after
    the name and git's whitespace, a `"`-quoted subsection in which a
    backslash takes the next character as it is, then `]` at once. None when
    git would refuse the header, which refuses the whole file."""
    if header[name_end] == "]":
        return name_end
    index = name_end
    while index < len(header) and header[index] in " \t\r":
        index += 1
    if index == len(header) or header[index] != '"':
        return None
    index += 1
    while index < len(header) and header[index] != '"':
        index += 2 if header[index] == "\\" else 1
    if index + 1 >= len(header) or header[index + 1] != "]":
        return None
    return index + 1


def _absent_origin(git_dir) -> "OriginRead":
    """The answer for a repository whose config sets no origin.

    `--includes` reads only the includes that apply now, so an origin set by
    an `includeIf "onbranch:..."` file is absent on another branch, and the
    folder name would then bind. So the repository's own config file is read
    (a file read, no git call): any include section there declines, and so
    does a config file that cannot be read. A config file that does not
    exist holds no include."""
    try:
        text = read_git_file(os.path.join(git_dir, "config"))
    except FileNotFoundError:
        # No config file: git read nothing there, so nothing is included.
        text = ""
    except (OSError, ValueError):
        text = None
    if text is None:
        return OriginRead(ORIGIN_CONFIG_UNREAD, None)
    if _holds_include_section(text):
        return OriginRead(ORIGIN_INCLUDE_ONLY, None)
    return OriginRead(ORIGIN_ABSENT, None)


# The states that decline `origin_unrepresentable`, each with its detail.
_ORIGIN_DECLINE_DETAILS = {
    ORIGIN_EMPTY_ENTRY: EMPTY_URL_ENTRY_DETAIL,
    ORIGIN_NOT_OWN_FILE: NOT_OWN_FILE_ORIGIN_DETAIL,
    ORIGIN_INCLUDE_ONLY: INCLUDE_ONLY_ORIGIN_DETAIL,
    ORIGIN_CONFIG_UNREAD: UNCHECKED_CONFIG_DETAIL,
}


def decline_record(
    reason: str, override_path: Optional[str], source: Optional[str] = None
) -> dict:
    """The `decline_reason` dict: exactly `{source, reason, override_path}`.

    `reason` must be one of `DECLINE_REASONS` (ValueError otherwise).
    `override_path` is the `<git-common-dir>/pact-project-id` file that would
    remedy the decline, or None when no override file can remedy it. It is
    None for `gitfile_redirect`, `no_working_tree`, `broken_git_marker` and
    `declaration_unresolved`. It is also None for every other cause when
    the checkout's `.git` points at a git directory outside the checkout
    (one that is neither a linked worktree's nor a submodule's admin dir).
    A `gitfile_redirect` given a path raises ValueError: the common dir git
    reports there belongs to the repository the `.git` file points at, so a
    pin written to it would re-pin that other repository. The remedy is the
    `.git` file itself. A user's own `git init --separate-git-dir=<dir>/.git`
    layout is recovered by a pin the USER writes after confirming the git
    directory is theirs (the memory skill describes it); no printed text
    names that path, because a forged pointer has the same shape. `source`
    is the rung that declined; this module leaves it None, and the resolver
    in memory_api, the only layer that knows the rung, fills it in.
    """
    if reason not in DECLINE_REASONS:
        raise ValueError(f"unknown decline reason: {reason!r}")
    if reason == "gitfile_redirect" and override_path is not None:
        raise ValueError("a gitfile_redirect decline names no override path")
    return {"source": source, "reason": reason, "override_path": override_path}


def _server_identity_impl() -> tuple:
    """The two grammar predicates owned by database.py (see module docstring).

    Imported at call time, so loading this module does not load
    database.py. The import is package-relative: this module loads only as
    `scripts.project_identity`, which is how every caller reaches it.
    database.py must not import this module.
    """
    from .database import is_canonical_id, validate_identifier_for_write

    return is_canonical_id, validate_identifier_for_write


def _git_env_bool(name: str) -> bool:
    """The git boolean environment variable `name`, read by the one shared
    reader (`shared.git_helpers.git_env_bool`), so this module's marker
    walk and `shared.project_scope`'s agree: unset is false, a value git
    refuses is true (fail closed), and Python truthiness is never used."""
    from shared.git_helpers import git_env_bool

    return git_env_bool(os.environ.get(name))


def _git_ceiling_directories() -> set:
    """`GIT_CEILING_DIRECTORIES` parsed exactly as git parses it, by the one
    parser (`shared.git_helpers.git_ceiling_directories`): str paths."""
    from shared.git_helpers import git_ceiling_directories

    return git_ceiling_directories(os.environ.get("GIT_CEILING_DIRECTORIES"))


def _is_git_directory(path: Path) -> bool:
    """Whether git would accept `path` as a git directory (git's own
    `is_git_directory`): a `HEAD` entry, and `objects/` and `refs/` in
    it, or in the directory its `commondir` file names. A `commondir`
    that is not a regular file of at most GIT_FILE_LIMIT bytes (a FIFO, a
    huge file) refuses, read by the bounded `read_git_file` so it never
    blocks. Never raises."""
    try:
        if not os.path.lexists(path / "HEAD"):
            return False
        common = path
        commondir = path / "commondir"
        if os.path.lexists(commondir):
            text = read_git_file(commondir)
            if text is None:
                return False
            # git drops only trailing CRs and LFs (`get_common_dir_noenv`):
            # leading and other trailing whitespace stays in the path.
            named = text.rstrip("\r\n")
            common = Path(named) if os.path.isabs(named) else path / named
        return (common / "objects").is_dir() and (common / "refs").is_dir()
    except (OSError, ValueError):
        return False


def find_git_marker(start) -> Optional[Path]:
    """The `.git` marker (file or directory) git's discovery would reach, or None.

    Walks up from `start` with git's own boundaries:

    - the START directory is always examined, even when it is itself a
      `GIT_CEILING_DIRECTORIES` entry;
    - a PARENT equal to a ceiling entry stops the walk before it is examined
      (entries parsed as `_git_ceiling_directories` does);
    - the walk stops when the device changes, unless
      `GIT_DISCOVERY_ACROSS_FILESYSTEM` is true (`_git_env_bool`);
    - it stops at the filesystem root.

    An `lstat` of the candidate, so a broken marker (a dangling symlink, a
    `.git` file naming a missing directory) still counts. A `.git` FOLDER
    counts only when git would accept it as a git directory
    (`_is_git_directory`) or nothing above it is a marker: git walks past
    any other to a marker above, and so does this. Only a missing
    entry (`FileNotFoundError`, `NotADirectoryError`) lets the walk go on:
    a `.git` whose `lstat` fails for any other reason (a folder that cannot
    be searched, a symlink loop) is returned as the marker, so the caller
    asks git about it and refuses when git cannot answer, instead of reading
    the folder as having no repository. Never raises: an unreadable start or
    environment answers None.
    """
    if start is None:
        return None
    try:
        level = Path(start).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    ceilings = _git_ceiling_directories()
    cross_filesystem = _git_env_bool("GIT_DISCOVERY_ACROSS_FILESYSTEM")
    try:
        device = os.stat(level).st_dev
    except OSError:
        return None
    # GIT SKIPS A `.git` FOLDER THAT IS NOT A GIT DIRECTORY (an empty
    # `sub/.git`) and goes on up, so the walk does too: a `.git` above it is
    # the marker git uses, and stopping at the folder would let a `.git` file
    # above redirect git while this walk saw no redirect. With nothing above,
    # the first such folder is still returned, so a broken marker declines.
    skipped: Optional[Path] = None
    while True:
        marker = level / ".git"
        try:
            mode = os.lstat(marker).st_mode
            if not stat.S_ISDIR(mode) or _is_git_directory(marker):
                return marker
            skipped = skipped or marker
        except (FileNotFoundError, NotADirectoryError):
            pass
        except OSError:
            return marker
        except ValueError:
            return skipped
        parent = level.parent
        if parent == level or str(parent) in ceilings:
            return skipped
        if not cross_filesystem:
            try:
                if os.stat(parent).st_dev != device:
                    return skipped
            except OSError:
                return skipped
        level = parent


def _max_id_length() -> int:
    """The identifier length cap, from database.py (ONE definition).

    Imported at call time and package-relative, as `_server_identity_impl`
    is: loading this module does not load database.py.
    """
    from .database import CANONICAL_ID_MAX_LENGTH

    return CANONICAL_ID_MAX_LENGTH


def is_canonical(value: object) -> bool:
    """Whether `value` is a canonical repository id.

    Delegates to `database.is_canonical_id` so there is one implementation of
    the grammar. A legacy basename is NOT canonical — no POSIX basename can
    contain the mandatory `/segment`, which is what makes the two populations
    structurally disjoint.
    """
    is_canonical_id, _ = _server_identity_impl()
    return is_canonical_id(value)


def _valid_host(host: str) -> bool:
    """Whether a lowercased host is well formed.

    Alphanumeric at both ends, and only `[a-z0-9._-]` between, with no empty
    label (`a..b`): a trailing dot and a leading hyphen name something git
    would not contact the same way. An `_` inside the host is accepted on
    every transport: ssh resolves an ssh_config alias (`Host gh_work`), and
    git over http(s) and git:// hands the name to the resolver (measured, git
    2.43: `http://gitea_server.invalid:3000/o/r.git` fails with "Could not
    resolve host", not a url error), so a docker-compose service name such as
    `gitea_server` is a host.
    """
    if not host or not host[0].isalnum() or not host[-1].isalnum() or ".." in host:
        return False
    return all(char in _HOST_CHARS or char == "_" for char in host)


def _ipv6_host(host: str) -> Optional[str]:
    """A bracketed IPv6 literal (`[2001:db8::1]`) in its one compressed,
    bracketed spelling, or None. A zone index (`%eth0`) is machine-local and
    declines, as do an IPv4-mapped literal, an IPv4-compatible loopback
    literal and anything `ipaddress` refuses."""
    import ipaddress

    if not (host.startswith("[") and host.endswith("]")) or "%" in host:
        return None
    try:
        address = ipaddress.IPv6Address(host[1:-1])
    except ValueError:
        return None
    # AN IPv4-MAPPED LITERAL (`[::ffff:192.0.2.1]`) DECLINES: it names an IPv4
    # host that has a second, unbracketed spelling, so one server would get
    # two ids.
    if address.ipv4_mapped is not None:
        return None
    # AN IPv4-COMPATIBLE LOOPBACK LITERAL (`[::127.0.0.1]`, the deprecated
    # `::a.b.c.d` form) reaches this machine as `127.0.0.1` does, so it
    # declines with the loopback literals below.
    if int(address) >> 32 == 0 and ipaddress.IPv4Address(int(address)).is_loopback:
        return None
    # A LOOPBACK OR UNSPECIFIED LITERAL (`[::1]`, `[::]`) reaches this
    # machine, as `localhost` does (`_reserved_host`), so it declines.
    if address.is_loopback or address.is_unspecified:
        return None
    return "[" + address.compressed + "]"


def _url_host(host: str) -> Optional[str]:
    """The host as it goes into an id: a bracketed IPv6 literal compressed,
    a name lower-cased and checked; None for anything else (including a host
    in the reserved `.invalid` namespace and a loopback host,
    `_reserved_host`)."""
    if host.startswith("["):
        return _ipv6_host(host)
    host = host.lower()
    if not _valid_host(host) or _reserved_host(host):
        return None
    return host


def _reserved_host(host: str) -> bool:
    """Whether a lowercased host is in the `.invalid` namespace (RFC 2606)
    or names this machine (`localhost`, a `.localhost` name, RFC 6761, an
    IPv4 loopback address in any spelling `inet_aton` reads, such as
    `127.0.0.1`, `127.1` or `0x7f.1`, or the unspecified address
    `0.0.0.0`, also `0` or `0x0`: measured on Linux, a connection to it
    reaches this machine, while one to `0.0.0.1` does not).

    No remote is portably there: a loopback remote is a local server or a
    tunnel, so two unrelated repositories reached that way would share an id,
    and a hand-written pin may use `.invalid` for an id no remote can derive
    (`local.invalid/...`). A remote naming one declines; a pin is the remedy."""
    if host in ("invalid", "localhost") or host.endswith((".invalid", ".localhost")):
        return True
    import socket

    try:
        packed = socket.inet_aton(host)
    except (OSError, ValueError):
        return False
    return packed[0] == 127 or packed == bytes(4)


def _valid_port(port: str) -> bool:
    """Decimal 1..65535, no leading zeros.

    `022` is rejected rather than normalized: the grammar has one spelling per
    port, and accepting both would put two ids on one server.

    THE LENGTH IS BOUNDED BEFORE `int()`. CPython refuses int() on a decimal
    string longer than `sys.get_int_max_str_digits()` (4300 by default) with
    a ValueError, and this predicate is documented as pure and total on a
    port parsed from a remote. A 4301-digit port must DECLINE rather than
    escape into `PACTMemory.__init__`, where it turns every memory operation
    in that repository into SYSTEM_ERROR. No port has more than five digits,
    so the bound is exact rather than a heuristic.

    ONLY ASCII DIGITS: `str.isdigit` also accepts `²` and other non-decimal
    digits, which `int()` then refuses with a ValueError.
    """
    if len(port) > 5 or not (port.isascii() and port.isdigit()) or str(int(port)) != port:
        return False
    return 1 <= int(port) <= 65535


def _normalize_segment(raw: str) -> Optional[str]:
    """One path segment in RFC 3986 normal form, or None to decline.

    - `%XX` has its hex upper-cased, and is decoded only when it names an
      unreserved character: `%41` becomes `A`, `%2F` stays `%2F`. The path
      was split on `/` before this runs, so an encoded `/` never becomes a
      separator.
    - A `%` not followed by two hex digits declines.
    - A raw unreserved or sub-delimiter character is kept.
    - A raw non-ASCII character is encoded as its UTF-8 bytes, `%XX` each,
      with NO Unicode normalisation: NFC and NFD spellings stay two ids, a
      split `project-id rekey` can undo, where merging them could not be.
    - Anything else (whitespace, `@`, `?`, `#`, a control character)
      declines.

    The finished segment must be non-empty and not `.` or `..`, so `%2E%2E`
    is still refused as traversal. Ids therefore stay printable ASCII.
    """
    from .database import canonical_escape

    out = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "%":
            hex_pair = raw[index + 1:index + 3]
            if len(hex_pair) != 2 or not all(h in _HEX_DIGITS for h in hex_pair):
                return None
            out.append(canonical_escape(hex_pair))
            index += 3
            continue
        if char in _SEGMENT_RAW_CHARS:
            out.append(char)
        elif ord(char) > 0x7F:
            try:
                encoded = char.encode("utf-8")
            except UnicodeEncodeError:
                # A lone surrogate: an undecodable byte from `os.fsdecode`.
                return None
            out.append("".join(f"%{byte:02X}" for byte in encoded))
        else:
            return None
        index += 1
    segment = "".join(out)
    if segment in ("", ".", ".."):
        return None
    return segment


def _ssh_path_as_git_sends_it(path: str) -> Optional[str]:
    """An ssh url's path decoded as git decodes it, then put back in the
    form `_normalize_segment` reads: every character a segment may not hold
    raw is escaped again (`%20` stays `%20`, a decoded `%` is `%25`).
    None for a path holding a lone surrogate (an undecodable byte from
    `os.fsdecode`, or any other), which `_normalize_segment` declines in
    the scp and https spellings of the same path."""
    if any("\ud800" <= char <= "\udfff" for char in path):
        return None
    return quote(
        unquote(path, errors="surrogateescape"),
        safe="/" + "".join(sorted(_SEGMENT_RAW_CHARS)),
        errors="surrogateescape",
    )


def _strip_path_suffixes(path: str) -> str:
    """Drop one trailing `/`, then one `.git`, then one exposed `/`.

    The `.git` is read in the last segment's normal form
    (`_normalize_segment`), so a percent-encoded spelling of it (`%2Egit`)
    is dropped too; the segment then stays in that normal form, which
    `_split_path` leaves as it is. EXCEPT ON github.com over https and
    other non-ssh schemes: GitHub does not drop an escaped suffix
    (`https://github.com/git/git%2Egit` is not `git/git`), so such a
    remote declines before this strip matters (`_github_does_not_serve`). Only a lower-case `.git` is dropped, as
    git's own lookup appends exactly `.git`: on a server `repo.GIT` is
    another directory. github.com too drops only a lower-case `.git`
    (measured: `repo.GIT` and `repo.Git` reach nothing there, as
    `repo.git.git` does), and a github.com name still ending in `.git` then
    declines (`database.github_repo_path`).

    The second slash strip exists only for the `repo/.git` form (a
    non-bare repository's git dir, which a plain sshd serves): once `.git`
    is removed the path ends in the separator that used to precede it, and
    leaving it would create an empty final segment. In that form a
    `repo.git` before it loses its `.git` too, since git reaches
    `repo.git/.git` from `repo`. It does NOT rescue
    `owner/repo//`: that form has no `.git`, so the single strip runs out and
    the empty segment reaches validation.
    """
    if path.endswith("/"):
        path = path[:-1]
    head, separator, last = path.rpartition("/")
    normal = _normalize_segment(last)
    if normal is not None and normal.endswith(".git"):
        last = normal[:-4]
        if last:
            return head + separator + last
        # `repo.git/.git`: the git dir of a checkout at `repo.git`, which
        # git reaches from `repo` too (it tries `repo.git/.git`), so it
        # loses that `.git` as well (measured, git 2.43).
        parent_head, parent_separator, parent = head.rpartition("/")
        parent_normal = _normalize_segment(parent)
        if parent_normal is not None and parent_normal.endswith(".git") and parent_normal[:-4]:
            return parent_head + parent_separator + parent_normal[:-4]
        return head
    return path


def _split_path(path: str) -> Optional[list]:
    """The path's segments in normal form (`_normalize_segment`), or None.

    Split FIRST, then normalise each segment: normalising first would let
    an encoded `/` manufacture a separator.
    """
    segments = []
    for raw_segment in path.split("/"):
        segment = _normalize_segment(raw_segment)
        if segment is None:
            return None
        segments.append(segment)
    return segments


# Azure DevOps Services spells one repository five ways (two ssh hosts, the
# `dev.azure.com` url and two legacy `{org}.visualstudio.com` urls). Each is
# mapped to `dev.azure.com/{org}/{project}/_git/{repo}`. The rule is bounded
# to these hosts and path shapes; Azure DevOps Server, on a customer's own
# host, keeps the generic id.
_AZURE_SSH_HOSTS = frozenset({"ssh.dev.azure.com", "vs-ssh.visualstudio.com"})
_AZURE_LEGACY_SUFFIX = ".visualstudio.com"


def _azure_devops_services_id(host: str, port: str, segments: list) -> Optional[list]:
    """`[org, project, "_git", repo]` for an Azure DevOps Services spelling,
    or None to keep the generic id.

    Recognised, each with no explicit non-default port:
    - `ssh.dev.azure.com` or `vs-ssh.visualstudio.com`, path
      `v3/{org}/{project}/{repo}`;
    - `{org}.visualstudio.com`, path `{project}/_git/{repo}` or
      `DefaultCollection/{project}/_git/{repo}` (the collection in any case);
    - `dev.azure.com`, path `{org}/{project}/_git/{repo}`.

    The ORGANISATION is lower-cased in every spelling: Azure DevOps
    organisation names are case-insensitive, and the legacy spelling carries
    it in the (already lower-cased) host. Project and repository segments
    keep their case, as every generic path segment does.
    """
    if port:
        return None
    if host in _AZURE_SSH_HOSTS:
        if len(segments) == 4 and segments[0] == "v3":
            org, project, repo = segments[1:]
            return [org.lower(), project, "_git", repo]
        return None
    if host == "dev.azure.com":
        if len(segments) == 4 and segments[2] == "_git":
            org, project, _, repo = segments
            return [org.lower(), project, "_git", repo]
        return None
    if host.endswith(_AZURE_LEGACY_SUFFIX):
        org = host[: -len(_AZURE_LEGACY_SUFFIX)]
        if not org or "." in org:
            return None
        rest = segments[1:] if segments[:1] and segments[0].lower() == "defaultcollection" else segments
        if len(rest) == 3 and rest[1] == "_git":
            return [org, rest[0], "_git", rest[2]]
    return None


# Hosts whose owner and repository names are case-insensitive, so one
# repository has one id whatever case a remote spells it in: the path is
# lower-cased. Every other host keeps its path's case.
_CASE_INSENSITIVE_PATH_HOSTS = frozenset({"github.com"})
# GitHub's own SSH-over-443 host (`ssh://git@ssh.github.com:443/O/N`): the
# same service as github.com, so it takes github.com's id.
_GITHUB_SSH_443_HOST = "ssh.github.com"


def _identifier(
    host: str, port: str, segments: list, ssh: bool = False
) -> str:
    """`host[:port]/<segments>`, after the bounded Azure DevOps Services rule
    and the lower-cased path of a `_CASE_INSENSITIVE_PATH_HOSTS` host. No
    `.git` is removed here: `_strip_path_suffixes` drops the one literal
    `.git`, so a `.GIT` spelling stays part of the name on every host.
    `ssh` says the remote is an ssh or scp one, the only transport
    `ssh.github.com` folds for."""
    if ssh and host == _GITHUB_SSH_443_HOST and port in ("", "443"):
        # GitHub's SSH-over-443 endpoint is github.com itself, for ssh only.
        host, port = "github.com", ""
    from .database import github_folded_host, github_folded_segment

    # GitHub redirects www.github.com to github.com: one repository. The
    # rule is database's one copy, which a pin takes too.
    host = github_folded_host(host, port)
    azure = _azure_devops_services_id(host, port, segments)
    if azure is not None:
        host, segments = "dev.azure.com", azure
    if host in _CASE_INSENSITIVE_PATH_HOSTS:
        # Lower-cased OUTSIDE its percent escapes (`github_folded_segment`).
        # A `.GIT` spelling is kept, lower-cased: GitHub does not serve
        # `repo.GIT` as `repo` (measured), so it must not bind repo's id; a
        # port-less github.com one then declines (`github_repo_path`).
        segments = [github_folded_segment(segment) for segment in segments]
    return f"{host}:{port}/{'/'.join(segments)}" if port else f"{host}/{'/'.join(segments)}"


def _strip_one_terminator(url: str) -> str:
    """Drop exactly one trailing newline, as git writes it.

    A broad `.strip()` is deliberately NOT used: it would widen the grammar
    for every future form, and a remote with a trailing space must DECLINE
    visibly rather than be silently repaired.
    """
    if url.endswith("\r\n"):
        return url[:-2]
    if url.endswith("\n"):
        return url[:-1]
    return url


def _scp_match(value: str) -> Optional["re.Match[str]"]:
    """The scp-form match for `value`, or None when git would not read it so.

    On Windows only, a user-less single ASCII letter before the `:` is a
    drive path (`C:/repos/x`, `c:foo`), exactly as git's
    `has_dos_drive_prefix`, which is non-zero only there. On every other
    platform git connects to a one-letter host (typically an ssh_config
    alias), so it is a network remote here too.
    """
    match = _SCP_FORM_RE.match(value)
    if match is None:
        return None
    host = match.group("host")
    if (
        os.name == "nt"
        and match.group("user") is None
        and len(host) == 1
        and host.isascii()
        and host.isalpha()
    ):
        return None
    return match


def _git_reads_as_scp(value: str) -> bool:
    """Whether git would read `value` as scp syntax, by its rule alone.

    `:` present and no `/` before the first `:`, except the Windows drive
    path `_scp_match` also excludes. Unlike `_scp_match` this never parses a
    host or user, so it still answers for a value our stricter grammar
    cannot split, such as `git@a@b:owner/repo`.
    """
    prefix, colon, _ = value.partition(":")
    if not colon or "/" in prefix:
        return False
    return not (
        os.name == "nt" and len(prefix) == 1 and prefix.isascii() and prefix.isalpha()
    )


def _has_control_character(value: str) -> bool:
    """Whether `value` holds an ASCII control character (below 0x20) or DEL."""
    return any(char < "\x20" or char == "\x7f" for char in value)


# The schemes whose path the remote SHELL resolves, so a leading `~/` names
# the login's home exactly as an scp-relative path does.
_HOME_RELATIVE_SCHEMES = frozenset({"ssh", "git+ssh", "ssh+git"})


def _name_the_home(segments: list, login: Optional[str]) -> list:
    """`segments` with a leading `~` (the login's home, alone or with more
    path after it) spelled as `~<login>`.

    git sends `~/srv/repo` for `ssh://alice@host/~/srv/repo` and
    `alice@host:~/srv/repo`, which a plain sshd resolves in alice's home,
    the directory `~alice/srv/repo` names from any login. So the id is
    `host/~alice/srv/repo` and two logins' homes stay two ids. With no login
    in the URL the home is unknown, and `~` stays as written; so does it
    when the login holds a character a segment may not hold raw (`%`, a
    space, `/`, non-ASCII) or is `.` or `..`, rather than the remote
    declining. `~user/...` is kept as is (another user's home, and
    SourceHut's real path). A path with no `~` is not touched: on a forge
    it is virtual, so `git@host:o/r` keeps the https spelling's id.
    """
    if not segments or segments[0] != "~" or not login:
        return segments
    if login in (".", "..") or not set(login) <= _SEGMENT_RAW_CHARS:
        return segments
    return ["~" + login, *segments[1:]]


def _home_segments(sent: str, segments: list, login: Optional[str]) -> list:
    """`segments` with the login's home named (`_name_the_home`) when the
    path git sends, before its `.git` is dropped, is `~` or starts with
    `~/`. `~.git` is the home of a user called `.git`, not the login's (a
    `~<user>` path, kept as written), so it keeps its `.git` rather than
    becoming the lone `~` the strip leaves.
    """
    if sent == "~" or sent.startswith("~/"):
        return _name_the_home(segments, login)
    if segments == ["~"]:
        return _split_path(sent.rstrip("/")) or segments
    return segments


def normalize_remote_url(url: object) -> Optional[str]:
    """Return the canonical `host[:port]/<path>` id for a remote URL.

    Pure and total: any object is accepted and answers None rather than
    raising. Returns None for EVERY non-canonical form; it is deliberately
    NOT the classification surface — `classify_remote_origin` is, because this
    function cannot tell "no portable origin" from "a network remote that
    cannot be represented". Never logs its input or output: a remote routinely
    carries a credential in its userinfo.

    The parse order below IS the contract, because `:` means different things
    in the two forms: a port separator in a URL authority, and a path
    separator in scp syntax.
    """
    if not isinstance(url, str):
        return None

    # A TRAILING NEWLINE IS PART OF THE URL, NOT A TERMINATOR: every caller
    # passes the stored value (`read_origin` reads with `-z`), and git keeps
    # the newline too (measured, git 2.43: `url = "<path>\n"` makes
    # `git ls-remote origin` fail with "'<path>\n' does not appear to be a
    # git repository"). So such a value names no repository git reaches and
    # declines (`_is_network_shaped` still reads it as a network remote).
    value = url
    if _has_control_character(value):
        return None

    # Refuse the whole value before any parsing. Python's `urlsplit` and git
    # disagree on where the host ends in `https://evil.com\\@github.com/o/n`,
    # so neither reading is trusted. This is the rule `backlog.py` already
    # ships; a second parser with a laxer rule would be the defect. The
    # cross-parser corpus test pins that both refuse the same inputs.
    # A leading BOM is not part of a URL. Reject it HERE rather than letting
    # the scp form absorb it into the userinfo: `_is_network_shaped` classifies
    # a BOM-prefixed value as a present network remote that cannot be
    # represented, so accepting it in one spelling and refusing it in the other
    # would make that rule false. One rule, both spellings.
    if value.startswith("\ufeff"):
        return None

    if "\\" in value or value.count("@") > 1:
        return None

    scheme_match = _SCHEME_PREFIX_RE.match(value)
    if scheme_match is not None:
        scheme = scheme_match.group("scheme").lower()
        if scheme not in _ACCEPTED_SCHEMES:
            return None
        rest = value[scheme_match.end():]
        # Split the authority from the path BEFORE looking for userinfo: an
        # `@` in the path must not be mistaken for one in the authority.
        authority, separator, path = rest.partition("/")
        if not separator or not path:
            return None
        login = None
        if "@" in authority:
            userinfo, authority = authority.rsplit("@", 1)
            login = userinfo.split(":", 1)[0] or None
            if login and scheme in _HOME_RELATIVE_SCHEMES:
                # git decodes an ssh url's user as it does the path (measured:
                # `ssh://al%69ce@box/~/proj` logs in as alice).
                login = unquote(login, errors="surrogateescape")
        if authority.startswith("["):
            # An IPv6 literal: its colons are inside the brackets, and a port
            # can only follow the closing bracket.
            close = authority.find("]")
            if close < 0:
                return None
            host, rest = authority[:close + 1], authority[close + 1:]
            if rest and not rest.startswith(":"):
                return None
            port = rest[1:]
            if port and not _valid_port(port):
                return None
        elif ":" in authority:
            # An EMPTY port (`host:`) is git's default port: git passes no
            # `-p` for `ssh://h:/o/r`, so it is the same server as `h/o/r`.
            host, _, port = authority.rpartition(":")
            if port and not _valid_port(port):
                return None
        else:
            host, port = authority, ""
        if port and _DEFAULT_PORTS.get(scheme) == int(port):
            port = ""
        ssh = scheme in _HOME_RELATIVE_SCHEMES
        host = _url_host(host)
        if host is None:
            return None
        # git percent-decodes an ssh url's WHOLE path before sending it
        # (measured: `ssh://h/srv/a%2Bb` sends `/srv/a+b`, `%2F` sends a
        # separator and `%25` a literal `%`), while the scp form sends its
        # path as written. So an ssh path is decoded here too, then every
        # character a segment may not hold raw is escaped again, for the
        # segment rule: `ssh://h/a%2Bb` and `h:a+b` share one id, `%20` stays
        # `%20`, and a decoded `%` becomes `%25`. Other schemes keep their
        # escapes.
        sent_path = (
            _ssh_path_as_git_sends_it(path) if scheme in _HOME_RELATIVE_SCHEMES else path
        )
        if sent_path is None:
            return None
        github_unserved = _github_does_not_serve(sent_path)
        path = _strip_path_suffixes(sent_path)
        segments = _split_path(path)
        if not segments:
            return None
        if scheme in _HOME_RELATIVE_SCHEMES:
            segments = _home_segments(sent_path, segments, login)
        identifier = _identifier(host, port, segments, ssh)
    else:
        scp_match = _scp_match(value)
        if scp_match is None:
            # Neither a URL nor scp syntax: indistinguishable from a relative
            # local path, which is NO PORTABLE ORIGIN.
            return None
        host = _url_host(scp_match.group("host"))
        if host is None:
            return None
        # RELATIVE AND ABSOLUTE SCP PATHS ARE DELIBERATELY ONE ID.
        # `git@host:srv/repo`, `git@host:/srv/repo` and `ssh://git@host/srv/repo`
        # all become `host/srv/repo`. On a forge the path is virtual and the
        # leading slash does not matter: `git@github.com:o/r` sends `o/r` and
        # `ssh://git@github.com/o/r` sends `/o/r` to one repository, so
        # telling them apart would split every forge's scp and https pair.
        # The cost is a plain sshd host, where the relative form is resolved
        # under the login's home: the two directories that `git@host:srv/repo`
        # (in the login's home) and `git@host:/srv/repo` reach can be distinct
        # repositories, and if both are used against one store they share an id. That is
        # the class the userinfo strip already accepts (`alice@host:repo` and
        # `bob@host:repo` share one), and nothing in the URL says which kind
        # of server it is.
        #
        # AN EXPLICIT HOME NAMES ITS LOGIN: `alice@host:~/srv/repo` and
        # `ssh://alice@host/~/srv/repo` send `~/srv/repo`, alice's home, so
        # their id is `host/~alice/srv/repo`, the id `~alice/srv/repo` gives
        # from any login (`_name_the_home`). Only the `~` spelling says the
        # path is home-relative; a forge never uses it (GitHub declines it).
        # The `~` is tested on the RAW scp path, because git sends an scp
        # path literally: `%7E/srv/repo` is a directory named `%7E`, not the
        # home.
        #
        # Exactly ONE leading slash is removed, mirroring the URL branch's
        # `partition("/")`: a doubled separator still declines in BOTH
        # spellings (`git@host://a/b` and `https://host//a/b`), which stops a
        # typo from silently naming a different path.
        scp_path = scp_match.group("path")
        if scp_path.startswith("/"):
            scp_path = scp_path[1:]
        if host not in _AZURE_SSH_HOSTS:
            # git sends an scp path LITERALLY (measured: `srv/%41bc` sends
            # `srv/%41bc`), so a `%` there is an ordinary character and is
            # kept as `%25`. Decoding it would merge `srv/%41bc` with
            # `srv/Abc`, two directories. Azure DevOps Services is the one
            # exception: its server decodes the scp path, so its `%20` names
            # the same project as the https spelling's `%20`.
            scp_path = scp_path.replace("%", "%25")
        github_unserved = _github_does_not_serve(scp_path)
        path = _strip_path_suffixes(scp_path)
        segments = _split_path(path)
        if not segments:
            return None
        user = scp_match.group("user")
        segments = _home_segments(scp_path, segments, user[:-1] if user else None)
        # ONE SEGMENT IS ENOUGH. `git@host:repo` and `https://host/repo` name
        # the same endpoint, and the grammar allows a single segment;
        # declining the scp spelling while accepting the URL spelling would
        # split one repository into two ids.
        identifier = _identifier(host, "", segments, True)

    if len(identifier) > _max_id_length():
        return None
    # A GITHUB ID IS EXACTLY owner/repo, AND ONLY A PATH GITHUB SERVES NAMES
    # ONE: `https://github.com/owner/.git` (or any path of another depth)
    # names no repository there, and neither does a home-relative `~/o/r` or a
    # `o/r/.git` path (`_github_does_not_serve`). Each declines rather than
    # bind an id the remote does not reach.
    from .database import github_repo_path

    if identifier.startswith("github.com/"):
        # Exactly owner/repo, each name decoded as GitHub reads it: an escaped
        # `/` (`o/r%2Fx`), a `~` or any other character names nothing
        # GitHub serves. A pin takes the same step.
        repo_path = None if github_unserved else github_repo_path(identifier[len("github.com/"):])
        if repo_path is None:
            return None
        identifier = "github.com/" + repo_path

    # The grammar's single authority. If `database.is_canonical_id` is ever
    # stricter than the construction above, the answer is a DECLINE, never a
    # silently different id.
    if not is_canonical(identifier):
        return None
    return identifier


def is_github_name(name: object) -> bool:
    """`database.is_github_name`, the one GitHub repository name rule, for
    callers that load this module (the backlog's `_valid_slug`)."""
    from .database import is_github_name as rule

    return rule(name)


def is_github_owner(name: object) -> bool:
    """`database.is_github_owner`, the one GitHub owner name rule, for
    callers that load this module (the backlog's `_valid_slug`)."""
    from .database import is_github_owner as rule

    return rule(name)


def _github_does_not_serve(path: str) -> bool:
    """Whether a remote path, as git sends it, is a form github.com does not
    serve: home-relative (`~/o/r`, `~user/...`, which git sends as written)
    or a non-bare repository's `o/r/.git`. Measured: GitHub answers 'not
    found' for `https://github.com/O/R/.git` and refuses a `~` path over
    https, while `O/R` and `O/R.git/` are served. A plain sshd serves both
    forms, so only a github.com id declines them. GitHub drops only a
    literal `.git` suffix: `https://github.com/git/git%2Egit` is not
    `git/git` (measured: not found), so an escaped suffix declines too."""
    # Read as the server reads it: escapes decoded (`%2Egit`, `%7E`) and
    # `.git` in any case.
    trimmed = unquote(path, errors="surrogateescape").lstrip("/").rstrip("/").lower()
    escaped_suffix = trimmed.endswith(".git") and not path.rstrip("/").lower().endswith(".git")
    return trimmed.startswith("~") or trimmed.endswith("/.git") or escaped_suffix


def _is_network_shaped(url: object) -> bool:
    """Whether a present value names a NETWORK remote we are obliged to parse.

    Distinguishes the two None-returning outcomes of `normalize_remote_url`:
    a network-shaped value that cannot be a canonical id is a DECLINE; a
    local-path form is a legitimate basename fallback.

    `file://` and bare local paths are NOT network-shaped. Every other
    `scheme://` IS - including schemes outside the accepted set, because a
    present network remote we cannot represent must not be silently
    downgraded to a folder name, so `ftp://` declines too.

    A scheme-less value whose PRE-SLASH prefix holds BOTH `@` and `:` is
    ALSO network-shaped. git's own scp heuristic reads the first `:` as the
    host/path separator, so `deploy-token:<token>@host/owner/repo` names a
    network host to git. Downgrading that to a folder basename is the silent
    mis-scope this change exists to remove, so it DECLINES and names the
    override remedy instead. Only a Windows drive path (see `_scp_match`)
    and a value with a `/` before its first `:` are local.

    A value that still holds a control character once its one terminator is
    dropped is network-shaped too, whatever else it looks like: no
    repository uses such a path, and `git@a:o/a\\ngit@b:x/y` must not reach
    the basename because the scp form cannot match across the newline.
    """
    if not isinstance(url, str):
        return False
    value = _strip_one_terminator(url)
    if _has_control_character(value):
        return True
    # A leading BOM or leading WHITESPACE is a present network remote that
    # cannot be represented. Both are stripped for CLASSIFICATION only: the
    # canonicaliser refuses the value outright, so this decides
    # decline-versus-basename, never acceptance.
    value = value.lstrip("\ufeff").lstrip()
    if "\\" in value or value.count("@") > 1:
        # git's rule, not our grammar: the grammar refuses this value
        # outright, so asking it whether the value is scp would always say
        # no and send a network remote such as `a@b@github.com:o/n` to the
        # basename.
        return "://" in value or _git_reads_as_scp(value)
    scheme_match = _SCHEME_PREFIX_RE.match(value)
    if scheme_match is not None:
        return scheme_match.group("scheme").lower() not in _LOCAL_SCHEMES
    if _scp_match(value) is not None:
        return True
    first_slash = value.find("/")
    prefix = value if first_slash == -1 else value[:first_slash]
    return "@" in prefix and ":" in prefix


# REMOTE-HELPER TRANSPORTS. git hands `<helper>::<address>` to
# git-remote-<helper>, and these schemes to their own helpers too. None of them
# has a portable id, so such an origin is NO PORTABLE ORIGIN: the folder name,
# disclosed (`REMOTE_HELPER_DETAIL`). Every other unknown scheme still
# declines. The helper name holds no `/`, `@`, `[` or `:`, so an IPv6
# literal's `::` never reads as one.
_REMOTE_HELPER_FORM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")
_REMOTE_HELPER_SCHEMES = frozenset({"codecommit", "persistent-https", "sso", "keybase"})

# The `detail` for a folder-name id bound because the origin is a
# remote-helper transport (`_is_remote_helper`).
REMOTE_HELPER_DETAIL = (
    "its origin is a remote-helper transport, which has no portable id, so it "
    "uses its folder-name id (its folder's name, or its git dir's name for a "
    "--separate-git-dir repository): a bucket shared with any folder of that "
    "name. A canonical id written to <git-common-dir>/pact-project-id gives it "
    "its own"
)


def _is_remote_helper(url: str) -> bool:
    """Whether an origin value names a remote-helper transport: the
    `<helper>::<address>` form, or a scheme in `_REMOTE_HELPER_SCHEMES`. A
    value with a control character, a leading BOM or leading whitespace is
    not one (it declines as before)."""
    value = _strip_one_terminator(url)
    if _has_control_character(value) or value[:1] in ("\ufeff", " ", "\t"):
        return False
    if _REMOTE_HELPER_FORM_RE.match(value):
        return True
    scheme = _SCHEME_PREFIX_RE.match(value)
    return scheme is not None and scheme.group("scheme").lower() in _REMOTE_HELPER_SCHEMES


def unrepresentable_detail(url: Optional[str]) -> Optional[str]:
    """Why a network origin declined (`classify_remote_origin`), in words that
    name the rule it broke and never echo the url (a url can carry a token),
    or None when no more specific reason is found. For the
    `origin_unrepresentable` cause; diagnostic only, never a verdict."""
    if not isinstance(url, str):
        return None
    value = _strip_one_terminator(url)
    if _has_control_character(value):
        return "the origin url holds a control character"
    if value[:1] in ("\ufeff", " ", "\t"):
        return "the origin url starts with a byte-order mark or whitespace"
    if "\\" in value or value.count("@") > 1:
        return "the origin url holds a backslash or more than one '@'"
    scheme_match = _SCHEME_PREFIX_RE.match(value)
    if scheme_match is not None:
        if scheme_match.group("scheme").lower() not in _ACCEPTED_SCHEMES:
            return (
                "its scheme is not one an id is read from (https, http, ssh, git, "
                "git+ssh, ssh+git)"
            )
        authority = value[scheme_match.end():].partition("/")[0]
    else:
        scp_match = _scp_match(value)
        if scp_match is None:
            return None
        authority = scp_match.group("host")
    if "?" in value or "#" in value:
        return "the origin url carries a query or fragment ('?' or '#'), which names no repository"
    authority = authority.rpartition("@")[2]
    if authority.startswith("["):
        host, _, rest = authority.partition("]")
        host, port = host + "]", rest[1:] if rest.startswith(":") else ""
    elif scheme_match is not None:
        host, _, port = authority.partition(":")
    else:
        host, port = authority, ""
    if port and not _valid_port(port):
        return "its port is not a valid port number (1-65535, no leading zero)"
    host = host.lower()
    if host.startswith("["):
        if _ipv6_host(host) is None:
            return (
                "its host is an IPv6 literal that names no portable server (this "
                "machine, an IPv4-mapped address or a zone index)"
            )
        return None
    if _reserved_host(host):
        if host == "invalid" or host.endswith(".invalid"):
            return "its host is in the reserved .invalid namespace"
        return "its host is this machine (a loopback or unspecified address), so it names no portable repository"
    if not _valid_host(host):
        return "its host is not a valid host name"
    if host in ("github.com", "www.github.com", _GITHUB_SSH_443_HOST):
        return (
            "GitHub serves no repository at its path: a github.com id is exactly "
            "owner/repo of GitHub names, with no home-relative or /.git path and "
            "no repository name ending in .git"
        )
    return "its path is empty or holds a segment an id cannot hold"


def classify_remote_origin(url: Optional[str]) -> "tuple[str, Optional[str]]":
    """Classify an origin value into one of the three ruled outcomes.

    Returns `(outcome, project_id)` where outcome is OUTCOME_CANONICAL,
    OUTCOME_NO_PORTABLE_ORIGIN or OUTCOME_DECLINE.

    - canonical: a network remote that parses under the canonical grammar.
    - no_portable_origin: no origin, an empty or whitespace-only string, a
      local path or `file://`, or a remote-helper transport
      (`_is_remote_helper`). The caller falls through to the basename.
      (An empty value inside the url list never reaches this function:
      `read_origin` reports it as `empty_entry`, which declines. A
      whitespace-only value does reach it, as an ordinary first url.)
    - decline: a NETWORK remote that was present but cannot be a canonical id:
      an unsupported scheme; a bad, zero, out-of-range or leading-zero port;
      a backslash or more than one `@`; a host outside `[a-z0-9.-]`, with a
      leading or trailing `.`/`-` or with an empty label (`a..b`); an empty
      path or an empty, `.` or `..` segment (including `%2E%2E`); whitespace,
      `@`, `?`, `#` or a control character in a path segment; a `%` not followed by two hex digits; an
      undecodable byte; a leading BOM; or an id over the length cap. A
      non-ASCII character or a sub-delimiter does NOT decline: it is kept in
      percent-encoded form (`_normalize_segment`). No id is produced and no
      basename is used; the override is the remedy.

    This exists because a single Optional cannot carry the distinction, and
    because the grammar only judges a FINISHED string — it cannot see whether
    a remote was present at all.
    """
    if not url or not url.strip():
        return OUTCOME_NO_PORTABLE_ORIGIN, None
    identifier = normalize_remote_url(url)
    if identifier is not None:
        return OUTCOME_CANONICAL, identifier
    if _is_remote_helper(url):
        return OUTCOME_NO_PORTABLE_ORIGIN, None
    if _is_network_shaped(url):
        return OUTCOME_DECLINE, None
    return OUTCOME_NO_PORTABLE_ORIGIN, None


def read_origin(git_dir: Path) -> OriginRead:
    """Read the `origin` URL of the repository whose git directory is
    `git_dir`.

    THE CONFIG IS READ FROM THE GIT DIRECTORY ITSELF (`git --git-dir`), never
    by running git in a work tree: with `-C <work tree>`, git discovers the
    repository again from that directory, and a `core.worktree` that places
    the work tree inside ANOTHER repository makes git answer with that
    repository's config. The override is read from this repository's common
    dir, so the origin must come from the same repository.

    THE IDENTITY RULE: the origin is the FIRST `remote.origin.url` in the
    repository's own config file (`<git_dir>/config`); values git reads from
    files that config includes are skipped, wherever they sit. With no
    include before it, that is the url git fetches from; later values are
    push urls.

    INCLUDED VALUES ARE SKIPPED, AND WITH NO VALUE FROM THE OWN FILE THE READ
    IS `not_own_file`, WHICH THE CALLER DECLINES. Read through `--git-dir
    <common dir>`, git evaluates an `includeIf onbranch:` or `gitdir:`
    condition against the main checkout, not the worktree a session is in,
    so an included url, before or instead of the own one, could move every
    worktree's id when the main checkout changes branch. `--show-origin`
    names the file each value came from, in the same call, and the own file
    is matched by real path. An unconditional `include.path` is skipped
    too.

    A LIST HOLDING AN EMPTY VALUE IS `empty_entry`, AND THE CALLER DECLINES
    IT, whatever the git version. A whitespace-only value is not empty: it
    is an ordinary url, and the first url binds the id as usual. Git 2.46 reads an
    empty value as clearing the urls before it, and earlier versions do not
    (measured on 2.43: `[A, "", B]` fetches A, where 2.46 fetches B). Taking
    either reading would give one repository two ids on two machines.

    Global, system and environment config (`GIT_CONFIG_COUNT`/`KEY_n`/
    `VALUE_n`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG`) and `config.worktree`
    are NOT identity inputs: they are machine-local or per-worktree, so
    reading them would give one repository different ids on different
    machines or worktrees. That is the same reason `git remote get-url` is
    not used: it applies the machine-local `url.*.insteadOf` rewrite table.
    These exclusions are the rule, not exceptions to it.

    `GIT_CONFIG` is also REMOVED from the environment, not only ignored: it
    names a file for `git config` alone (git fetch never reads it), and
    `--local` with it set exits 129 ("only one config file at a time"), which
    would turn every memory operation into a decline.

    `-z` because a quoted value may hold a newline, and a line-oriented
    reader would take the text before it as a complete remote.

    The value is returned RAW (un-normalized) because normalization is
    `normalize_remote_url`'s job and it must see the whitespace it is
    required to reject. Never logs the value: it can carry a token
    (`x-access-token:<token>@` is routine in CI).

    Exit status is the whole distinction, never stderr text (git messages are
    localised): exit 1 with an empty answer is "no such key", and then the
    repository's own config file decides (`_absent_origin`): ABSENT with no
    include section in it, INCLUDE_ONLY with one, CONFIG_UNREAD when it
    cannot be read. Any other non-zero status is "git could not answer" =
    UNAVAILABLE. A missing git, a timeout and a configuration git cannot
    read are all UNAVAILABLE.

    CONTRACT: call this only with a git directory git has ALREADY answered
    for (a common dir from `rev-parse --git-common-dir`). A directory that
    is not one makes `--local` exit 128, which reads as UNAVAILABLE.
    `detect_id_for_root` therefore gates on `common_dir` rather than using
    this call to discover repo-ness.
    """
    from shared.git_helpers import answered_once
    from shared.project_scope import git_env_without_location

    argv = [
        "git", "--git-dir", str(git_dir), "config", "--local", "--includes",
        "--show-origin", "-z", "--get-all", "remote.origin.url",
    ]
    try:
        result = answered_once(
            argv,
            lambda: subprocess.run(
                argv,
                capture_output=True,
                text=False,
                timeout=5,
                env=git_env_without_location(without=("GIT_CONFIG",)),
            ),
            text=False,
            env=git_env_without_location(without=("GIT_CONFIG",)),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return OriginRead(ORIGIN_UNAVAILABLE, None)
    if result.returncode == 1 and not result.stdout:
        # `git config --get-all` exits 1 when the key is not set.
        return _absent_origin(git_dir)
    if result.returncode != 0:
        return OriginRead(ORIGIN_UNAVAILABLE, None)
    # Each value is preceded by its origin (`file:<path>`), and both are
    # NUL-terminated, so the last piece of the split is empty.
    pieces = result.stdout.split(b"\0")[:-1]
    if len(pieces) % 2:
        return OriginRead(ORIGIN_UNAVAILABLE, None)
    origins, values = pieces[0::2], pieces[1::2]
    if not values:
        return _absent_origin(git_dir)
    if any(not value for value in values):
        return OriginRead(ORIGIN_EMPTY_ENTRY, None)
    own = os.path.realpath(os.path.join(git_dir, "config"))
    first_own = next(
        (
            value for origin, value in zip(origins, values)
            if origin.startswith(b"file:")
            and os.path.realpath(os.fsdecode(origin[len(b"file:"):])) == own
        ),
        None,
    )
    if first_own is None:
        return OriginRead(ORIGIN_NOT_OWN_FILE, None)
    # os.fsdecode, never locale decoding: a URL may hold bytes invalid in
    # the locale, and UnicodeDecodeError must not escape this reader.
    return OriginRead(ORIGIN_PRESENT, os.fsdecode(first_own))


def read_override(common_dir: Path) -> OverrideRead:
    """Read `<common_dir>/pact-project-id`.

    The common dir is resolved by the caller with `rev-parse
    --git-common-dir`; this function never shells to git. The location is
    deliberate: a working-tree `.pact/project-id` arrives with a clone, so it
    would let a repository redirect the memory scope of whoever checks it
    out.

    ONLY A CANONICAL `host/owner/repo` ID IS ACCEPTED. A folder-name value is
    reported as `not_canonical` and resolution continues without it, because
    a basename pin let a delivered checkout name a legacy bucket (any
    project's folder-name rows) with no evidence that it was that project.
    A copied store is moved with `project-id rekey` instead.

    THE COMMON-DIR PLACEMENT IS NARROWER, NOT UNREACHABLE, AND THE INFLUENCE
    IS NOT CONFINED TO THIS PROJECT. An archive that ships a `.git/`
    directory can carry this file, exactly as it can already carry
    `.git/config`. What it can name is ANY canonical bucket in the same
    user's store: another project's id or this project's own. A plain
    `.git` FILE in a checkout can likewise carry
    `gitdir:` and point at any repository, binding that repository's id with
    no override file at all. The trust requirement is therefore the one
    `.git` already has — an untrusted checkout can choose which bucket this
    process reads and writes, across the whole store. Never claim the file
    cannot travel, and never claim the impact is confined to the caller's
    own rows.

    Never raises; every failure is a fixed reason token.
    """
    path = Path(common_dir) / _OVERRIDE_FILENAME
    try:
        # lstat, NOT open(), BEFORE the read: a FIFO in this position would
        # block forever and hang every memory operation that resolves an
        # identity. Refuse anything that is not a regular file.
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return OverrideRead(None, path, "not_regular")
    except FileNotFoundError:
        return OverrideRead(None, None, None)
    except OSError:
        return OverrideRead(None, path, "unreadable")
    try:
        # O_NOFOLLOW, O_NONBLOCK and a second check on the descriptor: a
        # symlink put in place after the lstat fails the open (unreadable),
        # and a FIFO there is refused, not waited on.
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        with open(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return OverrideRead(None, path, "not_regular")
            # BOUNDED READ: one byte past the cap is enough to classify the
            # file as too long, so an accidentally huge or hostile file is
            # never allocated whole.
            raw = handle.read(_MAX_OVERRIDE_BYTES + 1)
    except FileNotFoundError:
        return OverrideRead(None, None, None)
    except OSError:
        return OverrideRead(None, path, "unreadable")
    from .database import pin_file_value

    value, reason = pin_file_value(raw)
    return OverrideRead(value, path, reason)


def read_user_setting(config_path: Path) -> Optional[str]:
    """The `project_id` from `<memory_dir>/config.json`, or None.

    The user-scope fallback for a session with no repository at all. Applied
    by the caller only when no repository is found, so a repo-scoped override
    wins wherever one is read. When the checkout the session is in is the
    home directory, a linked worktree included (inside a repository, a stray
    `.git` that git looks past does not make its folder a checkout), no
    override is read, and the
    cwd-marker route applies this setting there. Never raises.

    READ LIKE THE OVERRIDE FILE (`read_override`): only a regular file, never
    through a symlink or a FIFO, and at most `_MAX_USER_SETTING_BYTES`; a
    FIFO in this position would otherwise hang every resolution that reaches
    the setting.
    """
    path = Path(config_path)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        with open(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            data = handle.read(_MAX_USER_SETTING_BYTES + 1)
        if len(data) > _MAX_USER_SETTING_BYTES:
            return None
        payload = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, RecursionError):  # RecursionError: deeply nested JSON
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("project_id")
    if not isinstance(value, str) or not value or len(value) > _max_id_length():
        return None
    try:
        _, validate_identifier_for_write = _server_identity_impl()
        validate_identifier_for_write(value)
    except (ImportError, ValueError):
        return None
    return value


def _submodule_admin_dir(path: Optional[Path]) -> bool:
    """Whether `path` is a submodule admin dir, at any depth.

    THE CONDITION IS THE PATH SHAPE — a `.git` component followed by
    `modules`, either directly or after `worktrees/<one name>` — NOT a slash
    or a derived name. Measured: a genuine submodule reports the SAME
    directory as `--git-dir` and `--git-common-dir`, under `.git/modules/`
    (`<super>/.git/modules/<name>`, with `<name>` one or more segments, and
    `.../modules/<a>/modules/<b>` when nested); a submodule inside a linked
    worktree of its superproject lives under
    `<super>/.git/worktrees/<wt>/modules/<name>`. An ordinary local
    repository is `<root>/.git`, a linked worktree's common dir is its main
    repository's `.git`, and a `--separate-git-dir` checkout is an arbitrary
    external directory. None of the last three match, so none is suppressed.

    shortcut: a checkout whose own path sits under a literal `.git/modules`
    directory reads as a submodule admin dir; declining there is the safe
    direction, and such a layout has never been observed.
    """
    from .database import submodule_admin_path

    return submodule_admin_path(path)


# ONE DETECTION PASS'S `git rev-parse --git-dir` answers, keyed by the
# resolved start directory. `memory_api.detection_pass` sets a fresh dict for
# the length of one pass (one backlog command, one memory CLI command, or one
# construction's detection outside any pass), beside its common-dir answers,
# so the root finder, the redirect check and the submodule check ask git once
# per directory. Outside a pass every call runs git. A ContextVar, so
# concurrent passes never share one.
DETECTION_GIT_DIRS: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "DETECTION_GIT_DIRS", default=None
)


def one_git_answer(result) -> Optional[str]:
    """The one path a git command printed, with only git's final newline
    dropped, or None when git failed or printed nothing. Never stripped: a
    path can end in a space or hold a line separator."""
    if result.returncode != 0:
        return None
    out = result.stdout[:-1] if result.stdout.endswith(b"\n") else result.stdout
    return os.fsdecode(out) or None


def gitfile_target(text: str) -> Optional[str]:
    """`shared.git_helpers.gitfile_target`: the git dir a `.git` file
    names, read as git reads it, or None."""
    from shared.git_helpers import gitfile_target as target

    return target(text)


def __getattr__(name: str):
    """`GIT_FILE_LIMIT`, the most git reads of a `.git` file, is
    `shared.git_helpers.GIT_FILE_LIMIT`, the one constant the reader
    enforces; it is imported on first use, as every `shared` import here
    is, so the two cannot drift."""
    if name == "GIT_FILE_LIMIT":
        from shared.git_helpers import GIT_FILE_LIMIT

        return GIT_FILE_LIMIT
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def read_git_file(path) -> Optional[str]:
    """`shared.git_helpers.read_git_file`: the text of a `.git` file or a
    worktree admin `gitdir` file when it is a regular file of at most
    GIT_FILE_LIMIT bytes, else None; a FIFO is never opened."""
    from shared.git_helpers import read_git_file as read

    return read(path)


def worktree_admin_gitdir(text: str) -> str:
    """`shared.git_helpers.worktree_admin_gitdir`: the path a worktree admin
    dir's `gitdir` file names, trailing whitespace trimmed."""
    from shared.git_helpers import worktree_admin_gitdir as trimmed

    return trimmed(text)


def git_dir_answer(start) -> Optional[str]:
    """`git [-C start] rev-parse --git-dir` (`one_git_answer`), or None;
    answered once per resolved start inside a detection pass."""
    memo = DETECTION_GIT_DIRS.get()
    key = None
    if memo is not None:
        try:
            key = os.path.realpath(str(start) if start is not None else os.getcwd())
        except (OSError, ValueError):
            key = None
        if key is not None and key in memo:
            return memo[key]
    from shared.git_helpers import answered_once
    from shared.project_scope import git_env_without_location

    command = ["git"]
    if start is not None:
        command += ["-C", str(start)]
    command += ["rev-parse", "--git-dir"]
    try:
        result = answered_once(
            command,
            lambda: subprocess.run(
                command,
                capture_output=True,
                text=False,
                timeout=5,
                env=git_env_without_location(),
            ),
            text=False,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        answer = None
    else:
        answer = one_git_answer(result)
    if memo is not None and key is not None:
        memo[key] = answer
    return answer


def git_refuses_ownership(start) -> bool:
    """Whether git refuses `start`'s repository as owned by another user
    ("detected dubious ownership"): the same `git -C start rev-parse
    --git-dir` `git_dir_answer` asks (one answer per detection pass), read
    for git's hint line, `git config --global --add safe.directory <path>`,
    which git prints untranslated, naming `start` itself. Never raises;
    False when git cannot be asked."""
    from shared.git_helpers import answered_once
    from shared.project_scope import git_env_without_location

    command = ["git"]
    if start is not None:
        command += ["-C", str(start)]
    command += ["rev-parse", "--git-dir"]
    try:
        result = answered_once(
            command,
            lambda: subprocess.run(
                command,
                capture_output=True,
                text=False,
                timeout=5,
                env=git_env_without_location(),
            ),
            text=False,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return False
    if result.returncode == 0:
        return False
    # Keyed on git's untranslated hint line, not on the English "dubious
    # ownership" git translates, nor on "safe.directory" anywhere, which a
    # path git echoes can contain; the line must name `start`, since a path
    # holding a newline can carry a whole hint line naming somewhere else.
    try:
        asked = os.path.realpath(str(start) if start is not None else os.getcwd())
    except (OSError, ValueError):
        return False
    prefix = b"git config --global --add safe.directory "
    for line in (result.stderr or b"").splitlines():
        line = line.lstrip()
        if not line.startswith(prefix):
            continue
        try:
            named = shlex.split(os.fsdecode(line[len(prefix):]))
        except ValueError:
            continue
        if len(named) == 1 and os.path.realpath(named[0]) == asked:
            return True
    return False


# =============================================================================
# Detection-pass memos and the worktree listing reader
# =============================================================================
#
# One reader of `git worktree list` for the memory resolver and the backlog.
# `memory_api.detection_pass` opens a pass by setting these memos; outside a
# pass every call asks git.

# THE PASS'S OTHER GIT ANSWERS (`rev-parse` questions and worktree listings,
# keyed by the question and the resolved directory it is asked in, and a
# root's identity read, keyed ('identity', root, common dir, asked root)), a
# failed answer included. Set by `memory_api.detection_pass` beside its common-dir memo and
# reset when the pass ends; unlike that memo it is not handed to the first
# legacy read. A forged worktree pointer is checked twice in one pass, by the
# root finder and by the rung's guard, and without this each check lists the
# worktrees and asks the toplevel again.
DETECTION_ANSWERS: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "DETECTION_ANSWERS", default=None
)


# THE PASS'S WORKTREE LISTINGS, keyed by the `--git-dir` argument exactly as
# git was given it, each the records `worktree_records` read. Only a listing
# git gave is kept. Set by `memory_api.detection_pass` beside `DETECTION_ANSWERS`, and
# handed by a construction to its FIRST legacy read, whose gate asks for the
# same listing of the same common dir; a later read lists again, answered
# from an open pass's git results (`git_helpers.PASS_GIT_RESULTS`) when that
# pass already listed the same common dir, and by git otherwise.
DETECTION_LISTINGS: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "DETECTION_LISTINGS", default=None
)


# The default for `listing` in `memory_api.main_repo_root`,
# `memory_api.root_check_refusal` and the redirect check: the caller holds no
# worktree listing, so the check asks git itself.
LISTING_UNASKED: Any = object()


def detection_answer(question: tuple, ask):
    """`ask()`'s answer, asked once per detection pass for each question.
    Outside a pass every call asks."""
    memo = DETECTION_ANSWERS.get()
    if memo is None:
        return ask()
    if question not in memo:
        memo[question] = ask()
    return memo[question]


def question_dir(start) -> str:
    """The resolved directory a git question is asked in (the cwd for None)."""
    try:
        return os.path.realpath(start if start is not None else os.getcwd())
    except (OSError, ValueError):
        return str(start)


def listed_worktree_records(
    start, *, git_dir: Optional[Path] = None
) -> "Optional[List[List[str]] | str]":
    """Every record `git worktree list` reports for `start`'s repository.

    With `git_dir`, the listing is of THAT repository (`--git-dir`), which is
    the only form that reaches a submodule's own worktrees: run from a
    directory, git discovers the enclosing repository instead.

    `None` when git cannot enumerate, or the read raises — a failed ANSWER,
    not a failed verification, so callers must not treat it as proof of a
    redirect. `EMPTY_LISTING` or `UNREADABLE_LISTING` when git printed a
    listing that is no record list (`worktree_records`).
    """
    target = ("--git-dir", question_dir(git_dir)) if git_dir is not None else ("-C", question_dir(start))

    def ask():
        # A read that raises gives no listing, as a git that failed does, and
        # that answer is the pass's: every later reader sees the same failure.
        try:
            return _ask_worktree_records(start, git_dir)
        except (OSError, RuntimeError, ValueError):
            return None

    return detection_answer(("worktree list", target), ask)


def worktree_toplevels(records) -> "Optional[set | str]":
    """The resolved path of every record; an answer that is no record list
    (`None`, `EMPTY_LISTING`, `UNREADABLE_LISTING`) is returned as it is. A
    path that will not resolve is skipped."""
    if not isinstance(records, list):
        return records
    toplevels = set()
    for record in records:
        for field in record:
            if not field.startswith("worktree "):
                continue
            try:
                toplevels.add(Path(field[len("worktree "):]).resolve())
            except (OSError, RuntimeError):
                continue
    return toplevels


# git's exit status for a usage error (parse-options), which is how a git
# older than 2.36 answers `worktree list -z`: that option did not exist yet.
_GIT_USAGE_ERROR = 129

# The fields `git worktree list --porcelain` prints, one per line, each a
# key and a value (ending in a space here) or a bare flag, with its place
# in the order git prints a record (builtin/worktree.c,
# show_worktree_porcelain): `worktree`, then `bare` or `HEAD`, then
# `detached` or `branch`, then `locked`, then `prunable`, which git prints
# only with a reason.
_PORCELAIN_ORDER = (
    ("worktree ", 0), ("bare", 1), ("HEAD ", 1), ("detached", 2),
    ("branch ", 2), ("locked", 3), ("locked ", 3), ("prunable ", 4),
)

# `worktree_records`' answers for a listing git printed that is no record
# list (None is a listing git did not give): one holding no record or an
# empty record, and one whose records are not git's fields in git's order.
EMPTY_LISTING = "empty"
UNREADABLE_LISTING = "unreadable"


def worktree_records(prefix: list) -> "Optional[List[List[str]] | str]":
    """`git <prefix> worktree list --porcelain` as records, each the list of
    its fields; `EMPTY_LISTING` when git printed no record or an empty one;
    `UNREADABLE_LISTING` when git printed records that are not git's fields
    in git's order; or None when no listing can be had.

    `-z` is asked first: every field then ends in NUL and an empty field ends
    a record, so a path holding a newline stays whole. A git older than 2.36
    rejects `-z` as a usage error, and the listing is asked again without
    it; that listing is one field per line, with a blank line ending each
    record, and git prints a path there unquoted, so a path holding a newline
    is cut into lines.

    With `-z`, a field whose key git did not print when this reader was
    written is skipped (`porcelain_key_known`). Either way, a record whose
    fields are not git's, in git's order (`_record_fields_in_order`), makes
    the listing `UNREADABLE_LISTING`, not records. An empty record or a
    listing with no record is `EMPTY_LISTING`: git always lists the main
    worktree first. Without `-z`, a `bare` record that is not the main
    repository's directory also makes the listing `UNREADABLE_LISTING`, and
    a linked worktree's record is dropped when its folder is present but
    holds no `.git`; a record whose folder is absent is kept
    (`_plain_records_kept`). None is a failed or timed-out git. A caller
    reads neither string as an empty listing.
    """
    from shared.git_helpers import answered_once
    from shared.project_scope import git_env_without_location

    def ask(extra: list):
        argv = ["git", *prefix, "worktree", "list", "--porcelain", *extra]
        return answered_once(
            argv,
            lambda: subprocess.run(
                argv, capture_output=True, text=False, timeout=5,
                env=git_env_without_location(),
            ),
            text=False,
            env=git_env_without_location(),
        )

    try:
        result = ask(["-z"])
        nul = result.returncode != _GIT_USAGE_ERROR
        if not nul:
            result = ask([])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    # The text after the last terminator is "" when git ended the listing
    # with one; it is not an empty field.
    *fields, tail = os.fsdecode(result.stdout).split("\0" if nul else "\n")
    records, current = [], []
    for field in fields + ([tail] if tail else []):
        if field:
            current.append(field)
        elif current:
            records.append(current)
            current = []
        else:
            return EMPTY_LISTING
    if current:
        records.append(current)
    if not records:
        return EMPTY_LISTING
    if nul:
        records = [[f for f in record if porcelain_key_known(f)] for record in records]
    if not all(_record_fields_in_order(record) for record in records):
        return UNREADABLE_LISTING
    if not nul:
        kept = _plain_records_kept(records)
        if kept is None:
            return UNREADABLE_LISTING
        records = kept
    # git prints the path a worktree's admin entry holds; a NUL in that file
    # can leave it empty or relative, and such a path would resolve against
    # the process cwd. It names no directory, so its record is dropped.
    return [record for record in records if os.path.isabs(record[0][len("worktree "):])]


def porcelain_key_known(field: str) -> bool:
    """Whether a `-z` listing field's key is one `_PORCELAIN_ORDER` names.

    A field with any other key is one git added after this reader (as git
    2.31 added `locked` and `prunable`), and is skipped. With `-z` no path is
    cut, so such a field is never part of a path; without `-z` it can be,
    and every field must be known."""
    key = field.split(" ", 1)[0]
    return any(key == known.rstrip() for known, _ in _PORCELAIN_ORDER)


def _record_fields_in_order(record: List[str]) -> bool:
    """Whether a listing record holds only fields git prints, in the order
    git prints them (`_PORCELAIN_ORDER`), with or without `-z`.

    git prints a record's `worktree` field first and its `bare` or `HEAD`
    field second. Without `-z`, a path cut at a newline puts the rest of the
    folder name between those two lines, or ends the record after the
    `worktree` line; with `-z`, a listing cut short or holding a stray NUL
    does the same. Either record is refused here.
    """
    places = []
    for field in record:
        place = next(
            (
                rank for key, rank in _PORCELAIN_ORDER
                if (field.startswith(key) if key.endswith(" ") else field == key)
            ),
            None,
        )
        if place is None:
            return False
        places.append(place)
    if places[:2] != [0, 1] or any(a >= b for a, b in zip(places, places[1:])):
        return False
    return record[1] != "bare" or 2 not in places


def _plain_records_kept(records: List[List[str]]) -> Optional[List[List[str]]]:
    """The records of a listing without `-z` that name a checkout, or None
    when the listing cannot be read: one stat per record, no git call.

    The main repository's record is kept: git derives its path from the
    repository itself, and with `--separate-git-dir` that path is the git
    directory, which holds no `.git`. If it says `bare`, its path must be a
    directory; `bare` in any other record is not git's. A linked worktree's
    record is dropped when its folder is present but holds no `.git`: git
    still lists a worktree whose folder lost its `.git`, and a path cut at a
    newline can name a present folder git did not list. A dropped record
    cannot be the asked checkout and adds no root. A record whose folder is
    absent is kept, as the `-z` listing keeps it: a worktree on a drive not
    mounted now, or one moved or deleted (git before 2.31 prints no
    `prunable` or `locked` line to tell them apart). An absent folder names
    no other repository's checkout.
    """
    kept = []
    for index, record in enumerate(records):
        path = record[0][len("worktree "):]
        if "bare" in record:
            if index != 0 or not os.path.isdir(path):
                return None
        elif (
            index != 0
            and not os.path.lexists(os.path.join(path, ".git"))
            and os.path.lexists(path)
        ):
            continue
        kept.append(record)
    return kept


def _ask_worktree_records(start, git_dir: Optional[Path]) -> "Optional[List[List[str]] | str]":
    prefix = (
        ["--git-dir", str(git_dir)] if git_dir is not None
        else ["-C", str(start)] if start is not None
        else []
    )
    records = worktree_records(prefix)
    # KEEP THIS `if not isinstance(records, list): return` ABOVE THE STORE
    # BELOW, and bind `records` nowhere between them. A listing answer may be
    # stored in a container only where such a test, or the body of `if
    # isinstance(records, list):`, has excluded every answer that is not a
    # record list; a test fails a store of a listing answer neither guards.
    if not isinstance(records, list):
        return records
    listings = DETECTION_LISTINGS.get()
    if listings is not None and git_dir is not None:
        listings[str(git_dir)] = records
    return records


def verified_worktree_roots(
    repo_root: Path, common_dir: Optional[Path], listing: Optional[list] = None
) -> List[Path]:
    """The gate roots: the identity root plus every TWO-WAY-LINKED worktree.

    `git worktree list` reports whatever `<common>/worktrees/*/gitdir` names,
    and a delivered archive with its own `.git` can name ANY directory there,
    including another project's real checkout, which would then place that
    project's absolute rows "under" this repository. A listed worktree W is
    kept only when (a) git does not call it prunable, (b) `W/.git` is a FILE
    whose `gitdir:` is `<common>/worktrees/<name>`, and (c) that admin dir's
    `gitdir` names `W/.git` back. Anything else is dropped, which can only
    narrow the gate; no listing (git failed, or the read raised), or a
    listing that is no record list (`EMPTY_LISTING`, `UNREADABLE_LISTING`),
    narrows it to the identity root.

    `listing`, the records (`worktree_records`) the detection pass got for
    this same `common_dir`, is used instead of asking git again.
    """
    roots = [Path(repo_root).resolve()]
    if common_dir is None:
        return roots
    try:
        records = listing if listing is not None else worktree_records(["--git-dir", str(common_dir)])
    except (OSError, RuntimeError, ValueError):
        # A read that raises gives no listing, as a git that failed does.
        records = None
    if not isinstance(records, list):
        return roots
    try:
        admin_parent = (Path(common_dir) / "worktrees").resolve()
    except (OSError, RuntimeError, ValueError):
        return roots
    for record in records[1:]:  # the first record is the main worktree
        worktree = next(
            (f[len("worktree "):] for f in record if f.startswith("worktree ")), None
        )
        if worktree is None or any(
            f == "prunable" or f.startswith("prunable ") for f in record
        ):
            continue
        try:
            marker = Path(worktree) / ".git"
            if not marker.is_file():
                continue
            text = read_git_file(marker)
            target = gitfile_target(text) if text is not None else None
            if target is None:
                continue
            admin = Path(target)
            if not admin.is_absolute():
                admin = Path(worktree) / admin
            admin = admin.resolve()
            if admin.parent != admin_parent:
                continue
            back_text = read_git_file(admin / "gitdir")
            if back_text is None:
                continue
            back = Path(worktree_admin_gitdir(back_text))
            if not back.is_absolute():
                back = admin / back
            if back.resolve() != marker.resolve():
                continue
            roots.append(Path(worktree).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return list(dict.fromkeys(roots))


def _resolved_git_dir(asked_root: Path) -> Optional[Path]:
    """The resolved `--git-dir` for `asked_root`, or None.

    Used only by the submodule conjunction below, so it runs on the rare
    admin-dir-shaped repository rather than on every detection. A relative
    answer is resolved against the directory the command ran in, as git
    reports it. Inside a detection pass the root finder's answer for the
    same directory is reused.
    """
    raw = git_dir_answer(asked_root)
    if raw is None:
        return None
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = asked_root / git_dir
    try:
        return git_dir.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _submodule_checkout(
    root: Optional[Path],
    common_dir: Optional[Path],
    asked_root: Optional[Path],
) -> bool:
    """The three-leg SUBMODULE-CHECKOUT conjunction.

    A basename-derived id is refused only when ALL THREE hold, because any
    one alone is also true of a shape that must keep its name:

    1. the resolved common dir has the submodule admin-dir path shape
       (`_submodule_admin_dir`);
    2. `git-dir == common-dir` for the asked directory, OR the git dir is
       `<common-dir>/worktrees/<name>`: a linked worktree OF THE SUBMODULE,
       whose folder name is no more the user's choice than the submodule's.
       Any other git dir is ruled out;
    3. the identity root's `.git` is a FILE (rules out an ordinary checkout,
       whose `.git` is a directory, and a `--separate-git-dir` store, which
       has no `.git` marker at the root at all).

    When a leg is shown false the answer is False: the ordinary basename
    ruling continues. When a leg cannot be checked (the `.git` cannot be
    examined, git gives no git dir, or a path will not resolve) the answer is
    True: a check that could not be made never lets the basename through.
    """
    if root is None or common_dir is None:
        return False
    if not _submodule_admin_dir(common_dir):
        return False
    try:
        if not (Path(root) / ".git").is_file():
            return False
    except (OSError, RuntimeError, ValueError):
        return True
    asked = Path(asked_root) if asked_root is not None else Path(root)
    git_dir = _resolved_git_dir(asked)
    if git_dir is None:
        return True
    try:
        common = Path(common_dir).resolve()
    except (OSError, RuntimeError, ValueError):
        return True
    return git_dir == common or git_dir.parent == common / "worktrees"


def filesystem_case_insensitive(root: Optional[Path]) -> bool:
    """Whether the filesystem holding `root` PROVABLY folds case.

    The legacy-bucket fold exists for one situation: the bucket was named on
    a filesystem that cannot tell `foreignproj` from `ForeignProj`, so the
    two spellings name one directory. On a case-SENSITIVE filesystem they
    name two projects, and folding merges them.

    The probe toggles the case of the root's own basename and asks whether
    the toggled name is the SAME directory entry (`os.lstat` identity, so a
    symlink planted at the toggled name is never followed), which answers the
    case question without writing anything and without `os.path.normcase`
    (a no-op on Linux, so a normcase gate would fold on a case-sensitive
    filesystem and pass on macOS by construction).

    True only on a positive answer. Every other state — no root, a basename
    with no cased characters, a missing or unreadable path — is
    UNDETERMINABLE and answers False, which is the safe direction: no fold.
    """
    if root is None:
        return False
    try:
        path = Path(root)
        name = path.name
        toggled = name.swapcase()
        if not name or toggled == name:
            return False
        # THE DIRECTORY ENTRY, NOT WHAT IT POINTS AT (lstat, never samefile):
        # on a case-sensitive filesystem anyone who can write the parent can
        # plant `ForeignProj -> foreignproj`, and a followed comparison would
        # report a fold. On a folding filesystem the toggled name IS the
        # root's own entry, so the two lstat answers are one inode.
        return os.path.samestat(os.lstat(path), os.lstat(path.parent / toggled))
    except (OSError, RuntimeError, ValueError):
        return False


def _refuse_submodule_basename(
    root: Path, common_dir: Path, override_ignored: Optional[dict]
) -> RootIdentity:
    """Decline a basename-derived id for a submodule-controlled checkout.

    A submodule's CHECKOUT PATH is chosen by the superproject's tracked
    `.gitmodules`, so using its basename as a project id lets a repository
    name another project's bucket — and that bucket is then read AND written
    with no union involved. A canonical origin or an operator override still
    answers; only the basename fallback is refused. The override is named in
    the decline record because it is the remedy, and it is read BEFORE this
    point, so a deliberate pin still works. NOTHING IS LOGGED HERE: the
    resolver reports the decline once, with its rung.
    """
    return RootIdentity(
        None,
        None,
        "declined",
        override_ignored,
        # `source` is filled by the calling rung, which is the only layer
        # that knows which declaration or fallback declined.
        decline_record("submodule_checkout", str(common_dir / _OVERRIDE_FILENAME)),
    )


def _admin_dir_refusal(admin_dir: Path, asked_root: Path) -> Optional[str]:
    """None when a SUBMODULE admin dir records `asked_root` as its checkout,
    else the detail of the refusal: CORE_WORKTREE_UNREAD_DETAIL when git
    could not read `core.worktree`, FOREIGN_GIT_DIR_DETAIL otherwise.

    A `.git` FILE pointing at `<other>/.git/modules/<name>` makes git report
    that submodule's git dir and common dir, which are EQUAL — so the
    linked-worktree listing check cannot see the redirect, and the target's
    basename is `<name>`, not `.git`, so the direct predicate cannot either.
    A genuine submodule's admin dir records where its checkout actually is in
    `core.worktree`, relative to the admin dir; a forged pointer names a
    directory that is not that checkout.

    MEASURED: a genuine submodule's `core.worktree` is `../../../submod`,
    resolving to the submodule checkout the caller is standing in; a forged
    pointer's resolves to somebody else's checkout. AN UNSET `core.worktree`
    (`git submodule deinit` and `git config --unset` leave it) is accepted
    only when git, asked at the marker's directory, names that directory as
    its top level; otherwise it is refused, since the submodule's origin
    survives the deinit and accepting it would bind another repository's id.
    """
    try:
        from shared.git_helpers import answered_once
        from shared.project_scope import git_env_without_location

        # `--local`: the admin dir's OWN config only. A global, system or
        # environment `core.worktree` is not this submodule's record, and
        # reading one would let machine-local config vouch for a pointer.
        argv = ["git", "--git-dir", str(admin_dir), "config", "--local", "--get", "core.worktree"]
        result = answered_once(
            argv,
            lambda: subprocess.run(
                argv,
                capture_output=True,
                text=False,
                timeout=5,
                env=git_env_without_location(),
            ),
            text=False,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        # UNPROVABLE, so FAIL CLOSED. This function is reached only for the
        # `<...>/.git/modules/<name>` shape (a submodule admin dir), and git
        # being unable to answer there says nothing about whether the pointer
        # is genuine, so refusing here is fail-closed by design: a legitimate
        # checkout git cannot answer for is refused too, with the cause named.
        return CORE_WORKTREE_UNREAD_DETAIL
    if result.returncode not in (0, 1) or (result.returncode == 1 and result.stdout):
        # `git config --get` exits 1 with no output when the key is unset;
        # any other failure is git not reading it.
        return CORE_WORKTREE_UNREAD_DETAIL
    value = one_git_answer(result)
    if value is None:
        # AN UNSET `core.worktree` IS A STATE GIT COMMANDS MAKE (`git config
        # --unset core.worktree`, a deinit and re-init), and git then takes
        # the checkout it is run from as the work tree. So the checkout is
        # this admin dir's when git, asked there, names it as its top level.
        # A hand-written `.git` naming such an admin dir reads the same and
        # binds only the submodule's own origin (adversarial-only).
        if _git_names_toplevel(asked_root):
            return None
        return FOREIGN_GIT_DIR_DETAIL
    recorded = Path(value)
    if not recorded.is_absolute():
        recorded = admin_dir / recorded
    try:
        recorded = recorded.resolve()
        asked = Path(asked_root).resolve()
    except (OSError, RuntimeError, ValueError):
        return FOREIGN_GIT_DIR_DETAIL
    try:
        # SAME INODE, NOT THE SAME SPELLING: on a case-insensitive filesystem
        # a genuinely recorded `core.worktree` can differ from the checkout
        # path only by case. `samefile` settles that without folding the
        # comparison, which would be wrong on a case-sensitive filesystem.
        return None if os.path.samefile(recorded, asked) else FOREIGN_GIT_DIR_DETAIL
    except OSError:
        # A recorded path that does not exist cannot be this checkout's
        # worktree, and the question is unprovable: fail closed.
        return FOREIGN_GIT_DIR_DETAIL


def _git_names_toplevel(root: Path) -> bool:
    """Whether `git -C root rev-parse --show-toplevel` names `root` itself.
    Any failure is False."""
    try:
        from shared.git_helpers import answered_once
        from shared.project_scope import git_env_without_location

        argv = ["git", "-C", str(root), "rev-parse", "--show-toplevel"]
        result = answered_once(
            argv,
            lambda: subprocess.run(
                argv,
                capture_output=True,
                text=False,
                timeout=5,
                env=git_env_without_location(),
            ),
            text=False,
            env=git_env_without_location(),
        )
        toplevel = one_git_answer(result)
        return toplevel is not None and os.path.samefile(toplevel, root)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return False


def _redirect_target_refusal(target: Path, marker_root: Path) -> Optional[str]:
    """None unless a `.git` marker's resolved TARGET is another repository's
    git dir; then the detail of the refusal.

    ONE predicate for every route by which `.git` can redirect: a gitfile's
    `gitdir:` line and a symlinked `.git`, so the routes cannot drift apart.

    - A target named exactly `.git` is another checkout's git dir: a
      redirect. (A legitimate `--separate-git-dir=<dir>/.git` takes this
      shape too; see `_gitfile_redirect_detail`.)
    - A submodule admin dir at any depth (`_submodule_admin_dir`) has its
      git dir EQUAL to its common dir, so only its recorded `core.worktree`
      can tell a genuine submodule from a pointer at somebody else's.

      THE SHAPE IS CHECKED FIRST, so a linked-worktree target never reaches
      that git subprocess, which is unaffordable on every detection.

      A LINKED-WORKTREE ADMIN DIR IS EXCLUDED BY GIT'S OWN MARK, its
      `commondir` file, which a submodule admin dir never holds. A worktree
      OF a submodule has its admin dir at
      `<super>/.git/modules/<name>/worktrees/<wt>`, which has the submodule
      shape, but its `core.worktree` is read from the shared submodule config
      and names the submodule's MAIN checkout, so the comparison would refuse
      the genuine worktree. A name test cannot replace the mark: a submodule
      may itself be named `x/worktrees/y`.
    - Anything else is not a redirect here. A linked-worktree admin dir is
      checked against git's worktree listing by the resolver, and an
      arbitrary external git dir (a bare clone, a `--separate-git-dir`
      store, an Android `.repo/projects/<name>.git`) binds its own identity,
      the accepted residual.
    """
    if target.name == ".git":
        return FOREIGN_GIT_DIR_DETAIL
    if _submodule_admin_dir(target) and not os.path.exists(target / "commondir"):
        return _admin_dir_refusal(target, marker_root)
    return None


def _gitfile_redirect_detail(root: Path) -> Optional[str]:
    """None unless `<root>/.git` redirects at another repository's git dir;
    then the detail of the refusal.

    Two routes reach the same question, and both answer through
    `_redirect_target_refusal`:

    - a `.git` FILE whose `gitdir:` line names the target (followed, if the
      file is reached through a symlink, exactly as git reads it);
    - a `.git` SYMLINK to a directory. `is_dir()` follows the link and would
      see an ordinary checkout, so the link is resolved to its target first.

    A genuine submodule points at `<super>/.git/modules/<name>`, a genuine
    linked worktree at `<main>/.git/worktrees/<name>`, and a genuine
    `--separate-git-dir` at an arbitrary path. The ONE shape whose target is
    a directory named exactly `.git` is a checkout redirecting at another
    repository's git dir: git then reports that repository's git dir, common
    dir and origin as if the asking directory were its checkout, so a write
    would file into a project nobody named.

    Read straight from the marker rather than through git: a comparison of
    git answers cannot see this (every answer agrees). `detect_id_for_root`
    runs the check AFTER the override read and BEFORE the origin read: the
    origin would otherwise be taken from the foreign repository, and a usable
    override is answered first on purpose (see the measured exception below).

    A SYMLINKED PARENT DIRECTORY IS NOT A REDIRECT: the marker is then the
    real checkout's own `.git` directory (the walk resolves the start), so
    the asked folder IS that checkout, reached by another path.

    MEASURED EXCEPTION, and the reason the caller suppresses only the ORIGIN:
    a legitimate `git init --separate-git-dir=<dir>/.git` names its store
    directory literally `.git`, so it takes this same shape. It therefore
    cannot derive an id from its own remote, but it is RECOVERABLE — the
    override is read first, so one pin at `<common-dir>/pact-project-id`
    restores it. A forgery can likewise bind a pin, but only one the victim
    deliberately wrote, never its remote. That binding is ADVERSARIAL-ONLY
    and kept: it needs a hand-written `.git` file, and declining the pin
    would leave the honest `--separate-git-dir` layout, whose `.git` file is
    byte-identical, with no route to an id.
    """
    # THE MARKER IS FOUND BY WALKING UP, AS GIT DOES (`find_git_marker`).
    # The caller may hand us a SUBDIRECTORY of the redirected checkout (a
    # declared path one level down is the common shape), and `<subdir>/.git`
    # does not exist — so testing only the immediate directory leaves the
    # subdirectory case open. The `core.worktree` comparison is against the
    # MARKER'S directory (the checkout root), never the raw input, or a
    # legitimate subdirectory of a genuine submodule would be refused.
    target = _marker_target(root)
    if target is None:
        return None
    return _redirect_target_refusal(*target)


def _marker_target(root: Path) -> Optional[Tuple[Path, Path]]:
    """`(resolved git dir, checkout root)` when `.git` points elsewhere.

    Found by walking up as git does (`find_git_marker`). A `.git` SYMLINK to
    a directory resolves to its target; a `.git` FILE is read from its
    `gitdir:` line (relative to the marker's directory, as git reads it).
    None for a real `.git` directory, no marker, or an unreadable marker.
    """
    marker = find_git_marker(root)
    if marker is None:
        return None
    marker_root = marker.parent
    try:
        if marker.is_symlink() and marker.is_dir():
            return marker.resolve(), marker_root
        if not marker.is_file():
            # A real `.git` DIRECTORY: an ordinary checkout.
            return None
        text = read_git_file(marker)
    except (OSError, RuntimeError):
        return None
    if text is None:
        return None
    target = gitfile_target(text)
    if target is None:
        return None
    resolved = Path(target)
    if not resolved.is_absolute():
        resolved = marker_root / resolved
    try:
        return resolved.resolve(), marker_root
    except (OSError, RuntimeError):
        return None


def _without_path(override_ignored: Optional[dict]) -> Optional[dict]:
    """`override_ignored` with its path withheld and its reason kept."""
    if not override_ignored:
        return override_ignored
    return {"path": None, "reason": override_ignored.get("reason")}


def _marker_names_external_git_dir(root: Path) -> bool:
    """Whether the checkout's `.git` points at a git dir outside it that is
    neither a linked worktree's admin dir nor a submodule's.

    Such a dir is a user's own `--separate-git-dir` store, an Android
    `.repo/projects/<name>.git`, or a forged pointer at somebody else's: the
    shapes look the same, so its override path is never printed.
    """
    target = _marker_target(root)
    if target is None:
        return False
    git_dir = target[0]
    if os.path.exists(git_dir / "commondir"):
        return False
    return not _submodule_admin_dir(git_dir)


def repository_folder_name(root: Path, common_dir: Path) -> Optional[str]:
    """The folder-name id of a repository with no portable origin.

    The checkout's name, except for a non-bare repository whose git dir is
    not `<checkout>/.git` (`git init --separate-git-dir`): git names no main
    checkout for such a git dir, so each checkout would otherwise name its
    own bucket. There the id is the git dir's own name without a trailing
    `.git` (its parent's name when the git dir is named `.git`), the same
    for the main checkout and every linked worktree. None when git cannot
    say whether the repository is bare (`_git_dir_is_bare`): the caller
    declines rather than guess between two names. Never raises.
    """
    name = Path(root).name
    try:
        common = Path(common_dir).resolve()
        if common.parent == Path(root).resolve():
            return name
    except (OSError, RuntimeError, ValueError):
        return name
    bare = _git_dir_is_bare(common)
    if bare is None:
        return None
    if bare:
        return name
    from .database import git_dir_own_name

    return git_dir_own_name(common) or name


def _git_dir_is_bare(common_dir: Path) -> Optional[bool]:
    """Whether the repository's own config (`--local`) says `core.bare`, as
    `git config --bool` reads it: a valueless `bare` is true, an empty
    `bare =` false. An unset key is not bare. None when git cannot answer (a
    timeout, no git, or an exit other than 0 or 1, which includes a config
    or a value git refuses). `database.core_bare_answer` gives the same
    answers from the config file.
    """
    try:
        from shared.git_helpers import CORE_BARE_ARGS, answered_once, core_bare_answer_from
        from shared.project_scope import git_env_without_location

        argv = ["git", "--git-dir", str(common_dir), *CORE_BARE_ARGS]
        result = answered_once(
            argv,
            lambda: subprocess.run(
                argv,
                capture_output=True,
                text=False,
                timeout=5,
                env=git_env_without_location(),
            ),
            text=False,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None
    # The one reading of git's answer, shared with the CLAUDE.md root's
    # bare check (`project_scope._repository_is_bare`).
    return core_bare_answer_from(result)


# Why a folder-name id could not be chosen (`repository_folder_name`).
BARE_UNREAD_DETAIL = (
    "git could not say whether this repository is bare (core.bare), which "
    "decides the folder name the repository is filed under"
)


def _bare_unread_decline(
    override_ignored: Optional[dict], override_path: Optional[str]
) -> RootIdentity:
    """The `origin_unreadable` decline of a repository whose folder-name id
    cannot be chosen (`repository_folder_name` gave None)."""
    return RootIdentity(
        None, None, "declined", override_ignored,
        decline_record("origin_unreadable", override_path), BARE_UNREAD_DETAIL,
    )


def _folder_name_identity(
    root: Path,
    common_dir: Path,
    override_ignored: Optional[dict],
    override_path: Optional[str],
) -> RootIdentity:
    """The folder-name identity of a repository with no portable origin
    (`repository_folder_name`), or `_bare_unread_decline` when that name
    cannot be chosen."""
    folder = repository_folder_name(root, common_dir)
    if folder is None:
        return _bare_unread_decline(override_ignored, override_path)
    return RootIdentity(folder, "basename", "basename", override_ignored)


def detect_id_for_root(
    root: Optional[Path],
    common_dir: Optional[Path] = None,
    asked_root: Optional[Path] = None,
) -> RootIdentity:
    """Detect the identity for ONE repository root.

    `common_dir` is the caller's statement that this root IS a repository
    (it comes from `rev-parse --git-common-dir`). When it is None the root is
    not an established repository — a non-git marker or an umbrella folder —
    and the designed behaviour is the folder basename.

    For an established repository the order is:
    - a USABLE override at `<common_dir>/pact-project-id` -> that value. The
      override FOLLOWS THE IDENTITY ROOT: reading it at the process's
      repository instead would let a cwd pin retarget a declared session and
      would silently ignore a pin in the repository that actually won;
    - the asked directory's `.git` FILE redirects at another repository's git
      dir -> DECLINE (`gitfile_redirect`, no override path);
    - the origin is PRESENT and canonicalises -> the canonical id;
    - the origin is PRESENT and cannot be represented, its url list holds
      an empty value, or no url comes from the repository's own config file
      -> DECLINE (`origin_unrepresentable`);
    - the origin is ABSENT (none configured, `file://`, a local path) ->
      the repository's folder name (`repository_folder_name`: the
      checkout's name, or the git dir's own name for a `--separate-git-dir`
      repository, shared by its main checkout and every linked worktree),
      EXCEPT in a submodule: there
      the checkout path is chosen by the superproject's tracked `.gitmodules`,
      so a basename would let a repository name another project's bucket. A
      submodule-controlled basename DECLINES and names the override as the
      remedy. An ordinary local repository still basenames by ruling;
    - the origin is UNAVAILABLE (git cannot answer, unreadable config) ->
      DECLINE: a broken repository must not fall back
      to a folder name, because that files it in a silently different bucket.

    An UNUSABLE override is carried in `override_ignored` and resolution
    CONTINUES: a bad file must not disable memory. Never raises.
    """
    if root is None:
        return RootIdentity(None, None, "declined", None)
    name = Path(root).name
    if common_dir is None:
        return RootIdentity(name, "basename", "basename", None)

    override = read_override(common_dir)
    override_ignored = None
    if override.value is not None:
        # `read_override` returns only a canonical value.
        return RootIdentity(
            override.value, "canonical", "override file", None, None, None,
        )
    if override.reason is not None and override.path is not None:
        override_ignored = {"path": str(override.path), "reason": override.reason}

    # A REDIRECTED CHECKOUT SUPPRESSES THE ORIGIN READ, AND ONLY THAT.
    # The override above still answers first, deliberately: a legitimate
    # `--separate-git-dir=<dir>/.git` checkout takes this same shape, and
    # refusing before the pin would make it permanently unusable. What is
    # refused is the ORIGIN, which belongs to the other repository — so a
    # forgery can at most bind a pin the victim deliberately wrote, which is
    # strictly narrower than binding its remote. See
    # `_gitfile_redirect_detail`.
    #
    # THE PREDICATE IS EVALUATED ON THE ASKED DIRECTORY, NOT ON `root`. This
    # is load-bearing and was measured wrong once: `main_repo_root` follows a
    # forged gitfile's `core.worktree` to the VICTIM's checkout, so by the
    # time `root` arrives here it is a genuine submodule from the predicate's
    # point of view, the predicate answers False, and the origin read returns
    # the victim's id. `asked_root` is the directory the caller actually
    # asked about (the cwd, or the declared directory).
    asked = Path(asked_root) if asked_root is not None else Path(root)
    override_path = str(common_dir / _OVERRIDE_FILENAME)
    redirect_detail = _gitfile_redirect_detail(asked)
    if redirect_detail is not None:
        # THE REASON NAMES THE REAL CAUSE. Without it the rung's generic
        # "origin could not be read or represented" warning sends the
        # operator to inspect a remote when the `.git` marker is the thing
        # that redirected the answer.
        # NOR THE IGNORED OVERRIDE'S PATH: it sits in the repository the
        # marker points at, so printing it invites a write into that
        # repository. Its REASON is still reported.
        return RootIdentity(
            None,
            None,
            "declined",
            _without_path(override_ignored),
            decline_record("gitfile_redirect", None),
            redirect_detail,
        )

    # A GIT DIR OUTSIDE THE CHECKOUT THAT IS NEITHER A LINKED WORKTREE'S NOR A
    # SUBMODULE'S ADMIN DIR NAMES NO OVERRIDE PATH. Its override file
    # lives in that external directory, and the user's own store and a forged
    # pointer at another repository's store are the same shape, so a printed
    # path could lead a reader to re-pin a repository it does not own. The
    # override is still READ above; only the path is withheld from output,
    # and an unusable file there is still reported by its reason.
    external_detail = None
    if _marker_names_external_git_dir(asked):
        override_path = None
        override_ignored = _without_path(override_ignored)
        external_detail = EXTERNAL_GIT_DIR_DETAIL

    # A SUBMODULE'S CHECKOUT PATH IS REPOSITORY-CHOSEN, so a basename derived
    # from it must never bind. A canonical origin or the override above still
    # answers; only the basename fallback is refused. The test is the THREE-LEG
    # conjunction (admin-dir path shape, git-dir == common-dir, a `.git` FILE
    # at the identity root), so an ordinary local repository, a linked
    # worktree and a `--separate-git-dir` checkout each fail a different leg
    # and keep basenaming by ruling.
    submodule_controlled = _submodule_checkout(root, common_dir, asked_root)

    origin = read_origin(common_dir)
    if origin.state == ORIGIN_UNAVAILABLE:
        return RootIdentity(
            None,
            None,
            "declined",
            override_ignored,
            decline_record("origin_unreadable", override_path),
            external_detail,
        )
    if origin.state in _ORIGIN_DECLINE_DETAILS:
        cause = _ORIGIN_DECLINE_DETAILS[origin.state]
        return RootIdentity(
            None,
            None,
            "declined",
            override_ignored,
            decline_record("origin_unrepresentable", override_path),
            cause if external_detail is None else f"{cause}; {external_detail}",
        )
    if origin.state == ORIGIN_ABSENT:
        if submodule_controlled:
            return _refuse_submodule_basename(root, common_dir, override_ignored)
        return _folder_name_identity(root, common_dir, override_ignored, override_path)

    outcome, identifier = classify_remote_origin(origin.value)
    if outcome == OUTCOME_CANONICAL:
        return RootIdentity(identifier, "canonical", "origin remote", override_ignored)
    if outcome == OUTCOME_DECLINE:
        why = unrepresentable_detail(origin.value)
        return RootIdentity(
            None,
            None,
            "declined",
            override_ignored,
            decline_record("origin_unrepresentable", override_path),
            why if external_detail is None else (
                external_detail if why is None else f"{why}; {external_detail}"
            ),
        )
    if submodule_controlled:
        return _refuse_submodule_basename(root, common_dir, override_ignored)
    if _is_remote_helper(origin.value):
        folder = repository_folder_name(root, common_dir)
        if folder is None:
            return _bare_unread_decline(override_ignored, override_path)
        return RootIdentity(
            folder, "basename", "basename", override_ignored, None, REMOTE_HELPER_DETAIL
        )
    return _folder_name_identity(root, common_dir, override_ignored, override_path)
