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

`database.py` owns the canonical-id grammar (R1, `is_canonical_id`) and the
write-ingress rule (R2, `validate_identifier_for_write`); this module consumes
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
from urllib.parse import unquote

# Accepted remote schemes. The same set `hooks/shared/backlog.py` already
# ships, because two parsers in one repo that disagree about which schemes are
# remotes would be the defect, not the fix. `file` and every other scheme is
# handled as its own outcome rather than being silently accepted.
_ACCEPTED_SCHEMES = frozenset({"https", "http", "ssh", "git", "git+ssh"})

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
}

# `scheme://` — the scheme is matched here and validated against the accepted
# set afterwards, so an unsupported scheme can be classified rather than
# silently treated as a local path.
_SCHEME_PREFIX_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://")

# scp-style `user@host:path`. The user is REQUIRED and non-empty, matching
# `backlog.py`'s regex; a bare `host:path` is a Windows drive-letter form, not
# scp, and is deliberately not parsed here. The colon is the PATH SEPARATOR in
# this form and is never a port, so `git@host:2222/owner/repo` has path
# `2222/owner/repo`.
_SCP_FORM_RE = re.compile(r"^[^@\s/:\\]+@(?P<host>[^:/\s\\]+):(?P<path>.+)$")

_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")
_PATH_BANNED_CHARS = frozenset("@?#%")

# A hard cap on the override file we are willing to read. The value itself is
# capped at the canonical length, so anything larger is unusable by definition.
_MAX_OVERRIDE_BYTES = 4096

_OVERRIDE_FILENAME = "pact-project-id"


class OverrideRead(NamedTuple):
    """One read of `<common_dir>/pact-project-id`.

    `value` is the usable first line, or None. `path` is the file consulted
    (None when it is absent). `reason` is a fixed token naming why a PRESENT
    file was unusable — "not_regular", "not_utf8", "empty", "control_chars",
    "too_long" or "unreadable". Absence is `value=None, path=None`, never a
    reason. "not_regular" covers a FIFO, a device node and a SYMLINK: none is
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
    """

    project_id: Optional[str]
    id_shape: Optional[str]
    source: str
    override_ignored: Optional[dict]


# The three outcomes the classifier distinguishes. Collapsing any two is the
# likeliest way to write a wrong pin: the basename forms become declines, or
# the decline forms become basenames.
OUTCOME_CANONICAL = "canonical"
OUTCOME_NO_PORTABLE_ORIGIN = "no_portable_origin"
OUTCOME_DECLINE = "decline"

ORIGIN_PRESENT = "present"
ORIGIN_ABSENT = "absent"
ORIGIN_UNAVAILABLE = "unavailable"


def _server_identity_impl() -> tuple:
    """The two grammar predicates owned by database.py (see module docstring).

    Imported at call time because the memory scripts are loaded both as a
    package (`scripts.memory_api`) and by direct `sys.path` insertion, and
    because `database.py` must not import this module.
    """
    from .database import is_canonical_id, validate_identifier_for_write

    return is_canonical_id, validate_identifier_for_write


def _max_id_length() -> int:
    """The identifier length cap, from database.py (ONE definition).

    Imported at call time for the same reason `_server_identity_impl` is: the
    memory scripts load both as a package and by direct `sys.path` insertion,
    so a module-level relative import would break the second form.
    """
    from .database import CANONICAL_ID_MAX_LENGTH

    return CANONICAL_ID_MAX_LENGTH


def is_canonical(value: object) -> bool:
    """Whether `value` is a canonical repository id (R1).

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
    """
    if not port.isdigit() or str(int(port)) != port:
        return False
    return 1 <= int(port) <= 65535


def _valid_segment(segment: str) -> bool:
    """Whether one decoded path segment may appear in an id.

    Banned: the empty segment, `.` and `..` (path traversal in an id that
    reaches SQL and log lines), the reserved characters, whitespace and
    anything outside printable ASCII. Non-ASCII DECLINES rather than being
    transliterated, because an NFC/NFD rewrite makes two distinct paths
    collide. `/` is checked on the DECODED value so `%2F` cannot manufacture
    a separator after the split has already happened.
    """
    if segment in ("", ".", ".."):
        return False
    for char in segment:
        if char == "/" or char in _PATH_BANNED_CHARS or char.isspace():
            return False
        if not ("\x21" <= char <= "\x7e"):
            return False
    return True


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


def _decode_path(path: str) -> Optional[list]:
    """Percent-decode a path into validated segments, or None.

    Decoding happens PER SEGMENT, after splitting: decoding first would let
    `%2F` manufacture a separator, so `owner/re%2Fpo` would silently become a
    three-segment path rather than being rejected.
    """
    segments = []
    for raw_segment in path.split("/"):
        try:
            segment = unquote(raw_segment, errors="strict")
        except UnicodeDecodeError:
            return None
        if not _valid_segment(segment):
            return None
        segments.append(segment)
    return segments


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
        segments = _decode_path(path)
        if not segments:
            return None
        identifier = (
            f"{host}:{port}/{'/'.join(segments)}"
            if port
            else f"{host}/{'/'.join(segments)}"
        )
    else:
        scp_match = _SCP_FORM_RE.match(value)
        if scp_match is None:
            # Neither a URL nor scp syntax: indistinguishable from a relative
            # local path, which is NO PORTABLE ORIGIN.
            return None
        host = scp_match.group("host").lower()
        if not _valid_host(host):
            return None
        path = _strip_path_suffixes(scp_match.group("path"))
        segments = _decode_path(path)
        if not segments:
            return None
        # ONE SEGMENT IS ENOUGH. `git@host:repo` and `https://host/repo` name
        # the same endpoint, and R1 allows a single segment; declining the
        # scp spelling while accepting the URL spelling would split one
        # repository into two ids. (The PREPARE corpus's U45 row said decline;
        # that row is superseded here.)
        identifier = f"{host}/{'/'.join(segments)}"

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
    downgraded to a folder name. That ruling supersedes the PREPARE corpus's
    U58 (`ftp://` as basename).

    A scheme-less value whose PRE-SLASH prefix holds BOTH `@` and `:` is
    ALSO network-shaped. git's own scp heuristic reads the first `:` as the
    host/path separator, so `deploy-token:<token>@host/owner/repo` names a
    network host to git even though our stricter scp form rejects it (its
    user class excludes `:`). Downgrading that to a folder basename is the
    silent mis-scope this change exists to remove, so it DECLINES and names
    the override remedy instead. A user-less `host:path` and a drive path
    keep the ruled no-portable-origin narrowing.
    """
    if not isinstance(url, str):
        return False
    # A leading BOM or leading WHITESPACE is a present network remote that
    # cannot be represented. Both are stripped for CLASSIFICATION only: the
    # canonicaliser refuses the value outright, so this decides
    # decline-versus-basename, never acceptance.
    value = _strip_one_terminator(url).lstrip("\ufeff").lstrip()
    if "\\" in value or value.count("@") > 1:
        return "://" in value or _SCP_FORM_RE.match(value) is not None
    scheme_match = _SCHEME_PREFIX_RE.match(value)
    if scheme_match is not None:
        return scheme_match.group("scheme").lower() not in _LOCAL_SCHEMES
    if _SCP_FORM_RE.match(value) is not None:
        return True
    first_slash = value.find("/")
    prefix = value if first_slash == -1 else value[:first_slash]
    return "@" in prefix and ":" in prefix


def classify_remote_origin(url: Optional[str]) -> "tuple[str, Optional[str]]":
    """Classify an origin value into one of the three ruled outcomes.

    Returns `(outcome, project_id)` where outcome is OUTCOME_CANONICAL,
    OUTCOME_NO_PORTABLE_ORIGIN or OUTCOME_DECLINE.

    - canonical: a network remote that parses under R1.
    - no_portable_origin: no origin, an empty value, a local path or
      `file://`. The caller falls through to the basename.
    - decline: a NETWORK remote that was present but cannot be a canonical id
      (a non-ASCII path, a bad port, a reserved character, a backslash, more
      than one `@`, a trailing space, or an unsupported scheme). No id is
      produced and no basename is used; the override is the remedy.

    This exists because a single Optional cannot carry the distinction, and
    because R1 only judges a FINISHED string — it cannot see whether a remote
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

    `git config --get remote.origin.url` and NOT `git remote get-url`: the
    latter applies the machine-local `url.*.insteadOf` rewrite table, so a
    local rewrite rule would silently change which repository this process
    believes it is in.

    The value is returned RAW (un-normalized, terminator included) because
    normalization is `normalize_remote_url`'s job and it must see the trailing
    whitespace it is required to reject. Never logs the value: it can carry a
    token (`x-access-token:<token>@` is routine in CI).

    Exit status is the whole distinction, never stderr text (git messages are
    localised): exit 1 with an empty answer is "no such key" = ABSENT; any
    other non-zero status is "git could not answer" = UNAVAILABLE. A missing
    git, a timeout and an unreadable configuration are all UNAVAILABLE.

    CONTRACT: call this only for a root ALREADY ESTABLISHED as a repository
    (its common dir resolved). `git config --get` does not require a
    repository — outside one it reads the global config and exits 1, which is
    indistinguishable from a repository with no origin. `detect_id_for_root`
    therefore gates on `common_dir` rather than using this call to discover
    repo-ness.
    """
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=False,
            timeout=5,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return OriginRead(ORIGIN_UNAVAILABLE, None)
    if result.returncode == 0:
        if not result.stdout.strip():
            return OriginRead(ORIGIN_ABSENT, None)
        # os.fsdecode, never locale decoding: a URL may hold bytes invalid in
        # the locale, and UnicodeDecodeError must not escape this reader.
        return OriginRead(ORIGIN_PRESENT, os.fsdecode(result.stdout))
    if result.returncode == 1 and not result.stdout.strip():
        # `git config --get` exits 1 when the key is not set.
        return OriginRead(ORIGIN_ABSENT, None)
    return OriginRead(ORIGIN_UNAVAILABLE, None)


def read_override(common_dir: Path) -> OverrideRead:
    """Read `<common_dir>/pact-project-id`.

    The common dir is resolved by the caller with `rev-parse
    --git-common-dir`; this function never shells to git. The location is
    deliberate: a working-tree `.pact/project-id` arrives with a clone, so it
    would let a repository redirect the memory scope of whoever checks it
    out.

    THE COMMON-DIR PLACEMENT IS NARROWER, NOT UNREACHABLE, AND THE INFLUENCE
    IS NOT CONFINED TO THIS PROJECT. An archive that ships a `.git/`
    directory can carry this file, exactly as it can already carry
    `.git/config`. What it can name is ANY bucket in the same user's store:
    another project's canonical id, another project's legacy basename, or
    this project's own. A plain `.git` FILE in a checkout can likewise carry
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
        # `utf-8-sig` drops an encoding BOM. R2 does not reject U+FEFF (it is
        # not a control character), so a decoded BOM would otherwise become an
        # invisible first character of the id.
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
    dotgit = Path(root) / ".git"
    try:
        if not dotgit.is_file():
            return False
        text = dotgit.read_text(encoding="utf-8", errors="replace")
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
            resolved = Path(root) / resolved
        try:
            resolved = resolved.resolve()
        except (OSError, RuntimeError):
            return False
        return resolved.name == ".git"
    return False


def detect_id_for_root(
    root: Optional[Path], common_dir: Optional[Path] = None
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
      the basename, the designed non-git form;
    - the origin is UNAVAILABLE (git cannot answer, unreadable config) ->
      DECLINE. This is the #1699 rule: a broken repository must not fall back
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
        return RootIdentity(
            override.value,
            "canonical" if is_canonical(override.value) else "basename",
            "override file",
            None,
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
    # `_gitfile_points_at_foreign_gitdir`.
    if _gitfile_points_at_foreign_gitdir(Path(root)):
        return RootIdentity(None, None, "declined", override_ignored)

    origin = read_origin(root)
    if origin.state == ORIGIN_UNAVAILABLE:
        return RootIdentity(None, None, "declined", override_ignored)
    if origin.state == ORIGIN_ABSENT:
        return RootIdentity(name, "basename", "basename", override_ignored)

    outcome, identifier = classify_remote_origin(origin.value)
    if outcome == OUTCOME_CANONICAL:
        return RootIdentity(identifier, "canonical", "origin remote", override_ignored)
    if outcome == OUTCOME_DECLINE:
        return RootIdentity(None, None, "declined", override_ignored)
    return RootIdentity(name, "basename", "basename", override_ignored)