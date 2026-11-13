"""
PACT Memory API

Location: pact-plugin/skills/pact-memory/scripts/memory_api.py

High-level API for the PACT Memory skill providing a clean interface
for saving, searching, and managing memories.

This is the primary entry point for agents and hooks to interact
with the memory system.

Used by:
- SKILL.md: Documents API usage for skill invocation
- Agents: Direct memory operations during PACT phases

Note: Memory initialization is lazy-loaded on first use via memory_init.py,
eliminating startup cost for non-memory users.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import os
import shlex
import stat
import struct
import subprocess
import threading
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional, cast

# Use the same sqlite3 module as database.py for type consistency
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .config import (
    DERIVED_STORE_ORIGINS,
    get_user_memory_dir,
    read_only_scope,
    resolve_db_path,
    store_path_origin,
    store_scope,
)
from .database import (
    db_connection,
    create_memory,
    get_memory,
    update_memory,
    delete_memory,
    list_memories,
    ensure_initialized,
    resolve_memory_id_prefix,
    _unusable_identifier,
    MEMORY_ID_LENGTH,
    SQLITE_EXTENSIONS_ENABLED,
    validate_identifier_for_write,
)
from .embeddings import (
    generate_embedding,
    generate_embedding_text
)
from .graph import (
    count_project_file_paths,
    link_memory_to_paths,
    track_file,
    get_files_for_memory
)
from .models import MemoryObject, memory_from_db_row
from . import project_identity
from .project_identity import (
    _submodule_admin_dir,
    decline_record,
    detect_id_for_root,
    find_git_marker,
    is_canonical,
    read_user_setting,
)
if TYPE_CHECKING:
    # ANNOTATION-ONLY. `RootIdentity` appears solely in the string
    # annotations of `_disclosed_source` and `_warn_override_ignored`, which
    # an AST usage walk cannot see; the repo's import-hygiene sweep has an
    # explicit carve-out for this block, so this is the intended shape rather
    # than a noqa.
    from .project_identity import RootIdentity
from .search import (
    graph_enhanced_search,
    search_by_file,
    get_search_capabilities
)
from .working_memory import (
    MAX_WORKING_MEMORIES,
    AmbientSyncRefused,
    SyncResult,
    project_memories_to_claude_md,
    sync_to_claude_md,
    sync_retrieved_to_claude_md,
)
from .memory_init import ensure_memory_ready, get_embedding_catchup_status
# Dual import: relative (when loaded as package) vs absolute (when tests add scripts/ to sys.path)
try:
    from .pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_session_id_from_context_file,
        get_worktree_identity_from_session_record,
    )
except ImportError:
    from pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_session_id_from_context_file,
        get_worktree_identity_from_session_record,
    )

# Configure logging
logger = logging.getLogger(__name__)

# Fields whose changes should trigger embedding regeneration.
# Kept as a module-level constant so tests can import and verify it.
CONTENT_FIELDS = {
    "context", "goal", "lessons_learned", "decisions", "entities",
    "reasoning_chains", "agreements_reached", "disagreements_resolved",
}


class _DetectedIdentity(NamedTuple):
    """One detection pass's full result.

    `legacy_basename` is what the PRE-change resolver produced for this
    location — the id existing stored rows were keyed by. It is carried from
    this same pass rather than re-derived: re-deriving it from the canonical
    id yields the REMOTE's repo name, not the folder name, and the two differ
    whenever a checkout folder is named differently from its repository. The
    read-scope union uses this value to find the legacy bucket, so a drifted
    derivation would silently stop finding it.

    `override_ignored` is a structured {path, reason} when an override file
    was read and found unusable — a stderr warning alone is invisible to a
    caller that parses the envelope. It is None, with nothing read, when a
    rung declines before the identity read (a repository with no working
    tree, a `.git` naming a linked worktree's admin dir that does not link
    back, a broken `.git` marker, a declaration that cannot be resolved) and
    when the root is the user's home directory. `path` is None (the reason is
    kept) when
    the checkout's `.git` is a redirect, or points at a git directory outside
    the checkout that is neither a linked worktree's nor a submodule's admin
    dir.
    """

    project_id: Optional[str]
    source: str
    legacy_basename: Optional[str]
    override_ignored: Optional[dict]
    # THE ROOT THE WINNING RUNG RESOLVED. The plausibility gate compares a
    # legacy bucket's absolute file rows against THIS, not against the
    # process's cwd repository: in a declared-repo session the two differ, and
    # gating on the cwd would exclude the declared repo's own legacy bucket.
    identity_root: Optional[Path]
    # STRUCTURED DECLINE DISCLOSURE — `{source, reason, override_path}`, built
    # by `project_identity.decline_record`, when a rung declined for a named
    # cause, else None. `project-id show` reports it; a warning alone is
    # invisible to a caller that parses the envelope.
    decline_reason: Optional[dict] = None
    # A FIXED sentence naming a narrower cause than the reason token
    # (`RootIdentity.detail`), printed once in the warning and the refusal.
    decline_detail: Optional[str] = None
    # WHAT THE PRE-CHANGE RESOLVER PRODUCED for this exact input, or None
    # where it refused. The backlog file name reads only this.
    pre_change_name: Optional[str] = None


# `_read_project_scopes` result cache: (store, mtime_ns, size, canonical,
# legacy) -> scope tuple. Keying on the store's stat is what makes a rekey
# performed by ANOTHER process observable instead of cached forever.
_LEGACY_SCOPE_CACHE: Dict[tuple, tuple] = {}

# Exclusion notices already emitted, keyed by (store, candidate). A warning
# repeated on every read is a warning readers learn to ignore.
_EXCLUSION_WARNED: set = set()

# Case-variant notices already emitted, keyed by (store, variants). The fold
# suppression is silent loss unless it is reported; reported once per process
# for the same reason as the exclusion notice.
_CASE_VARIANT_WARNED: set = set()

# Submodule legacy-suppression notices already emitted, keyed by admin dir.
# Detection runs on every memory operation, so this disclosure is once per
# process for the same reason `_EXCLUSION_WARNED` is.
_SUBMODULE_SUPPRESSED: set = set()
# Unproven legacy buckets already disclosed in this process, (store, bucket).
_UNPROVEN_WARNED: set = set()


LEGACY_SCOPE_KEYS = frozenset({
    "candidate", "memory_count", "file_paths", "rekey", "included",
    "verified", "included_unverified", "excluded", "unproven",
    "session_evidence", "ambiguous", "canonical_count", "detail",
    "case_variant", "case_variant_rekey", "suppressed",
    "suppressed_candidate", "degraded",
})


def _legacy_scope_record(**overrides: Any) -> Dict[str, Any]:
    """THE ONE BUILDER for `legacy_scope`: every key, in every state.

    A consumer reads a fixed key set, so a branch that forgets a key cannot
    turn "absent" into a second meaning of "false". Exactly one of
    `included`, `excluded` and `unproven` is true when `candidate` is set;
    all three are false when it is not.
    """
    unknown = set(overrides) - LEGACY_SCOPE_KEYS
    if unknown:
        raise ValueError(f"unknown legacy_scope keys: {sorted(unknown)}")
    record: Dict[str, Any] = {
        "candidate": None,
        "memory_count": 0,
        "file_paths": None,
        "rekey": None,
        "included": False,
        "verified": False,
        "included_unverified": False,
        "excluded": False,
        "unproven": False,
        "session_evidence": None,
        "ambiguous": [],
        "canonical_count": None,
        "detail": None,
        "case_variant": [],
        "case_variant_rekey": [],
        "suppressed": None,
        "suppressed_candidate": None,
        "degraded": None,
    }
    record.update(overrides)
    return record


def _suppressed_bucket_rows(store: str, bucket: Optional[str]) -> int:
    """Rows in a SUPPRESSED legacy bucket, read read-only; disclosed if any.

    The suppression must never be silent when it hides rows: an operator
    whose pre-upgrade memories sit in that bucket would otherwise see them
    disappear with no notice. It is silent when the bucket is empty, because
    a notice with nothing behind it is one readers learn to skip. Once per
    process per (store, bucket). The bucket is never read beyond its count.
    """
    if not bucket:
        return 0
    count = read_project_bucket_census(store).get(bucket, 0)
    key = (store, bucket)
    if count and key not in _SUBMODULE_SUPPRESSED:
        _SUBMODULE_SUPPRESSED.add(key)
        logger.warning(
            "this repository is a submodule, so the pre-upgrade project "
            "bucket %r derived from its admin directory (%d memories) is NOT "
            "in this project's read scope: a superproject chooses that name "
            "through its tracked .gitmodules, so reading it would let a "
            "repository read another project's memories. If those rows belong "
            "to this checkout, list them with `project-id show` and move them "
            "with `project-id rekey`.",
            bucket,
            count,
        )
    return count


# The prune ledger is read from its end, at most this many bytes. A line is
# about 200 bytes, so this covers roughly the last 5,000 pruned sessions of
# one project; an older pruned session reads as no evidence (unproven), the
# fail-closed direction, and the unproven notice prints its remedy.
_PRUNE_LEDGER_TAIL_BYTES = 1024 * 1024


def _description_predates(common_dir: Optional[Path], started_at: Any) -> bool:
    """Whether `<common_dir>/description` was created before `started_at`.

    `started_at` must be an ISO-8601 timestamp that carries its zone; a naive
    or unparseable one fails closed. The description must be a regular file
    by `lstat` (a symlink to an old file is refused), and its ctime must be
    STRICTLY earlier than the session's start. A copied, restored or
    re-cloned repository has a new description, so its old sessions fail.
    Never raises.
    """
    if common_dir is None or not isinstance(started_at, str) or not started_at:
        return False
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            return False
        described = os.lstat(Path(common_dir) / "description")
        return (
            stat.S_ISREG(described.st_mode)
            and described.st_ctime < started.timestamp()
        )
    except Exception:
        return False


def _session_record_counts(
    record: Any, roots: set, common_dir: Optional[Path]
) -> bool:
    """Whether one recorded session (a live context or a ledger line) counts.

    It ran in a gate root (its `project_dir` resolves to one) and started
    after this repository's description was created. Never raises.
    """
    try:
        project_dir = record.get("project_dir")
        return (
            isinstance(project_dir, str)
            and bool(project_dir)
            and os.path.realpath(project_dir) in roots
            and _description_predates(common_dir, record.get("started_at"))
        )
    except Exception:
        return False


def _pruned_session_records(ledger: Path) -> List[dict]:
    """The parseable lines at the end of a prune ledger, newest last.

    Reads at most `_PRUNE_LEDGER_TAIL_BYTES` from the end of a REGULAR file
    (a symlink or FIFO is refused, O_NONBLOCK keeps a FIFO from blocking the
    open), drops the partial first line of a cut read, and skips every line
    that is not a JSON object. Never raises; any failure is an empty list.
    """
    try:
        if not stat.S_ISREG(os.lstat(ledger).st_mode):
            return []
        fd = os.open(ledger, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except Exception:
        return []
    try:
        size = os.fstat(fd).st_size
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return []
        offset = max(0, size - _PRUNE_LEDGER_TAIL_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        chunks = []
        remaining = size - offset
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if offset > 0:
            data = data.partition(b"\n")[2]
        records = []
        for line in data.split(b"\n"):
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records
    except Exception:
        return []
    finally:
        os.close(fd)


# One parsed ledger per (path, size, mtime) for the life of the process. A
# bucket that stays unproven re-runs the evidence check whenever the store
# changes, and without this each run re-reads and re-parses the same ledger.
_PRUNED_SESSION_INDEX: Dict[str, tuple] = {}


def _pruned_session_index(ledger: Path) -> Dict[str, List[dict]]:
    """The ledger's parseable lines grouped by `session_id`; never raises.

    Built once per ledger state and then reused, so the evidence check costs
    one dictionary lookup per writing session instead of a scan of every
    line. The cache key carries the file's size and mtime, so an appended
    line (the reaper's only write) is seen on the next read.
    """
    try:
        status = os.lstat(ledger)
        stamp = (status.st_size, status.st_mtime_ns)
    except OSError:
        return {}
    cached = _PRUNED_SESSION_INDEX.get(str(ledger))
    if cached is not None and cached[0] == stamp:
        return cached[1]
    index: Dict[str, List[dict]] = {}
    for record in _pruned_session_records(ledger):
        session_id = record.get("session_id")
        if isinstance(session_id, str):
            index.setdefault(session_id, []).append(record)
    _PRUNED_SESSION_INDEX[str(ledger)] = (stamp, index)
    return index


def _legacy_session_evidence(
    conn: Any, candidate: str, gate_roots: list, common_dir: Optional[Path]
) -> bool:
    """Whether a recorded session from one of `gate_roots` wrote `candidate`.

    The evidence an UNVERIFIED legacy bucket needs to be read: a session id
    that wrote into the bucket, whose record names a `project_dir` that IS a
    gate root and a zone-aware `started_at` later than the ctime of
    `<common_dir>/description` (`common_dir` is the MAIN repository's, so
    every linked worktree shares one file). The record is the session
    folder's context file while the folder exists, and its line in the
    project's prune ledger once the reaper has removed the folder; both pass
    the same test. A delivered checkout with the same folder name has no such
    record, and every new write binds the canonical id, so the evidence
    cannot be manufactured after the upgrade. Stops at the first hit. Never
    raises: any failure for one record is "no evidence" for that record.
    """
    from shared.pact_context import (
        PRUNE_LEDGER_NAME,
        _UNSAFE_SLUG_CHARS_RE,
        reconstruct_session_dir,
    )

    from .database import bucket_session_ids

    roots = set()
    for root in gate_roots:
        try:
            roots.add(os.path.realpath(root))
        except (OSError, ValueError):
            continue
    if not roots or common_dir is None:
        return False
    try:
        session_ids = bucket_session_ids(conn, candidate)
    except sqlite3.Error:
        return False
    # ONE LISTING PER ROOT, NOT A PATH BUILD PER SESSION. Every session of
    # one root lives in the same slug folder, and a sanitized id holds no
    # separator, so `<slug folder>/<safe id>` is the folder
    # `reconstruct_session_dir` names. Listing it once replaces one resolve
    # per writing session (measured: most of the cost at 1,000 sessions).
    # A PRESENT ENTRY IS A LIVE FOLDER, exactly as the per-session existence
    # test it replaces: its context is read (and anything unreadable is no
    # evidence), and only an ABSENT entry lets the ledger stand in.
    #
    # THE LISTING IS A SPEEDUP, NEVER THE ANSWER. A slug folder that does not
    # exist (or is a file) has no live session folders, so every session is
    # absent, as the per-session test would also find. Any OTHER failure (a
    # folder that is searchable but not readable, say) says nothing about
    # which entries exist, so that root falls back to the per-session
    # existence test (`present` is None).
    folders: Dict[str, tuple] = {}
    for root in sorted(roots):
        base: Optional[Path] = None
        present: Optional[set] = set()
        try:
            probe = reconstruct_session_dir(root, "_")
            base = Path(probe).parent if probe else None
            if base is not None:
                present = set(os.listdir(base))
        except (FileNotFoundError, NotADirectoryError):
            present = set()
        except OSError:
            present = None
        except Exception:
            base = None
        folders[root] = (base, present)
    ledgers: Dict[str, Dict[str, List[dict]]] = {}
    for session_id in session_ids:
        safe_id = _UNSAFE_SLUG_CHARS_RE.sub("_", str(session_id))
        for root in sorted(roots):
            try:
                base, present = folders[root]
                if base is None:
                    continue
                session_path = base / safe_id
                if (
                    safe_id in present
                    if present is not None
                    else os.path.lexists(session_path)
                ):
                    context = session_path / "pact-session-context.json"
                    recorded = json.loads(context.read_text(encoding="utf-8"))
                    if isinstance(recorded, dict) and _session_record_counts(
                        recorded, roots, common_dir
                    ):
                        return True
                    continue
                # THE FOLDER IS GONE: its line in the prune ledger stands in.
                ledger = str(session_path.parent / PRUNE_LEDGER_NAME)
                if ledger not in ledgers:
                    ledgers[ledger] = _pruned_session_index(Path(ledger))
                for record in ledgers[ledger].get(session_id, ()):
                    if _session_record_counts(record, roots, common_dir):
                        return True
            except Exception:
                continue
    return False


def _warn_legacy_excluded(
    store: str, candidate: str, memories: int, file_paths: int, repo_root: Any
) -> None:
    """Disclose an EXCLUDED legacy bucket, once per process per bucket.

    NO COMMAND IS RENDERED. The gate excluded it because its absolute rows lie
    outside this repository, so they belong to the repository their paths
    name; a printed move into THIS id would file another checkout's rows here.
    """
    key = (store, candidate)
    if key in _EXCLUSION_WARNED:
        return
    _EXCLUSION_WARNED.add(key)
    logger.warning(
        "legacy project bucket %r holds %d memories and %d file path%s, but "
        "none of its absolute file rows could be located under %r, so it is "
        "NOT in this read scope: its rows belong to the repository their "
        "paths name. Run "
        "the `project-id show` verb from that repository to move them. A row "
        "the gate could not READ has an unknown location, not a known one — "
        "check for a corrupt or unreadable file row before hunting paths.",
        candidate,
        memories,
        file_paths,
        "" if file_paths == 1 else "s",
        str(repo_root) if repo_root is not None else "none",
    )


def _printed_rekey_remedy(commands: list) -> str:
    """The remedy text a warning prints: the commands as they run, or why not.

    A rendered command is shell-quoted, so pasted as printed it runs (`%r`
    made it one quoted word). None (`runnable_rekey_command` found the id is
    one rekey refuses) prints why no command exists. A command holding a
    control or format character (a store path with a newline) is NOT
    printed: in a log line it would start a forged line. `project-id show`
    carries the same command in its JSON envelope, where it is escaped.
    """
    if any(command is None for command in commands):
        return (
            "no command: `project-id rekey` refuses a bucket id that holds a "
            "control character"
        )
    if any(
        unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp")
        for command in commands
        for char in command
    ):
        return (
            "the command printed by the `project-id show` verb (its bucket id "
            "or store path holds a control character, so it is not printed "
            "in this log line)"
        )
    return " ; ".join(commands)


def _warn_legacy_unproven(
    store: str, candidate: str, target: str, memories: int, file_paths: int
) -> None:
    """Disclose an UNPROVEN legacy bucket, once per process per bucket."""
    key = (store, candidate)
    if key in _UNPROVEN_WARNED:
        return
    _UNPROVEN_WARNED.add(key)
    logger.warning(
        "legacy project bucket %r holds %d memories and %d file path%s, but "
        "no file row places it and no recorded session from this repository "
        "wrote it, so it is NOT in this read scope: its folder name alone "
        "cannot tell this checkout's rows from another checkout of the same "
        "name. If the rows belong here, move them explicitly with: %s",
        candidate,
        memories,
        file_paths,
        "" if file_paths == 1 else "s",
        _printed_rekey_remedy(
            [runnable_rekey_command(candidate, target, db_path=store)]
        ),
    )


def _warn_case_variant(
    store: str, legacy: str, variants: list, target: str
) -> None:
    """DISCLOSE a suppressed case-variant candidate, once per process.

    A fold suppression that is not reported is silent loss: the operator's
    own pre-upgrade bucket can be visible in `project-id show`'s census and
    absent from every read with no explanation. Naming the bucket and the
    literal rekey command is the difference between a deliberate non-binding
    and a lost bucket. The bucket is NEVER read here.
    """
    key = (store, tuple(variants))
    if key in _CASE_VARIANT_WARNED:
        return
    _CASE_VARIANT_WARNED.add(key)
    commands = _printed_rekey_remedy(
        [runnable_rekey_command(variant, target, db_path=store) for variant in variants]
    )
    logger.warning(
        "the legacy project basename %r matches stored bucket(s) %s only by "
        "case, and this filesystem distinguishes case, so none is in this "
        "read scope: on a case-sensitive filesystem those are different "
        "projects, not one project spelled differently. If a bucket belongs "
        "to this project, move its rows explicitly with: %s",
        legacy,
        ", ".join(repr(variant) for variant in variants),
        commands,
    )


_DECLINE_CAUSES = {
    "gitfile_redirect": (
        "the `.git` marker redirects at another repository's git directory, "
        "so its remote belongs to that repository"
    ),
    "origin_unreadable": "git could not read the repository's origin remote",
    "origin_unrepresentable": (
        "the repository's origin remote is present but cannot be represented "
        "as a project id"
    ),
    "broken_git_marker": (
        "the directory carries a `.git` marker but git could not answer for it"
    ),
    "no_working_tree": "the repository has no working tree to name",
    "submodule_checkout": (
        "the checkout is a submodule, so its folder name is chosen by the "
        "superproject's .gitmodules"
    ),
    "declaration_unresolved": "the declared directory could not be resolved",
}

# THE REMEDY FOR A DECLINE NO PIN CAN FIX. A pin is offered only when the
# decline names the override file that would fix it; every other cause names
# its own remedy, so an unworkable `<git-common-dir>` pin is never printed.
_UNPINNABLE_REMEDIES = {
    "broken_git_marker": (
        "Repair or remove the broken `.git` marker, or check that git is "
        "installed and can read it."
    ),
    "no_working_tree": (
        "Run from a checkout with a working tree; a bare repository has none."
    ),
    "declaration_unresolved": (
        "Set CLAUDE_PROJECT_DIR to an existing project root."
    ),
}

# THE REDIRECT'S REMEDY IS THE MARKER, AND IT NAMES NO PIN PATH. The common
# dir a redirect could name belongs to the repository the `.git` file points
# AT, and a forged pointer looks exactly like a user's own
# `--separate-git-dir` layout, so no reader of this text can judge whether a
# pin there is safe. Pinning one's own layout is the USER's decision,
# described in the memory skill.
_REDIRECT_REMEDY = (
    "Fix or remove the `.git` file, or run from the repository it points at. "
    "A `git init --separate-git-dir` layout of your own is a user decision: "
    "see the pact-memory skill."
)


def _warn_declined(
    record: Optional[dict],
    root: Any = None,
    detail: Optional[str] = None,
    cause_detail: Optional[str] = None,
) -> None:
    """Log ONE decline warning with its real cause, never a generic origin claim.

    The generic sentence ("an origin remote that could not be read or
    represented") is WRONG for a forged `.git` marker or a broken repository,
    and sent the operator to inspect a remote that was not the problem. The
    structured token knows the cause, so the message uses it and falls back
    to the generic sentence only when no reason was recorded. `detail` names
    the rung's own facts (which declaration, which path); `cause_detail` is
    the decline's own fixed sentence, appended to the cause.
    """
    token = (record or {}).get("reason")
    cause = _DECLINE_CAUSES.get(token) if token else None
    if cause is None:
        cause = (
            "the repository at %r has an origin remote that could not be "
            "read or represented as a project id" % (str(root),)
        )
    if cause_detail:
        cause = f"{cause}; {cause_detail}"
    if detail:
        cause = f"{cause} ({detail})"
    if token == "gitfile_redirect":
        logger.warning(
            "%s, so no project id was derived. %s Run the `project-id show` "
            "verb to inspect the detected identity.",
            cause,
            _REDIRECT_REMEDY,
        )
        return
    override_path = (record or {}).get("override_path")
    if override_path is None:
        remedy = _UNPINNABLE_REMEDIES.get(token, "")
        logger.warning(
            "%s, so no project id was derived.%s Run the `project-id show` "
            "verb to inspect the detected identity.",
            cause,
            f" {remedy}" if remedy else "",
        )
        return
    logger.warning(
        "%s, so no project id was derived. Run the `project-id show` verb to "
        "inspect the detected identity, or write an operator-chosen canonical "
        "id to %r.",
        cause,
        str(override_path),
    )


def _decline(
    source: str,
    reason: str,
    *,
    label: Optional[str] = None,
    override_path: Optional[str] = None,
    root: Optional[Path] = None,
    legacy: Optional[str] = None,
    pre_change_name: Optional[str] = None,
    override_ignored: Optional[dict] = None,
    detail: Optional[str] = None,
    cause_detail: Optional[str] = None,
) -> "_DetectedIdentity":
    """THE ONE PLACE a resolver decline is built and reported.

    `source` is the rung token recorded in `decline_reason`; `label` is the
    `source` the result discloses, `"unresolved"` unless the rung keeps its
    own label (the declared rungs do). Logs exactly one warning.
    """
    record = decline_record(reason, override_path, source)
    _warn_declined(record, root, detail, cause_detail)
    return _DetectedIdentity(
        project_id=None,
        source=label or "unresolved",
        legacy_basename=legacy,
        override_ignored=override_ignored,
        identity_root=root,
        decline_reason=record,
        pre_change_name=pre_change_name,
        decline_detail=cause_detail,
    )


def _decline_detected(
    identity: "RootIdentity",
    source: str,
    *,
    label: Optional[str] = None,
    root: Optional[Path] = None,
    legacy: Optional[str] = None,
    pre_change_name: Optional[str] = None,
) -> "_DetectedIdentity":
    """`_decline` for a decline `detect_id_for_root` already named.

    That layer does not know which rung called it, so its record carries no
    `source`; the rung supplies it here.
    """
    reason = identity.decline_reason or {}
    return _decline(
        source,
        reason.get("reason") or "origin_unrepresentable",
        label=label,
        override_path=reason.get("override_path"),
        root=root,
        legacy=legacy,
        pre_change_name=pre_change_name,
        override_ignored=identity.override_ignored,
        cause_detail=getattr(identity, "detail", None),
    )


# `_root_guard`'s default for `toplevel`: the caller holds no answer, so the
# guard asks git itself.
_TOPLEVEL_UNASKED: Any = object()


def _root_guard(
    asked_root: Optional[Path],
    common_dir: Optional[Path],
    source: str,
    *,
    label: Optional[str] = None,
    root: Optional[Path] = None,
    toplevel: Any = _TOPLEVEL_UNASKED,
    no_working_tree_detail: Optional[str] = None,
) -> "Optional[_DetectedIdentity]":
    """The repository guards every rung runs before it derives an id.

    1. No common dir, but a `.git` marker git could not answer for: a broken
       repository, not an umbrella folder -> `broken_git_marker`.
    2. A linked-worktree shape that `git worktree list` does not back: a
       forged `.git` file naming another repository's worktree admin dir
       -> `gitfile_redirect`.
    3. A common dir git answers for, but no working tree: a bare repository,
       or a `.git` file naming one -> `no_working_tree`. Every rung reaches
       this shape (the cwd-marker rung through a `GIT_CEILING_DIRECTORIES`
       entry at the checkout root), so it is checked here, not per rung.

    `toplevel` is the caller's `--show-toplevel` answer for `asked_root`,
    when it holds one (None: git gave none); left out, the guard asks git
    once, which only the rungs that reach the guard with a common dir pay.
    `no_working_tree_detail` keeps a rung's own wording for arm 3.

    Returns the decline, or None when the root passes.
    """
    if common_dir is None:
        if asked_root is not None and _unanswerable_repository(asked_root) is not None:
            return _decline(
                source,
                "broken_git_marker",
                label=label,
                root=root,
                detail=f"{source}: {str(asked_root)!r}",
            )
        return None
    # ONE TOPLEVEL QUESTION per guard: arm 2 needs it for a linked-worktree
    # shape and arm 3 needs it always, so it is asked here once and shared.
    if toplevel is _TOPLEVEL_UNASKED:
        toplevel = (
            _git_rev_parse(str(asked_root), "--show-toplevel")
            if asked_root is not None
            else None
        )
    if asked_root is not None and _worktree_redirect(
        asked_root, common_dir, toplevel=Path(toplevel) if toplevel else None,
    ):
        # No override path: the common dir is the TARGET repository's.
        return _decline(
            source,
            "gitfile_redirect",
            label=label,
            root=root,
            detail=(
                f"{source}: {str(asked_root)!r} resolves to another "
                "repository's worktree admin dir but is not one of its "
                "listed worktrees"
            ),
        )
    if toplevel is None:
        return _decline(
            source,
            "no_working_tree",
            label=label,
            root=root,
            detail=no_working_tree_detail or (
                f"{source}: {str(asked_root)!r} is a bare repository, or git "
                "cannot describe its working tree"
            ),
        )
    return None


def _is_live_decline(detected: "_DetectedIdentity") -> bool:
    """Whether a declared rung declined for a cause OTHER than a gone path.

    Only `declaration_unresolved` means "the declared directory is gone", the
    one case the git root is designed to answer for. Every other cause names
    a LIVE repository that refused (an unrepresentable origin, a submodule, a
    redirected `.git`): falling through to the process's own repository would
    file the declared session's memories under another project.
    """
    reason = (detected.decline_reason or {}).get("reason")
    return detected.project_id is None and reason not in (None, "declaration_unresolved")


def _is_home(root: Path) -> bool:
    """Whether `root` is the user's home directory."""
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False
    return os.path.normcase(str(root)) == os.path.normcase(str(home))


def _warn_home_landing(root: Path) -> None:
    """The HOME-landing warning, shared by every rung that can land there."""
    logger.warning(
        "project_id resolved to the HOME directory (%r); memories "
        "will be scoped to user scope %r, not a project. Run from "
        "the project directory or set CLAUDE_PROJECT_DIR.",
        root,
        root.name,
    )


def _suppression_record() -> dict:
    """The record for a SUPPRESSED legacy candidate. Not a decline.

    The identity still resolves; only the pre-upgrade bucket derived from a
    submodule admin dir is kept out of the read scope, so this record never
    goes through `_decline` and its reason is not a decline token.
    `override_path` is None because the remedy is an explicit rekey, not a
    pin.
    """
    return {
        "source": "submodule_admin_dir",
        "reason": "repository_chosen_name",
        "override_path": None,
    }


def _pre_change_declared_name(
    declared_dir: str, declared_root: Optional[Path], common_dir: Optional[Path]
) -> Optional[str]:
    """What the PRE-change resolver named a LIVE declared directory.

    It compared the common dir's PARENT (not the worktree- and
    submodule-aware root) with the resolved declared path: when they differ
    it named the parent, otherwise the declared path itself. A path that
    does not exist is the gone arm, which the caller names separately.
    """
    old_main = common_dir.parent if common_dir is not None else None
    if (
        old_main is not None
        and declared_root is not None
        and os.path.normcase(str(old_main)) != os.path.normcase(str(declared_root))
    ):
        return old_main.name
    return (declared_root or Path(declared_dir)).name or None


def _legacy_suppression(
    identity_root: Optional[Path],
) -> "tuple[Optional[dict], Optional[str]]":
    """The structured disclosure for a SUPPRESSED legacy candidate.

    A submodule admin dir supplies a REPOSITORY-CHOSEN bucket name, so the
    pre-upgrade candidate derived from it is never admitted to the read
    scope. Returning no name is not enough: the operator must be able to see
    what was suppressed and why, on the same envelope as every other
    read-scope field. Returns `(record, derived_name)` or `(None, None)`.
    """
    if identity_root is None:
        return None, None
    common_dir = main_repo_common_dir(str(identity_root))
    if common_dir is None or not _submodule_admin_dir(common_dir):
        return None, None
    try:
        derived = common_dir.resolve().parent.name
    except (OSError, RuntimeError, ValueError):
        derived = common_dir.parent.name
    return _suppression_record(), derived


def _disclosed_source(identity: "RootIdentity", rung_label: str) -> str:
    """The source token to disclose when a declared rung won.

    When an operator pin decided the value, the disclosure must say
    `override file`, not the declaration label: a reader who sees
    `CLAUDE_PROJECT_DIR` believes the environment chose the id, and the
    whole point of the pin is that a human chose it.
    """
    return "override file" if identity.source == "override file" else rung_label


def runnable_rekey_command(
    from_id: str, to_id: str, db_path: Any = None
) -> Optional[str]:
    """`render_rekey_command`, or None when rekey would refuse `from_id`.

    A printed remedy must run as printed. `project-id rekey` binds `--from`
    through the write-ingress rule, so an id that rule refuses (a control
    character, a lone surrogate, an over-long id) has no runnable command,
    and printing one would be a remedy that fails.
    """
    try:
        validate_identifier_for_write(from_id)
    except ValueError:
        return None
    return render_rekey_command(from_id, to_id, db_path=db_path)


def render_rekey_command(
    from_id: str, to_id: str, db_path: Any = None, prog: Any = None
) -> str:
    """The literal, SHELL-QUOTED AND RUNNABLE rekey command for one bucket.

    ONE HELPER FOR EVERY RENDER SURFACE, because a bucket id is
    STORE-INFLUENCED and this string is presented as something a user pastes
    into a shell. An unquoted id containing a space, `;`, `$` or a quote is a
    copy-paste command-injection surface.

    THE TARGET IS REQUIRED. A command that leaves `--to` to its default
    moves the bucket into whatever id the session that RUNS it resolves,
    which is another repository's id when the command is pasted elsewhere.

    EVERY VALUE IS ONE `--flag=value` ARGUMENT, so an id that begins with `-`
    (a legal folder name) is a value to argparse, never an option.

    INTERPRETER-PREFIXED, because there is no `pact-memory` executable: the
    skill's documented invocation is `python3 <skill>/scripts/cli.py`.

    `db_path` IS CARRIED WHEN KNOWN. Without it the printed remedy runs
    against the DEFAULT store, so a bucket reported from `--db-path X` is
    "moved" in some other file and reports success.
    """
    argv = [
        "python3",
        str(prog) if prog is not None else str(Path(__file__).with_name("cli.py")),
        "project-id",
        "rekey",
        "--from=" + from_id,
        "--to=" + to_id,
    ]
    if db_path is not None:
        argv.append("--db-path=" + str(db_path))
    return shlex.join(argv)


def project_candidate_rows(
    buckets: Dict[str, int],
    *,
    store: Any,
    current_id: Optional[str],
    legacy_state: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """THE ONE CENSUS BUILDER: every non-empty bucket, its relation and remedy.

    A command is rendered only for a bucket this session can defend moving
    into its own id: its own legacy candidate (unless the gate excluded it,
    whose rows lie in another repository) and a case variant of it. The
    session's own bucket and every other bucket get none, because a printed
    command is read as advice to run it.
    """
    state = legacy_state or {}
    candidate = state.get("candidate")
    variants = list(state.get("case_variant") or [])
    variant_commands = list(state.get("case_variant_rekey") or [])
    rows = []
    for bucket_id, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0])):
        if bucket_id == current_id:
            relation, rekey = "current", None
        elif candidate is not None and bucket_id == candidate:
            relation, rekey = "legacy_candidate", state.get("rekey")
        elif bucket_id in variants:
            index = variants.index(bucket_id)
            relation = "case_variant"
            rekey = variant_commands[index] if index < len(variant_commands) else None
        else:
            relation, rekey = "other", None
        rows.append({
            "id": bucket_id,
            "memory_count": count,
            "relation": relation,
            "rekey": rekey,
        })
    return rows


def _store_stat_key(store: str):
    """`(main, wal)` change stamps for the store, or None when it is absent.

    The `-wal` leg is load-bearing: under WAL another process's commit can
    land only in the journal, leaving the main file's stamp unchanged, and a
    cache keyed on the main file alone would keep serving a stale legacy
    membership. `-shm` is deliberately not included — shared-memory churn
    would recompute on every call without any row having changed.
    """
    try:
        main = os.stat(store)
    except OSError:
        return None
    try:
        wal = os.stat(store + "-wal")
        wal_key = (wal.st_mtime_ns, wal.st_size)
    except OSError:
        wal_key = None
    return ((main.st_mtime_ns, main.st_size), wal_key)


def _invalidate_legacy_scope_cache() -> None:
    """Drop every cached read scope after an in-process write."""
    _LEGACY_SCOPE_CACHE.clear()


def _read_only_connection(store: str):
    """Open `store` READ-ONLY; raises sqlite3.OperationalError on failure.

    A PURE READ MUST NOT CREATE OR MIGRATE THE STORE. `db_connection` opens
    read-write, and every counter it runs calls `ensure_initialized`, so a
    `project-id show` against a 0-byte store used to CREATE the full schema —
    contradicting the verb's own "creates none" contract. The URI form with
    `mode=ro` refuses those writes instead.

    SQLite may still create the EMPTY `-wal`/`-shm` sidecars when the
    directory is writable; they carry no schema and no rows. The store file
    itself is never written.
    """
    connection = sqlite3.connect(
        Path(store).resolve().as_uri() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def _store_schema_present(store: str) -> bool:
    """Whether an EXISTING store carries the `memories` table.

    A READ MUST NOT INITIALISE AN EXISTING STORE IT IS ONLY REPORTING ON, and
    the guard has to run BEFORE any read-write connection: opening one is
    already a write, because `get_connection` pre-creates the file and the WAL
    switch writes the header. So this uses the same read-only URI the identity
    probe uses and answers False for a store without a schema, creating
    nothing.

    AN ABSENT STORE IS NOT DECIDED HERE. `_schema_present` treats absence
    according to the store's ORIGIN: a derived location bootstraps on first
    use (pre-existing production contract), while a caller-named store must
    already exist. This function only inspects a store that is present.
    """
    try:
        if not Path(store).exists():
            return False
        with closing(_read_only_connection(store)) as connection:
            return _connection_has_memories_table(connection)
    except sqlite3.DatabaseError as exc:
        if _read_only_store_unavailable(exc, store):
            return False
        raise


def _connection_has_memories_table(conn: Any) -> bool:
    """Whether an OPEN connection's store carries the `memories` table.

    `sqlite_master` answers on an empty database without creating anything,
    so this is safe on a connection whose store is 0 bytes. A corrupt store
    raises, which is the non-transient direction.
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone() is not None


class StoreFaultError(sqlite3.OperationalError):
    """A store fault whose message IS the remedy `present_store_fault` built.

    Still an OperationalError, so every existing handler and exit code
    applies. Its own class so a wrapper that meets it again passes the
    message through instead of naming the remedy a second time.
    """


def present_store_fault(exc: Exception, store: str) -> Optional[str]:
    """The remedy for a store that EXISTS and cannot serve a read, or None.

    Two causes, each with its own check: the file cannot be opened
    (SQLITE_CANTOPEN: permissions or a parent directory that cannot be
    traversed), or it opens but is not a usable SQLite database
    (SQLITE_NOTADB, SQLITE_CORRUPT). None for an absent path and for every
    other failure, which the caller reports as it would have. A
    `StoreFaultError` already carries the remedy and is returned as it is.
    """
    if isinstance(exc, StoreFaultError):
        return str(exc)
    if not Path(store).exists():
        return None
    errorname = getattr(exc, "sqlite_errorname", "")
    text = str(exc).lower()
    if errorname == "SQLITE_CANTOPEN" or (
        not errorname and "unable to open database file" in text
    ):
        check = "that this process can read the file and traverse its parent directory"
    elif errorname in ("SQLITE_NOTADB", "SQLITE_CORRUPT") or (
        not errorname
        and ("file is not a database" in text or "disk image is malformed" in text)
    ):
        check = (
            "that --db-path names a PACT memory store, or restore the file "
            "from a backup"
        )
    else:
        return None
    return (
        f"the store at {store!r} exists but cannot be opened ({exc}). Check "
        f"{check}; no memory was written to it."
    )


def _read_only_store_unavailable(
    exc: "sqlite3.DatabaseError", store: str
) -> bool:
    """Whether a read-only probe failure is the DEGRADED state, not a fault.

    ONE sqlite condition is the fail-soft the architecture record promises:
    the store EXISTS but has no schema yet, so the DDL is refused read-only
    (SQLITE_READONLY) and the caller narrows to the canonical scope.

    SQLITE_CANTOPEN IS DEGRADED ONLY WHEN THE PATH IS ABSENT — a race after
    the presence check. A store that EXISTS and cannot be opened, or is not
    a database, is a FAULT: reporting "no memories" for it is a loud-to-silent
    regression, so this RAISES `present_store_fault`'s remedy. Any other
    failure raises unchanged.
    """
    errorname = getattr(exc, "sqlite_errorname", "")
    text = str(exc).lower()
    readonly = errorname == "SQLITE_READONLY" or (
        not errorname and "readonly" in text and "database" in text
    )
    if readonly:
        return True
    remedy = present_store_fault(exc, store)
    if remedy is not None:
        raise StoreFaultError(remedy) from exc
    cantopen = errorname == "SQLITE_CANTOPEN" or (
        not errorname and "unable to open database file" in text
    )
    return cantopen


@contextmanager
def _legacy_probe_context(store: str, conn):
    """Yield `(connection, degraded_reason)` for the legacy-scope probe.

    A caller's live connection (the save path) is used UNCHANGED: it is
    already initialized, and its writes belong to the caller. Otherwise the
    store is opened READ-ONLY, because this path is reached by PURE READS and
    must not bring a store, or a schema, into existence.

    When the open or its initialization check is refused for one of the two
    degraded conditions, yield `(None, reason)` so the caller narrows to the
    canonical scope and discloses it. Every other failure raises.

    THE `try` WRAPS THE PROBE ONLY, never the yielded connection: an
    exception raised by the CALLER's body must propagate, not be re-caught
    here (a generator cannot yield a second time after an exception is
    thrown into it).
    """
    if conn is not None:
        yield conn, None
        return
    degraded: Optional[str] = None
    try:
        connection = _read_only_connection(store)
        # A SCHEMA CHECK, NOT THE INITIALISER: a read never runs migrations
        # or the vector-table CREATE. A store with no schema degrades BEFORE
        # any of the body's work.
        if not _connection_has_memories_table(connection):
            connection.close()
            degraded = "no schema"
    except sqlite3.DatabaseError as exc:
        if not _read_only_store_unavailable(exc, store):
            raise
        degraded = str(exc)
    # EVERY YIELD SITS OUTSIDE THE `try`: an exception the caller's body
    # raises is thrown in at the yield, and inside the `try` the
    # OperationalError arm would catch it and yield a second time.
    if degraded is not None:
        yield None, degraded
        return
    with closing(connection), read_only_scope():
        yield connection, None


def read_project_bucket_census(store: str) -> Dict[str, int]:
    """Every NON-EMPTY project bucket in `store`, read READ-ONLY.

    The `project-id show` / `rekey --candidates` census. An ABSENT or
    SCHEMA-LESS store answers `{}`: a report must not create a store or its
    schema. A store that is PRESENT but cannot be opened, or is not a
    readable SQLite database, RAISES with the remedy named
    (`present_store_fault`), which the CLI reports as exit 2: "no buckets"
    for an unreadable store would hide its rows.
    """
    if not Path(store).exists():
        return {}
    from .database import project_bucket_counts

    try:
        with closing(_read_only_connection(store)) as connection:
            # NO SCHEMA, NO BUCKETS. `project_bucket_counts` requires the
            # schema and never creates it, so the census checks first.
            if not _connection_has_memories_table(connection):
                return {}
            return project_bucket_counts(connection)
    except sqlite3.DatabaseError as exc:
        if not _read_only_store_unavailable(exc, store):
            raise
        return {}


def _match_legacy_candidate(
    legacy: str, buckets: Dict[str, int], allow_fold: bool = False
) -> "tuple[Optional[str], list]":
    """The stored bucket spelling that matches `legacy`, or None.

    Exact case first. A folded fallback is used ONLY when `allow_fold` is
    True — the caller's statement that the filesystem holding the identity
    root PROVABLY cannot distinguish the two spellings (see
    `project_identity.filesystem_case_insensitive`). On a case-sensitive
    filesystem `ForeignProj` and `foreignproj` are two different projects, so
    folding them is a cross-project disclosure; when the filesystem cannot be
    determined the answer is False and no fold happens.

    A folded-first lookup passes on a case-insensitive filesystem and misses
    on a case-sensitive one; two folded matches are two different projects on
    such a filesystem, so guessing would merge them.

    Returns `(candidate, ambiguous)`. `ambiguous` is the sorted list of folded
    matches when more than one bucket differs only by case; it is carried into
    the disclosure so the operator can choose, rather than being dropped
    silently.

    THE FOLDED MATCH IS NOT REQUIRED TO BE `verified`, AND MUST NOT BE. The
    live legacy bucket's file rows are all relative, so the plausibility gate
    can only ever call it `unverified`; requiring `verified` here would hide a
    bucket holding thousands of memories on a case-folding path mismatch —
    silent loss, which is the defect this change exists to remove. The guards
    that keep a fold from merging two projects are the FILESYSTEM CAPABILITY
    gate and UNIQUENESS (exactly one folded match). The matched bucket then
    passes the same admission as an exact match: read when verified, or when
    unverified with session evidence (disclosed `included_unverified`), and
    otherwise `unproven` and not read.

    THE RETURNED VALUE IS THE STORED SPELLING, BYTE FOR BYTE. Folding finds a
    candidate; it never produces one.

    `allow_fold` DEFAULTS TO FALSE, THE FAIL-CLOSED DIRECTION. A security gate
    with a permissive default is a footgun for the next call site: a new
    caller that forgets the argument must lose a fold, never gain one. The
    production call site passes
    `project_identity.filesystem_case_insensitive(root)` explicitly, and the
    pure-string surrogate arms pass True because they exercise the matcher,
    not the gate.
    """
    if legacy in buckets:
        return legacy, []
    if not allow_fold:
        return None, []
    folded = [bucket for bucket in buckets if bucket.casefold() == legacy.casefold()]
    if len(folded) == 1:
        return folded[0], []
    if len(folded) > 1:
        logger.warning(
            "legacy project basename %r matches %d stored buckets by case "
            "(%s); none is used, because a fold cannot tell them apart.",
            legacy,
            len(folded),
            ", ".join(sorted(folded)),
        )
        return None, sorted(folded)
    return None, []


def _content_fields_changed(
    before: Dict[str, Any],
    after: Dict[str, Any],
    keys: List[str],
) -> bool:
    """Return True if any of ``keys`` differs between ``before`` and ``after``.

    Used by ``PACTMemory.update`` to skip embedding
    regeneration when an additive merge produces no actual change. Values are
    compared via canonical JSON serialization so that dict/list ordering and
    non-JSON-native types (e.g. datetimes) compare deterministically.
    """
    for k in keys:
        if json.dumps(before.get(k), sort_keys=True, default=str) != \
           json.dumps(after.get(k), sort_keys=True, default=str):
            return True
    return False


def _ensure_ready() -> None:
    """
    Ensure the memory system is initialized before database operations.

    This wrapper exists as:
    - A single injection point for all API methods (centralized initialization)
    - A testing seam (can be mocked to skip initialization in tests)
    - An abstraction layer if initialization logic needs to change

    Handles lazily on first use:
    - Dependency installation
    - Embedding migration
    - Pending embedding catch-up

    The initialization only runs once per session.
    """
    ensure_memory_ready()


def _with_store_scope(method):
    """Bind this instance's store for the WHOLE call.

    A DECORATOR RATHER THAN A `with` BLOCK INSIDE EACH METHOD, because an
    omission is then visible. Eight repeated blocks hide a missing one, and a
    method that quietly resolves the default store returns plausible results
    from the wrong file rather than failing.

    IT COVERS MORE THAN THE CONNECTION, AND THAT IS THE POINT. Passing the path
    to `db_connection` reached the connection only. The side paths accept no
    path and cannot be given one: `_ensure_ready` takes no parameter, and not
    one function in the search, graph, catch-up or init layers accepts a
    `db_path`. Those layers open their own connection with no argument, so
    before this scope a caller store was read for the main query and the DEFAULT
    store was read for the search, with nothing raised. Binding the store for
    the whole call is what reaches them, and it needs no signature change in
    any of them.

    A `_db_path` of None INHERITS an enclosing scope. So the module singleton,
    which holds no path, stays scopable by its caller.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with store_scope(self._db_path):
            return method(self, *args, **kwargs)
    return wrapper


# ONE DETECTION PASS'S git common-dir answers, keyed by the resolved start.
# Set only while a constructor detects its identity (a fresh dict per
# construction), and handed to that instance's FIRST legacy read, which asks
# the same question about the same directory. Nothing survives into another
# construction. A ContextVar, so concurrent constructions never share one.
_DETECTION_COMMON_DIRS: "contextvars.ContextVar[Optional[Dict[str, Optional[Path]]]]" = (
    contextvars.ContextVar("_DETECTION_COMMON_DIRS", default=None)
)

# THE PASS'S OTHER GIT ANSWERS (`rev-parse` questions and worktree listings),
# keyed by the question and the resolved directory it is asked in. Set beside
# `_DETECTION_COMMON_DIRS` as a fresh dict for each construction's detection
# pass and reset when it ends; unlike that memo it is not handed to the first
# legacy read. A forged worktree pointer is checked twice in one pass, by the
# root finder and by the rung's guard, and without this each check lists the
# worktrees and asks the toplevel again.
_DETECTION_ANSWERS: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "_DETECTION_ANSWERS", default=None
)


def _detection_answer(question: tuple, ask):
    """`ask()`'s answer, asked once per detection pass for each question.
    Outside a pass every call asks."""
    memo = _DETECTION_ANSWERS.get()
    if memo is None:
        return ask()
    if question not in memo:
        memo[question] = ask()
    return memo[question]


def _where(start) -> str:
    """The resolved directory a git question is asked in (the cwd for None)."""
    try:
        return os.path.realpath(start if start is not None else os.getcwd())
    except (OSError, ValueError):
        return str(start)


def main_repo_common_dir(start: Optional[str] = None) -> Optional[Path]:
    """Resolve the repository's GIT COMMON DIR via `git rev-parse`.

    Split out of `main_repo_root` because two callers need different things
    from the same answer: the identity resolver needs the common dir itself
    (the repository override file lives at `<common_dir>/pact-project-id`, and
    that path is NOT necessarily `<root>/.git` — `git init
    --separate-git-dir` and bare repositories both break the literal join),
    while every existing caller wants its parent.

    Args:
        start: Directory git resolves from, passed as `-C`. When None, git
            runs in the current working directory.

    Returns:
        The resolved common dir, or None when git is absent, times out, exits
        non-zero, or the path is not inside a repository.
    """
    # INSIDE ONE DETECTION PASS, ONE ANSWER PER DIRECTORY (see
    # `_DETECTION_COMMON_DIRS`): a second call for the same resolved
    # directory returns the first answer instead of running the identical
    # git command again. Outside a pass every call runs git.
    memo = _DETECTION_COMMON_DIRS.get()
    if memo is not None:
        try:
            key = os.path.realpath(start if start is not None else os.getcwd())
        except (OSError, ValueError):
            key = None
        if key is not None:
            if key not in memo:
                outside_pass = _DETECTION_COMMON_DIRS.set(None)
                try:
                    memo[key] = main_repo_common_dir(start)
                finally:
                    _DETECTION_COMMON_DIRS.reset(outside_pass)
            return memo[key]

    command = ["git"]
    if start is not None:
        command += ["-C", str(start)]
    command += ["rev-parse", "--git-common-dir"]
    # Function-level: the shared package is importable once pact_session's
    # sys.path bootstrap has run, which this module's import of it does.
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=False,
            timeout=5,
            env=git_env_without_location(),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        # os.fsdecode, never subprocess's locale decoding: a path may contain
        # bytes invalid in the locale, and UnicodeDecodeError would escape this
        # reader.
        common = Path(os.fsdecode(result.stdout).strip())
        if not common.is_absolute():
            # git returns this path RELATIVE TO THE DIRECTORY IT RAN IN, at any
            # depth — measured in the main checkout: ".git" at the root,
            # "../.git" one level down, "../../../.git" three levels down. Not
            # only the bare ".git" at a root, and CLAUDE_PROJECT_DIR is known to
            # reach subdirectories in real launch modes, so this branch is
            # exercised rather than an edge case.
            # The base therefore tracks `start`: resolving unconditionally
            # against the cwd returns the WRONG ROOT for any caller whose
            # `start` differs from the cwd.
            # Measured, and stated because the obvious example is the wrong one:
            # a LINKED WORKTREE returns an ABSOLUTE path at every depth, so this
            # branch never runs there. The case it protects is the main
            # checkout, not the worktree.
            base = Path(start) if start is not None else Path.cwd()
            common = base / common
        return common.resolve()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        # git not installed, not a repo, command timed out, a subprocess-level
        # refusal (ValueError: e.g. an embedded NUL), or the path could not be
        # resolved. NOT an undecodable answer: stdout is bytes and decoded by
        # os.fsdecode, which cannot raise.
        return None



def _listed_worktree_toplevels(start, *, git_dir: Optional[Path] = None) -> Optional[set]:
    """Every toplevel `git worktree list` reports for `start`'s repository.

    With `git_dir`, the listing is of THAT repository (`--git-dir`), which is
    the only form that reaches a submodule's own worktrees: run from a
    directory, git discovers the enclosing repository instead.

    `None` when git cannot enumerate — a failed ANSWER, not a failed
    verification, so callers must not treat it as proof of a redirect.
    """
    target = ("--git-dir", _where(git_dir)) if git_dir is not None else ("-C", _where(start))
    return _detection_answer(
        ("worktree list", target), lambda: _ask_worktree_toplevels(start, git_dir)
    )


def _ask_worktree_toplevels(start, git_dir: Optional[Path]) -> Optional[set]:
    command = ["git"]
    if git_dir is not None:
        command += ["--git-dir", str(git_dir)]
    elif start is not None:
        command += ["-C", str(start)]
    command += ["worktree", "list", "--porcelain"]
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            command, capture_output=True, text=False, timeout=5,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    toplevels = set()
    for line in os.fsdecode(result.stdout).splitlines():
        if not line.startswith("worktree "):
            continue
        try:
            toplevels.add(Path(line[len("worktree "):]).resolve())
        except (OSError, RuntimeError):
            continue
    return toplevels


def _verified_worktree_roots(repo_root: Path, common_dir: Optional[Path]) -> List[Path]:
    """The gate roots: the identity root plus every TWO-WAY-LINKED worktree.

    `git worktree list` reports whatever `<common>/worktrees/*/gitdir` names,
    and a delivered archive with its own `.git` can name ANY directory there,
    including another project's real checkout, which would then place that
    project's absolute rows "under" this repository. A listed worktree W is
    kept only when (a) git does not call it prunable, (b) `W/.git` is a FILE
    whose `gitdir:` is `<common>/worktrees/<name>`, and (c) that admin dir's
    `gitdir` names `W/.git` back. Anything else is dropped, which can only
    narrow the gate; a listing failure narrows it to the identity root.
    """
    roots = [Path(repo_root).resolve()]
    if common_dir is None:
        return roots
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "worktree", "list", "--porcelain", "-z"],
            capture_output=True,
            text=False,
            timeout=5,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return roots
    if result.returncode != 0:
        return roots
    try:
        admin_parent = (Path(common_dir) / "worktrees").resolve()
    except (OSError, RuntimeError, ValueError):
        return roots
    # `-z`: every field ends in NUL and an empty field ends a record.
    records, current = [], []
    for field in os.fsdecode(result.stdout).split("\0"):
        if field:
            current.append(field)
        elif current:
            records.append(current)
            current = []
    if current:
        records.append(current)
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
            line = next(
                (
                    entry for entry in marker.read_text(encoding="utf-8").splitlines()
                    if entry.startswith("gitdir:")
                ),
                None,
            )
            if line is None:
                continue
            admin = Path(line.split(":", 1)[1].strip())
            if not admin.is_absolute():
                admin = Path(worktree) / admin
            admin = admin.resolve()
            if admin.parent != admin_parent:
                continue
            back = Path((admin / "gitdir").read_text(encoding="utf-8").strip())
            if not back.is_absolute():
                back = admin / back
            if back.resolve() != marker.resolve():
                continue
            roots.append(Path(worktree).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return list(dict.fromkeys(roots))


def _worktree_redirect(
    asked_root, common_dir, *, git_dir: Optional[Path] = None,
    toplevel: Optional[Path] = None,
) -> bool:
    """Whether the linked-worktree shape is NOT backed by git's listing.

    A `.git` FILE naming another repository's WORKTREE ADMIN dir makes every
    git answer agree — `--git-dir`, `--git-common-dir` and `--show-toplevel`
    all describe that other repository — while `asked_root` is not a checkout
    of it at all (the forged-pointer shape `shared/project_scope.py` names).
    A genuine linked
    worktree IS listed by `git worktree list`, so the listing test separates
    them.

    APPLIED ONLY WHEN THE GIT DIR DIFFERS FROM THE COMMON DIR. A submodule and
    a `--separate-git-dir` checkout report the same directory as both, and
    `worktree list` there reports the GIT DIR rather than the checkout — they
    would fail this test (measured). They never reach it.

    `git_dir` and `toplevel`, when the caller already holds git's answers
    for `asked_root`, are used instead of asking git again.
    """
    if asked_root is None or common_dir is None:
        return False
    try:
        if git_dir is None:
            git_dir_raw = _git_rev_parse(str(asked_root), "--git-dir")
            if git_dir_raw is None:
                return False
            git_dir = Path(git_dir_raw)
            if not git_dir.is_absolute():
                git_dir = Path(asked_root) / git_dir
        git_dir = Path(git_dir).resolve()
        common_path = Path(common_dir).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if git_dir == common_path:
        return False
    # LISTED BY THE REPOSITORY THE COMMON DIR BELONGS TO: a submodule's
    # admin dir sits inside the superproject's `.git`, so a listing run from
    # a directory would report the SUPERPROJECT's worktrees.
    listed = _listed_worktree_toplevels(None, git_dir=common_path)
    if listed is None:
        return False
    if toplevel is None:
        toplevel_raw = _git_rev_parse(str(asked_root), "--show-toplevel")
        if toplevel_raw is None:
            return False
        toplevel = Path(toplevel_raw)
    try:
        toplevel = Path(toplevel).resolve()
    except (OSError, RuntimeError):
        return False
    return toplevel not in listed


def _cwd_or_none() -> Optional[Path]:
    """`Path.cwd()`, or None when the process cwd no longer exists.

    `Path.cwd()` RAISES FileNotFoundError once the directory is deleted, so it
    must never be called inline on a path that resolves identity: a deleted
    cwd is an ordinary state for a long-lived process and the resolver's job
    is to decline, not to crash.
    """
    try:
        return Path.cwd()
    except (OSError, RuntimeError):
        return None


def _unanswerable_repository(start) -> Optional[Path]:
    """The `.git` marker proving `start` sits in a repository git could not answer for.

    Returns the marker path, or None when no marker is reachable. ONE WALK,
    `project_identity.find_git_marker`, with git's own boundaries, so a
    directory git would not treat as inside a repository is never declined
    for that reason:

    - a `.git` FILE counts (a submodule and a linked worktree both use one),
      tested with `lexists` so a broken marker still marks a repository;
    - the START directory is always examined, even when it is a
      `GIT_CEILING_DIRECTORIES` entry (git uses it); a PARENT that is a
      ceiling stops the walk before it is examined (git will not move up
      into one);
    - it stops when the device changes, unless
      `GIT_DISCOVERY_ACROSS_FILESYSTEM` is truthy, and at the filesystem root.

    Never raises: an unreadable path or environment answers None.
    """
    return find_git_marker(start)


def _git_rev_parse(start: Optional[str], *args: str) -> Optional[str]:
    """One `git rev-parse` answer, decoded, or None.

    Bytes are decoded with `os.fsdecode` (never locale decoding), so a path
    holding bytes invalid in the locale answers rather than raising.
    """
    if args == ("--git-dir",):
        # One answer per directory inside a detection pass, shared with the
        # identity layer's submodule check (`git_dir_answer`).
        return project_identity.git_dir_answer(start)
    return _detection_answer(
        ("rev-parse", _where(start), args), lambda: _ask_rev_parse(start, args)
    )


def _ask_rev_parse(start: Optional[str], args: tuple) -> Optional[str]:
    command = ["git"]
    if start is not None:
        command += ["-C", str(start)]
    command += ["rev-parse", *args]
    from shared.project_scope import git_env_without_location

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=False,
            timeout=5,
            env=git_env_without_location(),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return os.fsdecode(result.stdout).strip()


def main_repo_root(
    start: Optional[str] = None, common: Optional[Path] = None
) -> Optional[Path]:
    """The repository's identity ROOT — the working tree, or the main checkout.

    `common.parent` IS NOT ALWAYS THE ROOT. It is right only when the common
    dir is literally `<root>/.git`. Measured counter-examples:

    - SUBMODULE: common is `<super>/.git/modules/vendor/sub`, so
      `common.parent` is `.../modules/vendor` -- a path with no origin, which
      filed the submodule under the basename `vendor`.
    - `git init --separate-git-dir=<ext>/sep.git`: common is external, so
      `common.parent` is `<ext>` -- again not the working tree.

    The rule: `git rev-parse --show-toplevel` IS the identity root, EXCEPT for
    a LINKED WORKTREE, where `git_dir` is per-worktree (`.../.git/worktrees/x`)
    while `common` is the main repository's `.git`; there the main checkout is
    the identity, so `common.parent` is returned. The discriminator is
    `git_dir != common` AND `realpath(common.parent/.git) == common`: a
    submodule's git dir EQUALS its common dir, and an external
    `--separate-git-dir` has no `.git` at `common.parent`, so neither can be
    mistaken for a linked worktree.

    A BARE repository has no toplevel, so it DECLINES (None): there is no
    working tree to name, and guessing one would be the misfile this change
    exists to prevent.

    Args:
        start: Directory git resolves from, passed as `-C`. When None, git
            runs in the current working directory.
        common: The already-resolved common dir, when the caller has it. Saves
            a duplicate `rev-parse`; `None` resolves it here.

    Returns:
        The identity root, or None when git is absent, times out, exits
        non-zero, the path is not inside a repository, or the repository is
        bare.
    """
    if common is None:
        common = main_repo_common_dir(start)
    if common is None:
        return None
    toplevel = _git_rev_parse(start, "--show-toplevel")
    if toplevel is None:
        # No working tree: a bare repository, or a repository git cannot
        # describe. There is no root to name.
        return None
    git_dir_raw = _git_rev_parse(start, "--git-dir")
    if git_dir_raw is not None:
        try:
            git_dir = Path(git_dir_raw)
            if not git_dir.is_absolute():
                base = Path(start) if start is not None else Path.cwd()
                git_dir = base / git_dir
            git_dir = git_dir.resolve()
            common_path = common.resolve()
            main_git = (common_path.parent / ".git").resolve()
            if git_dir != common_path:
                # ANY LINKED WORKTREE IS VERIFIED, of a main checkout or of a
                # submodule: a forged `.git` FILE naming another
                # repository's worktree admin dir makes every git answer
                # agree, and a genuine linked worktree is listed. The caller
                # reports the refusal: every resolver rung names it as
                # `gitfile_redirect` through its own guard, so a warning here
                # would log the one cause twice.
                asked = Path(start) if start is not None else Path.cwd()
                if _worktree_redirect(
                    asked, common_path, git_dir=git_dir, toplevel=Path(toplevel)
                ):
                    return None
                if main_git == common_path:
                    # LINKED WORKTREE of a main checkout: the main checkout
                    # is the identity.
                    return common_path.parent
        except (OSError, RuntimeError):
            # A path that will not resolve cannot prove the worktree shape;
            # the toplevel is the safe answer.
            pass
    return Path(toplevel).resolve()


def _declared_path_exists(declared_dir: str) -> bool:
    """Whether a DECLARED project directory is still present on disk.

    The predicate that separates the live arm from the declined one. It is
    deliberately `exists()` (following symlinks), NOT `is_dir()` and NOT
    `lexists()`:

    - an existing non-directory keeps today's basename behaviour, so the live
      arm stays byte-identical;
    - a broken symlink is not a usable declaration: `exists()` follows the
      link and reports False, so it takes the declined arm. (This predicate
      makes no claim about what `record_resolution` would conclude; the
      decline is decided here, before the record is consulted.)

    Never raises: an unreadable or invalid path is "does not exist", which
    routes to the record lookup and then to the decline, never to a crash.
    """
    try:
        return Path(declared_dir).exists()
    except (OSError, RuntimeError, ValueError):
        # ValueError is listed for a contract reason, not a measured one:
        # `Path.exists()` happens to swallow it on Python 3.12, so this arm is
        # unreachable today. The "Never raises" guarantee above belongs to
        # THIS function and must not depend on a library's internal exception
        # policy, so the type is named here rather than left implicit.
        return False


class ProjectIdentityUnresolvedError(ProjectScopeDisagreementError):
    """A WRITE was refused because no project identity could be resolved.

    Distinct from the base class, whose cause is an env/record disagreement.
    Subclassed rather than replacing it so the existing
    `except ProjectScopeDisagreementError` sites keep catching this refusal,
    while the message names the real cause.

    Raised by `save()` only: every other write path either writes by
    `memory_id`, already refuses a None id, or funnels through `save()`.
    """


class PACTMemory:
    """
    High-level interface for PACT Memory operations.

    Provides a clean API for saving, searching, and managing memories
    with automatic project/session detection and file tracking.

    Usage:
        memory = PACTMemory()

        # Save a memory
        memory_id = memory.save({
            "context": "Working on authentication",
            "goal": "Add JWT refresh tokens",
            "lessons_learned": ["Redis INCR is atomic"],
            "decisions": [{"decision": "Use Redis", "rationale": "Fast TTL"}]
        })

        # Search memories
        results = memory.search("authentication tokens")

        # List recent memories
        recent = memory.list(limit=10)
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        db_path: Optional[Path] = None
    ):
        """
        Initialize the PACTMemory API.

        Args:
            project_id: Project identifier. If not provided, it is the
                        repository identity of the first rung that resolves:
                        CLAUDE_PROJECT_DIR, the session record's project_dir,
                        the git root (each reading its repository's override
                        file, then its origin), the user-scope setting (only
                        with no declaration and no repository), then the
                        working directory's folder name (home scope warns).
                        Measured git subprocesses per detection, by layout
                        and rung, as the fewest to the most over that rung's
                        routes (each takes about 1 ms):
                          checkout: git root 4, CLAUDE_PROJECT_DIR 4, cwd marker 5;
                          linked worktree: git root 5, CLAUDE_PROJECT_DIR 5, cwd marker 6;
                          submodule: git root 5-6, CLAUDE_PROJECT_DIR 5-6, cwd marker 6;
                          separate-git-dir checkout: git root 4, CLAUDE_PROJECT_DIR 4, cwd marker 5;
                          linked worktree of a submodule: git root 5-6, CLAUDE_PROJECT_DIR 5-6, cwd marker 6;
                          submodule of a superproject worktree: git root 5-6, CLAUDE_PROJECT_DIR 5-6, cwd marker 6.
                        A submodule asked about from a subdirectory costs the
                        one more. The cwd marker is reached through a
                        GIT_CEILING_DIRECTORIES entry above the cwd. The first
                        legacy-scope read adds up to 2 more (the worktree
                        list, plus the common dir when detection asked about
                        another directory). No call is repeated with the same
                        argument.
            session_id: Session identifier. Auto-detected from context file if not provided.
            db_path: Custom database path. Uses default if not provided.

        Raises:
            ValueError: If a SUPPLIED `project_id` or `session_id` is not a
                non-empty string free of control characters and lone
                surrogates. A falsy supplied value still means "auto-detect"
                — the guard sits INSIDE the truthy branch, so
                `project_id=""` keeps its documented detection meaning
                instead of becoming a refusal. A value is refused here
                rather than at its first use because a SUPPLIED identifier
                is caller input that will reach a utf-8 TEXT binding, where
                it would otherwise fail as an unhandled UnicodeEncodeError.
                A DETECTED project id or session id is deliberately NOT
                validated here: by-id and read-only commands construct this
                object without binding it, so the paths that DO bind it
                refuse at their own ingress (see the `project_id` and
                `session_id` properties).
        """
        if project_id:
            # A caller-supplied project_id short-circuits detection entirely —
            # measured end to end, not inferred. It is also invisible to the
            # env/record scope guard, which takes no arguments and never sees
            # a payload, so "supplied" is the least-checked source of the six
            # and is exactly the one worth naming in the disclosure.
            #
            # THE GUARD SITS INSIDE THIS BRANCH, NOT IN FRONT OF IT. In front,
            # a falsy-but-present id would be refused, and `PACTMemory("")`
            # flips from "auto-detect" (documented, and the route the save
            # guard's remedy text names) to an error. The rule here is about
            # values a caller actually supplied; omission and falsy stay the
            # detection signal they have always been.
            if _unusable_identifier(project_id):
                raise ValueError(
                    "project_id, when supplied, must be a non-empty string "
                    "without control characters (U+0000-U+001F, "
                    "U+007F-U+009F) or lone surrogates (U+D800-U+DFFF); "
                    "pass nothing to auto-detect the project."
                )
            self._project_id: Optional[str] = project_id
            self._project_id_source = "supplied"
            # A SUPPLIED value is arbitrary by design: the caller named the
            # destination. It is neither canonicalised nor classified as a
            # folder name, so the disclosure says so rather than guessing.
            self._project_id_shape = "caller"
            self._legacy_project_basename = None
            self._override_ignored = None
            # A SUPPLIED id does not widen: there is no legacy candidate and
            # therefore no gate root to carry. PINNED, not coded around.
            self._project_identity_root = None
            self._git_rung_root = None
            self._decline_reason = None
            self._decline_detail = None
            self._legacy_suppressed = None
            self._legacy_suppressed_name = None
        else:
            detection_pass = _DETECTION_COMMON_DIRS.set({})
            git_dir_pass = project_identity.DETECTION_GIT_DIRS.set({})
            answers_pass = _DETECTION_ANSWERS.set({})
            try:
                detected = self._detect_project_identity_with_legacy()
                # THE SUPPRESSION DISCLOSURE ASKS INSIDE THE SAME PASS: its
                # common-dir question is the one detection just answered for
                # the identity root.
                suppression = (
                    _legacy_suppression(detected.identity_root)
                    if detected.legacy_basename is None
                    else (None, None)
                )
            finally:
                self._detection_common_dirs = _DETECTION_COMMON_DIRS.get()
                _DETECTION_COMMON_DIRS.reset(detection_pass)
                project_identity.DETECTION_GIT_DIRS.reset(git_dir_pass)
                _DETECTION_ANSWERS.reset(answers_pass)
            self._project_id = detected.project_id
            self._project_id_source = detected.source
            # The name the PRE-change resolver produced for this location.
            # The read-scope union needs it to find rows filed under the old
            # key; it is carried from the detection pass, never re-derived.
            self._legacy_project_basename = detected.legacy_basename
            self._override_ignored = detected.override_ignored
            # THE ROOT THE WINNING RUNG RESOLVED. The plausibility gate and
            # the override read use this, not the process's cwd repository:
            # in a declared-repo session those differ, and gating on the cwd
            # would exclude the declared repo's own legacy bucket.
            self._project_identity_root = detected.identity_root
            # THE GIT RUNG RESOLVED FROM THE PROCESS'S OWN LOCATION, so its
            # identity root IS the working directory's repository root: the
            # divergence check reuses it instead of asking git again.
            self._git_rung_root = (
                detected.identity_root
                if detected.source in ("origin remote", "git root")
                else None
            )
            # STRUCTURED DECLINE DISCLOSURE, carried from the same pass. None
            # on every resolution that produced an id; otherwise
            # `{source, reason, override_path}` from `decline_record`, for
            # whichever rung declined and why.
            self._decline_reason = detected.decline_reason
            self._decline_detail = detected.decline_detail
            # STRUCTURED SUPPRESSION DISCLOSURE. A submodule admin dir yields
            # no legacy candidate; carry WHY and WHICH name, once, so every
            # later read can report it without another git call.
            self._legacy_suppressed, self._legacy_suppressed_name = suppression
            self._project_id_shape = (
                "canonical"
                if self._project_id is not None and is_canonical(self._project_id)
                else "basename"
                if self._project_id is not None
                else None
            )
            # THE DETECTED ID IS ADOPTED AS FOUND, NOT VALIDATED HERE — the
            # same rule as the detected session id below, for the same
            # reason. A by-id command (`get`/`update`/`delete` with a full
            # 32-character id) never binds this value, so refusing at
            # construction would gate the identity-independent by-id
            # capability on a value those calls do not use, and the CLI would
            # surface it as SYSTEM_ERROR. The public `project_id` property
            # may therefore expose an unusable value; every path that WOULD
            # bind it goes through `_project_scope()` (reads treat it as
            # unresolvable) or `_project_id_for_write()` (writes refuse with
            # the remedy text). Do not re-add the constructor check.
        if session_id:
            # Same branch placement and the same reason as project_id above.
            if _unusable_identifier(session_id):
                raise ValueError(
                    "session_id, when supplied, must be a non-empty string "
                    "without control characters (U+0000-U+001F, "
                    "U+007F-U+009F) or lone surrogates (U+D800-U+DFFF); "
                    "pass nothing to auto-detect the session."
                )
            self._session_id: Optional[str] = session_id
        else:
            # THE DETECTED ID IS ADOPTED AS FOUND, NOT VALIDATED HERE. It is
            # read from the session context file — a value this process did
            # not type — but a malformed one must not gate a READ: `search`,
            # `list`, `get`, `status` and `sync` construct this object and do
            # not bind the session id, so refusing at construction turns a
            # listing or diagnostics command into a crash over a value it
            # never uses. The public `session_id` property may therefore
            # expose an unusable value; every path that WOULD bind it guards
            # at its own ingress: save() validates the adopted value before
            # defaulting it into the row, update() validates any
            # payload-supplied session id, and list(session_only=True)
            # treats an unusable value as "no records from this session"
            # rather than binding it. Do not re-add the constructor check —
            # it is exactly what made reads gate on a write value.
            self._session_id = self._detect_session_id()
        self._db_path = db_path

        # Cache for the cwd's main repo root and its canonical identity, read
        # by the location-divergence check on every save. `False` means "not
        # yet resolved"; None is a real answer (not in a repo). BOTH ARE
        # KEYED ON `_cwd_cache_dir`, the working directory they were derived
        # for: a process that changes directory after construction gets them
        # derived again (`_refresh_cwd_cache`), so a save from another
        # repository still warns. While the directory is unchanged the seed
        # and the cached answers stand, and no git call repeats.
        self._cwd_cache_dir: Optional[Path] = _cwd_or_none()
        self._cwd_repo_root: Any = (
            self._git_rung_root if self._git_rung_root is not None else False
        )
        self._cwd_repo_id: Any = False
        # Set by `_read_project_scopes` when it decides whether a populated
        # legacy bucket joins this read. Process state, not a store write.
        self._legacy_scope_state: Optional[dict] = None

        # Session file tracking (populated by hooks)
        self._session_files: List[str] = []

        # Reason code from the most recent save() or update(), or None when the
        # last write had nothing to report. save() returns a bare memory_id, so
        # this is how a caller reaches the embedding outcome without changing
        # that signature.
        self._last_embedding_status: Optional[str] = None
        # File links the most recent update() left on their old project
        # because their recorded path could not be re-tracked. See
        # _cascade_rekey: a quarantined link is reported, never fatal.
        self._last_cascade_skipped: int = 0

        # Outcome of the most recent save()'s CLAUDE.md sync, or None before
        # any save has run. Unlike the embedding status above, this is set on
        # EVERY save, success included -- see `last_sync_status` for why the
        # two neighbours differ.
        self._last_sync_status: Optional[str] = None

        # Scope disclosure for the most recent COMPLETED save. It is NOT total
        # and does not belong beside `last_sync_status`, which is: that one is
        # set on every branch, refusal included, so its absence has one
        # meaning. This one is assigned only AFTER the store write verifies, so
        # its ABSENCE MEANS THIS PROCESS DID NOT CONFIRM A FILING — no save ran
        # on this instance, or a save ran and exited before its write was
        # verified.
        #
        # ABSENT IS NOT "NOT FILED", AND THE DIFFERENCE IS OBSERVED RATHER THAN
        # THEORETICAL. A save can raise AFTER `create_memory` returned an id
        # and before the read-back succeeds; the row is then IN THE STORE with
        # this field absent. The store write completing and the call still
        # failing is the ordinary shape here, not a corner case — a live hang
        # downstream of the write, in the embedding step, leaves a complete
        # record behind a call that never returned. So absence licenses "I did
        # not confirm a filing" and NOTHING STRONGER. Do not read it as a
        # filing, and do not read it as the absence of one.
        #
        # THE ASYMMETRY IS DELIBERATE AND IT IS THE POINT. Assigning it early
        # would make absence single-valued, at the price of making PRESENCE
        # two-valued: a save that raised at the store write would leave a
        # populated dict byte-identical to a successful one, and a reader could
        # not tell a completed filing from one that never landed. Between a
        # wide honest absence and a narrow lying presence, this field takes the
        # absence — absence sends a reader to look, a false presence answers
        # them.
        #
        # What absence NEVER means, in either state, is "the scope was fine".
        # It reports rather than judges, so it has no false-positive rate by
        # construction.
        self._last_project_scope: Optional[Dict[str, Any]] = None

        logger.debug(
            f"PACTMemory initialized: project={self._project_id}, session={self._session_id}"
        )

    @staticmethod
    def _find_project_root(start: Path) -> Path:
        """
        Walk UP from `start` looking for a project marker; return the first
        marker-containing directory.

        Markers (any of):
        - `.git` (file or dir — submodules use a file)
        - `.claude/` directory
        - `CLAUDE.md` at either supported location (./ or .claude/)

        If no marker is found walking to the filesystem root, returns `start`
        unchanged (fallback to original CWD-basename behavior).

        Args:
            start: Path to begin the walk from (typically Path.cwd()).

        Returns:
            First ancestor (inclusive of `start`) containing a project marker,
            or `start` if none found.
        """
        try:
            current = start.resolve()
        except (OSError, RuntimeError):
            return start
        for parent in [current] + list(current.parents):
            if (parent / ".git").exists():
                return parent
            if (parent / ".claude").is_dir():
                return parent
            if (parent / "CLAUDE.md").exists():
                return parent
            if (parent / ".claude" / "CLAUDE.md").exists():
                return parent
        return start  # fallback: use original

    @staticmethod
    def _legacy_project_name(
        common_dir: Optional[Path], root: Optional[Path]
    ) -> Optional[str]:
        """The PRE-change resolver's output for this location.

        It is `common_dir.resolve().parent.name`, which is what the old
        resolver returned, NOT the new identity root's name. The two differ
        exactly where this matters: a submodule's common dir is
        `<super>/.git/modules/vendor/sub`, so the old bucket is `vendor` while
        the new identity root is `sub`; an external `--separate-git-dir` puts
        the common dir outside the working tree. Unioning on the NEW name
        would look for a bucket that never existed and report no candidate at
        all — silent loss of every pre-upgrade row.

        A SUBMODULE ADMIN DIR YIELDS NO CANDIDATE AT ALL. The name there is
        chosen by the superproject's tracked `.gitmodules` (`modules`,
        `vendor`, `checks` — whatever path the superproject writes), so it is
        a REPOSITORY-CHOSEN BUCKET NAME and admitting it to the union would
        let a checkout read another project's memories. An ordinary
        `--separate-git-dir` checkout is NOT suppressed: its common dir is an
        arbitrary external path, not `<...>/.git/modules/<name>`, and it
        keeps its pre-upgrade name. The suppression is disclosed by the read
        scope when the bucket holds rows (`_suppressed_bucket_rows`).
        """
        if common_dir is not None:
            if _submodule_admin_dir(common_dir):
                # Disclosed by the read scope, and only when the bucket holds
                # rows: see `_suppressed_bucket_rows`.
                return None
            try:
                return common_dir.resolve().parent.name
            except (OSError, RuntimeError, ValueError):
                return common_dir.parent.name
        if root is not None:
            return Path(root).name
        return None

    @staticmethod
    def _warn_override_ignored(identity: "RootIdentity") -> None:
        """Report an unusable override once, at the rung that read it."""
        ignored = identity.override_ignored
        if not ignored:
            return
        if ignored.get("path") is None:
            # THE PATH IS WITHHELD, NOT THE REPORT: the checkout's `.git` is
            # a redirect, or points at an outside git directory that is no
            # linked worktree's or submodule's admin dir.
            logger.warning(
                "ignoring an unusable project override (%s) in the git "
                "directory this checkout's `.git` points at; resolution "
                "continues without it. Pinning that directory is a user "
                "decision: see the pact-memory skill.",
                ignored.get("reason"),
            )
            return
        logger.warning(
            "ignoring the project override at %r (%s); resolution continues "
            "without it.",
            ignored.get("path"),
            ignored.get("reason"),
        )

    @staticmethod
    def _declared_dir_identity(declared_dir: str, source: str) -> "_DetectedIdentity":
        """The identity a DECLARED directory belongs to, as one record.

        Shared by Strategy 1 (CLAUDE_PROJECT_DIR) and Strategy 1.5 (the
        session record). The identity root is the repository root of the
        declared directory — a worktree main root, a recorded checkout, or the
        declared path itself — and it is carried so the plausibility gate and
        the override read use the SAME root the identity came from.

        The legacy basename is `common_dir.resolve().parent.name` whenever a
        common dir exists, because that is what the PRE-change resolver
        produced; see `_legacy_project_name`.

        A declared directory that no longer exists declines. A declaration
        whose repository has a remote that could not be read or represented also
        declines: never a basename for a real but unrepresentable remote,
        because a basename there is a silently different bucket. A decline
        keeps the declaration's own label as `source`.
        """
        try:
            declared_root = Path(declared_dir).resolve()
        except (OSError, RuntimeError, ValueError):
            # ValueError: a NUL byte (or another embedded invalid value) in a
            # session record must read as "cannot resolve", not crash the
            # resolver.
            declared_root = None
        declared_common_dir = main_repo_common_dir(declared_dir)
        declared_main_root = main_repo_root(declared_dir, declared_common_dir)
        # WHAT THE PRE-CHANGE RESOLVER NAMED THIS DECLARATION, carried on every
        # result below that the pre-change resolver would also have named.
        pre_change = _pre_change_declared_name(
            declared_dir, declared_root, declared_common_dir
        )

        def done(detected: "_DetectedIdentity") -> "_DetectedIdentity":
            return detected._replace(pre_change_name=pre_change)

        # Compare via normcase so a case-insensitive filesystem does not fire
        # the rewrite for paths that differ only in case.
        if (
            declared_main_root is not None
            and declared_root is not None
            and os.path.normcase(str(declared_main_root))
            != os.path.normcase(str(declared_root))
        ):
            return done(PACTMemory._identity_at_root(
                declared_main_root,
                declared_common_dir,
                asked_root=declared_root or Path(declared_dir),
                source=source,
                label=source,
            ))
        # A FORGED WORKTREE-ADMIN POINTER DECLINES HERE. `main_repo_root`
        # correctly refuses the redirect and returns None, but the fall-through
        # below would then hand `detect_id_for_root` the DECLARED directory
        # paired with the VICTIM's common dir, and the origin read binds the
        # victim. The asked-root predicate cannot see this shape: the target's
        # basename is the worktree's name, not `.git`, and it is not under
        # `.git/modules/`. Measured: both the declared root and a declared
        # subdirectory bound `github.com/victim/secret-lib`.
        #
        # A BARE repository also has `main_repo_root() is None`, but its git
        # dir EQUALS its common dir, so `_worktree_redirect` answers False; the
        # guard declines it `no_working_tree`, exactly as on every rung.
        live_root = declared_main_root or declared_root or Path(declared_dir)
        if declared_main_root is None and declared_root is not None:
            # A `.git`-MARKED BUT UNANSWERABLE declared directory declines for
            # the same reason as the git rung; a non-git declared path has no
            # marker and keeps the designed basename. `toplevel=None`:
            # `main_repo_root` found no root, so git gave none.
            guarded = _root_guard(
                declared_root, declared_common_dir, source, label=source,
                root=live_root if declared_common_dir is None else None,
                toplevel=None,
                no_working_tree_detail=(
                    f"declared via {source}: {declared_dir!r} is a bare repository"
                ),
            )
            if guarded is not None:
                return done(guarded)

        # GONE-DECLARATION ARM. Reached only when the live rewrite could not
        # fire AND the declared path does not resolve to an existing target.
        if not _declared_path_exists(declared_dir):
            # Function-level: the shared package is importable only after
            # pact_session's sys.path bootstrap has run at module import.
            from shared.project_scope import recorded_main_root

            # AN INVALID DECLARATION MUST NOT CRASH THE RESOLVER. A corrupt
            # session record can carry a NUL byte, which `os.path.realpath`
            # rejects with ValueError. Catch it HERE, at the boundary this
            # module owns.
            try:
                recorded_root = recorded_main_root(
                    Path(declared_dir),
                    get_worktree_identity_from_session_record(),
                )
            except (OSError, RuntimeError, ValueError):
                recorded_root = None
            if recorded_root is None:
                return _decline(
                    source,
                    "declaration_unresolved",
                    label=source,
                    detail=(
                        f"declared via {source}: {declared_dir!r} does not exist "
                        "and no verified worktree identity record names it; no "
                        "name was derived from the path"
                    ),
                )
            recorded_label = "worktree identity record"
            # The pre-change resolver named a recorded declaration by the
            # recorded checkout itself.
            pre_change = recorded_root.name
            recorded_common_dir = main_repo_common_dir(str(recorded_root))
            # The same guards as every other rung, on the recorded checkout.
            guarded = _root_guard(
                recorded_root, recorded_common_dir, recorded_label,
                label=recorded_label, root=recorded_root,
            )
            if guarded is not None:
                return done(guarded)
            # The record's own label is disclosed; a decline's record names the
            # rung that asked, as it always has. The identity read asks about
            # the recorded checkout, the root the guard just judged: the
            # declared directory is gone, so a `.git` predicate evaluated on
            # it sees no marker and passes whatever the recorded root holds.
            return done(PACTMemory._identity_at_root(
                recorded_root,
                recorded_common_dir,
                asked_root=recorded_root,
                source=source,
                label=recorded_label,
            ))
        return done(PACTMemory._identity_at_root(
            live_root, declared_common_dir, asked_root=Path(declared_dir),
            source=source, label=source,
        ))

    @staticmethod
    def _identity_at_root(
        root: Path,
        common_dir: Optional[Path],
        *,
        asked_root: Optional[Path],
        source: str,
        label: str,
        decline_label: Optional[str] = None,
    ) -> "_DetectedIdentity":
        """Detect the identity of one established root and wrap it as a record.

        `label` is the disclosed source when an id is found (an override pin
        discloses `override file` instead); `decline_label` is the disclosed
        source when the root declines (defaults to `label`).

        A ROOT THAT IS THE USER'S HOME IS A HOME LANDING on every rung that
        reaches it, declared or discovered, repository or not: it warns and
        keeps the folder name, and is never canonicalised. A dotfiles home
        would otherwise give every directory under it that repository's id.
        """
        legacy_name = PACTMemory._legacy_project_name(common_dir, root)
        if _is_home(root):
            _warn_home_landing(root)
            return _DetectedIdentity(
                project_id=root.name,
                source=label,
                legacy_basename=legacy_name,
                override_ignored=None,
                identity_root=root,
            )
        identity = detect_id_for_root(root, common_dir, asked_root=asked_root)
        PACTMemory._warn_override_ignored(identity)
        if identity.project_id is not None:
            logger.debug(
                "project_id detected from %s: %s", source, identity.project_id
            )
            return _DetectedIdentity(
                project_id=identity.project_id,
                source=_disclosed_source(identity, label),
                legacy_basename=legacy_name,
                override_ignored=identity.override_ignored,
                identity_root=root,
            )
        return _decline_detected(
            identity,
            source,
            label=decline_label or label,
            root=root,
            legacy=legacy_name,
        )

    @staticmethod
    def _detect_project_identity_with_legacy() -> "_DetectedIdentity":
        """Detect the project identity, its provenance, and the legacy name.

        The precedence is `supplied` (handled by the constructor, never here)
        -> CLAUDE_PROJECT_DIR -> session record -> git root -> user setting ->
        cwd marker. The repository override is NOT a rung of its own: it
        follows the identity root, so each root rung reads its own
        `<git-common-dir>/pact-project-id`. That is what stops a cwd pin from
        retargeting a declared session, and what stops a declared repo's pin
        from being silently ignored.

        A declaration that is PRESENT but cannot be honoured DECLINES before
        the user-scope setting is consulted: a stale declaration must not be
        silently absorbed by a user setting. The setting answers only when no
        declaration was present at all.
        """
        # A DECLARATION THAT WAS PRESENT BUT NAMED NO PROJECT. Local only: no
        # instance, module or thread state.
        declined_declaration = False
        # The STRUCTURED reason from the highest-priority declaration that
        # declined, so `project-id show` can name it when no later rung
        # answers. First non-None wins: the env declaration outranks the
        # session record.
        declined_reason: Optional[dict] = None
        # THE ROOT A DECLINED DECLARATION NAMED, carried so the folder-name
        # fallback derives from the winning rung's location rather than the
        # process cwd. First non-None wins for the same reason.
        declined_root: Optional[Path] = None

        # Strategy 1: Environment variable (original behavior)
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            detected = PACTMemory._declared_dir_identity(project_dir, "CLAUDE_PROJECT_DIR")
            if detected.project_id:
                return detected
            if _is_live_decline(detected):
                # A LIVE declaration that declines ENDS resolution.
                return detected._replace(source="unresolved")
            declined_reason = declined_reason or detected.decline_reason
            declined_root = declined_root or detected.identity_root
            declined_declaration = True

        # Strategy 1.5: session record (inert under pytest — the discovery
        # refuses test processes — so the replica in test_project_id.py needs
        # no record leg to stay equivalent here).
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            detected = PACTMemory._declared_dir_identity(record_dir, "session record")
            if detected.project_id:
                return detected
            if _is_live_decline(detected):
                # A LIVE declaration that declines ENDS resolution.
                return detected._replace(source="unresolved")
            declined_reason = declined_reason or detected.decline_reason
            declined_root = declined_root or detected.identity_root
            declined_declaration = True

        # Strategy 2: Git repository root (worktree-safe)
        cwd_common_dir = main_repo_common_dir()
        if cwd_common_dir is not None:
            # The pre-change resolver named every repository it reached here,
            # bare and redirected included, by its common dir's parent.
            git_pre_change = cwd_common_dir.parent.name or None
            repo_root = main_repo_root(common=cwd_common_dir)
            if repo_root is None:
                # NO ROOT: a forged worktree pointer, or a bare repository.
                # The guard tells them apart, so a redirect is reported as a
                # redirect and only a real bare repository as having no
                # working tree. With a common dir and `toplevel=None` the
                # guard always declines, so the result is never None here.
                guarded = cast("_DetectedIdentity", _root_guard(
                    _cwd_or_none(), cwd_common_dir, "git root",
                    toplevel=None,
                    no_working_tree_detail=(
                        "the process's repository is bare, or git cannot "
                        "describe its working tree"
                    ),
                ))
                return guarded._replace(pre_change_name=git_pre_change)
            detected = PACTMemory._identity_at_root(
                repo_root,
                cwd_common_dir,
                asked_root=_cwd_or_none(),
                source="git root",
                label="git root",
                decline_label="unresolved",
            )._replace(pre_change_name=git_pre_change)
            if detected.project_id is not None:
                # THE DISCLOSURE DECISION LIVES IN ONE PLACE: a canonical id
                # discloses its remote, a pin discloses the override file,
                # and anything else is the git root's folder name.
                if detected.source == "git root" and is_canonical(detected.project_id):
                    return detected._replace(source="origin remote")
                return detected
            # A REAL remote that cannot be represented. Falling through to a
            # basename would file this project under a different bucket from
            # the one its remote names, which is the defect this change
            # exists to remove.
            return detected
        # A `.git`-MARKED DIRECTORY WHERE GIT CANNOT ANSWER DECLINES. Falling
        # through to a folder name here files the project in a silently
        # different bucket. git absent from PATH, a corrupted or partial
        # `.git`, or a timeout all take this arm; a genuinely non-git umbrella
        # has no marker and keeps its designed basename fallback.
        broken_start = _cwd_or_none()
        if _unanswerable_repository(broken_start) is not None:
            # With no declaration the pre-change resolver fell through to the
            # cwd marker and named it; with one, it refused.
            return _decline(
                "git root",
                "broken_git_marker",
                pre_change_name=(
                    PACTMemory._find_project_root(broken_start).name or None
                    if not declined_declaration and broken_start is not None
                    else None
                ),
                detail="the working directory is inside that work tree",
            )
        # git not installed, not a repo, or command timed out
        logger.debug("Git detection failed, falling back to cwd")

        # A PRESENT DECLARATION THAT COULD NOT BE HONOURED DECLINES HERE, and
        # BEFORE the user setting: absorbing a stale declaration into a
        # user-scope pin hides the failure instead of reporting it. Strategy 3
        # is skipped for the same reason it always was — it names the PROCESS,
        # not the session.
        if declined_declaration:
            logger.warning(
                "project_id unresolved: the git root did not answer either, "
                "so Strategy 3 is skipped and no project scope was derived."
            )
            return _DetectedIdentity(
                project_id=None,
                source="unresolved",
                legacy_basename=None,
                override_ignored=None,
                identity_root=declined_root,
                decline_reason=declined_reason,
            )

        # Strategy 2.5: the USER-SCOPE setting. Reached only when NO
        # declaration was present and no repository answered. A repo-scoped
        # override and this setting can therefore never disagree within one
        # resolution — that is why there is no disagreement warning to emit.
        user_setting = read_user_setting(get_user_memory_dir() / "config.json")
        if user_setting is not None:
            # THE PRE-CHANGE VALUE IS STILL COMPUTABLE, so it must be carried.
            # Dropping it would silently strand every row filed under the
            # folder name (the union has no legacy candidate to widen to) and
            # would make `_detect_project_basename()` return None, which makes
            # the backlog writer refuse to write at all. `_find_project_root`
            # falls back to the cwd itself, which is exactly the value the
            # pre-change resolver produced for this location.
            # A DELETED CWD MUST NOT CRASH CONSTRUCTION. `Path.cwd()` raises
            # FileNotFoundError once the directory is gone, and this rung is
            # reachable in exactly that state (no declaration, no repository).
            setting_start = _cwd_or_none()
            setting_root = (
                PACTMemory._find_project_root(setting_start)
                if setting_start is not None
                else None
            )
            return _DetectedIdentity(
                project_id=user_setting,
                source="user setting",
                legacy_basename=setting_root.name if setting_root is not None else None,
                override_ignored=None,
                identity_root=setting_root,
                pre_change_name=setting_root.name if setting_root is not None else None,
            )

        # Strategy 3: Current working directory — walk UP to nearest project
        # marker. A walk that lands on HOME WARNS and never canonicalises.
        try:
            cwd_start = _cwd_or_none()
            if cwd_start is None:
                return _DetectedIdentity(
                    project_id=None, source="unresolved", legacy_basename=None,
                    override_ignored=None, identity_root=None,
                )
            cwd_root = PACTMemory._find_project_root(cwd_start)
            cwd_name = cwd_root.name
            if cwd_name:
                if _is_home(cwd_root):
                    _warn_home_landing(cwd_root)
                    return _DetectedIdentity(
                        project_id=cwd_name, source="cwd", legacy_basename=cwd_name,
                        override_ignored=None, identity_root=cwd_root,
                        pre_change_name=cwd_name,
                    )
                cwd_common_dir = main_repo_common_dir(str(cwd_root))
                # The same guards as the declared rungs: a marker without a
                # git answer, the forged worktree-admin shape, and a
                # repository with no working tree (`main_repo_root` is not
                # called on this rung, so the guard asks git). This rung
                # meets a `.git` marker when a `GIT_CEILING_DIRECTORIES` entry
                # above the cwd hid the checkout from git and from
                # `find_git_marker`: `_find_project_root` does not stop at a
                # ceiling, and `git -C <root>` examines the root itself.
                guarded = _root_guard(cwd_root, cwd_common_dir, "cwd", root=cwd_root)
                if guarded is not None:
                    return guarded._replace(pre_change_name=cwd_name)
                return PACTMemory._identity_at_root(
                    cwd_root,
                    cwd_common_dir,
                    asked_root=cwd_root,
                    source="cwd",
                    label="cwd",
                    decline_label="unresolved",
                )._replace(pre_change_name=cwd_name)
        except OSError:
            logger.debug("Failed to detect project_id from cwd")

        return _DetectedIdentity(
            project_id=None, source="unresolved", legacy_basename=None,
            override_ignored=None, identity_root=None,
        )

    @staticmethod
    def _detect_project_id() -> Optional[str]:
        """Project id only — the long-standing signature, kept verbatim.

        Callers outside this module (and the precedence pins in
        test_project_dir_resolution.py) expect a bare string, so the richer
        variant is added BESIDE this rather than by changing this return type.
        """
        return PACTMemory._detect_project_id_with_source()[0]

    @staticmethod
    def _detect_project_basename() -> Optional[str]:
        """The FOLDER-NAME id the PRE-change resolver produced here, or None.

        For callers that use the id as a FILE NAME. A canonical id contains
        `/`, so using one as a path component silently nests the file and
        orphans every existing `<basename>.json` — the backlog does exactly
        that, so it asks for this shape explicitly.

        It returns the pre-change value AND NOTHING ELSE: where the pre-change
        resolver refused, this refuses too, so the backlog writer refuses in
        exactly the states it always did. No walk of the cwd or of the
        identity root stands in for a missing value.
        """
        return PACTMemory._detect_project_identity_with_legacy().pre_change_name

    @staticmethod
    def _detect_project_id_with_source() -> "tuple[Optional[str], str]":
        """Detect the identity and name the rung that decided it.

        Detection order:
        1. CLAUDE_PROJECT_DIR environment variable (original behavior)
        1.5. Session record — the project_dir session_init persisted at
           SessionStart. BELOW env (a present declaration wins) and ABOVE git:
           in a multi-repo workspace the cwd's git root can be the WRONG
           scope, so the session's own recorded identity outranks it.
        2. Git repository root (worktree-safe)
        3. The user-scope setting, then the cwd marker.

        A declaration that is present but cannot be honoured declines. `source`
        is one of `supplied`, `override file`, `CLAUDE_PROJECT_DIR`,
        `session record`, `worktree identity record`, `origin remote`,
        `git root`, `user setting`, `cwd`, `unresolved`.
        """
        detected = PACTMemory._detect_project_identity_with_legacy()
        return detected.project_id, detected.source


    @staticmethod
    def _detect_session_id() -> Optional[str]:
        """Detect session ID from PACT context file."""
        return get_session_id_from_context_file() or None

    @property
    def project_id(self) -> Optional[str]:
        """Get the current project ID.

        A DETECTED value is exposed AS FOUND and may be unusable (a control
        character, a lone surrogate); a SUPPLIED constructor value was
        validated and is always usable. This asymmetry is deliberate: by-id
        commands (`get`/`update`/`delete` with a full 32-character id) and
        most reads construct a PACTMemory without binding the project id, so
        the constructor must not refuse a value those calls never use. Every
        path that WOULD bind the value goes through `_project_scope()` (reads
        treat it as unresolvable) or `_project_id_for_write()` (writes refuse
        with remedy text).
        """
        return self._project_id

    def _detected_project_id_is_unusable(self) -> bool:
        """Whether the ADOPTED project id cannot bind to a utf-8 TEXT column.

        A SUPPLIED value is validated in the constructor, so this can only be
        true for a DETECTED one: a control character or lone surrogate from
        an environment, git or cwd-derived basename, which
        `_unusable_identifier` is the single rule for.
        """
        return (
            self._project_id is not None
            and _unusable_identifier(self._project_id)
        )

    def _project_scope(self) -> Optional[str]:
        """The project id a READ may bind, or None when identity is unusable.

        "Unusable" covers BOTH no detected identity and a detected value that
        cannot be bound. Callers treat None as no records — never as "every
        project" — so a malformed directory name degrades a project-scoped
        read to the empty answer instead of raising from the TEXT binding.
        """
        if self._detected_project_id_is_unusable():
            return None
        return self._project_id

    def _schema_present(self) -> bool:
        """Whether this READ may proceed against the store.

        "Zero CREATE" is SCOPED TO AN EXISTING STORE (the accepted ruling).
        An existing store without a schema (0 bytes, a truncated file) answers
        the empty result and is never initialised by a read. An ABSENT store
        is decided by ORIGIN: on the DERIVED route the first memory operation
        bootstraps directory + file + schema — the pre-existing initialisation
        path, a separately asserted exception the security change did not take
        on — while a store the caller NAMED answers empty and is not created.
        """
        from .database import get_db_path

        try:
            # `get_db_path` performs the derived DIRECTORY creation as a path
            # side effect; the store file/schema decision is separate.
            store = get_db_path()
        except (OSError, RuntimeError, ValueError):
            store = self._db_path or resolve_db_path()
        if not Path(store).exists():
            return store_path_origin() in DERIVED_STORE_ORIGINS
        return _store_schema_present(str(store))

    def _read_project_scopes(self, conn: Any = None) -> tuple:
        """The project ids a READ may bind: the canonical id plus its legacy bucket.

        Returns `()` when `_project_scope()` is None: an EMPTY scope, which
        every store, graph and search seam treats as no rows. It is never
        None, because None is the library's whole-store value and a read with
        no identity must not reach a query as an unscoped filter. A
        NON-canonical id (a supplied
        value, or a legacy basename) is a one-element scope: there is no
        canonical/legacy split to reconcile.

        For a canonical id, the PRE-change basename joins the scope ONLY when
        its bucket has rows and either the plausibility gate verifies it, or
        the gate finds it unverified and a recorded session from this
        repository wrote into it (`_legacy_session_evidence`). An excluded or
        unproven bucket is not read.
        The candidate is matched exact-case first and folded only as a
        fallback, and only when the identity root's filesystem PROVABLY
        folds case (`project_identity.filesystem_case_insensitive`): a
        folded-first lookup passes on a case-insensitive filesystem and
        misses on a case-sensitive one, and on a case-sensitive filesystem
        the two spellings are two different projects. The value returned is
        the STORED spelling, byte for byte — folding finds a candidate, it
        never produces one.

        READS NEVER WRITE. An absent store returns the canonical scope alone
        rather than being created, and when `conn` is not supplied the probe
        opens the store READ-ONLY, so a read never runs `init_schema`.

        WHAT DEGRADES AND WHAT RAISES. A store with no schema yet, or one that
        vanished between the presence check and the open, is DEGRADED, not
        fatal: the scope narrows to the canonical id, the state is disclosed
        in `_legacy_scope_state` (`degraded` is `no schema` or
        `store_unreadable`), and the result is not cached. A store that is
        PRESENT but cannot be opened, or is not a readable SQLite database,
        RAISES `StoreFaultError` with the remedy named: reporting the
        canonical scope alone for it would hide every legacy row it holds.

        THE CACHE KEY STATS BOTH THE DATABASE AND ITS `-wal` SIBLING. The
        store runs in WAL mode, and another process's commit can land only in
        the `-wal` file, leaving the main file's `(mtime_ns, size)` unchanged
        — a long-lived session would then keep serving a stale legacy
        membership after a rekey elsewhere, which is the silent-loss class
        this change exists to remove. `-shm` is deliberately EXCLUDED: it is
        shared-memory churn and would defeat the cache with spurious
        recomputes. An in-process write clears the cache as well, so a save
        that changes the bucket set is observed immediately.
        """
        self._legacy_scope_state = None
        scope = self._project_scope()
        if scope is None:
            return ()
        if not is_canonical(scope):
            return (scope,)
        legacy = self._legacy_project_basename
        store = str(self._db_path or resolve_db_path())
        if not legacy or legacy == scope:
            if self._legacy_suppressed is not None:
                # STRUCTURED SUPPRESSION DISCLOSURE: the operator sees that a
                # candidate existed, what it was, why it is not read and how
                # many rows it holds. Nothing is read from it.
                self._legacy_scope_state = _legacy_scope_record(
                    suppressed={
                        **self._legacy_suppressed,
                        "memory_count": _suppressed_bucket_rows(
                            store, self._legacy_suppressed_name
                        ),
                    },
                    suppressed_candidate=self._legacy_suppressed_name,
                )
            return (scope,)
        stat_key = _store_stat_key(store)
        if stat_key is None:
            # Absent store: nothing to reconcile and nothing to create.
            return (scope,)
        # THE IDENTITY ROOT IS THE THIRD INPUT TO THE DECISION, so it belongs
        # in the key: the plausibility verdict is computed against it, and two
        # same-named checkouts of one repository share store, scope and legacy
        # while gating against DIFFERENT roots. Omitting it serves one root's
        # include/exclude verdict for the other.
        cache_key = (store, stat_key, str(self._project_identity_root), scope, legacy)
        cached = _LEGACY_SCOPE_CACHE.get(cache_key)
        if cached is not None:
            self._legacy_scope_state = cached[1]
            return cached[0]

        from .database import (
            count_project_memories,
            legacy_candidate_plausibility,
            project_bucket_counts,
        )

        # THE IDENTITY ROOT OF THE WINNING RUNG, not the process's cwd
        # repository. In a declared-repo session those differ, and gating on
        # the cwd would exclude the declared repo's OWN legacy bucket while
        # advising a rekey the operator does not need.
        repo_root = self._project_identity_root
        # BIND THE RESOLVED STORE EXPLICITLY. A pathless `db_connection()` only
        # inherits an enclosing scope; a direct call outside one (the CLI verb
        # is scoped, but a library caller is not) would read the DEFAULT store
        # while the stat above inspected the caller's. The two must be the
        # same file.
        with _legacy_probe_context(store, conn) as (connection, degraded_reason):
            if connection is None:
                # FAIL-SOFT, DISCLOSED. The store has no schema yet, or it
                # vanished before the read-only open, so the legacy bucket
                # cannot be judged. Narrow to the canonical scope and say so;
                # nothing was written and the degraded result is NOT cached,
                # so a store that appears is observed on the next read. A
                # present store that cannot be opened never reaches here:
                # the probe raises StoreFaultError.
                logger.warning(
                    "the legacy project bucket could not be checked: the store "
                    "at %r has no schema yet or was not there to open (%s). "
                    "Reads are limited to the canonical project id %r until "
                    "the store holds memories.",
                    store,
                    degraded_reason,
                    scope,
                )
                self._legacy_scope_state = _legacy_scope_record(
                    degraded=(
                        "no schema"
                        if degraded_reason == "no schema"
                        else "store_unreadable"
                    ),
                )
                return (scope,)
            buckets = project_bucket_counts(connection)
            # The canonical count is what lets a SAVE disclose that it landed
            # in a bucket with no prior rows. The in-process cache makes a
            # second READ cheap, but on a saving process every save/update/
            # delete clears `_LEGACY_SCOPE_CACHE`, so both COUNTs and the
            # `git worktree list` below re-run after each write — that is the
            # accepted cost, not a once-per-process constant.
            canonical_count = count_project_memories(connection, scope)
            # THE FOLD IS GATED ON THE FILESYSTEM, NOT ON `verified`: see
            # `_match_legacy_candidate`. The predicate is called as a module
            # attribute so a test can substitute it.
            allow_fold = project_identity.filesystem_case_insensitive(repo_root)
            candidate, ambiguous = _match_legacy_candidate(
                legacy, buckets, allow_fold=allow_fold
            )
            # THE FOLD SUPPRESSION IS SILENT LOSS UNLESS DISCLOSED. On a
            # case-sensitive filesystem a bucket that differs from the legacy
            # basename only by case is never read — and it may be the
            # operator's own pre-upgrade bucket, filed on a machine whose
            # filesystem folded case. Report it as PRESENT BUT NOT INCLUDED
            # with the literal rekey command, while never reading it. It is
            # NOT the `excluded` state: the plausibility gate never judged it.
            case_variants = (
                sorted(
                    bucket
                    for bucket in buckets
                    if bucket.casefold() == legacy.casefold()
                )
                if candidate is None and not allow_fold
                else []
            )
            # MEASURED WHENEVER A CANDIDATE EXISTS, not only when the canonical
            # bucket is empty: a few canonical rows hiding a large legacy
            # bucket is exactly the case the file count is judged by. `None`
            # still means "no candidate", never zero.
            file_paths = (
                count_project_file_paths(connection, candidate)
                if candidate is not None
                else None
            )
            if candidate is None:
                result = (scope,)
                state = _legacy_scope_record(
                    ambiguous=ambiguous,
                    canonical_count=canonical_count,
                    case_variant=case_variants,
                    case_variant_rekey=[
                        runnable_rekey_command(bucket, scope, db_path=store)
                        for bucket in case_variants
                    ],
                )
                if case_variants:
                    _warn_case_variant(store, legacy, case_variants, scope)
            else:
                # A SET OF ROOTS, NOT ONE, AND ROOTED AT THE IDENTITY ROOT.
                # A linked worktree's identity root is the MAIN checkout (by
                # design), while a row saved from the worktree records the
                # WORKTREE's absolute path, so gating on the main checkout
                # alone excludes the operator's own pre-upgrade bucket.
                # `git worktree list` run AT `repo_root` returns that
                # repository's main checkout plus each of its worktrees —
                # exactly the set the gate needs.
                #
                # NOT `backlog.checkout_roots()`, DELIBERATELY: that helper
                # resolves against the PROCESS, so in a declared-repo session
                # it appends the cwd repository's checkouts to the declared
                # one's gate, and it refuses with a write-oriented error that
                # must never escape a read.
                gate_roots = []
                # The common dir GIT reported for the identity root. It anchors
                # the worktree listing and the gate's submodule exemption, and
                # its `description` dates the repository for session evidence;
                # None (git failed) only narrows the gate, never fails the read.
                gate_common = None
                if repo_root is not None:
                    try:
                        # THE DETECTION PASS ASKED THIS ALREADY. Its answer for
                        # the same directory is taken ONCE, by the first read;
                        # every later read asks git afresh.
                        passed = getattr(self, "_detection_common_dirs", None) or {}
                        key = os.path.realpath(str(repo_root))
                        gate_common = (
                            passed.pop(key) if key in passed
                            else main_repo_common_dir(str(repo_root))
                        )
                    except Exception:
                        gate_common = None
                    # ONLY TWO-WAY-LINKED worktrees widen the gate: a listed
                    # entry is what the repository's own `.git` CLAIMS, and a
                    # delivered `.git` can claim another project's checkout.
                    try:
                        gate_roots.extend(
                            str(root)
                            for root in _verified_worktree_roots(
                                Path(repo_root), gate_common
                            )
                        )
                    except Exception:
                        # A listing failure must narrow the gate to the
                        # identity root alone, never fail the read.
                        gate_roots = [str(Path(repo_root).resolve())]
                deduped = list(dict.fromkeys(gate_roots))
                outcome, detail = legacy_candidate_plausibility(
                    connection, candidate, deduped or None, root_common=gate_common
                )
                # ADMISSION. `verified` is read; `excluded` is not. An
                # UNVERIFIED bucket (no absolute file row places it) is read
                # only when a recorded session from one of these roots wrote
                # into it: its folder name alone cannot tell this checkout's
                # rows from a same-named checkout's.
                session_evidence = (
                    _legacy_session_evidence(
                        connection, candidate, deduped, gate_common
                    )
                    if outcome == "unverified"
                    else None
                )
                included = outcome == "verified" or bool(session_evidence)
                unproven = outcome == "unverified" and not session_evidence
                memory_count = buckets.get(candidate, 0)
                state = _legacy_scope_record(
                    candidate=candidate,
                    memory_count=memory_count,
                    # DISTINCT PATHS, not rows: one path can carry a row in
                    # each bucket of a union.
                    file_paths=file_paths,
                    # An EXCLUDED bucket's rows belong elsewhere, so it gets
                    # no runnable command anywhere.
                    rekey=(
                        None
                        if outcome == "excluded"
                        else runnable_rekey_command(candidate, scope, db_path=store)
                    ),
                    included=included,
                    verified=outcome == "verified",
                    included_unverified=included and outcome != "verified",
                    # LOAD-BEARING: the exclusion disclosure fires on this
                    # field. Without it a populated legacy bucket can be
                    # dropped with no notice at all.
                    excluded=outcome == "excluded",
                    unproven=unproven,
                    session_evidence=session_evidence,
                    ambiguous=ambiguous,
                    canonical_count=canonical_count,
                    detail=detail,
                )
                if outcome == "excluded":
                    _warn_legacy_excluded(
                        store, candidate, memory_count,
                        file_paths
                        if file_paths is not None
                        else count_project_file_paths(connection, candidate),
                        repo_root,
                    )
                elif unproven:
                    _warn_legacy_unproven(
                        store, candidate, scope, memory_count, file_paths or 0
                    )
                result = (scope, candidate) if included else (scope,)
        self._legacy_scope_state = state
        # PRUNE superseded stamps for this same store/scope/legacy pair: the
        # key carries the store's stat, so every write leaves the old entry
        # behind and the dict would grow for the process's lifetime.
        for stale in [
            key for key in _LEGACY_SCOPE_CACHE
            if key != cache_key and key[:1] == (store,) and key[3:] == (scope, legacy)
        ]:
            _LEGACY_SCOPE_CACHE.pop(stale, None)
        _LEGACY_SCOPE_CACHE[cache_key] = (result, state)
        return result

    def _detected_identity_is_canonical(self) -> bool:
        """Whether this instance DETECTED a canonical id (not a supplied one)."""
        return (
            self._project_id_source != "supplied"
            and self._project_id is not None
            and is_canonical(self._project_id)
        )

    def _refuse_legacy_write(self, what: str) -> None:
        """Refuse a canonical session's write into a non-canonical bucket.

        AFTER THE UPGRADE ONLY A BASENAME-IDENTITY SESSION WRITES A BASENAME
        BUCKET. A canonical session that filed (or relabelled) a row there
        could create the session evidence that admits that bucket to its own
        reads, so a delivered checkout sharing a folder name could read
        another project's memories.
        """
        raise ValueError(
            f"{what}: this session's project id is {self._project_id!r} (a "
            "repository identity), and a repository session does not write "
            "into a folder-name bucket. Nothing was written. To move rows, "
            "run `project-id rekey --from=FOLDER_NAME --to=host/owner/repo`; "
            "to write a folder-name bucket, save from a folder whose project "
            "id is that name (`project-id show` reports it)."
        )

    def _project_id_for_write(self) -> Optional[str]:
        """The project id a WRITE may bind; ValueError for an unusable one.

        This is the write ingress's remedy channel: a detected value that no
        utf-8 TEXT column can hold refuses HERE, naming the detection source
        and the directory to fix, rather than dying later as an unhandled
        UnicodeEncodeError. A SUPPLIED value was validated already.
        """
        if self._detected_project_id_is_unusable():
            raise ValueError(
                "the project id detected from "
                f"{self._project_id_source!r} ({self._project_id!r}) is not a "
                "non-empty string free of control characters and lone "
                "surrogates; refusing to write it. Supply project_id "
                "explicitly, or fix the project directory name or session "
                "record it was derived from. Nothing was written."
            )
        return self._project_id

    def _session_id_for_envelope(self) -> Optional[str]:
        """The session id a status ENVELOPE reports; the FINAL value.

        One contract with `_project_id_for_envelope()`: FOLD ONLY. A value
        `_unusable_identifier` rejects (a control character or a lone
        surrogate) becomes None, because no row can carry it. Everything
        else is returned RAW: this is a structured payload value the caller
        may feed back as an identifier, and escaping it here made the
        reported id name no rows. Display escaping belongs to messages,
        warnings and log lines, not to payloads.
        """
        if (
            self._session_id is not None
            and _unusable_identifier(self._session_id)
        ):
            return None
        if self._session_id is None:
            return None
        return self._session_id

    def _project_id_for_envelope(self) -> Optional[str]:
        """The project id a status or sync ENVELOPE reports.

        Fold only: `_project_scope()` yields None for an unusable detected
        value or for no identity at all. The surviving value is returned
        RAW — it is a structured payload the caller may feed back as a
        scope, and the CLI's sync envelope uses this same helper so status
        and sync always agree. Display escaping stays in messages, warnings
        and log lines.
        """
        project_scope = self._project_scope()
        if project_scope is None:
            return None
        return project_scope

    @property
    def session_id(self) -> Optional[str]:
        """Get the current session ID.

        A DETECTED value is exposed AS FOUND and may be unusable (a control
        character, a lone surrogate); a SUPPLIED constructor value was
        validated and is always usable. This asymmetry is deliberate: reads
        construct a PACTMemory without binding the session id, so the
        constructor must not refuse a value those commands never use. Every
        path that WOULD bind the value guards first — save() refuses to write
        an unusable adopted id, update() refuses an unusable payload id, and
        list(session_only=True) returns no records instead of binding it.
        """
        return self._session_id

    @property
    def last_embedding_status(self) -> Optional[str]:
        """Reason code from the most recent save() or update().

        None means there was nothing to report. A string is a reason code the
        caller may surface: `degraded:<search_mode>:<cause>` or `fault`.
        """
        return self._last_embedding_status

    @property
    def last_cascade_skipped(self) -> int:
        """File links the most recent update() quarantined.

        A quarantined link is one whose recorded path could not be
        re-tracked (a NUL byte, refused by `track_file`), so the link was
        left on its old project instead of aborting the re-key. Zero is the
        normal answer. Reported here because update() has no envelope: the
        count is the structured half, the warning in `_cascade_rekey` is the
        human half.
        """
        return self._last_cascade_skipped

    @property
    def last_project_scope(self) -> Optional[Dict[str, Any]]:
        """How the most recent save resolved its project, and from where.

        Keys, all seven emitted on every completed filing:
        - `project_id` — what the record was filed under;
        - `source` — which of supplied / CLAUDE_PROJECT_DIR / session record /
          worktree identity record / git root / cwd decided it;
        - `id_shape` — `canonical`, `basename`, or `caller` when the payload
          supplied the id;
        - `cwd_repo` — the CANONICAL PROJECT ID of the process's working
          directory repository, or None when none resolved (NOT the repo
          root path: the root is `repo_root` on the `project-id show`
          envelope);
        - `override_ignored` — `{path, reason}` when the identity read found
          `<git-common-dir>/pact-project-id` unusable, else None (a rung that
          declines before that read, or a home-directory root, reads none);
          `path`
          is None when the checkout's `.git` is a redirect or points at an
          outside git dir that is no linked worktree's or submodule's admin
          dir;
        - `legacy_scope` — the legacy candidate, its admission state and the
          canonical bucket's prior count (the same state `project-id show`
          reports);
        - `location_divergence` — True when `cwd_repo` and `project_id` name
          different projects.
        `unresolved` never appears in `source`: it cannot reach a completed
        filing, because save() refuses an unresolved instance identity unless
        the payload itself names the project (which then discloses as
        `supplied`).

        `location_divergence` IS NOT A MISFILE FLAG. It compares where the
        PROCESS was against what the record was filed under. It cannot see
        what the record is ABOUT, so False does not mean correctly filed.

        PRESENCE IS SINGLE-VALUED: this is assigned only after the store write
        is read back and verified, so a populated dict always describes a
        record that was actually filed.

        NONE MEANS THIS PROCESS DID NOT CONFIRM A FILING: no save has run on
        this instance, OR a save ran and exited before its write was verified —
        a refusal, a store failure, or a failed read-back.

        NONE DOES NOT MEAN NOTHING WAS WRITTEN. The read-back can fail after
        `create_memory` has already returned an id, and a failure downstream of
        the write (the embedding step can hang) leaves a complete row in the
        store behind a call that never returned. So a record may exist while
        this field is None. Absence licenses "not confirmed filed" and nothing
        stronger: do not read it as a filing, do not read it as the absence of
        one, and do not read it as approval. No state of this field ever means
        "the scope was fine".

        An in-line caller never meets the ambiguity, because control flow
        settles it: a normal return from save() implies a populated dict.
        It is the reader holding neither a return value nor an exception —
        reading across calls, from another instance, or in a `finally` — for
        whom absence is two-valued.

        NOT PERSISTED. This lives on the instance and in the save envelope; it
        is not a column and no store holds it, so it is gone when the process
        exits. It makes a save's project resolution visible AT THE TIME and
        afterwards only in whatever output the caller kept. Do not build an
        after-the-fact audit on it.

        RAW HERE; the CLI's save envelope display-renders `project_id` and
        `cwd_repo` (`cli._project_scope_envelope`), so a programmatic caller
        gets the values it can bind while a rendered consumer gets markers.
        """
        return self._last_project_scope

    @property
    def db_path(self) -> Path:
        """The resolved store path — RAW, for programmatic callers.

        The status ENVELOPE reports a display-escaped form; this property is
        the value a caller may open, compare or bind. The store scope is
        bound to THIS instance's path for the lookup, exactly as the
        `_with_store_scope` methods do, so a caller-store instance reports
        its own file rather than the default one.
        """
        with store_scope(self._db_path):
            return resolve_db_path()

    @property
    def last_sync_status(self) -> Optional[str]:
        """Outcome of the most recent save(), sync() or search() CLAUDE.md write.

        One of `SyncResult`'s reasons: `wrote`, `refused`, `suppressed`,
        `unresolved`, `missing`, `failed`, `no_window` or `uncertain`, plus
        `empty` on the sync() path (this project has no records; the file was
        not touched) and `root_unknown` when a retrieved-context sync could
        not identify the project's memory root.
        None means no such write has run yet on this instance, or the most
        recent search did not sync (no results, or `sync_to_claude=False`).

        THIS LIST IS THE REACHABLE SET AND NOT THE FULL ENUM. `empty` never
        arrives from a save, and `suppressed` never arrives from a sync. A
        reader who takes this list for the enum will look for a case that
        cannot arrive on the path they are reading.

        READ THIS BESIDE `last_embedding_status`, BECAUSE THE TWO DIFFER IN
        MORE THAN POLARITY. That one is PARTIAL: it reports a PROBLEM, and it
        is None when the embedding succeeded. This one is TOTAL: it names the
        outcome in every case, `wrote` included.

        So an ABSENT `sync_status` NEVER MEANS SUCCESS. It means neither a
        save nor a sync has run. Reading absence as success is the exact
        inference this channel exists to make impossible -- a refused sync and
        a suppressed one used to be one indistinguishable silence, and treating
        silence as a good outcome is how that silence stayed invisible.
        """
        return self._last_sync_status

    def track_file(self, path: str) -> None:
        """
        Track a file modified in this session.

        Called by file tracking hooks to accumulate files
        that will be linked to saved memories.

        Args:
            path: File path that was modified.
        """
        if path not in self._session_files:
            self._session_files.append(path)
            logger.debug(f"Tracking file: {path}")

    def get_tracked_files(self) -> List[str]:
        """Get list of files tracked in this session."""
        return self._session_files.copy()

    def clear_tracked_files(self) -> None:
        """Clear the list of tracked files."""
        self._session_files.clear()

    def _refresh_cwd_cache(self) -> None:
        """Drop the cached cwd root and identity if the directory moved.

        One `getcwd` per call and no git call: the cached answers are reused
        only while the working directory is the one they were derived for.
        """
        current = _cwd_or_none()
        if current != self._cwd_cache_dir:
            self._cwd_cache_dir = current
            self._cwd_repo_root = False
            self._cwd_repo_id = False

    def _current_repo_identity(self) -> Optional[str]:
        """The identity of the repository this PROCESS is in, override included.

        Cached on the instance beside `_cwd_repo_root`. The override is part
        of the comparison ON PURPOSE: a repository pinned by
        `<git-common-dir>/pact-project-id` binds that value, so comparing
        against the remote-derived id would fire the divergence warning on
        every in-repo save — exactly the noise the fix exists to stop.

        `common_dir is None` means the cwd is not an established repository;
        the folder name is then the honest answer, and a divergence warning
        from a non-repository location is not a divergence at all.
        """
        self._refresh_cwd_cache()
        if self._cwd_repo_id is not False:
            return self._cwd_repo_id
        if self._cwd_repo_root is False:
            self._cwd_repo_root = main_repo_root()
        root = self._cwd_repo_root
        if root is None:
            self._cwd_repo_id = None
            return None
        if self._project_identity_root is not None and root == self._project_identity_root:
            # The resolver already derived this root's identity; asking git
            # again would answer the same question twice.
            self._cwd_repo_id = self._project_id
            return self._cwd_repo_id
        # ASK FOR THIS ROOT'S common dir, not the process cwd's: `main_repo_root`
        # may have been resolved for a different start, and pairing a root with
        # another repository's common dir reads the wrong config. A path that
        # is not a repository answers None, which is the designed basename arm.
        common_dir = main_repo_common_dir(str(root))
        identity = detect_id_for_root(root, common_dir, asked_root=_cwd_or_none())
        # NO FOLDER PSEUDO-ID ON A DECLINE. `identity.project_id or root.name`
        # invented an id the resolver had just refused, and the warning then
        # advised a save that would itself be refused. None means "unknown
        # here"; the divergence warning skips on it.
        self._cwd_repo_id = identity.project_id
        return self._cwd_repo_id

    def _location_divergence_warning(self, project_id: Optional[str]) -> Optional[str]:
        """Warning text when the filed project disagrees with the repo we sit in.

        THE ASYMMETRY THIS CLOSES. _detect_project_id warns loudly when
        resolution lands on HOME, because that is a known mis-scope vector, and
        says NOTHING when a perfectly valid project key is stamped on a record
        about somewhere else. Only the silent case has actually occurred.

        WHY HERE AND NOT IN _detect_project_id, which is where the HOME warning
        lives: detection runs ONLY when the payload omits project_id
        (`project_id or self._detect_project_id()`), so a supplied project_id
        never reaches it. Checking at the point of FILING covers both routes —
        supplied and detected — with one comparison.

        WHAT IT COMPARES, AND WHAT IT THEREFORE CANNOT SEE. It compares the
        filed project against the main repo root of the process's working
        directory. That is the process LOCATION, which is a PROXY for the
        record's subject and not the subject itself. It catches a save issued
        from inside a different repository. It CANNOT catch a record about
        another project written from the correct directory — there every
        strategy agrees and every one of them is right about the location and
        silent about the subject. Do not read a silent save as evidence the
        record is correctly filed.

        Silent when the working directory is not in a repository at all: an
        absent lower answer is no evidence of disagreement, and treating
        absence as divergence is what would make this fire on ordinary saves
        from a temp directory and get it ignored within a day.
        """
        if not project_id:
            return None
        self._refresh_cwd_cache()
        if self._cwd_repo_root is False:
            self._cwd_repo_root = main_repo_root()
        root = self._cwd_repo_root
        if root is None:
            return None
        # COMPARE IDS, NOT FOLDER NAMES. The filed project is now a canonical
        # id; comparing it with `root.name` would differ on every in-repo save
        # and fire this warning until readers learned to ignore it. The
        # canonical id of the working directory is the same derivation the
        # resolver used — INCLUDING a repository override, which is why the
        # helper resolves one rather than reading only the remote.
        cwd_id = self._current_repo_identity()
        if cwd_id is None or cwd_id == project_id:
            return None
        return (
            f"location divergence: this memory is being filed under "
            f"{project_id!r}, but the working directory is inside repository "
            f"{cwd_id!r} ({str(root)!r}). If the record is about {cwd_id!r}, "
            f"pass project_id explicitly or save from that project. "
            f"THIS IS NOT A MISFILE DETECTOR: it compares the process LOCATION "
            f"against the filed project, so it cannot see what the record is "
            f"ABOUT. A record concerning another project, written from the "
            f"correct directory, produces no warning and is still misfiled."
        )

    def _unresolved_identity_message(self) -> str:
        """Explain a refusal caused by NO project identity resolving.

        THE CAUSE IS THE RESOLVER'S OWN DECISION, carried from this instance's
        detection pass (`_decline_reason`, `_override_ignored`), never
        re-derived from the process's location: a re-derivation reads the
        cwd's repository, which in a declared session is not the one that
        declined, and invents a cause the resolver did not reach.

        The declarations are still listed from BOTH sources, without
        short-circuiting, so a session record that also failed is named. The
        wording reports what was observed; it never claims the two sources
        disagree, which is a different refusal with a different remedy.
        """
        env_dir = os.environ.get("CLAUDE_PROJECT_DIR") or ""
        record_dir = get_project_dir_from_session_record() or ""
        declarations = []
        if env_dir:
            declarations.append(f"declared via CLAUDE_PROJECT_DIR: {env_dir!r}")
        if record_dir:
            declarations.append(f"declared via the session record: {record_dir!r}")
        reason = self._decline_reason or {}
        ignored = self._override_ignored or {}
        parts = []
        if reason.get("reason"):
            cause = _DECLINE_CAUSES.get(reason["reason"], reason["reason"])
            detail = getattr(self, "_decline_detail", None)
            if detail:
                cause = f"{cause}; {detail}"
            parts.append(f"The {reason.get('source') or 'resolver'} rung declined: {cause}.")
        if declarations:
            parts.append("Declarations present: " + "; ".join(declarations) + ".")
        elif not reason.get("reason"):
            parts.append(
                "No project directory was declared (CLAUDE_PROJECT_DIR is unset "
                "and the session record names none), and no repository answered."
            )
        if ignored.get("path"):
            parts.append(
                f"An override file at {str(ignored['path'])!r} was present but "
                f"unusable ({ignored.get('reason')})."
            )
        elif ignored.get("reason"):
            parts.append(
                "An override file in the git directory this checkout's `.git` "
                f"points at was present but unusable ({ignored['reason']})."
            )
        override_path = reason.get("override_path") or ignored.get("path")
        if reason.get("reason") == "gitfile_redirect":
            remedy = _REDIRECT_REMEDY
        elif override_path:
            remedy = (
                "Write an operator-chosen canonical id to "
                f"{str(override_path)!r}, or set CLAUDE_PROJECT_DIR to another "
                "project root."
            )
        else:
            remedy = (
                "Run from inside the project directory, or set "
                "CLAUDE_PROJECT_DIR to an existing project root."
            )
        return (
            "no project identity could be resolved, so the write was refused "
            "rather than filed under an unknown project. Nothing was written. "
            + " ".join(parts)
            + f" {remedy} Run the `project-id show` verb to inspect the "
            "detected identity."
        )

    @_with_store_scope
    def save(
        self,
        memory: Dict[str, Any],
        files: Optional[List[str]] = None,
        include_tracked: bool = True,
        sync_to_claude: bool = True,
        claude_md_root: Optional[Path] = None
    ) -> str:
        """
        Save a memory to the database.

        Automatically:
        - Adds project_id and session_id if not provided
        - Links tracked files from the session
        - Generates and stores embedding for semantic search

        Args:
            memory: Memory dictionary with fields like context, goal,
                    lessons_learned, decisions, entities, active_tasks.
                    A caller-supplied `id` is normalised to lowercase — the
                    stored representation every lookup computes.
            files: Optional explicit file list to link.
            include_tracked: Include automatically tracked session files.
            sync_to_claude: Whether to project this memory into CLAUDE.md's
                Working Memory section. Default True — existing callers are
                unaffected. Pass False when the projection would defeat the
                caller's purpose: the pin-archival path removes a block from
                CLAUDE.md, and syncing writes the same bytes back, so the pin
                SLOT is freed while the file is not. Mirrors `search`'s
                parameter of the same name.
            claude_md_root: Declared containment anchor forwarded to the sync.
                The write must land inside it or the containment check refuses.
                It does not steer resolution. Omit for today's behaviour.

        Returns:
            The ID of the saved memory.
        """
        # Clear the sync status FIRST, so a save that raises before the sync
        # cannot leave the PREVIOUS save's outcome behind for a caller to read
        # as this one's. `last_sync_status` promises to describe the most recent
        # save; a stale value would make that promise false in exactly the
        # silent way this channel exists to remove.
        self._last_sync_status = None

        # Clear the scope disclosure for the same reason, and CLEAR ONLY --
        # this field is deliberately NOT populated on the refusal path the way
        # `last_sync_status` is twelve lines above. THE TWO HAVE DIFFERENT
        # DOMAINS, which is the whole of why they differ: `last_sync_status` is
        # an OUTCOME channel and REFUSED is a member of its value set, so
        # setting it on a refusal uses that domain. `last_project_scope`
        # DESCRIBES A COMPLETED FILING -- which project, decided by which of
        # the six sources, against which repo -- and no member of THAT domain
        # means "no filing occurred" except absence. A refusal forced into it
        # would have to emit `location_divergence: False`, which reads as "these
        # agree" when the truth is "nothing was compared": a false negative in
        # the one field whose purpose is removing ambiguous silence. Putting a
        # refusal into `source` instead would overload a key whose documented
        # domain is the six resolution sources.
        #
        # AT ENTRY RATHER THAN BESIDE EACH EARLY EXIT, because the set of early
        # exits is NOT RELIABLY ENUMERABLE. Grepping `raise` between here and
        # the assignment finds ONE site; there are NINE CALLABLES in that range
        # and every one of them can propagate. A clear at entry is correct
        # without knowing the set. Populating at each known exit is correct only
        # for the exits someone remembered, and the obvious instrument for
        # remembering them under-counts by eight.
        self._last_project_scope = None

        # And the embedding status, for the same reason and against a WIDER
        # window than either sibling -- it is not assigned until after the
        # store write, ~150 lines below, so every exit above it could leave a
        # previous call's `degraded:<mode>` or `fault` readable as this one's.
        #
        # ITS PARTIALITY IS NOT A LICENCE TO SKIP THIS, and that is the step a
        # reader is most likely to get wrong here. `last_embedding_status` is
        # PARTIAL -- absent means "nothing to report" rather than "no call ran"
        # -- so the temptation is to conclude a gap is expected and staleness
        # tolerable. PARTIAL GOVERNS WHAT ABSENCE MEANS, NOT WHETHER A STALE
        # VALUE IS WRONG. The docstring promises a code from "the most recent
        # save() or update()", and a value surviving from an earlier call is
        # not from the most recent one, whatever absence would have meant.
        self._last_embedding_status = None

        # FAIL CLOSED on an env/record disagreement, BEFORE any store work: the
        # row would land under the env-derived project while the session's other
        # readers follow the record — the silent mis-scope this refusal exists
        # to make visible. Reads are unaffected; only writes refuse. The status
        # channel reports the refusal FIRST, matching the ambient-guard refusal
        # class — a caller that only reads `last_sync_status` sees a deliberate
        # refusal, not an absent status.
        disagreement = env_record_project_dir_disagreement()
        if disagreement is not None:
            self._last_sync_status = SyncResult.REFUSED
            raise ProjectScopeDisagreementError(
                format_project_dir_disagreement(*disagreement)
            )

        # A PAYLOAD project_id THAT IS PRESENT MUST BE USABLE. Omission means
        # "auto-detect"; an empty string (or any other falsy-but-present value)
        # cannot mean that, so it is rejected as bad input rather than treated
        # as a warrant or silently replaced by the detected id. This check is
        # unconditional on the instance's identity, because on a RESOLVED
        # instance a falsy payload id would otherwise survive the defaulting
        # and be written to the memory row AND its file rows under a key no
        # `PACTMemory` instance can ever hold (`__init__` routes falsy input to
        # detection), so no reader could address those rows.
        #
        # ValueError is this module's payload-validation channel; the CLI
        # already envelopes it as invalid input (exit 2), so it cannot read as
        # an env/record disagreement.
        payload_project = memory.get("project_id")
        if payload_project is not None and _unusable_identifier(payload_project):
            # Reject C0, DEL and C1 controls and lone surrogates without
            # restricting other Unicode.
            raise ValueError(
                "project_id, when supplied, must be a non-empty string without "
                "control characters (U+0000-U+001F, U+007F-U+009F) or lone "
                "surrogates (U+D800-U+DFFF); omit it to auto-detect the "
                "project. Nothing was written."
            )
        if (
            payload_project is not None
            and self._detected_identity_is_canonical()
            and not is_canonical(payload_project)
        ):
            self._refuse_legacy_write(
                f"save into project_id {payload_project!r} was refused"
            )

        # THE SAME RULE FOR session_id, SAME VERB, SAME SHAPE: a supplied
        # value must be usable; null does NOT apply here — on save, a null
        # session_id means "default me" exactly as a null project_id means
        # "auto-detect me", and only a value someone actually sent can be
        # bad input. Sits beside the project_id guard, before `_ensure_ready`
        # and before the defaulting, so a refused payload does no store work.
        payload_session = memory.get("session_id")
        if payload_session is not None and _unusable_identifier(payload_session):
            raise ValueError(
                "session_id, when supplied, must be a non-empty string without "
                "control characters (U+0000-U+001F, U+007F-U+009F) or lone "
                "surrogates (U+D800-U+DFFF); omit it to default the session. "
                "Nothing was written."
            )

        # THE ADOPTED SESSION ID IS VALIDATED AT THE WRITE INGRESS, NOT AT
        # CONSTRUCTION. The constructor exposes a DETECTED value as it found
        # it so read-only commands do not gate on it (see __init__); this is
        # the point where that value would be WRITTEN into a row, so the rule
        # applies here. A payload-supplied value was validated just above;
        # only the adopted default is left to check, and it is checked before
        # _ensure_ready() and before the defaulting, so a refusal does no
        # store work. `session_id` is the row's other scalar identifier, so it
        # gets the same remedy channel as the payload guard: ValueError, which
        # the CLI envelopes as exit 2 rather than SYSTEM_ERROR.
        if (
            memory.get("session_id") is None
            and self._session_id is not None
            and _unusable_identifier(self._session_id)
        ):
            raise ValueError(
                "the session id detected from the session context file "
                f"({self._session_id!r}) is not a non-empty string free of "
                "control characters and lone surrogates; refusing to write "
                "it. Pass session_id explicitly, or fix the session context "
                "file. Nothing was written."
            )

        # AND FAIL CLOSED WHEN NO IDENTITY RESOLVED AT ALL. A None id is not a
        # neutral default here: `create_memory` would stamp a NULL project_id,
        # which `list_memories`/search then treat as "no filter" or as a
        # shared NULL bucket — a cross-project read path. Refuse BEFORE any
        # store work, on the same status channel as the disagreement above,
        # and name the real cause: this is not an env/record disagreement.
        #
        # AN EXPLICIT PAYLOAD project_id IS A WARRANT, exactly as an explicit
        # sync target is a warrant elsewhere in this module: the caller has
        # NAMED the destination, so the record is not NULL-keyed and the
        # cross-project read this guard closes cannot arise. Refusing it would
        # be an over-block of a route the disclosure code treats as
        # first-class ("supplied"). The defaulting below runs after this
        # guard, so `memory` still holds the caller's raw value here.
        if memory.get("project_id") is None:
            # TYPED REFUSAL FOR AN UNUSABLE DETECTED VALUE, BEFORE THE
            # UNRESOLVED ONE: this is the write ingress, so the binding rule
            # applies here — ValueError, the CLI's exit-2 remedy channel —
            # rather than letting the value die at the TEXT bind. A supplied
            # payload project is a warrant, was validated above, and never
            # consults the adopted value.
            project_for_write = self._project_id_for_write()
            if project_for_write is None:
                self._last_sync_status = SyncResult.REFUSED
                raise ProjectIdentityUnresolvedError(
                    self._unresolved_identity_message()
                )

        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        # Add project/session context if not provided.
        #
        # Capture whether THIS PAYLOAD carried its own project BEFORE the
        # defaulting overwrites the distinction. The disclosure below must
        # describe how THIS RECORD's project was decided, not how the instance
        # resolved its default: the CLI passes a payload project_id inside the
        # memory dict rather than as a constructor argument, so reporting
        # `self._project_id_source` here would label a payload-supplied
        # project with whatever strategy the instance happened to detect --
        # wrong on precisely the least-checked of the six routes.
        payload_supplied_project = memory.get("project_id") is not None
        if "project_id" not in memory or memory["project_id"] is None:
            memory["project_id"] = self._project_id
        if "session_id" not in memory or memory["session_id"] is None:
            memory["session_id"] = self._session_id

        # ONE REPRESENTATION FOR AN ID. `_resolve_id_or_full` lowercases every
        # lookup, while `create_memory` stores a caller-supplied id verbatim,
        # so an uppercase id would be written and then unreachable by
        # get/update/delete. Normalise at the write ingress so the stored form
        # is the one every read path computes. Generated ids are already
        # lowercase hex, so this only touches caller-supplied strings.
        if isinstance(memory.get("id"), str):
            memory["id"] = memory["id"].lower()

        # WARNING (secondary): the narrow, name-what-it-detects signal. It
        # WARNS and does not refuse — filing under another project is
        # legitimate and the caller may mean it, so the decision stays theirs
        # and only the silence goes.
        # Reads the payload directly rather than borrowing the disclosure
        # block's local, so the two are independently revertible: the warning
        # is the optional half and must be droppable without touching the
        # disclosure, which is the deliverable.
        divergence_warning = self._location_divergence_warning(
            memory.get("project_id")
        )
        if divergence_warning:
            logger.warning("%s", divergence_warning)

        # VALIDATE THE FILE LIST BEFORE ANY ROW IS WRITTEN. A NUL in a path
        # makes the file layer refuse, and by then `create_memory` has already
        # inserted the memory row — MEASURED: the row persisted while the
        # error text said "nothing was written". Refusing HERE, before the
        # connection is even opened, is what makes that promise true.
        files_to_link = []
        if files:
            files_to_link.extend(files)
        if include_tracked and self._session_files:
            files_to_link.extend(self._session_files)
        for candidate in files_to_link:
            try:
                raw_path = os.fspath(candidate)
            except TypeError:
                raise ValueError(
                    "file path must be a string or a path-like object, got "
                    f"{type(candidate).__name__}; nothing was written."
                ) from None
            if isinstance(raw_path, bytes):
                # THE CONTRACT IS `List[str]`, SO BYTES ARE REFUSED OUTRIGHT.
                # Decoding only to CHECK left the original bytes in the list,
                # and `_normalize_path` raised on them AFTER the memory row
                # was committed — measured: 1 memory row, 0 file rows. Refusing
                # the type at the ingress is what makes the promise true for
                # NUL-free bytes as well as NUL-bearing ones.
                raise ValueError(
                    "file paths must be str, not bytes; nothing was written."
                )
            if "\x00" in raw_path:
                raise ValueError(
                    f"file path {raw_path!r} must not contain a NUL character; "
                    "nothing was written."
                )

        with db_connection() as conn:
            ensure_initialized(conn)

            # The save envelope's evidence, computed BEFORE the write: "no prior rows"
            # must be the state the save FOUND, not the state it created.
            # `_read_project_scopes` records the legacy candidate, whether it
            # was admitted, and the canonical bucket's prior count.
            self._read_project_scopes(conn)

            # Create the memory record
            memory_id = create_memory(conn, memory)

            # Link files to memory
            if files_to_link:
                # THE EFFECTIVE ID, NOT THE INSTANCE DEFAULT. The memory row
                # above was filed under `memory["project_id"]`, which may be a
                # payload-supplied id on a path where the instance resolved
                # nothing. Passing `self._project_id` here would then key the
                # FILE rows NULL while the memory itself is scoped: the memory
                # becomes invisible to `search_by_file` and to
                # `graph_stats["files"]` in its own project, and the link is a
                # cross-scope edge in a table with no project column.
                link_memory_to_paths(
                    conn, memory_id, files_to_link,
                    memory.get("project_id"), "modified"
                )

            # Store embedding for semantic search
            self._last_embedding_status = self._store_embedding(
                conn, memory_id, memory
            )

            logger.info(f"Saved memory {memory_id} with {len(files_to_link)} files")

        # IN-PROCESS WRITE: drop the cached read scope. The stat guard may not
        # see this commit while the connection is still open (WAL), and a
        # stale legacy membership in the same process is the silent-loss
        # class this change exists to remove.
        _invalidate_legacy_scope_cache()

        # Verify the save persisted by reading back (before syncing to CLAUDE.md,
        # so we never reference a phantom memory in working memory)
        if memory_id is None:
            raise RuntimeError("Save failed — no memory_id returned")
        # VERIFY BY EXACT ID, NOT THROUGH THE PREFIX RESOLVER. `self.get()`
        # routes an id shorter than 32 characters through the project-scoped
        # prefix resolver, so it judged a SUCCESSFUL save with a lookup scoped
        # to the INSTANCE's identity: a payload project that differs from the
        # detected one, or no usable detected identity at all, turned a
        # committed row into "not found" — and a retry then collided on the
        # primary key. The row just written is addressable by its exact id;
        # read it that way and confirm it carries the project this call filed
        # it under.
        effective_project = memory.get("project_id")
        with db_connection() as conn:
            ensure_initialized(conn)
            verification = get_memory(conn, memory_id)
        if (
            verification is None
            or verification.get("project_id") != effective_project
        ):
            raise RuntimeError(
                f"Save verification failed — memory_id {memory_id} not found "
                f"under project {effective_project!r} after save"
            )

        # DISCLOSURE (primary): report, IN THIS CALL'S RETURN VALUE, how this
        # save decided its project. It judges nothing.
        #
        # IT IS NOT PERSISTED, AND THE COMMENT HERE USED TO IMPLY OTHERWISE.
        # The old wording read "today's misfile was undetectable after the fact
        # by any means; with this recorded it is one field away". The second
        # half is false: `project_scope` is not a column, appears nowhere in
        # database.py or models.py, and is written to no store. It lives on the
        # instance and in the save envelope, and it is gone when the process
        # exits. Nothing about it is one field away after the fact.
        #
        # WHAT IT ACTUALLY BUYS, stated at its real size. The misfile was
        # undetectable because NOTHING WAS EMITTED. Something is emitted now,
        # so the resolution is visible AT THE TIME OF THE SAVE, and afterwards
        # only for as long as whoever called it kept their output. That is a
        # genuine move from "never knowable" to "knowable then, and later only
        # if someone kept the receipt" -- worth having, and strictly weaker
        # than the promise the old sentence made. Do not plan a later audit
        # around this field; plan it around the caller's captured output.
        #
        # BELOW THE VERIFIED WRITE, NOT ABOVE IT, AND THAT PLACEMENT IS THE
        # WHOLE CONTRACT. Assigned before the write, this field was TWO-VALUED
        # in the present direction: a save raising at `create_memory` left a
        # populated dict BYTE-IDENTICAL to a successful one, so a reader could
        # not tell a completed filing from one that never landed. Measured, not
        # reasoned -- the failing arm returned the same four keys and the same
        # four values as the succeeding arm.
        #
        # WHY THE IN-LINE CALLER NEVER SAW IT, and why that is not a defence.
        # Control flow settles it for them: `save()` assigns on the single path
        # to its one return, so a normal return implies present and present
        # implies filed. The ambiguous reader was the one holding NEITHER a
        # return value NOR an exception -- reading the field across calls, from
        # another instance, or in a `finally`. That reader is rarer than the
        # in-line one and is not hypothetical, which is why presence was worth
        # collapsing even though the common path never showed the defect.
        #
        # AND NOT THE AFTER-THE-FACT READER, WHO CANNOT REACH THIS FIELD AT
        # ALL. An earlier version of this comment rested the argument on the
        # sentence above about after-the-fact detection. That sentence was
        # false -- nothing persists this field -- so an after-the-fact reader
        # never gets here to be confused. The fix stands on the narrower and
        # true ground: within a single process, presence must not lie.
        #
        # THE RESIDUAL, STATED BECAUSE IT IS REAL AND IT GOT WIDER. Absence
        # now means only "this process did not confirm a filing": no save ran,
        # or a save ran and exited between the clear at entry and this line --
        # a window that now spans the whole store write and the read-back
        # rather than stopping short of them. BECAUSE THE WINDOW NOW CONTAINS
        # THE WRITE, AN ABSENT FIELD NO LONGER IMPLIES AN ABSENT ROW: exit
        # after `create_memory` returns and the record is in the store with
        # this field None. That is the honest cost of moving the assignment
        # down and it must be stated, not implied. That is the
        # correct trade and not a regression: a WIDE HONEST ABSENCE beats a
        # NARROW LYING PRESENCE, because absence sends a reader to look while a
        # false presence answers them. The surfaces that describe absence must
        # therefore describe two states, and they do.
        #
        # AFTER THE READ-BACK AND NOT MERELY AFTER `create_memory`. The
        # verification above exists because a `create_memory` that returns is
        # not proof the row persisted -- its own comment says so. Landing this
        # assignment above that check would leave PRESENT meaning "a row we
        # could not read back", which is presence lying in a NARROWER window.
        # That is worse than the wide absence, not better: nobody goes looking
        # inside a narrow window.
        #
        # THIS NOW DISAGREES WITH THE DIVERGENCE WARNING ABOVE, DELIBERATELY,
        # AND IT WILL READ AS A BUG. That warning fires before the store write
        # and describes an ATTEMPT; this describes a COMPLETED FILING. They are
        # two different events, so a save that warns and then fails emits a
        # warning with NO disclosure beside it. That pairing is correct. Any
        # test asserting the warning and `location_divergence` agree must be
        # scoped to the SUCCESS path, because on the failure path they are
        # required to disagree.
        filed_under = memory.get("project_id")
        # THE SAME ID COMPARISON THE WARNING USES, through the same helper, so
        # the two fields always describe one derivation — override included.
        self._current_repo_identity()
        cwd_repo = self._cwd_repo_root
        self._last_project_scope = {
            "project_id": filed_under,
            "source": (
                "supplied" if payload_supplied_project else self._project_id_source
            ),
            "id_shape": (
                "caller"
                if payload_supplied_project
                else self._project_id_shape
            ),
            "cwd_repo": self._cwd_repo_id,
            "override_ignored": self._override_ignored,
            # The legacy candidate, its admission state and the
            # canonical bucket's PRIOR count. Present on every save, so a
            # caller never has to read an absent field as "nothing to say".
            "legacy_scope": self._legacy_scope_state,
            # SILENT ON AN UNKNOWN, exactly as the warning above is: when the
            # cwd repository's identity did not resolve, there is no second id
            # to disagree with, and `None != filed_under` is not a divergence.
            "location_divergence": bool(
                filed_under
                and cwd_repo is not None
                and self._cwd_repo_id is not None
                and self._cwd_repo_id != filed_under
            ),
        }

        # Sync to CLAUDE.md working memory (outside db connection context)
        # This is non-critical - failures are logged but don't fail the save.
        # Gated on sync_to_claude (default True): callers that omit the
        # parameter reach this call exactly as before.
        #
        # EVERY BRANCH BELOW RECORDS A REASON, including the suppressed one and
        # the successful one. That totality is the point: a caller must never
        # have to read an absent status as success. `suppressed` has no other
        # producer -- `sync_to_claude_md` is never called on that path, so the
        # gate itself is the only place the fact exists.
        if sync_to_claude:
            try:
                result = sync_to_claude_md(
                    memory, files_to_link if files_to_link else None, memory_id,
                    claude_md_root=claude_md_root
                )
                self._last_sync_status = result.reason
            except AmbientSyncRefused as e:
                # MUST precede the general handler. The guard RAISES rather than
                # returning, so without this clause a refusal is indistinguishable
                # from any other failure -- and a refusal is the one outcome that
                # is deliberate rather than broken.
                self._last_sync_status = SyncResult.REFUSED
                # DEBUG, NOT WARNING. The refusal is ALREADY on the structured
                # channel: `sync_status='refused'` reaches the caller on stdout
                # one line above. A WARNING here would also reach stderr via
                # logging.lastResort, which the CLI's callers parse as JSON.
                logger.debug(f"Refused to sync to CLAUDE.md: {e}")
            except Exception as e:
                self._last_sync_status = SyncResult.FAILED
                logger.warning(f"Failed to sync to CLAUDE.md: {e}")
        else:
            self._last_sync_status = SyncResult.SUPPRESSED

        return memory_id

    def _store_embedding(
        self,
        conn: sqlite3.Connection,
        memory_id: str,
        memory: Dict[str, Any]
    ) -> Optional[str]:
        """
        Generate and store embedding for a memory.

        Requires SQLITE_EXTENSIONS_ENABLED (pysqlite3) and sqlite-vec.

        REMOVAL HAPPENS ON TWO DIFFERENT ROUTES, and they are not alike.
        First, when this returns WITHOUT writing a vector and the vector table
        is reachable, it removes any vector stored for this memory, and that
        delete is committed on its own. Second, on the WRITE route, it removes
        the stored vector as the first half of a replace, with the commit
        DEFERRED so that the delete and the insert share one transaction.
        A missing vector makes a record invisible to semantic search; a stale
        one makes it findable for the wrong query, which is the worse failure.

        THREE EXITS DO NOT REMOVE, so a stale vector can survive all three.
        The two capability exits cannot open the vector table to issue the
        delete, so nothing here repairs them and nothing else does either. The
        fault handler does not remove either, and the reason has changed: the
        replace route rolls back its own failure, so a failed insert leaves
        the ORIGINAL vector in place and there is nothing for the handler to
        repair. The handler still wraps the drop, the insert and the commit,
        so it can be entered after a rollback has restored the original. A
        drop placed in the handler would destroy that restored vector. That
        omission stays deliberate and a test pins it.

        Args:
            conn: Active database connection.
            memory_id: Memory ID to associate embedding with.
            memory: Memory data for embedding generation.

        Returns:
            None when there is nothing for the caller to report - either the
            vector was stored, or storing none was correct for this input.
            Otherwise a reason code the caller may surface:
            `degraded:<search_mode>:<cause>` when this process cannot embed
            at all -- cause is `no-vector-store` (no extension support, or
            sqlite-vec absent) or `no-model` (the embedding model would not
            load) -- or
            `fault` when storing raised.
        """
        # Check if SQLite extension loading is available
        if not SQLITE_EXTENSIONS_ENABLED:
            logger.debug(
                "Skipping embedding storage - SQLite extensions unavailable. "
                "Search will use keyword mode."
            )
            # No extension means the vector table cannot be reached at all, so
            # an existing vector cannot be removed here. It stays stale until a
            # process that can embed rewrites or removes it.
            return self._degraded_reason(self.DEGRADED_NO_VECTOR_STORE)

        # Generate text for embedding
        text = generate_embedding_text(memory)
        if not text:
            # Storing no vector is correct for a record with no embeddable
            # text, but any vector already stored describes the previous text.
            self._drop_existing_vector(conn, memory_id)
            return None

        # Generate embedding
        embedding = generate_embedding(text)
        if embedding is None:
            logger.debug("Embedding generation unavailable, skipping")
            self._drop_existing_vector(conn, memory_id)
            return self._degraded_reason(self.DEGRADED_NO_MODEL)

        try:
            # Enable extension loading (safe because SQLITE_EXTENSIONS_ENABLED is True)
            conn.enable_load_extension(True)
            try:
                import sqlite_vec
                sqlite_vec.load(conn)
            except ImportError:
                logger.debug("sqlite-vec not installed, skipping embedding storage")
                # Same as the no-extension exit: the vector table is
                # unreachable, so an existing vector cannot be removed here.
                return self._degraded_reason(self.DEGRADED_NO_VECTOR_STORE)

            # Convert to blob
            embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)

            # A REPLACE ON THIS TABLE IS A DROP AND THEN AN INSERT, INSIDE ONE
            # TRANSACTION. A vec0 table honours NO conflict clause: a plain
            # INSERT, an OR REPLACE and an OR IGNORE each RAISE on a row that
            # is present. So `OR REPLACE` here was a statement of a behaviour
            # that does not happen, and it hid the reason the delete is
            # necessary. A test arm measures each of the three spellings.
            #
            # `commit=False` IS THE FIX, AND THE ORDER IS THE REASON. The drop
            # helper commits by default. A committed delete MOVES THE RESTORE
            # POINT, so a later rollback returns to the state WITHOUT the
            # vector, and the record loses it outright when the insert fails.
            # With the commit deferred, the delete and the insert share one
            # transaction, so a failed insert leaves the ORIGINAL vector in
            # place. A separating test arm holds this apart from the ordering
            # that commits the delete first, which reaches the same end state
            # on the success path and loses the vector on the failure path.
            self._drop_existing_vector(conn, memory_id, commit=False)
            try:
                conn.execute(
                    """
                    INSERT INTO vec_memories (memory_id, project_id, embedding)
                    VALUES (?, ?, ?)
                    """,
                    (memory_id, memory.get("project_id"), embedding_blob)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            logger.debug(f"Stored embedding for memory {memory_id}")
            return None

        except Exception as e:
            # This handler wraps the drop, the insert and the commit. The
            # replace route rolls back its own failure, so by the time this
            # handler runs the ORIGINAL vector has been restored. Removing the
            # vector here would destroy that restored one; leave it and report
            # the fault.
            #
            # DEBUG, NOT WARNING, AND RAISING IT BREAKS A CONTRACT. cli.py
            # configures no logging, so logging.lastResort emits WARNING and
            # above to STDERR -- and the CLI's stderr is a structured JSON
            # channel that callers parse. One free-text line corrupts that
            # parse. The fault is not being swallowed: it reaches the caller as
            # `embedding_status: "fault"` on stdout, which is the channel a
            # caller can actually act on. See the measured table in
            # memory_init.check_and_install_dependencies for the same hazard.
            logger.debug(f"Failed to store embedding for {memory_id}: {e}")
            return "fault"

    # The two mechanisms that leave a save without a vector. They are DIFFERENT
    # FAULTS WANTING DIFFERENT RESPONSES -- one is a missing library on this
    # machine, the other is a model this process could not load -- and a single
    # symbol for both is the defect this names away.
    DEGRADED_NO_VECTOR_STORE = "no-vector-store"
    DEGRADED_NO_MODEL = "no-model"

    @staticmethod
    def _degraded_reason(cause: str) -> str:
        """Report this process's search capability AND why it degraded.

        Reads the same capability the search path reports, so a caller is never
        told one thing by `status` and another by a save. `cause` is appended
        as a THIRD segment rather than replacing the mode, because the two
        answer different questions and collapsing them is what this fixes:
        the mode says what search will do now, the cause says why.

        Shape: `degraded:<search_mode>:<cause>`.
        """
        try:
            capabilities = get_search_capabilities()
            mode = capabilities.get("search_mode", "unknown")
        except Exception:
            mode = "unknown"
        return f"degraded:{mode}:{cause}"

    @staticmethod
    def _drop_existing_vector(
        conn: sqlite3.Connection, memory_id: str, commit: bool = True,
    ) -> bool:
        """Remove any stored vector for this memory.

        Keyed on the CONDITION (returning without a vector) rather than on the
        caller, so it covers an update that fails to re-embed and an orphaned
        row left by any other path.

        `commit` DEFAULTS TO TRUE, AND THE DEFAULT IS THE CONTRACT FOR THE
        CALLERS THAT RETURN WITHOUT A VECTOR. Each of them wants the delete to
        be permanent on its own, because no further write follows it.

        PASS `commit=False` WHEN A WRITE FOLLOWS THE DELETE IN THE SAME
        LOGICAL OPERATION. A commit here closes the restore point, so a later
        `conn.rollback()` returns to the state WITHOUT the vector rather than
        to the state before the delete. The replace path on the success side
        depends on the deferred form for exactly that reason.

        Returns True if the delete ran. False means the vector table could not
        be reached, so a stale vector may survive.
        """
        try:
            conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(conn)
            conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
            if commit:
                conn.commit()
            return True
        except Exception as e:
            logger.debug(f"Could not drop existing vector for {memory_id}: {e}")
            return False

    @_with_store_scope
    def search(
        self,
        query: str,
        current_file: Optional[str] = None,
        limit: int = 20,
        sync_to_claude: bool = True,
        claude_md_root: Optional[Path] = None
    ) -> List[MemoryObject]:
        """
        Search memories using semantic similarity and graph relationships.

        Args:
            query: Search query text.
            current_file: Optional current file for context boosting.
            limit: Maximum number of results.
            sync_to_claude: Whether to sync top result to CLAUDE.md Retrieved Context.
            claude_md_root: Declared containment anchor forwarded to the
                retrieved-context sync, exactly as on `save`.

        Returns:
            List of matching MemoryObject instances.
        """
        # A search with no results or no identity attempts no sync. Clear any
        # previous save's outcome before either early-return path.
        self._last_sync_status = None
        # NO IDENTITY MEANS NO PROJECT'S RECORDS, never all projects'. The
        # search layer drops its project filter on None, so an unguarded call
        # would return the whole store and, on a non-empty result, follow it
        # with a Retrieved Context write. `_project_scope()` folds an
        # UNUSABLE detected id into the same answer: unresolvable, not a
        # binding, not an exception. Return before _ensure_ready(): a call
        # that returns nothing must not initialize the store either.
        project_scope = self._project_scope()
        if project_scope is None:
            return []
        # A READ MUST NOT INITIALISE THE STORE. Checked before
        # `_ensure_ready()` and before any read-write connection, so a 0-byte
        # store answers empty instead of being grown by the WAL pragma or the
        # schema DDL.
        if not self._schema_present():
            return []
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()
        # NO SCHEMA DDL ON A READ: see `config.read_only_scope`. The scope is
        # computed INSIDE the block too: it is a read, and its probe must not
        # run the write-path initialiser either.
        with read_only_scope():
            # THE UNION, AFTER `_ensure_ready()`: the canonical id plus the
            # caller's own legacy bucket when the gate admits it. The early
            # `_project_scope()` return above stays first so a no-op read
            # does no store work.
            project_scopes = self._read_project_scopes()
            results = graph_enhanced_search(
                query,
                current_file=current_file,
                project_id=project_scopes,
                limit=limit
            )

        # Sync to CLAUDE.md Retrieved Context section
        if sync_to_claude and results:
            try:
                # Convert MemoryObjects to dicts for sync
                memory_dicts = [r.to_dict() for r in results]
                memory_ids = [r.id for r in results]
                # graph_enhanced_search doesn't return scores, so pass None
                #
                # KEEP THE RESULT. This call used to discard it, so the
                # reason it produced -- including ROOT_UNKNOWN, "git could not
                # answer, so the project memory target is unknown" -- reached
                # no reader.
                result = sync_retrieved_to_claude_md(
                    memory_dicts, query, None, memory_ids,
                    claude_md_root=claude_md_root
                )
                self._last_sync_status = result.reason
            except Exception as e:
                self._last_sync_status = SyncResult.FAILED
                logger.warning(f"Failed to sync retrieved context to CLAUDE.md: {e}")

        return results

    @_with_store_scope
    def search_by_file(
        self,
        file_path: str,
        limit: int = 10
    ) -> List[MemoryObject]:
        """
        Find memories related to a specific file.

        Args:
            file_path: File path to search for.
            limit: Maximum number of results.

        Returns:
            List of related MemoryObject instances.
        """
        # NO IDENTITY MEANS NO RECORDS. A None id reaches graph's NULL-safe
        # filter as "match rows whose project_id IS NULL" — the shared
        # unknown-scope bucket, not this project's records.
        # `_project_scope()` folds an UNUSABLE detected id into the same
        # answer. Return before _ensure_ready(): a no-op call must not
        # initialize the store.
        project_scope = self._project_scope()
        if project_scope is None:
            return []
        # A READ MUST NOT INITIALISE THE STORE (see `search`).
        if not self._schema_present():
            return []
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        # NO SCHEMA DDL ON A READ: see `config.read_only_scope`.
        with read_only_scope():
            return search_by_file(
                file_path, self._read_project_scopes(), limit
            )

    def _resolve_id_or_full(
        self, conn, memory_id: str
    ) -> Optional[str]:
        """
        Resolve a caller-supplied ID into a full 32-char memory ID.

        Input is case-folded to lowercase before any branch, so an
        uppercase or mixed-case full ID resolves identically to its
        lowercase form (memory IDs are stored as lowercase hex).
        Full-length input is then returned unchanged (no DB query).
        Shorter input is treated as a prefix and resolved via the
        storage-layer resolver: a unique prefix returns the full ID;
        ambiguity raises AmbiguousPrefixError; too-short raises
        PrefixTooShortError; no match returns None.

        THE PREFIX RESOLUTION IS SCOPED TO THIS INSTANCE'S PROJECT, and
        that scope changes what a prefix can DISCOVER, not what a full id
        can REACH. A full 32-character id is returned before any query, so
        the by-id capability stays project-blind; a prefix is resolved with
        `_project_scope()` so a prefix matching only another project's row
        resolves as no row at all.

        AN UNRESOLVED OR UNUSABLE IDENTITY REFUSES THE PREFIX. `None` at the
        resolver
        means "unscoped", which is the right storage-layer contract but the
        wrong caller behaviour here: it would let a process that could not
        resolve its project enumerate every project's ids and context
        snippets. The refusal uses the existing unresolved-identity
        exception with a READ-shaped message (the shared write message says
        "nothing was written", which is false for a lookup).

        Caller already owns an open `conn` (inside a `db_connection`
        context manager). This helper does not open or close connections.

        Args:
            conn: Active database connection.
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.
                Case-insensitive: uppercase and mixed-case input is
                normalized to lowercase before lookup.

        Returns:
            The full memory ID (lowercase), or None if the prefix matches
            no row.

        Raises:
            PrefixTooShortError: prefix shorter than MIN_PREFIX_LENGTH.
            AmbiguousPrefixError: prefix matches more than one memory.
            ProjectIdentityUnresolvedError: a prefix was supplied while no
                USABLE project identity could be resolved (absent, or a
                detected value that cannot be bound). A full id still
                resolves.
        """
        memory_id = memory_id.lower()
        if len(memory_id) >= MEMORY_ID_LENGTH:
            return memory_id
        # A PREFIX CANNOT MATCH IN A STORE WITH NO SCHEMA. Checked on the
        # caller's connection (sqlite_master answers without creating), so a
        # prefix lookup on an empty store returns None instead of letting the
        # resolver's CREATE-on-read initialize it.
        if not _connection_has_memories_table(conn):
            return None
        # `_project_scope()` returns None for an UNUSABLE detected id as well
        # as for an absent one, and the prefix refusal below then applies to
        # both. The full-id return above happens FIRST, which is what keeps
        # get/update/delete identity-independent under a malformed directory
        # name (the Bugbot finding this closes).
        project_scope = self._project_scope()
        if project_scope is None:
            raise ProjectIdentityUnresolvedError(
                "no project identity could be resolved, so a memory-id "
                "prefix was refused rather than listing matches from every "
                "project. A full 32-character id still resolves. Run from "
                "inside the project directory, or set CLAUDE_PROJECT_DIR to "
                "an existing project root."
            )
        return resolve_memory_id_prefix(
            conn, memory_id, project_id=self._read_project_scopes(conn)
        )

    @_with_store_scope
    def get(self, memory_id: str) -> Optional[MemoryObject]:
        """
        Get a specific memory by ID or unique prefix.

        Accepts a full 32-char memory ID or a prefix of at least
        MIN_PREFIX_LENGTH characters. A unique prefix resolves to the
        matching memory; ambiguity and too-short input surface as
        exceptions from the storage-layer resolver.

        Args:
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.

        Returns:
            MemoryObject if found, None if no match.

        Raises:
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
            ProjectIdentityUnresolvedError: a prefix was supplied while no
                USABLE project identity could be resolved (absent, or a
                detected value that cannot be bound). A full 32-character
                id still resolves.
        """
        # A READ MUST NOT INITIALISE THE STORE. A by-id lookup on an empty
        # store has no row to return, so it answers None without creating one.
        if not self._schema_present():
            return None
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        # NO SCHEMA DDL ON A READ: see `config.read_only_scope`.
        with read_only_scope(), db_connection() as conn:
            ensure_initialized(conn)

            resolved = self._resolve_id_or_full(conn, memory_id)
            if resolved is None:
                return None
            memory_id = resolved

            memory_dict = get_memory(conn, memory_id)
            if memory_dict is None:
                return None

            # Get associated files, CONFIRMED AGAINST THE MEMORY'S OWN
            # PROJECT. The link carries no project, so a stale link left by
            # an interrupted re-key would attach another project's file path
            # to this memory. A NULL value keeps the whole-store meaning: a
            # NULL project cannot be expressed as a filter here without also
            # meaning "no filter".
            files_data = get_files_for_memory(
                conn, memory_id, memory_dict.get("project_id")
            )
            file_paths = [f["path"] for f in files_data]

            return memory_from_db_row(memory_dict, file_paths)

    @_with_store_scope
    def update(
        self,
        memory_id: str,
        updates: Dict[str, Any],
        *,
        replace: bool = False,
    ) -> Optional[str]:
        """
        Update an existing memory by ID or unique prefix.

        Accepts a full 32-char memory ID or a prefix of at least
        MIN_PREFIX_LENGTH characters. A unique prefix resolves to the
        matching memory; ambiguous prefixes are refused (the update is
        rejected via AmbiguousPrefixError so the caller can disambiguate).

        Args:
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.
            updates: Dictionary of fields to update.
            replace: If True, list-valued fields are replaced wholesale
                instead of merged additively (default False = additive merge
                with content-hash dedup).

        Returns:
            The resolved full 32-char memory ID on successful update, or
            None when the input matched no row. Callers that invoked with a
            prefix get the canonical ID back so downstream operations key off
            the storage form.

        Raises:
            ValueError: If `updates` is not a dict (a non-mapping
                payload cannot be indexed by field name), if updates
                contains unknown field names, if any dict-list item
                contains unknown sub-object keys, or if a supplied
                `project_id` or `session_id` is not a non-empty string
                without control characters or lone surrogates (explicit
                null included — it would scalar-replace the row's key to
                NULL; omit the field to leave it unchanged). Payload
                validation precedes id resolution: such a payload is
                refused before the memory id is resolved, so it reports
                the payload, never the prefix.
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
            ProjectScopeDisagreementError: CLAUDE_PROJECT_DIR and the session
                record name different project directories (fail-closed write
                refusal; reads follow env). Evaluated before payload
                validation: a session that disagrees refuses on the
                disagreement, whatever the payload carries.
        """
        # Clear the embedding status FIRST, for the reason given at save()'s
        # entry: it is assigned only after the store write, so any exit above
        # that point would otherwise leave a previous call's reason code
        # readable as this one's. update() shares the field with save(), so a
        # failed update could surface the last SAVE's code.
        self._last_embedding_status = None
        # The cascade's skip count is per call for the same reason: a caller
        # reading it after a later update must not see an earlier one's
        # quarantine.
        self._last_cascade_skipped = 0

        # Same fail-closed rule as save()/sync(), evaluated at CALL time — the
        # constructor-bound project_id may predate a mid-process disagreement.
        # update() has no sync-status channel, so there is no REFUSED line to
        # set here; the typed exception is the refusal's whole surface.
        disagreement = env_record_project_dir_disagreement()
        if disagreement is not None:
            raise ProjectScopeDisagreementError(
                format_project_dir_disagreement(*disagreement)
            )

        # A PAYLOAD project_id OR session_id ON AN UPDATE MUST BE A USABLE
        # IDENTIFIER, AND PRESENCE — NOT TRUTHINESS — DECIDES.
        # `update_memory` scalar-replaces both fields, so an explicit null
        # re-keys the row to NULL and an explicit "" re-keys it to the empty
        # string: either is a key no reader can address and no instance can
        # hold (the de-key hazard save()'s payload guard closes on the create
        # path). `.get()` cannot make the needed
        # distinction — on update, explicit null IS the hazard and omission is
        # a no-op, the OPPOSITE boundary from save(), where null means
        # "auto-detect" — so the guard keys on `"field" in updates` and
        # validates the value it finds. Updates omitting the fields proceed
        # with no identity requirement: the row is already keyed, and refusing
        # identity-free edits would block honest updates in a session whose
        # declared directory has died. session_id is validated by the same
        # rule as project_id: it is the row's other scalar identifier, and the
        # CLI passes it through verbatim on the same path.
        #
        # ValueError for the same reason as save()'s payload block: it is this
        # module's bad-input channel and the CLI envelopes it as exit 2, so it
        # cannot read as an env/record disagreement.
        #
        # FIRST, THE PAYLOAD MUST BE A MAPPING AT ALL. `update_memory` writes
        # from `updates.items()` and the loop below indexes by field name, so
        # a list, a string or a scalar reached one of two bare
        # TypeErrors/AttributeErrors instead of this channel. `cmd_update`
        # rejects a non-object JSON payload before calling here, but the
        # method is public and the library route had no gate.
        if not isinstance(updates, dict):
            raise ValueError(
                "update payload must be an object mapping field names to "
                f"values, not {type(updates).__name__}; nothing was written."
            )
        for _identity_field in ("project_id", "session_id"):
            if _identity_field in updates and _unusable_identifier(
                updates[_identity_field]
            ):
                raise ValueError(
                    f"{_identity_field}, when supplied to update, must be a "
                    "non-empty string without control characters "
                    "(U+0000-U+001F, U+007F-U+009F) or lone surrogates "
                    "(U+D800-U+DFFF); omit it to leave the row's "
                    f"{_identity_field} unchanged. Nothing was written."
                )

        # NO ADOPTED-SESSION GUARD BELONGS HERE. `update_memory` writes only
        # the fields this payload carries, so update() never binds
        # `self._session_id`: the only session id it can write is a SUPPLIED
        # one, refused by the loop above. An instance-level guard would
        # refuse honest updates that never touch the value. The constructor
        # adopting an unusable DETECTED id (see __init__) is therefore safe
        # on this path without any further check — do not add one.

        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection() as conn:
            ensure_initialized(conn)

            resolved = self._resolve_id_or_full(conn, memory_id)
            if resolved is None:
                return None
            memory_id = resolved

            # NOT FOUND COMES BEFORE EVERY REFUSAL. A full id resolves without
            # a query, so a missing row must be detected here; otherwise a
            # refusal below would report a folder-name bucket the row does
            # not have, and exit 2 where the caller should learn "no row".
            stored = get_memory(conn, memory_id)
            if stored is None:
                return None

            # THE WRITE-INGRESS RULE PROTECTS FOLDER-NAME BUCKETS ONLY: a
            # canonical session may neither file a row into one (a project_id
            # change) nor relabel a session in one (a session_id change),
            # because either can create the session evidence that admits that
            # bucket to its own reads. A row with NO project (NULL) is not a
            # folder-name bucket, so its session_id may change.
            if self._detected_identity_is_canonical():
                if "project_id" in updates and not is_canonical(
                    updates["project_id"]
                ):
                    self._refuse_legacy_write(
                        f"update to project_id {updates['project_id']!r} "
                        "was refused"
                    )
                stored_project = stored.get("project_id")
                if (
                    "session_id" in updates
                    and stored_project is not None
                    and not is_canonical(stored_project)
                ):
                    self._refuse_legacy_write(
                        "update of session_id on a row in the folder-name "
                        f"bucket {stored_project!r} was refused"
                    )

            # Snapshot CONTENT_FIELDS before the
            # update so we can detect whether the merge actually changed
            # any embedding-relevant value. update_memory's additive merge
            # is idempotent for repeat calls — without this snapshot we'd
            # regenerate embeddings on every no-op update touching any
            # CONTENT_FIELDS key, even when the merge produced no diff.
            content_keys_in_update = [
                f for f in CONTENT_FIELDS if f in updates
            ]
            before_snapshot: Optional[Dict[str, Any]] = None
            if content_keys_in_update or "project_id" in updates:
                before_snapshot = get_memory(conn, memory_id) or {}

            success = update_memory(conn, memory_id, updates, replace=replace)

            vector_current = False
            if success and content_keys_in_update:
                memory_dict = get_memory(conn, memory_id)
                if memory_dict and _content_fields_changed(
                    before_snapshot or {}, memory_dict, content_keys_in_update,
                ):
                    self._last_embedding_status = self._store_embedding(
                        conn, memory_id, memory_dict
                    )
                    # The content re-store reads the re-keyed row, so on
                    # SUCCESS it already lands in the NEW project's vec
                    # partition and the cascade below must not embed a second
                    # time for it. A FAULT is not success: the reason string
                    # means nothing was written (the fault path rolls its own
                    # vector-replace back and returns without raising), so
                    # the vector is NOT current and the cascade still
                    # re-stores. Only a None status means stored-or-correctly-
                    # absent.
                    vector_current = self._last_embedding_status is None

            if success and "project_id" in updates:
                # A RE-KEY CASCADES ON A CHANGE THIS CALL MADE, OR ON AN
                # INVARIANT THE STORE STILL BREAKS. update_memory moved the
                # memories row; two project-keyed surfaces still carry the old
                # key: the linked files rows (and the memory_files links
                # pointing at them) and the vec_memories partition row.
                #
                # The OBSERVED-INVARIANT half is what lets a RE-ISSUED update
                # finish a cascade an earlier fault interrupted: by then the
                # memories row already carries the target, so the snapshot
                # comparison alone calls the update a no-op and leaves the
                # links and the vector on the old project forever. Both
                # observations are indexed lookups, so the satisfied case —
                # the normal one — writes nothing.
                # THE ROW'S OBSERVED PROJECT, NOT THE REQUESTED ONE. A
                # concurrent writer can re-key the row between update_memory's
                # commit and this cascade; driving the links and the vector at
                # the REQUESTED target would then key them to a project the
                # row no longer carries (measured: row projC, links
                # [projC, projB]). Re-read AFTER the commit and treat the
                # committed row as the truth. A row that vanished between the
                # two reads has nothing left to cascade (`delete()` has
                # already removed its vector and links), so stop rather than
                # moving anything to a NULL target.
                row_after_update = get_memory(conn, memory_id)
                if row_after_update is None:
                    if success:
                        _invalidate_legacy_scope_cache()
                    return memory_id if success else None
                target_project = row_after_update.get("project_id")
                # The CHANGE half is kept as it was: a call that re-keyed the
                # row cascades even when the content store faulted, which is
                # what the faulted-re-embed arm pins.
                project_changed = (
                    before_snapshot.get("project_id") != target_project
                )
                files_stale = self._linked_file_partition_is_stale(
                    conn, memory_id, target_project
                )
                vector_stale = self._vector_partition_is_stale(
                    conn, memory_id, target_project
                )
                if project_changed or files_stale or vector_stale:
                    # `and not vector_stale` is the failure direction: when the
                    # partition is observed wrong the re-store must happen even
                    # if the content path reported a successful store, because
                    # an extra embed is cheaper than a wrong skip.
                    self._cascade_rekey(
                        conn, memory_id, target_project,
                        vector_current=vector_current and not vector_stale,
                    )

            if success:
                _invalidate_legacy_scope_cache()
            return memory_id if success else None

    @staticmethod
    def _linked_file_partition_is_stale(
        conn: sqlite3.Connection, memory_id: str, target_project: str,
    ) -> bool:
        """Report whether a linked file row sits outside the target project.

        The cascade's invariant, OBSERVED rather than inferred from what this
        call changed. `IS NOT` is null-safe, so a link to an unprojected file
        row counts as stale for a projected target — correct, because that row
        is keyed NULL and no project-scoped read of the target reaches it.
        """
        return conn.execute(
            "SELECT 1 FROM files f JOIN memory_files mf ON mf.file_id = f.id "
            "WHERE mf.memory_id = ? AND f.project_id IS NOT ? LIMIT 1",
            (memory_id, target_project),
        ).fetchone() is not None

    @staticmethod
    def _vector_partition_is_stale(
        conn: sqlite3.Connection, memory_id: str, target_project: str,
    ) -> bool:
        """Report whether this memory's vector row sits in another partition.

        TWO STATES ARE NOT STALE, both deliberate:

        - A MISSING row belongs to the embedding catch-up, not to the
          cascade. Treating absence as stale would make every update on a
          process that cannot embed re-attempt a store forever.
        - An UNOBSERVABLE table (no extension support, or sqlite-vec absent)
          is not evidence of a wrong partition. The trigger does not fire
          there; the read-side confirmation in search.vector_search is what
          keeps a stranded row from being served to another project.

        Only an observed row under a different project makes this true.
        """
        try:
            conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(conn)
            row = conn.execute(
                "SELECT project_id FROM vec_memories WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        except Exception:
            return False
        return row is not None and row["project_id"] != target_project

    def _cascade_rekey(
        self,
        conn: sqlite3.Connection,
        memory_id: str,
        new_project: str,
        *,
        vector_current: bool,
    ) -> None:
        """Move what a re-key leaves behind: file LINKS and the vector row.

        `new_project` IS THE ROW'S OBSERVED PROJECT, not the payload's
        requested one: the caller re-reads the row after update_memory's
        commit, so a concurrent re-key that won the race is the target the
        links and the vector follow.

        Files: each linked row is keyed (path, project_id) and
        search_by_file filters by project, so the link is re-pointed to the
        (path, new_project) row — created on demand by track_file, which
        get-or-creates — and the old link removed. The OLD file ROW stays,
        and what that costs is named here rather than waved away as inert:
        `file_relations` recorded against the old row STAY THERE and become
        invisible from the new project (`get_file_relations` and
        `get_file_context` resolve relations from the (path, project) row),
        so graph relations do not follow a re-key; and the old row still
        counts in the OLD project's `get_status` graph_stats["files"], which
        counts rows rather than links. Deleting the old row instead could
        dangle `file_relations` references, so the residue is left, not
        hidden.

        A LINK WHOSE PATH CANNOT BE RE-TRACKED IS QUARANTINED, NOT FATAL.
        `track_file` refuses a path carrying a NUL byte, and that refusal
        arrives AFTER update_memory has committed the row under its new
        project. Letting it escape would half-apply the update — the row
        moved, the link stranded under the old project — and re-issuing the
        update would hit the same row and fail identically forever. The row is
        SKIPPED (left where it is, never deleted), every other link still
        moves, and one warning names the memory and the stranded file ids. The
        number of links left behind is `last_cascade_skipped`, which the
        `update` envelope carries; zero is the normal answer. No scoped read
        serves the stranded link, because every read confirms the row's
        stored project.

        Vector: vec0 partitions by project_id, so a vector left in the old
        partition is invisible to the new project's semantic search — and,
        because the partition is what a project-scoped query filters on,
        still visible to the OLD one. Re-stored through _store_embedding,
        which drops the old partition row and inserts under the new one.
        `vector_current` skips the re-store when the content-change path
        already stored the vector under the new key. If the re-store FAULTS,
        the rollback restores the OLD partition's row and that row is LEFT IN
        PLACE. Three things make that safe, and each is why the drop this
        used to perform was removed: every vector read confirms the row's
        stored project, so a stranded row cannot be served to the old
        project; the partition-aware catch-up reports the memory unembedded
        in its own project and repairs it; and the row is still observably
        wrong, so RE-ISSUING the same update re-stores it. The drop removed
        that last repair path, and because it is id-keyed and partition-blind
        it could also delete a row another session had just stored correctly
        under a third project.

        RETRY: the caller invokes this when the update moved the key, or when
        the OBSERVED invariant is still broken. The second half means
        re-issuing the same update after an interrupted cascade finishes it
        rather than reading as a no-op.

        BOUND ON CONCURRENT RE-KEYS, stated here because it is the one
        window the re-read cannot close: the target is read ONCE, after
        update_memory's commit, and a writer that lands INSIDE this cascade
        can still leave one link keyed to its own project while the row
        carries another's (measured: row projC, links [projB, projC]). No
        read serves that link — every memory and vector read confirms the
        row's stored project — and `get_graph_stats` counts the file under
        the scope that owns its row. Re-issuing the same update sees the
        link as stale and clears it. Documentation rather than an unbounded
        second pass: any further pass only narrows a window a later write
        reopens, and the re-issue already repairs it.
        """
        linked = conn.execute(
            "SELECT f.id AS file_id, f.path AS path, "
            "mf.relationship AS relationship "
            "FROM files f JOIN memory_files mf ON mf.file_id = f.id "
            "WHERE mf.memory_id = ?",
            (memory_id,),
        ).fetchall()
        quarantined: List[str] = []
        for row in linked:
            try:
                new_file_id = track_file(conn, row["path"], new_project)
            except ValueError:
                # A REFUSED PATH MUST NOT ABORT THE CASCADE. `update_memory`
                # has ALREADY committed the memory row under its new project
                # before this runs, so letting the refusal escape leaves the
                # update half applied — the row moved, the link stranded under
                # the old project — and re-issuing the update hits the same
                # row and fails identically forever. The link is left exactly
                # where it is (quarantined, never deleted: this is somebody's
                # recorded data), and reported once below. No scoped read
                # serves it: every read confirms the row's stored project, so
                # the link and the row disagreeing is already invisible.
                quarantined.append(row["file_id"])
                continue
            # `new_file_id == row["file_id"]` IS THE "already in the target"
            # TEST. track_file resolves (path, new_project), which is this
            # very row iff it already carries the target, so a separate
            # project comparison would duplicate this one.
            if new_file_id == row["file_id"]:
                continue
            # OR IGNORE, BECAUSE TWO CONCURRENT CASCADES CAN COMPUTE THE SAME
            # LINK. Both re-point this memory at the same (path, project)
            # file row; the first commits, the second's INSERT then violates
            # the (memory_id, file_id) primary key. A plain INSERT would
            # raise IntegrityError and abort that update AFTER its row write
            # committed; the ignored duplicate is already the link this loop
            # wants, and the DELETE below still retires the old one.
            conn.execute(
                "INSERT OR IGNORE INTO memory_files "
                "(memory_id, file_id, relationship) VALUES (?, ?, ?)",
                (memory_id, new_file_id, row["relationship"]),
            )
            conn.execute(
                "DELETE FROM memory_files WHERE memory_id = ? AND file_id = ?",
                (memory_id, row["file_id"]),
            )
        # COMMIT THE LINKS BEFORE THE EMBEDDING CALL, as save() commits its
        # links before embedding: _store_embedding's fault path rolls the
        # connection back, and link writes left in that transaction would be
        # undone with it — the row would stay addressable only under its OLD
        # key, the exact state the cascade exists to retire.
        conn.commit()
        self._last_cascade_skipped = len(quarantined)
        if quarantined:
            # THE REPORT. Ids only: the path is the thing that was refused,
            # so it can carry a control character and must not reach a log
            # line that someone tail-parses. Re-issuing the update skips the
            # same rows and reports them again, which is the honest answer —
            # the row is unrepairable until its recorded path is corrected.
            logger.warning(
                "re-key of memory %r left %d file link(s) on their old "
                "project: the recorded path cannot be re-tracked (file ids: "
                "%s). The memory row moved; the links did not, and no scoped "
                "read serves a link whose file row carries another project.",
                memory_id,
                len(quarantined),
                ", ".join(repr(file_id) for file_id in quarantined),
            )
        if not vector_current:
            memory_dict = get_memory(conn, memory_id)
            if memory_dict:
                # A FAULT LEAVES THE OLD PARTITION'S ROW IN PLACE, restored
                # by the rollback inside _store_embedding. DO NOT DROP IT: a
                # stranded row is not served (every vector read confirms the
                # stored project), it is visible to the partition-aware
                # catch-up, and it keeps this update's own re-issue a repair
                # rather than a no-op. See this method's docstring.
                self._last_embedding_status = self._store_embedding(
                    conn, memory_id, memory_dict
                )

    @_with_store_scope
    def delete(self, memory_id: str) -> Optional[str]:
        """
        Delete a memory by ID or unique prefix.

        Accepts a full 32-char memory ID or a prefix of at least
        MIN_PREFIX_LENGTH characters. A unique prefix resolves to the
        matching memory; ambiguous prefixes are refused (the delete is
        rejected via AmbiguousPrefixError so the caller can disambiguate).

        Args:
            memory_id: Full 32-char ID or a prefix >= MIN_PREFIX_LENGTH.

        Returns:
            The resolved full 32-char memory ID on successful delete, or
            None when the input matched no row. Callers that invoked with a
            prefix get the canonical ID back so downstream operations key off
            the storage form.

        Raises:
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
            ProjectScopeDisagreementError: CLAUDE_PROJECT_DIR and the session
                record name different project directories (fail-closed write
                refusal; reads follow env).
        """
        # Same fail-closed rule as save()/sync(), evaluated at CALL time.
        # delete() has no status channel; the typed exception is the surface.
        disagreement = env_record_project_dir_disagreement()
        if disagreement is not None:
            raise ProjectScopeDisagreementError(
                format_project_dir_disagreement(*disagreement)
            )

        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection() as conn:
            ensure_initialized(conn)

            resolved = self._resolve_id_or_full(conn, memory_id)
            if resolved is None:
                return None
            memory_id = resolved

            # Also remove from vector table. vec_memories is created lazily
            # by the FTS extension; absence is expected when FTS is
            # unavailable, and the "no such table" OperationalError is
            # silently swallowed in that case. All other OperationalErrors
            # (lock contention, corruption, schema violations) and every
            # other exception class must propagate so the caller sees the
            # real failure instead of a silent orphan-vector.
            try:
                conn.execute(
                    "DELETE FROM vec_memories WHERE memory_id = ?",
                    (memory_id,)
                )
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise

            deleted = delete_memory(conn, memory_id)
            if deleted:
                _invalidate_legacy_scope_cache()
            return memory_id if deleted else None

    @_with_store_scope
    def list(
        self,
        limit: int = 20,
        session_only: bool = False
    ) -> List[MemoryObject]:
        """
        List recent memories.

        Args:
            limit: Maximum number of results.
            session_only: Only return memories from current session.

        Returns:
            List of MemoryObject instances ordered by creation time.
        """
        # NO IDENTITY — absent OR an unusable detected value — MEANS NO
        # RECORDS. `list_memories` applies its `project_id = ?` condition only
        # when the id is non-None, so passing the raw unusable value would
        # raise from the TEXT binding and passing None would return the
        # newest records of EVERY project. Return before _ensure_ready(): a
        # no-op call must not initialize the store.
        project_scope = self._project_scope()
        if project_scope is None:
            return []
        # THIS READ BINDS THE SESSION ID ONLY WHEN `session_only` IS ASKED
        # FOR, and an unusable DETECTED id must not gate it. No row can
        # carry such a value — every write validates it before binding — so
        # the empty answer is the correct one, and it is returned before
        # _ensure_ready() so a no-op read does no store work.
        #
        # THE EMPTY RESULT MEANS "THE ADOPTED VALUE IS UNUSABLE", NOT "THIS
        # SESSION HAS NO MEMORIES". A caller cannot tell the two apart, so
        # say it here: the distinction is real, and this is the only place
        # that can record it.
        if (
            session_only
            and self._session_id is not None
            and _unusable_identifier(self._session_id)
        ):
            return []
        # A READ MUST NOT INITIALISE THE STORE (see `search`).
        if not self._schema_present():
            return []
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        # NO SCHEMA DDL ON A READ: see `config.read_only_scope`.
        with read_only_scope(), db_connection() as conn:
            ensure_initialized(conn)

            session_id = self._session_id if session_only else None

            memories_data = list_memories(
                conn,
                project_id=self._read_project_scopes(conn),
                session_id=session_id,
                limit=limit
            )

            memories = []
            for memory_dict in memories_data:
                # Same confirmation as get(): the memory row decides which
                # project's file rows may appear in its file list.
                files_data = get_files_for_memory(
                    conn, memory_dict["id"], memory_dict.get("project_id")
                )
                file_paths = [f["path"] for f in files_data]
                memories.append(memory_from_db_row(memory_dict, file_paths))

            return memories

    @_with_store_scope
    def sync(self, claude_md_root: Optional[Path] = None) -> List[str]:
        """Rebuild CLAUDE.md's Working Memory section from the store.

        Stateless: the newest MAX_WORKING_MEMORIES records of this project
        replace whatever the section holds. Returns the ids projected, newest
        first, when the file was written; [] otherwise. `last_sync_status`
        carries the outcome as for save(); `empty` means nothing was
        projected -- no project id resolved, or the project has no records --
        and the file was not touched.

        Refuses (raises ProjectScopeDisagreementError, status `refused`) when
        CLAUDE_PROJECT_DIR and the session record disagree — UNLESS the caller
        declares `claude_md_root`: an explicit destination warrant is
        containment-checked downstream, so the ambient disagreement is moot
        and the warranted call proceeds.
        """
        self._last_sync_status = None
        if self._project_scope() is None:
            # REFUSE BEFORE THE QUERY, NOT AFTER IT. `list_memories` applies
            # its `project_id = ?` condition only when the id is non-None, so
            # passing None selects the newest records of EVERY project, and
            # this method would write those foreign records over this file's
            # section. An UNUSABLE detected id is folded into the same
            # answer: unresolvable, never a binding. A project with no usable
            # id has no records, so `empty` is the correct answer on its own
            # terms. The envelope's `project_id` reports the None beside it,
            # which is what makes it diagnosable.
            self._last_sync_status = SyncResult.EMPTY
            return []
        # Same fail-closed rule as save(): on an env/record disagreement the
        # rebuild would project the env-derived project's records over the
        # record-scoped file. Refuse BEFORE the query, and raise so the CLI
        # can envelope the refusal on stderr rather than report a falsy
        # outcome with the reason invisible. The status channel reports the
        # refusal FIRST, matching save() and the ambient-guard refusal class.
        #
        # THE WARRANT EXCEPTION: a caller that declares `claude_md_root` has
        # named and containment-checked the destination, so the ambient
        # disagreement is moot — the module-layer guard in working_memory
        # already exempts a declared anchor, and the public API honors the
        # same warrant rather than refusing a warranted call. save() keeps
        # the unconditional refusal: its mis-scope vector is the DB ROW,
        # which no CLAUDE.md destination warrant covers.
        if claude_md_root is None:
            disagreement = env_record_project_dir_disagreement()
            if disagreement is not None:
                self._last_sync_status = SyncResult.REFUSED
                raise ProjectScopeDisagreementError(
                    format_project_dir_disagreement(*disagreement)
                )
        # A READ MUST NOT INITIALISE THE STORE: an empty store has nothing to
        # project, and `empty` is the honest outcome.
        if not self._schema_present():
            self._last_sync_status = SyncResult.EMPTY
            return []
        records = self.list(limit=MAX_WORKING_MEMORIES)
        payload = [r.to_dict() for r in records]
        try:
            result = project_memories_to_claude_md(
                payload, claude_md_root=claude_md_root
            )
            self._last_sync_status = result.reason
        except AmbientSyncRefused as e:
            # Same two-handler shape as save(): the refusal is deliberate and
            # already on the structured channel, so DEBUG, not WARNING.
            self._last_sync_status = SyncResult.REFUSED
            logger.debug(f"Refused to sync to CLAUDE.md: {e}")
        except Exception as e:
            self._last_sync_status = SyncResult.FAILED
            logger.warning(f"Failed to sync to CLAUDE.md: {e}")
        if self._last_sync_status != SyncResult.WROTE:
            return []
        return [r.id for r in records]

    def _empty_status_envelope(self) -> Dict[str, Any]:
        """The zeroed status envelope for NO IDENTITY or NO SCHEMA yet.

        Both states mean "there are no records to report", and neither may
        initialise the store: the schema guard must run before
        `_ensure_ready()`, which can CREATE the schema on first use.
        """
        return {
            "project_id": self._project_id_for_envelope(),
            "session_id": self._session_id_for_envelope(),
            # PROVENANCE, ALWAYS. `id_shape` says whether the id is a
            # canonical repository id or a legacy folder name, so a reader
            # can tell at a glance which population the counts describe.
            # `override_ignored` is structured, not a warning: an
            # envelope-parsing caller never sees stderr.
            "id_shape": self._project_id_shape,
            "override_ignored": self._override_ignored,
            # STRUCTURED DECLINE REASON, beside the override it names.
            "decline_reason": self._decline_reason,
            "project_candidates": [],
            # ALWAYS PRESENT, null when no candidate exists. An
            # envelope consumer must be able to tell that a bucket was
            # dropped without reading a warning it may never see.
            "legacy_scope": self._legacy_scope_state,
            "memory_count": 0,
            "tracked_files_count": len(self._session_files),
            "graph_stats": {
                "files": 0,
                "file_paths": 0,
                "memories": 0,
                "memory_file_links": 0,
                "file_relations": 0,
            },
            "capabilities": get_search_capabilities(),
            "embedding_catchup": get_embedding_catchup_status(),
            # A PROCESS fact, like session_id and capabilities, and part of
            # the envelope every other status call returns. Dropping it on
            # this arm alone would change the envelope's key set.
            "db_path": str(resolve_db_path()),
        }

    @_with_store_scope
    def get_status(self) -> Dict[str, Any]:
        """
        Get status information about the memory system.

        PAYLOAD CONTRACT: the identity fields (`project_id`, `session_id`)
        and `db_path` are STRUCTURED PAYLOAD values. Each is folded to null
        when no row or address can carry it, and otherwise returned RAW so a
        caller can feed it back — the ids as a scope, the path to
        `--db-path`. Escaping them made the reported id name no rows and the
        reported path open no store. Display escaping belongs to messages,
        warnings, log lines and the AMBIGUOUS_PREFIX diagnostic, not to
        payloads. MEMORY CONTENT is raw for the same reason: the row payloads
        from `search`, `list` and `get` are data.

        Returns:
            Dictionary with database stats and capabilities.
        """
        # A READ MUST NOT INITIALISE THE STORE. Checked BEFORE
        # `_ensure_ready()`, which can CREATE the schema on first use; an
        # uninitialized store has no counts to report.
        if not self._schema_present():
            return self._empty_status_envelope()
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        # NO IDENTITY MEANS NO STATUS QUERY. Both the memory count and the graph
        # stats report WHOLE-STORE numbers when the id is None, so an unguarded
        # report would present every project's counts as this session's. The
        # guard sits AFTER _ensure_ready() (capabilities and the catch-up
        # report are process facts and stay meaningful) and BEFORE
        # db_connection(), so THIS METHOD performs no status query and opens
        # no store connection. It is NOT "no store work": _ensure_ready() runs
        # first and may do store work itself, including a whole-store
        # embedding catch-up on the process's first memory surface.
        #
        # WHAT ZERO HERE GUARANTEES: every counter in `graph_stats`.
        # All five are project-scoped — `files`, `file_paths`, `memories`,
        # `memory_file_links` and `file_relations`. `graph.get_graph_stats`
        # applies the same scope predicate to the last three as to `files`
        # and `memories`, and derives `file_paths` from the scoped rows.
        # The earlier wording here called `memory_file_links` and
        # `file_relations` "UNCONDITIONAL global counts"; that was false, and
        # a reader who trusted it would write a global-count arm (red) or
        # revert the scoping. They read 0 here only because the guard performs
        # no query — an implementation choice, not a guarantee.
        # `_project_scope()` folds an UNUSABLE detected id into the unresolved
        # answer: zeroed, not an exception, and never a binding of the raw
        # value. The ENVELOPE VALUES are computed once so BOTH arms report
        # them the same way, and each accessor FOLDS ONLY: a value no row
        # can carry becomes None, and everything else is returned RAW so the
        # caller can use it as a scope or a store address. The counters
        # below use the same raw `project_scope`.
        project_scope = self._project_scope()
        project_id = self._project_id_for_envelope()
        session_id = self._session_id_for_envelope()
        if project_scope is None:
            return self._empty_status_envelope()

        from .database import get_memory_count, project_bucket_counts
        from .graph import get_graph_stats

        # NO SCHEMA DDL ON A READ: see `config.read_only_scope`.
        with read_only_scope(), db_connection() as conn:
            ensure_initialized(conn)

            # THE UNION: counts report the canonical id TOGETHER with the
            # caller's legacy bucket when the gate admits it. Reporting only
            # the canonical count is how a repo with a few new rows hides two
            # thousand old ones.
            project_scopes = self._read_project_scopes(conn)
            memory_count = get_memory_count(conn, project_scopes)
            graph_stats = get_graph_stats(conn, project_scopes)
            # Every populated bucket, with the literal command that
            # moves it. This is the report a reader lands on after an empty
            # search, so it must name the remedy rather than only the counts.
            project_candidates = project_candidate_rows(
                project_bucket_counts(conn),
                store=str(resolve_db_path()),
                current_id=self._project_scope(),
                legacy_state=self._legacy_scope_state,
            )

        capabilities = get_search_capabilities()

        return {
            "project_id": project_id,
            "session_id": session_id,
            "id_shape": self._project_id_shape,
            "override_ignored": self._override_ignored,
            # STRUCTURED DECLINE REASON: the same field the zeroed arm and
            # `project-id show` report, so a status consumer never has to read
            # a warning it may not receive.
            "decline_reason": self._decline_reason,
            "project_candidates": project_candidates,
            "legacy_scope": self._legacy_scope_state,
            "memory_count": memory_count,
            "tracked_files_count": len(self._session_files),
            "graph_stats": graph_stats,
            "capabilities": capabilities,
            # THE SWEEP'S REASON, SURFACED WHERE SOMEBODY READS IT. The catch-up
            # reports whether outstanding embedding work is KNOWABLE, and until
            # this key existed that answer reached no consumer: `_ensure_ready`
            # discards `ensure_memory_ready()`'s return, so every hop below
            # carried the reason correctly into a value nothing looked at.
            #
            # None means the catch-up has not run in this process. Otherwise
            # read its `status`: `ok` is "looked, found nothing", while
            # `degraded` and `error` both mean OUTSTANDING WORK IS UNKNOWN.
            # Do NOT read an absent backlog as an empty one.
            "embedding_catchup": get_embedding_catchup_status(),
            # THE NON-CREATING RESOLVER, BECAUSE THIS IS A REPORT. `get_db_path`
            # creates the parent directory as a side result, so asking a
            # read-shaped method where the store is used to LEAVE A DIRECTORY
            # BEHIND. It also reported the default location while the counts
            # above came from the caller store, so the envelope disagreed with
            # itself. The scope makes this the caller store, and the resolver
            # creates nothing.
            "db_path": str(resolve_db_path())
        }


# Module-level singleton for convenience
_lock = threading.Lock()
_instance: Optional[PACTMemory] = None


def get_memory_instance(
    project_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> PACTMemory:
    """
    Get the PACTMemory singleton instance.

    Args:
        project_id: Optional project ID override.
        session_id: Optional session ID override.

    Returns:
        PACTMemory instance.
    """
    global _instance
    with _lock:
        if _instance is None:
            _instance = PACTMemory(project_id, session_id)
    return _instance


def reset_memory_instance() -> None:
    """Reset the singleton instance (useful for testing)."""
    global _instance
    with _lock:
        _instance = None


# Convenience functions for simple usage
def save_memory(memory: Dict[str, Any], **kwargs) -> str:
    """Save a memory using the default instance."""
    return get_memory_instance().save(memory, **kwargs)


def search_memory(query: str, sync_to_claude: bool = True, **kwargs) -> List[MemoryObject]:
    """Search memories using the default instance."""
    return get_memory_instance().search(query, sync_to_claude=sync_to_claude, **kwargs)


def list_memories_simple(limit: int = 20) -> List[MemoryObject]:
    """List recent memories using the default instance."""
    return get_memory_instance().list(limit=limit)
