"""
PACT Memory Configuration

Location: pact-plugin/skills/pact-memory/scripts/config.py

Centralized configuration for the PACT Memory skill.
All path resolution is defined here to ensure consistency across all modules.

Used by:
- database.py: Database path resolution (resolve_db_path, store_scope)
- setup_memory.py: Directory creation (get_memory_dir)
- cli.py, memory_api.py: Store binding for one call (store_scope)

ONE EXPRESSION COMPOSES THE STORE FILE PATH, and it is inside `resolve_db_path`
below. Each other name delegates to it. The tree used to hold THREE derivations
that could disagree inside one invocation: this module composed the name, so did
`database.get_db_path`, and `database.get_connection` had a third rule,
`db_path or get_db_path()`, which made the caller path a RIVAL of the resolver
rather than an input to it. Two of the three are gone. To keep several
derivations in step is the defect's own generator, so the repair removes them.

EVERY PATH HERE RESOLVES AT USE TIME, NEVER AT IMPORT TIME. This module used to
export `PACT_MEMORY_DIR` and `DB_PATH` as module-level constants, each computed
from `Path.home()` when the module first imported. That made the store location
a property of WHEN the module was imported rather than of the environment, and
it produced two defects a caller could not see:

  1. A test that redirects `Path.home()` protected the store only when this
     module happened to import INSIDE that test. Import it at collection time --
     which any module-level `from scripts.memory_api import ...` does -- and the
     constant was already bound to the real home, so the redirect reached
     nothing. The protection existed and was ACCIDENTAL, decided by import order.
  2. `from .config import DB_PATH` COPIED the value into a second module's
     namespace, so patching it here did not reach that module at all.

Resolving in a function removes both: no stored value can go stale, and no
second copy can diverge. The environment variable exists because no in-process
mechanism crosses a process boundary -- a spawned child is a fresh interpreter,
in which a patched `Path.home()` never happened.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Optional

# Environment variable that relocates the store THIS MODULE resolves.
#
# ITS EXTENT IS NARROWER THAN THE STORE ROOT, and the gap is not obvious from
# here. It governs `get_memory_dir()` and everything derived from it, which is
# the database. It does NOT govern `hooks/track_files.py`: that writes
# `session-tracking/` INSIDE the same root, but resolves it through
# `get_claude_config_dir()`, so that subtree follows `CLAUDE_CONFIG_DIR` and is
# unaffected by this variable. Setting only this one relocates the database and
# leaves the tracking data where it was.
#
# DELIBERATELY MEMORY-SPECIFIC. It names this store and nothing else. A general
# home or config override is the wrong shape here: `CLAUDE_CONFIG_DIR` takes
# precedence over `Path.home()` in `get_claude_config_dir`, so honouring that
# variable would shadow the home-redirect convention the rest of the suite is
# built on. Narrow scope is what makes this variable safe to add.
#
# AN EXPLICIT DISCRIMINATOR BEATS AN INFERRED ONE, and that is the load-bearing
# reason for this design rather than the relocation feature it also provides.
# Isolating a test process from the real store looks like it needs the code to
# work out whether it is under test, and every mechanism for working that out
# can be WRONG ABOUT THE WORLD: `_refuse_live_db_under_pytest` in cli.py keys on
# an inherited `PYTEST_CURRENT_TEST`, and its own docstring records that the
# variable is absent during collection and around session-scoped setup, and that
# its fail direction is ALLOW.
#
# The two are complements, not alternatives: this harness sets the variable in
# `pytest_configure`, which runs BEFORE collection -- the window that guard's
# own docstring declares it does not cover.
#
# This variable removes the inference entirely. A test harness SETS it and a
# production process does not, so there is nothing to detect: the resolver
# cannot be mistaken about whether a variable was set.
#
# The guarantee is narrower than it looks, and the narrow one is what to rely
# on. The resolver cannot be mistaken about whether the variable was SET, but
# nothing here detects a SET value that points at the real store. The safety
# comes from the harness, which assigns unconditionally and never with
# `setdefault`.
#
# Keep this a REDIRECT, never a refusal: production is pathless by design, so a
# resolver that refuses the real store breaks every production caller.
MEMORY_DIR_ENV = "PACT_TEST_MEMORY_DIR"

# The store file name. THIS IS THE ONLY PLACE THE NAME IS WRITTEN, and
# `resolve_db_path` is the only expression that composes a path from it. A
# second composition anywhere in this package is a second derivation, whether
# it spells the name again or reuses this constant.
DB_FILENAME = "memory.db"

# The store file bound for the duration of one call, or None outside a scope.
#
# FIRST USE OF contextvars IN THIS PACKAGE. There is no house pattern here to
# follow, so the reasons are recorded rather than assumed.
#
# IT CARRIES THE FULL FILE, NOT A DIRECTORY, and that is a correctness
# requirement rather than a convenience. `--db-path` always names a FILE, and
# that file is routinely not called `memory.db`. Bind a DIRECTORY instead and
# the resolver composes <dir>/memory.db while the connection opens the caller
# file, which is the disagreement this whole design removes.
#
# ⚠️ THREAD HAZARD, LATENT AND NOT LIVE. This package starts no thread today.
# `database.get_connection` passes `check_same_thread=False` to `sqlite3.connect`,
# which DECLARES that a connection may cross a thread. A ContextVar set in one
# thread is NOT readable in a thread started after it, because a new thread
# begins with an empty context. Add a worker thread and this binding goes
# silently absent there, and the store reverts to the default with nothing to
# see. The remedy at that time is to pass the resolved path across the thread
# boundary explicitly. Do not rely on this variable for it.
_STORE_DB_PATH: ContextVar[Optional[Path]] = ContextVar(
    "pact_memory_store_db_path", default=None
)


@contextmanager
def store_scope(db_path: Optional[Path]) -> Iterator[None]:
    """Bind the store file for the duration of the block.

    A REDIRECT, NEVER A REFUSAL. An unbound resolution falls through to the
    environment variable and then to the home directory, so a production caller
    that passes no path keeps reaching the real store. That rule outranks the
    isolation this scope provides: `archive_pin --index N` is a documented
    production command that passes no path on purpose.

    ⚠️ A `db_path` of None INHERITS the enclosing scope. IT MUST NOT RESET TO
    THE DEFAULT, and the obvious spelling does exactly that. An unconditional
    `_STORE_DB_PATH.set(db_path)` writes None, which IS the default, so an inner
    pathless call would CLEAR an outer binding for the length of its block. That
    is not a theoretical case: it is the main path. A scoped caller reaches the
    search layer, which opens `db_connection()` with no argument, and the inner
    reset would send that read back to the default store. The failure is silent
    against a test that sets no outer scope, because there a reset from None to
    None changes nothing observable. Only a NESTED arm separates the two
    spellings.
    """
    if db_path is None:
        yield
        return
    token = _STORE_DB_PATH.set(Path(db_path))
    try:
        yield
    finally:
        _STORE_DB_PATH.reset(token)


_READ_ONLY_SCOPE: ContextVar[bool] = ContextVar(
    "pact_memory_read_only_scope", default=False
)


@contextmanager
def read_only_scope() -> Iterator[None]:
    """Declare that this block must not write to the store.

    The read verbs wrap their store work in this so `ensure_initialized`
    SKIPS migrations and the vector-table CREATE: a READ must not emit schema
    DDL on a store it is only reporting on. It is a DECLARATION, not a
    connection mode — a caller that genuinely needs a write must stay outside
    the scope, and none of the read verbs does.

    The same thread caveat as `store_scope` applies: this package starts no
    thread today, and a ContextVar set here is not visible in a thread
    started later.
    """
    token = _READ_ONLY_SCOPE.set(True)
    try:
        yield
    finally:
        _READ_ONLY_SCOPE.reset(token)


def read_only_scope_active() -> bool:
    """Whether the current block declared that it must not write."""
    return _READ_ONLY_SCOPE.get()


def get_memory_dir() -> Path:
    """Return the base directory for the PACT memory DATABASE.

    NOT for all memory data. `hooks/track_files.py` writes `session-tracking/`
    under the same root but resolves it through `get_claude_config_dir()`, so
    that subtree follows `CLAUDE_CONFIG_DIR` and is unaffected by the variable
    below. Setting only this one relocates the database and leaves the tracking
    data where it was.

    Resolution order:
      1. The store scope, when a block has bound one. Its parent directory.
      2. The `PACT_TEST_MEMORY_DIR` environment variable, when set and non-empty.
      3. `~/.claude/pact-memory` (matches a default-root pin in code — do not migrate).

    THE SCOPE OUTRANKS THE VARIABLE, and the reason is coherence rather than
    preference. `database.get_connection` gave an explicit caller path
    precedence above the resolver before this scope existed, so putting the
    scope on top introduces no new precedence. It makes the resolver AGREE with
    the connection. The opposite order is disqualified: the test harness assigns
    that variable for each test, so a scoped call would resolve to the harness
    directory whether the store binding worked or not, and no arm could tell the
    two apart.

    An empty value counts as unset, so `PACT_TEST_MEMORY_DIR=""` cannot silently
    relocate the store to the current directory.
    """
    scoped = _STORE_DB_PATH.get()
    if scoped is not None:
        return scoped.parent
    override = os.environ.get(MEMORY_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "pact-memory"


def resolve_db_path() -> Path:
    """Return the memory database file path. THE ONE COMPOSING EXPRESSION.

    Creates no directory. `database.get_db_path` calls this and then creates the
    parent, so the side effect belongs to the caller that intends it and a
    read-shaped caller can ask where the store is without leaving a tree behind.

    A bound scope returns the file WHOLE. It does not recompose the name from
    `get_memory_dir()`, because the scoped file is routinely not called
    `memory.db` and a recomposition would name a different file than the
    connection opens.
    """
    scoped = _STORE_DB_PATH.get()
    if scoped is not None:
        return scoped
    return get_memory_dir() / DB_FILENAME


# WHERE A RESOLVED STORE PATH CAME FROM.
#
# THE ORIGIN IS CARRIED, NOT INFERRED, and that is the whole point of these
# names. A caller that wants to treat a caller-supplied path differently from
# a derived one could instead test whether a scope is bound, and that test is
# a PROXY: it holds only while every present and future binding site binds a
# path a person typed. Nothing can hold that. An origin the resolver REPORTS
# cannot drift away from the resolution it describes.
STORE_ORIGIN_SCOPE = "scope"
STORE_ORIGIN_ENV = "environment"
STORE_ORIGIN_HOME = "home"

# The origins the RESOLVER derives with no caller path. A consumer that creates
# a directory as a side effect must create it for THESE and not for a path the
# caller supplied.
DERIVED_STORE_ORIGINS = frozenset({STORE_ORIGIN_ENV, STORE_ORIGIN_HOME})


def store_path_origin() -> str:
    """Report where `resolve_db_path` takes the store file from. Creates nothing.

    THE ORDER HERE MIRRORS `get_memory_dir` AND MUST CONTINUE TO. A reader that
    changes one order and not the other makes this function describe a
    resolution that does not happen.
    """
    if _STORE_DB_PATH.get() is not None:
        return STORE_ORIGIN_SCOPE
    if os.environ.get(MEMORY_DIR_ENV):
        return STORE_ORIGIN_ENV
    return STORE_ORIGIN_HOME


def compute_db_path() -> Path:
    """Return the memory database file path. Creates no directory.

    DELEGATES AND COMPOSES NOTHING. This function used to compose the name a
    second time, alongside `database.get_db_path`, so the two could drift apart
    with nothing to report it. Its result is unchanged.

    NAMED `compute_` TO SEPARATE IT FROM `database.get_db_path`, which resolves
    the same location and then creates the directory with mode 0o700. The two
    differ only in that side effect, and they used to differ only in which
    module you imported from -- so `from .config import get_db_path` in
    production would have silently stopped creating the directory, with nothing
    failing loudly. There is no longer a `get_db_path` here to reach for.
    """
    return resolve_db_path()
