"""
Location: pact-plugin/hooks/shared/backlog_store.py
Summary: READ side of the cross-session backlog — schema rules, file load,
         project resolution by path containment, the session-start block, and
         the total helper session_block() that converts every failure into a
         returned value.
Used by: hooks/session_init.py (session_block only) and hooks/shared/backlog.py
         (validate/read_json/find_for/file_local_flags, reused by the write side).

WHY THIS MODULE IS SEPARATE FROM backlog.py: the session-start read path must
issue no subprocess and no network call, and must not carry pact-memory in its
import closure. Keeping the two sides in one module and deferring the heavy
imports inside functions would hold today and rot tomorrow — a later edit
hoisting an import to module scope breaks the constraint with nothing going
red. A separate module makes the constraint structural. The dependency arrow is
one-way: backlog.py imports this module; this module imports backlog.py never.

NOTHING HERE MAY IMPORT pact-memory, subprocess, or any network client.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from .claude_md_manager import _stat_if_present
from .failure_cause import failure_cause
from .paths import get_backlog_dir

# Schema constants, shared with the write side so a writer cannot emit what a
# reader rejects.
SCHEMA_VERSION = 1
NOTE_MAX_CHARS = 500
MEMORY_MAX_IDS = 5
STATUSES = frozenset({"planned", "active", "blocked", "done", "dropped"})

# `dropped` says the user DECIDED NOT TO DO THIS. It is not `done` (that would
# be false) and not `planned` (that clutters the ranked list forever). The id
# stays alive, so nothing that points at the item dangles — which is the whole
# reason this exists instead of a delete.
# SETTLED means "no more work will happen here", which is what every consumer
# that used to test `== "done"` actually meant.
SETTLED = frozenset({"done", "dropped"})
RELATIONAL_FIELDS = ("blocked_by", "batch_with", "exclusive_with")

# Item ids are exactly four lowercase hex characters, generated at creation and
# never reused, so the relational fields can point at something stable when a
# title is reworded.
# `\Z` rather than `$`: Python's `$` also matches before a trailing newline, so
# `$` would accept "ab12\n" from a hand-edited file.
_ITEM_ID = re.compile(r"^[0-9a-f]{4}\Z")

# How many planned items the session-start block names by title.
_BLOCK_PLANNED_LIMIT = 3

# How long a rendered title may be. A JUDGEMENT, not a measurement: its only
# anchor is coherence with NOTE_MAX_CHARS, since a title is a label rather than
# a sentence and so must bound tighter than a note. Anything in 80-150 is
# defensible; move it without re-deriving why it exists.
_TITLE_DISPLAY_MAX = 120

# Sort key for an item with no usable rank: rank orders planned items and is
# neither contiguous nor complete, so an unranked item sorts last rather than
# raising against an int.
_UNRANKED = float("inf")


class BacklogNotice(NamedTuple):
    """What session_init receives. Two independent fields, never one string
    plus a classifier.

    context: text for additionalContext; "" means nothing to say.
    alert:   text for systemMessage;     "" means nothing to say.

    The routing decision is already made by which field carries the value, so
    a caller has no text to inspect and the substring-matching router the
    design forbids is unconstructible rather than merely prohibited.
    """

    context: str
    alert: str


class BacklogFileError(Exception):
    """A specific backlog file could not be read, parsed, or validated.

    Carries the offending path so every loud message can name the file the
    reader must repair, whichever layer raised.
    """

    def __init__(self, path: Path, problem: str) -> None:
        super().__init__(f"{path}: {problem}")
        self.path = path
        self.problem = problem


class BacklogUnreadableError(BacklogFileError):
    """The BYTES could not be read — permission, a directory at the path, a
    lock, a transient IO failure. NOT a statement about the contents.

    Separate from its parent because the two answer different questions and
    one caller acts on the difference: `repair` MOVES the user's file, and
    "I could not read it" is not evidence of corruption. Collapsing them let a
    healthy backlog behind a permission bit present exactly as a corrupt one.

    A subclass rather than a sibling so every existing `except
    BacklogFileError` keeps catching both; only a caller that needs the
    distinction has to know it exists.
    """


def validate(obj: Any) -> List[str]:
    """Return a list of human-readable problems. An empty list means valid.

    Never raises, never mutates, never truncates. One validator serves both
    sides, so a writer cannot emit a file its own reader rejects.

    Dangling ids in the relational fields are deliberately NOT problems. They
    are reconciliation flags: a backlog carrying one is a backlog to report on,
    not a corrupt file to refuse.
    """
    problems: List[str] = []

    if not isinstance(obj, dict):
        return [f"top level is {type(obj).__name__}, expected an object"]

    if obj.get("version") != SCHEMA_VERSION:
        problems.append(
            f"version is {obj.get('version')!r}, this reader knows {SCHEMA_VERSION}"
        )

    for key in ("project", "project_path"):
        value = obj.get(key)
        if not isinstance(value, str) or not value:
            problems.append(f"{key} is {value!r}, expected a non-empty string")

    # `roots` is REQUIRED, with no optional-with-fallback branch. A fallback
    # would have to guess the missing checkouts, and the only guess available
    # is containment — the defect this field exists to remove. The writer
    # always emits at least project_root(), so an absent or empty roots means
    # the file was not written by this writer.
    roots = obj.get("roots")
    if not isinstance(roots, list) or not roots:
        problems.append(f"roots is {roots!r}, expected a non-empty list of paths")
    elif not all(isinstance(r, str) and r for r in roots):
        problems.append("roots holds a non-string or empty entry")
    elif not all(Path(r).is_absolute() for r in roots):
        # ABSOLUTE, for the same reason `plan` must be RELATIVE: a stored path
        # whose meaning depends on where the reader stands is not an identity.
        # The writer only ever records resolved absolute paths, so this can
        # arise from a hand-edited or corrupted file, which is the population
        # the read path exists to survive.
        #
        # THE RULE LIVES IN TWO PLACES ON PURPOSE, AND NEITHER IS REDUNDANT.
        # `_scan` DECLINES to match on a non-absolute root, which is what stops
        # a stored "." claiming whatever session happens to open in the
        # directory it resolves against. This rule REPORTS that root to the
        # user, which is what turns a file that silently stopped matching into
        # one whose problem is named. Delete this and the diagnosis goes;
        # delete the filter and the claim comes back.
        problems.append(f"roots holds a relative path: {roots!r}")

    items = obj.get("items")
    if not isinstance(items, list):
        return problems + [f"items is {type(items).__name__}, expected a list"]

    seen_ids = set()
    for index, item in enumerate(items):
        problems.extend(_validate_item(item, index, seen_ids))

    # `archive` is OPTIONAL — membership, not `.get()`, separates an absent key
    # (exactly the pre-archive shape, always conforming) from an explicit null
    # (non-conforming), the same distinction `add_item` draws on `items`.
    # There is deliberately NO unknown-top-level-key rule: an older reader must
    # keep validating a newer file clean, so additive keys pass unnamed and the
    # archive carries its own rules instead of a closed key space.
    if "archive" in obj:
        archive = obj["archive"]
        if not isinstance(archive, list):
            return problems + [f"archive is {type(archive).__name__}, expected a list"]
        for index, item in enumerate(archive):
            # THE SAME seen_ids threads both lists, so a hand-edit that leaves
            # an id in both is a named duplicate rather than a silent shadow.
            problems.extend(_validate_item(item, index, seen_ids, kind="archive item"))
            if isinstance(item, dict) and item.get("status") not in SETTLED:
                item_id = item.get("id")
                label = (
                    f"archive item {item_id!r}"
                    if isinstance(item_id, str)
                    else f"archive item {index}"
                )
                problems.append(
                    f"{label}: status is {item.get('status')!r}, "
                    f"the archive holds settled items only"
                )

    return problems


def _validate_item(
    item: Any, index: int, seen_ids: set, kind: str = "item"
) -> List[str]:
    """Schema rules for one item. `seen_ids` accumulates across the list so a
    duplicate id is reported on its second occurrence. `kind` is the label's
    noun — "item" or "archive item" — so a problem names WHICH list holds it."""
    if not isinstance(item, dict):
        return [f"{kind} {index} is {type(item).__name__}, expected an object"]

    problems: List[str] = []
    item_id = item.get("id")
    label = f"{kind} {item_id!r}" if isinstance(item_id, str) else f"{kind} {index}"

    if not isinstance(item_id, str) or not _ITEM_ID.match(item_id):
        problems.append(f"{label}: id is {item_id!r}, expected four hex characters")
    elif item_id in seen_ids:
        problems.append(f"{label}: id is a duplicate")
    else:
        seen_ids.add(item_id)

    status = item.get("status")
    if status not in STATUSES:
        problems.append(
            f"{label}: status is {status!r}, expected one of {sorted(STATUSES)}"
        )

    # Type only, deliberately no length rule: the length bound lives on the
    # RENDER path in _title. A rule here would produce a flag and nothing more,
    # since a validation failure no longer suppresses the block.
    title = item.get("title")
    if title is not None and not isinstance(title, str):
        problems.append(f"{label}: title is {type(title).__name__}, expected a string")

    note = item.get("note")
    if note is not None:
        if not isinstance(note, str):
            problems.append(f"{label}: note is {type(note).__name__}, expected a string")
        elif len(note) > NOTE_MAX_CHARS:
            problems.append(
                f"{label}: note is {len(note)} characters, limit is {NOTE_MAX_CHARS}"
            )

    memory = item.get("memory")
    if memory is not None:
        if not isinstance(memory, list):
            problems.append(
                f"{label}: memory is {type(memory).__name__}, expected a list"
            )
        elif len(memory) > MEMORY_MAX_IDS:
            problems.append(
                f"{label}: memory holds {len(memory)} ids, limit is {MEMORY_MAX_IDS}"
            )

    # `ref` and `rank` are the two USER-SETTABLE fields that carried no type
    # rule. Both degraded quietly: a non-string ref is dropped from the write
    # side's ref query so the item gets no tracker check, and a non-numeric
    # rank falls to _rank_key's isinstance guard and sorts last. Neither said
    # anything. Every other field the user can set already reports, so these
    # two were the outliers rather than the precedent.
    ref = item.get("ref")
    if ref is not None and not isinstance(ref, str):
        problems.append(f"{label}: ref is {type(ref).__name__}, expected a string")

    rank = item.get("rank")
    if rank is not None and not isinstance(rank, (int, float)):
        problems.append(f"{label}: rank is {type(rank).__name__}, expected a number")

    plan = item.get("plan")
    if plan is not None:
        if not isinstance(plan, str):
            problems.append(f"{label}: plan is {type(plan).__name__}, expected a string")
        elif Path(plan).is_absolute():
            problems.append(
                f"{label}: plan {plan!r} is absolute, expected a repo-relative path"
            )

    # `added` and `touched` are dates, tested through THE SAME as_datetime the
    # staleness checks use, so validate cannot accept a value the reader will
    # not take. That helper returns None both for a non-string AND for an
    # unparseable string, and a None there silently disables the staleness
    # check — so a type-only rule would close half the hole, leaving
    # `touched: "banana"` exactly as quiet as `touched: 5`. A bare date and a
    # full timestamp both pass, because both parse there.
    for field in ("added", "touched"):
        value = item.get(field)
        if value is not None and as_datetime(value) is None:
            problems.append(
                f"{label}: {field} is {value!r}, expected a date like 2026-01-31"
            )

    for field in RELATIONAL_FIELDS:
        value = item.get(field)
        if value is not None and not isinstance(value, list):
            problems.append(
                f"{label}: {field} is {type(value).__name__}, expected a list"
            )

    return problems


def as_datetime(value: Any) -> Optional[datetime]:
    """Parse a stored date or timestamp, or None when it is unusable.

    Dates are stored as YYYY-MM-DD and memory timestamps carry a time and a
    zone, so both spellings reach this helper.

    PUBLIC AND LIVING HERE so validate() and the write side's staleness checks
    share ONE definition of a usable date. Held separately they drifted by
    construction: a validator that accepts what its consumer rejects passes a
    value the reader cannot use, and one that rejects what its consumer accepts
    refuses a file that would have read fine.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_json(path: Path) -> Dict[str, Any]:
    """Read and parse one backlog file, with no schema check.

    Raises BacklogFileError naming the path on an unreadable or unparseable
    file. Used by find_for, which needs only the stored project_path and must
    not reject a file for schema reasons before deciding whether it is even
    this project's file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # We READ the bytes and they are not text, so this is corruption rather
        # than an access failure. Caught explicitly because it is a ValueError
        # raised by the READ call: it is not an OSError, so the OSError clause
        # does not see it and it would escape read_json uncaught. Measured —
        # it crashed the CLI with a traceback before this clause existed.
        raise BacklogFileError(path, f"unparseable ({exc})") from exc
    except OSError as exc:
        raise BacklogUnreadableError(path, f"could not be read ({exc})") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise BacklogFileError(path, f"unparseable ({exc})") from exc
    # Valid JSON is not necessarily an object. Without this, a top-level list,
    # number or string reaches `.get()` in the scan and raises AttributeError,
    # which is NOT a BacklogFileError and so escapes the per-file catch there
    # into the generic handler — producing a message that names no file, where
    # the designed unreadable path names every offending one. One check routes
    # every caller, read side and write side alike, into the named-path path.
    if not isinstance(data, dict):
        raise BacklogFileError(
            path, f"top level is {type(data).__name__}, expected an object"
        )
    return data


# THERE IS DELIBERATELY NO read-parse-AND-VALIDATE HELPER HERE. One existed and
# was the single site that turned a validation failure into a BacklogFileError
# — the exception the CLI maps to its unreadable exit code, which the command
# file routes to `repair`, which MOVES THE USER'S FILE. Every caller had already
# moved to read_json + validate so the two dispositions stay separate, leaving
# it dead; deleting it makes "a merely non-conforming file cannot reach repair"
# a property of the code rather than a fact about today's callers.


def find_for(project_dir: str, backlog_dir: Path) -> Tuple[Optional[Path], List[Path]]:
    """The best match (or None) AND the files that could not be read.

    Thin face over _scan for callers that do not need the match count.
    """
    match, unreadable, _, _ = _scan(project_dir, backlog_dir)
    return match, unreadable


def _scan(
    project_dir: str, backlog_dir: Path
) -> Tuple[Optional[Path], List[Path], List[Path], int]:
    """Locate this project's backlog by EXACT MEMBERSHIP in the stored `roots`.

    Returns the best match (or None), the files that could not be read,
    EVERY file that claimed this project best first, and HOW MANY share the
    newest stamp. That count is 1 exactly when recency chose the winner; above
    1 the stamps are tied and something else did, which the caller must not
    describe as recency.

    `roots` holds every checkout of the project — the main one and each
    worktree — as the writer resolved them. A file claims this session when
    `project_dir` IS one of them, or when the checkout ENCLOSING it is —
    `_enclosing_checkout` walks up to the nearest `.git`, so a session opened
    in a subdirectory of a recorded checkout matches, and one opened in a
    nested project that has its own `.git` does not.

    CONTAINMENT IS THE DEFECT, NOT THE FIX, AND "or under" IS CONTAINMENT
    RENAMED. A git repo at any ANCESTOR of another project claimed that
    project's sessions, which a dotfiles repo at $HOME makes the ordinary case
    rather than the exotic one: a store recording <tmp>/home surfaced its own
    active item into a session at <tmp>/home/Sites/unrelated-project. Exact
    membership declines that and keeps everything containment existed for,
    because a worktree path and the main root are BOTH `worktree ` lines and
    therefore both members.

    BOTH SIDES ARE RESOLVED before comparing: the writer stores paths that have
    been through `.resolve()`, so a lexical comparison against an unresolved
    `project_dir` compares different things the moment either crosses a
    symlink. On macOS `/var` is a symlink to `/private/var`, which makes that
    the default under any temporary directory. Resolution is a stat-level call
    — still no subprocess and no network.

    A CHECKOUT CREATED SINCE THE LAST WRITE is absent from `roots`, so its
    session finds no match and goes LOUD through the existing resolution-failure
    branch. That is the design working: the remedy that message names is itself
    a write, which refreshes `roots`. There is deliberately NO containment
    fallback for this case — it would reintroduce the defect verbatim.

    A project RENAME puts two files in the directory carrying the same roots —
    the old name and the new one — because the name derivation changed while
    the checkouts did not. The newer `updated` wins rather than whichever name
    sorts first, and the duplication is reported.

    AN UNREADABLE FILE NEVER ABORTS THE SCAN. The store is one flat directory
    shared by every project, so raising on the first bad file would let one
    project's corruption suppress another project's healthy block, and the
    chance of at least one bad file grows with every project the user touches.
    A file cannot be attributed to a project without parsing it, so ownership
    is not the rule: unreadable files are collected and returned, and the
    caller decides whether they are the loud reason or a note beside a block.
    Matching on the FILENAME would sidestep the parse, and it is shut for a
    second reason — it would make the read path derive a project name, and
    deriving no name at all is what removes the hazard of a duplicate
    derivation entirely.

    A directory-level failure still raises, so this never swallows an error
    that genuinely aborts the scan.
    """
    target = _resolved(Path(project_dir))
    # Computed ONCE. It depends only on `target`, so re-walking it per file was
    # one filesystem walk per backlog in the store for an unchanging answer.
    # A walk that could not tell (an unreadable level) DECLINES the enclosing
    # rung: exact membership still matches, and no match reaches the loud
    # resolution-failure branch in session_block, which names the level.
    try:
        enclosing = _enclosing_checkout(target)
    except OSError:
        enclosing = None
    found = []
    unreadable: List[Path] = []

    for path in sorted(backlog_dir.glob("*.json")):
        try:
            data = read_json(path)
        except BacklogFileError:
            unreadable.append(path)
            continue
        roots = data.get("roots")
        if not isinstance(roots, list):
            continue
        # NON-ABSOLUTE ROOTS ARE FILTERED HERE, NOT LEFT TO validate().
        # validate() runs on the file ALREADY SELECTED, so the rule was
        # enforced downstream of the decision it governs: a stored `"."`
        # resolved against the process working directory and CLAIMED whatever
        # session happened to open there, rendering another project's items
        # with a conformance note beside them.
        #
        # This is not the silent-skip that keeping the rule in validate() was
        # meant to avoid. Skipping a legitimate root would hide a real match;
        # a relative root IDENTIFIES NOTHING, because its meaning depends on
        # where the reader stands, so declining to match on it withholds no
        # identity. A file whose roots are ALL relative therefore never
        # matches — and it is not invisible: no match means the existing loud
        # resolution failure fires, and `/PACT:next` still finds the file by
        # name and reports the relative root as a schema problem.
        #
        # A malformed entry yields an empty set: no match, same loud path. No
        # new raise site, so the totality boundary does not move.
        recorded = {
            _resolved(Path(r))
            for r in roots
            if isinstance(r, str) and Path(r).is_absolute()
        }
        if target not in recorded and enclosing not in recorded:
            continue
        found.append((str(data.get("updated") or ""), path))

    # KEY ON THE STAMP ALONE. Without it the tuple comparison falls through
    # to element 2 and the PATH decides an equal-stamp tie — the criterion
    # forbids that in the same sentence as the path-LENGTH tie-break this
    # sort dropped when the tuple shrank from three elements to two.
    found.sort(key=lambda entry: entry[0], reverse=True)
    matched = [path for _, path in found]
    # COMPARING THE TOP TWO IS SUFFICIENT FOR ANY N because the list is sorted
    # descending — a consequence of the sort, not a shortcut around it.
    tied = sum(1 for stamp, _ in found if stamp == found[0][0])
    return (matched[0] if matched else None), unreadable, matched, tied


def _enclosing_checkout(path: Path) -> Optional[Path]:
    """The nearest ancestor holding a `.git`, or None.

    THIS IS NOT CONTAINMENT RE-ADMITTED, and that is the only question worth
    asking about it. A genuine subdirectory of a checkout and an unrelated
    project nested under one are INDISTINGUISHABLE BY PATH SHAPE — both are "a
    directory under a recorded root" — which is exactly why containment cannot
    come back in any form, including one wearing a different noun.

    `.git` is the discriminator the path itself does not carry. An unrelated
    project HAS one, so its enclosing checkout is ITSELF and is not in `roots`,
    and it is declined. A genuine subdirectory has none, so the walk continues
    up to the checkout that DOES, which IS in `roots`. Same signal git uses,
    read by stat rather than by subprocess.

    The rung exists because CLAUDE_PROJECT_DIR really does point at in-repo
    subdirectories: measured across recorded session contexts, one names a
    path ending `/pact-plugin`, which is TRACKED repo content and so could
    never have been a worktree root. Exact membership alone declined those
    sessions, and the loud state could not self-heal, because the porcelain
    that refreshes `roots` never emits a subdirectory.

    RAISES OSError at a level it cannot examine (EACCES, EPERM), and does not
    climb past it: skipping that level would claim a checkout further up that
    this path may not belong to. `Path.exists()` cannot decide this, because it
    raised on 3.9-3.13 and returned False on 3.14, which climbed. Both callers
    catch the raise: `_scan` declines the enclosing rung and
    `backlog.project_root` refuses the write.
    """
    for directory in [path, *path.parents]:
        if _stat_if_present(directory / ".git") is not None:
            return directory
    return None


def _items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The item dicts, or NONE when `items` is not a list.

    `data.get("items") or []` returns THE VALUE when it is truthy, so a
    non-list `items` reaches the `for` and raises TypeError on a file that
    parses. Both readers of `items` run on data validate() has already
    rejected, where fields arrive unchecked, so the type is tested rather than
    assumed — the docstrings said so and the code did not.

    The guard belongs here rather than at either call site: the READ path is
    already protected upstream by session_block's render gate, but reconcile()
    on the write side is not, and both route through this function.
    """
    value = data.get("items")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _archived(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The archived item dicts, or an empty list — the same guard `_items`
    carries, over the archive list.

    `archive` is OPTIONAL in the schema: a file without the key is exactly the
    pre-archive shape, and absent reads as empty everywhere. A PRESENT but
    non-list `archive` is non-conformance validate() names, and this function
    runs on data validate() has already rejected — so the type is tested rather
    than assumed, same as `_items`.

    ONE DEFINITION, imported by the write side rather than re-declared there
    the way `_items` is: nothing on the write path needs a differently-shaped
    archive accessor, so the second copy would buy nothing and could drift.
    """
    value = data.get("archive")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _resolved(path: Path) -> Path:
    """Absolute, symlink-free form, or the path unchanged when it will not
    resolve. Never raises, so a comparison is always well defined."""
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def file_local_flags(
    data: Dict[str, Any], include_settled: bool = False, *, subject_pool: str
) -> List[str]:
    """Drift visible in the file alone, with no git, tracker or store lookup.

    Three of the drift classes are decidable from the file's own contents, and
    those three are the only ones the read path may compute: a relational field
    naming an unknown id, a blocked_by naming an item already done, and two
    mutually exclusive items both active right now. Every other class needs an
    external source and belongs to the reconciliation path.

    Shared with the write side so both surfaces report the same file-local
    drift rather than growing two answers to one question.

    EVERY relational field is read through _relation_ids, because this function
    now runs on data validate() has already REJECTED: rendering a
    non-conforming file means its fields arrive unchecked.

    TWO POPULATIONS, AND THIS IS THE ONE DRIFT PRODUCER THAT NEEDS BOTH. The
    other three take a single `live` list because every consumer inside them
    wants live items. Here `by_id` is an ID UNIVERSE, not a work list: one
    branch asks whether a relation names an id that EXISTS AT ALL, and the
    blocked-by rule has to be able to FIND a settled blocker to report it.
    Narrow `by_id` to live items and both break at once — a live item blocked
    by a done item stops matching and is accused of naming an unknown id,
    which is a FALSE flag replacing a correct one, and the blocked-by rule
    becomes unreachable by construction. So `by_id` spans BOTH lists — the
    archive is a relocation, not a removal, and an id in it must still resolve —
    while only the SUBJECTS narrow.

    `include_settled` is the view, not the data. A flag against a hidden row
    contradicts the listing beside it; against a row `--all` displays, it does
    not. The default serves the default view.

    `subject_pool` selects WHICH list the subjects come from: "items" (the
    live list) or "archive" for the `--archived` view, whose displayed rows
    are the archived ones. KEYWORD-ONLY with NO default: the pool is the
    answer to "which rows does this report show", and a caller that omits it
    gets Python's own TypeError rather than a silently assumed list. The
    universe never varies — only the subjects do.
    """
    items = _items(data)
    by_id = {
        item.get("id"): item
        for item in items + _archived(data)
        if isinstance(item.get("id"), str)
    }
    pool = _archived(data) if subject_pool == "archive" else items
    subjects = (
        pool
        if include_settled
        else [item for item in pool if item.get("status") not in SETTLED]
    )
    flags: List[str] = []

    for item in subjects:
        label = _label(item)
        for field in RELATIONAL_FIELDS:
            ids, malformed = _relation_ids(item, field, label)
            flags.extend(malformed)
            for other in ids:
                if other not in by_id:
                    flags.append(f"{label}: {field} names unknown id {other!r}")
                elif field == "blocked_by" and by_id[other].get("status") in SETTLED:
                    # SETTLED, not just `done`. Tested against `== "done"` this
                    # said NOTHING about an item blocked by a DROPPED one, which
                    # leaves it stuck forever with no flag — an absent flag, not
                    # a wrong one, and the harder kind to notice.
                    blocker = by_id[other]
                    flags.append(
                        f"{label}: blocked_by names {_label(blocker)}, which is "
                        f"{blocker.get('status')} — it will not clear on its own"
                    )

    # DEDUP ON IDS, NOT LABELS, and only when the subject HAS one.
    #
    # The old guard was `_label(item) < _label(peer)`, which reports a pair
    # once ONLY IF BOTH SIDES CARRY THE LINK. The writer sets exclusive_with
    # from user args on ONE item, so one-sided is the normal shape — and a
    # one-sided link whose subject sorts second satisfied the guard from
    # neither direction and went unreported. A 50% silent miss.
    #
    # `_label` is `str(id or title or "?")`, so it is NOT unique on the
    # non-conforming data this function is documented to run on: two items
    # with neither id nor title both label "?". Keying `seen` on labels would
    # swallow a real pair there — trading this silent miss for a new one.
    #
    # A subject with no string id needs no dedup AT ALL, which is why the key
    # is skipped rather than faked: it never enters `by_id`, so no other item's
    # exclusive_with can resolve to it, so its pair cannot be emitted from the
    # other direction.
    seen_pairs = set()
    for item in subjects:
        if item.get("status") != "active":
            continue
        # The malformed flags are DISCARDED here: this field was already read
        # in the loop that precedes this one, and reporting it twice would
        # double-count one defect in the block's flag count.
        ids, _ = _relation_ids(item, "exclusive_with", _label(item))
        item_id = item.get("id")
        for other in ids:
            peer = by_id.get(other)
            if peer is None or peer.get("status") != "active":
                continue
            if isinstance(item_id, str):
                key = tuple(sorted((item_id, other)))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
            # SORTED, which reproduces the old text byte for byte: the old
            # guard only fired when the subject's label sorted first, so the
            # smaller label was always named first.
            first, second = sorted((_label(item), _label(peer)))
            flags.append(f"{first} and {second} are exclusive and both active")

    return flags


def _relation_ids(
    item: Dict[str, Any], field: str, label: str
) -> Tuple[List[str], List[str]]:
    """Usable ids from one relational field, plus flags for what was dropped.

    TWO RAISES LIVE HERE WITHOUT THIS GUARD, and both became reachable when the
    read path began rendering files that failed validate(): a truthy
    non-iterable field raises on iteration, and an unhashable entry (a dict, a
    list) raises on the `in by_id` membership test. Either one is caught by
    session_block's outer handler, so totality holds — but the OUTCOME is that
    the block and its named conformance note are both replaced by a read
    failure naming no file, which is the state the named-path work exists to
    prevent.

    Ids are strings by schema, so a non-string entry could never match one; it
    is dropped rather than compared. Dropping is REPORTED rather than silent,
    because a non-conforming file is exactly what the render path now shows and
    says so about.

    The guard lives here rather than in validate() for the reason that settled
    the title-length question: a validate() failure no longer suppresses the
    block, so a rule there yields a flag and defends nothing on this path.
    """
    value = item.get(field)
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [f"{label}: {field} is {type(value).__name__}, expected a list"]
    ids = [entry for entry in value if isinstance(entry, str)]
    dropped = len(value) - len(ids)
    if not dropped:
        return ids, []
    # Noun-only phrasing: no verb, so nothing has to agree with the count.
    return ids, [
        f"{label}: {field} holds {dropped} non-id entr{'y' if dropped == 1 else 'ies'}"
    ]


def format_block(data: Dict[str, Any], context_anchor: Optional[float] = None) -> str:
    """The session-start block: active work, the next few planned by rank, and
    a count of file-local drift. Never the whole list — a block that grows with
    the backlog becomes noise and gets skimmed.

    `context_anchor` is UNIX epoch SECONDS for when the context was last
    populated from scratch, or None. Passed rather than imported: this module
    deliberately excludes session_init from its import closure.
    """
    items = _items(data)
    active = [item for item in items if item.get("status") == "active"]
    planned = sorted(
        (item for item in items if item.get("status") == "planned"),
        # The report's fourth key rides here too — a missing or non-string
        # `added` keys as "" and sorts first, same loud-non-conformance choice
        # as `_render`; the isinstance test keeps a truthy non-string from
        # TypeErroring the comparison. On any writer-produced file the stable
        # sort already yielded oldest-first, so this changes uniformity, not
        # behaviour.
        key=lambda item: (
            _rank_key(item),
            item.get("added") if isinstance(item.get("added"), str) else "",
        ),
    )[:_BLOCK_PLANNED_LIMIT]
    flags = file_local_flags(data, subject_pool="items")

    lines = [f"PACT backlog ({data.get('project')}):"]
    if active:
        lines.append("  active: " + "; ".join(_title(item) for item in active))
    if planned:
        lines.append("  next: " + "; ".join(_title(item) for item in planned))
    if not active and not planned:
        lines.append("  nothing active or planned")
    if flags:
        lines.append(f"  {len(flags)} flagged — run /PACT:next for the detail")
    age = _age_line(data, context_anchor)
    if age:
        lines.append(age)
    return "\n".join(lines)


def _age_line(data: Dict[str, Any], context_anchor: Optional[float]) -> str:
    """One line when the backlog has not been written since the context was
    last populated from scratch, otherwise "".

    REPORTS THE OBSERVATION, NOT A DIAGNOSIS. "Nothing written since" cannot
    separate a write mechanism that stopped from a caller that skipped its
    write, and both are worth surfacing, so it says what is true and claims
    no cause.

    With NO anchor this renders nothing rather than falling back to a
    fabricated left-hand side. The caller gates on the TRIGGER — whether this
    is a context reset — never on this value's null-ness: `compact` is not a
    consuming source, so a compact-only journal yields None, and a
    render-when-not-None rule would miss the primary re-injection trigger while
    firing on every resume, which is not a re-injection at all.
    """
    if context_anchor is None:
        return ""
    written = _as_epoch(data.get("updated"))
    if written is None or written >= context_anchor:
        return ""
    return (
        f"  nothing written to the backlog since this context was built "
        f"(last write {data.get('updated')})"
    )


def _as_epoch(value: Any) -> Optional[float]:
    """UNIX seconds for a stored UTC ISO-8601 stamp, or None if unusable.

    DELEGATES so this module holds ONE definition of a parseable stamp. Kept
    separate they had already drifted: this one lacked as_datetime's .strip(),
    so a whitespace-padded value parsed for validate() and not here — the same
    value, two answers, both silent, because both return None on failure.

    The deeper reason to have one and not two agreeing ones: a single parser
    cannot diverge from ITSELF across interpreter versions. Two parsers with
    different normalisation can, and the difference would show up only on the
    versions where fromisoformat's own tolerance differs.
    """
    parsed = as_datetime(value)
    return None if parsed is None else parsed.timestamp()


def session_block(
    project_dir: str,
    backlog_dir: Optional[Path] = None,
    context_anchor: Optional[float] = None,
) -> BacklogNotice:
    """THE TOTAL HELPER. Every state is a return value; nothing raises.

    An exception escaping here does not surface a message — it reaches
    session_init's outer handler, which discards the accumulated context parts
    and emits a safety net instead, destroying the plugin banner and the
    pin-slot line along with anything this module wanted to say. So loudness is
    CONSTRUCTED, as a returned string, and never raised.

    The boundary is the outermost call and cannot be drawn any further in:
    Path.home(), the directory listing, every read and every parse can raise,
    so a boundary one call deeper leaves a raising call outside it.

    States:
      backlog dir absent, or present and empty  -> ("", "")
      one entry matches and validates           -> (block, "")
      a match PLUS unreadable files             -> (block + note, note)
      a match that does not CONFORM             -> (block + note, note)
      a match whose `items` is not a list       -> loud, names the file
      no match, unreadable files seen           -> loud, names those files
      no match, all files readable              -> loud, resolution failure
      project_dir empty or relative             -> loud, names the cause
      anything unexpected                       -> loud, names the exception

    The two block-plus-note rows are the only ones quiet and loud at once, and
    their channels are ASYMMETRIC: `context` carries the block and the notes,
    `alert` carries the notes alone. The block is not news to the user; the bad
    file is, and the user is the only party who can authorise a repair. Putting
    a note only in `context` would leave the problem visible to the
    orchestrator and invisible to the one person able to act.

    This state repeats every session until the file is repaired, and that is
    correct rather than a defect. A correction must outlive the belief it
    corrects, so there is deliberately no suppress-after-first-seen rule.

    Residual, stated rather than implied: `except Exception` does not catch
    BaseException, so KeyboardInterrupt and MemoryError still cross. That
    matches session_init's own handler, and swallowing an interrupt during a
    dying session would be the worse trade.
    """
    try:
        if not project_dir or not Path(project_dir).is_absolute():
            return _loud(
                f"PACT backlog: cannot resolve a backlog because the project "
                f"directory is {project_dir!r}, which is not an absolute path. "
                f"No backlog was read. Nothing is wrong with the store."
            )

        root = get_backlog_dir() if backlog_dir is None else backlog_dir
        if not root.is_dir():
            return BacklogNotice("", "")

        entries = sorted(root.glob("*.json"))
        if not entries:
            return BacklogNotice("", "")

        match, unreadable, claimants, tied = _scan(project_dir, root)
        notes = []
        if unreadable:
            notes.append(
                "PACT backlog: could not read "
                + ", ".join(str(path) for path in unreadable)
                + ". Nothing was modified — this path only reads. Run /PACT:next, "
                "which reports what it can do with each one."
            )

        if match is None:
            if notes:
                return _loud(notes[0])
            return _loud(
                f"PACT backlog: {len(entries)} backlog file(s) under {root}, and "
                f"none records {project_dir} as a checkout root. This is a "
                f"resolution failure, NOT an empty backlog — do not treat this "
                f"project as having no backlog. Either a checkout created since "
                f"the last write is not yet recorded, or this project has never "
                f"held a backlog; /PACT:next writes, which records it."
                + _unexamined_level(project_dir)
            )

        # NON-CONFORMANCE IS NOT CORRUPTION. Corruption is "I cannot understand
        # this" and justifies replacing the block; a rule violation is "I
        # understand it and it breaks a rule I now enforce" and does not. So
        # the block renders from a non-conforming file and the problems are
        # reported beside it. The isinstance gate is load-bearing, for a
        # NARROWER class than "not a list": format_block coerces the item
        # fields IT renders — title, id and rank — and its `or []` absorbs any
        # falsy `items`, so what actually raises HERE is
        # a TRUTHY NON-ITERABLE. Measured — 5, 3.5 and True raise TypeError,
        # while "not a list", {"a": 1}, (1, 2), b"bytes" and None all render
        # cleanly. Pick a truthy non-iterable when testing this branch: a
        # string is a non-list scalar that does NOT raise, so an arm built on
        # one passes with the gate removed. This gate covers `items` ONLY; the
        # relational fields are read by file_local_flags and carry their own
        # guard in _relation_ids.
        data = read_json(match)
        problems = validate(data)
        if problems and not isinstance(data.get("items"), list):
            raise BacklogFileError(match, "; ".join(problems))
        if problems:
            notes.append(
                f"PACT backlog: {match} does not conform ({'; '.join(problems)}). "
                f"The block is rendered from it anyway and nothing was modified."
            )

        block = format_block(data, context_anchor)
        if len(claimants) > 1:
            # SAY ONLY WHAT THIS BRANCH WITNESSED. `tied == 1` means the stamps
            # separated and recency really did choose; above 1 they did not, and
            # calling the pick "most recently updated" there names a mechanism
            # that did not operate. Which mechanism DID is deliberately unsaid:
            # it is the sort's stability over a pre-sorted glob, an implementation
            # detail that goes stale the moment either changes.
            chosen = (
                "most recently updated"
                if tied == 1
                else f"not chosen by recency — {tied} share the newest stamp"
            )
            block += (
                f"\n  {len(claimants)} stored backlogs record this checkout: "
                + ", ".join(path.name for path in claimants)
                + f". Reading {match.name} ({chosen}); "
                f"run /PACT:next to reconcile them."
            )
        if notes:
            # Quiet and loud at once: the block goes only to context, the notes
            # go to both, so the user sees what only they can act on.
            joined = "\n  ".join(notes)
            return BacklogNotice(f"{block}\n  {joined}", joined)
        return BacklogNotice(block, "")

    except BacklogFileError as exc:
        return _loud(
            f"PACT backlog: {_safe_detail(exc)}. The file was NOT modified — this "
            f"path only reads. Run /PACT:next, which reports what it can do "
            f"with it."
        )
    except Exception as exc:  # total helper: every state is a value, never a raise
        return _loud(
            f"PACT backlog: could not be read "
            f"({type(exc).__name__}: {_safe_detail(exc)}). Nothing was modified."
        )


def _unexamined_level(project_dir: str) -> str:
    """One clause naming the level the enclosing-checkout walk could not
    examine, or "" when the walk could. Only the no-match branch asks, so the
    extra walk runs only when the message is already being built."""
    try:
        _enclosing_checkout(_resolved(Path(project_dir)))
    except OSError as exc:
        return (
            f" {_unexamined(exc, Path(project_dir))} could not be examined "
            f"({failure_cause(exc)}), so no checkout enclosing this project "
            f"could be looked for."
        )
    return ""


def _unexamined(exc: OSError, fallback: Path) -> Path:
    """The directory `exc` could not examine: a failed `<dir>/.git` names
    `<dir>`, any other failed path names itself. The read path's decline and
    the write path's refusal both name the level through this, so they agree."""
    if not exc.filename:
        return fallback
    failed = Path(exc.filename)
    return failed.parent if failed.name == ".git" else failed


def _safe_detail(exc: BaseException) -> str:
    """`str(exc)`, or a type-only stand-in when the exception will not print.

    An exception whose own `__str__` raises must not escape the helper whose
    entire contract is that nothing escapes. `repr()` is no safer — `__repr__`
    can raise too — so the fallback names the type, which is a class attribute
    lookup and cannot raise. Shared by both handlers so the guarantee is in one
    place: the corrupt-file handler still gets to name WHICH file, which is the
    only actionable content in that message.
    """
    try:
        return str(exc)
    except Exception:
        return f"<unprintable {type(exc).__name__}>"


def _loud(message: str) -> BacklogNotice:
    """Both channels carry the same string. additionalContext is ungated and
    survives compaction, so the correction outlives the belief it corrects;
    systemMessage is the only channel the user sees, and the user is the only
    party who can authorise a repair. The source gate on systemMessage lives at
    the call site, which already holds `source`.
    """
    return BacklogNotice(context=message, alert=message)


def _label(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("title") or "?")


def _title(item: Dict[str, Any]) -> str:
    """The item's title as ONE capped line, for the session-start block.

    FLATTENING IS THE SECURITY HALF and it has no tunable parameter: the block
    is line-structured, and a title carrying a newline can forge a second
    `active:` line or a role marker into session-start context. `split()` on no
    argument splits on every whitespace class, so the rejoin removes newlines,
    carriage returns and tabs together and collapses runs.

    Capping is noise control, a different job with an arbitrary number. Both
    live here rather than in validate() because an over-long or multi-line
    title is non-conformance, not corruption, and must not take the loud path.
    """
    text = " ".join(str(item.get("title") or item.get("id") or "?").split())
    if len(text) <= _TITLE_DISPLAY_MAX:
        return text or "?"
    return text[: _TITLE_DISPLAY_MAX - 1] + "…"


def _rank_key(item: Dict[str, Any]) -> float:
    rank = item.get("rank")
    return float(rank) if isinstance(rank, (int, float)) else _UNRANKED
