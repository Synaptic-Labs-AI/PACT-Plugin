"""
Location: pact-plugin/hooks/shared/backlog.py
Summary: WRITE side and CLI of the cross-session backlog — item mutations,
         atomic save, corrupt-file repair, batched pact-memory resolution,
         one batched tracker query, and the reconciliation that reports drift.
Used by: commands/next.md, invoked as a direct script —
         python3 {plugin_root}/hooks/shared/backlog.py <subcommand>.
         NOT a lifecycle hook: this file MUST NOT be registered in hooks.json.

THE DEPENDENCY ARROW IS ONE-WAY. This module imports backlog_store;
backlog_store imports this module never. session_init reaches only
backlog_store, so pact-memory and the tracker stay out of the session-start
import closure. An import in the other direction would undo that.

Two measured constraints govern the shape here and neither is a preference:
a subprocess per memory id measures ~9.6s median and is forbidden, so ids
resolve in-process against ONE store instance; and `gh` does not self-timeout
against an unreachable host, so every tracker call carries an explicit timeout
and results are parsed from stdout rather than gated on a return code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the package-path import (`from shared.X import Y`) resolvable when this
# file runs as a direct script. In script mode Python puts this file's OWN
# directory (`hooks/shared/`) on sys.path[0], not `hooks/`, so `from shared.X`
# would raise ModuleNotFoundError and a bare `from X` fallback would import a
# sibling as a TOP-LEVEL module, breaking that sibling's own relative imports.
# Idempotent: skip when already present.
_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from shared.backlog_store import (  # noqa: E402  # follows the sys.path bootstrap
    MEMORY_MAX_IDS,
    NOTE_MAX_CHARS,
    SCHEMA_VERSION,
    SETTLED,
    STATUSES,
    BacklogFileError,
    BacklogUnreadableError,
    _archived,
    _enclosing_checkout,
    _resolved,
    _unexamined,
    as_datetime,
    file_local_flags,
    read_json,
    validate,
)
from shared.claude_md_manager import _stat_if_present  # noqa: E402  # follows the bootstrap
from shared.paths import get_backlog_dir  # noqa: E402  # follows the bootstrap

# Exit codes.
#
# WHEN A CODE HERE TURNS OUT TO COLLIDE, MOVE THIS TOOL'S VERDICT OFF IT — do
# not teach the caller to disambiguate some other way, and do not keep the code
# because it is still correct most of the time.
#
# CHOOSING THE REPLACEMENT: the test is NOT "is this number unused in this
# file". Ask "can any layer beneath us emit it" — the interpreter, a shell, or
# a library. Take the answer from sysexits.h's 64-78 band, which exists so a
# program can state its OWN verdict: CPython exits 0, 1, 2 and 120, shells
# reserve 126, 127 and 128+N, argparse exits 2, so a code from that band can
# only have come from code that chose it. Then state the measurement at the
# constant, as the three below do.
#
# The unused-here test is what failed all three times: 2 was free by this
# file's own accounting on each occasion, and something underneath produced it
# anyway.
_EXIT_OK = 0
# MOVED OFF 2 BECAUSE THE INTERPRETER ALSO EXITS 2 when it cannot open the
# script it was handed, which made a process that NEVER STARTED read as one
# that deliberately declined. Measured: a run from the wrong directory produced
# six failures whose messages read as considered refusals, one of them stating
# a design rationale — "unparseable=2, unreadable=2" — for a program that had
# not run. Three agents spent about an hour on four wrong hypotheses. 65 is
# EX_DATAERR, the neighbour of the _EXIT_USAGE below and the other half of the
# same distinction: 64 says the command line was malformed, 65 says the command
# line was fine and the DATA was not acceptable. That is what every refusal
# here means — nothing was written, the store is untouched, fix the input.
_EXIT_REFUSED = 65
# A SEPARATE CODE FOR UNREADABLE, because repair is the one operation that moves
# user data and it must be reachable ONLY from a file that genuinely will not
# parse. Sharing code 2 with the refusal exits made a merely NON-CONFORMING file
# — readable, renderable, fixable by editing one field — route the agent to
# repair, which renames it aside. Measured before this split: `set` on a file
# with a bad item id and `show` on an unparseable one BOTH exited 2. The
# alternative was to have the caller read the message text, which is the
# substring routing this design refuses everywhere else.
_EXIT_UNREADABLE = 3
# A MALFORMED COMMAND LINE IS NOT A REFUSAL, and sharing argparse's default 2
# with `_EXIT_REFUSED` made the two indistinguishable: a mistyped invocation
# reported as the tool declining. Measured: `--backlog-dir` placed after the
# subcommand returned 2 across five different store states, reading as five
# real refusals. 64 is EX_USAGE from sysexits.h, the BSD convention for exactly
# this; it collides with none of 0/2/3 and sits below the 126-255 range shells
# reserve for not-executable, not-found and signal deaths.
_EXIT_USAGE = 64

# One threshold serves two flags. On an `active` item, untouched past this
# means the work stalled; on an UNRANKED `planned` item it means nobody has
# ordered it. Both readings survive the one value — if a future arc needs them
# to diverge, that is the day a second constant is earned.
_STALE_AFTER = timedelta(days=14)

# The tracker is a constant cost, not a per-item one: one batched round trip
# measures flat at roughly a third of a second from 1 ref to 25. The timeout is
# mandatory because `gh` runs to the caller's kill ceiling against an
# unreachable host rather than giving up on its own.
_TRACKER_TIMEOUT_SECONDS = 5

# Characters a repository owner or name may hold. Everything else is refused
# rather than interpolated into a query.
# `\Z` rather than `$`, matching the sibling anchor in backlog_store.py: Python's
# `$` also matches before a trailing newline, so `$` would accept "owner\n".
_SLUG_CHARS = re.compile(r"^[A-Za-z0-9._-]+\Z")

# Sentinel for "the caller explicitly asked to clear this field", which None
# cannot express here because None already means "the flag was not passed".
_CLEAR = object()

# Returned per id when the memory store could not be opened at all. Distinct
# from None, which means the store was asked and had no such record.
_UNVERIFIABLE: Dict[str, Any] = {"unverifiable": True}


class BacklogWriteError(Exception):
    """The write path cannot proceed. Raised rather than defaulted, because a
    backlog written under a wrong or empty project name is a silent data-loss
    path."""


# ---------------------------------------------------------------------------
# Store resolution — the only surface that derives a project name or path
# ---------------------------------------------------------------------------

def _memory_api():
    """Import pact-memory lazily, from the write side only.

    Kept inside a function rather than at module scope so that importing this
    module for its CLI does not pay for the store, and so the read side has no
    route to it even by accident.
    """
    scripts_parent = Path(__file__).resolve().parents[2] / "skills" / "pact-memory"
    if str(scripts_parent) not in sys.path:
        sys.path.insert(0, str(scripts_parent))
    from scripts import memory_api

    return memory_api


def store_path(backlog_dir: Optional[Path] = None) -> Path:
    """Absolute path of this project's backlog file.

    Raises BacklogWriteError when the project id will not resolve. A default
    slug would write a backlog nobody can find again.
    """
    project = _memory_api().PACTMemory._detect_project_id()
    if not project:
        raise BacklogWriteError(
            "project id did not resolve, so there is no name to write under. "
            "Nothing was written. Run from inside the project directory, or set "
            "CLAUDE_PROJECT_DIR."
        )
    root = get_backlog_dir() if backlog_dir is None else backlog_dir
    return root / f"{project}.json"


def project_root() -> Path:
    """The path a backlog stores as project_path, resolved in this order:

    1. Git resolves a MAIN repo root from CLAUDE_PROJECT_DIR: that root. A
       repo root, a subdirectory below one and a linked worktree all
       normalise to the same main root, which is what puts every checkout of
       this project in one place: the read path is deliberately git-free and
       cannot perform this resolution itself, so the writer does it and
       records the result.
    2. Git resolves nothing, CLAUDE_PROJECT_DIR names an existing directory,
       and no `.git` sits at or above it: that directory, resolved. This is
       a workspace umbrella — a directory whose children are separate
       repositories, itself under no `.git` — and it is a stable project key
       in its own right.
    3. Otherwise refuse: the variable is unset, names no directory, names a
       directory inside a repository git could not read, or names one whose
       path cannot be examined, so that whether a repository encloses it
       cannot be told (the refusal names the level). Writing under a
       checkout git failed to resolve would key a second backlog on a
       subdirectory or worktree that every git-present session keys on the
       main root; a default would write a backlog nobody can find.

    ANCHORED ON CLAUDE_PROJECT_DIR, NOT THE PROCESS CWD. Unanchored, a write run
    from another repository resolved THAT repo's root, so `checkout_roots()`
    stored the other project's worktrees into this project's backlog — after
    which this project's sessions stop matching their own file and the other
    project's sessions can claim it. That is the cross-project bleed the
    disambiguator exists to prevent, arriving through the writer. Every input
    this function ACCEPTS is one `_detect_project_id` names from the same
    resolved directory, so the stored name and the stored paths agree; the
    inputs on which the two would diverge are the ones this function refuses,
    and a refusal writes nothing.

    TWO SESSION-RECORD RULES SIT AROUND THAT ANCHOR, both reached through the
    existing `_memory_api()` channel (no new import direction):

    - DISAGREEMENT REFUSES. When CLAUDE_PROJECT_DIR and the session record are
      both present and name different directories, the write fails closed,
      naming both values and the remedy. Reads stay liberal (env wins); a
      write under a disagreed scope is the silent mis-scope this family of
      issues pays for.
    - ABSENCE ANCHORS ON THE RECORD. When CLAUDE_PROJECT_DIR is unset, the
      recorded project_dir stands in for it below, so an umbrella session
      whose env var never arrived still resolves the session's own scope
      instead of refusing. The refusal itself is unchanged — it stays the
      guard for shells with no session behind them (cron, manual CLI).
    """
    memory_api = _memory_api()
    disagreement = memory_api.env_record_project_dir_disagreement()
    if disagreement is not None:
        raise BacklogWriteError(
            memory_api.format_project_dir_disagreement(*disagreement)
        )
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    record_dir = "" if env_dir else memory_api.get_project_dir_from_session_record()
    # `source` names where the anchor came from so the refusal below never
    # attributes a recorded value to the (unset) variable, or vice versa.
    project_dir = env_dir or record_dir or None
    source = "CLAUDE_PROJECT_DIR" if env_dir else "the session record's project_dir"
    root = memory_api.main_repo_root(project_dir)
    if root is not None:
        return root
    if project_dir:
        why = _umbrella_refusal(project_dir, source)
        if why is None:
            # _resolved never raises: an unresolvable directory falls back to
            # its unresolved path, the same fallback the detector takes for
            # the name.
            return _resolved(Path(project_dir))
    else:
        why = "CLAUDE_PROJECT_DIR is unset"
    raise BacklogWriteError(
        f"the main repository root did not resolve and {why}, so project_path "
        f"would be wrong or empty. Nothing was written. Set CLAUDE_PROJECT_DIR "
        f"to the project directory; a directory with no repository of its own "
        f"or above it is accepted."
    )


def _umbrella_refusal(project_dir: str, source: str) -> Optional[str]:
    """Why `project_dir` cannot key a backlog as an umbrella, or None when it can.

    It can when it is an existing directory with no `.git` at or above it. A
    level that cannot be examined (EACCES, EPERM) is a refusal naming that
    level, never a pass: whether a repository encloses the directory cannot be
    told, and guessing "none" would key a second backlog on a subdirectory.
    """
    try:
        found = _stat_if_present(project_dir)
        if found is None or not stat.S_ISDIR(found.st_mode):
            return f"{source}={project_dir!r} does not name an existing directory"
        if _enclosing_checkout(_resolved(Path(project_dir))) is None:
            return None
    except OSError as exc:
        return (
            f"{_unexamined(exc, Path(project_dir))} could not be examined "
            f"({exc.strerror or type(exc).__name__}), which leaves open whether "
            f"a repository encloses {source}={project_dir!r}"
        )
    return f"{source}={project_dir!r} sits inside a repository git could not read"


def checkout_roots() -> List[str]:
    """Every checkout of this project — the main one and each worktree — as
    absolute resolved paths. NEVER empty and never absent.

    This is what the read path matches against, so a wrong entry would claim
    another project's session. A missing entry costs that checkout a loud
    resolution failure UNLESS it sits inside a checkout that IS recorded, in
    which case the read path's enclosing-checkout rung still matches it. The
    fallback when git cannot answer is the project root alone, which is
    always right about at least itself.

    Deliberately NOT merged with _branch_and_worktree_names, which runs the
    same porcelain: that caller reduces each `worktree` line to a BASENAME for
    substring matching, this one resolves it to an absolute path for exact
    membership — opposite ends of the same string, so one helper serving both
    still needs a mode flag. It also runs a second git call this one does not
    need. Two subprocesses per write on a path the design says can afford one.
    """
    porcelain = _run_capture(
        ["git", "-C", str(project_root()), "worktree", "list", "--porcelain"]
    )
    # os.path.realpath, not Path.resolve: on 3.9 resolve() raises RuntimeError
    # for a worktree whose directory became a symlink loop, while realpath
    # keeps the looping component unresolved, as resolve() does on 3.13+.
    roots = [
        os.path.realpath(line[len("worktree "):])
        for line in (porcelain or "").splitlines()
        if line.startswith("worktree ")
    ]
    return roots or [str(project_root())]


# Key under which load_or_create stashes the bytes it read, so save() can
# refuse a write that would discard somebody else's. Carried ON THE LOADED
# DOCUMENT rather than in a path-keyed cache: two loads of one path in one
# process are two independent baselines, and a shared cache lets a later load
# overwrite an earlier one's — measured, and it let the lost update straight
# through. save() pops it before writing, so it never reaches the file.
# CONTENT, not `updated`: that field is whole-second (`_now_iso`) and two
# consecutive calls in one process return the byte-identical string, so a
# guard keyed on it would be coarser than the interval between the events it
# guards and would pass most reliably in exactly the case it exists to catch.
_BASELINE_KEY = "__baseline_bytes__"
_UNSET = object()


def _bytes_now(path: Path) -> Optional[bytes]:
    """The file's current bytes, or None when it is not readable as a file."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def load_or_create(path: Path) -> Dict[str, Any]:
    """Load this project's backlog, or build an empty one in memory.

    An absent file is normal and yields a fresh document. An UNPARSEABLE file
    raises, which the CLI turns into a repair offer.

    A file that parses but does NOT CONFORM does not raise, and that asymmetry
    is deliberate. The read path already renders a non-conforming file rather
    than suppressing it, so raising here would let a backlog the user just saw
    at session start be renamed off the read path by the repair route — the two
    sides disagreeing about whether one file is usable. The schema problems are
    surfaced as flags instead, and save() still refuses to write while any
    remain, so nothing invalid is persisted and nothing readable is moved aside.
    """
    if path.exists():
        baseline = _bytes_now(path)
        data = read_json(path)
        data[_BASELINE_KEY] = baseline
        return data
    return {
        _BASELINE_KEY: None,
        "version": SCHEMA_VERSION,
        "project": path.stem,
        # project_path stays a SIBLING of roots, not roots[0]: three consumers
        # need the project root specifically (_plan_flags, the _abandoned_flags
        # git -C, and rename detection), and roots[0] would tie them to
        # porcelain ordering.
        "project_path": str(project_root()),
        "roots": checkout_roots(),
        "updated": _now_iso(),
        "items": [],
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def save(data: Dict[str, Any], path: Path) -> List[str]:
    """Validate, then write atomically. Returns the problem list UNWRITTEN.

    A non-empty return means nothing was written and the caller must fix the
    data. There is no truncation anywhere in this module — an over-long note is
    refused, never shortened, because silently shortening one would lose the
    intent the note exists to carry.
    """
    # roots is refreshed on EVERY write, before validation, so a checkout added
    # since the last write is recorded by the next one. That is what makes the
    # read path's exact-membership rule self-healing: its loud message names
    # /PACT:next, and /PACT:next writes.
    data["roots"] = checkout_roots()
    problems = validate(data)
    if problems:
        return problems
    # Compare-and-swap, immediately before the write. Reproduced without it:
    # two load_or_create calls before either save, both returned ok, one write
    # silently won and the other writer's item reverted with no error. Refused
    # through the existing problem-list shape, so no new raise site.
    # THE POP IS ON THE SUCCESS PATH ONLY. Popping inside the condition ran it
    # on the refusal too, handing the rejected document back with no baseline —
    # so a caller that retried the same object wrote unguarded and destroyed the
    # other writer's change one retry later. Measured. `_UNSET` rather than
    # `.get()` because a legitimately-empty baseline is falsy.
    baseline = data.get(_BASELINE_KEY, _UNSET)
    if baseline is not _UNSET and _bytes_now(path) != baseline:
        return [
            f"{path} changed since it was read, so writing would discard the "
            f"other change. Nothing was written. Re-run the command; do NOT "
            f"reapply your change to the copy you are holding, which is now "
            f"stale."
        ]
    data.pop(_BASELINE_KEY, None)

    data["updated"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=False)
            stream.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return []


def repair(path: Path, force: bool = False) -> Tuple[Path, str]:
    """Move a CORRUPT backlog aside and report where it went.

    REFUSES A FILE IT CAN READ unless `force`, and equally refuses one whose
    BYTES it cannot read, where corruption is undetermined rather than ruled
    out. Repair is the one operation here that moves the user's data, and
    nothing in the code used to stop it moving a perfectly good backlog — only
    the command file's prose and an agent's compliance with it. The condition
    for the readable refusal is whether `read_json` succeeds, which is
    exactly the corruption line: a dict has a key space and can be interrogated,
    so it is non-conforming at worst; an array or a truncated file cannot be
    asked anything and is corrupt. A non-conforming file therefore needs the
    override too, and that is correct — the override IS the deliberate-archive
    path, not an escape hatch for a rule that got in the way.

    The guard is HERE rather than in the CLI handler because a call-site guard
    leaves this function able to move a readable file for the next caller.

    Renamed, never overwritten and never deleted: destroying a corrupt file to
    fix corruption would eat this design's own thesis, and the moved-aside copy
    is what makes a wrong rebuild recoverable. The READ path never does this —
    it runs at every session start, so a rename there would leave one
    moved-aside copy per session.
    """
    # The probe runs under --force too. Unforced it decides whether to refuse;
    # FORCED it decides what the success message may claim, because a move is
    # not a rescue when we could not read the bytes in the first place.
    caveat = ""
    try:
        read_json(path)
    except BacklogUnreadableError as exc:
        # COULD NOT READ THE BYTES, so corruption is undetermined and we
        # must not move the file. A permission bit or a directory at the
        # path is not a broken backlog.
        if not force:
            raise BacklogWriteError(
                f"{path} {exc.problem}, so whether it is corrupt is unknown. "
                f"Nothing was moved. Pass --force to rename it aside anyway — "
                f"it is kept, not deleted."
            ) from exc
        caveat = (
            f" It was NOT made readable: it {exc.problem}, and that still "
            f"applies to the copy."
        )
    except BacklogFileError:
        pass              # read it, and it is not a backlog — repair's case
    else:
        if not force:
            raise BacklogWriteError(
                f"{path} is readable, so it is not corrupt. Nothing was moved. "
                f"Pass --force to rename it aside anyway — it is kept, not deleted."
            )

    # The moved-aside name deliberately DROPS the .json suffix. The read path
    # globs *.json across the whole store directory and reads every match, so a
    # corrupt file kept under .json would be picked up again at the next
    # session start and the loud state would survive its own repair.
    # MICROSECONDS, not seconds. Path.rename OVERWRITES its destination on
    # POSIX (measured — it does not raise), so two repairs inside one second
    # destroyed the first moved-aside copy: the very copy this function exists
    # to preserve. Microsecond granularity keeps the names sortable while making
    # a collision need two repairs in the same microsecond.
    aside = path.with_name(
        f"{path.stem}.corrupt-{datetime.now(timezone.utc):%Y%m%dT%H%M%S.%fZ}.bak"
    )
    path.rename(aside)
    return aside, f"moved the backlog aside to {aside}.{caveat}"


def _items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The item dicts, or an empty list. THE ONLY way this module reads them.

    `data` reaches here from a file that PARSED but need not CONFORM, since the
    read-anyway change stopped a schema problem raising. So `items` can be a
    truthy non-iterable, and iterating one raises TypeError — not a
    BacklogFileError, so it crashes the CLI instead of reporting. Two of the
    four call sites carried this guard and two did not; one accessor means a
    fifth cannot be added without it.
    """
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def new_item_id(data: Dict[str, Any]) -> str:
    """Four hex characters, unique within this file. The taken set spans BOTH
    lists: an id retired to the archive stays taken, so no live item is ever
    minted an id a relation could resolve to the wrong record."""
    taken = {item.get("id") for item in _items(data) + _archived(data)}
    while True:
        candidate = secrets.token_hex(2)
        if candidate not in taken:
            return candidate


def add_item(data: Dict[str, Any], title: str, **fields: Any) -> Dict[str, Any]:
    """Append a new item. Unset fields keep their empty defaults so every item
    has the same shape.

    REFUSES rather than repairs when `items` is present and not a list. The
    file parses but does not conform, and `_items()` — which every READER here
    routes through — does not fit: it returns a filtered COPY, and appending to
    a copy writes nothing. Coercing `data["items"]` to a clean list instead
    would silently discard whatever was there and then pass validate(), turning
    a refusal the user can act on into an unannounced overwrite of their file.
    """
    # `"items" in data`, NOT `data.get("items") is not None`. The clause only
    # needs to permit an ABSENT key, which setdefault then creates — but
    # `.get()` collapses absent and JSON `null` into the same None, so the
    # clause that permitted absence also permitted null, and `setdefault` then
    # returned None for `.append` to raise on. Membership separates the two
    # where `.get()` cannot: absence is legitimate, an explicit null is a
    # non-conforming value and validate() reports it as one.
    if "items" in data and not isinstance(data["items"], list):
        items = data["items"]
        raise BacklogWriteError(
            f"items is {type(items).__name__}, expected a list. The backlog "
            f"does not conform and nothing was written — run `show` to see "
            f"every schema problem."
        )
    today = _now_iso()[:10]
    item: Dict[str, Any] = {
        "id": new_item_id(data),
        "title": title,
        "status": "planned",
        "rank": None,
        "blocked_by": [],
        "batch_with": [],
        "exclusive_with": [],
        "ref": None,
        "plan": None,
        "memory": [],
        "note": None,
        "added": today,
        "touched": today,
    }
    _apply_fields(item, fields)
    data.setdefault("items", []).append(item)  # items is a list by the guard
    return item


def _apply_fields(item: Dict[str, Any], fields: Dict[str, Any]) -> None:
    """Apply passed fields to an item. THE ONLY place the three-way distinction
    lives: None means the flag was not passed and the field is left alone,
    _CLEAR means the caller asked to clear it, anything else is a value.

    Both callers route through here so the two cannot disagree about what an
    unpassed flag means — they each carried their own copy of the None filter
    before, and the copy is what made `--ref none` silently inert.
    """
    for key, value in fields.items():
        if value is None:
            continue
        item[key] = None if value is _CLEAR else value


def find_item(data: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    for item in _items(data):
        if item.get("id") == item_id:
            return item
    return None


def update_item(item: Dict[str, Any], **fields: Any) -> None:
    """Apply the named fields and stamp `touched`. Validation happens in
    save(), so a rejected change never reaches the file."""
    _apply_fields(item, fields)
    item["touched"] = _now_iso()[:10]


def archive_items(data: Dict[str, Any], item_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """Move settled items from `items` to `archive`, ALL-OR-NOTHING.

    Every id is checked BEFORE anything moves: an unknown id, an
    already-archived id (the two are distinguished — one was a valid command
    yesterday) or a non-settled item refuses the WHOLE command with nothing
    written, so a multi-id invocation never half-moves. Item fields are
    untouched — no `touched` stamp: archival is a relocation, not an edit, and
    falsifying "last meaningful touch" buys nothing (no settled consumer reads
    it). There is no unarchive verb: nothing asks for a route back, and `set`
    on an archived id is refused by name rather than reaching save.
    """
    by_id = {item.get("id"): item for item in _items(data)}
    archived_ids = {item.get("id") for item in _archived(data)}
    # Same membership-not-`.get()` distinction add_item draws on `items`: an
    # ABSENT archive is created by setdefault below, but a PRESENT non-list one
    # would reach `.extend` and raise AttributeError — a traceback where the
    # design is a named refusal. Checked at ENTRY so the structural problem is
    # named before any id lookup, the add_item ordering.
    if "archive" in data and not isinstance(data["archive"], list):
        archive = data["archive"]
        raise BacklogWriteError(
            f"archive is {type(archive).__name__}, expected a list. The backlog "
            f"does not conform and nothing was written — run `show` to see "
            f"every schema problem."
        )
    moving = []
    seen = set()
    for item_id in item_ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        item = by_id.get(item_id)
        if item is None:
            if item_id in archived_ids:
                raise BacklogWriteError(
                    f"item {item_id!r} is already archived. Nothing was written."
                )
            raise BacklogWriteError(
                f"no item with id {item_id!r}. Nothing was written."
            )
        status = item.get("status")
        if status not in SETTLED:
            raise BacklogWriteError(
                f"item {item_id!r} is {status!r} — the archive holds settled "
                f"items only; settle it first with set --status done|dropped. "
                f"Nothing was written."
            )
        moving.append(item)
    # Reaching the mutation at all means every id resolved to a live settled
    # item, which means `items` IS a list — a non-list would have emptied
    # `by_id` and refused the first id.
    if moving:
        moving_ids = {item.get("id") for item in moving}
        # The isinstance guard PRESERVES non-dict entries rather than dropping
        # them: `.get` on one raises AttributeError, and filtering it OUT of the
        # rebuilt list would remove the very entry save()'s validate() names —
        # turning a refusal into a silent discard.
        data["items"] = [
            item
            for item in data["items"]
            if not isinstance(item, dict) or item.get("id") not in moving_ids
        ]
        data.setdefault("archive", []).extend(moving)
    return moving


# ---------------------------------------------------------------------------
# Resolution — pact-memory in-process, the tracker in one batched call
# ---------------------------------------------------------------------------

def resolve_memory_ids(
    ids: Sequence[str], store: Any = None
) -> Dict[str, Optional[Dict[str, Any]]]:
    """One store instance, N lookups. A None VALUE means unresolvable, and
    _UNVERIFIABLE means the store could not be opened to ask at all.

    THOSE TWO ARE DIFFERENT ANSWERS AND MUST NOT COLLAPSE. An unopenable store
    once made every linked id report as deleted — an inability to CHECK
    presented as a definite negative, which is the same conflation the tracker
    path already refuses by giving `unverifiable` its own state. The memory
    path now matches it rather than inventing a third pattern.

    Returning a value per id rather than omitting the key is what lets a caller
    tell "resolved to nothing" from "never asked".

    `store` is a testing seam: pass an opened store to exercise the lookup
    without reaching the real one. None opens the real store.

    Resolution goes through `get <id>`, never a search. A stored id resolves
    across project scope where a search cannot, and resolving a known id set is
    not a search, so this satisfies the prohibition rather than skirting it.
    """
    # UNVERIFIABLE IS THE DEFAULT, and that is the whole fix rather than a
    # style choice. Starting at None made "we never found out" indistinguishable
    # from "we asked and there is no such record", so every path that failed to
    # get an answer — an unopenable store, a lookup that raised, a future early
    # return — silently reported the records as DELETED. Starting here means
    # only a real answer overwrites it, and any new non-answer path inherits the
    # honest value for free.
    resolved: Dict[str, Optional[Dict[str, Any]]] = {
        identifier: _UNVERIFIABLE for identifier in ids
    }
    if not ids:
        return resolved

    if store is None:
        try:
            store = _memory_api().get_memory_instance()
        except Exception:
            return resolved

    # ponytail: N sequential gets against one open store. The measured cost was
    # process spawn, and that is already gone; push the id set into one query
    # only if a real backlog ever makes this the bottleneck.
    for identifier in ids:
        try:
            record = store.get(identifier)
        except Exception:
            # A store that opens and then fails on a lookup — missing, locked
            # or corrupt database — is still an inability to CHECK. Stop: the
            # remaining ids would fail the same way and give the same answer N
            # times, and they already hold it.
            break
        resolved[identifier] = (
            None
            if record is None
            else {
                "id": getattr(record, "id", identifier),
                "updated_at": _as_text(getattr(record, "updated_at", None)),
            }
        )
    return resolved


def resolve_refs(refs: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """ONE batched tracker query for every ref. Never a call per ref.

    Every entry comes back with a `state` of `open`, `closed` or
    `unverifiable`. Unverifiable covers a ref this tracker cannot address, a
    ref that does not exist, an absent `gh`, and an unreachable host — the
    caller reports it as such rather than as a clean pass.

    STDOUT IS THE RESULT, NOT THE RETURN CODE. `gh api graphql` exits 1 on
    PARTIAL success with every field that did resolve present on stdout, so a
    return-code gate would discard good resolutions and report a blanket
    tracker outage where per-ref `unverifiable` is required.
    """
    outcome: Dict[str, Dict[str, Any]] = {
        ref: {"state": "unverifiable", "reason": "no tracker resolution for this ref"}
        for ref in refs
    }
    numbered = {ref: _issue_number(ref) for ref in refs}
    addressable = {ref: number for ref, number in numbered.items() if number is not None}
    if not addressable:
        return outcome

    repo = _repo_slug()
    if repo is None:
        for ref in addressable:
            outcome[ref] = {"state": "unverifiable", "reason": "no tracker configured"}
        return outcome

    owner, name = repo
    aliases = {f"r{index}": ref for index, ref in enumerate(sorted(addressable))}
    selections = " ".join(
        f'{alias}: issueOrPullRequest(number: {addressable[ref]}) '
        f"{{ ... on Issue {{ state stateReason }} "
        f"... on PullRequest {{ state merged }} }}"
        for alias, ref in aliases.items()
    )
    query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {selections} }} }}'

    stdout = _run_capture(["gh", "api", "graphql", "-f", f"query={query}"])
    if stdout is None:
        for ref in addressable:
            # NOT "tracker unreachable". `_run_capture` returns None for THREE
            # causes — a timeout, an absent `gh`, and an OSError at the argv
            # wall — and only the first is unreachability. At the argv wall the
            # tracker is reachable and configured and the request never left,
            # so naming the network sent the reader to check the one thing that
            # was fine. This says what is true of all three and asserts no cause.
            outcome[ref] = {
                "state": "unverifiable",
                "reason": "the tracker call did not complete",
            }
        return outcome

    try:
        payload = json.loads(stdout)
    except ValueError:
        return outcome

    # `or {}` alone defends against a FALSY value and not against a truthy
    # wrong-typed one, so a payload carrying `"data": 5` reached `(5).get` and
    # raised AttributeError. An empty mapping here reports every ref as
    # unverifiable through the loop, which is what an unreachable tracker
    # already does.
    data = payload.get("data") if isinstance(payload, dict) else None
    repository = data.get("repository") if isinstance(data, dict) else None
    if not isinstance(repository, dict):
        repository = {}
    for alias, ref in aliases.items():
        node = repository.get(alias)
        if not isinstance(node, dict) or not node.get("state"):
            outcome[ref] = {
                "state": "unverifiable",
                "reason": "the tracker did not resolve this ref",
            }
            continue
        outcome[ref] = _ref_outcome(node)
    return outcome


# A closed ref is NOT evidence the work was done. GitHub closes an issue for
# more than one reason and two of them mean the OPPOSITE. Measured live over a
# sample of this repo's most recent closed issues: COMPLETED and NOT_PLANNED
# both occur. DUPLICATE is documented, did NOT occur in that sample, and is
# handled anyway — handled-and-unobserved, which is the part a later reader
# would otherwise round off to "unused".
# A PR carries the same distinction without a second field: MERGED is done,
# CLOSED is abandoned, and those two arrived together and were collapsed into
# one bucket on the line that used to be here.
_ABANDONED_REASONS = {"not_planned", "duplicate"}


def _ref_outcome(node: Dict[str, Any]) -> Dict[str, Any]:
    """`done`, `abandoned`, `closed` or `open` for one resolved ref.

    `closed` is the honest third bucket, not a fallback: an issue closed before
    GitHub had `stateReason` returns None for it, and a closed-with-no-reason
    ref cannot be called done or abandoned without inventing the answer.
    """
    state = str(node.get("state") or "").lower()
    if state == "merged":
        return {"state": "done"}
    if state != "closed":
        return {"state": "open"}
    reason = str(node.get("stateReason") or "").lower()
    if reason in _ABANDONED_REASONS:
        return {"state": "abandoned", "reason": reason}
    if reason == "completed":
        return {"state": "done"}
    # A PR closed unmerged has no stateReason and is abandoned by definition;
    # an issue with no reason is genuinely undetermined.
    # PRESENCE, not truth: the case this catches is `merged: False`, which is
    # falsy, so `node.get("merged")` would break exactly the closed-unmerged PR
    # and nothing else. The key is selected only on the PullRequest fragment, so
    # its presence is what separates a closed PR from a closed issue.
    if "merged" in node:
        return {"state": "abandoned", "reason": "closed unmerged"}
    return {"state": "closed"}


def _repo_slug() -> Optional[Tuple[str, str]]:
    """Owner and name of the configured tracker repository, or None.

    One call, and its cost is a constant rather than a per-ref one. None means
    no tracker is configured, which is a fully supported state.
    """
    stdout = _run_capture(["gh", "repo", "view", "--json", "nameWithOwner"])
    if stdout is None:
        return None
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    # ValueError covers unparseable bytes and nothing else: a payload that
    # parsed to an array reached `.get` and a numeric nameWithOwner reached
    # `.partition`, both AttributeError, neither caught.
    if not isinstance(payload, dict):
        return None
    slug = payload.get("nameWithOwner")
    if not isinstance(slug, str):
        return None
    owner, _, name = slug.partition("/")
    # The slug reaches us from the local git remote, so it is interpolated
    # into a GraphQL document only after it is confirmed to hold nothing but
    # the characters a repository slug may contain. A slug carrying a quote or
    # a brace would otherwise reshape the query.
    if not owner or not name:
        return None
    if not all(_SLUG_CHARS.match(part) for part in (owner, name)):
        return None
    return owner, name


def _run_capture(command: Sequence[str]) -> Optional[str]:
    """Run a tracker command and return its stdout, or None when there is none.

    Non-zero exit is NOT a failure here: stdout carries the resolved fields
    even when one ref in the batch was bad. Only an empty stdout, a missing
    binary or a timeout is a failure.
    """
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=_TRACKER_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return result.stdout if result.stdout.strip() else None


def _issue_number(ref: Any) -> Optional[int]:
    """The issue number a ref addresses, or None when this tracker cannot.

    A ref is an opaque string by design — a bare number, a Linear key, a URL
    and null are all valid — so anything this resolver cannot address is
    reported unverifiable rather than treated as absent.
    """
    if not isinstance(ref, str):
        return None
    digits = ref.strip().lstrip("#").rstrip("/").rsplit("/", 1)[-1]
    return int(digits) if digits.isdigit() else None


# ---------------------------------------------------------------------------
# Reconcile — reports drift, repairs nothing
# ---------------------------------------------------------------------------

def reconcile(
    data: Dict[str, Any], store: Any = None, include_settled: bool = False
) -> List[str]:
    """Every drift class, as a list of flags. Writes nothing, changes nothing.

    Drift is reported and never silently repaired: an automatic fix is the
    orchestrator overwriting the user's recorded intent on the strength of an
    inference. Propose, and let the user decide.
    """
    items = _items(data)
    # `--all` DISPLAYS settled items, so their file-local flags contradict
    # nothing and belong. Default False keeps the suppression the default view needs.
    flags = list(file_local_flags(
        data, include_settled=include_settled, subject_pool="items"
    ))
    flags.extend(_ref_flags(items))
    flags.extend(_plan_flags(items, data.get("project_path")))
    flags.extend(_memory_flags(items, store))
    flags.extend(_staleness_flags(items))
    flags.extend(_abandoned_flags(items, data.get("project_path")))
    return flags


def _ref_flags(items: List[Dict[str, Any]]) -> List[str]:
    """An item with NO ref is never flagged. An item whose ref will not resolve
    is flagged unverifiable. Absent and unresolvable are different states.

    SETTLED IS FILTERED ONCE, HERE, before the resolver is called — the same
    shape `_plan_flags` and `_memory_flags` use. A single `live` list feeds
    both the ref set and the emitting loop, so a settled item reaches
    neither. Filtering only the ref set would not be enough: two items can
    share one ref, so a live item keeps that ref in the query and the loop
    would still flag the settled one.
    The skip used to sit inside the per-item branch chain, after the ref had
    been queried and after the `unverifiable` branch had already fired — so a
    settled item could still flag, and `show` printed that flag while hiding
    the item itself, naming a title the same report had just omitted.
    Filtering the items means settled refs are not queried either, so the
    query stops growing with the project's whole history.

    A settled item in an all-settled backlog therefore produces no tracker
    call at all, and a broken tracker is silent. That is accepted: the states
    this filter suppresses are ones the user already decided not to act on, so
    nothing is lost that anyone would have used, and one live ref restores the
    signal.
    """
    live = [item for item in items if item.get("status") not in SETTLED]
    # STR ONLY: `sorted` over a mixed set raises TypeError, and `validate`
    # does not type-check `ref`, so a non-str ref is neither reported nor
    # survivable. Dropped here the same way `_memory_ids` drops a non-str id.
    refs = sorted({item["ref"] for item in live
                   if isinstance(item.get("ref"), str) and item["ref"]})
    if not refs:
        return []
    states = resolve_refs(refs)
    flags = []
    for item in live:
        ref = item.get("ref")
        if not ref:
            continue
        state = states.get(ref, {})
        if state.get("state") == "unverifiable":
            flags.append(
                f"{_label(item)}: ref {ref} is unverifiable "
                f"({state.get('reason', 'no reason given')})"
            )
        elif state.get("state") == "done":
            flags.append(
                f"{_label(item)}: ref {ref} is closed as completed but the item "
                f"is {item.get('status')!r} — probably done"
            )
        elif state.get("state") == "abandoned":
            flags.append(
                f"{_label(item)}: ref {ref} was closed WITHOUT the work being "
                f"done ({state.get('reason', 'abandoned')}) but the item is "
                f"{item.get('status')!r} — probably abandoned, NOT done"
            )
        elif state.get("state") == "closed":
            flags.append(
                f"{_label(item)}: ref {ref} is closed with no stated reason, so "
                f"whether the work was done is unknown; the item is "
                f"{item.get('status')!r}"
            )
    return flags


def _plan_flags(items: List[Dict[str, Any]], project_path: Any) -> List[str]:
    if not isinstance(project_path, str) or not project_path:
        return []
    root = Path(project_path)
    return [
        f"{_label(item)}: plan {item['plan']!r} does not resolve under {root}"
        for item in items
        # A SETTLED item's attachments are not drift. The work is not
        # happening, so a dead plan pointer is not something to chase.
        if item.get("status") not in SETTLED
        and item.get("plan") and not _plan_resolves(root, str(item["plan"]))
    ]


def _plan_resolves(root: Path, plan: str) -> bool:
    """The plan exists AND stays under the project root.

    validate() already refuses an ABSOLUTE plan, which leaves `../` — and a
    bare exists() on an escaped path answers questions about files outside the
    project.

    CONTAINMENT GATES THE ANSWER, NOT THE ACCESS. `resolve()` runs first and
    walks the path, statting components, so a traversal DOES touch the
    filesystem. What containment prevents is the RESULT ever depending on a
    file outside the root: it is evaluated before `.exists()`, and `and`
    short-circuits, so an escaping path reports as not resolving without that
    file being probed.
    """
    try:
        base = root.resolve()
        target = (base / plan).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return (target == base or base in target.parents) and target.exists()


def _memory_ids(item: Dict[str, Any]) -> List[str]:
    """This item's memory ids, or an empty list. THE ONLY way this module reads
    them.

    The same unchecked-field hazard as `_items`, one level down. `memory`
    reaches here from a file that PARSED but need not CONFORM, so it can be a
    truthy non-iterable — iterating one raises TypeError, not a
    BacklogFileError — or a truthy iterable that is not a list of ids: a str
    iterates CHARACTERS and a dict iterates KEYS, and both then read as
    perfectly good ids from a field that holds none.

    The str filter was on ONE of the two call sites and not the other, so the
    same file could contribute an id to the resolve batch and a flag naming a
    different one. One accessor makes the two agree.

    A non-list `memory` returns empty rather than flagging, because `validate`
    already reports it by name and a second report would say it twice.
    """
    memory = item.get("memory")
    if not isinstance(memory, list):
        return []
    return [identifier for identifier in memory if isinstance(identifier, str)]


def _memory_flags(
    items: List[Dict[str, Any]], store: Any = None
) -> List[str]:
    # Same rule as the plan pointers and the refs: a settled item's links are
    # not drift. Measured before any filter existed here — a dropped item and
    # a done item were both flagged for a dead memory id.
    #
    # ONE list, read by BOTH the id set and the loop. Filtering only the id
    # set would not be enough: two items can share a memory id, so a live
    # item keeps that id in `wanted` and the loop would still flag the
    # settled one against it.
    live = [item for item in items if item.get("status") not in SETTLED]
    wanted = sorted({
        identifier
        for item in live
        for identifier in _memory_ids(item)
    })
    if not wanted:
        return []
    records = resolve_memory_ids(wanted, store)
    flags = []
    for item in live:
        for identifier in _memory_ids(item):
            record = records.get(identifier)
            if record is _UNVERIFIABLE:
                flags.append(
                    f"{_label(item)}: memory id {identifier} is unverifiable "
                    f"(the memory store could not be opened) — NOT a missing record"
                )
                continue
            if record is None:
                flags.append(f"{_label(item)}: memory id {identifier} no longer resolves")
                continue
            if _is_newer(record.get("updated_at"), item.get("touched")):
                flags.append(
                    f"{_label(item)}: memory record {identifier} changed after it "
                    f"was linked ({record.get('updated_at')} > {item.get('touched')})"
                )
    return flags


def _staleness_flags(items: List[Dict[str, Any]]) -> List[str]:
    # DAYS ON BOTH SIDES, the sibling of the same fix in _is_newer. `touched` is
    # stored as a date, so it parses to midnight — the EARLIEST moment it could
    # mean — while the cutoff carried a real time. An item touched on the cutoff
    # day therefore compared older than it was and flagged at roughly 13 days
    # against a 14-day threshold.
    #
    # THE EDGE THIS PICKS: an item touched exactly _STALE_AFTER days ago does
    # NOT flag; one touched a day earlier does. Both arms share that edge, the
    # cutoff and the touched-parse — hoisted ONCE so the two flags cannot drift
    # apart, and disjoint BY STATUS (one status per item), so nothing dedups.
    # The active arm means the work stalled; the planned arm means nobody
    # ordered it — an unranked item is exactly the population `_rank_of` sends
    # to `inf`, and a RANKED planned item never flags because its rank is the
    # signal that someone ordered it deliberately.
    cutoff = (datetime.now(timezone.utc) - _STALE_AFTER).date()
    flags = []
    for item in items:
        status = item.get("status")
        touched = as_datetime(item.get("touched"))
        if touched is None or touched.date() >= cutoff:
            continue
        if status == "active":
            flags.append(
                f"{_label(item)}: active and untouched since {item.get('touched')}"
            )
        elif status == "planned" and not isinstance(item.get("rank"), (int, float)):
            flags.append(
                f"{_label(item)}: planned and unranked, "
                f"untouched since {item.get('touched')}"
            )
    return flags


def _abandoned_flags(
    items: List[Dict[str, Any]], project_path: Any = None
) -> List[str]:
    """An `active` item with no branch and no worktree carrying its ref.

    Only items that HAVE a ref are checked, so a ref-less item is never flagged
    here either.

    `project_path` anchors git to THIS project. Without it git read whichever
    repository the process CWD happened to sit in, so running from outside the
    project evaluated the heuristic against a different repo's branches and
    emitted wrong flags with nothing to show they were wrong. Threaded down
    from reconcile the way _plan_flags already receives it, rather than calling
    project_root(), which raises and would turn a drift report into a refusal.
    """
    # A RECENTLY TOUCHED ITEM IS NOT ABANDONED, whatever its branch is called.
    # The linkage below only sees a branch that carries the ref's digits, and
    # a branch named without its issue number reads as no branch at all. Where
    # that naming is common — and in some repositories it is the majority —
    # live work reports as abandoned. `_STALE_AFTER` already encodes this
    # notion of neglect; a second threshold would be a second thing to keep in
    # step. Filtered HERE rather than in the loop below so the early return
    # still spares the git calls when nothing qualifies.
    cutoff = (datetime.now(timezone.utc) - _STALE_AFTER).date()
    tracked = []
    for item in items:
        if item.get("status") != "active" or not item.get("ref"):
            continue
        touched = as_datetime(item.get("touched"))
        if touched is not None and touched.date() >= cutoff:
            continue
        tracked.append(item)
    if not tracked:
        return []
    # ponytail: the linkage is the ref's digits appearing in a branch or
    # worktree name, which is how this project names them. Add an explicit
    # `branch` field to the item shape if a backlog ever needs a firmer link.
    names = _branch_and_worktree_names(project_path)
    if names is None:
        return []
    flags = []
    for item in tracked:
        number = _issue_number(item.get("ref"))
        token = str(number) if number is not None else str(item.get("ref"))
        if not any(token in name for name in names):
            flags.append(
                f"{_label(item)}: active, but no branch or worktree carries "
                f"{item.get('ref')} — work may have been abandoned"
            )
    return flags


def _branch_and_worktree_names(project_path: Any = None) -> Optional[List[str]]:
    # ABSOLUTE STR ONLY. A truthy non-string fails safe already — `git -C 5`
    # exits 128 with empty stdout, so `_run_capture` returns None. The
    # dangerous value is a truthy RELATIVE string: `"."` resolves to the
    # process CWD and git reports THAT repository's branches, producing
    # abandoned-work flags computed against the wrong project. Anything
    # else degrades to None, which `_abandoned_flags` reads as no flags.
    if not (isinstance(project_path, str) and project_path
            and Path(project_path).is_absolute()):
        return None
    at = ["-C", project_path]
    branches = _run_capture(["git", *at, "branch", "--format=%(refname:short)"])
    worktrees = _run_capture(["git", *at, "worktree", "list", "--porcelain"])
    if branches is None and worktrees is None:
        return None
    # NAMES ONLY. The porcelain also carries `HEAD <sha>` — once PER WORKTREE —
    # and absolute paths, and the caller matches by SUBSTRING. So a ref's digits
    # could be satisfied by chance hex or by a parent directory, the token was
    # "found", and the abandoned-work flag this heuristic exists to emit was
    # silently suppressed. `detached`, `locked`, `bare` and `prunable` carry no
    # name and drop out with them.
    names = (branches or "").splitlines()
    for line in (worktrees or "").splitlines():
        if line.startswith("worktree "):
            names.append(Path(line[len("worktree "):]).name)
        elif line.startswith("branch "):
            names.append(line[len("branch "):].removeprefix("refs/heads/"))
    return names


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_examples(*lines: str) -> str:
    return "Examples:\n" + "\n".join(f"  {line}" for line in lines)


class _UsageErrorParser(argparse.ArgumentParser):
    """Exits `_EXIT_USAGE` on a malformed command line, not argparse's default 2.

    Subclassed rather than caught in `main()` so the code is carried by the
    parser itself: `add_subparsers` propagates this class to every subparser,
    and a caller holding `build_parser()` gets it too.
    """

    _teach_example = ""

    def error(self, message):
        extra = (
            f"\n\nExamples:\n  {self._teach_example}\n"
            if self._teach_example
            else "\n"
        )
        self.exit(
            _EXIT_USAGE,
            f"{self.format_usage()}{self.prog}: error: {message}{extra}",
        )


def build_parser() -> argparse.ArgumentParser:
    """Argument grammar, separated so it is testable without running anything."""
    prog = sys.argv[0]
    fmt = argparse.RawDescriptionHelpFormatter
    # Interpreter-prefixed so a pasted example executes verbatim: the script
    # has no shebang or exec bit, so the bare path is not shell-executable.
    show_ex = f'python3 "{prog}" show'
    archive_ex = f'python3 "{prog}" archive item-id'
    add_ex = f'python3 "{prog}" add "title"'
    set_ex = f'python3 "{prog}" set item-id --status done'
    repair_ex = f'python3 "{prog}" repair'

    parser = _UsageErrorParser(
        prog=prog,
        description="PACT cross-session backlog — the user's ordered intent, "
        "reconciled against git, the tracker and pact-memory.",
        formatter_class=fmt,
        epilog=_cli_examples(show_ex, archive_ex, add_ex, set_ex, repair_ex),
    )
    parser._teach_example = show_ex
    parser.add_argument(
        "--backlog-dir",
        help="Override the store directory. Testing seam; omit in normal use.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    show = sub.add_parser(
        "show",
        help="Report every item with its drift flags",
        formatter_class=fmt,
        epilog=_cli_examples(show_ex),
    )
    show._teach_example = show_ex
    view = show.add_mutually_exclusive_group()
    view.add_argument(
        "--all",
        action="store_true",
        help="Show every item, including done and dropped",
    )
    view.add_argument(
        "--archived",
        action="store_true",
        help="Show the archive instead of the live list",
    )
    show.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Skip the tracker, pact-memory and git checks",
    )

    archive = sub.add_parser(
        "archive",
        help="Move settled items into the archive",
        formatter_class=fmt,
        epilog=_cli_examples(archive_ex),
    )
    archive._teach_example = archive_ex
    archive.add_argument("item_ids", nargs="+")

    add = sub.add_parser(
        "add",
        help="Add an item",
        formatter_class=fmt,
        epilog=_cli_examples(add_ex),
    )
    add._teach_example = add_ex
    add.add_argument("title")
    _add_item_arguments(add)

    update = sub.add_parser(
        "set",
        help="Change fields on an existing item",
        formatter_class=fmt,
        epilog=_cli_examples(set_ex),
    )
    update._teach_example = set_ex
    update.add_argument("item_id")
    _add_item_arguments(update)
    update.add_argument("--title")

    fix = sub.add_parser(
        "repair",
        help="Move a corrupt backlog aside and start fresh",
        formatter_class=fmt,
        epilog=_cli_examples(repair_ex),
    )
    fix._teach_example = repair_ex
    fix.add_argument(
        "--force",
        action="store_true",
        help="Move the file aside even when it is readable, or could not be read",
    )
    return parser


def _add_item_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--status", choices=sorted(STATUSES))
    parser.add_argument("--rank", type=int)
    parser.add_argument("--ref", help="Opaque tracker reference, or 'none' to clear")
    parser.add_argument("--plan", help="Repo-relative path to a plan document")
    parser.add_argument(
        "--note",
        help="One line in the orchestrator's voice, never the user's first "
             f"person. Longer than {NOTE_MAX_CHARS} characters is refused, not "
             "shortened.",
    )
    parser.add_argument(
        "--memory",
        action="append",
        help=f"pact-memory record id; repeatable up to {MEMORY_MAX_IDS}",
    )
    for field in ("blocked-by", "batch-with", "exclusive-with"):
        parser.add_argument(
            f"--{field}",
            action="append",
            help=f"Item id for {field}; the single value 'none' clears it",
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    backlog_dir = Path(args.backlog_dir) if args.backlog_dir else None

    try:
        path = store_path(backlog_dir)
        if args.command == "repair":
            return _handle_repair(path, args.force)
        return _handle_write_or_show(args, path)
    except BacklogFileError as exc:
        # THE ADVICE DEPENDS ON THE SUBCLASS, because `repair` REFUSES an
        # unreadable file without --force. Promising a repair that declines is
        # worse than saying nothing: the user runs it, gets a refusal, and
        # learns only that two of our messages disagree. Exit code stays 3 for both —
        # the distinction the caller needs is already in `exc`, so a second
        # code would add a branch every consumer must learn for no new fact.
        advice = (
            "Nothing was changed. `repair` will REFUSE this — it moves aside "
            "files it can read and found corrupt, and it never read this one. "
            "Fix the access problem the message names, or pass --force to move "
            "it aside unread; it is kept, not deleted."
            if isinstance(exc, BacklogUnreadableError)
            else "Nothing was changed. Run `repair` to move it aside and rebuild."
        )
        print(f"backlog is unreadable: {exc}\n{advice}", file=sys.stderr)
        return _EXIT_UNREADABLE
    except BacklogWriteError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return _EXIT_REFUSED


def _handle_repair(path: Path, force: bool = False) -> int:
    if not path.exists():
        print(f"nothing to repair: {path} does not exist")
        return _EXIT_OK
    aside, message = repair(path, force)
    print(message)
    print(f"a fresh backlog will be created at {path} on the next write")
    return _EXIT_OK


def _handle_write_or_show(args: argparse.Namespace, path: Path) -> int:
    data = load_or_create(path)
    # Schema problems are file-local, so they show even with --no-reconcile.
    schema = [f"schema: {problem}" for problem in validate(data)]

    if args.command == "show":
        if args.archived:
            # The external reconcile arms filter settled items BY CONSTRUCTION,
            # so they could only ever report on live items — the wrong subject
            # for this view — while still paying the tracker subprocess.
            # Skipping them is deliberate, not a shortcut.
            flags = file_local_flags(data, include_settled=True, subject_pool="archive")
            print(_render(data, schema + flags, show_archived=True))
        else:
            flags = [] if args.no_reconcile else reconcile(data, include_settled=args.all)
            print(_render(data, schema + flags, show_all=args.all))
        return _EXIT_OK

    if args.command == "add":
        item = add_item(data, args.title, **_field_updates(args))
        done_echo = [f"add ok: {item['id']} {item['title']}"]
    elif args.command == "archive":
        moved = archive_items(data, args.item_ids)
        done_echo = [f"archive ok: {item['id']} {item['title']}" for item in moved]
    else:
        item = find_item(data, args.item_id)
        if item is None:
            if any(i.get("id") == args.item_id for i in _archived(data)):
                print(
                    f"refused: item {args.item_id!r} is archived. "
                    f"Nothing was written.",
                    file=sys.stderr,
                )
            else:
                print(f"refused: no item with id {args.item_id!r}", file=sys.stderr)
            return _EXIT_REFUSED
        update_item(item, title=args.title, **_field_updates(args))
        done_echo = [f"set ok: {item['id']} {item['title']}"]

    problems = save(data, path)
    if problems:
        print("refused, nothing written:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return _EXIT_REFUSED

    for line in done_echo:
        print(line)
    return _EXIT_OK


def _field_updates(args: argparse.Namespace) -> Dict[str, Any]:
    """Namespace to item fields. `none` clears; an unpassed flag leaves the
    field alone. The clear rides _CLEAR rather than None, because None is
    already how an unpassed flag reaches _apply_fields.

    A LIST FIELD CLEARS THE SAME WAY, because `action="append"` cannot produce
    an empty list and omitting the flag means "leave it" — so without this,
    removing a dependency meant hand-editing a file whose whole design says
    the user never hand-edits it.

    THE REF IS NORMALISED HERE, the single choke point both `add` and `set`
    route through: what `_issue_number` resolves collapses to the bare number
    (so `#1602` and a GitHub URL store as `1602`), and everything else —
    Linear keys, `owner/repo#N`, arbitrary strings — passes byte-identical,
    because the qualifier IS the meaning. Write-time only; existing stores
    normalise on their next touch, never by migration.
    """
    ref = getattr(args, "ref", None)
    number = _issue_number(ref)
    normalised_ref = None if ref is None else (
        _CLEAR if ref.lower() == "none" else
        str(number) if number is not None else ref
    )
    return {
        "status": getattr(args, "status", None),
        "rank": getattr(args, "rank", None),
        "ref": normalised_ref,
        "plan": getattr(args, "plan", None),
        "note": getattr(args, "note", None),
        "memory": getattr(args, "memory", None),
        "blocked_by": _list_field(args, "blocked_by"),
        "batch_with": _list_field(args, "batch_with"),
        "exclusive_with": _list_field(args, "exclusive_with"),
    }


def _list_field(args: argparse.Namespace, name: str) -> Any:
    """A repeated flag's value: _CLEAR for `none`, the list otherwise.

    MIXING `none` WITH AN ID IS REFUSED rather than resolved. `--blocked-by
    none --blocked-by x1` reads as "clear then add" to one person and "add,
    and one of them is literally none" to another, and either silently does
    something the other did not ask for. Refusing costs one retype; guessing
    costs a relationship the user did not mean to change.
    """
    values = getattr(args, name, None)
    if values is None:
        return None
    clears = [v for v in values if v.lower() == "none"]
    if clears and len(values) > 1:
        raise BacklogWriteError(
            f"--{name.replace('_', '-')} takes ids OR the single value "
            f"'none' to clear, not both. Nothing was written."
        )
    return _CLEAR if clears else values


def _render(
    data: Dict[str, Any],
    flags: Sequence[str],
    show_all: bool = False,
    show_archived: bool = False,
) -> str:
    """The `show` report. BOUNDS THE REPORT, NOT THE STORE.

    SETTLED items are omitted by default because `show` is read every time and
    its readability is what degrades, while keeping every item costs about a
    millisecond. Hiding `dropped` is not a bonus alongside hiding `done` — it
    is the half that makes the status do its job, because the case for adding
    it was that a mistaken item clutters the ranked list forever, and that
    stays true if `show` keeps displaying it.

    THE HIDDEN COUNT IS NOT OPTIONAL: an omission the reader cannot see is a
    lie by another route, the same defect as a message naming a cause it did
    not establish. It names the categories SEPARATELY rather than summing —
    four done items is a working project and four dropped ones is a project
    that keeps abandoning things, and those want different responses.

    `show_archived` renders the ARCHIVE instead of the live list — the same
    comparator and line format over `_archived(data)`, with the header naming
    the view. The live views carry the archived-count line whenever the
    archive is non-empty, so the archive is named on every report: an archive
    nothing mentions is a file nobody reads, and its rot is silent.
    """
    header = f"{data.get('project')} — {data.get('project_path')}"
    if show_archived:
        header += " (archive)"
    lines = [header]
    shown = _archived(data) if show_archived else _items(data)
    hidden = {}
    if not show_all and not show_archived:
        hidden = {
            status: sum(1 for i in shown if i.get("status") == status)
            for status in sorted(SETTLED)
        }
        hidden = {status: n for status, n in hidden.items() if n}
        shown = [i for i in shown if i.get("status") not in SETTLED]
    items = sorted(
        shown,
        # Four keys, not three: SETTLED items sort last regardless of rank,
        # because rank orders WORK TO DO and a settled item has none. Without
        # the first key a dropped item with rank 1 outranks a planned item
        # with rank 2. The fourth decides a rank tie by AGE — date-only ISO
        # strings sort lexicographically, which is chronologically — so the
        # oldest item wins rather than the tie falling silently through to
        # file order. A missing or NON-STRING `added` keys as "" and sorts
        # FIRST in its tie group: the loud choice, surfacing a non-conforming
        # item instead of burying it — and the isinstance test, not `or ""`,
        # because a truthy non-string (an int from a hand-edit) would
        # TypeError the comparison against a string sibling. Beyond equal
        # full keys, stable file order stands.
        key=lambda item: (
            item.get("status") in SETTLED,
            item.get("status") != "active",
            _rank_of(item),
            item.get("added") if isinstance(item.get("added"), str) else "",
        ),
    )
    # The id is emitted for the AGENT, which needs it as the argument to `set`.
    # `add` echoes it once at creation, and that echo is gone by the next
    # session — so without it here a later session, which is the only kind this
    # backlog exists for, has no route to any id at all. The rule that it is
    # never cited to the USER lives in commands/next.md and is unaffected.
    for item in items:
        lines.append(
            f"  [{item.get('status')}] {item.get('title')}"
            + (f"  ref={item.get('ref')}" if item.get("ref") else "")
            + (f"  plan={item.get('plan')}" if item.get("plan") else "")
            + f"  [id={item.get('id')}]"
        )
        if item.get("note"):
            lines.append(f"      note: {item['note']}")
    if not items:
        lines.append("  (no items)")
    if hidden:
        # A category absent at zero, and the whole line absent when nothing is
        # hidden — the one omission here that is honest, because the reader is
        # missing nothing.
        # NO NOUN: "1 done and 1 dropped items" is wrong and "item" is wrong
        # at two categories of one each, so the count word is the trap. Naming
        # the statuses alone has nothing to agree with.
        parts = " and ".join(f"{n} {status}" for status, n in hidden.items())
        lines.append(f"  {parts} hidden (--all to show)")
    if not show_archived:
        # The archive's permanent presence on the read path: counted and its
        # flag advertised in BOTH live views whenever it is non-empty, absent
        # entirely when empty. Same no-noun trick as the hidden line.
        archived = _archived(data)
        if archived:
            lines.append(f"  {len(archived)} archived (--archived to show)")
    lines.append("")
    lines.append(f"{len(flags)} flag(s):" if flags else "no drift found")
    lines.extend(f"  {flag}" for flag in flags)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_text(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _is_newer(candidate: Any, baseline: Any) -> bool:
    """Strictly LATER CALENDAR DAY, not later instant.

    `touched` is stored as a date, so it parses to midnight UTC, while a memory
    record's `updated_at` carries a real time. Comparing them as instants made
    ANY record updated later the same day compare greater — so an item linked
    this morning reported "the record changed after it was linked" on its very
    next reconcile. The natural workflow makes that the common case rather than
    the edge one: you link a record and keep working on the same material, so
    the flag fired on exactly the items under active work.

    Comparing days costs the within-day signal, which is the cheapest one to
    lose: a same-day change is one the user was present for, and a change on any
    later day still flags. The alternative was storing `touched` at full
    precision, which changes the stored shape and would have to take `added`
    with it for consistency — a schema change to recover a signal nobody needs.
    """
    left, right = as_datetime(candidate), as_datetime(baseline)
    return left is not None and right is not None and left.date() > right.date()


def _rank_of(item: Dict[str, Any]) -> float:
    rank = item.get("rank")
    return float(rank) if isinstance(rank, (int, float)) else float("inf")


def _label(item: Dict[str, Any]) -> str:
    return str(item.get("title") or item.get("id") or "?")


if __name__ == "__main__":
    raise SystemExit(main())
