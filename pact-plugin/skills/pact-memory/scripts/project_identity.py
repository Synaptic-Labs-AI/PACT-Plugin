"""
PACT Memory — repository-identity canonicaliser.

Location: pact-plugin/skills/pact-memory/scripts/project_identity.py

Turns a repository's remote URL into the stable project id that pact-memory
keys stored memories by, so one repository resolves to one bucket whatever
directory it was cloned into. Before this module the id was the working
directory's basename, which split one repository across machines and merged
unrelated folders that happened to share a name.

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

import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple, Optional

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
    r"^(?P<user>[^@\s/:\\]+@)?(?P<host>[^@:/\s\\]+):(?P<path>.*)$"
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


class OverrideRead(NamedTuple):
    """One read of `<common_dir>/pact-project-id`.

    `value` is the usable first line, or None. `path` is the file consulted
    (None when it is absent). `reason` is a fixed token naming why a PRESENT
    file was unusable — "not_regular", "not_utf8", "empty", "control_chars",
    "too_long", "not_canonical" or "unreadable". Absence is `value=None,
    path=None`, never a reason. "not_regular" covers a FIFO, a device node and a SYMLINK: none is
    read, because a FIFO in this position would block every memory operation
    that resolves an identity.
    """

    value: Optional[str]
    path: Optional[Path]
    reason: Optional[str]


class OriginRead(NamedTuple):
    """The tri-state answer to "what does this repository's origin say?".

    `present`     — git answered with a configured value.
    `absent`      — git answered, and no origin is configured (an empty value
                    is the same thing).
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
    `detect_id_for_root` sets it, and the caller discloses it.
    `decline_reason` is a structured {source, reason, override_path} when the
    decline had a NAMED cause (a repository-chosen basename), so
    `project-id show` can report it rather than leaving the operator with
    `declined` and no remedy. `source` names the rung that declined and is
    filled in by the caller, which is the only layer that knows it. Defaulted
    so the declines without a named cause keep their positional shape.
    """

    project_id: Optional[str]
    id_shape: Optional[str]
    source: str
    override_ignored: Optional[dict]
    decline_reason: Optional[dict] = None


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


def decline_record(
    reason: str, override_path: Optional[str], source: Optional[str] = None
) -> dict:
    """The `decline_reason` dict: exactly `{source, reason, override_path}`.

    `reason` must be one of `DECLINE_REASONS` (ValueError otherwise).
    `override_path` is the `<git-common-dir>/pact-project-id` file that would
    remedy the decline, or None when no override file can remedy it. It is
    None for `gitfile_redirect`, `no_working_tree`, `broken_git_marker` and
    `declaration_unresolved`.
    A `gitfile_redirect` given a path raises ValueError: the common dir git
    reports there belongs to the repository the `.git` file points at, so a
    pin written to it would re-pin that other repository. The remedy is the
    `.git` file itself. `source` is the rung that declined; this module
    leaves it None, and the resolver in memory_api, the only layer that
    knows the rung, fills it in.
    """
    if reason not in DECLINE_REASONS:
        raise ValueError(f"unknown decline reason: {reason!r}")
    if reason == "gitfile_redirect" and override_path is not None:
        raise ValueError("a gitfile_redirect decline names no override path")
    return {"source": source, "reason": reason, "override_path": override_path}


def _server_identity_impl() -> tuple:
    """The two grammar predicates owned by database.py (see module docstring).

    Imported at call time because the memory scripts are loaded both as a
    package (`scripts.memory_api`) and by direct `sys.path` insertion, and
    because `database.py` must not import this module.
    """
    from .database import is_canonical_id, validate_identifier_for_write

    return is_canonical_id, validate_identifier_for_write


_GIT_ENV_TRUE = frozenset({"true", "yes", "on"})
_GIT_ENV_FALSE = frozenset({"false", "no", "off"})


def _git_env_bool(name: str) -> bool:
    """A git boolean environment variable, parsed the way git parses it.

    Python truthiness is WRONG here: `bool("0")`, `bool("false")`, `bool("no")`
    and `bool("off")` are all True, while git reads each as FALSE. A value of
    "0" therefore crossed a filesystem boundary git would have stopped at.

    Accepted, case-insensitively: `true`/`yes`/`on` and any non-zero decimal;
    false for `false`/`no`/`off`/`0`/empty. An UNSET variable is the default,
    which is 0 for both git variables this module reads. Git treats anything
    else as a fatal config error; here it is FALSE.

    The empty string is FALSE and whitespace is NOT stripped, both MEASURED
    against git's own parser (`git -c probe.bool=<v> config --type=bool`):
    `""` answers false, and both `" "` and `" true"` answer
    `fatal: bad boolean config value`.
    """
    raw = os.environ.get(name)
    if raw is None:
        return False
    value = raw.lower()
    if not value or value in _GIT_ENV_FALSE:
        return False
    if value in _GIT_ENV_TRUE:
        return True
    if value.isascii() and value.isdigit():
        return int(value) != 0
    return False


def _git_ceiling_directories() -> set:
    """`GIT_CEILING_DIRECTORIES` parsed exactly as git parses it.

    Split on `os.pathsep`. An empty entry is dropped, and every entry AFTER
    the first empty one is kept AS WRITTEN, without resolving symlinks (git
    documents an empty entry as the way to skip that cost); entries before
    it are resolved. A relative entry is dropped. Measured: with
    `:<symlink to a>`, git keeps walking from `a/sub` into `a`.
    """
    ceilings = set()
    after_empty = False
    for entry in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep):
        if not entry:
            after_empty = True
            continue
        if not os.path.isabs(entry):
            continue
        try:
            ceilings.add(Path(entry) if after_empty else Path(entry).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return ceilings


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

    `lexists`, so a broken marker (a dangling symlink, a `.git` file naming a
    missing directory) still counts. Never raises: an unreadable path or
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
    while True:
        marker = level / ".git"
        try:
            if os.path.lexists(marker):
                return marker
        except (OSError, ValueError):
            return None
        parent = level.parent
        if parent == level or parent in ceilings:
            return None
        if not cross_filesystem:
            try:
                if os.stat(parent).st_dev != device:
                    return None
            except OSError:
                return None
        level = parent


def _max_id_length() -> int:
    """The identifier length cap, from database.py (ONE definition).

    Imported at call time for the same reason `_server_identity_impl` is: the
    memory scripts load both as a package and by direct `sys.path` insertion,
    so a module-level relative import would break the second form.
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

    Alphanumeric at both ends, and only `[a-z0-9.-]` between: a trailing dot,
    a leading hyphen and an underscore all name something git would not
    contact the same way.
    """
    if not host or not host[0].isalnum() or not host[-1].isalnum():
        return False
    return all(char in _HOST_CHARS for char in host)


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
    """
    if len(port) > 5 or not port.isdigit() or str(int(port)) != port:
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
    out = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "%":
            hex_pair = raw[index + 1:index + 3]
            if len(hex_pair) != 2 or not all(h in _HEX_DIGITS for h in hex_pair):
                return None
            decoded = chr(int(hex_pair, 16))
            out.append(
                decoded if decoded in _UNRESERVED_CHARS else "%" + hex_pair.upper()
            )
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


def _strip_path_suffixes(path: str) -> str:
    """Drop one trailing `/`, then one `.git`, then one exposed `/`.

    The second slash strip exists only for the `owner/.git` form: once `.git`
    is removed the path ends in the separator that used to precede it, and
    leaving it would create an empty final segment. It does NOT rescue
    `owner/repo//`: that form has no `.git`, so the single strip runs out and
    the empty segment reaches validation.
    """
    if path.endswith("/"):
        path = path[:-1]
    if path.lower().endswith(".git"):
        path = path[:-4]
        if path.endswith("/"):
            path = path[:-1]
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
      `DefaultCollection/{project}/_git/{repo}`;
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
        rest = segments[1:] if segments[:1] == ["DefaultCollection"] else segments
        if len(rest) == 3 and rest[1] == "_git":
            return [org, rest[0], "_git", rest[2]]
    return None


def _identifier(host: str, port: str, segments: list) -> str:
    """`host[:port]/<segments>`, after the bounded Azure DevOps Services rule."""
    azure = _azure_devops_services_id(host, port, segments)
    if azure is not None:
        host, segments = "dev.azure.com", azure
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

    value = _strip_one_terminator(url)

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
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        if ":" in authority:
            host, _, port = authority.rpartition(":")
            if not _valid_port(port):
                return None
            if _DEFAULT_PORTS.get(scheme) == int(port):
                port = ""
        else:
            host, port = authority, ""
        host = host.lower()
        if not _valid_host(host):
            return None
        path = _strip_path_suffixes(path)
        segments = _split_path(path)
        if not segments:
            return None
        identifier = _identifier(host, port, segments)
    else:
        scp_match = _scp_match(value)
        if scp_match is None:
            # Neither a URL nor scp syntax: indistinguishable from a relative
            # local path, which is NO PORTABLE ORIGIN.
            return None
        host = scp_match.group("host").lower()
        if not _valid_host(host):
            return None
        # RELATIVE AND ABSOLUTE SCP PATHS ARE DELIBERATELY ONE ID.
        # `git@host:srv/repo`, `git@host:/srv/repo` and `ssh://git@host/srv/repo`
        # all become `host/srv/repo`. On a forge the path is virtual and the
        # leading slash does not matter: `git@github.com:o/r` sends `o/r` and
        # `ssh://git@github.com/o/r` sends `/o/r` to one repository, so
        # telling them apart would split every forge's scp and https pair.
        # The cost is a plain sshd host, where the relative form is resolved
        # under the login's home: two distinct repositories at `~login/srv/repo`
        # and `/srv/repo`, both used against one store, share an id. That is
        # the class the userinfo strip already accepts (`alice@host:repo` and
        # `bob@host:repo` share one), and nothing in the URL says which kind
        # of server it is.
        #
        # Exactly ONE leading slash is removed, mirroring the URL branch's
        # `partition("/")`: a doubled separator still declines in BOTH
        # spellings (`git@host://a/b` and `https://host//a/b`), which stops a
        # typo from silently naming a different path.
        scp_path = scp_match.group("path")
        if scp_path.startswith("/"):
            scp_path = scp_path[1:]
        path = _strip_path_suffixes(scp_path)
        segments = _split_path(path)
        if not segments:
            return None
        # ONE SEGMENT IS ENOUGH. `git@host:repo` and `https://host/repo` name
        # the same endpoint, and the grammar allows a single segment;
        # declining the scp spelling while accepting the URL spelling would
        # split one repository into two ids.
        identifier = _identifier(host, "", segments)

    if len(identifier) > _max_id_length():
        return None

    # The grammar's single authority. If `database.is_canonical_id` is ever
    # stricter than the construction above, the answer is a DECLINE, never a
    # silently different id.
    if not is_canonical(identifier):
        return None
    return identifier


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


def classify_remote_origin(url: Optional[str]) -> "tuple[str, Optional[str]]":
    """Classify an origin value into one of the three ruled outcomes.

    Returns `(outcome, project_id)` where outcome is OUTCOME_CANONICAL,
    OUTCOME_NO_PORTABLE_ORIGIN or OUTCOME_DECLINE.

    - canonical: a network remote that parses under the canonical grammar.
    - no_portable_origin: no origin, an empty value, a local path or
      `file://`. The caller falls through to the basename.
    - decline: a NETWORK remote that was present but cannot be a canonical id
      (a non-ASCII path, a bad port, a reserved character, a backslash, more
      than one `@`, a trailing space, or an unsupported scheme). No id is
      produced and no basename is used; the override is the remedy.

    This exists because a single Optional cannot carry the distinction, and
    because the grammar only judges a FINISHED string — it cannot see whether a remote
    was present at all.
    """
    if not url or not url.strip():
        return OUTCOME_NO_PORTABLE_ORIGIN, None
    identifier = normalize_remote_url(url)
    if identifier is not None:
        return OUTCOME_CANONICAL, identifier
    if _is_network_shaped(url):
        return OUTCOME_DECLINE, None
    return OUTCOME_NO_PORTABLE_ORIGIN, None


def read_origin(root: Path) -> OriginRead:
    """Read this repository's `origin` URL, as a tri-state.

    THE IDENTITY RULE: the origin is the FIRST `remote.origin.url` in the
    repository's own config (`.git/config` plus the files it includes),
    which is the url git fetches from; later values are push urls. An empty
    value clears the urls before it (git 2.46 semantics), so only the values
    after the last empty one count, and none left is ABSENT.

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
    localised): exit 1 with an empty answer is "no such key" = ABSENT; any
    other non-zero status is "git could not answer" = UNAVAILABLE. A missing
    git, a timeout and an unreadable configuration are all UNAVAILABLE.

    CONTRACT: call this only for a root ALREADY ESTABLISHED as a repository
    (its common dir resolved). Outside one, `--local` exits 128, which reads
    as UNAVAILABLE. `detect_id_for_root` therefore gates on `common_dir`
    rather than using this call to discover repo-ness.
    """
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            [
                "git", "-C", str(root), "config", "--local", "--includes",
                "-z", "--get-all", "remote.origin.url",
            ],
            capture_output=True,
            text=False,
            timeout=5,
            # Inline, so the resolver git-env guard can check the shape: a
            # pure key filter over the location-free environment.
            env={
                key: value
                for key, value in git_env_without_location().items()
                if key != "GIT_CONFIG"
            },
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return OriginRead(ORIGIN_UNAVAILABLE, None)
    if result.returncode == 1 and not result.stdout:
        # `git config --get-all` exits 1 when the key is not set.
        return OriginRead(ORIGIN_ABSENT, None)
    if result.returncode != 0:
        return OriginRead(ORIGIN_UNAVAILABLE, None)
    # Every value is NUL-terminated, so the last piece of the split is empty.
    values = result.stdout.split(b"\0")[:-1]
    if b"" in values:
        values = values[len(values) - values[::-1].index(b""):]
    if not values or not values[0].strip():
        return OriginRead(ORIGIN_ABSENT, None)
    # os.fsdecode, never locale decoding: a URL may hold bytes invalid in
    # the locale, and UnicodeDecodeError must not escape this reader.
    return OriginRead(ORIGIN_PRESENT, os.fsdecode(values[0]))


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
        with path.open("rb") as handle:
            # BOUNDED READ: one byte past the cap is enough to classify the
            # file as too long, so an accidentally huge or hostile file is
            # never allocated whole.
            raw = handle.read(_MAX_OVERRIDE_BYTES + 1)
    except FileNotFoundError:
        return OverrideRead(None, None, None)
    except OSError:
        return OverrideRead(None, path, "unreadable")
    if len(raw) > _MAX_OVERRIDE_BYTES:
        return OverrideRead(None, path, "too_long")
    try:
        # `utf-8-sig` drops an encoding BOM. `validate_identifier_for_write`
        # does not reject U+FEFF (it is not a control character), so a decoded
        # BOM would otherwise become an invisible first character of the id.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return OverrideRead(None, path, "not_utf8")
    _, validate_identifier_for_write = _server_identity_impl()
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        if len(candidate) > _max_id_length():
            return OverrideRead(None, path, "too_long")
        try:
            validate_identifier_for_write(candidate)
        except ValueError:
            return OverrideRead(None, path, "control_chars")
        if not is_canonical(candidate):
            return OverrideRead(None, path, "not_canonical")
        return OverrideRead(candidate, path, None)
    return OverrideRead(None, path, "empty")


def read_user_setting(config_path: Path) -> Optional[str]:
    """The `project_id` from `<memory_dir>/config.json`, or None.

    The user-scope fallback for a session with no repository at all. Applied
    by the caller ONLY when no repository resolves, so a repo-scoped override
    always wins. Never raises.
    """
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
    if path is None:
        return False
    try:
        parts = Path(path).resolve().parts
    except (OSError, RuntimeError, ValueError):
        parts = Path(path).parts
    for index, component in enumerate(parts):
        if component != ".git":
            continue
        following = parts[index + 1:]
        if following[:1] == ("modules",):
            return True
        if following[:1] == ("worktrees",) and following[2:3] == ("modules",):
            return True
    return False


def _resolved_git_dir(asked_root: Path) -> Optional[Path]:
    """The resolved `--git-dir` for `asked_root`, or None.

    Used only by the submodule conjunction below, so it runs on the rare
    admin-dir-shaped repository rather than on every detection. A relative
    answer is resolved against the directory the command ran in, as git
    reports it.
    """
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            ["git", "-C", str(asked_root), "rev-parse", "--git-dir"],
            capture_output=True,
            text=False,
            timeout=5,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    git_dir = Path(os.fsdecode(result.stdout).strip())
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

    When the conjunction cannot be established the answer is False: the
    ordinary basename ruling continues.
    """
    if root is None or common_dir is None:
        return False
    if not _submodule_admin_dir(common_dir):
        return False
    try:
        if not (Path(root) / ".git").is_file():
            return False
    except (OSError, RuntimeError, ValueError):
        return False
    asked = Path(asked_root) if asked_root is not None else Path(root)
    git_dir = _resolved_git_dir(asked)
    if git_dir is None:
        return False
    try:
        common = Path(common_dir).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return git_dir == common or git_dir.parent == common / "worktrees"


def filesystem_case_insensitive(root: Optional[Path]) -> bool:
    """Whether the filesystem holding `root` PROVABLY folds case.

    The legacy-bucket fold exists for one situation: the bucket was named on
    a filesystem that cannot tell `foreignproj` from `ForeignProj`, so the
    two spellings name one directory. On a case-SENSITIVE filesystem they
    name two projects, and folding merges them.

    The probe toggles the case of the root's own basename and asks whether
    the toggled path is the SAME file (`os.path.samefile`), which answers the
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
        return os.path.samefile(path, path.parent / toggled)
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


def _admin_dir_names_another_checkout(admin_dir: Path, asked_root: Path) -> bool:
    """Whether a SUBMODULE admin dir records a checkout other than `asked_root`.

    A `.git` FILE pointing at `<other>/.git/modules/<name>` makes git report
    that submodule's git dir and common dir, which are EQUAL — so the
    linked-worktree listing check cannot see the redirect, and the target's
    basename is `<name>`, not `.git`, so the direct predicate cannot either.
    A genuine submodule's admin dir records where its checkout actually is in
    `core.worktree`, relative to the admin dir; a forged pointer names a
    directory that is not that checkout.

    MEASURED: a genuine submodule's `core.worktree` is `../../../submod`,
    resolving to the submodule checkout the caller is standing in; a forged
    pointer's resolves to somebody else's checkout. A BARE OR UNSET
    `core.worktree` is UNPROVABLE and answers True (decline): `git submodule
    deinit` leaves exactly that state while the submodule's origin survives,
    so treating it as innocent bound another repository's id.
    """
    try:
        from shared.project_scope import git_env_without_location

        result = subprocess.run(
            ["git", "--git-dir", str(admin_dir), "config", "--get", "core.worktree"],
            capture_output=True,
            text=False,
            timeout=5,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        # UNPROVABLE, so FAIL CLOSED. This function is reached only for the
        # `<...>/.git/modules/<name>` shape (a submodule admin dir), and git
        # being unable to answer there says nothing about whether the pointer
        # is genuine. A live submodule always records `core.worktree`, so
        # nothing legitimate is refused by answering True here.
        return True
    if result.returncode != 0 or not result.stdout.strip():
        # AN UNSET `core.worktree` IS UNPROVABLE, NOT PROVEN-INNOCENT, and the
        # difference was a blocking defect: `git submodule deinit` UNSETS
        # `core.worktree` while the submodule's origin SURVIVES, so a forged
        # `.git` file naming that admin dir bound another repository's
        # canonical id for reads AND writes. A live submodule always records
        # it (measured: `../../../../checks/ForeignProj`), so failing closed
        # here refuses only the deinit-ed state that cannot be checked.
        return True
    recorded = Path(os.fsdecode(result.stdout).strip())
    if not recorded.is_absolute():
        recorded = admin_dir / recorded
    try:
        recorded = recorded.resolve()
        asked = Path(asked_root).resolve()
    except (OSError, RuntimeError, ValueError):
        return True
    try:
        # SAME INODE, NOT THE SAME SPELLING: on a case-insensitive filesystem
        # a genuinely recorded `core.worktree` can differ from the checkout
        # path only by case. `samefile` settles that without folding the
        # comparison, which would be wrong on a case-sensitive filesystem.
        return not os.path.samefile(recorded, asked)
    except OSError:
        # A recorded path that does not exist cannot be this checkout's
        # worktree, and the question is unprovable: fail closed.
        return True


def _gitfile_points_at_foreign_gitdir(root: Path) -> bool:
    """Whether `<root>/.git` is a FILE redirecting at another repo's git dir.

    A genuine submodule points at `<super>/.git/modules/<name>`, a genuine
    linked worktree at `<main>/.git/worktrees/<name>`, and a genuine
    `--separate-git-dir` at an arbitrary path. The ONE shape whose target is
    a directory named exactly `.git` is a checkout redirecting at another
    repository's git dir: git then reports that repository's git dir, common
    dir and origin as if the asking directory were its checkout, so a write
    would file into a project nobody named.

    Read straight from the file rather than through git: a comparison of git
    answers cannot see this (every answer agrees), and the check must run
    BEFORE the override and origin reads, because both would otherwise be
    taken from the foreign repository.

    MEASURED EXCEPTION, and the reason the caller suppresses only the ORIGIN:
    a legitimate `git init --separate-git-dir=<dir>/.git` names its store
    directory literally `.git`, so it takes this same shape. It therefore
    cannot derive an id from its own remote, but it is RECOVERABLE — the
    override is read first, so one pin at `<common-dir>/pact-project-id`
    restores it. A forgery can likewise bind a pin, but only one the victim
    deliberately wrote, never its remote.
    """
    # THE MARKER IS FOUND BY WALKING UP, AS GIT DOES (`find_git_marker`).
    # The caller may hand us a SUBDIRECTORY of the redirected checkout (a
    # declared path one level down is the common shape), and `<subdir>/.git`
    # does not exist — so testing only the immediate directory leaves the
    # subdirectory case open. The `core.worktree` comparison below is
    # against the MARKER'S directory (the checkout root), never the raw
    # input, or a legitimate subdirectory of a genuine submodule would be
    # refused.
    marker = find_git_marker(root)
    if marker is None:
        return False
    try:
        if not marker.is_file():
            # A real `.git` DIRECTORY: an ordinary checkout.
            return False
    except OSError:
        return False
    marker_root = marker.parent

    try:
        text = (marker_root / ".git").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        if not line.startswith("gitdir:"):
            continue
        target = line.split(":", 1)[1].strip()
        if not target:
            return False
        resolved = Path(target)
        if not resolved.is_absolute():
            resolved = marker_root / resolved
        try:
            resolved = resolved.resolve()
        except (OSError, RuntimeError):
            return False
        if resolved.name == ".git":
            return True
        # A submodule admin dir at any depth (`_submodule_admin_dir`): the
        # submodule-admin redirect, whose git dir EQUALS its common dir, so
        # only the recorded checkout can tell a genuine submodule from a
        # pointer at somebody else's.
        #
        # THE SHAPE IS CHECKED FIRST, so a linked-worktree marker
        # (`.../.git/worktrees/<name>`) never reaches the git subprocess —
        # that call is unaffordable on every detection.
        #
        # A LINKED-WORKTREE ADMIN DIR IS EXCLUDED BY GIT'S OWN MARK, its
        # `commondir` file, which a submodule admin dir never holds. A
        # worktree OF a submodule has its admin dir at
        # `<super>/.git/modules/<name>/worktrees/<wt>`, which has the
        # submodule shape, but its `core.worktree` is read from the shared
        # submodule config and names the submodule's MAIN checkout, so the
        # comparison below would refuse the genuine worktree. A name test
        # cannot replace the mark: a submodule may itself be named
        # `x/worktrees/y`.
        if _submodule_admin_dir(resolved) and not os.path.exists(
            resolved / "commondir"
        ):
            return _admin_dir_names_another_checkout(resolved, marker_root)
        return False
    return False


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
    - the origin is PRESENT and canonicalises -> the canonical id;
    - the origin is PRESENT and cannot be represented -> DECLINE;
    - the origin is ABSENT (none configured, `file://`, a local path) ->
      the basename, the designed non-git form, EXCEPT in a submodule: there
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
        return RootIdentity(override.value, "canonical", "override file", None)
    if override.reason is not None and override.path is not None:
        override_ignored = {"path": str(override.path), "reason": override.reason}

    # A REDIRECTED CHECKOUT SUPPRESSES THE ORIGIN READ, AND ONLY THAT.
    # The override above still answers first, deliberately: a legitimate
    # `--separate-git-dir=<dir>/.git` checkout takes this same shape, and
    # refusing before the pin would make it permanently unusable. What is
    # refused is the ORIGIN, which belongs to the other repository — so a
    # forgery can at most bind a pin the victim deliberately wrote, which is
    # strictly narrower than binding its remote. See
    # `_gitfile_points_at_foreign_gitdir`.
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
    if _gitfile_points_at_foreign_gitdir(asked):
        # THE REASON NAMES THE REAL CAUSE. Without it the rung's generic
        # "origin could not be read or represented" warning sends the
        # operator to inspect a remote when the `.git` marker is the thing
        # that redirected the answer.
        return RootIdentity(
            None,
            None,
            "declined",
            override_ignored,
            decline_record("gitfile_redirect", None),
        )

    # A SUBMODULE'S CHECKOUT PATH IS REPOSITORY-CHOSEN, so a basename derived
    # from it must never bind. A canonical origin or the override above still
    # answers; only the basename fallback is refused. The test is the THREE-LEG
    # conjunction (admin-dir path shape, git-dir == common-dir, a `.git` FILE
    # at the identity root), so an ordinary local repository, a linked
    # worktree and a `--separate-git-dir` checkout each fail a different leg
    # and keep basenaming by ruling.
    submodule_controlled = _submodule_checkout(root, common_dir, asked_root)

    origin = read_origin(root)
    if origin.state == ORIGIN_UNAVAILABLE:
        return RootIdentity(
            None,
            None,
            "declined",
            override_ignored,
            decline_record("origin_unreadable", override_path),
        )
    if origin.state == ORIGIN_ABSENT:
        if submodule_controlled:
            return _refuse_submodule_basename(root, common_dir, override_ignored)
        return RootIdentity(name, "basename", "basename", override_ignored)

    outcome, identifier = classify_remote_origin(origin.value)
    if outcome == OUTCOME_CANONICAL:
        return RootIdentity(identifier, "canonical", "origin remote", override_ignored)
    if outcome == OUTCOME_DECLINE:
        return RootIdentity(
            None,
            None,
            "declined",
            override_ignored,
            decline_record("origin_unrepresentable", override_path),
        )
    if submodule_controlled:
        return _refuse_submodule_basename(root, common_dir, override_ignored)
    return RootIdentity(name, "basename", "basename", override_ignored)