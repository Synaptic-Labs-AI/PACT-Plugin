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
        self._project_id = project_id or self._detect_project_id()
        self._session_id = session_id or self._detect_session_id()
        self._db_path = db_path

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
        """
        Detect project ID with multiple fallback strategies.

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
            return PACTMemory._project_name_for_declared_dir(project_dir, "CLAUDE_PROJECT_DIR")

        # Strategy 1.5: session record (inert under pytest — the discovery
        # refuses test processes — so the replica in test_project_id.py needs
        # no record leg to stay equivalent here).
        record_dir = get_project_dir_from_session_record()
        if record_dir:
            return PACTMemory._project_name_for_declared_dir(record_dir, "session record")

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
            return project_name
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
                return cwd_name
        except OSError:
            logger.debug("Failed to detect project_id from cwd")

        return None

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
        caller may surface: `degraded:<search_mode>` or `fault`.
        """
        return self._last_embedding_status

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

        # Add project/session context if not provided
        if "project_id" not in memory or memory["project_id"] is None:
            memory["project_id"] = self._project_id
        if "session_id" not in memory or memory["session_id"] is None:
            memory["session_id"] = self._session_id

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
            `degraded:<search_mode>` when this process cannot embed at all, or
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
            return self._degraded_reason()

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
            return self._degraded_reason()

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
                return self._degraded_reason()

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

    @staticmethod
    def _degraded_reason() -> str:
        """Report this process's search capability as a reason code.

        Reads the same capability the search path reports, so a caller is never
        told one thing by `status` and another by a save.
        """
        try:
            capabilities = get_search_capabilities()
            return f"degraded:{capabilities.get('search_mode', 'unknown')}"
        except Exception:
            return "degraded:unknown"

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
