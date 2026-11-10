"""
PACT Memory Lazy Initialization Module

Location: pact-plugin/skills/pact-memory/scripts/memory_init.py

Summary: Provides lazy initialization for the PACT memory system. Instead of
running at session start (which penalizes non-memory users), initialization
happens on first actual memory operation.

Handles:
1. Auto-installing dependencies (pysqlite3, sqlite-vec, model2vec)
2. Migrating embeddings when dimension changes (e.g., backend switch)
3. Catch-up embedding for memories that failed embedding at save time

Used by:
- memory_api.py: Calls ensure_memory_ready() before database operations
- Can be called directly for explicit initialization

Thread-safety: Uses threading.Lock for the session-scoped initialization flag.
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import uuid
import struct
import subprocess
import sys
import threading
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

# Session-scoped initialization state
# Two state mechanisms exist:
# 1. _initialized (in-memory): Controls overall lazy init, reset per process
# 2. Session marker file (in _get_embedding_attempted_path): Controls maybe_embed_pending()
#    specifically, persists across process restarts within the same session
_init_lock = threading.Lock()
_initialized = False

# Outcome of this session's embedding catch-up, kept so a STATUS READER can see
# it. `_ensure_ready()` discards `ensure_memory_ready()`'s return value, and an
# ignored return is syntactically indistinguishable from a call made for its
# side effects -- which is why the sweep's reason reached no consumer despite
# being carried correctly at every hop below this one. Session-scoped, mirroring
# `_initialized`, because the catch-up itself runs at most once per session.
_last_catchup_result: "dict | None" = None


# Values of `CI` that mean "NOT running under CI" even though the variable is
# SET. A bare `os.environ.get('CI')` treats every one of these as CI, because a
# non-empty string is truthy — so `CI=false` took the CI branch and skipped the
# install it was meant to permit. Pinned as an explicit set so the intent is
# readable at the call site instead of resting on a truthiness accident.
_CI_FALSY_VALUES = frozenset({'', '0', 'false', 'no'})


def _ci_is_declared() -> bool:
    """
    Report whether the environment declares that this is a CI run.

    Absent, empty, `0`, `false` and `no` all mean NOT CI, in any letter case and
    ignoring surrounding whitespace. Every other value means CI.

    Returns:
        True when the run is under CI, False otherwise.
    """
    return os.environ.get('CI', '').strip().lower() not in _CI_FALSY_VALUES


def check_and_install_dependencies() -> dict:
    """
    Check for pact-memory dependencies and auto-install if missing.

    Returns:
        dict with status, installed, and failed packages
    """
    packages = [
        ('pysqlite3', 'pysqlite3'),  # CRITICAL: enables SQLite extension loading
        ('sqlite-vec', 'sqlite_vec'),
        ('model2vec', 'model2vec'),  # Embedding backend
    ]

    missing = []
    installed = []
    failed = []

    # Check what's missing
    for pip_name, import_name in packages:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return {'status': 'ok', 'installed': [], 'failed': []}

    # CI declares these packages up front (see the workflow's DEPENDENCY SET),
    # so reaching here under CI means that list and the one above have drifted
    # apart. Report it and skip, rather than install mid-run: a test-time
    # `pip install` reaches the network, mutates the environment the suite is
    # measuring, and lands AFTER collection-time availability checks have
    # already skipped their tests — so the suite silently runs fewer tests than
    # it reports.
    #
    # `failed` stays EMPTY on purpose: nothing was attempted. That means
    # ensure_memory_ready's "Failed to install" branch does NOT fire, and the
    # `skipped` key below has no production consumer — so if this branch did not
    # emit here, the drift it exists to detect would be reported NOWHERE.
    #
    # THIS BRANCH IS DELIBERATELY SILENT, and that is a measured decision rather
    # than the oversight it looks like. Do not "fix" it by adding a log line.
    #
    # The DOMINANT caller of this function is not the pytest process: it is a
    # CLI SUBPROCESS (scripts/cli.py invoked via subprocess.run by the CLI
    # tests). On that path the CLI's STDERR IS A STRUCTURED JSON CONTRACT — the
    # tests do `json.loads(result.stderr)` and read `ok`/`error` out of it. Any
    # free text written there corrupts the parse. Measured, drift forced, same
    # suite, one variable changed at a time:
    #     no emission at all ................ 154 passed
    #     print(file=sys.stderr) ............ 15 failed
    #     logger.warning (lastResort→stderr)  15 failed
    #     warnings.warn (default→stderr) .... 15 failed
    # All three reach stderr, so all three break the contract identically. There
    # is no in-process emission that is both visible here and safe.
    #
    # And visibility does not survive the process boundary anyway: a warning
    # raised in a subprocess never reaches pytest's warnings summary, which only
    # collects warnings raised in the pytest process.
    #
    # So drift is detected STATICALLY instead, by the package-list/workflow
    # parity test in tests/test_memory_init.py. A failing test is both louder
    # than any log line and immune to capture, to the process boundary, and to
    # the `-r` flag. `status` and `skipped` below remain the programmatic signal
    # for any caller that wants to inspect the result.
    if _ci_is_declared():
        return {'status': 'skipped_ci', 'installed': [], 'failed': [],
                'skipped': list(missing)}

    # Attempt installation
    for pkg in missing:
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', pkg],
                capture_output=True,
                timeout=60
            )
            if result.returncode == 0:
                installed.append(pkg)
            else:
                failed.append(pkg)
        except subprocess.TimeoutExpired:
            failed.append(f"{pkg} (timeout)")
        except Exception as e:
            failed.append(f"{pkg} ({str(e)[:20]})")

    status = 'ok' if not failed else ('partial' if installed else 'failed')
    return {'status': status, 'installed': installed, 'failed': failed}


def maybe_migrate_embeddings() -> dict:
    """
    Check if embeddings need migration due to dimension change.

    When switching embedding backends, dimensions may change (e.g., 384->256).
    This function:
    1. Detects dimension mismatch
    2. Drops the old vector table
    3. Re-embeds all existing memories

    Returns:
        dict with status and message
    """
    result = {"status": "ok", "message": None}

    try:
        # Import required modules - we're inside the scripts package now
        try:
            import pysqlite3 as sqlite3  # noqa: F401  # availability probe: absence must route to skipped_deps
            import sqlite_vec
            from .database import get_connection
            from .embeddings import get_embedding_service, generate_embedding_text, EMBEDDING_DIM
        except ImportError:
            # Distinct status to differentiate from "nothing to migrate"
            return {"status": "skipped_deps", "message": "Dependencies not available"}

        # Get expected dimension
        expected_dim = EMBEDDING_DIM

        # Connect to database
        conn = get_connection()
        sqlite_vec.load(conn)

        # Check if vec_memories table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
        )
        if cursor.fetchone() is None:
            conn.close()
            return result  # No table, nothing to migrate

        # Check actual dimension via byte-length of a stored embedding.
        # Uses len(blob)//4 (not MATCH probe) because we need the actual old
        # dimension value for the diagnostic message below.
        # Note: database.py:_check_and_migrate_vector_table uses a MATCH probe
        # instead — it only needs binary match/mismatch, not the old value.
        try:
            row = conn.execute("SELECT embedding FROM vec_memories LIMIT 1").fetchone()
            if row is None:
                conn.close()
                return result  # Empty table, nothing to migrate

            actual_dim = len(row[0]) // 4  # 4 bytes per float
            if actual_dim == expected_dim:
                conn.close()
                return result  # Dimensions match, no migration needed

        except Exception:
            conn.close()
            return result

        # Dimension mismatch detected - need to migrate
        result["status"] = "migrating"
        result["message"] = f"Migrating embeddings: {actual_dim}-dim -> {expected_dim}-dim"

        # Drop old table
        conn.execute("DROP TABLE IF EXISTS vec_memories")
        conn.commit()

        # Recreate with new dimension
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                memory_id TEXT PRIMARY KEY,
                project_id TEXT PARTITION KEY,
                embedding float[{expected_dim}]
            )
        """)
        conn.commit()

        # Re-embed all memories using SELECT * to capture all fields (including CT fields)
        service = get_embedding_service()
        rows = conn.execute("SELECT * FROM memories").fetchall()

        success = 0
        for row in rows:
            # Bound before the try so the handler below can always name the row.
            # Assigning it inside meant a failure on the two lines that follow
            # left it unbound, so the handler raised NameError instead of
            # logging — masking the real error and killing the `continue`, which
            # turned one bad row into the loss of the whole sweep.
            mem_id = None
            try:
                memory_dict = dict(row)
                mem_id = memory_dict["id"]
                embed_text = generate_embedding_text(memory_dict)
                embedding = service.generate(embed_text)

                if embedding:
                    embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
                    conn.execute(
                        "INSERT OR REPLACE INTO vec_memories(memory_id, project_id, embedding) VALUES (?, ?, ?)",
                        (mem_id, memory_dict.get("project_id"), embedding_blob)
                    )
                    success += 1
            except Exception as e:
                logger.debug(f"Failed to re-embed memory {mem_id}: {e}")
                continue

        conn.commit()
        conn.close()

        result["status"] = "ok"
        result["message"] = f"Migrated {success}/{len(rows)} embeddings to {expected_dim}-dim"
        return result

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)[:50]
        return result


# Dual import: relative (when loaded as package) vs absolute (when tests add scripts/ to sys.path)
try:
    from .pact_session import get_session_id_from_context_file
except ImportError:
    from pact_session import get_session_id_from_context_file


# Fallback marker name for when the session id cannot be resolved. Process-
# unique, and stable for the life of the process so it still suppresses a retry
# after a sweep raises.
#
# A shared constant here would be fail-OPEN: every unresolved process would land
# on one machine-wide name, so the first sweep to finish would suppress recovery
# for every later session permanently. A process-unique name is fail-SAFE — the
# worst case is that a sweep runs more than once.
_PROCESS_MARKER_TOKEN = f"process-{os.getpid()}-{uuid.uuid4().hex[:8]}"

# Upper bound for the session-derived part of the /tmp marker filename. 64
# keeps the whole name far below NAME_MAX (255 on the usual filesystems) while
# leaving room for the hash suffix used when the slug is longer.
_MAX_MARKER_SLUG_CHARS = 64


def _remove_process_marker() -> None:
    """Delete this process's own fallback marker as the process exits.

    The fallback marker's ONLY remaining job is to suppress a retry within the
    process that created it, so its useful life ends when that process does.
    Left behind it is pure litter: no other process can ever match the token,
    and every CLI invocation is a fresh process.

    Scoped to the process-unique token alone. A session-scoped marker MUST
    survive process exit -- suppressing the sweep across the calls of one
    session is the whole point of it -- so this must never touch that path.
    Rebuild the path from the token here rather than calling
    _get_embedding_attempted_path(): that helper returns whichever path this
    process resolved, so reusing it would delete a session-scoped marker.

    BEST EFFORT. A process killed by a signal, or exiting through os._exit,
    never runs atexit handlers and leaves its marker behind. Those markers are
    empty and unmatchable rather than harmful, so nothing recovers them.
    """
    try:
        (Path("/tmp") / f"pact_embedding_attempted_{_PROCESS_MARKER_TOKEN}").unlink(
            missing_ok=True
        )
    except OSError:
        pass


# Registered unconditionally: it is a no-op when no fallback marker was created,
# and registering here cannot miss a creation site the way a call-site hook could.
atexit.register(_remove_process_marker)


def _get_embedding_attempted_path() -> Path:
    """Get path to session-scoped embedding attempt marker file.

    The session id is SANITISED, exactly as `_build_session_path` sanitises
    the directory it composes from the same value: it is read from a session
    context file, so it is not this module's own text, and it is about to be
    a path component under /tmp. Path traversal (`../`), a separator, a NUL
    or a control character must not survive into the filename; the shared
    substitution keeps the two surfaces on one rule.

    THE SANITISER FILTERS CHARACTERS, NOT LENGTH, so it is bounded here. The
    regex leaves every ASCII alphanumeric intact, which means a long context
    value composes a filename past the filesystem's NAME_MAX and the first
    `exists()` raises ENAMETOOLONG — out of `ensure_memory_ready` and into
    every memory operation of the process. A bare truncation would make two
    sessions sharing a long prefix share one marker and suppress each
    other's catch-up, so the suffix is a hash of the full slug.
    """
    from shared.pact_context import _UNSAFE_SLUG_CHARS_RE

    session_id = get_session_id_from_context_file() or _PROCESS_MARKER_TOKEN
    slug = _UNSAFE_SLUG_CHARS_RE.sub('_', str(session_id))
    if len(slug) > _MAX_MARKER_SLUG_CHARS:
        digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:16]
        slug = f"{slug[:_MAX_MARKER_SLUG_CHARS - 17]}-{digest}"
    return Path("/tmp") / f"pact_embedding_attempted_{slug}"


def maybe_embed_pending() -> dict:
    """
    Check for and process unembedded memories.

    This is a catch-up mechanism for embeddings that failed at save time.

    Features:
    - Session-scoped: Only attempts once per session
    - RAM check: Skips if available RAM is below threshold
    - Fail-fast: Stops on first failure (no retry loops)

    Returns:
        dict with status info (embedded count, skipped reason, etc.)
    """
    result = {"status": "skipped", "message": None}

    # Check if we've already attempted this session, then mark it as attempted
    # (first, to prevent retry on errors). BOTH FILESYSTEM CALLS ARE GUARDED:
    # `exists()` raises OSError for more than a missing file (ENAMETOOLONG,
    # EACCES on a parent, ...), and a marker fault is a reason to skip the
    # sweep, not to fail the caller's memory operation.
    #
    # THE TWO FAILURES ARE REPORTED SEPARATELY. When the PROBE fails, the
    # file may exist and be unreadable; telling the caller creation failed
    # sends them to the wrong remedy (permissions or the path, not a write
    # that was never attempted). Both skip the sweep.
    marker_path = _get_embedding_attempted_path()
    try:
        already_attempted = marker_path.exists()
    except OSError:
        result["message"] = "Could not check session marker"
        return result
    if already_attempted:
        result["message"] = "Already attempted this session"
        return result
    try:
        marker_path.touch()
    except OSError:
        result["message"] = "Could not create session marker"
        return result

    try:
        # Import the embedding catch-up function from sibling module
        from .embedding_catchup import embed_pending_memories

        # Process pending embeddings
        embed_result = embed_pending_memories(limit=20)

        if embed_result.get("skipped_ram"):
            result["status"] = "skipped_ram"
            result["message"] = "Low RAM, skipping"
            return result

        processed = embed_result.get("processed", 0)
        if processed > 0:
            result["status"] = "ok"
            result["message"] = f"Embedded {processed} pending memories"
            return result

        if embed_result.get("failed"):
            result["status"] = "partial"
            result["message"] = embed_result.get("error", "Unknown error")
            return result

        # READ THE REASON BEFORE READING THE ABSENCE OF WORK. `processed == 0`
        # with no failure has two causes that mean opposite things: the sweep
        # LOOKED and found nothing, or it COULD NOT LOOK. Reporting `ok` for the
        # second announces that the catch-up succeeded in exactly the degraded
        # state the catch-up exists to repair, which is the defect the sweep's
        # reason channel was added to expose and which nothing yet read.
        unknown = embed_result.get("unembedded_unknown")
        if unknown:
            # `query_failed` is a FAULT and the other two are CONFIGURATION.
            # Both dependencies were present and the query raised anyway, so it
            # is an incident rather than a capability limit; filing it under
            # `degraded` would bury the one reason that warrants attention.
            #
            # The other two are ordinary degraded states, NOT anomalies:
            # `SQLITE_EXTENSIONS_ENABLED` tracks pysqlite3 only, so a process
            # with pysqlite3 and WITHOUT sqlite-vec passes the extensions check,
            # fails to create the vector table (`database._init_vector_table`
            # returns False on that ImportError), and reaches the sweep with the
            # table genuinely absent. That is a configuration, not a defect.
            result["status"] = "error" if unknown == "query_failed" else "degraded"
            result["unembedded_unknown"] = unknown
            result["message"] = (
                f"Outstanding embedding work is UNKNOWN ({unknown}) -- the sweep "
                f"could not query the vector table, so this is not a report of "
                f"zero work."
            )
            return result

        # No pending memories to process
        result["status"] = "ok"
        result["message"] = None
        return result

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)[:50]
        return result


def ensure_memory_ready() -> dict:
    """
    Ensure the memory system is fully initialized.

    This is the main entry point for lazy initialization. It runs once per
    session, performing:
    1. Dependency installation (if needed)
    2. Embedding migration (if dimension changed)
    3. Pending embedding catch-up (if any)

    Thread-safe: Multiple calls will only run initialization once.

    Returns:
        dict with initialization results:
            - already_initialized: bool - True if this was a no-op
            - deps: dict - Dependency installation result
            - migration: dict - Migration result
            - embedding: dict - Embedding catch-up result
    """
    global _initialized

    # Fast path: already initialized this session
    if _initialized:
        return {"already_initialized": True}

    with _init_lock:
        # Double-check after acquiring lock
        if _initialized:
            return {"already_initialized": True}

        result = {
            "already_initialized": False,
            "deps": None,
            "migration": None,
            "embedding": None,
        }

        # 1. Check and install dependencies
        deps_result = check_and_install_dependencies()
        result["deps"] = deps_result

        if deps_result.get("installed"):
            logger.info(f"Installed dependencies: {', '.join(deps_result['installed'])}")
        if deps_result.get("failed"):
            logger.warning(f"Failed to install: {', '.join(deps_result['failed'])}")

        # 2. Migrate embeddings if dimension changed
        migration_result = maybe_migrate_embeddings()
        result["migration"] = migration_result

        if migration_result.get("message"):
            logger.info(f"Migration: {migration_result['message']}")

        # 3. Process any unembedded memories
        embedding_result = maybe_embed_pending()
        result["embedding"] = embedding_result
        # Record it for `get_embedding_catchup_status()`. Assigned here rather
        # than inside `maybe_embed_pending` so there is ONE write site instead
        # of one per early return.
        global _last_catchup_result
        _last_catchup_result = embedding_result

        if embedding_result.get("message") and embedding_result.get("status") == "ok":
            logger.info(f"Embedding catch-up: {embedding_result['message']}")

        # Mark as initialized
        _initialized = True
        logger.debug("Memory system initialized")

        return result


def get_embedding_catchup_status() -> "dict | None":
    """This session's embedding catch-up outcome, for a status reader.

    None means the catch-up has not run in this process yet. Otherwise the dict
    `maybe_embed_pending()` returned, whose `status` distinguishes a sweep that
    LOOKED and found nothing (`ok`) from one that COULD NOT LOOK (`degraded`, or
    `error` when the query raised despite both dependencies being present).

    READ `status` RATHER THAN INFERRING FROM A COUNT. There is no processed
    count here that means "no outstanding work": zero processed is produced both
    by an empty backlog and by a sweep that could not see the backlog at all,
    and telling those apart is the entire reason this accessor exists.
    """
    return _last_catchup_result


def reset_initialization() -> None:
    """
    Reset the in-memory initialization state.

    Useful for testing or when forcing re-initialization.

    This resets the in-memory flag ONLY. It deliberately does not delete the
    embedding marker: the marker path is shared with any real session running
    on the same machine, so deleting it here would reach outside the caller.
    Call clear_embedding_marker() when the marker itself needs removing.
    """
    global _initialized
    with _init_lock:
        _initialized = False


def clear_embedding_marker() -> None:
    """
    Delete the embedding attempt marker so maybe_embed_pending() can run again.

    Separated from reset_initialization() so that resetting the in-memory flag
    cannot remove a marker belonging to another process.
    """
    marker_path = _get_embedding_attempted_path()
    marker_path.unlink(missing_ok=True)


def is_initialized() -> bool:
    """
    Check if the memory system has been initialized this session.

    Returns:
        True if ensure_memory_ready() has completed, False otherwise.
    """
    return _initialized
