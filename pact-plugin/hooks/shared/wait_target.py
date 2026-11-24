"""
Location: pact-plugin/hooks/shared/wait_target.py
Summary: Deliverable predicate registry for the structural wait mechanism.
         ONE evaluation pass of a watched task file, classified into an
         outcome the caller maps onto its exit contract. The registry is the
         keying seam: changing WHAT counts as "the deliverable arrived" is a
         predicate name in the caller's command text, never schema or output
         surgery.
Used by: hooks/pact_wait.py (the watcher CLI: registry lookup + poll loop),
         hooks/task_lifecycle_gate.py and hooks/wait_filler_gate.py (emit
         the arm command through arm_command), shared/turn_end_gate.py
         (classifies a running watcher through is_wait_watcher_command),
         tests/test_pact_wait.py, tests/test_structural_wait_matrix.py.

Contract: pure functions; never raise. Every filesystem error path resolves
to an explicit outcome constant — the caller decides loudness, not this
module. Target containment is delegated to the shared task_utils seam
(``task_dir_for``): the team name is validated and joined AS GIVEN with the
symlink-escape anchor, never re-implemented here.

Public surface:
- EVAL_* outcome constants — one evaluation pass, mutually exclusive:
    EVAL_PRESENT            deliverable present (predicate satisfied)
    EVAL_NOT_PRESENT        task file parsed, predicate not satisfied
    EVAL_TARGET_INVALID     team name or task id unusable (no file to watch)
    EVAL_TARGET_MISSING     task file absent
    EVAL_TARGET_UNREADABLE  task file exists but cannot be read (OSError)
    EVAL_TARGET_CORRUPT     task file read but not a JSON object
    EVAL_UNKNOWN_PREDICATE  predicate_name not in PREDICATES
- handoff_present(task) — `handoff` predicate: metadata.handoff with a
  non-empty `produced` STRING, not the one a handoff_rejection rejected.
- teachback_submit_present(task) — `teachback_submit` predicate, not the
  one a teachback_rejection rejected.
- PREDICATES — name -> predicate mapping (the registry).
- evaluate_target(team_name, task_id, predicate_name, tasks_base_dir=None)
  — ONE pass: resolve, read, parse, evaluate.
- WATCHER_PATH, TEAM_PLACEHOLDER, TASK_ID_PLACEHOLDER, arm_command(...) —
  the ONE builder of the watcher arm command every emitter uses.
- is_wait_watcher_command(command) — the ONE classifier of a running
  watcher's command text; it accepts every form arm_command emits.
- wait_identity(input_data) — the ONE source of an arm command's store
  team (and the membership team and member name beside it).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
from typing import Any, Callable, NamedTuple

from shared.task_utils import sanitize_task_id, task_dir_for
from shared.paths import get_claude_config_dir


# Outcome constants (plain strings, not an Enum, matching the house style of
# intentional_wait's vocabulary constants). Membership groups in the CLI:
# the first two are "keep polling or declare fired", the rest are terminal.
EVAL_PRESENT = "present"
EVAL_NOT_PRESENT = "not_present"
EVAL_TARGET_INVALID = "target_invalid"
EVAL_TARGET_MISSING = "target_missing"
EVAL_TARGET_UNREADABLE = "target_unreadable"
EVAL_TARGET_CORRUPT = "target_corrupt"
EVAL_UNKNOWN_PREDICATE = "unknown_predicate"


def _revision(value: Any) -> int:
    """A revision number as the rejection protocol writes it: a positive int
    (bool excluded). Anything else counts as 1, the first submission."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return 1


def _awaiting_revision(metadata: dict[str, Any], rejection_key: str) -> bool:
    """True while the deliverable on disk is the one the lead REJECTED.

    The rejection record stays on disk after the teammate re-submits, so
    presence of the deliverable alone cannot tell a revision from the
    rejected original. The rejection carries the revision it rejected
    (`revision_number`, 1 on the first rejection) and the teammate raises
    `metadata.revision_number` past it when re-submitting (absent counts
    as 1). The deliverable counts as delivered again only once
    `metadata.revision_number` exceeds the rejection's.
    """
    rejection = metadata.get(rejection_key)
    if not isinstance(rejection, dict):
        return False
    return _revision(metadata.get("revision_number")) <= _revision(
        rejection.get("revision_number")
    )


def handoff_present(task: dict[str, Any]) -> bool:
    """The `handoff` predicate: True iff the task carries a HANDOFF whose
    `produced` field is a non-empty STRING and that HANDOFF is not the one
    a `metadata.handoff_rejection` rejected (see _awaiting_revision).

    Keying per hooks/shared/handoff_schema.py: `produced` is a REQUIRED
    canonical field and the watch keys on the canonical key only. It does
    NOT resolve the legacy alias spellings that read-side consumers
    (handoff_merge.py, session_resume.py — via resolve_handoff_field)
    accept: a handoff whose `produced` sits under a legacy spelling passes
    lead-side reads yet reads as ABSENT here, so the watch burns its
    deadline to a loud EXPIRED instead of firing. That degraded-loud
    asymmetry is accepted, not hidden. STRING is load-bearing, not pedantry: the
    live failures this mechanism answers were handoffs whose fields were
    JSON ARRAYS — a list-typed `produced` is not a delivered summary, so it
    must read as absent. Membership-grade emptiness ("" only, since a
    non-str already failed the type check) rather than truthiness; the
    strip catches a whitespace-only placeholder.
    """
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if _awaiting_revision(metadata, "handoff_rejection"):
        return False
    handoff = metadata.get("handoff")
    if not isinstance(handoff, dict):
        return False
    produced = handoff.get("produced")
    return isinstance(produced, str) and produced.strip() != ""


def teachback_submit_present(task: dict[str, Any]) -> bool:
    """The `teachback_submit` predicate: True iff the task carries a
    metadata.teachback_submit object with at least one field, and that
    object is not the one a `metadata.teachback_rejection` rejected.

    Presence of the sub-object is the witness, not its schema conformance.
    A rejected teachback stays on disk under the same key until the
    teammate rewrites it, so a watch armed after a rejection must not fire
    on it: the revision-number comparison in _awaiting_revision holds the
    watch until the teammate re-submits.
    """
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if _awaiting_revision(metadata, "teachback_rejection"):
        return False
    teachback = metadata.get("teachback_submit")
    return isinstance(teachback, dict) and len(teachback) > 0


# The registry. Adding a predicate is one function + one entry; the arm
# command then names it. NEVER key a new predicate on a field that is not
# required by its schema module (handoff_schema.py, teachback_schema.py):
# a recommended-only field goes dark on legitimate handoffs and the watch
# silently burns its whole deadline.
PREDICATES: dict[str, Callable[[dict[str, Any]], bool]] = {
    "handoff": handoff_present,
    "teachback_submit": teachback_submit_present,
}


def _read_regular_file(path) -> "str | None":
    """The file's UTF-8 text, or None when the path is not a regular file.

    Only a regular file is read: a FIFO blocks a reader until a writer
    appears and a device such as /dev/zero never ends, either of which
    would hold the watcher past its deadline. The check runs on the opened
    descriptor (open non-blocking, then fstat), so the file read is the
    file checked even if the path is replaced in between. Raises OSError
    and ValueError (UnicodeDecodeError) for the caller to map."""
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        os.set_blocking(fd, True)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def evaluate_target(
    team_name: str,
    task_id: str,
    predicate_name: str,
    tasks_base_dir: str | None = None,
) -> str:
    """ONE evaluation pass of the watched deliverable; returns an EVAL_*
    outcome. Polling (when to call this again) is the CLI's concern.

    Reads exactly ``tasks/{team_name}/{task_id}.json`` under the tasks base
    (default the harness config dir). task_id is sanitized to the same
    single-component form read_task_json uses, then joined inside the
    containment-validated team dir. The team dir resolves under the RAW
    name only (``task_dir_for``, never folded): a legacy non-lowercase
    team name reads MISSING here while read-side consumers resolve
    folded-first candidates — accepted, production team names are minted
    lowercase.

    Args:
        team_name: The {team_name} path component under the tasks root.
        task_id: Task identifier (the task file's stem).
        predicate_name: A PREDICATES key; anything else resolves to
            EVAL_UNKNOWN_PREDICATE (never raises).
        tasks_base_dir: Override for the tasks base dir (testing only).
    """
    check = PREDICATES.get(predicate_name) if isinstance(predicate_name, str) else None
    if check is None:
        return EVAL_UNKNOWN_PREDICATE
    if not isinstance(task_id, str) or not task_id:
        return EVAL_TARGET_INVALID
    sanitized = sanitize_task_id(task_id)
    if not sanitized:
        return EVAL_TARGET_INVALID
    # Every exception the resolve/read/parse path can raise, mapped to an
    # outcome so the watcher never exits 1 on a bad file:
    #   resolving the tasks base and the team dir: RuntimeError (Path.home
    #                             with no home, a symlink loop) -> INVALID
    #   exists() / open / read:   OSError, incl. PermissionError -> UNREADABLE
    #   not a regular file:       a FIFO, device, socket or directory -> UNREADABLE
    #   read decode:              UnicodeDecodeError (a ValueError) -> CORRUPT
    #   json.loads():             JSONDecodeError and the int-digit limit
    #                             (both ValueError), RecursionError -> CORRUPT
    # MemoryError is deliberately not caught: it is not a property of the
    # file and the interpreter may not survive handling it.
    try:
        base = tasks_base_dir if tasks_base_dir else str(get_claude_config_dir() / "tasks")
        team_dir = task_dir_for(team_name, tasks_base_dir=base)
    except RuntimeError:
        return EVAL_TARGET_INVALID
    if team_dir is None:
        return EVAL_TARGET_INVALID
    task_file = team_dir / f"{sanitized}.json"
    try:
        if not task_file.exists():
            return EVAL_TARGET_MISSING
        raw = _read_regular_file(task_file)
    except OSError:
        return EVAL_TARGET_UNREADABLE
    except ValueError:
        return EVAL_TARGET_CORRUPT
    if raw is None:
        return EVAL_TARGET_UNREADABLE
    try:
        task = json.loads(raw)
    except (ValueError, RecursionError):
        return EVAL_TARGET_CORRUPT
    if not isinstance(task, dict):
        return EVAL_TARGET_CORRUPT
    return EVAL_PRESENT if check(task) else EVAL_NOT_PRESENT


# --- the arm command: one builder, one classifier ---------------------------

# hooks/pact_wait.py, resolved from this file so an emitted command always
# names the live install's real path.
WATCHER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pact_wait.py"
)

# Emitted unquoted where the emitter does not know the value; the agent
# substitutes the real value before running the command.
TEAM_PLACEHOLDER = "<team-name>"
TASK_ID_PLACEHOLDER = "<task-id>"
_PLACEHOLDERS = (TEAM_PLACEHOLDER, TASK_ID_PLACEHOLDER)


def _arg(value: object) -> str:
    text = str(value)
    return text if text in _PLACEHOLDERS else shlex.quote(text)


def arm_command(
    team_name: str,
    task_id: str,
    predicate: str,
    deadline: int,
    watcher: str = WATCHER_PATH,
) -> str:
    """The ready-to-run watcher arm command. Every value is shlex-quoted
    except a placeholder, so any install path or team name survives the
    shell, and is_wait_watcher_command accepts the result once the
    placeholders are substituted."""
    return (
        f"python3 {_arg(watcher)} --team {_arg(team_name)} "
        f"--task {_arg(task_id)} --predicate {_arg(predicate)} "
        f"--deadline {_arg(deadline)}"
    )


# The watcher command shape. Each value slot is ONE shell word built only
# from shell-inert pieces: shlex.quote's no-op character set bare, a
# single-quoted span, or a double-quoted span holding no expansion or
# escape character. Adjacent pieces concatenate, which is how shlex.quote
# renders a value containing a single quote. A ; | & < > ( ) $ or backtick
# outside quotes cannot sit inside a word, so a command composed onto the
# watcher (in any slot, spaced or not) does not match and still counts as
# a job the ending frame must account for. Slots are separated by spaces or
# tabs only: a newline ends a shell command, so a line break between slots
# would start another job (leading and trailing whitespace of any kind runs
# nothing and is admitted). Admitted around the invocation:
# an optional `cd <dir> &&`, env assignments, a python3 interpreter bare
# or by path, and a trailing `2>&1`. The script word, once the shell's
# quote removal is applied, must name a file called exactly pact_wait.py.
_BARE = r"[A-Za-z0-9_@%+=:,./-]"
_PIECE = rf"""(?:{_BARE}|'[^']*'|"[^"$`\\]*")"""
_WORD = rf"{_PIECE}+"
_PACT_WAIT_SHAPE = re.compile(
    r"\A\s*"
    rf"(?:cd[ \t]+{_WORD}[ \t]*&&[ \t]*)?"
    rf"(?:[A-Za-z_][A-Za-z0-9_]*=(?:{_WORD})?[ \t]+)*"
    rf"(?:(?:{_BARE}*/)?python3(?:\.\d+)?[ \t]+)?"
    rf"(?P<script>{_WORD})[ \t]+"
    rf"--team[ \t]+{_WORD}[ \t]+--task[ \t]+{_WORD}[ \t]+"
    rf"--predicate[ \t]+{_WORD}[ \t]+--deadline[ \t]+{_WORD}"
    r"(?:[ \t]+2>&1)?\s*\Z",
    re.ASCII,
)


def is_wait_watcher_command(command: Any) -> bool:
    """True iff `command` IS an armed pact-wait watcher, in the shape
    described above. A command that only mentions the watcher, runs it
    partially, or composes another command onto it does not match."""
    if not isinstance(command, str):
        return False
    match = _PACT_WAIT_SHAPE.match(command)
    if match is None:
        return False
    try:
        script = shlex.split(match.group("script"))
    except ValueError:
        return False
    return len(script) == 1 and os.path.basename(script[0]) == "pact_wait.py"


# --- one identity source for both arm-command producers --------------------


class WaitIdentity(NamedTuple):
    """store_team: the team an arm command's --team names and the store the
    lifecycle gate reads tasks from. member_team: the team whose config
    holds membership and role (in a config-less substrate the config is
    keyed session-<id8> while the store is the session-UUID directory, so
    the two differ). member: this frame's teammate name, "" for a lead or a
    frame whose name does not resolve."""

    store_team: str
    member_team: str
    member: str


def wait_identity(input_data: Any) -> WaitIdentity:
    """The one resolver both producers (the lifecycle advisory and the
    filler gate's tier-3 outlet) use for an arm command's identity. Never
    raises; an unresolvable identity is WaitIdentity("", "", "").

    The store is the live task store (resolve_task_dir). Only a frame with
    NO session context (frame_team_and_name named a member: the registry,
    frame-field or agent_id routes a separate-process teammate takes) falls
    back to its frame team, and only when that team's store directory
    exists. A frame WITH a context whose store resolver returns None (the
    ambiguous both-names-exist case, or no store at all) stays without a
    store: no command is better than one aimed at a guessed store."""
    try:
        from shared.background_work import frame_team_and_name, teammate_launch_name
        from shared.task_utils import resolve_task_dir

        frame_team, frame_name = frame_team_and_name(input_data)
        task_dir = resolve_task_dir()
        if task_dir is not None:
            store_team = task_dir.name
        elif frame_name and frame_team:
            base = get_claude_config_dir() / "tasks"
            frame_dir = task_dir_for(frame_team, tasks_base_dir=str(base))
            store_team = frame_team if frame_dir is not None and frame_dir.is_dir() else ""
        else:
            store_team = ""
        member_team = frame_team or store_team
        member = teammate_launch_name(input_data, member_team) if member_team else ""
        return WaitIdentity(store_team, member_team, member or "")
    except Exception:
        return WaitIdentity("", "", "")
