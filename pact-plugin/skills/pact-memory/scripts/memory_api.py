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
import struct
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# Use the same sqlite3 module as database.py for type consistency
try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

from .config import resolve_db_path, store_scope
from .database import (
    db_connection,
    create_memory,
    get_memory,
    update_memory,
    delete_memory,
    list_memories,
    ensure_initialized,
    resolve_memory_id_prefix,
    MEMORY_ID_LENGTH,
    SQLITE_EXTENSIONS_ENABLED
)
from .embeddings import (
    generate_embedding,
    generate_embedding_text
)
from .graph import (
    link_memory_to_paths,
    get_files_for_memory
)
from .models import MemoryObject, memory_from_db_row
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
    )
except ImportError:
    from pact_session import (
        ProjectScopeDisagreementError,
        env_record_project_dir_disagreement,
        format_project_dir_disagreement,
        get_project_dir_from_session_record,
        get_session_id_from_context_file,
    )

# Configure logging
logger = logging.getLogger(__name__)

# Fields whose changes should trigger embedding regeneration.
# Kept as a module-level constant so tests can import and verify it.
CONTENT_FIELDS = {
    "context", "goal", "lessons_learned", "decisions", "entities",
    "reasoning_chains", "agreements_reached", "disagreements_resolved",
}


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


def main_repo_root(start: Optional[str] = None) -> Optional[Path]:
    """Resolve the MAIN repository root via `git rev-parse --git-common-dir`.

    Worktree-safe: --git-common-dir points at the shared .git directory from a
    linked worktree as well as from the main checkout, so its parent is the
    main repo root in both. `git rev-parse --show-toplevel` would return the
    WORKTREE path instead, fragmenting a project across its own checkouts.

    Args:
        start: Directory git resolves from, passed as `-C`. When None, git
            runs in the current working directory.

    Returns:
        The main repo root, or None when git is absent, times out, exits
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
            text=True,
            timeout=5,
            env=git_env_without_location(),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        common_dir = Path(result.stdout.strip())
        if not common_dir.is_absolute():
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
            common_dir = base / common_dir
        return common_dir.resolve().parent
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # git not installed, not a repo, command timed out, or the path could
        # not be resolved.
        return None


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
        """
        if project_id:
            # A caller-supplied project_id short-circuits detection entirely —
            # measured end to end, not inferred. It is also invisible to the
            # env/record scope guard, which takes no arguments and never sees
            # a payload, so "supplied" is the least-checked source of the five
            # and is exactly the one worth naming in the disclosure.
            self._project_id: Optional[str] = project_id
            self._project_id_source = "supplied"
        else:
            self._project_id, self._project_id_source = (
                self._detect_project_id_with_source()
            )
        self._session_id = session_id or self._detect_session_id()
        self._db_path = db_path

        # Cache for the cwd's main repo root, resolved lazily by
        # _project_scope_warning. One git subprocess per instance rather than
        # per save: the working directory does not move under a live instance,
        # and the CLI builds a fresh instance per invocation anyway. `False`
        # means "not yet resolved" — None is a real answer (not in a repo).
        self._cwd_repo_root: Any = False

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
    def _project_name_for_declared_dir(declared_dir: str, source: str) -> str:
        """Name the project a DECLARED directory belongs to.

        Shared by Strategy 1 (CLAUDE_PROJECT_DIR) and Strategy 1.5 (the
        session record): both carry one directory naming the session's scope,
        and both must derive the project name from it identically.

        When the directory points below a repo's root (a worktree OR an
        in-repo subdirectory), its basename is not the project name and would
        fragment the project_id across sessions. Prefer the MAIN repo's
        basename so every session of a project shares one key, aligning these
        branches with the git-root and cwd-marker branches (Strategies 2/3),
        which already resolve to the repo root. The rewrite fires when git
        resolves a main repo whose root differs from the declared path; only
        a repo-root declared path or a non-git path (where the main anchor
        equals, or cannot be resolved from, the declared path) keeps the
        declared basename — RESOLVED, so a symlinked project dir names its
        target, which is the path the git branch above and the backlog writer
        already record. A path that will not resolve keeps its unresolved
        basename.

        Args:
            declared_dir: The directory value (env var or session record).
            source: Label for the debug log naming where the value came from.
        """
        try:
            declared_root = Path(declared_dir).resolve()
        except (OSError, RuntimeError):
            declared_root = None
        # The local name is declared_main_root, NOT main_repo_root: rebinding
        # the module-level helper's own name would make it local for the whole
        # method and raise UnboundLocalError on the call itself.
        declared_main_root = main_repo_root(declared_dir)
        # Compare via normcase so a case-insensitive filesystem does not
        # fire the rewrite for paths that differ only in case (a no-op on
        # case-sensitive systems, where normcase is identity).
        if (
            declared_main_root is not None
            and declared_root is not None
            and os.path.normcase(str(declared_main_root)) != os.path.normcase(str(declared_root))
        ):
            logger.debug(
                "project_id detected from %s worktree main repo: %s",
                source,
                declared_main_root.name,
            )
            return declared_main_root.name
        project_name = (declared_root or Path(declared_dir)).name
        logger.debug("project_id detected from %s: %s", source, project_name)
        return project_name

    @staticmethod
    def _detect_project_id() -> Optional[str]:
        """Project id only — the long-standing signature, kept verbatim.

        Callers outside this module (and the precedence pins in
        test_project_dir_resolution.py) expect a bare string, so the richer
        variant is added BESIDE this rather than by changing this return type.
        """
        return PACTMemory._detect_project_id_with_source()[0]

    @staticmethod
    def _detect_project_id_with_source() -> "tuple[Optional[str], str]":
        """
        Detect project ID with multiple fallback strategies, naming the winner.

        Returns (project_id, source) where source is the strategy that decided
        it. The source exists so a save can DISCLOSE how its project was
        resolved: the resolution is otherwise invisible, and an invisible
        resolution is what makes a mis-scope unrecoverable after the fact.

        Detection order:
        1. CLAUDE_PROJECT_DIR environment variable (original behavior)
        1.5. Session record — the project_dir session_init persisted at
           SessionStart, discovered via the CLAUDE_CODE_SESSION_ID glob in
           pact_session. BELOW env (a present declaration wins) and ABOVE git:
           in a multi-repo workspace the cwd's git root can be the WRONG
           scope, so the session's own recorded identity outranks it.
        2. Git repository root via 'git rev-parse --git-common-dir' (worktree-safe)
        3. Current working directory — walked UP to the nearest project marker
           (.git, .claude/, or CLAUDE.md at either location). This handles the
           case where the user runs the CLI from a subdirectory. A walk that
           lands on the HOME directory WARNS: home scope means every project
           shares one memory space, and silent home scope is a mis-scope
           vector.

        Returns:
            Project ID string (directory basename), or None if all methods fail.
        """
        # Strategy 1: Environment variable (original behavior)
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            # Kept on ONE LINE deliberately: test_project_id.py's
            # source-equivalence pin matches the literal substring
            # `_project_name_for_declared_dir(project_dir,`. Wrapping this call
            # breaks that marker without changing behaviour, which is a silent
            # way to disarm a working pin.
            name = PACTMemory._project_name_for_declared_dir(project_dir, "CLAUDE_PROJECT_DIR")
            return name, "CLAUDE_PROJECT_DIR"

        # Strategy 1.5: session record (inert under pytest — the discovery
        # refuses test processes — so the replica in test_project_id.py needs
        # no record leg to stay equivalent here).
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            name = PACTMemory._project_name_for_declared_dir(record_dir, "session record")
            return name, "session record"

        # Strategy 2: Git repository root (worktree-safe)
        # main_repo_root() carries the --git-common-dir resolution and the
        # relative-result guard for both this strategy and Strategy 1, so the
        # two share one derivation of the path exactly as they already share
        # one derivation of the name. Passing no `start` runs git in the cwd,
        # which is this strategy's base.
        # NOTE: Twin pattern in working_memory.py (_get_claude_md_path) and
        #       hooks/staleness.py (get_project_claude_md_path) -- keep in sync.
        #       Those two couple the resolution to a CLAUDE.md existence check
        #       and return the root only when one is found there, so they need
        #       restructuring rather than substitution and are deliberately not
        #       migrated onto this helper.
        repo_root = main_repo_root()
        if repo_root is not None:
            project_name = repo_root.name
            logger.debug("project_id detected from git root: %s", project_name)
            return project_name, "git root"
        # git not installed, not a repo, or command timed out
        logger.debug("Git detection failed, falling back to cwd")

        # Strategy 3: Current working directory — walk UP to nearest project marker.
        # Fixes subdirectory invocation (e.g., running CLI from .claude/ or src/
        # would previously return the subdirectory basename as the project_id).
        try:
            cwd_root = PACTMemory._find_project_root(Path.cwd())
            cwd_name = cwd_root.name
            if cwd_name:
                # Home scope is the last resort, and it must not be silent: a
                # walk that lands on the home directory (typically via the
                # .claude marker there) scopes every save to the USER, and
                # searches under a project then silently miss. Warn so the
                # mis-scope is visible.
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
                logger.debug("project_id detected from cwd: %s", cwd_name)
                return cwd_name, "cwd"
        except OSError:
            logger.debug("Failed to detect project_id from cwd")

        return None, "unresolved"

    @staticmethod
    def _detect_session_id() -> Optional[str]:
        """Detect session ID from PACT context file."""
        return get_session_id_from_context_file() or None

    @property
    def project_id(self) -> Optional[str]:
        """Get the current project ID."""
        return self._project_id

    @property
    def session_id(self) -> Optional[str]:
        """Get the current session ID."""
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
        supplied / CLAUDE_PROJECT_DIR / session record / git root / cwd /
        unresolved decided it), `cwd_repo` (the main repo root of the working
        directory, or None when not in one) and `location_divergence` (True
        when those last two name different projects).

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
        """
        return self._last_project_scope

    @property
    def last_sync_status(self) -> Optional[str]:
        """Outcome of the most recent save() or sync() CLAUDE.md write.

        One of `SyncResult`'s reasons: `wrote`, `refused`, `suppressed`,
        `unresolved`, `missing`, `failed` or `no_window`, plus `empty` on the
        sync() path (this project has no records; the file was not touched).
        None means neither has run yet on this instance.

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
        if root is None or root.name == project_id:
            return None
        return (
            f"location divergence: this memory is being filed under "
            f"{project_id!r}, but the working directory is inside repository "
            f"{root.name!r} ({root}). If the record is about {root.name!r}, "
            f"pass project_id explicitly or save from that project. "
            f"THIS IS NOT A MISFILE DETECTOR: it compares the process LOCATION "
            f"against the filed project, so it cannot see what the record is "
            f"ABOUT. A record concerning another project, written from the "
            f"correct directory, produces no warning and is still misfiled."
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
        # the five sources, against which repo -- and no member of THAT domain
        # means "no filing occurred" except absence. A refusal forced into it
        # would have to emit `location_divergence: False`, which reads as "these
        # agree" when the truth is "nothing was compared": a false negative in
        # the one field whose purpose is removing ambiguous silence. Putting a
        # refusal into `source` instead would overload a key whose documented
        # domain is the five resolution strategies.
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
        # wrong on precisely the least-checked of the five routes.
        payload_supplied_project = memory.get("project_id") is not None
        if "project_id" not in memory or memory["project_id"] is None:
            memory["project_id"] = self._project_id
        if "session_id" not in memory or memory["session_id"] is None:
            memory["session_id"] = self._session_id

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
                link_memory_to_paths(
                    conn, memory_id, files_to_link,
                    self._project_id, "modified"
                )

            # Store embedding for semantic search
            self._last_embedding_status = self._store_embedding(
                conn, memory_id, memory
            )

            logger.info(f"Saved memory {memory_id} with {len(files_to_link)} files")

        # Verify the save persisted by reading back (before syncing to CLAUDE.md,
        # so we never reference a phantom memory in working memory)
        if memory_id is None:
            raise RuntimeError("Save failed — no memory_id returned")
        verification = self.get(memory_id)
        if verification is None:
            raise RuntimeError(
                f"Save verification failed — memory_id {memory_id} not found after save"
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
        if self._cwd_repo_root is False:
            self._cwd_repo_root = main_repo_root()
        cwd_repo = self._cwd_repo_root
        self._last_project_scope = {
            "project_id": filed_under,
            "source": (
                "supplied" if payload_supplied_project else self._project_id_source
            ),
            "cwd_repo": cwd_repo.name if cwd_repo is not None else None,
            "location_divergence": bool(
                filed_under and cwd_repo is not None and cwd_repo.name != filed_under
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
        limit: int = 5,
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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        results = graph_enhanced_search(
            query,
            current_file=current_file,
            project_id=self._project_id,
            limit=limit
        )

        # Sync to CLAUDE.md Retrieved Context section
        if sync_to_claude and results:
            try:
                # Convert MemoryObjects to dicts for sync
                memory_dicts = [r.to_dict() for r in results]
                memory_ids = [r.id for r in results]
                # graph_enhanced_search doesn't return scores, so pass None
                sync_retrieved_to_claude_md(
                    memory_dicts, query, None, memory_ids,
                    claude_md_root=claude_md_root
                )
            except Exception as e:
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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        return search_by_file(file_path, self._project_id, limit)

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
        """
        memory_id = memory_id.lower()
        if len(memory_id) >= MEMORY_ID_LENGTH:
            return memory_id
        return resolve_memory_id_prefix(conn, memory_id)

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

            # Get associated files
            files_data = get_files_for_memory(conn, memory_id)
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
            ValueError: If updates contains unknown field names, or if any
                dict-list item contains unknown sub-object keys.
            PrefixTooShortError: prefix is shorter than the minimum.
            AmbiguousPrefixError: prefix matches more than one memory.
            ProjectScopeDisagreementError: CLAUDE_PROJECT_DIR and the session
                record name different project directories (fail-closed write
                refusal; reads follow env).
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
            if content_keys_in_update:
                before_snapshot = get_memory(conn, memory_id) or {}

            success = update_memory(conn, memory_id, updates, replace=replace)

            if success and content_keys_in_update:
                memory_dict = get_memory(conn, memory_id)
                if memory_dict and _content_fields_changed(
                    before_snapshot or {}, memory_dict, content_keys_in_update,
                ):
                    self._last_embedding_status = self._store_embedding(
                        conn, memory_id, memory_dict
                    )

            return memory_id if success else None

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

            return memory_id if delete_memory(conn, memory_id) else None

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
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        with db_connection() as conn:
            ensure_initialized(conn)

            session_id = self._session_id if session_only else None

            memories_data = list_memories(
                conn,
                project_id=self._project_id,
                session_id=session_id,
                limit=limit
            )

            memories = []
            for memory_dict in memories_data:
                files_data = get_files_for_memory(conn, memory_dict["id"])
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
        if self._project_id is None:
            # REFUSE BEFORE THE QUERY, NOT AFTER IT. `list_memories` applies
            # its `project_id = ?` condition only when the id is non-None, so
            # passing None selects the newest records of EVERY project, and
            # this method would write those foreign records over this file's
            # section. A project with no id has no records, so `empty` is the
            # correct answer on its own terms. The envelope's `project_id`
            # reports the None beside it, which is what makes it diagnosable.
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

        Returns:
            Dictionary with database stats and capabilities.
        """
        # Ensure memory system is ready (lazy initialization)
        _ensure_ready()

        from .database import get_memory_count
        from .graph import get_graph_stats

        with db_connection() as conn:
            ensure_initialized(conn)

            memory_count = get_memory_count(conn, self._project_id)
            graph_stats = get_graph_stats(conn, self._project_id)

        capabilities = get_search_capabilities()

        return {
            "project_id": self._project_id,
            "session_id": self._session_id,
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
