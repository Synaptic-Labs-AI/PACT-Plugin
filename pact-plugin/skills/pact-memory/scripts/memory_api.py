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

import functools
import json
import logging
import os
import shlex
import struct
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

# Use the same sqlite3 module as database.py for type consistency
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .config import get_memory_dir, resolve_db_path, store_scope
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
    SQLITE_EXTENSIONS_ENABLED
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
from .project_identity import (
    detect_id_for_root,
    is_canonical,
    read_override,
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
    was present but unusable — a stderr warning alone is invisible to a
    caller that parses the envelope.
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


# `_read_project_scopes` result cache: (store, mtime_ns, size, canonical,
# legacy) -> scope tuple. Keying on the store's stat is what makes a rekey
# performed by ANOTHER process observable instead of cached forever.
_LEGACY_SCOPE_CACHE: Dict[tuple, tuple] = {}

# Exclusion notices already emitted, keyed by (store, candidate). A warning
# repeated on every read is a warning readers learn to ignore.
_EXCLUSION_WARNED: set = set()


def _disclosed_source(identity: "RootIdentity", rung_label: str) -> str:
    """The source token to disclose when a declared rung won.

    When an operator pin decided the value, the disclosure must say
    `override file`, not the declaration label: a reader who sees
    `CLAUDE_PROJECT_DIR` believes the environment chose the id, and the
    whole point of the pin is that a human chose it.
    """
    return "override file" if identity.source == "override file" else rung_label


def render_rekey_command(project_id: str) -> str:
    """The literal, SHELL-QUOTED rekey command for one bucket id.

    ONE HELPER FOR EVERY RENDER SURFACE — the candidate list, the exclusion
    warning, the save-envelope state and the CLI examples — because a bucket
    id is STORE-INFLUENCED and this string is presented as something a user
    pastes into a shell. An unquoted id containing a space, `;`, `$` or a
    quote is a copy-paste command-injection surface, and four renderers with
    four spellings is how one of them loses the quoting.
    """
    return shlex.join(["pact-memory", "project-id", "rekey", "--from", project_id])


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


def _match_legacy_candidate(
    legacy: str, buckets: Dict[str, int]
) -> "tuple[Optional[str], list]":
    """The stored bucket spelling that matches `legacy`, or None.

    Exact case first, folded ONLY as a fallback and only when the folded match
    is unique. A folded-first lookup passes on a case-insensitive filesystem
    and misses on a case-sensitive one; two folded matches are two different
    projects on such a filesystem, so guessing would merge them.

    Returns `(candidate, ambiguous)`. `ambiguous` is the sorted list of folded
    matches when more than one bucket differs only by case; it is carried into
    the disclosure so the operator can choose, rather than being dropped
    silently.

    THE FOLDED MATCH IS NOT REQUIRED TO BE `verified`, AND MUST NOT BE. The
    live legacy bucket's file rows are all relative, so the plausibility gate
    can only ever call it `unverified`; requiring `verified` here would hide a
    bucket holding thousands of memories on a case-folding path mismatch —
    silent loss, which is the defect this change exists to remove. The guard
    that keeps a fold from merging two projects is UNIQUENESS (exactly one
    folded match), and the inclusion is disclosed as unverified.
    """
    if legacy in buckets:
        return legacy, []
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

    Used by ``PACTMemory.update`` (M7, #374 remediation) to skip embedding
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
        # reader (#1653 F1 twin site).
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


def _git_rev_parse(start: Optional[str], *args: str) -> Optional[str]:
    """One `git rev-parse` answer, decoded, or None.

    Bytes are decoded with `os.fsdecode` (never locale decoding), so a path
    holding bytes invalid in the locale answers rather than raising.
    """
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
            if git_dir != common_path and main_git == common_path:
                # LINKED WORKTREE: per-worktree git dir, shared main `.git`.
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
            project_id: Project identifier. If not provided, auto-detected using
                        (in order): CLAUDE_PROJECT_DIR env var, the session
                        record's project_dir, git repo root, or current working
                        directory basename (home scope warns).
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
        else:
            detected = self._detect_project_identity_with_legacy()
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

        # Cache for the cwd's main repo root, resolved lazily by
        # _project_scope_warning. One git subprocess per instance rather than
        # per save: the working directory does not move under a live instance,
        # and the CLI builds a fresh instance per invocation anyway. `False`
        # means "not yet resolved" — None is a real answer (not in a repo).
        self._cwd_repo_root: Any = False
        # The working directory's canonical identity, cached beside the root
        # for the same reason: `_location_divergence_warning` runs on every
        # save and must not shell to git twice for one answer.
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
        """
        if common_dir is not None:
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
        logger.warning(
            "ignoring the project override at %r (%s); resolution continues "
            "without it.",
            ignored.get("path"),
            ignored.get("reason"),
        )

    @staticmethod
    def _declared_dir_identity(
        declared_dir: str, source: str
    ) -> "tuple[Optional[str], str, Optional[str], Optional[Path], Optional[dict]]":
        """The project id a DECLARED directory belongs to, its label, the
        legacy basename, the identity root, and any unusable override.

        Shared by Strategy 1 (CLAUDE_PROJECT_DIR) and Strategy 1.5 (the
        session record). The identity root is the repository root of the
        declared directory — a worktree main root, a recorded checkout, or the
        declared path itself — and it is carried so the plausibility gate and
        the override read use the SAME root the identity came from.

        The legacy basename is `common_dir.resolve().parent.name` whenever a
        common dir exists, because that is what the PRE-change resolver
        produced; see `_legacy_project_name`.

        A declared directory that no longer exists declines. A declaration
        whose repository has a remote that cannot be represented also
        declines: never a basename for a real but unrepresentable remote,
        because a basename there is a silently different bucket.
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
        # Compare via normcase so a case-insensitive filesystem does not fire
        # the rewrite for paths that differ only in case.
        if (
            declared_main_root is not None
            and declared_root is not None
            and os.path.normcase(str(declared_main_root))
            != os.path.normcase(str(declared_root))
        ):
            legacy_name = PACTMemory._legacy_project_name(
                declared_common_dir, declared_main_root
            )
            identity = detect_id_for_root(declared_main_root, declared_common_dir)
            PACTMemory._warn_override_ignored(identity)
            if identity.project_id is not None:
                logger.debug(
                    "project_id detected from %s worktree main repo: %s",
                    source,
                    identity.project_id,
                )
                return (
                    identity.project_id,
                    _disclosed_source(identity, source),
                    legacy_name,
                    declared_main_root,
                    identity.override_ignored,
                )
            logger.warning(
                "the repository at %r (declared via %s) has a remote that "
                "cannot be represented as a project id, so no id was derived. "
                "Run `pact-memory project-id show` to inspect the detected "
                "identity, or write an operator-chosen canonical id to %s.",
                str(declared_main_root),
                source,
                declared_common_dir / "pact-project-id",
            )
            return (
                None,
                source,
                legacy_name,
                declared_main_root,
                identity.override_ignored,
            )
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
            if recorded_root is not None:
                recorded_common_dir = main_repo_common_dir(str(recorded_root))
                legacy_name = PACTMemory._legacy_project_name(
                    recorded_common_dir, recorded_root
                )
                identity = detect_id_for_root(recorded_root, recorded_common_dir)
                PACTMemory._warn_override_ignored(identity)
                if identity.project_id is not None:
                    logger.debug(
                        "project_id resolved from the worktree identity record "
                        "for %r via %s: %s",
                        declared_dir,
                        source,
                        identity.project_id,
                    )
                    return (
                        identity.project_id,
                        _disclosed_source(identity, "worktree identity record"),
                        legacy_name,
                        recorded_root,
                        identity.override_ignored,
                    )
                logger.warning(
                    "the recorded main checkout %r for the directory declared "
                    "via %s has a remote that cannot be represented as a "
                    "project id; no id was derived. Run `pact-memory "
                    "project-id show` to inspect the detected identity, or "
                    "write an operator-chosen canonical id to %s.",
                    str(recorded_root),
                    source,
                    (
                        recorded_common_dir / "pact-project-id"
                        if recorded_common_dir is not None
                        else "<git-common-dir>/pact-project-id"
                    ),
                )
                return (
                    None,
                    "worktree identity record",
                    legacy_name,
                    recorded_root,
                    identity.override_ignored,
                )
            logger.warning(
                "the directory declared via %s could not be resolved: %r "
                "does not exist and no verified worktree identity record "
                "names it. No name was derived from the path.",
                source,
                declared_dir,
            )
            return None, source, None, None, None
        live_root = declared_main_root or declared_root or Path(declared_dir)
        legacy_name = PACTMemory._legacy_project_name(declared_common_dir, live_root)
        # A non-git declared path has no common dir, so `detect_id_for_root`
        # returns the designed basename without shelling to git.
        identity = detect_id_for_root(live_root, declared_common_dir)
        PACTMemory._warn_override_ignored(identity)
        if identity.project_id is not None:
            logger.debug(
                "project_id detected from %s: %s", source, identity.project_id
            )
            return (
                identity.project_id,
                _disclosed_source(identity, source),
                legacy_name,
                live_root,
                identity.override_ignored,
            )
        logger.warning(
            "the repository at %r (declared via %s) has a remote that cannot "
            "be represented as a project id, so no id was derived. Run "
            "`pact-memory project-id show` to inspect the detected identity, "
            "or write an operator-chosen canonical id to %s.",
            str(live_root),
            source,
            declared_common_dir / "pact-project-id"
            if declared_common_dir is not None
            else "<git-common-dir>/pact-project-id",
        )
        return (
            None,
            source,
            legacy_name,
            live_root,
            identity.override_ignored,
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

        # Strategy 1: Environment variable (original behavior)
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            name, label, legacy, root, ignored = PACTMemory._declared_dir_identity(
                project_dir, "CLAUDE_PROJECT_DIR"
            )
            if name:
                return _DetectedIdentity(name, label, legacy, ignored, root)
            declined_declaration = True

        # Strategy 1.5: session record (inert under pytest — the discovery
        # refuses test processes — so the replica in test_project_id.py needs
        # no record leg to stay equivalent here).
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            name, label, legacy, root, ignored = PACTMemory._declared_dir_identity(
                record_dir, "session record"
            )
            if name:
                return _DetectedIdentity(name, label, legacy, ignored, root)
            declined_declaration = True

        # Strategy 2: Git repository root (worktree-safe)
        cwd_common_dir = main_repo_common_dir()
        if cwd_common_dir is not None:
            repo_root = main_repo_root(common=cwd_common_dir)
            if repo_root is None:
                # A bare repository has no working tree, so it has no root to
                # name. Decline rather than inventing a directory.
                logger.warning(
                    "the process's repository has no working tree (a bare "
                    "repository, or git cannot describe one), so no project "
                    "id was derived from it."
                )
                return _DetectedIdentity(None, "unresolved", None, None, None)
            legacy_name = PACTMemory._legacy_project_name(cwd_common_dir, repo_root)
            identity = detect_id_for_root(repo_root, cwd_common_dir)
            PACTMemory._warn_override_ignored(identity)
            if identity.project_id is not None:
                # THE DISCLOSURE DECISION LIVES IN ONE PLACE. This rung used
                # to hand-roll the same "a pin decided it, so say so" test as
                # `_disclosed_source`, which is the shape that let a fifth
                # render surface survive a review cycle while its four
                # siblings stayed green.
                source = _disclosed_source(
                    identity,
                    "origin remote" if identity.id_shape == "canonical" else "git root",
                )
                logger.debug(
                    "project_id detected from git root (%s): %s",
                    source,
                    identity.project_id,
                )
                return _DetectedIdentity(
                    identity.project_id,
                    source,
                    legacy_name,
                    identity.override_ignored,
                    repo_root,
                )
            if identity.source == "declined":
                # A REAL remote that cannot be represented. Falling through to
                # a basename would file this project under a different bucket
                # from the one its remote names, which is the defect this
                # change exists to remove.
                logger.warning(
                    "the repository at %r has an origin remote that cannot be "
                    "represented as a project id, so no id was derived. Run "
                    "`pact-memory project-id show` to inspect the detected "
                    "identity, or write an operator-chosen canonical id to %s.",
                    str(repo_root),
                    cwd_common_dir / "pact-project-id",
                )
                return _DetectedIdentity(
                    None,
                    "unresolved",
                    legacy_name,
                    identity.override_ignored,
                    repo_root,
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
            return _DetectedIdentity(None, "unresolved", None, None, None)

        # Strategy 2.5: the USER-SCOPE setting. Reached only when NO
        # declaration was present and no repository answered. A repo-scoped
        # override and this setting can therefore never disagree within one
        # resolution — that is why there is no disagreement warning to emit.
        user_setting = read_user_setting(get_memory_dir() / "config.json")
        if user_setting is not None:
            return _DetectedIdentity(user_setting, "user setting", None, None, None)

        # Strategy 3: Current working directory — walk UP to nearest project
        # marker. A walk that lands on HOME WARNS and never canonicalises.
        try:
            cwd_root = PACTMemory._find_project_root(Path.cwd())
            cwd_name = cwd_root.name
            if cwd_name:
                try:
                    home = Path.home().resolve()
                except (OSError, RuntimeError):
                    home = None
                if home is not None and os.path.normcase(str(cwd_root)) == os.path.normcase(str(home)):
                    logger.warning(
                        "project_id resolved to the HOME directory (%s); memories "
                        "will be scoped to user scope %r, not a project. Run from "
                        "the project directory or set CLAUDE_PROJECT_DIR.",
                        cwd_root,
                        cwd_name,
                    )
                    return _DetectedIdentity(cwd_name, "cwd", cwd_name, None, cwd_root)
                cwd_common_dir = main_repo_common_dir(str(cwd_root))
                legacy_name = PACTMemory._legacy_project_name(cwd_common_dir, cwd_root)
                identity = detect_id_for_root(cwd_root, cwd_common_dir)
                PACTMemory._warn_override_ignored(identity)
                if identity.project_id is not None:
                    logger.debug(
                        "project_id detected from cwd: %s", identity.project_id
                    )
                    return _DetectedIdentity(
                        identity.project_id,
                        _disclosed_source(identity, "cwd"),
                        legacy_name,
                        identity.override_ignored,
                        cwd_root,
                    )
                logger.warning(
                    "the repository at %r has an origin remote that cannot be "
                    "represented as a project id, so no id was derived.",
                    str(cwd_root),
                )
                return _DetectedIdentity(
                    None, "unresolved", legacy_name, identity.override_ignored, cwd_root
                )
        except OSError:
            logger.debug("Failed to detect project_id from cwd")

        return _DetectedIdentity(None, "unresolved", None, None, None)

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
        """The FOLDER-NAME id the pre-change resolver produced for this location.

        For callers that use the id as a FILE NAME. A canonical id contains
        `/`, so using one as a path component silently nests the file and
        orphans every existing `<basename>.json` — the backlog does exactly
        that, so it asks for this shape explicitly.
        """
        return PACTMemory._detect_project_identity_with_legacy().legacy_basename

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

    def _read_project_scopes(self, conn: Any = None) -> Optional[tuple]:
        """The project ids a READ may bind: the canonical id plus its legacy bucket.

        Returns None when `_project_scope()` is None — callers treat that as
        NO RECORDS, exactly today's semantics. A NON-canonical id (a supplied
        value, or a legacy basename) is a one-element scope: there is no
        canonical/legacy split to reconcile.

        For a canonical id, the PRE-change basename joins the scope ONLY when
        its bucket has rows and the plausibility gate does not exclude it.
        The candidate is matched exact-case first and folded only as a
        fallback: a folded-first lookup passes on a case-insensitive
        filesystem and misses on a case-sensitive one. The value returned is
        the STORED spelling, byte for byte — folding finds a candidate, it
        never produces one.

        An empty sequence is unreachable. `None` keeps its "no identity"
        meaning, which every caller already treats as no records.

        Reads never write: an absent store returns the canonical scope alone
        rather than being created.

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
            return None
        if not is_canonical(scope):
            return (scope,)
        legacy = self._legacy_project_basename
        if not legacy or legacy == scope:
            return (scope,)
        store = str(self._db_path or resolve_db_path())
        stat_key = _store_stat_key(store)
        if stat_key is None:
            # Absent store: nothing to reconcile and nothing to create.
            return (scope,)
        cache_key = (store, stat_key, scope, legacy)
        cached = _LEGACY_SCOPE_CACHE.get(cache_key)
        if cached is not None:
            self._legacy_scope_state = cached[1]
            return cached[0]

        from contextlib import nullcontext

        from .database import (
            count_project_memories,
            db_connection,
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
        with (
            db_connection(Path(store)) if conn is None else nullcontext(conn)
        ) as connection:
            buckets = project_bucket_counts(connection)
            # The canonical count is what lets a SAVE disclose that it landed
            # in a bucket with no prior rows. Two indexed COUNTs per process,
            # which is the budget the design set; the cache key makes the
            # second one free.
            canonical_count = count_project_memories(connection, scope)
            candidate, ambiguous = _match_legacy_candidate(legacy, buckets)
            # THE FILE COUNT IS MEASURED ONLY WHERE SURFACE 4 NEEDS IT. The
            # save path already pays two indexed COUNTs; a third query on
            # every resolution would be a new cost on the hottest write for a
            # value only the zero-prior-rows disclosure uses. `None` means
            # "not measured on this path", never zero.
            file_paths = (
                count_project_file_paths(connection, candidate)
                if candidate is not None and canonical_count == 0
                else None
            )
            if candidate is None:
                result = (scope,)
                state = {
                    "candidate": None,
                    "memory_count": 0,
                    "file_paths": None,
                    "rekey": None,
                    "included": False,
                    "verified": False,
                    "included_unverified": False,
                    "excluded": False,
                    "ambiguous": ambiguous,
                    "canonical_count": canonical_count,
                    "detail": None,
                }
            else:
                outcome, detail = legacy_candidate_plausibility(
                    connection, candidate, repo_root
                )
                included = outcome != "excluded"
                state = {
                    "candidate": candidate,
                    # SURFACE 4 CARRIES BOTH COUNTS AND THE COMMAND. A reader
                    # deciding whether to move the bucket needs its size
                    # (memories AND files) and the exact command, not just
                    # the id.
                    "memory_count": buckets.get(candidate, 0),
                    # DISTINCT PATHS, not rows: one path can carry a row in
                    # each bucket of a union, and `graph_stats` reports rows
                    # and paths separately for the same reason.
                    "file_paths": file_paths,
                    "rekey": render_rekey_command(candidate),
                    "included": included,
                    "verified": outcome == "verified",
                    # A3: the gate cannot discriminate in practice — the live
                    # legacy bucket's file rows are all relative — so the
                    # inclusion is a first-class field rather than something a
                    # consumer must infer from `verified`.
                    "included_unverified": included and outcome != "verified",
                    # LOAD-BEARING: the exclusion disclosure fires on this
                    # field. Without it a populated legacy bucket can be
                    # dropped with no notice at all.
                    "excluded": outcome == "excluded",
                    "ambiguous": ambiguous,
                    "canonical_count": canonical_count,
                    "detail": detail,
                }
                if outcome == "excluded":
                    # ONCE PER PROCESS: a warning emitted on every read is a
                    # warning readers learn to skip. The file count is what
                    # lets the operator judge the loss.
                    warn_key = (store, candidate)
                    if warn_key not in _EXCLUSION_WARNED:
                        _EXCLUSION_WARNED.add(warn_key)
                        # THE WARNING ALWAYS REPORTS A FILE COUNT, even when
                        # the surface-4 query was skipped: the count is how
                        # the operator judges the loss, and this branch runs
                        # at most once per bucket per process.
                        excluded_file_paths = (
                            file_paths
                            if file_paths is not None
                            else count_project_file_paths(connection, candidate)
                        )
                        logger.warning(
                            "legacy project bucket %r holds %d memories and %d "
                            "file path%s, but none of its absolute file rows "
                            "lies under %s, so it is NOT in this read scope. "
                            "Move it explicitly with: %s",
                            candidate,
                            buckets.get(candidate, 0),
                            excluded_file_paths,
                            "" if excluded_file_paths == 1 else "s",
                            repo_root,
                            render_rekey_command(candidate),
                        )
                result = (scope, candidate) if included else (scope,)
        self._legacy_scope_state = state
        # PRUNE superseded stamps for this same store/scope/legacy pair: the
        # key carries the store's stat, so every write leaves the old entry
        # behind and the dict would grow for the process's lifetime.
        for stale in [
            key for key in _LEGACY_SCOPE_CACHE
            if key != cache_key and key[:1] == (store,) and key[2:] == (scope, legacy)
        ]:
            _LEGACY_SCOPE_CACHE.pop(stale, None)
        _LEGACY_SCOPE_CACHE[cache_key] = (result, state)
        return result

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
    def last_project_scope(self) -> Optional[Dict[str, Any]]:
        """How the most recent save resolved its project, and from where.

        Keys: `project_id` (what it was filed under), `source` (which of
        supplied / CLAUDE_PROJECT_DIR / session record / worktree identity
        record / git root / cwd decided it), `cwd_repo` (the main repo root of
        the working directory, or None when not in one) and
        `location_divergence` (True when those last two name different
        projects). `unresolved` never appears here: it cannot reach a
        completed filing, because save() refuses an unresolved instance
        identity unless the payload itself names the project (which then
        discloses as `supplied`).

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
        if self._cwd_repo_id is not False:
            return self._cwd_repo_id
        if self._cwd_repo_root is False:
            self._cwd_repo_root = main_repo_root()
        root = self._cwd_repo_root
        if root is None:
            self._cwd_repo_id = None
            return None
        # ASK FOR THIS ROOT'S common dir, not the process cwd's: `main_repo_root`
        # may have been resolved for a different start, and pairing a root with
        # another repository's common dir reads the wrong config. A path that
        # is not a repository answers None, which is the designed basename arm.
        common_dir = main_repo_common_dir(str(root))
        identity = detect_id_for_root(root, common_dir)
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

        Re-derived at raise time from BOTH declarations, deliberately without
        short-circuiting: when the env var is set the session record may still
        name a second declaration that ALSO failed, and the message must be
        able to name every declaration that was present. The resolver keeps no
        state for this; nothing here is cached.

        The wording reports causes it OBSERVED. It never claims the two
        sources disagree — that is a different refusal with a different remedy
        — and it never advises re-exporting the recorded value, because the
        recorded directory may not exist.
        """
        # THE DISPATCHER IS THE AUTHORITY for which declarations exist and in
        # what order: `_detect_project_id_with_source` reads the same two
        # sources to decide precedence. This method re-reads them because the
        # message must name every declaration that was PRESENT, not only the
        # first one tried. A THIRD declared rung added to the dispatcher must
        # be added here too, or the refusal message will silently omit it.
        env_dir = os.environ.get("CLAUDE_PROJECT_DIR") or ""
        record_dir = get_project_dir_from_session_record() or ""
        declarations = []
        if env_dir:
            declarations.append(f"declared via CLAUDE_PROJECT_DIR: {env_dir!r}")
        if record_dir:
            declarations.append(f"declared via the session record: {record_dir!r}")
        # THE OVERRIDE IS A THIRD DECLARATION, and the one the remedy names.
        # It is a literal id rather than a directory, so it never joins the
        # env/record disagreement machinery — but a refusal the override could
        # have prevented must say the override exists and was unusable, or the
        # reader would not know the remedy was already present and broken.
        override_note = ""
        common_dir = main_repo_common_dir()
        if common_dir is not None:
            override = read_override(common_dir)
            if override.reason is not None and override.path is not None:
                override_note = (
                    f"An override file at {str(override.path)!r} was present "
                    f"but unusable ({override.reason}). "
                )
        if declarations:
            # OBSERVATION, never a comparison: the message names what was
            # present and what could not be verified, and does not imply the
            # declarations caused each other to fail or that they disagree.
            cause = (
                "Declarations present — "
                + "; ".join(declarations)
                + ". None could be verified against a repository. "
            )
        else:
            cause = (
                "No project directory was declared (CLAUDE_PROJECT_DIR is "
                "unset and the session record names none). "
            )
        return (
            "no project identity could be resolved, so the write was refused "
            "rather than filed under an unknown project. Nothing was written. "
            f"{cause}{override_note}"
            "Run from inside the project directory, set CLAUDE_PROJECT_DIR "
            "to an existing project root, or write an operator-chosen "
            "canonical id to <git-common-dir>/pact-project-id. Run "
            "`pact-memory project-id show` to inspect the detected identity."
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

        with db_connection() as conn:
            ensure_initialized(conn)

            # SURFACE 4's evidence, computed BEFORE the write: "no prior rows"
            # must be the state the save FOUND, not the state it created.
            # `_read_project_scopes` records the legacy candidate, whether it
            # was admitted, and the canonical bucket's prior count.
            self._read_project_scopes(conn)

            # Create the memory record
            memory_id = create_memory(conn, memory)

            # Collect files to link
            files_to_link = []
            if files:
                files_to_link.extend(files)
            if include_tracked and self._session_files:
                files_to_link.extend(self._session_files)

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
            # SURFACE 4: the legacy candidate, its admission state and the
            # canonical bucket's PRIOR count. Present on every save, so a
            # caller never has to read an absent field as "nothing to say".
            "legacy_scope": self._legacy_scope_state,
            "location_divergence": bool(
                filed_under and cwd_repo is not None and self._cwd_repo_id != filed_under
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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()
        # THE UNION, AFTER `_ensure_ready()`: the canonical id plus the
        # caller's own legacy bucket when the plausibility gate admits it. The
        # early `_project_scope()` return above stays first so a no-op read
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
                # KEEP THE RESULT (F5). This call used to discard it, so the
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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection() as conn:
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

            # M7 (#374 remediation): snapshot CONTENT_FIELDS before the
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
        for row in linked:
            new_file_id = track_file(conn, row["path"], new_project)
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
        # FOR, and an unusable DETECTED id must not gate it (F10). No row can
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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection() as conn:
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
        # WHAT ZERO HERE GUARANTEES: `memory_count`, `graph_stats["files"]` and
        # `graph_stats["memories"]` — the three project-scoped counters.
        # `graph_stats["memory_file_links"]` and `graph_stats["file_relations"]`
        # are UNCONDITIONAL global counts even when a real project id is passed;
        # they are not project-scoped, are pre-existing, and are not part of
        # this fix. They read 0 here only because the guard performs no query —
        # an implementation choice, not a guarantee.
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
            return {
                "project_id": project_id,
                "session_id": session_id,
                # PROVENANCE, ALWAYS. `id_shape` says whether the id is a
                # canonical repository id or a legacy folder name, so a reader
                # can tell at a glance which population the counts describe.
                # `override_ignored` is structured, not a warning: an
                # envelope-parsing caller never sees stderr.
                "id_shape": self._project_id_shape,
                "override_ignored": self._override_ignored,
                "project_candidates": [],
                # A2: ALWAYS PRESENT, null when no candidate exists. An
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

        from .database import get_memory_count, project_bucket_counts
        from .graph import get_graph_stats

        with db_connection() as conn:
            ensure_initialized(conn)

            # THE UNION: counts report the canonical id TOGETHER with the
            # caller's legacy bucket when the gate admits it. Reporting only
            # the canonical count is how a repo with a few new rows hides two
            # thousand old ones.
            project_scopes = self._read_project_scopes(conn)
            memory_count = get_memory_count(conn, project_scopes)
            graph_stats = get_graph_stats(conn, project_scopes)
            # SURFACE 2: every populated bucket, with the literal command that
            # moves it. This is the report a reader lands on after an empty
            # search, so it must name the remedy rather than only the counts.
            project_candidates = [
                {
                    "id": bucket_id,
                    "memory_count": count,
                    # SHELL-QUOTED THROUGH THE SHARED HELPER. This surface
                    # was the fifth renderer and the one that made "one
                    # helper" false: a mutation of the helper left it green
                    # because it hand-rolled the join.
                    "rekey": render_rekey_command(bucket_id),
                }
                for bucket_id, count in sorted(
                    project_bucket_counts(conn).items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ]

        capabilities = get_search_capabilities()

        return {
            "project_id": project_id,
            "session_id": session_id,
            "id_shape": self._project_id_shape,
            "override_ignored": self._override_ignored,
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
