"""
PACT Memory Database Layer

SQLite database implementation for the PACT Memory skill.
Provides schema initialization, connection management, and CRUD operations
for rich memory objects.

Storage Location: ~/.claude/pact-memory/memory.db (matches a default-root pin in code — do not migrate)
Uses WAL mode for corruption prevention and concurrent access safety.

Note: Uses pysqlite3 when available for SQLite extension loading support.
Standard library sqlite3 has enable_load_extension disabled by default.
Falls back gracefully to keyword-only search when extensions unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib.parse import unquote

# Try to import pysqlite3 which has extension loading enabled.
# Standard library sqlite3 has enable_load_extension disabled for security.
# Fall back to standard sqlite3 if pysqlite3 is not installed.
try:
    import pysqlite3 as sqlite3
    SQLITE_EXTENSIONS_ENABLED = True
except ImportError:
    import sqlite3
    SQLITE_EXTENSIONS_ENABLED = False

from .config import (
    DERIVED_STORE_ORIGINS,
    read_only_scope_active,
    resolve_db_path,
    store_path_origin,
    store_scope,
)

# Configure logging
logger = logging.getLogger(__name__)

# Memory IDs are 32-char lowercase hex (secrets.token_hex(16) — see generate_id).
# Prefix resolution accepts any input shorter than the full ID, but enforces
# a minimum of 7 characters to match git's default core.abbrev and keep
# false-ambiguity rare on stores of practical size.
MEMORY_ID_LENGTH = 32
MIN_PREFIX_LENGTH = 7

# Cap for the disambiguation match list. Two queries fire only on the
# ambiguous branch (rare); the unique-match common path is unaffected.
# 50 is the human/agent disambiguation ceiling: with a >=7-char prefix
# (16^7 ~= 268M keyspace), >50 collisions implies caller-malformed input,
# at which point matches_capped/total_matches already signal truncation.
AMBIGUOUS_MATCH_CAP = 50

# Upper bound for prefix range scan: smallest ASCII char strictly greater
# than 'f' (max char of lowercase hex alphabet). If `generate_id` ever
# broadens the alphabet beyond [0-9a-f], this constant must change AND
# the alphabet-invariant test on generate_id will fail loudly.
_PREFIX_UPPER_BOUND_CHAR = "g"


# Characters that must not reach a rendered envelope raw: C0/DEL/C1 controls
# (`Cc`), Unicode format characters (`Cf`, which include every bidi override
# that can reorder or hide the text around it), unassigned code points and
# noncharacters (`Cn`), private-use code points (`Co`), lone surrogates
# (`Cs`), and the line/paragraph separators (`Zl`/`Zp`) that break
# line-oriented logs. JSON encoding alone does not cover this — a consumer
# that PARSES the envelope and prints the message re-materialises the
# character.
#
# `<` IS UNSAFE TOO, FOR A DIFFERENT REASON: it starts the marker form, so a
# literal marker-shaped input (`a<U+000A>b`) would render byte-identically to
# an escaped newline and the reader could not tell a typed sequence from an
# escape. Escaping it keeps the marker form decodable on its own
# (escape_for_envelope is injective), at the cost of displaying a literal
# `<` as `<U+003C>`.
def _char_is_unsafe_for_display(char: str) -> bool:
    return char == "<" or unicodedata.category(char) in (
        "Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"
    )


def escape_for_envelope(text: str) -> str:
    """Render a caller-supplied value inert in a rendered JSON envelope.

    Control, format, unassigned, private-use and lone-surrogate characters
    become visible ``<U+XXXX>`` markers, so a value the caller typed (a
    memory id or prefix, echoed back on an error path) cannot steer a
    terminal or reorder the visible text around it. A literal ``<`` is
    escaped as ``<U+003C>`` so the marker form is unambiguous: every
    ``<U+...>`` in the output was produced by this function.
    The value is ESCAPED, not refused: the caller needs to see what it sent
    in order to correct it.
    """
    return "".join(
        f"<U+{ord(char):04X}>" if _char_is_unsafe_for_display(char) else char
        for char in text
    )


class PrefixTooShortError(ValueError):
    """Raised when a memory-ID prefix is shorter than MIN_PREFIX_LENGTH."""

    def __init__(self, prefix: str, minimum: int = MIN_PREFIX_LENGTH):
        self.prefix = prefix
        self.minimum = minimum
        super().__init__(
            f"Prefix '{escape_for_envelope(prefix)}' is too short — "
            f"provide at least {minimum} characters."
        )


class AmbiguousPrefixError(LookupError):
    """Raised when a memory-ID prefix matches multiple stored memories.

    The `matches` attribute is a list of {"id", "raw_id", "context",
    "context_truncated"} dicts capped at AMBIGUOUS_MATCH_CAP entries.
    `total_matches` is the true row count and `matches_capped` is True when
    the cap was applied.

    `id` IS THE DISPLAY FORM and `raw_id` IS THE REPLAY FORM. A stored id may
    be caller-supplied, so it can carry a control or bidi character the
    store accepts and no display can safely render; `id` therefore goes
    through `escape_for_envelope` here, at the envelope's payload
    construction, so every consumer of this exception gets a safe display
    value. A caller that must replay the id (to disambiguate, then fetch)
    uses `raw_id`, which is deliberately NOT escaped and MUST NOT be printed.
    `context` stays RAW here: it is memory content, and escaping it would
    corrupt the data a programmatic caller reads. The CLI is the rendering
    boundary that scrubs and escapes it.
    """

    def __init__(
        self,
        prefix: str,
        matches: List[Dict[str, Any]],
        *,
        matches_capped: bool = False,
        total_matches: Optional[int] = None,
    ):
        self.prefix = prefix
        self.matches = matches
        self.matches_capped = matches_capped
        self.total_matches = total_matches if total_matches is not None else len(matches)
        suffix = f" (showing {len(matches)} of {self.total_matches})" if matches_capped else ""
        # THE MESSAGE IS A RENDERING SURFACE, so the echoed prefix is escaped
        # there. `self.prefix` stays RAW above: a caller may use it
        # programmatically, and the CLI is the boundary that renders. Reaching
        # this branch needs a stored id that is not hex-generated (create
        # honours a caller-supplied `id`), so the prefix can carry the same
        # control/bidi characters the memory-id envelope rule covers.
        super().__init__(
            f"Prefix '{escape_for_envelope(prefix)}' is ambiguous — "
            f"{self.total_matches} memories match.{suffix}"
        )


def get_db_path() -> Path:
    """Get the database file path, creating parent directories if needed.

    Resolves through `config.resolve_db_path()` on EVERY call. Holding the value
    in a module constant is what let an import-time binding outlive a later
    redirect, so the directory and the file are recomputed together here.

    COMPOSES NOTHING. This function used to build the file name itself, which
    made it a second derivation that could name a different file than the
    resolver did. It now adds the side effect and nothing else: resolve, then
    create the parent. The parent comes from the resolved FILE, so a scoped
    caller gets the directory of its own store rather than the default one.
    """
    path = resolve_db_path()
    # ⚠️ CREATE THE PARENT FOR A DERIVED PATH ONLY, NEVER FOR ONE THE CALLER
    # SUPPLIED. The side effect used to aim at the DEFAULT directory, so a
    # caller path reached `sqlite3.connect` with no tree behind it and a typo
    # failed loudly. When the resolver began returning the caller path, this
    # line began building the tree for it, and the typo started to SUCCEED. A
    # mistyped store takes the write, the command reports success, and the
    # record sits in a file the caller does not know about.
    #
    # LOUD TO SILENT IS THE WRONG DIRECTION ON A CALLER-CONTROLLED VALUE, so
    # the origin decides. The derived origins carry no caller path and MUST
    # keep the create, because production passes no path and has to reach and
    # build the real store. That is the redirect-never-refuse rule, and this
    # test does not weaken it: an unscoped caller takes the same branch it
    # always took.
    #
    # `setup` KEEPS CREATING A CALLER PATH, and that asymmetry is deliberate.
    # Bringing a store into existence at a named location is what `setup` is
    # for, so the create IS its operation. `save`, `get` and `archive_pin`
    # open a store that should be there, so an absent path is a typo, and the
    # correct answer to a typo is to fail. That leg runs through
    # `setup_memory.ensure_directories` and does not reach this line.
    #
    # ⚠️ THIS LINE COVERS THE DIRECTORY HALF ONLY, AND SAYING SO IS THE POINT.
    # A typo in the FILE NAME reaches a directory that is present, so this test
    # has nothing to refuse. THE FILE HALF LIVES AT THE CLI BOUNDARY, in
    # `cli.main`, which refuses a caller `--db-path` that names a store that is
    # absent.
    #
    # IT DOES NOT LIVE IN `get_connection`, and that location was tried and
    # rejected rather than passed over. A refusal there reaches each caller of
    # the connection factory, so it breaks the custom-store contract that
    # production and the test suite depend on. `get_connection` also cannot
    # tell a person from a library caller, so a refusal there answers a
    # question it cannot see. Read the two halves together. A comment that
    # claims the whole hazard from here would retire a question that another
    # file answers.
    # ⚠️ TWO READS OF THE RESOLVER STATE, AND WHAT MAKES THAT SAFE IS A
    # PROPERTY OF THE PACKAGE RATHER THAN OF THESE TWO LINES. The path arrives
    # from one call and the origin from a second, so a change to the store
    # scope between them would make the origin describe a different path. No
    # such change can occur here: this package starts NO THREAD, and neither
    # call yields, so no other code runs between them.
    #
    # THAT NO-THREAD PROPERTY NOW CARRIES TWO DEPENDENTS AND NOTHING PINS IT.
    # The ContextVar note in `config.py` records the first: a scope set in one
    # thread is not readable in a thread started after it, and the design
    # shipped that as a named residual because the package starts no thread.
    # This double read is the second. ONE DEPENDENT IS A RESIDUAL. TWO IS A
    # COUPLING, and a reader who adds a worker thread must find each of them.
    # A single call that returns the path and the origin together would remove
    # this one, at the cost of moving the `DB_FILENAME` composition out of
    # `resolve_db_path`, which a test pins as the ONE composing site.
    if store_path_origin() in DERIVED_STORE_ORIGINS:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Create and return a database connection.

    Args:
        db_path: Optional custom database path. Uses default if not provided.

    Returns:
        SQLite connection with foreign keys and a busy timeout set. The
        journal mode is WAL when this call CREATES the store or opens one that
        already holds data; an EMPTY store is left for `init_schema` to switch,
        because the pragma writes the database header and a read that returns
        nothing must not leave one behind. A connection opened inside a read
        scope never switches an existing store's journal mode.

    `db_path` IS AN INPUT TO THE RESOLVER, NOT A RIVAL OF IT. This used to read
    `db_path or get_db_path()`, which was a second rule for the same question:
    the connection opened the caller file while every layer that asked the
    resolver got the default one, and the two named different files inside a
    single invocation. Binding the scope instead sends the caller value THROUGH
    the resolver, so the layers below agree with the connection above.
    """
    with store_scope(db_path):
        path = get_db_path()

    # Track whether the DB file is newly created for permission hardening
    is_new = not path.exists()

    # A READ NEVER CREATES A STORE. Inside a read scope an absent store is
    # refused before the pre-create below and before `sqlite3.connect`, both
    # of which would leave a 0-byte file behind. This is the error sqlite
    # itself raises for a read-only open of a missing file.
    if is_new and read_only_scope_active():
        raise sqlite3.OperationalError(
            f"unable to open database file: {path} is absent, and a read "
            "does not create a store"
        )

    # TOCTOU mitigation: pre-create the file with 0o600 permissions atomically
    # so it is never world-readable, even briefly before sqlite3.connect() creates it.
    if is_new:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            # Race: another process created it between our check and os.open
            is_new = False
        except OSError:
            pass  # Fall through to sqlite3.connect which will create it

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # THE TIMEOUT GOES BEFORE ANY JOURNAL-MODE SWITCH. Converting a
    # delete-mode store to WAL contends for the database; with the timeout
    # still at sqlite's default ZERO, two concurrent opens can race that
    # conversion into `database is locked`. The timeout is a connection
    # setting and costs nothing on the no-op path.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")

    # THE WAL SWITCH IS DEFERRED ON AN EMPTY STORE. `PRAGMA journal_mode=WAL`
    # WRITES the database header (measured: a 0-byte file becomes 4096 bytes),
    # so running it here makes even a read that returns nothing leave a
    # database behind. On an uninitialized store the switch moves to
    # `init_schema`, the shared CREATE point, so every path that creates a
    # store (`ensure_initialized`, `initialize_database`/`setup`) leaves it in
    # WAL. A store that already has content switches here as before.
    try:
        empty_store = path.stat().st_size == 0
    except OSError:
        empty_store = False
    # A READ NEVER SWITCHES AN EXISTING STORE'S JOURNAL MODE, and an EMPTY
    # store is left for `init_schema` (the pragma writes the header, so a read
    # that returns nothing must not leave a database behind). A store THIS
    # call creates, or one that already holds data, is switched here as
    # before; only a read scope is exempt, so a read cannot convert a
    # DELETE-mode store. On an already-WAL store the pragma is a no-op.
    if (is_new or not empty_store) and not read_only_scope_active():
        # Enable WAL mode for corruption prevention and better concurrency
        conn.execute("PRAGMA journal_mode=WAL")

    # Harden WAL sidecar files on first creation (avoids a redundant syscall
    # on every connection). For a NEW store the WAL pragma above is what will
    # create them on the first write; `init_schema` also hardens after its own
    # WAL switch, because an existing 0-byte store is switched there instead.
    if is_new:
        _harden_sidecars(path)

    return conn


def _harden_sidecars(path) -> None:
    """chmod `-wal` and `-shm` to 0600 where they exist. Never raises.

    ONE HELPER FOR EVERY SITE that switches a store to WAL: `get_connection`
    for a new store and `init_schema` for an existing empty one. A sidecar
    can already hold memory content, so a mode inherited from the process
    umask is a disclosure, not a cosmetic difference.

    AN EMPTY PATH IS NO PATH. `PRAGMA database_list` reports `""` for an
    in-memory or temporary database, and `"" + "-wal"` would name a file in
    the current directory.
    """
    if not path:
        return
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        try:
            if sidecar.exists():
                os.chmod(str(sidecar), 0o600)
        except OSError:
            pass


@contextmanager
def db_connection(db_path: Optional[Path] = None):
    """
    Context manager for database connections.

    Handles connection lifecycle and ensures proper cleanup.

    Usage:
        with db_connection() as conn:
            cursor = conn.execute("SELECT * FROM memories")

    THE SCOPE COVERS THE WHOLE BODY, not only the connect call, so anything the
    caller does inside the block resolves to the same file. A pathless call
    INHERITS an enclosing scope, which is how the search and catch-up layers
    reach a caller store without one of them accepting a path parameter.
    """
    with store_scope(db_path):
        conn = get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """
    Initialize the database schema.

    Creates all required tables, indexes, and attempts to create
    the vector table for semantic search.

    Args:
        conn: Active database connection.
    """
    # Core memory table (rich objects)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            context TEXT,
            goal TEXT,
            active_tasks TEXT,
            lessons_learned TEXT,
            decisions TEXT,
            entities TEXT,
            reasoning_chains TEXT,
            agreements_reached TEXT,
            disagreements_resolved TEXT,
            project_id TEXT,
            session_id TEXT,
            -- NOT NULL AND NO DEFAULT, SO AN OMITTING INSERT FAILS RATHER
            -- THAN INVENTING A STAMP. A default has to pick a format, and
            -- `datetime('now')` picks a different one from the sole writer's
            -- `datetime.now(timezone.utc).isoformat()`; TEXT compares
            -- bytewise, so the two forms interleave wrongly and the rowid
            -- tiebreak cannot rescue it -- a wrong comparison is not a tie.
            -- Dropping the default alone would be worse than keeping it:
            -- the column would take NULL, which sorts lowest, burying the
            -- row last forever instead of merely misplacing it.
            -- Existing stores keep the schema they were created with.
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Files table (for graph network)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
            path TEXT NOT NULL,
            project_id TEXT,
            last_modified TEXT,
            UNIQUE(path, project_id)
        )
    """)

    # Memory to File relationships (graph edges)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_files (
            memory_id TEXT REFERENCES memories(id) ON DELETE CASCADE,
            file_id TEXT REFERENCES files(id),
            relationship TEXT DEFAULT 'modified',
            PRIMARY KEY (memory_id, file_id)
        )
    """)

    # File to File relationships (imports, tests, etc.)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_relations (
            source_file TEXT REFERENCES files(id),
            target_file TEXT REFERENCES files(id),
            relationship TEXT,
            PRIMARY KEY (source_file, target_file, relationship)
        )
    """)

    # Create indexes for efficient queries
    #
    # BEFORE ADDING AN INDEX THAT WOULD BECOME THE SOURCE OF THE ORDERING for
    # the two `created_at DESC, rowid DESC` queries, read the tied-rows arm in
    # tests/test_working_memory_fidelity.py
    # (TestTheStoreIsWhatTheNextSyncShows). It is the arm at risk: it catches
    # DELETING the tiebreak only because of where the ordering comes from
    # TODAY. Catching a FLIP to `rowid ASC` is not at risk anywhere, because
    # ascending reverses descending whenever two rows tie, whatever the plan.
    # The sibling arm in tests/test_memory_database.py
    # (TestSearchMemoriesByText) already cannot see a deletion and says so.
    #
    # WHAT HAPPENS TODAY: the planner takes idx_memories_project for the
    # filter and then SORTS through a temp b-tree, and that sorter emits tied
    # rows in ASCENDING rowid -- the reverse of what `rowid DESC` produces. So
    # deleting the tiebreak reverses the result and an arm goes red.
    #
    # WHAT AN INDEX WOULD CHANGE: once the ordering is served by an index walk
    # instead, the tie order becomes the index's. Measured on this schema with
    # four tied rows: under `(project_id, created_at)` -- ASCENDING, the shape
    # someone optimising this sort would naturally write -- the query plan
    # carries no sort at all and deleting the tiebreak returns exactly what
    # keeping it returns, so that deletion becomes undetectable with nothing
    # red. Under `(project_id, created_at DESC)` the deletion still reverses
    # the result and stays detected. The index that looks tailored to a DESC
    # order-by is the safe one; the plain ascending index is the one that
    # silences. That is a measured example on one fixture and not a law: the
    # planner is cost-based and may choose differently at other table sizes.
    #
    # idx_memories_created BELOW IS ALREADY SUCH AN ASCENDING INDEX AND
    # SILENCES NOTHING, because the planner does not use it here -- the filter
    # is on project_id, so idx_memories_project wins and the sort still runs.
    # It is a near miss rather than a counterexample.
    #
    # WHAT SURVIVES AN INDEX EITHER WAY: TestTheTiebreakReachesTheEngine in
    # tests/test_memory_database.py asserts the clause in the statement handed
    # to the engine and never looks at rows, so a deletion still reddens on
    # every plan. An index costs the output arm's deletion coverage, not all
    # of it.
    #
    # This is a condition on adding an index, not a prohibition. The sort is
    # unneeded overhead today, not forbidden forever.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_files_file ON memory_files(file_id)")

    # Attempt to create vector table for semantic search
    _init_vector_table(conn)

    # THE SHARED CREATE POINT SETS WAL, AFTER the CREATE statements.
    # `get_connection` deliberately does not switch an EMPTY store (the pragma
    # writes the header), so every path that creates the schema must switch
    # here — otherwise `initialize_database` (and the production `setup` path)
    # would build a NON-WAL store, losing crash safety and making a later
    # concurrent conversion contend. A read-only connection has already raised
    # on the CREATEs, so this line is only reached on a create path.
    conn.execute("PRAGMA journal_mode=WAL")
    # HARDEN THE SIDECARS THE SWITCH MAY HAVE CREATED. `get_connection` cannot
    # do it for an existing 0-byte store (it deliberately leaves an empty
    # store alone), so the create point must. The store path comes from the
    # connection: row_factory is not guaranteed for a raw test connection, so
    # the file column is read by position.
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        _harden_sidecars(row[2] if row is not None else None)
    except (sqlite3.Error, IndexError, TypeError):
        pass

    conn.commit()
    logger.info("Database schema initialized successfully")


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec into `conn`, then turn extension loading back off.

    Extension loading stays enabled only for the `sqlite_vec.load` call, so
    no later statement on the connection can load another extension (for
    example through SQL's `load_extension()`). Raises ImportError when
    sqlite-vec is not installed, and whatever `sqlite_vec.load` raises.
    """
    import sqlite_vec

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _init_vector_table(conn: sqlite3.Connection) -> bool:
    """
    Attempt to create the vector table for semantic search.

    Requires:
    1. pysqlite3 package (for extension loading support)
    2. sqlite-vec extension (for vector storage)

    Standard library sqlite3 has enable_load_extension disabled by default,
    so we check SQLITE_EXTENSIONS_ENABLED before attempting.

    Note: The embedding dimension depends on the active backend:
    - model2vec: 256 dimensions
    - sentence-transformers/sqlite-lembed: 384 dimensions

    If the dimension changes (e.g., switching backends), existing embeddings
    will need to be regenerated. The table will be recreated with the new dimension.

    Args:
        conn: Active database connection.

    Returns:
        True if vector table was created, False otherwise.
    """
    # Check if extension loading is possible
    if not SQLITE_EXTENSIONS_ENABLED:
        logger.info(
            "SQLite extension loading unavailable (using standard sqlite3). "
            "Vector storage disabled - semantic search will fall back to keyword search. "
            "Install pysqlite3 for full vector support: pip install pysqlite3"
        )
        return False

    try:
        try:
            load_sqlite_vec(conn)
        except ImportError:
            logger.warning(
                "sqlite-vec not installed. Vector search will be unavailable. "
                "Install with: pip install sqlite-vec"
            )
            return False

        # Get the embedding dimension from the embedding service
        # Import here to avoid circular imports
        from .embeddings import get_embedding_service
        embedding_dim = get_embedding_service().embedding_dimension

        # Check if table exists with different dimension and needs recreation
        _check_and_migrate_vector_table(conn, embedding_dim)

        # Create vector table with dynamic dimension
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
                memory_id TEXT PRIMARY KEY,
                project_id TEXT PARTITION KEY,
                embedding float[{embedding_dim}]
            )
        """)
        logger.info(f"Vector table ready (dimension={embedding_dim}, semantic search enabled)")
        return True

    except Exception as e:
        logger.warning(f"Could not create vector table: {e}. Semantic search will be unavailable.")
        return False


def _check_and_migrate_vector_table(conn: sqlite3.Connection, new_dim: int) -> None:
    """
    Check if vec_memories table exists with different dimension and handle migration.

    If the dimension has changed (e.g., from 384 to 256 when switching backends),
    drop the old table so it can be recreated with the correct dimension.
    Embeddings will need to be regenerated.

    Args:
        conn: Active database connection.
        new_dim: The new embedding dimension to use.
    """
    try:
        # Check if table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memories'"
        )
        if cursor.fetchone() is None:
            return  # Table doesn't exist, nothing to migrate

        # Probe dimension via MATCH query — sqlite-vec validates dimensions internally.
        # This gives a binary match/mismatch answer (not the actual old dimension).
        # Note: memory_init.py uses byte-length (len(blob)//4) instead because it needs
        # the actual old dimension value for diagnostic messages during re-embedding.
        import struct
        test_embedding = struct.pack(f'{new_dim}f', *([0.0] * new_dim))

        try:
            # Try a test query that would fail if dimensions don't match
            conn.execute(
                "SELECT memory_id FROM vec_memories WHERE embedding MATCH ? LIMIT 1",
                (test_embedding,)
            )
            # If we get here, dimensions match
            return
        except Exception as dim_error:
            error_str = str(dim_error).lower()
            if "dimension" in error_str or "mismatch" in error_str or "invalid" in error_str:
                logger.warning(
                    f"Vector table dimension mismatch detected. "
                    f"Dropping old table and recreating with dimension={new_dim}. "
                    f"Existing embeddings will need to be regenerated."
                )
                conn.execute("DROP TABLE IF EXISTS vec_memories")
                conn.commit()
            else:
                logger.warning(
                    f"Unexpected error during vector dimension probe: {dim_error}"
                )

    except Exception as e:
        logger.debug(f"Could not check vector table dimension: {e}")


def _migrate_ct_fields(conn: sqlite3.Connection) -> None:
    """
    Add CT-aware columns to existing databases (v3.8.0).

    New columns: reasoning_chains, agreements_reached, disagreements_resolved.
    Safe to call repeatedly — silently skips columns that already exist.
    """
    for col in ("reasoning_chains", "agreements_reached", "disagreements_resolved"):
        try:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def ensure_initialized(conn: sqlite3.Connection) -> None:
    """
    Ensure the database is initialized.

    Checks if tables exist and initializes schema if not.
    For existing databases, runs migrations to add any new columns.

    Args:
        conn: Active database connection.
    """
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
    )
    if cursor.fetchone() is None:
        if read_only_scope_active():
            # A READ MUST NOT CREATE THE SCHEMA. The read verbs refuse a
            # store without `memories` before opening it, so this is only the
            # backstop for a store truncated between that guard and this
            # call; creating it here would be the write the guard exists to
            # prevent.
            return
        # `init_schema` performs the WAL switch itself: it is the shared CREATE
        # point, so every initializer (this one, `initialize_database`, the
        # `setup` path) leaves the store in WAL. A read-only connection
        # raises SQLITE_READONLY there, which the caller's read-side fail-soft
        # handles.
        init_schema(conn)
    else:
        if read_only_scope_active():
            # NO MIGRATIONS ON A READ. `_migrate_ct_fields` issues ALTERs and
            # `_init_vector_table` issues a CREATE; both are writes, and a
            # read of an existing store must emit neither.
            return
        _migrate_ct_fields(conn)
        _init_vector_table(conn)


# =============================================================================
# Column allow-list
# =============================================================================
#
# The `memories` table allows the following columns in write paths. Any unknown
# key passed to save_memory() or update_memory() raises ValueError instead of
# being silently dropped (the old behavior). This matters
# because typos in payloads — especially sub-object keys that looked like
# top-level keys (`description` instead of `notes`) — used to disappear with
# no warning, causing silent data loss.
#
# `id`, `created_at`, and `updated_at` are all stripped from caller payloads
# BEFORE validation (see _STRIPPED_ON_INGRESS below). This preserves the
# "id/created_at silently ignored on update" contract
# (test_ignores_id_and_created_at) and extends the same tolerate-then-drop
# semantics to `updated_at`, which is a server-owned timestamp and must never
# be set by a caller — update_memory stamps it on every successful write.
SCALAR_FIELDS = frozenset({
    "context", "goal", "project_id", "session_id", "updated_at",
})

STRING_LIST_FIELDS = frozenset({
    "lessons_learned", "reasoning_chains",
    "agreements_reached", "disagreements_resolved",
})

DICT_LIST_FIELDS = frozenset({
    "active_tasks", "decisions", "entities",
})

LIST_FIELDS = STRING_LIST_FIELDS | DICT_LIST_FIELDS

# All columns update_memory may write to the DB (updated_at is server-owned
# and stamped inside update_memory; it stays in the set because the UPDATE
# statement sets it).
ALLOWED_UPDATE_COLUMNS = SCALAR_FIELDS | LIST_FIELDS

# Columns create_memory/save_memory may set; adds `id` and `created_at` which
# are legal on create but not on update.
ALLOWED_CREATE_COLUMNS = ALLOWED_UPDATE_COLUMNS | frozenset({"id", "created_at"})

# Server-owned fields stripped from caller payloads BEFORE validation. Passing
# any of these is tolerated (so callers can round-trip a full memory dict) but
# their values are dropped — update_memory stamps `updated_at`, and `id` /
# `created_at` are immutable after creation.
_STRIPPED_ON_INGRESS = frozenset({"id", "created_at", "updated_at"})

# Caller-facing view of the allowlists: what a user may legitimately put in a
# save/update payload. Stripped fields are tolerated but excluded here so
# error envelopes (cli.py) don't list server-owned fields as valid options.
# THESE TWO ARE CURRENTLY EQUAL AND MUST NOT BE COLLAPSED INTO ONE CONSTANT.
# Measured: their symmetric difference is EMPTY, both holding 11 fields. That
# is arithmetic, not duplication -- `ALLOWED_CREATE_COLUMNS` exceeds
# `ALLOWED_UPDATE_COLUMNS` by exactly `{id, created_at}`, and both of those are
# in `_STRIPPED_ON_INGRESS`, so subtracting it removes precisely the difference.
#
# They are DERIVED INDEPENDENTLY from two different allowlists and coincide only
# while the create/update difference stays a subset of the stripped set. Add one
# create-only field that is NOT stripped on ingress and they diverge the same
# day. Collapsing them now would be invisible until then, and then wrong.
CALLER_FACING_CREATE_FIELDS = ALLOWED_CREATE_COLUMNS - _STRIPPED_ON_INGRESS
CALLER_FACING_UPDATE_FIELDS = ALLOWED_UPDATE_COLUMNS - _STRIPPED_ON_INGRESS


def _reject_unknown_columns(
    updates: Dict[str, Any],
    allowed: frozenset,
    operation: str,
    memory_id: Optional[str] = None,
) -> None:
    """
    Raise ValueError if `updates` contains any keys not in `allowed`.

    The error message lists both the unknown keys (for debugging) and the
    allowed-field set (for discoverability). This is the primary public
    contract for the unknown-key refusal — callers rely on the message
    shape in the JSON error envelope rendered by cli.py::cmd_update.

    The prose list is DERIVED from `allowed` by subtracting the server-owned
    fields, and THE SAME DERIVED LIST IS ATTACHED TO THE RAISED EXCEPTION as
    `allowed_fields`, so cli.py renders its machine-readable array from this
    object rather than from a constant of its own choosing.

    THAT ATTACHMENT IS WHAT MAKES THE NO-DRIFT CLAIM STRUCTURAL. Deriving the
    prose here is not on its own enough: cli.py used to pick between
    CALLER_FACING_CREATE_FIELDS and CALLER_FACING_UPDATE_FIELDS by hand, so
    the two halves of one error object agreed only while every call site
    passed the `allowed` set matching the constant cli.py chose for that
    operation. That is a CONVENTION ACROSS TWO FILES, enforced by nothing -- a
    third operation, or the two constants transposed, would have reopened the
    exact split this closed, silently, while this docstring still promised
    they could not drift. Now both halves read one computation on one object,
    so a mismatch is not expressible rather than merely unlikely.

    The two previously disagreed: the sentence named server-owned fields the
    array omitted, so one error object gave two different answers about the
    same call depending on which half a caller read.
    """
    # The allowed_fields list is intentionally exposed in error envelopes —
    # it is how a caller discovers which keys are allowed.
    unknown = sorted(set(updates) - allowed)
    if not unknown:
        return
    # Validation above uses the FULL `allowed` set (create legitimately
    # tolerates id/created_at); only the human-facing list is narrowed, so
    # what is accepted is unchanged and only what is advertised is corrected.
    caller_facing = sorted(allowed - _STRIPPED_ON_INGRESS)
    allowed_list = ", ".join(caller_facing)
    unknown_list = ", ".join(repr(k) for k in unknown)
    # RAW, ESCAPED ONCE AT THE RENDERER. The CLI's ValueError arm renders
    # this message through `escape_for_envelope`, so escaping the id here
    # too would double-escape it: the injective escaper turns the '<' of a
    # real marker into '<U+003C>', and the caller could no longer match the
    # rendered id to the stored one. A programmatic caller gets the raw id,
    # like every other payload; display escaping is the CLI's job.
    target = f" (memory {str(memory_id)})" if memory_id else ""
    error = ValueError(
        f"Unknown memory field(s) for {operation}{target}: {unknown_list}. "
        f"Allowed fields: {allowed_list}"
    )
    # ONE list, rendered twice: the sentence above and the array cli.py
    # attaches are now the same object's `caller_facing`, so they cannot
    # disagree about a given call. Carried as an attribute rather than a new
    # exception type because every existing `except ValueError` must keep
    # catching this unchanged.
    error.allowed_fields = caller_facing
    raise error


# =============================================================================
# Additive list-field merge with content-hash dedup
# =============================================================================
#
# update_memory() used to replace list fields wholesale: passing
# {"lessons_learned": ["c"]} to a memory whose lessons_learned was ["a", "b"]
# would overwrite it to ["c"], silently losing the two existing items. The
# pinned "snapshot the current get(); read-merge-write-back with the FULL
# list" recovery protocol in CLAUDE.md was a workaround for this clobber.
#
# New default behavior:
#   - Incoming list items are concatenated onto the existing list and
#     de-duplicated by content hash. Repeat calls are idempotent.
#   - The existing `replace=True` kwarg (new in this PR) restores the old
#     wholesale-replace behavior for callers who intentionally want to
#     clobber a list (e.g. "correct a bad entry" workflows).
#
# Dedup key: SHA-256 over a canonical JSON representation. For dict list
# items (active_tasks, decisions, entities) we first round-trip through the
# relevant dataclass (via _canonicalize_dict_item) so that None-vs-missing
# optional keys produce a single canonical shape. For string list items
# we hash the stripped string.
#
# Atomicity: the merge SELECT and the subsequent UPDATE run inside a single
# BEGIN IMMEDIATE transaction. BEGIN IMMEDIATE (not the default DEFERRED)
# matters because DEFERRED would start as a reader and only upgrade to a
# writer on the UPDATE — leaving a window between our SELECT and our
# UPDATE where another writer could race in and invalidate our merged
# view. IMMEDIATE grabs the write lock at BEGIN, so the SELECT is
# guaranteed to see the state the UPDATE will overwrite.
#
# Sequencing invariant (architect §8 / ADR): dedup MUST ship in the same
# commit as the additive semantic. A dedup-less additive path would corrupt
# memories during the rollout window because the pinned CLAUDE.md protocol
# still tells callers to read-merge-write-back; without dedup, every call
# would double the list.


# Map of DICT_LIST_FIELDS → the dataclass used for canonicalization.
# Populated lazily in _canonicalize_dict_item to avoid a circular import
# with models.py.
_DICT_FIELD_CLASS: Dict[str, type] = {}


def _get_dict_field_class(field: str) -> type:
    """Lazy-init the DICT_LIST_FIELDS → dataclass map (avoids circular import)."""
    if not _DICT_FIELD_CLASS:
        from .models import Decision, Entity, TaskItem  # noqa: WPS433
        _DICT_FIELD_CLASS["active_tasks"] = TaskItem
        _DICT_FIELD_CLASS["decisions"] = Decision
        _DICT_FIELD_CLASS["entities"] = Entity
    return _DICT_FIELD_CLASS[field]


def _canonicalize_dict_item(
    field: str, item: Any, strict: bool = True,
) -> Dict[str, Any]:
    """
    Round-trip a dict list item through its dataclass to produce a canonical
    shape for content hashing. Also provides the write-path strict validation
    hook (unknown sub-object keys → ValueError) when strict=True.

    - A string input is treated as the primary field value (Entity("X") etc.).
    - A dict input is validated with strict=<flag>; strict=True raises on
      unknown keys (write path), strict=False drops them (read/hash path for
      legacy rows — see the lenient fallback in _merge_with_dedup).
    - Anything else raises TypeError to avoid silent data loss.
    """
    cls = _get_dict_field_class(field)
    if isinstance(item, str):
        obj = cls.from_dict(item)
    elif isinstance(item, dict):
        obj = cls.from_dict(item, strict=strict)
    else:
        raise TypeError(
            f"Invalid item in {field!r}: expected dict or str, got {type(item).__name__}"
        )
    return obj.to_dict()


def _nfc_canonical(canonical: Any) -> Any:
    """Recursively NFC-normalize string leaves in a canonical dict/list."""
    if isinstance(canonical, str):
        return unicodedata.normalize("NFC", canonical)
    if isinstance(canonical, dict):
        return {k: _nfc_canonical(v) for k, v in canonical.items()}
    if isinstance(canonical, list):
        return [_nfc_canonical(v) for v in canonical]
    return canonical


def _content_hash(field: str, item: Any) -> str:
    """
    Stable content-hash key for dedup. Stable across Python runs because:
    - sort_keys=True removes dict-iteration-order nondeterminism.
    - separators=(',', ':') removes whitespace variation between callers.
    - ensure_ascii=False hashes UTF-8 bytes directly (prevents \\uXXXX
      escape differences across Python versions for equivalent strings).
    - default=str stringifies non-JSON types deterministically (datetime's
      str() is ISO-8601-stable; repr is not).
    - String leaves are NFC-normalized so canonically equivalent Unicode
      forms (precomposed vs decomposed) hash identically.
    """
    if field in STRING_LIST_FIELDS:
        s = "" if item is None else str(item).strip()
        s = unicodedata.normalize("NFC", s)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()
    # DICT_LIST_FIELDS — canonicalize via dataclass round-trip first, then
    # NFC-normalize string leaves so equivalent Unicode forms collapse.
    canonical = _nfc_canonical(_canonicalize_dict_item(field, item))
    payload = json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_with_dedup(
    field: str,
    existing: List[Any],
    incoming: List[Any],
) -> List[Any]:
    """
    Concatenate (existing + incoming) and drop exact content-hash duplicates.

    Ordering: existing items come first (history is preserved); then incoming
    items in the order they were passed. First-seen wins on ties.

    Incoming dict items are canonicalized via _canonicalize_dict_item so the
    OUTPUT list contains the canonical (round-tripped) form. This matters
    because write-path strict validation happens inside _canonicalize_dict_item
    — we get the unknown-sub-object-key refusal "for free" as a side
    effect of dedup.
    """
    seen: Set[str] = set()
    out: List[Any] = []

    # Canonicalize dict list items; string list items pass through as-is.
    # Existing items (already stored via this path or the old path) are
    # canonicalized for the hash but kept as-is in the output so we don't
    # rewrite rows on touchless updates.
    def _materialize(item: Any) -> Any:
        if field in DICT_LIST_FIELDS:
            return _canonicalize_dict_item(field, item)
        # String list fields: normalize to stripped str for storage consistency.
        return "" if item is None else str(item).strip()

    for item in list(existing):
        try:
            h = _content_hash(field, item)
        except (ValueError, TypeError):
            # Existing row contains an item the strict canonicalizer rejects
            # (legacy stray key, unusual shape). Do NOT raise on the read
            # side — that would brick updates to legacy rows.
            #
            # LENIENT FALLBACK: before falling back to repr(), try lenient
            # canonicalization (strict=False drops unknown sub-object keys).
            # This makes existing legacy items hash-compatible with incoming
            # items that pass strict validation — preventing asymmetric
            # dedup where a legacy {"description": ...} row and an incoming
            # {"notes": ...} row for the same logical entity would never
            # collide because one hashes via repr() and the other via the
            # strict canonical form.
            try:
                if field in DICT_LIST_FIELDS:
                    canonical = _nfc_canonical(
                        _canonicalize_dict_item(field, item, strict=False)
                    )
                    payload = json.dumps(
                        canonical,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )
                    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                else:
                    # String list fields have no strict/lenient distinction
                    # (bytes or other exotics fall through to repr).
                    raise TypeError("non-dict field in lenient path")
            except (ValueError, TypeError):
                logger.warning(
                    "Legacy %s item failed canonicalization (strict + lenient); "
                    "keeping verbatim: %r",
                    field, item,
                )
                h = hashlib.sha256(repr(item).encode("utf-8")).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            out.append(item)
            continue
        if h in seen:
            continue
        seen.add(h)
        out.append(_materialize(item))

    for item in list(incoming):
        # Incoming items go through full canonicalization — this is the
        # write path where strict=True validation fires via _content_hash.
        h = _content_hash(field, item)
        if h in seen:
            continue
        seen.add(h)
        out.append(_materialize(item))

    return out


def _normalize_list_field(field: str, value: Any) -> List[Any]:
    """
    Coerce a list-field update value into a flat list. Rejects non-list inputs
    (e.g. a bare dict or string passed where a list is expected) by raising
    ValueError with a clear message — yet another "silently dropped" failure
    mode we don't want to tolerate.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            f"Field {field!r} must be a list, got {type(value).__name__}"
        )
    return value


# =============================================================================
# JSON Field Helpers
# =============================================================================

JSON_FIELDS = {"active_tasks", "lessons_learned", "decisions", "entities",
               "reasoning_chains", "agreements_reached", "disagreements_resolved"}


def _serialize_json_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Serialize JSON fields in a memory dict for database storage.

    Args:
        data: Memory dictionary with potential list/dict values.

    Returns:
        Dictionary with JSON fields serialized to strings.
    """
    result = data.copy()
    for field in JSON_FIELDS:
        if field in result and result[field] is not None:
            if isinstance(result[field], (list, dict)):
                result[field] = json.dumps(result[field])
    return result


def _deserialize_json_fields(row: Union[sqlite3.Row, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Deserialize JSON fields from database row to Python objects.

    Args:
        row: Database row or dictionary with serialized JSON fields.

    Returns:
        Dictionary with JSON fields deserialized to Python objects.
    """
    if isinstance(row, sqlite3.Row):
        result = dict(row)
    else:
        result = row.copy()

    for field in JSON_FIELDS:
        if field in result and result[field] is not None:
            if isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except json.JSONDecodeError:
                    # Keep as string if not valid JSON
                    pass
    return result


# =============================================================================
# Memory CRUD Operations
# =============================================================================

def generate_id() -> str:
    """Generate a unique ID for a new memory."""
    import secrets
    return secrets.token_hex(16)


def _unusable_identifier(value: object) -> bool:
    """One rule for every identifier the memory surfaces validate.

    Unusable: not a non-empty string, or it carries C0/DEL/C1 control
    characters or a lone surrogate. The surrogate clause exists because such
    a value can arrive from `json.loads` (which accepts unpaired
    `\\ud800`-style escapes) or from a filesystem/environment string decoded
    with surrogateescape, and none of them can bind to a utf-8 TEXT column:
    without the clause the value passes validation and dies at the store as
    an unhandled UnicodeEncodeError, bypassing the ValueError remedy channel.
    `save()`, `update()` and the constructor call this so the payload
    ingresses and the identity the instance ADOPTS cannot drift apart.

    SINKS, named because the rule is only as wide as where the value goes:
    `project_id` reaches the `memories.project_id` and `files.project_id`
    TEXT bindings and every project-scoped read filter; `session_id` reaches
    `memories.session_id` and a /tmp marker path component; `id` reaches the
    `memories.id` primary key.
    """
    return (
        not isinstance(value, str)
        or not value
        or any(
            ord(char) < 0x20
            or 0x7F <= ord(char) <= 0x9F
            or 0xD800 <= ord(char) <= 0xDFFF
            for char in value
        )
    )


# =============================================================================
# Project identity: the canonical grammar and the write-ingress rule
# =============================================================================
#
# ONE DEFINITION EACH, in this module, because the write ingress
# (`create_memory`, `update_memory`) and the identity canonicaliser must agree
# on what an identifier is. `project_identity` imports these; this module does
# not import `project_identity`, so there is no cycle.
#
# THE CANONICAL GRAMMAR DESCRIBES AN ID; it does not parse a remote URL. The
# remote-to-canonical canonicaliser lives in `project_identity`, and the shape
# below is what it must produce: `host[:port]/<segment>[/<segment>...]`.
#
# THE SHAPE IS ALSO A DISCRIMINATOR: every canonical id contains at least one
# `/`, and no POSIX basename can, so a legacy folder-name bucket can never be
# mistaken for a canonical id. Do not relax the `/` requirement.

CANONICAL_ID_MAX_LENGTH = 255

# Host: lowercase, alnum at BOTH ends, dots, hyphens and underscores (an ssh
# alias, or a resolvable name such as a docker-compose service) only inside, and no two dots together (an empty label names no
# host). The
# optional port sits in the HOST position and nowhere else; a colon anywhere
# else is refused by the segment class. `[1-9]` rejects a leading zero (and
# zero itself); the 65535 ceiling is checked numerically because a regex
# cannot express it. Path case is PRESERVED — owner/repo are case-sensitive
# on a case-sensitive host, so folding them would silently merge two
# repositories.
# `fullmatch` is used below rather than `$`-anchored `match`: in Python `$`
# also matches just BEFORE a trailing newline, so `host/owner/repo\n` would
# pass an anchored pattern. The newline is git's record terminator and is
# dropped by the parser; a value that still carries one is not an id.
# An uppercase `%XX` is an RFC 3986 percent-encoding of a byte outside the
# unreserved and sub-delimiter set.
# A host is a DNS name, or an IPv6 literal in brackets in its one compressed
# spelling (`is_canonical_id` checks the spelling).
CANONICAL_ID_RE = re.compile(
    r"(?:[a-z0-9](?:(?:[a-z0-9_-]|\.(?!\.))*[a-z0-9])?|\[(?P<ipv6>[0-9a-f:.]+)\])"
    r"(?::(?P<port>[1-9][0-9]{0,4}))?"
    r"(?:/(?:[A-Za-z0-9._~!$&'()*+,;=-]|%[0-9A-F]{2})+)+"
)


def _compressed_ipv6(text: str) -> bool:
    """Whether `text` is an IPv6 address in its compressed spelling
    (`ipaddress`), so one address has one id. An IPv4-mapped address is
    not: it has a second, plain IPv4 spelling."""
    import ipaddress

    try:
        address = ipaddress.IPv6Address(text)
    except ValueError:
        return False
    return address.ipv4_mapped is None and address.compressed == text


def is_canonical_id(value: object) -> bool:
    """Whether `value` is a canonical `host[:port]/owner/repo` id.

    Pure and total: any object is accepted and answers False rather than
    raising, because this runs on values that arrived from a remote, an
    override file, an environment variable and a stored row.

    This answers only whether a FINISHED id is in canonical shape. The
    parser's own rules that leave no trace in the result — one `.git` strip,
    default-port dropping, userinfo stripping — belong to the canonicaliser
    that produces the id, not here.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > CANONICAL_ID_MAX_LENGTH
    ):
        return False
    match = CANONICAL_ID_RE.fullmatch(value)
    if match is None:
        return False
    port = match.group("port")
    if port is not None and int(port) > 65535:
        return False
    if match.group("ipv6") is not None and not _compressed_ipv6(match.group("ipv6")):
        return False
    path = value.split("/", 1)[1]
    return not any(segment in (".", "..") for segment in path.split("/"))


def validate_identifier_for_write(value: object) -> None:
    """The write-ingress rule for project ids, session ids and ids.

    `_unusable_identifier` plus a 255-character cap. NO printable-ASCII
    restriction: a non-Latin directory name is a legitimate project basename,
    and refusing it would reject real stores. The cap matches the canonical
    grammar's own limit, so an id that can be detected can also be written.

    Raises:
        ValueError: naming the rule, never the value. A rejected value may
            hold control characters or a credential-bearing remote, and the
            remedy text is what callers surface.
    """
    if isinstance(value, str) and len(value) > CANONICAL_ID_MAX_LENGTH:
        raise ValueError(
            f"identifier must be at most {CANONICAL_ID_MAX_LENGTH} "
            "characters; nothing was written."
        )
    if _unusable_identifier(value):
        raise ValueError(
            "identifier must be a non-empty string without control characters "
            "(U+0000-U+001F, U+007F-U+009F) or lone surrogates "
            "(U+D800-U+DFFF); nothing was written."
        )


# =============================================================================
# Union read scope: one normalisation and one predicate for every seam
# =============================================================================

def normalise_project_scopes(
    project_ids: Optional[Union[str, Sequence[str]]],
) -> Optional[Tuple[str, ...]]:
    """Normalise a union read scope for every seam.

    `None` keeps its whole-store meaning (no filter). A single string is a
    ONE-ELEMENT scope, not a sequence of characters. An empty sequence — and
    an empty string, which is the same thing to this rule — normalises to
    `()`, which every caller MUST treat as NO ROWS, never as unscoped.

    THAT EMPTINESS IS THE TRAP: the seams below used to decide "apply a
    filter?" with a truthiness test, so a falsy scope silently dropped the
    predicate and turned a single-project read into a whole-store read. This
    function is the one place the distinction is made; callers test
    `is None`, never truthiness.

    Elements that are not non-empty strings are dropped: no row can carry
    them, and `IN (NULL)` never matches. A value that is neither None, a
    string nor iterable (an int) raises ValueError naming its type. A repeated id is kept once, in
    first-seen order, because a per-scope loop (one KNN per scope) would
    otherwise return the same memory twice.
    """
    if project_ids is None:
        return None
    if isinstance(project_ids, str):
        return (project_ids,) if project_ids else ()
    try:
        scopes = iter(project_ids)
    except TypeError:
        # NOT A SCOPE AT ALL (an int, an object): refused by name rather
        # than surfacing as a TypeError from deep inside a read.
        raise ValueError(
            "a read scope is None, a project id, or a sequence of project ids; "
            f"got {type(project_ids).__name__}"
        ) from None
    return tuple(dict.fromkeys(
        scope for scope in scopes
        if isinstance(scope, str) and scope
    ))


def project_scope_predicate(
    column: str, scopes: Sequence[str]
) -> Tuple[str, Tuple[str, ...]]:
    """The SQL predicate for a NON-EMPTY scope, with its bound parameters.

    An empty scope returns `("0", ())` — a predicate that matches no row.
    Never render `IN ()` (SQLite rejects it) and never drop the clause (that
    would return the whole store).

    `column` is a SQL identifier chosen by our own code, never caller input;
    only the values are parameterised.
    """
    if not scopes:
        return "0", ()
    placeholders = ",".join("?" for _ in scopes)
    return f"{column} IN ({placeholders})", tuple(scopes)


# =============================================================================
# Store primitives for the identity resolver
# =============================================================================

def count_project_memories(conn: sqlite3.Connection, project_id: str) -> int:
    """Count memories stored under one project id.

    One indexed COUNT (`idx_memories_project`), used by the resolver's
    two-count decision. A NULL-project row is not this project's row and is
    not counted, exactly as a scoped read excludes it.

    PRECONDITION: the store's schema is present. This is an identity read
    primitive, reached from read-only probes, so it never calls
    `ensure_initialized` (which would attempt migrations and the vector-table
    CREATE); on a store without the table it raises
    `sqlite3.OperationalError`, and the caller checks the schema first.
    """
    cursor = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE project_id = ?",
        (project_id,),
    )
    return cursor.fetchone()[0]


def project_bucket_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Every NON-NULL project id with its memory count, in one query.

    Feeds three callers: the resolver's exact-case candidate lookup against
    the stored bucket set, the per-bucket disclosure split, and the
    `project-id show/status` counts.

    NULL is excluded by construction: it is a real bucket in the store but
    never a project scope, and `IN (NULL)` never matches, so surfacing it as
    a candidate would produce a filter that silently selects nothing.

    PRECONDITION: the store's schema is present. This is an identity read
    primitive, reached from read-only probes, so it never calls
    `ensure_initialized` (which would attempt migrations and the vector-table
    CREATE); on a store without the table it raises
    `sqlite3.OperationalError`, and the caller checks the schema first.
    """
    cursor = conn.execute(
        "SELECT project_id, COUNT(*) AS n FROM memories "
        "WHERE project_id IS NOT NULL GROUP BY project_id"
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def bucket_session_counts(
    conn: sqlite3.Connection, project_id: str
) -> List[Tuple[Optional[str], int]]:
    """Every session id that wrote a memory into `project_id`, with how many.

    The legacy-admission evidence check reads this (`_legacy_session_census`
    in memory_api): a session that wrote into the bucket, whose recorded
    project directory is this repository, and which started after the
    repository's `description` file last changed (or whose repository has no
    `description` file) is evidence the bucket belongs here, and a session
    recorded at another checkout marks its rows as that checkout's. NULL and
    empty session ids are kept, each as its own entry, because their rows
    count as rows no session record can place. Ordered by session id, NULL
    first.

    PRECONDITION: the store's schema is present. This is an identity read
    primitive, reached from read-only probes, so it never calls
    `ensure_initialized` (which would attempt migrations and the vector-table
    CREATE); on a store without the table it raises
    `sqlite3.OperationalError`, and the caller checks the schema first.
    """
    cursor = conn.execute(
        "SELECT session_id, COUNT(*) FROM memories WHERE project_id = ? "
        "GROUP BY session_id ORDER BY session_id",
        (project_id,),
    )
    return [(row[0], row[1]) for row in cursor.fetchall()]


def _gate_root_paths(roots: Any) -> Tuple[str, ...]:
    """Resolve the containment roots the legacy gate accepts.

    A single `Path`/`str` is ONE root, never a sequence of characters. A
    sequence is every checkout the caller knows about. Falsy entries are
    dropped and every surviving spelling is resolved with `realpath`, so a
    symlinked checkout and its target compare equal. NEVER case-fold: the
    repository's containment rule is validate-as-given, anchor raw, and a
    fold that passes on a case-insensitive filesystem would miss here.
    Duplicates are removed so a caller that passes a main root already
    present in a worktree list does not test the same prefix twice.
    """
    if roots is None:
        return ()
    if isinstance(roots, (str, os.PathLike)):
        candidates = [roots]
    else:
        candidates = list(roots)
    resolved: list = []
    for candidate in candidates:
        if not candidate:
            continue
        root = os.path.realpath(str(candidate))
        if root not in resolved:
            resolved.append(root)
    return tuple(resolved)


def _gitfile_target(marker: str) -> Optional[str]:
    """The real path a `gitdir: <path>` file names, read whole as git reads
    it (`shared.git_helpers.gitfile_target`), or None. Never raises."""
    from shared.git_helpers import gitfile_target, read_git_file

    try:
        text = read_git_file(marker)
    except (OSError, ValueError):
        return None
    target = gitfile_target(text) if text is not None else None
    if target is None:
        return None
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(marker), target)
    try:
        return os.path.realpath(target)
    except (OSError, ValueError):
        return None


def _worktree_admin_of(root: str, root_common: str, name: str) -> bool:
    """Whether `<root_common>/worktrees/<name>` is the admin dir of `root`.

    Read from the COMMON DIR's side, never from the root's own `.git`: the
    admin dir's `gitdir` file must name `<root>/.git` back. Never raises.
    """
    from shared.git_helpers import read_git_file, worktree_admin_gitdir

    admin = os.path.join(root_common, "worktrees", name)
    try:
        text = read_git_file(os.path.join(admin, "gitdir"))
        back = worktree_admin_gitdir(text) if text is not None else ""
        if not back:
            return False
        if not os.path.isabs(back):
            back = os.path.join(admin, back)
        return os.path.realpath(back) == os.path.realpath(os.path.join(root, ".git"))
    except (OSError, ValueError):
        return False


def _owns_row_instead_of(
    directory: str, root: str, root_common: Optional[str]
) -> bool:
    """Whether a `.git` at `directory` makes it a different checkout of `root`.

    A ROOT'S OWN SUBMODULE STILL BELONGS TO THE ROOT: its `.git` is a file
    naming a git dir under `<root_common>/modules/`, or, when `root` is a
    linked worktree, under `<root_common>/worktrees/<root's admin>/modules/`
    (git keeps a worktree's submodules in that worktree's own admin dir), and
    a superproject session's rows inside it are the superproject's. Any other
    `.git` (a directory, or a file naming a git dir anywhere else) is a
    separate checkout, which owns the rows below it.

    `root_common` IS THE COMMON DIR GIT REPORTED for the root, never one this
    module re-derives from the root's own `.git` and `commondir` files: a
    second parser accepts shapes git refuses, so the exemption's anchor would
    be a value git never verified. None means no verified value, and then no
    nested `.git` is exempt.
    """
    marker = os.path.join(directory, ".git")
    if not os.path.lexists(marker):
        return False
    if root_common is None or os.path.isdir(marker) or os.path.islink(marker):
        return True
    target = _gitfile_target(marker)
    if target is None:
        return True
    if target.startswith(os.path.join(root_common, "modules") + os.sep):
        return False
    worktrees = os.path.join(root_common, "worktrees") + os.sep
    if not target.startswith(worktrees):
        return True
    name, _, rest = target[len(worktrees):].partition(os.sep)
    if not name or not rest.startswith("modules" + os.sep):
        return True
    return not _worktree_admin_of(root, root_common, name)


def _contained_by_root(
    resolved: str, root: str, nested: Dict[tuple, bool], root_common: Optional[str]
) -> bool:
    """Whether `resolved` belongs to the checkout at `root` itself.

    Under the root is not enough. A directory strictly between the root and
    the row that is a separate checkout (`_owns_row_instead_of`: a nested
    repository, or one unpacked inside another) owns the row, so an ancestor
    root must not count it; the root's own submodules do not end its claim.
    `root_common` is git's verified common dir for the root (None: no
    exemption). `nested` caches each answer for one gate call. A root that
    already ends in a separator (the filesystem root, `/`) is its own
    prefix.
    """
    prefix = root if root.endswith(os.sep) else root + os.sep
    if resolved != root and not resolved.startswith(prefix):
        return False
    directory = os.path.dirname(resolved)
    while directory != root and directory.startswith(prefix):
        key = (root, directory)
        if key not in nested:
            nested[key] = _owns_row_instead_of(directory, root, root_common)
        if nested[key]:
            return False
        directory = os.path.dirname(directory)
    return True


def _nearest_checkout(resolved: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    """The nearest directory at or above `resolved` that holds a `.git`
    (the checkout that owns the path), or None. The walk starts at the path
    itself, so a row that IS a checkout's root (a tracked "." or project
    directory) belongs to that checkout. `cache` keeps each directory's
    answer for one evidence call. Never raises."""
    directory = resolved
    seen = []
    found: Optional[str] = None
    while True:
        if directory in cache:
            found = cache[directory]
            break
        seen.append(directory)
        try:
            if os.path.lexists(os.path.join(directory, ".git")):
                found = directory
                break
        except (OSError, ValueError):
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for each in seen:
        cache[each] = found
    return found


def _within(path: str, root: str) -> bool:
    """Whether `path` is `root` or lies under it (both resolved)."""
    prefix = root if root.endswith(os.sep) else root + os.sep
    return path == root or path.startswith(prefix)


# The pre-change name of a directory whose `.git` could not be read or
# resolved. It equals no name, and an enclosing checkout carrying it gets no
# neutral carve-out (`_foreign_kind`): falling back to the folder name would
# silently reopen the worktree-nest case.
UNKNOWN_NAME = object()


def submodule_admin_path(path: Any) -> bool:
    """Whether `path` is a submodule admin dir: a `.git` component followed
    by `modules`, directly or after `worktrees/<name>`, in its resolved
    form. THE ONE COPY of the rule: `project_identity._submodule_admin_dir`
    delegates here, because this module must not import `project_identity`.
    False for None. Never raises."""
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


def _read_pre_change_name(directory: str) -> Any:
    """The name the pre-change resolver gave `directory`: the parent folder
    name of its common dir, as `PACTMemory._legacy_project_name` computes it
    (`common_dir.resolve().parent.name`), derived from files with no git call.

    No `.git`: the folder's own name. A `.git` directory: the folder's name.
    A `.git` symlink: its target's parent. A `.git` file: the git dir it
    names, then that git dir's `commondir` (a linked worktree's common dir is
    its main checkout's `.git`), read bounded through `read_git_file`. A
    submodule admin dir has no name (None), as the old resolver suppressed
    it. `UNKNOWN_NAME` when a `.git` cannot be read or resolved. Never
    raises.
    """
    common = _read_common_dir(directory)
    if common is None:
        return os.path.basename(directory)
    if common is UNKNOWN_NAME:
        return UNKNOWN_NAME
    return pre_change_name_of_common(common)


def _read_common_dir(directory: str) -> Any:
    """The common dir of the repository whose `.git` sits in `directory`,
    derived from files with no git call: None with no `.git`, UNKNOWN_NAME
    when one cannot be read or resolved. Never raises."""
    from shared.git_helpers import read_git_file

    marker = os.path.join(directory, ".git")
    try:
        info = os.lstat(marker)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return UNKNOWN_NAME
    try:
        if stat.S_ISDIR(info.st_mode):
            common = marker
        elif stat.S_ISLNK(info.st_mode):
            common = os.path.realpath(marker)
        else:
            target = _gitfile_target(marker)
            if target is None:
                return UNKNOWN_NAME
            commondir = os.path.join(target, "commondir")
            if os.path.lexists(commondir):
                text = read_git_file(commondir)
                if text is None or not text.strip():
                    return UNKNOWN_NAME
                common = os.path.realpath(os.path.join(target, text.strip()))
            else:
                common = target
    except (OSError, ValueError):
        return UNKNOWN_NAME
    return common


def git_dir_own_name(common: Any) -> str:
    """A git dir's own folder-name id: its name without a trailing `.git`, or
    its parent's name when it is named `.git` (`proj.git` and `proj/.git`
    both give `proj`). The id of a `--separate-git-dir` repository with no
    portable origin (`project_identity.repository_folder_name`). Never
    raises."""
    path = Path(common)
    if path.name == ".git":
        return path.parent.name
    if path.name.endswith(".git") and len(path.name) > len(".git"):
        return path.name[: -len(".git")]
    return path.name


def core_bare_answer(common: Any) -> Optional[bool]:
    """`core.bare` from the git dir's own `config` file, with the answers
    `project_identity._git_dir_is_bare` takes from `git config --local
    --bool`: True or False as git reads the boolean (a valueless `bare` is
    true, an empty `bare =` false), False when the key is unset, None when
    the file cannot be read, git refuses it as fatal, or the value is one git
    refuses as a boolean. The file is read as git's config parser reads it
    (`_git_config_values`): one file only (no includes), the last value
    wins, a leading UTF-8 BOM is skipped. Never raises."""
    from shared.git_helpers import git_config_bool

    try:
        with open(os.path.join(common, "config"), "rb") as handle:
            data = handle.read(_CONFIG_READ_LIMIT + 1)
    except (OSError, ValueError):
        return None
    if len(data) > _CONFIG_READ_LIMIT:
        return None
    values = _git_config_values(os.fsdecode(data), "core", "bare")
    if values is None:
        return None
    if not values:
        return False
    value = values[-1]
    if value is None:
        return True
    return git_config_bool(value) if value else False


def _git_config_values(text: str, section: str, key: str) -> Optional[List[Optional[str]]]:
    """The values of `<section>.<key>` (no subsection) in a git config file,
    parsed as git's `config.c` parses it: section and key names compared
    case-insensitively, a valueless key as None, a value's leading and
    trailing unquoted blanks dropped and inner runs kept, `"` quoting,
    `#`/`;` comments outside quotes, the escapes `\\\\`, `\\"`, `\\n`, `\\t`,
    `\\b` and a backslash-newline continuation, a value cut at a NUL as git's
    C string is. None for a file git refuses (a bad header, key, escape,
    unterminated quote or a line with no `=` after a key)."""
    if text.startswith("\ufeff"):
        text = text[1:]
    n, i = len(text), 0
    current: Optional[str] = None
    found: List[Optional[str]] = []
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c in "#;":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "[":
            # The header ends at the first `]` outside its quoted subsection.
            match = _CONFIG_HEADER_RE.match(text, i + 1)
            if match is None or not text.startswith("]", match.end()):
                return None
            header = match.group(0)
            current = header.lower() if _CONFIG_SECTION_RE.fullmatch(header) else None
            i = match.end() + 1
            continue
        start = i
        while i < n and (text[i].isascii() and (text[i].isalnum() or text[i] == "-")):
            i += 1
        name = text[start:i]
        if not name or not name[0].isalpha():
            return None
        while i < n and text[i] in " \t":
            i += 1
        if i >= n or text[i] in "\r\n":
            value: Optional[str] = None
        elif text[i] == "=":
            i += 1
            chars: List[str] = []
            pending = 0
            quoted = False
            started = False
            while True:
                if i >= n:
                    if quoted:
                        return None
                    break
                c = text[i]
                if c == "\n":
                    if quoted:
                        return None
                    break
                if c in " \t\r" and not quoted:
                    if started:
                        pending += 1
                    i += 1
                    continue
                if c in "#;" and not quoted:
                    while i < n and text[i] != "\n":
                        i += 1
                    break
                if pending:
                    chars.append(" " * pending)
                    pending = 0
                if c == "\\":
                    i += 1
                    nxt = text[i] if i < n else ""
                    if nxt == "\n":
                        i += 1
                        continue
                    escaped = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "b": "\b"}.get(nxt)
                    if escaped is None:
                        return None
                    chars.append(escaped)
                    started = True
                    i += 1
                    continue
                if c == '"':
                    quoted = not quoted
                    started = True
                    i += 1
                    continue
                chars.append(c)
                started = True
                i += 1
            value = "".join(chars).split("\0", 1)[0]
        else:
            return None
        if current == section and name.lower() == key:
            found.append(value)
        while i < n and text[i] != "\n":
            i += 1
    return found


# The config file grammar git accepts (`config.c`): a section name of
# alphanumerics, `-` and `.`, with an optional quoted subsection after a
# run of spaces, tabs and carriage returns (git 2.43 refuses a vertical tab, a
# form feed and a newline there).
_CONFIG_SECTION_RE = re.compile(r"[A-Za-z0-9.-]+")
_CONFIG_HEADER_RE = re.compile(r'[A-Za-z0-9.-]+(?:[ \t\r]+"(?:[^"\\\n]|\\.)*")?')


# A git config file larger than this is not read for `core.bare`.
_CONFIG_READ_LIMIT = 1 << 20


def own_folder_name(directory: str, cache: Optional[Dict[Any, Any]] = None) -> Any:
    """The folder-name id a checkout at `directory` with no portable origin
    files under after this change: its git dir's own name when that git dir
    is not `<directory>/.git` and the repository is not bare
    (`git_dir_own_name`, `core_bare_answer`), else the folder's name. An
    unreadable `core.bare` takes the git dir's name, which the rival check
    adds to the folder's name rather than replacing it.
    UNKNOWN_NAME when its `.git` cannot be read. Read from files, cached in
    `cache` beside `pre_change_name`'s entries. Never raises."""
    key = (directory, "own")
    if cache is not None and key in cache:
        return cache[key]
    common = _read_common_dir(directory)
    if common is UNKNOWN_NAME:
        name = UNKNOWN_NAME
    elif common is None or os.path.dirname(os.path.realpath(common)) == os.path.realpath(directory):
        name = os.path.basename(directory)
    elif core_bare_answer(os.path.realpath(common)) is True:
        # A bare hub's worktree files under its own folder's name, as
        # `project_identity.repository_folder_name` gives it.
        name = os.path.basename(directory)
    else:
        name = git_dir_own_name(os.path.realpath(common))
    if cache is not None:
        cache[key] = name
    return name


def pre_change_name_of_common(common: Any) -> Optional[str]:
    """The pre-change resolver's name for a repository whose common dir is
    `common`: the parent folder name of the resolved common dir, or None
    for a submodule admin dir (a name the superproject chose). THE ONE COPY:
    `_read_pre_change_name` (from files) and
    `PACTMemory._legacy_project_name` (from git's answer) both end here."""
    if submodule_admin_path(common):
        return None
    return common_dir_parent_name(common)


def common_dir_parent_name(common: Any) -> str:
    """The parent folder name of the resolved common dir `common`, with NO
    submodule check: the name the pre-change resolver gave any repository.
    `pre_change_name_of_common` adds the submodule suppression; the callers
    that want the name a suppression hides, or a decline's name, call this.
    Never raises."""
    try:
        return Path(common).resolve().parent.name
    except (OSError, RuntimeError, ValueError):
        return Path(common).parent.name


def pre_change_name(directory: str, cache: Optional[Dict[str, Any]] = None) -> Any:
    """`_read_pre_change_name`, cached per directory in `cache`."""
    if cache is None:
        return _read_pre_change_name(directory)
    if directory not in cache:
        cache[directory] = _read_pre_change_name(directory)
    return cache[directory]


def origin_url_in_config(config_path: Any) -> Optional[str]:
    """The first `remote.origin.url` in the git config FILE at `config_path`,
    read with no git, or None when the file holds none or uses any form this
    strict reader does not take.

    It agrees with `project_identity.read_origin`'s rule (the first value in
    the repository's own file) on the plain form a clone writes,
    `[remote "origin"]` then `url = <value>`, and refuses wherever that rule
    declines or this reader cannot follow git: EVERY line of the file is read
    before the answer, so it refuses a file that is not a regular file of at
    most `GIT_FILE_LIMIT` bytes, an include section anywhere in it, a header
    or key line it cannot read (a dotted header such as `[remote.origin]`,
    which git reads as that section, included), and any origin url (not only the first) that
    is empty, valueless, or holds a quote, a backslash, `#` or `;`. An
    empty one is what `read_origin` declines as `empty_entry`. Never raises.
    """
    from shared.git_helpers import read_git_file

    try:
        text = read_git_file(config_path)
    except (OSError, ValueError):
        return None
    if text is None:
        return None
    section: Optional[Tuple[str, Optional[str]]] = None
    urls: List[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            # No dot in the name: git reads a deprecated `[remote.origin]` as
            # remote.origin, so a dotted header refuses the file rather than
            # be skipped while git takes a url from it.
            header = re.fullmatch(r'\[([A-Za-z0-9-]+)(?:\s+"([^"\\]*)")?\]', line)
            if header is None or header.group(1).lower() in ("include", "includeif"):
                return None
            section = (header.group(1).lower(), header.group(2))
            continue
        if section != ("remote", "origin"):
            continue
        entry = re.fullmatch(r"([A-Za-z][A-Za-z0-9-]*)\s*(?:=\s*(.*))?", line)
        if entry is None:
            return None
        if entry.group(1).lower() == "url":
            value = (entry.group(2) or "").strip()
            if not value or any(char in value for char in "\"\\#;"):
                return None
            urls.append(value)
    return urls[0] if urls else None


# GitHub's own spelling rules, THE ONE COPY for a remote's id
# (`project_identity._identifier`) and a pin's (`pin_file_value`): GitHub
# resolves owner/repo case-insensitively and redirects www.github.com.
_GITHUB_ESCAPE_RE = re.compile(r"%[0-9a-f]{2}")


def github_folded_host(host: str, port: str) -> str:
    """`github.com` for a port-less `www.github.com`, else `host`."""
    return "github.com" if host == "www.github.com" and not port else host


def github_folded_segment(segment: str) -> str:
    """A github.com path segment lower-cased OUTSIDE its percent escapes,
    which stay upper-case as the id grammar requires (`%C3%A9`)."""
    return _GITHUB_ESCAPE_RE.sub(lambda m: m.group(0).upper(), segment.lower())


# RFC 3986 unreserved characters: a `%XX` naming one of these is decoded.
UNRESERVED_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_ANY_ESCAPE_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def canonical_escape(hex_pair: str) -> str:
    """A `%XX` escape as an id holds it: the character itself when it is
    unreserved (`%41` is `A`), else `%XX` upper-cased (`%2F` stays).
    THE ONE COPY, for a remote's path (`project_identity._normalize_segment`)
    and a pin's (`pin_file_value`), so both spell one repository alike."""
    decoded = chr(int(hex_pair, 16))
    return decoded if decoded in UNRESERVED_CHARS else "%" + hex_pair.upper()


def _unreserved_escapes_decoded(path: str) -> str:
    """`path` with each escaped unreserved character decoded, in any hex
    case (`%6a` is `j`); every other escape stays exactly as written."""

    def decoded(match: "re.Match[str]") -> str:
        char = chr(int(match.group(1), 16))
        return char if char in UNRESERVED_CHARS else match.group(0)

    return _ANY_ESCAPE_RE.sub(decoded, path)


def _pin_path_escapes(candidate: str) -> str:
    """A canonical pin with every path escape in its id spelling
    (`canonical_escape`), as a remote naming the same path gets."""
    hostport, _, path = candidate.partition("/")
    return hostport + "/" + _ANY_ESCAPE_RE.sub(lambda m: canonical_escape(m.group(1)), path)


# A GitHub owner or repository name: only the characters GitHub allows, `\Z`
# anchored (`$` would also match before a trailing newline), and never dots
# alone (`.`, `..` are path traversal in a REST url). THE ONE COPY, for a
# remote's id, a pin, and the backlog's `_valid_slug`.
GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\Z")


def is_github_name(name: object) -> bool:
    """Whether `name` is a valid GitHub repository name."""
    return isinstance(name, str) and bool(GITHUB_NAME_RE.match(name)) and set(name) != {"."}


# A GitHub owner (user or organization) name: ASCII letters, digits,
# hyphens and underscores, starting with a letter or digit, at most 39
# characters (GitHub docs, "Username considerations for external
# authentication"). A trailing or doubled hyphen is accepted (older accounts
# can hold one), and so is an underscore (an Enterprise Managed User's
# `_shortcode` suffix, `octocat_acme`): refusing a real owner would block
# that user's saves. `\Z` anchored.
GITHUB_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,38}\Z")


def is_github_owner(name: object) -> bool:
    """Whether `name` is a valid GitHub owner name (`GITHUB_OWNER_RE`)."""
    return isinstance(name, str) and len(name) <= 39 and bool(GITHUB_OWNER_RE.match(name))


def github_repo_path(path: str) -> Optional[str]:
    """The `owner/repo` a port-less github.com path names, as GitHub reads
    it: each part decoded and lower-cased, or None when the path is not
    exactly two GitHub names or the repository's name ends in `.git` in
    any case. THE ONE STEP for a remote's id and a pin, so every escaped
    spelling of one repository gives one id."""
    parts = [unquote(part, errors="surrogateescape") for part in path.split("/")]
    if len(parts) != 2 or not is_github_owner(parts[0]) or not is_github_name(parts[1]):
        return None
    if parts[1].lower().endswith(".git"):
        # GitHub removes a trailing `.git` from a name when the repository is
        # created, so no repository there has one: `r.git.git`, `r.GIT` and
        # an https or scp `r%2Egit` reach nothing (an `ssh://` path arrives
        # here decoded, with its one `.git` already dropped).
        return None
    return "/".join(part.lower() for part in parts)


def _github_folded_pin(candidate: str) -> Optional[str]:
    """A canonical pin with GitHub's spelling rules applied as a remote's id
    takes them (`project_identity._identifier`), or None when it names no
    GitHub repository. A port-less `www.github.com` becomes github.com; a
    `github.com` path, on any port, is lower-cased outside its escapes; and a
    port-less github.com path loses one trailing `.git` in any case and is
    read as a remote's is (`github_repo_path`): decoded and lower-cased, or
    None when it is not exactly owner/repo of GitHub names. Every other host keeps its spelling, a ported
    `www.github.com` included, exactly as a remote naming it does."""
    hostport, _, path = candidate.partition("/")
    host, colon, port = hostport.partition(":")
    if host == "ssh.github.com" and port in ("", "22", "443"):
        # GitHub's SSH-over-443 host is github.com itself, as an ssh or scp
        # remote naming it folds (`project_identity._identifier`); an id holds
        # no scheme, so a pin or payload naming it folds the same way.
        host, colon, port = "github.com", "", ""
    host = github_folded_host(host, port)
    if host != "github.com":
        return candidate
    if not port:
        # A pin pasted from a remote url keeps its `.git`: drop one, in any
        # case, as the remote's own id does. No GitHub repository name ends in
        # `.git` (`github_repo_path`), so this names no other repository.
        if path.lower().endswith(".git"):
            path = path[: -len(".git")]
        repo_path = github_repo_path(path)
        return None if repo_path is None else host + "/" + repo_path
    segments = path.split("/")
    return host + colon + port + "/" + "/".join(
        github_folded_segment(segment) for segment in segments
    )


# The scheme's own default port is dropped so two spellings of one endpoint
# share one id (`project_identity`'s remote grammar, and `folded_github_id`
# for a github.com id). Non-default ports are preserved: two different servers
# on one host must never merge.
SCHEME_DEFAULT_PORTS = {
    "https": 443,
    "http": 80,
    "ssh": 22,
    "git": 9418,
    "git+ssh": 22,
    "ssh+git": 22,
}


# What a github.com id is, for a refusal that names the rule
# (`folded_github_id`).
GITHUB_ID_RULE = (
    "a github.com id is exactly owner/repo. The owner holds only ASCII "
    "letters, digits, hyphens and underscores, starts with a letter or "
    "digit and is at most 39 characters; the repository holds only "
    "ASCII letters, digits, '.', '-' and '_' and does not end in .git."
)


def folded_github_id(value: Any, what: str) -> Any:
    """A caller-supplied github.com id spelled as a pin naming it is
    (`pin_file_value`): host, owner and repository lower-cased, a port-less
    `www.` host dropped and one trailing `.git` dropped, so
    `GitHub.com/Acme/Widget` names the bucket `github.com/acme/widget` that
    repository's sessions read. A port some scheme's remote drops as its
    default (`SCHEME_DEFAULT_PORTS`: 443, 80, 22 or 9418, or an
    empty port) is dropped, as that remote's id drops it; any other port is
    kept (an id holds no scheme). Any
    other host's value is returned unchanged except that, as for every
    value, leading and trailing spaces are stripped first, as a pin's are (a
    tab is a control character, refused before the fold); a non-string is
    returned unchanged. THE ONE FOLD for a save or
    update payload and a rekey target.

    Raises ValueError naming `what` and `GITHUB_ID_RULE` for a port-less
    github.com value no pin may hold (`github.com/Acme`,
    `github.com/o/r.git.git`): no GitHub remote gives such an id, so rows
    filed under it are rows no session reads."""
    if not isinstance(value, str):
        return value
    value = value.strip(" ")
    hostport, slash, path = value.partition("/")
    host, colon, port = hostport.partition(":")
    if host.lower() not in ("github.com", "www.github.com", "ssh.github.com"):
        return value
    if (
        host.lower() != "ssh.github.com"
        and colon
        and (not port or port in {str(p) for p in SCHEME_DEFAULT_PORTS.values()})
    ):
        # ssh.github.com's ports are the pin fold's (`_github_folded_pin`): it
        # is github.com only over ssh, whose ports are 22 and 443.
        hostport = host
        value = hostport + slash + path
    pin_spelled, _ = pin_file_value(value.encode("utf-8"))
    if pin_spelled is not None:
        return pin_spelled
    if ":" not in hostport:
        raise ValueError(f"{what} {value!r} names no GitHub repository: {GITHUB_ID_RULE}")
    return value


# The most bytes a `pact-project-id` pin file may hold (`pin_file_value`).
PIN_FILE_MAX_BYTES = 4096


def pin_file_value(raw: bytes) -> Tuple[Optional[str], Optional[str]]:
    """`(value, reason)` for the bytes of a `pact-project-id` pin file: THE
    ONE COPY of the pin's text rule, used by `project_identity.read_override`
    and by `checkout_is_same_owner`.

    The first line that is neither blank nor a `#` comment, stripped, after
    an encoding BOM is dropped, is the candidate; it must be a canonical id.
    Its host is lower-cased first, as a remote's is. A valid candidate then
    takes GitHub's spelling rules (`_github_folded_pin`):
    the case and www folds a remote's id takes, and one trailing `.git` of a
    port-less github.com pin dropped, checked again after the fold; a
    port-less github.com pin that is not owner/repo, or whose repository
    still ends in `.git`, is "not_canonical". On every other host a trailing
    `.git` is kept, as a remote on it can give one.
    `reason` is None for a usable value, else "too_long" (over
    `PIN_FILE_MAX_BYTES` bytes, or a candidate over the id length cap),
    "not_utf8", "control_chars", "not_canonical" or "empty". Never raises.
    """
    if len(raw) > PIN_FILE_MAX_BYTES:
        return None, "too_long"
    try:
        # `utf-8-sig` drops an encoding BOM. `validate_identifier_for_write`
        # does not reject U+FEFF (it is not a control character), so a decoded
        # BOM would otherwise become an invisible first character of the id.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "not_utf8"
    # LINES END AT "\n" ONLY (one "\r" before it is dropped): `splitlines`
    # also breaks at \x0b, \x0c, \x1c-\x1e, \x85 and U+2028/2029, which would
    # cut a control character off a candidate instead of refusing it.
    for line in text.split("\n"):
        candidate = (line[:-1] if line.endswith("\r") else line).strip(" \t")
        if not candidate or candidate.startswith("#"):
            continue
        if len(candidate) > CANONICAL_ID_MAX_LENGTH:
            return None, "too_long"
        try:
            validate_identifier_for_write(candidate)
        except ValueError:
            return None, "control_chars"
        # A host is case-insensitive, and a remote's id lower-cases it. An
        # escaped unreserved character is decoded in any hex case, as a
        # remote's is; any other escape keeps its spelling, so a lower-case
        # one (`%c3`) is still refused by the canonical check.
        hostport, slash, path = candidate.partition("/")
        if not hostport.isascii():
            # Checked BEFORE lower-casing, which maps the Kelvin sign onto "k".
            return None, "not_canonical"
        candidate = hostport.lower() + slash + _unreserved_escapes_decoded(path)
        if not is_canonical_id(candidate):
            return None, "not_canonical"
        folded = _github_folded_pin(_pin_path_escapes(candidate))
        if folded is None or not is_canonical_id(folded):
            return None, "not_canonical"
        return folded, None
    return None, "empty"


def checkout_is_same_owner(checkout: str, own_id: str, own_url: Optional[str]) -> bool:
    """Whether `checkout`, a directory a store row names, is a plain clone of
    this session's repository, judged from its files: its usable pin
    (`pin_file_value`, the rule the resolver reads a pin by) is `own_id`,
    or, with no usable pin, the first origin url of its own config
    (`origin_url_in_config`) is EXACTLY `own_url`, the url this session's id
    comes from (None when its id comes from a pin), compared as written, not
    normalized. A pin the resolver would ignore is ignored here too.

    FILES ONLY, NO GIT: no git command runs on a path a store row names. So
    only a checkout whose `.git` is a directory is judged, read straight from
    it. A `.git` file or symlink (a linked worktree, a separate git dir, or a
    pointer at another repository), a usable pin that is not `own_id`, a url
    `origin_url_in_config` refuses, and a url spelled differently from
    `own_url` (ssh against https, with or without `.git`, even for one
    repository) are not the same owner, so such a checkout stays a rival
    (fail closed). Never raises."""
    from shared.git_helpers import read_git_file

    try:
        git_dir = os.path.join(checkout, ".git")
        if not stat.S_ISDIR(os.lstat(git_dir).st_mode):
            return False
        pin = os.path.join(git_dir, "pact-project-id")
        if os.path.lexists(pin) and not os.path.islink(pin):
            text = read_git_file(pin)
            value = pin_file_value(os.fsencode(text))[0] if text is not None else None
            if value is not None:
                return value == own_id
        url = origin_url_in_config(os.path.join(git_dir, "config"))
        return own_url is not None and url == own_url
    except (OSError, ValueError):
        return False


class _EvidenceWalk:
    """The per-call caches one `bucket_evidence` call shares across rows."""

    def __init__(
        self,
        recorded_dirs: Optional[Callable[[], Any]],
        same_owner: Optional[Callable[[str], bool]] = None,
    ):
        self.checkouts: Dict[str, Optional[str]] = {}
        self.names: Dict[str, Any] = {}
        self._recorded_dirs = recorded_dirs
        self._recorded: Optional[Any] = None
        self._same_owner = same_owner
        self._same: Dict[str, bool] = {}

    def same_owner(self, checkout: str) -> bool:
        """Whether `checkout` is a plain clone of this session's repository
        by its pin or its origin url as written (`checkout_is_same_owner`),
        per the caller's predicate, cached per checkout. False with no predicate or when it raises: an
        unreadable identity stays a rival."""
        if self._same_owner is None:
            return False
        if checkout not in self._same:
            try:
                self._same[checkout] = bool(self._same_owner(checkout))
            except Exception:
                self._same[checkout] = False
        return self._same[checkout]

    def recorded(self, directory: str) -> bool:
        """Whether a session record names `directory` as its project_dir,
        loaded on first use only."""
        if self._recorded is None:
            try:
                self._recorded = set(self._recorded_dirs()) if self._recorded_dirs else set()
            except Exception:
                self._recorded = set()
        return directory in self._recorded


def _foreign_kind(
    resolved: str, bucket: str, roots: Tuple[str, ...], walk: _EvidenceWalk
) -> Optional[str]:
    """`"checkout"` or `"folder"` when an existing, uncontained row is
    evidence of another owner, else None.

    (a) Its nearest checkout is not nested inside a gate root, and that
    checkout's pre-change name, its folder's name or its folder-name id
    (`own_folder_name`: its git dir's own name for a `--separate-git-dir`
    checkout) is the bucket's, or
    its name cannot be read: a rival checkout. Under folder-name keying a checkout with another name could
    only have written another bucket, so a row in it is a cross-reference,
    whether the checkout lies elsewhere or around a root (a `$HOME` dotfiles
    repository). One nested inside a root is not a rival.
    A SAME-OWNER CHECKOUT IS NO RIVAL under either rule: a plain clone whose
    usable pin is this session's id, or whose first origin url is this
    repository's own url as written (`walk.same_owner`,
    `checkout_is_same_owner`; read from files, no git), wrote the bucket as
    this repository did.
    (b) THE NAMED-DIRECTORY RULE: a STRICT ancestor directory (the row itself
    never counts) whose pre-change name is the bucket's, which is neither a
    gate root nor inside one, and which carries a project marker: a `.git`
    (a checkout), or a `.claude` or a session record naming it (a folder).
    It applies even around a gate root (a hostile root planted inside the
    victim). An unmarked `~/.config/<B>` or `/etc/<B>` is neutral.
    """
    nearest = _nearest_checkout(resolved, walk.checkouts)
    if nearest is not None and not any(_within(nearest, root) for root in roots):
        name = pre_change_name(nearest, walk.names)
        own = own_folder_name(nearest, walk.names)
        if (
            name is UNKNOWN_NAME
            or bucket in (name, os.path.basename(nearest), own)
        ) and not walk.same_owner(nearest):
            return "checkout"
    directory = os.path.dirname(resolved)
    while True:
        if not any(_within(directory, root) for root in roots):
            has_git = os.path.lexists(os.path.join(directory, ".git"))
            if has_git or os.path.basename(directory) == bucket:
                name = pre_change_name(directory, walk.names)
                if name is not UNKNOWN_NAME and name == bucket:
                    if has_git:
                        # A same-owner clone is skipped whole: its `.claude` or
                        # a session record naming it makes it no rival folder.
                        if not walk.same_owner(directory):
                            return "checkout"
                    elif os.path.lexists(os.path.join(directory, ".claude")) or walk.recorded(directory):
                        return "folder"
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


# The classes a bucket's `files.path` rows fall in (`bucket_evidence`).
BUCKET_EVIDENCE_KEYS = (
    "file_rows", "absolute_rows", "contained_rows", "foreign_rows",
    "foreign_checkout_rows", "foreign_folder_rows",
    "neutral_outside", "vanished", "unresolvable", "relative", "roots",
)


def _linked_after_description(
    conn: sqlite3.Connection, file_id: Any, common_dir: Optional[str],
    missing_counts: bool = True, memory_bucket: Optional[str] = None,
) -> bool:
    """Whether a memory linked to file row `file_id` was saved after
    `<common_dir>/description` last changed (its ctime), the date a clone or
    `git init` gives this repository. No description file: no ordering
    check, so `missing_counts` (True, as a session record's
    `_description_predates` rule; False for an earlier id, which no
    undated row may place). With `memory_bucket`, only that bucket's own
    memories date the row (an earlier id is dated by its own memories).
    No common dir, a description that is not a regular file, or a creation
    time without a zone: False. Never raises."""
    if not common_dir:
        return False
    try:
        described = os.lstat(os.path.join(common_dir, "description"))
    except FileNotFoundError:
        return missing_counts
    except (OSError, ValueError):
        return False
    if not stat.S_ISREG(described.st_mode):
        return False
    try:
        rows = conn.execute(
            "SELECT m.created_at FROM memory_files l "
            "JOIN memories m ON m.id = l.memory_id WHERE l.file_id = ?"
            + (" AND m.project_id = ?" if memory_bucket is not None else ""),
            (file_id,) if memory_bucket is None else (file_id, memory_bucket),
        ).fetchall()
    except sqlite3.Error:
        return False
    for (created,) in rows:
        try:
            when = datetime.fromisoformat(str(created))
        except ValueError:
            continue
        if when.tzinfo is not None and when.timestamp() > described.st_ctime:
            return True
    return False


def _realpath_by_folder(path: str, real_dirs: Dict[str, str]) -> str:
    """`os.path.realpath(path)`, resolving each parent folder once per call
    of `bucket_evidence` (`real_dirs`): a bucket's rows share a few folders,
    and resolving every row's whole path walked each folder again. The last
    component is followed when it is a symlink; anything unusual (a NUL,
    `.` or `..` last) takes the plain realpath. Raises as realpath does."""
    folder, name = os.path.split(path)
    if "\0" in path or name in ("", ".", ".."):
        return os.path.realpath(path)
    real_folder = real_dirs.get(folder)
    if real_folder is None:
        real_folder = real_dirs[folder] = os.path.realpath(folder)
    candidate = os.path.join(real_folder, name)
    return os.path.realpath(candidate) if os.path.islink(candidate) else candidate


def bucket_evidence(
    conn: sqlite3.Connection,
    bucket: str,
    roots: Optional[Union[Path, str, Sequence[Union[Path, str]]]],
    *,
    root_common: Optional[Union[Path, str]] = None,
    recorded_dirs: Optional[Callable[[], Any]] = None,
    same_owner: Optional[Callable[[str], bool]] = None,
    dated_only: bool = False,
    submodule_rows: Optional[List[Any]] = None,
) -> Dict[str, int]:
    """Count a bucket's file rows by what they show about its owner.

    COUNTS ONLY. The decision is `memory_api.decide_bucket`'s; nothing here
    says whether the bucket is read.

    Each `files.path` row of `bucket` falls in exactly one class:
      - `relative`: not absolute; it carries no location;
      - `unresolvable`: absolute, but `realpath` raises (an embedded NUL);
      - `contained_rows`: its resolved path lies under a gate root, and no
        directory strictly between the root and the row is a separate
        checkout (`_contained_by_root`; the root's own submodules stay
        contained), and a memory linked to it was saved after this
        repository's `description` last changed (`_linked_after_description`,
        "dated"), or its file still exists. A file that exists counts even
        beside missing, undated rows (a re-clone where one file was renamed
        upstream); an unrelated clone in a deleted project's folder that
        shares a file such as a README therefore places the bucket too, as
        every same-named folder did before this change.
        With `dated_only` only dated rows count, and a row in one of the
        root's submodules is skipped: in no class, though it counts in
        `file_rows` and `absolute_rows`, and its id is appended to
        `submodule_rows` when a list is given (an earlier canonical id,
        `memory_api.PACTMemory._earlier_canonical_buckets`);
      - `vanished`: the path no longer exists, and it is not contained, or
        it is contained but does not count by the rule above (a deleted file
        of an earlier project);
      - `foreign_rows`: exists, not contained, and evidence of another owner
        (`_foreign_kind`), split into `foreign_checkout_rows` (the rival is
        a git checkout) and `foreign_folder_rows` (a marked non-git folder);
      - `neutral_outside`: exists, not contained, not foreign: in no
        checkout, in a checkout that encloses a root, or in one nested inside
        a root.
    `foreign_rows` is the only class against this repository and
    `contained_rows` the only class for it; the rest are disclosed only.
    `absolute_rows` counts every absolute row; `roots` the gate roots.

    `roots` is the identity root plus its two-way-linked worktrees, which the
    caller computes (this module runs no git); a single `Path`/`str` is one
    root. `root_common` is the common dir git reported for them, which
    exempts the roots' own submodules from the nested-checkout rule; None
    exempts none, the fail-closed direction. Containment resolves both sides
    and compares literally, with no case folding. `recorded_dirs` returns the
    resolved `project_dir` of every recorded session; it is called at most
    once, and only when a folder named like the bucket has no other marker.
    `same_owner(checkout)` answers whether a checkout outside the roots is
    a plain clone of the session's repository by its usable pin or its origin
    url as written, read from files with no git (`checkout_is_same_owner`),
    and a checkout it answers True for is no rival.
    None treats every such checkout as a possible rival.

    PRECONDITION: the store's schema is present (a read-only identity
    primitive: it never runs `ensure_initialized`; on a store without the
    table it raises `sqlite3.OperationalError`). Never raises on a row.
    """
    # BY POSITION, so a plain connection (no sqlite3.Row factory) works too.
    cursor = conn.execute(
        "SELECT id, path FROM files WHERE project_id = ?",
        (bucket,),
    )
    root_paths = _gate_root_paths(roots)
    nested: Dict[tuple, bool] = {}
    walk = _EvidenceWalk(recorded_dirs, same_owner)
    verified_common: Optional[str] = None
    if root_common:
        try:
            verified_common = os.path.realpath(str(root_common))
        except (OSError, ValueError):
            verified_common = None
    counts = dict.fromkeys(BUCKET_EVIDENCE_KEYS, 0)
    counts["roots"] = len(root_paths)
    real_dirs: Dict[str, str] = {}
    strict: Dict[tuple, bool] = {}
    for row in cursor.fetchall():
        counts["file_rows"] += 1
        file_id, path = row[0], row[1]
        if not path or not os.path.isabs(path):
            counts["relative"] += 1
            continue
        counts["absolute_rows"] += 1
        try:
            resolved = _realpath_by_folder(path, real_dirs)
        except (OSError, ValueError):
            counts["unresolvable"] += 1
            continue
        contained = any(
            _contained_by_root(resolved, root, nested, verified_common)
            for root in root_paths
        )
        # AN EARLIER ID (`dated_only`) TAKES NO SUBMODULE ROW: a submodule has
        # its own origin and id, so its rows neither place the superproject's
        # earlier id nor count against it; they are skipped.
        if contained and dated_only and not any(
            _contained_by_root(resolved, root, strict, None) for root in root_paths
        ):
            if submodule_rows is not None:
                submodule_rows.append(file_id)
            continue
        if contained:
            # A CONTAINED ROW PLACES THE BUCKET HERE ONLY IF ITS FILE IS STILL
            # IN THIS CHECKOUT, or a memory linked to it was saved after this
            # repository's `description` last changed. Otherwise the path may
            # belong to an earlier project that once lived in this folder (an
            # unrelated repository cloned into a deleted project's path), so
            # it counts as vanished.
            # An existing file counts without a date (legacy rule), so the
            # per-row date query runs only where it can change the answer.
            if (not dated_only and os.path.lexists(resolved)) or _linked_after_description(
                conn, file_id, verified_common, missing_counts=not dated_only,
                memory_bucket=bucket if dated_only else None,
            ):
                counts["contained_rows"] += 1
            else:
                counts["vanished"] += 1
        elif not os.path.lexists(resolved):
            counts["vanished"] += 1
        else:
            kind = _foreign_kind(resolved, bucket, root_paths, walk)
            if kind is None:
                counts["neutral_outside"] += 1
            else:
                counts["foreign_rows"] += 1
                counts[f"foreign_{kind}_rows"] += 1
    return counts


def rekey_project(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    dry_run: bool = True,
    *,
    own_folder_name_target: bool = False,
) -> Dict[str, Any]:
    """Move an ENTIRE project bucket from `from_id` to `to_id`, atomically.

    This is the only path that rewrites rows. The read union never moves
    anything, and a save binds the canonical id, so a rekey is an explicit
    operator action: `--dry-run` is the default posture.

    THIS FUNCTION OWNS ITS TRANSACTION. A connection with an open transaction
    is refused with ValueError, because the move must be one unit and a
    caller's pending writes would otherwise be committed with it or rolled
    back with it. Apply runs `BEGIN IMMEDIATE ... COMMIT`; a failure before
    COMMIT leaves the old bucket intact, never a partial move.

    DRY RUN IS THE SAME CODE PATH, ROLLED BACK. A SAVEPOINT wraps the work
    and is rolled back and released before returning, so the reported
    counters are what an apply would produce. The CLI's dry run calls this
    on a temporary copy of the store (`cli._rekey_on_copy`), so the store
    itself is never opened for writing.

    NO SCHEMA IS EVER CREATED. A store without the `memories` table has
    nothing to move: the zero counters come back with `schema_absent: True`
    and no DDL runs, in a dry run or an apply.

    FILES. `UNIQUE(path, project_id)` means a path can already exist under
    both ids. A path present only under `from` is updated in place, and its
    links follow the file id unchanged. A CONFLICTING path keeps the target
    row: its memory links and its file relations are re-pointed and the old
    row is then deleted. `relations_repointed` counts each relation with at
    least one end on such a row ONCE, taken before the move, so an edge whose
    two ends both merge is one edge and so is a SELF-relation. A re-pointed
    edge that already exists under the target is dropped as an exact
    duplicate: the key `(source_file, target_file, relationship)` includes the
    relationship, so no relationship is ever lost and there is no
    `relations_conflicted` counter to match `links_conflicted`.

    MEMORY LINKS USE A DETERMINISTIC KEEPER RULE. `memory_files` is keyed
    `(memory_id, file_id)`, so when the target already carries a link for the
    same pair, the TARGET's relationship is kept and the moved one is
    DROPPED. `links_repointed` counts the links on file rows MERGED into an
    existing target row (links on files moved in place are not re-pointed and
    are not counted), and `links_conflicted` counts the ones whose
    relationship was dropped because the target named that pair differently.

    VECTORS. A vec0 partition key cannot be UPDATEd in place, so each moved
    memory's row is read, deleted and re-inserted under the target with the
    SAME embedding blob. A missing vector is skipped. When the vector table
    cannot be OPENED, nothing is touched and `vectors_pending` reports the
    number of moved memories; the partition-aware catch-up repairs them.

    NULL-PROJECT ROWS ARE NEVER MOVED and are reported in
    `null_project_memories` and `null_project_files`: NULL is a real bucket,
    not a basename, and moving it would change what every scoped read sees.

    `from_bucket_empty` is True when no memory and no file row was filed
    under `from_id` at the start: a typo, or a bucket already moved.

    A FAILURE ALWAYS PROPAGATES AS ITSELF. Cleanup runs only while SQLite
    still reports an open transaction, so a statement that made SQLite roll
    back on its own is not masked by a "no such savepoint" from the cleanup.

    Args:
        conn: A database connection with NO open transaction.
        from_id: The entire bucket to retire.
        to_id: The canonical id to move it to.
        dry_run: True computes and rolls back; False commits.
        own_folder_name_target: the caller has established that `to_id` is
            the calling session's own folder-name id and `from_id` its legacy
            candidate (`cli._own_folder_name_target`), the one non-canonical
            target allowed.

    Returns:
        The counters dict. Every key is always present.

    Raises:
        ValueError: an id fails the write-ingress rule, the target is not
            canonical, or `conn` already has an open transaction.
    """
    validate_identifier_for_write(from_id)
    validate_identifier_for_write(to_id)
    # A github.com target takes the payload fold (`folded_github_id`), so the
    # rows land in the bucket that repository's sessions read; `from_id` is
    # not folded, since it names a stored bucket, whatever its spelling.
    try:
        to_id = folded_github_id(to_id, "the rekey target")
    except ValueError as exc:
        raise ValueError(f"{exc} Nothing was moved.") from None
    # THE LIBRARY ENFORCES THE CLI'S TARGET RULE: a rekey may move rows only
    # INTO a canonical bucket. Without this a library caller could create the
    # basename bucket the CLI refuses, which is the misfile the rekey command
    # exists to prevent. `from_id` is deliberately NOT required canonical — it
    # names the legacy bucket being retired. `from_id == to_id` is a no-op
    # that moves nothing, so it keeps working for a non-canonical id.
    if from_id != to_id and not is_canonical_id(to_id) and not own_folder_name_target:
        raise ValueError(
            "the rekey target must be a canonical host/owner/repo id; moving "
            "rows into a non-canonical bucket is refused. Nothing was moved."
        )
    if conn.in_transaction:
        raise ValueError(
            "rekey_project owns its transaction; pass a connection with no "
            "open transaction. Nothing was moved."
        )

    counters: Dict[str, Any] = {
        "from": from_id,
        "to": to_id,
        "dry_run": dry_run,
        "memories": 0,
        "files_in_place": 0,
        "files_conflicted": 0,
        "links_repointed": 0,
        "links_conflicted": 0,
        "relations_repointed": 0,
        "vectors_moved": 0,
        "vectors_pending": 0,
        "null_project_memories": 0,
        "null_project_files": 0,
        "from_bucket_empty": True,
        "schema_absent": False,
    }

    # A SCHEMA CHECK, NOT `ensure_initialized`: that initialiser creates the
    # schema on an empty store and commits as it migrates, and a rekey (a dry
    # run above all) must do neither.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone() is None:
        counters["schema_absent"] = True
        return counters

    counters["from_bucket_empty"] = (
        conn.execute(
            "SELECT 1 FROM memories WHERE project_id = ? LIMIT 1", (from_id,)
        ).fetchone() is None
        and conn.execute(
            "SELECT 1 FROM files WHERE project_id = ? LIMIT 1", (from_id,)
        ).fetchone() is None
    )

    # TAKEN BEFORE THE NO-OP RETURN, so every outcome reports the store's
    # NULL rows. They are never moved, so the count is the same before and
    # after the move.
    counters["null_project_memories"] = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE project_id IS NULL"
    ).fetchone()[0]
    counters["null_project_files"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE project_id IS NULL"
    ).fetchone()[0]

    if from_id == to_id:
        # NOTHING MOVES, but the bucket's size is still reported, so a no-op
        # on a bucket that holds rows does not read as an empty one.
        counters["memories"] = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE project_id = ?", (from_id,)
        ).fetchone()[0]
        return counters

    savepoint = "rekey_project_work"
    if dry_run:
        conn.execute(f"SAVEPOINT {savepoint}")
    else:
        conn.execute("BEGIN IMMEDIATE")

    try:
        # Collect the moved memories BEFORE the UPDATE: afterwards they share
        # the target id with rows that were never in `from`.
        moved_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM memories WHERE project_id = ?", (from_id,)
            ).fetchall()
        ]
        counters["memories"] = len(moved_ids)

        conn.execute(
            "UPDATE memories SET project_id = ? WHERE project_id = ?",
            (to_id, from_id),
        )

        old_files = conn.execute(
            "SELECT id, path FROM files WHERE project_id = ? ORDER BY path",
            (from_id,),
        ).fetchall()

        # EACH EDGE COUNTS ONCE, so the count is taken once, BEFORE any row
        # moves: every relation with at least one end on a `from` row that
        # merges into an existing target row. Counting per merged row instead
        # counted an edge twice when both of its ends merged.
        counters["relations_repointed"] = conn.execute(
            "SELECT COUNT(*) FROM file_relations r WHERE EXISTS ("
            "SELECT 1 FROM files o JOIN files t "
            "ON t.path = o.path AND t.project_id = ? "
            "WHERE o.project_id = ? "
            "AND o.id IN (r.source_file, r.target_file))",
            (to_id, from_id),
        ).fetchone()[0]

        for old_id, old_path in old_files:
            target = conn.execute(
                "SELECT id FROM files WHERE path = ? AND project_id = ?",
                (old_path, to_id),
            ).fetchone()

            if target is None:
                conn.execute(
                    "UPDATE files SET project_id = ? WHERE id = ?",
                    (to_id, old_id),
                )
                counters["files_in_place"] += 1
                continue

            target_id = target[0]
            counters["files_conflicted"] += 1

            # A MEMORY LINK'S PRIMARY KEY IS (memory_id, file_id), so a target
            # link for the same pair KEEPS ITS OWN RELATIONSHIP and the moved
            # one is dropped; every dropped relationship is counted in
            # `links_conflicted` instead of vanishing.
            old_links = conn.execute(
                "SELECT memory_id, relationship FROM memory_files "
                "WHERE file_id = ?",
                (old_id,),
            ).fetchall()
            counters["links_repointed"] += len(old_links)
            for link_memory_id, link_relationship in old_links:
                existing = conn.execute(
                    "SELECT relationship FROM memory_files "
                    "WHERE memory_id = ? AND file_id = ?",
                    (link_memory_id, target_id),
                ).fetchone()
                if existing is not None:
                    if existing[0] != link_relationship:
                        counters["links_conflicted"] += 1
                    continue
                conn.execute(
                    "INSERT INTO memory_files (memory_id, file_id, relationship) "
                    "VALUES (?, ?, ?)",
                    (link_memory_id, target_id, link_relationship),
                )
            conn.execute(
                "DELETE FROM memory_files WHERE file_id = ?", (old_id,)
            )

            # A SELF-RELATION needs both statements: the outgoing one inserts
            # `target -> old`, and the incoming scan then sees that new row
            # and inserts `target -> target`.
            conn.execute(
                "INSERT OR IGNORE INTO file_relations "
                "(source_file, target_file, relationship) "
                "SELECT ?, target_file, relationship FROM file_relations "
                "WHERE source_file = ?",
                (target_id, old_id),
            )
            conn.execute(
                "DELETE FROM file_relations WHERE source_file = ?", (old_id,)
            )

            conn.execute(
                "INSERT OR IGNORE INTO file_relations "
                "(source_file, target_file, relationship) "
                "SELECT source_file, ?, relationship FROM file_relations "
                "WHERE target_file = ?",
                (target_id, old_id),
            )
            conn.execute(
                "DELETE FROM file_relations WHERE target_file = ?", (old_id,)
            )

            # Every reference to the old row (`memory_files.file_id` and the
            # two `file_relations` endpoints) was re-pointed and deleted
            # above, so the row is retired unconditionally.
            conn.execute("DELETE FROM files WHERE id = ?", (old_id,))

        # Vectors: delete+insert with the SAME blob, inside this transaction.
        # THE TABLE MAY NOT EXIST when the extension or its init failed; a
        # missing table means there is nothing to move, not a pending repair.
        vector_table_present = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='vec_memories'"
        ).fetchone() is not None

        vector_ready = False
        if moved_ids and vector_table_present and SQLITE_EXTENSIONS_ENABLED:
            try:
                load_sqlite_vec(conn)
                vector_ready = True
            except Exception as exc:  # noqa: BLE001 - degrade, never fail the move
                logger.debug(f"rekey_project: vector table unavailable: {exc}")

        if vector_ready:
            for memory_id in moved_ids:
                # PER-VECTOR SAVEPOINT: a failed delete+insert must restore
                # that memory's ORIGINAL vector, not leave it deleted. One bad
                # row reports as pending and never fails the whole move.
                conn.execute("SAVEPOINT rekey_vector")
                try:
                    vector = conn.execute(
                        "SELECT embedding FROM vec_memories WHERE memory_id = ?",
                        (memory_id,),
                    ).fetchone()
                    if vector is None:
                        conn.execute("RELEASE rekey_vector")
                        continue
                    conn.execute(
                        "DELETE FROM vec_memories WHERE memory_id = ?",
                        (memory_id,),
                    )
                    conn.execute(
                        "INSERT INTO vec_memories "
                        "(memory_id, project_id, embedding) VALUES (?, ?, ?)",
                        (memory_id, to_id, vector[0]),
                    )
                    conn.execute("RELEASE rekey_vector")
                    counters["vectors_moved"] += 1
                except Exception as exc:  # noqa: BLE001 - one row, not the move
                    # SQLITE ROLLED THE WHOLE TRANSACTION BACK: the move is
                    # already undone, so continuing would write the remaining
                    # vectors in autocommit. Propagate the original error.
                    if not conn.in_transaction:
                        raise
                    logger.debug(
                        f"rekey_project: vector move failed for {memory_id!r}: {exc}"
                    )
                    conn.execute("ROLLBACK TO rekey_vector")
                    conn.execute("RELEASE rekey_vector")
                    counters["vectors_pending"] += 1
        elif moved_ids and vector_table_present:
            # The table exists but cannot be opened, so a delete is impossible
            # too. Leave every row where it is; the catch-up repairs them.
            counters["vectors_pending"] = len(moved_ids)

        if dry_run:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
        else:
            conn.commit()

        return counters

    except BaseException:
        # GUARDED ON `in_transaction`: when SQLite has already rolled the
        # transaction back, the savepoint is gone and cleanup would raise a
        # second error that replaces the real one.
        if conn.in_transaction:
            if dry_run:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
            else:
                conn.rollback()
        raise


def create_memory(
    conn: sqlite3.Connection,
    memory: Dict[str, Any]
) -> str:
    """
    Create a new memory record.

    Args:
        conn: Active database connection.
        memory: Memory dictionary with fields:
            - context: Optional[str] - Working context
            - goal: Optional[str] - Goal description
            - active_tasks: Optional[List[dict]] - Task list
            - lessons_learned: Optional[List[str]] - Lessons
            - decisions: Optional[List[dict]] - Decisions
            - entities: Optional[List[dict]] - Entities
            - reasoning_chains: Optional[List[str]] - How key decisions connect
            - agreements_reached: Optional[List[str]] - What was verified via teachback
            - disagreements_resolved: Optional[List[str]] - Where agents disagreed and resolution
            - project_id: Optional[str] - Project identifier
            - session_id: Optional[str] - Session identifier

    Returns:
        The ID of the created memory.
    """
    # THE WRITE-INGRESS RULE IS SHARED AND INCLUDES THE 255 CAP. A
    # payload-supplied project id reaches the TEXT binding below, so the cap
    # is applied HERE, before any store work. Only a supplied VALUE is
    # validated: an absent id keeps its existing meaning (the NULL bucket)
    # for callers that fill it in upstream.
    supplied_project = memory.get("project_id")
    if supplied_project is not None:
        validate_identifier_for_write(supplied_project)

    ensure_initialized(conn)

    # Generate ID if not provided
    # A SUPPLIED ID IS AN IDENTIFIER, HELD TO THE SAME RULE AS project_id AND
    # session_id. `create` honours a caller-supplied `id`, so without this a
    # control character or lone surrogate reaches the `memories.id` TEXT bind:
    # the lone-surrogate case dies there as an unhandled UnicodeEncodeError
    # (a ValueError subclass, so it is caught, but it bypasses the remedy text
    # and fires after the validation phase). A falsy id keeps its documented
    # `or generate_id()` meaning. THE RULE LIVES IN THIS MODULE, so the API
    # layer imports it downward and there is exactly one live definition.
    supplied_id = memory.get("id")
    if supplied_id:
        if _unusable_identifier(supplied_id):
            raise ValueError(
                "id, when supplied, must be a non-empty string without control "
                "characters (U+0000-U+001F, U+007F-U+009F) or lone surrogates "
                "(U+D800-U+DFFF); omit it to generate one. Nothing was written."
            )
    memory_id = supplied_id or generate_id()

    # STRICT validation — unknown keys raise before any DB write.
    # ALLOWED_CREATE_COLUMNS includes 'id' and 'created_at' so create payloads
    # may pass them through; ALLOWED_UPDATE_COLUMNS does not, so update_memory
    # rejects them. Splitting the allowlists keeps both call sites honest about
    # which keys are legal at each ingress.
    _reject_unknown_columns(
        memory, ALLOWED_CREATE_COLUMNS, operation="save", memory_id=memory_id,
    )

    # STRICT sub-object validation — symmetric with update_memory's merge path
    # (the save-ingress half). An earlier fix wired strict=True into
    # _canonicalize_dict_item, but that hook only fires from _merge_with_dedup
    # inside update_memory. The save ingress bypassed it, so create_memory
    # silently stored dict-list items with unknown sub-object keys (e.g.
    # active_tasks=[{"id": "abc", "subject": "foo"}]) as verbatim junk that
    # read back as empty dataclass instances via the lenient read path.
    #
    # Canonicalization runs BEFORE _serialize_json_fields so that any raise
    # leaves the DB untouched (validation-before-write invariant). String
    # shorthand (e.g. entities=["Redis"]) still works — _canonicalize_dict_item
    # routes str inputs through the non-strict cls.from_dict(str) path.
    #
    # Within-batch dedup: create_memory previously
    # canonicalized each item but did not deduplicate within the incoming
    # batch. update_memory dedupes even on replace=True, so the same payload
    # behaves differently at the create and update ingresses. We now run
    # _merge_with_dedup with an empty existing list for both dict-list and
    # string-list fields so the two ingresses are symmetric.
    normalized = dict(memory)
    for field in LIST_FIELDS:
        if field not in normalized:
            continue
        # THE SAME GUARD THE UPDATE INGRESS USES (see update_memory). This
        # line held a TRUTHINESS test where a TYPE test belongs. A bare
        # string passed `if items:` and reached `list()` inside
        # _merge_with_dedup, which yields the CHARACTERS of that string. The
        # content-hash dedup then kept one item per unique character, so the
        # order and the repeats went away and the prose could not be
        # recovered.
        #
        # THE TRUTHINESS TEST WAS ITSELF PART OF THE DEFECT. It skipped a
        # FALSY non-list ("", {}, 0) in silence. The update ingress applies
        # no such pre-test, so it refuses those values today. This call makes
        # the two ingresses agree on every non-list input.
        items = _normalize_list_field(field, normalized[field])
        if items:
            normalized[field] = _merge_with_dedup(field, [], items)

    # Prepare data with JSON serialization
    data = _serialize_json_fields(normalized)

    # Set timestamps
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO memories (
            id, context, goal, active_tasks, lessons_learned,
            decisions, entities, reasoning_chains, agreements_reached,
            disagreements_resolved, project_id, session_id,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        memory_id,
        data.get("context"),
        data.get("goal"),
        data.get("active_tasks"),
        data.get("lessons_learned"),
        data.get("decisions"),
        data.get("entities"),
        data.get("reasoning_chains"),
        data.get("agreements_reached"),
        data.get("disagreements_resolved"),
        data.get("project_id"),
        data.get("session_id"),
        now,
        now
    ))

    conn.commit()
    logger.debug(f"Created memory with ID: {memory_id!r}")
    return memory_id


def resolve_memory_id_prefix(
    conn: sqlite3.Connection,
    prefix: str,
    *,
    project_id: Optional[Union[str, Sequence[str]]] = None,
    descriptor_chars: int = 60,
) -> Optional[str]:
    """
    Resolve a memory-ID prefix to a single full ID, git-style.

    Lookup uses a half-open range scan (`id >= prefix AND id < prefix + 'g'`)
    against the BINARY-collated primary key, which lets SQLite walk the
    index instead of full-scanning under a LIKE pattern. The prefix is
    lowercased before bounds construction so `ABCD1234` and `abcd1234`
    resolve identically. IDs are stored as lowercase hex.

    A SUPPLIED `project_id` SCOPES ALL THREE QUERIES, NOT JUST THE MATCH
    LIST. The ambiguity verdict is decided by the LIMIT-2 pre-check, the
    match list is capped by a second query and the true total comes from a
    third; scoping only the list would let a prefix that names one row in
    each of two projects take the ambiguous branch and then raise with a
    single match, and would report a total that includes rows the caller
    cannot see. With the scope applied to all three, a prefix that matches
    only another project's row resolves as no row at all. `None` keeps the
    unscoped whole-store behaviour callers had before the parameter existed.

    Args:
        conn: Active database connection.
        prefix: Memory-ID prefix (>= MIN_PREFIX_LENGTH chars; full IDs allowed).
        project_id: Optional project scope: one id, or a sequence of ids for
            the derived read union. When not None, every query is restricted
            to rows stored under one of those projects; a NULL-project row is
            NOT another project's row and is excluded. An empty sequence
            matches no row (never the whole store).
        descriptor_chars: Length of the context snippet attached to each match
            on ambiguity (for terse disambiguation output). Security-relevant
            knob if multi-tenant access is ever introduced — context may carry
            tenant data the disambiguation reader is not entitled to read; in
            that case shorten or drop the snippet, or scope it by tenant.

    Returns:
        The full memory ID when the prefix uniquely matches one row, or None
        when no row matches.

    Raises:
        PrefixTooShortError: prefix shorter than MIN_PREFIX_LENGTH.
        AmbiguousPrefixError: prefix matches more than one row. The exception
            carries `matches` (list capped at AMBIGUOUS_MATCH_CAP),
            `matches_capped` (True if the cap was applied), and
            `total_matches` (the true row count).
    """
    ensure_initialized(conn)

    if len(prefix) < MIN_PREFIX_LENGTH:
        raise PrefixTooShortError(prefix)

    # Case-fold to match the lowercase hex IDs produced by generate_id.
    prefix = prefix.lower()

    # Half-open range bounds; see `_PREFIX_UPPER_BOUND_CHAR` for the
    # alphabet-invariant. [lo, hi) covers exactly the rows whose ID begins
    # with `prefix` under BINARY collation on the lowercase-hex PK.
    lo = prefix
    hi = prefix + _PREFIX_UPPER_BOUND_CHAR

    # ONE SCOPE CLAUSE, APPLIED TO EVERY QUERY BELOW. `IN (?,...)` rather than
    # the null-safe `IS` form is deliberate: a scoped caller's projects are
    # non-empty strings (the write guards refuse anything else), so a
    # NULL-keyed row is not any of their rows and must not match.
    # AN EMPTY SCOPE IS NO ROWS, never unscoped: it resolves as no row at all
    # rather than dropping the clause and matching the whole store.
    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return None
    scope_sql = ""
    scope_params: Tuple[str, ...] = ()
    if scopes is not None:
        predicate, scope_params = project_scope_predicate("project_id", scopes)
        scope_sql = " AND " + predicate

    # LIMIT 2 short-circuits the common case (unique match): we only need to
    # know whether more than one row matches before issuing a wider query.
    cursor = conn.execute(
        "SELECT id FROM memories WHERE id >= ? AND id < ?"
        f"{scope_sql} LIMIT 2",
        (lo, hi, *scope_params),
    )
    rows = cursor.fetchall()

    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]["id"]

    # Ambiguous — fetch a capped match list with a short context descriptor
    # plus a separate COUNT for the true total. Both queries fire only on
    # this branch; the unique-match path above is single-query.
    cursor = conn.execute(
        "SELECT id, context FROM memories WHERE id >= ? AND id < ?"
        f"{scope_sql} ORDER BY id LIMIT ?",
        (lo, hi, *scope_params, AMBIGUOUS_MATCH_CAP),
    )
    matches = []
    for row in cursor.fetchall():
        full_context = row["context"] or ""
        snippet = full_context[:descriptor_chars]
        matches.append({
            # `id` is the display form, `raw_id` the replay form; see the
            # exception's docstring for the contract and why this boundary.
            "id": escape_for_envelope(row["id"]),
            "raw_id": row["id"],
            "context": snippet,
            # Per-match flag distinct from the list-level `matches_capped`
            # on the exception: signals that THIS row's `context` was
            # clipped to `descriptor_chars`, not that the match list
            # itself was capped.
            "context_truncated": len(full_context) > descriptor_chars,
        })
    count_row = conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE id >= ? AND id < ?"
        f"{scope_sql}",
        (lo, hi, *scope_params),
    ).fetchone()
    total_matches = count_row["n"]
    matches_capped = total_matches > len(matches)
    raise AmbiguousPrefixError(
        prefix,
        matches,
        matches_capped=matches_capped,
        total_matches=total_matches,
    )


def get_memory(
    conn: sqlite3.Connection,
    memory_id: str
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a memory by exact ID.

    This is the exact-match storage primitive. Callers that want git-style
    prefix resolution should compose with resolve_memory_id_prefix above
    (see PACTMemory.get for the canonical pattern).

    Args:
        conn: Active database connection.
        memory_id: The unique memory identifier (full ID).

    Returns:
        Memory dictionary if found, None otherwise.
    """
    ensure_initialized(conn)

    cursor = conn.execute(
        "SELECT * FROM memories WHERE id = ?",
        (memory_id,)
    )
    row = cursor.fetchone()

    if row is None:
        return None

    return _deserialize_json_fields(row)


def update_memory(
    conn: sqlite3.Connection,
    memory_id: str,
    updates: Dict[str, Any],
    *,
    replace: bool = False,
) -> bool:
    """
    Update an existing memory record.

    Args:
        conn: Active database connection.
        memory_id: The unique memory identifier.
        updates: Dictionary of fields to update.
        replace: If True, list-valued fields are replaced wholesale (the
            legacy behavior). If False (the default), list-valued
            fields are appended to the existing values with content-hash
            deduplication — repeat calls are idempotent and never clobber
            history. Scalar fields (context, goal, project_id, session_id)
            always replace regardless of this flag.

    Returns:
        True if the memory was updated, False if not found.

    Raises:
        ValueError: if `updates` contains unknown top-level keys, or if
            any list-valued field contains an item whose sub-object has
            unknown keys. Validation happens BEFORE any DB write, so a
            rejected update leaves the row unchanged.
    """
    # THE WRITE-INGRESS RULE AGAIN, AS IN create_memory: a supplied
    # project id replaces the stored TEXT and must obey the 255 cap and the
    # control/surrogate rule. An explicit None keeps its de-key meaning at
    # THIS layer; the API layer refuses it upstream, so a library
    # caller that reaches here with None has asked for a change the layer
    # below is allowed to make.
    supplied_project = updates.get("project_id")
    if supplied_project is not None:
        validate_identifier_for_write(supplied_project)

    ensure_initialized(conn)

    # Check if memory exists
    if get_memory(conn, memory_id) is None:
        return False

    # Strip server-owned fields (id, created_at, updated_at) BEFORE validation
    # so callers can pass a full memory dict through without tripping
    # validation. `updated_at` is stamped by this function on every successful
    # write, so any caller-supplied value is always dropped.
    working = {k: v for k, v in updates.items() if k not in _STRIPPED_ON_INGRESS}

    # Bug 2: STRICT validation — unknown keys raise before any DB write.
    _reject_unknown_columns(
        working, ALLOWED_UPDATE_COLUMNS, operation="update", memory_id=memory_id,
    )

    # Bug 1: normalize list fields and canonicalize dict items. This is the
    # point where from_dict(strict=True) fires for sub-object validation (bug
    # 3 write path). Any raise leaves the DB unchanged — validation runs
    # before the transaction on the replace path, and within BEGIN IMMEDIATE
    # + rollback on the additive path.
    normalized: Dict[str, Any] = {}
    touched_lists: Set[str] = set()
    for key, value in working.items():
        if key in LIST_FIELDS:
            list_value = _normalize_list_field(key, value)
            if replace:
                # Even on replace, dedup within the incoming batch and run
                # sub-object validation (so a caller sending duplicate items
                # still gets a deduped, validated write). On replace, an
                # empty list is a valid "clear this field" instruction and
                # MUST proceed; touched_lists is only consulted on the
                # additive merge path below, so we skip adding to it here.
                normalized[key] = _merge_with_dedup(key, [], list_value)
            else:
                # On the additive path, an empty
                # incoming list is a no-op (existing items + zero new items
                # = existing items). Skip the SELECT + UPDATE entirely.
                if not list_value:
                    continue
                normalized[key] = list_value
                touched_lists.add(key)
        else:
            normalized[key] = value

    # Nothing to write (e.g. payload contained only id/created_at, which were
    # stripped above). Return True without bumping updated_at — an id-only
    # call should be a no-op, not a touch.
    if not normalized:
        return True

    # If we have list fields to merge additively, we need a SELECT + UPDATE
    # transaction under BEGIN IMMEDIATE. Default sqlite3 isolation is
    # DEFERRED, which upgrades to a write lock only on the first write
    # statement — leaving a race window between our SELECT (merge read) and
    # our UPDATE. BEGIN IMMEDIATE grabs the write lock at BEGIN so no other
    # writer can slip in.
    needs_merge = bool(touched_lists)
    # Scalar fields the caller explicitly set. Even if they happen to equal
    # the current DB value, we still bump updated_at — scalar equality checks
    # are out of scope for the content no-op short-circuit below (only
    # content-no-op list merges are covered).
    scalar_fields_present = bool(
        set(normalized.keys()) - touched_lists
    )
    try:
        if needs_merge:
            conn.execute("BEGIN IMMEDIATE")
            cols = sorted(touched_lists)
            placeholders = ", ".join(cols)
            cursor = conn.execute(
                f"SELECT {placeholders} FROM memories WHERE id = ?",
                (memory_id,),
            )
            row = cursor.fetchone()
            current = _deserialize_json_fields(dict(row)) if row else {}
            # Track which merged list fields actually produced a different
            # result from the existing list. If every incoming item was a
            # duplicate, merged == existing and there's nothing to write.
            changed_lists: Set[str] = set()
            for key in touched_lists:
                existing = current.get(key) or []
                if isinstance(existing, str):
                    # Defensive: a legacy row where JSON deserialization
                    # didn't resolve (e.g. stored as a plain string).
                    try:
                        existing = json.loads(existing)
                    except (json.JSONDecodeError, TypeError):
                        existing = []
                if not isinstance(existing, list):
                    existing = []
                merged = _merge_with_dedup(key, existing, normalized[key])
                normalized[key] = merged
                if merged != existing:
                    changed_lists.add(key)

            # CONTENT NO-OP SHORT-CIRCUIT: if no list field actually
            # changed AND no scalar fields were set, the UPDATE would write
            # identical data and bump updated_at pointlessly. Close the
            # transaction and return without touching the row — extends the
            # "id-only no-op" contract to additive merges where every
            # incoming item was already present.
            if not changed_lists and not scalar_fields_present:
                conn.commit()  # release BEGIN IMMEDIATE write lock
                return True

            # Drop unchanged list fields from the write payload — no point
            # rewriting identical JSON blobs — but preserve scalar fields and
            # any list field that did change.
            if changed_lists != touched_lists:
                normalized = {
                    k: v for k, v in normalized.items()
                    if k not in touched_lists or k in changed_lists
                }

            # Serialize and write inside the same transaction.
            data = _serialize_json_fields(normalized)
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            set_clauses = [f"{k} = ?" for k in data.keys()]
            values = list(data.values()) + [memory_id]
            conn.execute(
                f"UPDATE memories SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            conn.commit()
        else:
            # Replace-only or no list fields: no merge-read hazard, so a
            # plain commit is fine.
            data = _serialize_json_fields(normalized)
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            set_clauses = [f"{k} = ?" for k in data.keys()]
            values = list(data.values()) + [memory_id]
            conn.execute(
                f"UPDATE memories SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            conn.commit()
    except Exception:
        # Safety net: roll back any open transaction (additive merge or
        # implicit on the replace path) so a partial write never persists.
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise

    logger.debug("Updated memory %r (replace=%s)", memory_id, replace)
    return True


def delete_memory(
    conn: sqlite3.Connection,
    memory_id: str
) -> bool:
    """
    Delete a memory record.

    Also removes associated memory_files entries due to CASCADE.

    Args:
        conn: Active database connection.
        memory_id: The unique memory identifier.

    Returns:
        True if the memory was deleted, False if not found.
    """
    ensure_initialized(conn)

    cursor = conn.execute(
        "DELETE FROM memories WHERE id = ?",
        (memory_id,)
    )
    conn.commit()

    deleted = cursor.rowcount > 0
    if deleted:
        logger.debug(f"Deleted memory {memory_id!r}")
    return deleted


def list_memories(
    conn: sqlite3.Connection,
    project_id: Optional[Union[str, Sequence[str]]] = None,
    session_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    List memories with optional filtering.

    THE SCOPE MAY BE A UNION, AND THE LIMIT APPLIES TO THE MERGED RESULT:
    this is ONE statement, so `LIMIT ? OFFSET ?` selects from the union rather
    than from each bucket. An empty scope returns no rows, never the store.

    Args:
        conn: Active database connection.
        project_id: Optional project scope: one id, or a sequence of ids for
            the derived read union. `None` means no filter.
        session_id: Optional session filter.
        limit: Maximum number of results (default 20).
        offset: Number of results to skip (default 0).

    Returns:
        List of memory dictionaries, ordered by created_at DESC. Two records
        sharing a created_at come newest-inserted first; `rowid` is
        table-global, so that order is deterministic across the union.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    query = "SELECT * FROM memories"
    params: List[Any] = []
    conditions = []

    if scopes is not None:
        predicate, scope_params = project_scope_predicate("project_id", scopes)
        conditions.append(predicate)
        params.extend(scope_params)

    if session_id is not None:
        conditions.append("session_id = ?")
        params.append(session_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    # DETERMINISM INSURANCE, NOT A TIE THIS PATH EXPECTS TO SEE. The only
    # writer stamps `created_at` with `datetime.now(timezone.utc).isoformat()`
    # -- microsecond precision -- so saves do not tie, not even several inside
    # one second. What CAN tie is a row written by the schema DEFAULT, whose
    # format differs from the writer's (see the CREATE TABLE), or a row whose
    # stamp was set by direct SQL. rowid rises with insertion, so where a tie
    # does arise the order is the insertion order rather than an arbitrary one.
    query += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()

    return [_deserialize_json_fields(row) for row in rows]


# The text columns the keyword search reads, in order. Fixed names, so the
# query built from them carries no caller input.
_TEXT_SEARCH_COLUMNS = (
    "context", "goal", "lessons_learned", "decisions",
    "reasoning_chains", "agreements_reached", "disagreements_resolved",
)


def search_memories_by_text(
    conn: sqlite3.Connection,
    search_term: str,
    project_id: Optional[Union[str, Sequence[str]]] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Search memories by text content (basic substring search).

    Searches across context, goal, lessons_learned, decisions,
    reasoning_chains, agreements_reached, and disagreements_resolved fields.
    For semantic search, use the search module with embeddings.

    THE SCOPE MAY BE A UNION, AND THE LIMIT APPLIES TO THE MERGED RESULT:
    one statement, so the recency ORDER BY and its LIMIT select from the
    union, not per bucket. An empty scope returns no rows, never the store.

    Args:
        conn: Active database connection.
        search_term: Text to search for.
        project_id: Optional project scope: one id, or a sequence of ids for
            the derived read union. `None` means no filter.
        limit: Maximum number of results.

    Returns:
        List of matching memory dictionaries.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is not None and not scopes:
        return []

    # Escape SQL LIKE wildcards in the search term so literal % and _ are matched.
    # NOTE: ESCAPE '\\' is SQLite-specific syntax; update if migrating to another DB dialect.
    escaped = search_term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    search_pattern = f"%{escaped}%"

    # ONLY THE COLUMNS THE STORE HAS. A store from before v3.8.0 lacks the
    # three CT columns until a write migrates it, and a read runs no
    # migration, so naming an absent column would fail the whole search.
    present = {
        row[1] for row in conn.execute("PRAGMA table_info(memories)")
    }
    columns = [c for c in _TEXT_SEARCH_COLUMNS if c in present]
    # NO `memories` TABLE, NO COLUMNS, NO ROWS: a schema-less store (a read
    # runs no DDL) would otherwise build `WHERE ()`, an SQL syntax error.
    if not columns:
        return []
    query = (
        "SELECT * FROM memories WHERE ("
        + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in columns)
        + ")"
    )
    params = [search_pattern] * len(columns)

    if scopes is not None:
        predicate, scope_params = project_scope_predicate("project_id", scopes)
        query += " AND " + predicate
        params.extend(scope_params)

    # SAFE BECAUSE THIS ORDER CARRIES NO RELEVANCE, NOT BECAUSE NOTHING READS
    # IT. Something does: the hybrid scorer's keyword-fallback branch turns a
    # result's POSITION here into its score. But this is a substring LIKE
    # search with no relevance signal of any kind, so the order was already
    # pure recency, and adding the tiebreak reshuffles only records that are
    # equally recent. Ranked search is the vector path's own ORDER BY on
    # distance, which this does not touch.
    query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()

    return [_deserialize_json_fields(row) for row in rows]


# =============================================================================
# Database Maintenance
# =============================================================================

def get_memory_count(
    conn: sqlite3.Connection,
    project_id: Optional[Union[str, Sequence[str]]] = None
) -> int:
    """
    Get the total count of memories.

    THE UNION TOTAL IS EXACT, not an approximation: a memory row carries
    exactly one `project_id`, so a union of ids counts each row once and the
    sum equals a single `COUNT(*)` over the same predicate. The per-bucket
    split for disclosure comes from `project_bucket_counts`.

    Args:
        conn: Active database connection.
        project_id: Optional project scope: one id, or a sequence of ids for
            the derived read union. `None` counts the whole store (including
            NULL-keyed rows, which no scoped read can see).

    Returns:
        Number of memories matching the criteria. An empty scope is zero.
    """
    ensure_initialized(conn)

    scopes = normalise_project_scopes(project_id)
    if scopes is None:
        cursor = conn.execute("SELECT COUNT(*) FROM memories")
        return cursor.fetchone()[0]
    if not scopes:
        return 0
    predicate, params = project_scope_predicate("project_id", scopes)
    cursor = conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {predicate}",
        params,
    )
    return cursor.fetchone()[0]


def vacuum_database(conn: sqlite3.Connection) -> None:
    """
    Optimize the database by running VACUUM.

    Reclaims space and defragments the database file.

    Args:
        conn: Active database connection.
    """
    conn.execute("VACUUM")
    logger.info("Database vacuumed successfully")


def check_integrity(conn: sqlite3.Connection) -> bool:
    """
    Check database integrity.

    Args:
        conn: Active database connection.

    Returns:
        True if database passes integrity check.
    """
    cursor = conn.execute("PRAGMA integrity_check")
    result = cursor.fetchone()[0]

    is_ok = result == "ok"
    if not is_ok:
        logger.error(f"Database integrity check failed: {result}")
    return is_ok


# =============================================================================
# Convenience Functions
# =============================================================================

def initialize_database(db_path: Optional[Path] = None) -> None:
    """
    Initialize the database with schema.

    Creates the database file and all required tables.

    Args:
        db_path: Optional custom database path.
    """
    with db_connection(db_path) as conn:
        init_schema(conn)


def quick_save(
    context: Optional[str] = None,
    goal: Optional[str] = None,
    lessons_learned: Optional[List[str]] = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
    reasoning_chains: Optional[List[str]] = None,
    agreements_reached: Optional[List[str]] = None,
    disagreements_resolved: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """
    Quickly save a memory with minimal setup.

    Convenience function for simple memory creation.

    Args:
        context: Working context description.
        goal: Goal description.
        lessons_learned: List of lessons.
        decisions: List of decision dicts.
        reasoning_chains: How key decisions connect.
        agreements_reached: What was verified via teachback.
        disagreements_resolved: Where agents disagreed and resolution.
        project_id: Project identifier.
        session_id: Session identifier.

    Returns:
        The ID of the created memory.

    ROUTED THROUGH ``PACTMemory.save`` RATHER THAN STRAIGHT TO ``create_memory``.
    This is the only other public way to land a memory row, and going direct
    bypassed EVERY scope mechanism the save path provides: project detection,
    the per-save scope disclosure, the location-divergence warning, and the
    fail-closed env/record refusal. A caller passing no ``project_id`` landed a
    row with a NULL project and nothing recorded about how that happened --
    which is the unscoped case, worse than the mis-scoped one the disclosure
    exists to make visible.

    Nothing in this repository calls this function today (its only other
    references are its import and its name in ``__all__``), so the bypass was
    latent rather than active. It is public, though, so "no caller reaches it"
    is a fact about this repository and not a property of the function.

    ``sync_to_claude=False`` PRESERVES THE EXISTING BEHAVIOUR and is not a
    detail: ``save`` projects into CLAUDE.md by default, this function never
    did, and silently acquiring an ambient document write would be a worse
    defect than the one being fixed.

    Imported inside the function because ``memory_api`` imports this module at
    module level; a top-level import here would be a cycle. Same idiom as the
    ``embeddings`` and ``models`` imports elsewhere in this file.
    """
    memory = {
        "context": context,
        "goal": goal,
        "lessons_learned": lessons_learned or [],
        "decisions": decisions or [],
        "reasoning_chains": reasoning_chains or [],
        "agreements_reached": agreements_reached or [],
        "disagreements_resolved": disagreements_resolved or [],
        "project_id": project_id,
        "session_id": session_id
    }

    from .memory_api import PACTMemory

    return PACTMemory().save(memory, sync_to_claude=False)
