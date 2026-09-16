"""
Location: pact-plugin/hooks/shared/background_work.py
Summary: Team-scoped registry of outstanding background Bash launches (a frame
         carrying the harness `run_in_background` flag, or a command ending in
         a bare `&`), plus the unflagged-background fire predicate. Rows are
         written for a TEAMMATE's launch, and for a launch made inside an
         Agent-tool SUBAGENT — the latter marked with `owner_role` and carrying
         no task ids, because that launch lands in the lead's own job list with
         no owner and would otherwise be charged to the lead.
         Pure helpers and fail-open loaders — no hook I/O, no registration.
Used by: track_files.py (Layer 1 writer), teammate_idle.py (Layer 2
         advisory), missed_wake_scan.py (Layer 3 lead surface),
         wait_filler_gate.py (who receives the launch advisory),
         task_lifecycle_gate.py (adds a claimed task to its owner's records).

A teammate who backgrounds Bash and ends the turn with no valid
intentional_wait is recorded here. Detection requires a registry row PLUS
in_progress PLUS validate_wait false — mid-arc in_progress with no row
must not fire.

SCOPE — A SUBSET OF SHELL-SHAPED WORK, AND BOTH LIMITS ARE STRUCTURAL
RATHER THAN OVERSIGHTS. The launch is observed from a PostToolUse `Bash`
frame, so the only background work this registry can ever hold is a shell
command. The
platform tracks several other kinds — a monitor, an Agent-tool subagent, an
MCP task, a workflow, a scheduled wakeup — and none of them raises a `Bash`
tool event, so none is recorded and no layer fires for one. A teammate can
therefore hold genuinely outstanding work that this mechanism cannot see.
That is the OUTER limit. There is an inner one: within shell work this
records two shapes and no others — a frame carrying the harness background
flag, or a command ENDING in a bare `&`. A shell launch backgrounded any
other way (`( cmd & )`, `cmd & echo started`, `… & disown`) is as invisible
as a subagent; `background_launch.is_shell_backgrounded_bash` carries the
measured list.
DO NOT DESCRIBE THIS AS ENFORCING THE WAIT RULE GENERALLY, AND DO NOT
DESCRIBE IT AS COVERING SHELL WORK GENERALLY EITHER. The instruction to
agents is unconditional — flag every self-started wait — but what is
DETECTED here is a subset of a subset, and conflating any of the three is
what makes an absent advisory read as an all-clear.

Contract: never raise on missing/corrupt files, an unusable team directory,
empty team name, or malformed records. Every state-file read and write goes
through `state_file` (read_text / locked_update / write_text): writes hold a
sidecar lock and swap in a complete temp file, no symlink at the file is
followed, files are created 0o600, undecodable bytes read as empty and are
rewritten on the next change, and a no-op update creates no file. Read-time
24h TTL drops stale rows. Team path uses
pact_context.get_team_name() after init() — the same identity-aligned
resolver teammate_idle and get_task_list use. The launch path resolves the
team through `frame_team_and_name`, which also finds a separate-process
teammate's team, since that teammate has no session context of its own.

HOW READERS MATCH A ROW, AND WHY A NEW ROW SHAPE MUST BE CHECKED AGAINST ALL
THREE. Every consumer of this registry selects rows by one of three keys: the
job id (`harness_task_id`), the owner's name (`agent_name`), or the tasks the
row covers (`task_ids`). An argument that a new row shape is safe for one key
says NOTHING about the other two — a row carrying no task ids is unreachable
by every task-keyed reader and still perfectly reachable by a name-keyed one.
The name-keyed readers are the MINORITY and the easiest to miss, which is what
makes the mistake worth naming here: an argument built while reading the
task-keyed majority feels complete and is not. `turn_end_gate`'s SubagentStop
branch and `extend_records_for_claim` are two of them.

THE SAME CLAIM FROM THE READER'S SIDE, because the paragraph above instructs
whoever adds a ROW and the person who needs it most is whoever adds a READER: a
new name-keyed reader must EXCLUDE rows that are not a member's — both of those
do it with `not record.get("owner_role")` — because matching a name says
nothing about whether the row belongs to a member at all.

MEASURED FROM SOURCE, NOT COPIED FROM A LIST. Which consumers exist is a
DECISION to re-derive rather than an inventory to trust: sweep `hooks/**/*.py`
and `scripts/**/*.py` for the three key names above and for the loaders that
hand out rows. A roll-call written here would be correct the day it was
written and quietly wrong the first time a reader was added, so the taxonomy is
the durable part and the set is not.

TWO team-scoped state files, and they must stay separate. The registry
(background_work.json) holds outstanding launches. The idle counter
(unflagged_background_idle.json) backs Layer 2's three-consecutive-idles
threshold. DO NOT collapse the counter into the existing idle_counts.json:
that file's writer pops a teammate's key whenever the task is not
`completed`, and Layer 2's whole population is a teammate idling on an
`in_progress` task — the exact branch that pops. Sharing the file resets
the counter every tick and Layer 2 can never reach three.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .background_launch import (  # noqa: F401 — re-exported for callers of this module
    command_from_frame,
    is_background_launch,
    is_harness_background_bash,
    is_shell_backgrounded_bash,
)
from . import state_file
from .intentional_wait import canonical_since, validate_wait
from .pact_context import get_team_name
from .paths import get_claude_config_dir

REGISTRY_FILENAME = "background_work.json"
UNFLAGGED_IDLE_FILENAME = "unflagged_background_idle.json"
RECORD_TTL_SECONDS = 24 * 3600
LEAD_STALE_MINUTES = 10

# Layer 3 falls back to registered_at when idled_at is absent, so that a
# missed TeammateIdle cannot disable the lead surface. registered_at is NOT
# evidence of idling — only that time has passed since a launch — so it
# carries the longer window. 30 minutes matches this project's existing
# intentional-wait staleness threshold rather than introducing a second
# number; it is a judgement, not a measurement, and is a named constant so
# a later measurement can move it.
LEAD_UNIDLED_STALE_MINUTES = 30

UNFLAGGED_IDLE_THRESHOLD = 3

# The owner role a row carries when its launcher is an Agent-tool subagent
# rather than a teammate. Written only on those rows: a row without it is a
# teammate row, which is also what every row written before this field existed
# is, so absence defaulting to "teammate" is correct rather than merely
# convenient.
#
# WHY A SUBAGENT ROW EXISTS AT ALL. A shell launched inside a subagent appears
# in the LEAD's background_tasks with no owner and outlives the subagent, and
# the lead's candidate set is every running shell MINUS the recorded launches.
# Unrecorded, it is charged to the lead, which is refused a turn end over a job
# it did not start and cannot flag. The row is what marks it as someone else's.
#
# WHY THE ROW CARRIES NO TASK IDS. A subagent holds no task by construction, so
# there is no anchor task to list. Every task-keyed reader of this store —
# matching_outstanding, any_listed_task_flagged, has_live_listed_task,
# discharge_acknowledged_for_owner, extend_records_for_claim and
# missed_wake_scan.find_unanchored_waits — skips a row it cannot match a task
# to, so an empty list already keeps a subagent row out of every TEAMMATE
# surface. This marker is what AUTHORISES that empty list past
# _sanitize_record, and what states the role rather than leaving the next
# reader to infer it from an empty field.
OWNER_ROLE_SUBAGENT = "subagent"

WAIT_CLASS_MISSING = "missing"
WAIT_CLASS_NULL = "null"
WAIT_CLASS_MALFORMED = "malformed"

# The wait key holding the SCOPING ANCHOR, as distinct from `since`, which is
# the freshness clock. Absent and malformed are kept apart because they have
# different causes and different remedies: absent means written before this
# field existed, or dropped by an agent on a re-SET; malformed means an agent
# wrote something unparseable and has a bug. Both fall back to `since` and
# both are surfaced, so neither is collapsed into a pass or a fail.
WAIT_ANCHOR_KEY = "covers_since"
ANCHOR_CLASS_ABSENT = "absent"
ANCHOR_CLASS_MALFORMED = "malformed"


def parse_iso(ts: Any) -> datetime | None:
    """Parse a tz-aware ISO-8601 timestamp, or None if unusable."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now(now: datetime | None = None) -> str:
    if now is None:
        return canonical_since()
    return now.isoformat(timespec="seconds")


def classify_wait(task: Any) -> str | None:
    """Return the wait-absence class, or None if validate_wait succeeds.

    Classes: missing (no intentional_wait key), null (value is None),
    malformed (present but not well-formed). Never raises on plain dicts.
    """
    if not isinstance(task, dict):
        return WAIT_CLASS_MISSING
    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        return WAIT_CLASS_MISSING
    if "intentional_wait" not in metadata:
        return WAIT_CLASS_MISSING
    wait = metadata.get("intentional_wait")
    if wait is None:
        return WAIT_CLASS_NULL
    if validate_wait(wait):
        return None
    return WAIT_CLASS_MALFORMED


def wait_scope_anchor(wait: Any) -> "tuple[datetime | None, str | None]":
    """The timestamp scoping what a wait covers, plus its anchor class.

    Returns (anchor, None) when `covers_since` is present and parseable, and
    (fallback, ANCHOR_CLASS_ABSENT|ANCHOR_CLASS_MALFORMED) otherwise, where
    the fallback is `since`. A non-None class means the scope is resting on
    the freshness clock, which is exactly the thing that moves.

    WHY `since` CANNOT DO BOTH JOBS. `since` is the freshness clock and
    agents are INSTRUCTED to re-SET it so a long wait does not read as stale.
    Scoping on it means every re-stamp widens the wait FORWARD to cover
    launches made after it was raised — a rolling amnesty, and precisely the
    blanket the `>= registered_at` comparison exists to prevent. So the
    clock re-stamps and the anchor does not.

    THE ANCHOR IS WRITTEN ONCE BY CONVENTION AND NOTHING ENFORCES THAT.
    It is agent-written, in the same TaskUpdate that re-stamps `since`, so an
    agent that drops it on a re-SET silently returns to the old behaviour via
    the fallback below. Calling it immutable would claim a guarantee this
    mechanism does not provide. It is strictly better than scoping on a
    re-stamped clock and it is not robust, and those are different claims.

    THE FALLBACK IS DELIBERATE AND IS NOT A FAIL-OPEN. An absent anchor is a
    third state beside covered and uncovered, and collapsing it into either
    is the overloaded-null failure: scoping it closed would refuse to cover
    anything for every wait written before this field existed, which today is
    all of them. So coverage falls back to `since` — behaviour identical to
    before — and the missing anchor is SURFACED to the lead instead, where it
    is visible rather than silently resolved in either direction.

    Pure: reads a wait dict, writes nothing.
    """
    if not isinstance(wait, dict):
        return None, ANCHOR_CLASS_ABSENT
    fallback = parse_iso(wait.get("since"))
    if WAIT_ANCHOR_KEY not in wait:
        return fallback, ANCHOR_CLASS_ABSENT
    anchor = parse_iso(wait.get(WAIT_ANCHOR_KEY))
    if anchor is None:
        return fallback, ANCHOR_CLASS_MALFORMED
    return anchor, None


def wait_anchor_class(task: Any) -> str | None:
    """Anchor class for a task's VALID wait, or None when it is anchored.

    Returns None for a task carrying no valid wait at all — the anchor is a
    property of a wait, so a task without one has no anchor defect to report.
    Callers wanting the absence of a wait itself use classify_wait.
    """
    if classify_wait(task) is not None:
        return None
    metadata = task.get("metadata") if isinstance(task, dict) else None
    wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
    return wait_scope_anchor(wait)[1]


def _team_file(filename: str, team_name: str | None = None) -> Path | None:
    name = team_name if team_name is not None else get_team_name()
    if not isinstance(name, str) or not name:
        return None
    return get_claude_config_dir() / "teams" / name / filename


def registry_path(team_name: str | None = None) -> Path | None:
    """Team-scoped registry path, or None when the team name is unusable."""
    return _team_file(REGISTRY_FILENAME, team_name)


def unflagged_idle_path(team_name: str | None = None) -> Path | None:
    """Path of Layer 2's own idle counter. See the module docstring for why
    this is not idle_counts.json."""
    return _team_file(UNFLAGGED_IDLE_FILENAME, team_name)


def _record_expired(record: dict, now: datetime) -> bool:
    registered = parse_iso(record.get("registered_at"))
    if registered is None:
        return True
    return (now - registered).total_seconds() >= RECORD_TTL_SECONDS


def _clean_task_ids(raw: Any) -> list[str] | None:
    """Normalize the task_ids field, or None when it is unusable.

    A bare string is REJECTED rather than coerced into a one-element list.
    Records are ephemeral team state with a 24h TTL and are never read
    across a version boundary, so a scalar here is a malformed write rather
    than an older schema, and dropping it is safer than reinterpreting it.
    """
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            return None
        out.append(item)
    return out or None


def _sanitize_record(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    agent_name = raw.get("agent_name")
    session_id = raw.get("session_id")
    task_ids = _clean_task_ids(raw.get("task_ids"))
    registered_at = raw.get("registered_at")
    # A subagent owner holds no task, so its row is the one shape allowed to
    # carry no task ids. See OWNER_ROLE_SUBAGENT for why that is safe against
    # every task-keyed reader, and why the marker rather than the empty list is
    # what carries the role.
    subagent_owned = raw.get("owner_role") == OWNER_ROLE_SUBAGENT
    if not isinstance(agent_name, str) or not agent_name:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    if task_ids is None and not subagent_owned:
        return None
    if parse_iso(registered_at) is None:
        return None
    out = {
        "agent_name": agent_name,
        "session_id": session_id,
        "task_ids": task_ids or [],
        "registered_at": registered_at,
    }
    if subagent_owned:
        out["owner_role"] = OWNER_ROLE_SUBAGENT
    harness = raw.get("harness_task_id")
    if isinstance(harness, str) and harness:
        out["harness_task_id"] = harness
    command = raw.get("command")
    if isinstance(command, str) and command:
        out["command"] = command[:240]
    idled_at = raw.get("idled_at")
    if parse_iso(idled_at) is not None:
        out["idled_at"] = idled_at
    # Preserved only when TRUE. A row without it is a teammate row, which is
    # also what every row written before this field existed is — and those
    # SHOULD expire on completion, so absence defaulting to False is correct
    # rather than merely convenient.
    if raw.get("anchor_completed") is True:
        out["anchor_completed"] = True
    return out


def _parse_records_text(text: str, now: datetime) -> list[dict]:
    try:
        raw = json.loads(text) if text.strip() else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    items = raw.get("records") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        record = _sanitize_record(item)
        if record is None or _record_expired(record, now):
            continue
        out.append(record)
    return out


def _teams_root() -> Path:
    """The directory every team-scoped state file must stay under."""
    return get_claude_config_dir() / "teams"


def _load_records(
    team_name: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Load outstanding (non-expired) records. Fail-open to [].

    Expiry here is the 24h TTL alone. See `unflagged_fire` for why no
    task-status predicate belongs here.
    """
    path = registry_path(team_name)
    if path is None:
        return []
    now = now or utc_now()
    try:
        text = state_file.read_text(path, _teams_root())
    except FileNotFoundError:
        return []
    except (OSError, TypeError, ValueError):
        return []
    return _parse_records_text(text, now)


def _atomic_update_records(
    mutator,
    team_name: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Read-modify-write the registry under one lock. Fail-open: returns False
    on any error and never raises, including an unusable team directory and
    an undecodable registry."""
    clock = now if now is not None else utc_now()

    def _apply(text: str) -> "tuple[str, bool, bool]":
        current = _parse_records_text(text, clock)
        updated, changed = mutator(current)
        if not changed:
            return text, False, True
        clean = [r for r in (_sanitize_record(x) for x in updated) if r is not None]
        return json.dumps({"records": clean}), True, True

    try:
        path = registry_path(team_name)
        if path is None:
            return False
        return state_file.locked_update(path, _apply, _teams_root())
    except (OSError, TypeError, ValueError):
        return False


def save_records(
    records: list[dict],
    team_name: str | None = None,
) -> bool:
    """Replace the registry. Fail-open: return False on any error."""
    try:
        path = registry_path(team_name)
        if path is None:
            return False
        clean = [r for r in (_sanitize_record(x) for x in records) if r is not None]
        state_file.write_text(path, json.dumps({"records": clean}), _teams_root())
        return True
    except (OSError, TypeError, ValueError):
        return False


def append_record(
    record: dict,
    team_name: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one sanitized record. Fail-open.

    `now` reaches the EXPIRY PRUNE, which runs on the read inside this write:
    _atomic_update_records -> _parse_records_text -> _record_expired. Past the
    TTL and without it, two successive appends silently drop the first record
    and still return True. This is the module's only clock on this path; the
    stamp lives in record_background_launch, which threads its own.
    """
    clean = _sanitize_record(record)
    if clean is None:
        return False

    def _append(current: list[dict]) -> tuple[list[dict], bool]:
        current.append(clean)
        return current, True

    return _atomic_update_records(_append, team_name=team_name, now=now)


def record_task_ids(record: Any) -> list[str]:
    """The task ids a record covers. [] for anything malformed."""
    if not isinstance(record, dict):
        return []
    ids = record.get("task_ids")
    return list(ids) if isinstance(ids, list) else []


def matching_outstanding(
    task: Any,
    records: list[dict] | None = None,
    now: datetime | None = None,
    team_name: str | None = None,
) -> dict | None:
    """First outstanding record listing this task among its task_ids."""
    if not isinstance(task, dict):
        return None
    task_id = task.get("id")
    if task_id is None:
        return None
    task_id = str(task_id)
    rows = records if records is not None else _load_records(team_name, now=now)
    for record in rows:
        if task_id in record_task_ids(record):
            return record
    return None


def any_listed_task_flagged(
    record: Any,
    tasks: Any = None,
) -> bool:
    """True iff SOME in_progress task listed on the record carries a valid wait.

    This is the R5 fire predicate's silencing half, and it generalises in
    the SAFE direction deliberately: a teammate holding two tasks who
    flagged the wait on EITHER has flagged it, so the advisory stays
    silent. Under the old exactly-one-task rule that teammate got no record
    at all, so this strictly adds coverage without adding a
    false-positive route.

    `tasks` is the team's task list. When it is None the caller has no
    task set to check and only the task in hand can be judged, so this
    returns False and the caller's own classify_wait decides.

    NO STATUS FILTER, DELIBERATELY. This used to require `in_progress`, which
    silently excluded consultants: a consultant's carrier is its most recently
    COMPLETED task, so its wait sat on a task this loop skipped and could never
    silence anything. Metadata writes to a completed task land, so that wait is
    real and readable. Listing is what scopes this — the record names the task
    ids it covers — and the status adds nothing to that.

    A RECORD WRITTEN WITH `anchor_completed` IS SILENCED ONLY BY A WAIT THAT
    COVERS ITS LAUNCH (`wait_covers_record`). Its anchor task was already
    completed when the launch happened, and a completed task routinely still
    carries a wait raised BEFORE that launch — the completion flow leaves one
    behind. Accepting any valid wait there would let that older wait hide
    every later launch the consultant makes. Records anchored on in_progress
    tasks keep the unscoped rule above: any valid wait on a listed task
    silences.
    """
    if not isinstance(tasks, list):
        return False
    listed = set(record_task_ids(record))
    if not listed:
        return False
    scoped = isinstance(record, dict) and record.get("anchor_completed") is True
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if str(task.get("id")) not in listed:
            continue
        if classify_wait(task) is not None:
            continue
        if scoped and not wait_covers_record(task, record):
            continue
        return True
    return False


def load_records_for_discharge(
    team_name: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """The UNGATED read. TTL only — no expiry gate, no suppression gate.

    THE NAME IS THE ENFORCEMENT. The raw loader is private (`_load_records`)
    so that a future consumer cannot reach an ungated read by accident; to get
    one it has to type the word `discharge`, which is a decision rather than a
    default. The previous arrangement — a public raw loader plus a docstring
    asking callers to remember two gates — did not hold for the second
    consumer, and would not have held for a third.

    USE IT ONLY WHERE THE READ MUST SEE FLAGGED RECORDS. There are two such
    production consumers. The discharge path exists to retire a flagged
    record, so a gated read would hide every record it is looking for and kill
    the mechanism while every gate test still passed. The lead-side
    `find_unanchored_waits` (missed_wake_scan) surfaces a wait that is
    acquitting a record on its fallback anchor, which is by definition a
    record the flagged-wait gate hides. Tests asserting raw on-disk state are
    the only other legitimate caller.

    Anything that surfaces a record AS OUTSTANDING — telling a human or an
    agent that work is unflagged — must call `outstanding_unflagged` instead.
    """
    return _load_records(team_name, now=now)


def owner_anchor_tasks(tasks: Any, owner: Any) -> "tuple[list[str], bool]":
    """This owner's anchor task ids, and whether the anchor is already completed.

    Returns (ids, anchor_completed): EVERY `in_progress` task owned by `owner`
    when there is at least one, otherwise the single most-recently-completed
    one. Empty list when the owner has no task at all.

    LIFTED FROM teammate_idle.find_teammate_task, WHICH ALREADY HAD THIS RIGHT.
    That resolver returns `in_progress or most-recently-completed`, and the
    shared module used to disagree with it by filtering on `in_progress`
    everywhere. The disagreement is what made a CONSULTANT invisible: a
    consultant owns no `in_progress` task BY DEFINITION — that is what being
    one means — so it matched zero tasks, nothing was recorded, and every
    layer was off for it. One resolver, used by both, is the fix.

    It generalises the lifted version in one direction only: ALL in_progress
    tasks rather than one, because a teammate may hold several and the
    framework's own instructions permit it. Most-recent-completed stays
    single — "most recent" has no plural.

    Recency is by integer task id. Ids are numeric strings, so a string
    compare would rank "3" above "20".
    """
    in_progress: list[str] = []
    completed_id: int | None = None
    completed: str | None = None
    if not isinstance(tasks, list):
        return [], False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("owner") != owner:
            continue
        task_id = task.get("id")
        if task_id is None:
            continue
        status = task.get("status")
        if status == "in_progress":
            in_progress.append(str(task_id))
        elif status == "completed":
            try:
                num = int(task_id)
            except (ValueError, TypeError):
                continue
            if completed_id is None or num > completed_id:
                completed_id, completed = num, str(task_id)
    if in_progress:
        return in_progress, False
    return ([completed], True) if completed is not None else ([], False)


def task_is_live(task: Any) -> bool:
    """True iff this task is still `in_progress`. The liveness SSOT.

    Named rather than inlined so the per-task consumer (`unflagged_fire`) and
    the per-record consumer (`has_live_listed_task`) share ONE implementation.
    Two copies of the same predicate is how they drift apart.
    """
    return isinstance(task, dict) and task.get("status") == "in_progress"


def has_live_listed_task(record: Any, tasks: Any) -> bool:
    """True iff some task this record covers is still live.

    🔴 CONSULTANT RECORDS HAVE NO STRUCTURAL EXPIRY, AND THAT IS A WEAKER
    GUARANTEE THAN A TEAMMATE'S — STATED HERE RATHER THAN LEFT TO BE FOUND.
    For a teammate, task-completion IS the expiry signal: the record dies when
    no listed task is `in_progress` any more. A consultant's anchor task is
    ALREADY completed at the moment the record is written, so that signal is
    spent before it can ever fire. Expiring on it would kill the record on
    arrival, which is why such records are exempt here — and the price is that
    nothing retires them except the 24h TTL upstream in `_load_records`.

    Coverage yes, expiry parity NO. Do not describe consultant coverage as
    equal to teammate coverage; an undocumented weaker guarantee reads as an
    equal one, and this is the sentence that stops that.

    The exemption keys on a flag written at RECORD time, not on the task's
    status now, because those differ: a teammate whose task has since
    completed must still expire, and only the write-time fact separates the
    two. `task_is_live` stays the unqualified liveness SSOT — the exemption
    belongs here, where the record is in hand, rather than inside a predicate
    whose whole job is to answer "is this task in_progress".
    """
    if not isinstance(tasks, list):
        return False
    if isinstance(record, dict) and record.get("anchor_completed") is True:
        return True
    listed = set(record_task_ids(record))
    return any(task_is_live(t) and str(t.get("id")) in listed for t in tasks)


def outstanding_unflagged(
    tasks: Any,
    team_name: str | None = None,
    now: datetime | None = None,
    records: list[dict] | None = None,
) -> list[dict]:
    """THE ONLY SANCTIONED READ PATH FOR SURFACING A RECORD TO A HUMAN OR AGENT.

    Applies BOTH gates, so a caller cannot surface a record by forgetting one:
      - TASK-STATUS: at least one listed task is still `in_progress`.
      - FLAGGED-WAIT: no listed `in_progress` task carries a valid wait.
    (The 24h TTL is applied upstream by `_load_records`.)

    WHY THIS EXISTS AS A NAMED SELECTOR RATHER THAN A CONVENTION. Layer 2
    reached records through `unflagged_fire`, which applies both gates; Layer 3
    went straight to `_load_records`, which applies neither. MEASURED on one
    40-minute-old record: the lead-side path surfaced it while the teammate-side
    path refused it, both because the task was `completed` AND because a valid
    wait was flagged. The lead-facing text asserts "outstanding launches and no
    flagged wait" — on the ungated path nothing evaluated the second clause, so
    the surface claimed a property the code never checked. A docstring asking
    the next consumer to remember two gates is exactly what produced that; a
    selector makes the next consumer INHERIT them.

    THE TWO GATES ARE DIFFERENT KINDS OF PREDICATE, and that is why only one
    of them could ever have moved into the loader:

      GATE A — task status — is EXPIRY. A record none of whose listed tasks
      is still `in_progress` is dead PERMANENTLY. That is a property of the
      record alone given the task store, so no consumer should be able to opt
      out of it, and it does not interact with the discharge: a discharged
      record is removed, a record on a dead task is removed, no conflict.

      GATE B — flagged wait — is SUPPRESSION. The record is still LIVE and
      still meaningful; we are declining to surface it RIGHT NOW because the
      teammate has flagged. That is a property of the MOMENT, not of the
      record. Move it into any read the discharge uses and
      `discharge_acknowledged_for_owner` never sees a flagged record to retire — the
      mechanism dies silently, green, because every gate test still passes.

    So expiry filters the READ and suppression filters the SURFACE. Do not
    "simplify" them together: they differ in lifetime, not just in placement.
    """
    rows = records if records is not None else _load_records(team_name, now=now)
    if not isinstance(tasks, list):
        # No task set means neither gate can be evaluated. Surface NOTHING
        # rather than fall back to the ungated list — this function's whole
        # purpose is that an unevaluable gate never reads as a passed gate.
        return []
    out = []
    for record in rows:
        # GATE A — EXPIRY. No live task means the record is dead permanently.
        if not has_live_listed_task(record, tasks):
            continue
        # GATE B — SUPPRESSION. The record is LIVE; we decline to surface it
        # right now because the teammate has flagged. See the note above on
        # why this one cannot move into a read the discharge uses.
        if any_listed_task_flagged(record, tasks):
            continue
        out.append(record)
    return out


def wait_covers_record(task: Any, record: Any) -> bool:
    """True iff this task's VALID wait acknowledges this record's launch.

    THE ACKNOWLEDGMENT SIGNAL IS THE FLAG, NOT THE JOB'S COMPLETION. The
    advisory exists to catch a teammate who backgrounded work and never said
    so. Once it has flagged a wait covering that launch, it has demonstrably
    associated the two, and the record has done its job — whether or not the
    job itself has finished. Nothing needs to observe the shell.

    `anchor >= registered_at` IS LOAD-BEARING AND MUST NOT BE SIMPLIFIED AWAY.
    It is what makes this precise rather than a blanket amnesty: a wait
    flagged for job 1 does NOT acquit a job 2 launched afterwards, because
    job 2's `registered_at` is later than that wait's anchor.

    SCOPE ON THE ANCHOR, NEVER ON `since`. `since` is the freshness clock and
    agents are instructed to re-SET it; comparing against it would let every
    re-stamp widen the wait forward over launches it never acknowledged,
    which annuls the comparison above. wait_scope_anchor reads the anchor and
    falls back to `since` only when there is none, so an unanchored wait
    behaves exactly as it did before rather than breaking — and the lead-side
    scan reports the missing anchor instead of this returning a quiet verdict
    on it. Callers needing that class use wait_anchor_class.

    Pure: reads a task dict and a record dict, writes nothing.
    """
    if not isinstance(task, dict) or not isinstance(record, dict):
        return False
    if classify_wait(task) is not None:
        return False  # no valid wait to acknowledge anything
    metadata = task.get("metadata")
    wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
    anchor, _ = wait_scope_anchor(wait)
    registered = parse_iso(record.get("registered_at"))
    if anchor is None or registered is None:
        return False
    return anchor >= registered


def discharge_acknowledged_for_owner(
    tasks: Any,
    owner: Any,
    team_name: str | None = None,
    now: datetime | None = None,
) -> int:
    """Drop every record that a valid wait on one of `owner`'s tasks acknowledges.
    Returns the count.

    A record is dropped iff some task owned by `owner` is listed on it and that
    task's wait covers the record's launch (`wait_covers_record`). The whole
    pass is ONE registry update, however many tasks the owner holds.

    RESIDUAL, AND IT IS NOT EMPTY. Discharge needs a TeammateIdle between the
    flag and the clear, because this runs on that event. A teammate that
    flags and clears inside ONE turn, never idling, keeps its record and can
    still draw a stale advisory later. That population correlates with
    UNNECESSARY flagging rather than with diligence — flagging exists because
    you are about to end a turn, and ending a turn is an idle — but it is
    real and is pinned by a test rather than claimed away.
    """
    if not isinstance(tasks, list) or not owner:
        return 0
    owned = {
        str(t.get("id")): t
        for t in tasks
        if isinstance(t, dict) and t.get("owner") == owner and t.get("id") is not None
    }
    if not owned:
        return 0
    dropped = 0

    def _apply(records: list[dict]) -> tuple[list[dict], bool]:
        nonlocal dropped
        # Reset per call: the state-file writer may call this twice.
        dropped = 0
        kept = []
        for record in records:
            if any(
                task_id in owned and wait_covers_record(owned[task_id], record)
                for task_id in record_task_ids(record)
            ):
                dropped += 1
                continue
            kept.append(record)
        return kept, dropped > 0

    _atomic_update_records(_apply, team_name=team_name, now=now)
    return dropped


def extend_records_for_claim(
    owner: Any,
    task_id: Any,
    team_name: str | None = None,
    now: datetime | None = None,
) -> int:
    """Add a task its owner just claimed to every live record that owner launched.
    Returns the number of records extended.

    A record lists the tasks its launcher held at LAUNCH. A task claimed later
    is not on it, so a wait flagged on that task could neither silence the
    record nor discharge it. Listing the task lets it do both.

    `registered_at` and `anchor_completed` stay as written, so a wait still
    clears only launches older than its anchor (`wait_covers_record`). Expired
    records are pruned by the read inside the update, so only live records
    gain the task. An absent registry creates no file.
    """
    if not isinstance(owner, str) or not owner or task_id is None:
        return 0
    task_id = str(task_id)
    if not task_id:
        return 0
    extended = 0

    def _apply(records: list[dict]) -> tuple[list[dict], bool]:
        nonlocal extended
        # Reset per call: the state-file writer may call this twice.
        extended = 0
        out = []
        for record in records:
            listed = record_task_ids(record)
            # `owner_role` EXCLUDES A NON-TEAMMATE ROW, for the same semantic
            # reason as the other name-keyed read (turn_end_gate's SubagentStop
            # branch): a subagent's row is not a member's row, so a member's
            # claimed task must not be appended to it — whatever that member is
            # named. Truthiness rather than equality with one role, so a role
            # added later is excluded by default and must opt in.
            if (
                record.get("agent_name") == owner
                and not record.get("owner_role")
                and task_id not in listed
            ):
                record = dict(record)
                record["task_ids"] = listed + [task_id]
                extended += 1
            out.append(record)
        return out, extended > 0

    ok = _atomic_update_records(_apply, team_name=team_name, now=now)
    return extended if ok else 0


def unflagged_fire(
    task: Any,
    records: list[dict] | None = None,
    now: datetime | None = None,
    team_name: str | None = None,
    tasks: list | None = None,
) -> tuple[bool, str | None, dict | None]:
    """Fire when in_progress + outstanding record + no listed task flagged.

    Returns (fire, wait_class, record). wait_class is set only on fire.
    Pass `tasks` (the team task list) so the R5 silencing check can see the
    record's other tasks; without it only `task` is judged.
    """
    if not isinstance(task, dict):
        return False, None, None
    # THIS GATE COVERS THIS FUNCTION'S CALLERS ONLY — Layer 2. IT IS NOT A
    # PROPERTY OF THE MODULE.
    #
    # An earlier version of this comment claimed "every consumer reaches a
    # record through this function" and named `_load_records` as a REJECTED
    # place for the predicate on that basis. MEASURED FALSE: Layer 3's
    # lead-side selector read `_load_records` directly and applied neither the
    # status gate nor the flagged-wait gate, so it surfaced records this line
    # refuses. The comment did not merely fail to prevent that — it argued
    # against the check the other consumer needed, and warned the next reader
    # off "cleaning up the duplication". A false scope claim in a comment is
    # worse than no comment, because it stops the reader looking.
    #
    # The gates now live in `outstanding_unflagged` for any read path that
    # surfaces a record. This line stays because Layer 2 is per-task and
    # reaches records through here.
    #
    # It also does NOT resolve the sticky-row defect, which is a different
    # case: a job finishing WITHIN one still-`in_progress` task leaves the
    # record live and the task status unchanged, so no status check of any
    # kind reaches it. That is what the acknowledgment discharge is for.
    if not task_is_live(task):
        return False, None, None
    record = matching_outstanding(task, records=records, now=now, team_name=team_name)
    if record is None:
        return False, None, None
    if any_listed_task_flagged(record, tasks):
        return False, None, record
    wait_class = classify_wait(task)
    if wait_class is None:
        return False, None, record
    return True, wait_class, record


def stamp_idled_at(
    task_id: str,
    now: datetime | None = None,
    team_name: str | None = None,
) -> bool:
    """Set idled_at on the first matching record that lacks it."""
    if not isinstance(task_id, str) or not task_id:
        return False
    stamp = iso_now(now)
    changed = False

    def _apply(records: list[dict]) -> tuple[list[dict], bool]:
        nonlocal changed
        # Reset per call: the state-file writer may call this twice.
        changed = False
        out = []
        for record in records:
            if task_id in record_task_ids(record) and not record.get("idled_at"):
                record = dict(record)
                record["idled_at"] = stamp
                changed = True
            out.append(record)
        return out, changed

    ok = _atomic_update_records(_apply, team_name=team_name, now=now)
    return bool(ok and changed)


def effective_since(record: Any) -> tuple[datetime | None, int]:
    """The clock Layer 3 measures against, and the window that applies to it.

    Returns (since, threshold_minutes). `idled_at` is preferred; when it is
    absent we fall back to `registered_at` with the longer window, so that a
    missed TeammateIdle cannot disable the lead surface. Before this
    fallback existed, `stamp_idled_at`'s single production call site was the
    only writer of `idled_at`, which made Layer 3 a consumer of Layer 2
    rather than a backstop for it.
    """
    if not isinstance(record, dict):
        return None, LEAD_STALE_MINUTES
    idled = parse_iso(record.get("idled_at"))
    if idled is not None:
        return idled, LEAD_STALE_MINUTES
    return parse_iso(record.get("registered_at")), LEAD_UNIDLED_STALE_MINUTES


def lead_stale(
    record: dict,
    now: datetime | None = None,
) -> bool:
    """True iff the record is older than the window its own clock carries."""
    since, threshold_minutes = effective_since(record)
    if since is None:
        return False
    now = now or utc_now()
    return (now - since).total_seconds() >= threshold_minutes * 60


def _parse_idle_counts_text(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_unflagged_idle_counts(team_name: str | None = None) -> dict:
    path = unflagged_idle_path(team_name)
    if path is None:
        return {}
    try:
        text = state_file.read_text(path, _teams_root())
    except FileNotFoundError:
        return {}
    except (OSError, TypeError, ValueError):
        return {}
    return _parse_idle_counts_text(text)


def update_unflagged_idle_counts(mutator, team_name: str | None = None) -> dict:
    """Atomic RMW of unflagged_background_idle.json. Fail-open to {}."""

    def _apply(text: str) -> "tuple[str, bool, dict]":
        counts = _parse_idle_counts_text(text)
        before = json.dumps(counts)
        updated = mutator(counts)
        if not isinstance(updated, dict):
            updated = {}
        new_text = json.dumps(updated)
        return new_text, new_text != before, updated

    try:
        path = unflagged_idle_path(team_name)
        if path is None:
            return {}
        return state_file.locked_update(path, _apply, _teams_root())
    # TypeError / ValueError are here to keep the module's stated contract —
    # "never raise on missing/corrupt files, empty team name, or MALFORMED
    # RECORDS". A counter entry whose `count` is a non-int is a malformed
    # record, and a mutator coercing it raises ValueError from inside this
    # call. MEASURED before this was added: a `{"count": "not-an-int"}` entry
    # raised straight out of here, so the docstring was false.
    # The caller's own coercion is total as well (see teammate_idle), so this
    # is the contract backstop rather than the primary defence — a mutator
    # should not depend on it to be careless.
    except (OSError, TypeError, ValueError):
        return {}


def save_unflagged_idle_counts(counts: dict, team_name: str | None = None) -> bool:
    try:
        path = unflagged_idle_path(team_name)
        if path is None:
            return False
        text = json.dumps(counts if isinstance(counts, dict) else {})
        state_file.write_text(path, text, _teams_root())
        return True
    except (OSError, TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Layer 1 — the write path.
#
# This lives here rather than in the host hook deliberately. track_files.py
# hosts the call because it is already registered on PostToolUse
# `Edit|Write|Bash`, so folding into it costs no new subprocess — but the
# host is meant to gain a gate and a call, nothing more. Identity resolution
# inside the host would be a second resolver sitting in a file whose job is
# file tracking, which is exactly the coupling the fold was supposed to
# avoid.
# --------------------------------------------------------------------------


# The launch predicate (the flag, or a command ending in a bare `&`) lives in
# `background_launch`, shared with the PreToolUse advisory. Its names are
# re-exported at the top of this module.


# Platform-supplied agent types that are NOT this plugin's agents. Hard-coded
# because they come from the harness rather than from a file we can enumerate.
# This list going stale is a real exposure — see the residual note on
# `agent_type_names_a_member`.
_PLATFORM_AGENT_TYPES = frozenset(
    {"general-purpose", "Explore", "Plan", "statusline-setup"}
)

# An Agent-tool subagent's `agent_id`.
_SUBAGENT_ID = re.compile(r"a[0-9a-f]{16}")


def _known_agent_types() -> frozenset:
    """Agent-type stems this plugin ships, DERIVED AT RUNTIME from agents/.

    Derived rather than listed so that adding an agent file cannot silently
    open a collision with a teammate name. A hard-coded copy would go stale
    the day someone adds one, and the failure would be a mis-bind rather than
    an error.
    """
    try:
        agents_dir = Path(__file__).resolve().parents[2] / "agents"
        return frozenset(p.stem for p in agents_dir.glob("*.md"))
    except (OSError, IndexError):
        return frozenset()


def agent_type_names_a_member(
    agent_type: Any, team_name: str, *, agent_id: Any = None
) -> bool:
    """True iff the frame's `agent_type` is an IDENTITY rather than a TYPE.

    MEASURED 2026-09-11 on a live in-process Agent-Teams teammate PostToolUse
    Bash frame: `agent_type` carried the teammate's own NAME, not the
    `pact-`-prefixed agentType its team config records. `agent_name` was
    absent and `agent_id` carried no `@`, so no other route resolves. See
    `tests/fixtures/role_frames.py` ::
    captured_posttooluse_teammate_inprocess_bash_background.

    The test is MEMBERSHIP in the team config the platform wrote at spawn —
    a lookup in authoritative local state, not string surgery. Step 4 already
    strips this field today; this validates the value before trusting it.

    THE FRAME'S `agent_id` SHAPE IS READ FIRST. An in-process teammate's frame
    carries "a" + `agent_type` + "-" + 16 lowercase hex, and an Agent-tool
    subagent's carries "a" + 16 lowercase hex. A subagent-shaped id is never a
    member, whatever its type. A teammate-shaped id is built from `agent_type`
    and compared whole, never parsed, and goes to membership without the deny
    set, so a member named after an agent type is not refused on its own
    frame. Any other id, or none, takes the type checks below.

    RESIDUAL, STATED RATHER THAN CLAIMED AWAY, on a frame without a recognized
    `agent_id`. `classify_session_role` treats any `agent_type` outside
    `LEAD_AGENT_TYPES` as a teammate, so a generic Agent-tool subagent reaches
    this code. The deny set below reduces the collision to perverse naming — a
    member would have to be NAMED after a real agent type, and such a member is
    refused on that frame — but an UNKNOWN FUTURE PLATFORM TYPE colliding with
    a member name remains possible, and that case fails toward MIS-BIND rather
    than silence, which is the worse direction: a launch would be attributed
    to a teammate who did not make it. This is an accepted exposure, not an
    eliminated one.

    TMUX IS UNTESTED, NOT COVERED. Under tmux the frame reportedly carries the
    real type and no `agent_id`, so this returns False and the caller falls through to the
    registry, which works there because the in-process self-guard does not
    fire. That rests on one captured PreToolUse frame and NO Bash PostToolUse
    frame. It was UNEXERCISED during development because the development
    machine had no tmux teams — which bounds the VERIFICATION, not the
    behaviour. This plugin ships to consumers who may run tmux teams, so do
    not read "no teams to test against" as "a path nobody takes".
    """
    if not isinstance(agent_type, str) or not agent_type:
        return False
    if isinstance(agent_id, str):
        if _SUBAGENT_ID.fullmatch(agent_id):
            return False
        if re.fullmatch(re.escape(f"a{agent_type}-") + "[0-9a-f]{16}", agent_id):
            return _names_a_member(agent_type, team_name)
    from .session_state import is_safe_path_component

    if not is_safe_path_component(team_name):
        return False
    if agent_type in _PLATFORM_AGENT_TYPES:
        return False
    if agent_type in _known_agent_types():
        return False
    from .pact_context import _iter_members

    return any(
        isinstance(m, dict) and m.get("name") == agent_type
        for m in _iter_members(team_name)
    )


def _names_a_member(name: Any, team: str) -> bool:
    """True iff `name` is a member in `team`'s config. `team` may come from stdin."""
    from .pact_context import _iter_members
    from .session_state import is_safe_path_component

    if not isinstance(name, str) or not name or not is_safe_path_component(team):
        return False
    return any(m.get("name") == name for m in _iter_members(team))


def frame_team_and_name(input_data: Any) -> "tuple[str, str]":
    """(team, member name) for the frame's own session, "" for each part unresolved.

    Never raises.

    The team comes from the first route that resolves:
      1. `get_team_name()`, when the session has a PACT context: the lead, and
         in-process teammates, which share the lead's session.
      2. The frame's own `team_name` and `teammate_name`, when that name is a
         member of that team. A TeammateIdle frame carries both, and a
         separate-process teammate's own process has no PACT context, so this
         is how its idle hook finds its team.
      3. The session registry entry for the frame's `session_id`.
         `session_registry.resolve` has already checked the name against that
         team's members. A separate-process teammate has no session context of
         its own, so this is the route its other hooks use.
      4. An `agent_id` of the form `name@team` whose name is a member of that
         team.
    The name comes from routes 2 to 4. Route 1 returns "": a context names the
    session's team, not which member is acting in it.
    """
    try:
        if not isinstance(input_data, dict):
            return "", ""
        from .pact_context import init as init_context
        from .session_registry import resolve as registry_resolve

        init_context(input_data)
        team = get_team_name()
        if team:
            return team, ""
        frame_team = input_data.get("team_name")
        frame_name = input_data.get("teammate_name")
        if isinstance(frame_team, str) and _names_a_member(frame_name, frame_team):
            return frame_team, frame_name
        session_id = input_data.get("session_id")
        if isinstance(session_id, str) and session_id:
            name, _, team = (registry_resolve(session_id) or "").partition("@")
            if name and team:
                return team, name
        agent_id = input_data.get("agent_id")
        if isinstance(agent_id, str):
            name, _, team = agent_id.partition("@")
            if team and _names_a_member(name, team):
                return team, name
    except Exception:
        pass
    return "", ""


def teammate_is_separate_process(team_name: Any, member_name: Any) -> bool:
    """True iff `member_name`'s `backendType` in `team_name`'s config is exactly "tmux".

    A separate-process teammate is the main session of its own process, so its
    own background completion starts its next turn; an in-process teammate's
    does not. The stall layers (the launch advisory, Layer 2 and Layer 3's
    unflagged surface) exist for a teammate that is not woken, and skip a
    teammate this returns True for.

    FALSE IS THE SAFE ANSWER, and every doubt returns it: "in-process",
    "iterm2", any other value or spelling, a missing key, a missing member, or
    an unreadable config. A wrong False sends a woken teammate one extra
    advisory; a wrong True silences the layers for a teammate that stalls.
    "iterm2" stays False until its wake behaviour is measured.
    """
    if not isinstance(team_name, str) or not isinstance(member_name, str) or not member_name:
        return False
    from .pact_context import _iter_members
    from .session_state import is_safe_path_component

    if not is_safe_path_component(team_name):
        return False
    return any(
        m.get("name") == member_name and m.get("backendType") == "tmux"
        for m in _iter_members(team_name)
    )


def teammate_launch_name(input_data: Any, team_name: str) -> str:
    """The member name of the teammate of `team_name` acting in this frame, or "".

    "" means the frame is not a teammate: the lead, an Agent-tool subagent, or
    a plain frame. The launch advisory and Layer 1 both decide through this
    (Layer 1 via `is_teammate_launch_frame`), so the advisory and the registry
    cover the same population. In order:
      1. No `agent_type`, or a lead spelling: not a teammate.
      2. `agent_type` names a member: an in-process teammate, whose frame
         carries its own name in that field. Returns that name (a
         subagent-shaped `agent_id` is refused here even then).
      3. An `agent_id` is present. `name@<this team>` returns `name`. Any other
         id, such as a bare hex id, is an Agent-tool subagent, because an
         in-process teammate already matched at step 2. No captured frame has
         the `name@team` shape: a measured separate-process teammate frame
         carries no `agent_id` and resolves at step 4.
      4. No `agent_id`, a `session_id` that is not the lead's, and a session
         registry entry for this team: a separate-process teammate. Returns
         the registry's name.
    Anything else is not a teammate.
    """
    if not isinstance(input_data, dict) or not isinstance(team_name, str) or not team_name:
        return ""
    from .pact_context import LEAD_AGENT_TYPES, _read_lead_session_id
    from .session_registry import resolve as registry_resolve

    agent_type = input_data.get("agent_type")
    if not isinstance(agent_type, str) or not agent_type or agent_type in LEAD_AGENT_TYPES:
        return ""
    if agent_type_names_a_member(
        agent_type, team_name, agent_id=input_data.get("agent_id")
    ):
        return agent_type
    agent_id = input_data.get("agent_id")
    if agent_id:
        if not isinstance(agent_id, str):
            return ""
        name, _, id_team = agent_id.partition("@")
        return name if name and id_team.lower() == team_name.lower() else ""
    session_id = input_data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return ""
    if session_id == _read_lead_session_id(team_name):
        return ""
    name, _, registry_team = (registry_resolve(session_id) or "").partition("@")
    return name if name and registry_team.lower() == team_name.lower() else ""


def is_teammate_launch_frame(input_data: Any, team_name: str) -> bool:
    """True iff the frame is a teammate of `team_name`: not the lead, and not an
    Agent-tool subagent. `teammate_launch_name` holds the steps."""
    return bool(teammate_launch_name(input_data, team_name))


def bind_launcher_identity(
    input_data: Any, team_name: str
) -> "tuple[str, str, list[str], bool] | None":
    """Return (agent_name, session_id, task_ids, anchor_completed), or None.

    None means identity is absent. `anchor_completed` is True when the owner
    held no `in_progress` task and the ids are its most recently completed one
    — the consultant case — and it is APPENDED to the tuple rather than
    inserted, so positional readers of the first three elements are unaffected.

    Steps 1-3.5 of resolve_agent_name only. `agent_type` is deliberately NOT
    type-stripped as the owner.

    THE FIELD IS POLYMORPHIC BY ROLE, NOT RANDOMLY UNRELIABLE, and the
    distinction decides when the membership match can be trusted. MEASURED:
    on TEAMMATE frames it carried the member's NAME every time — three
    teammates, two independent instruments, two operators — and on LEAD
    frames it carries the agent-type spelling (`PACT:pact-orchestrator` in
    the team whose file-edits rows were the original evidence). So it is
    consistently a name for teammates and consistently a type for the lead.
    Reading it as "sometimes one, sometimes the other, per frame" would be
    wrong and would undersell a mechanism that is deterministic per role.
    Stripping it unconditionally would therefore attribute a launch to
    whatever string happens to be there, which is why the value is VALIDATED
    against the team config instead of trusted for its shape.

    A hex agent_id is an in-process discriminator, not a teammate name.
    Refusing to guess means the registry stays silent rather than wrong.

    STEP 1 IS INERT IN EVERY TOPOLOGY MEASURED SO FAR AND STAYS ANYWAY.
    `agent_name` is absent from the in-process frame's 15 keys, and the SSOT
    records it absent under tmux too, so it may be dead everywhere — nobody
    has established that. The branch costs one dict lookup on a fail-open
    ordering, so keeping it is a cheap option on a future harness that does
    carry the field, not dead weight. Do not delete it as unreachable
    without measuring the topology you are deleting it for.

    ALL matching in_progress tasks are returned, not one. Requiring exactly
    one silently recorded nothing for a teammate holding two — and holding two
    is behaviour the pact-teachback skill explicitly permits, so the mechanism
    switched itself off for teammates following the framework's own
    instruction.

    ZERO in_progress TASKS FALLS BACK TO THE MOST RECENTLY COMPLETED ONE. This
    previously stayed a no-write, on the stated reason that "a teammate with no
    in_progress task is not inside a dispatch, so there is no task context for
    an advisory to reference". THAT REASON IS FALSE and the no-write it
    justified was the whole consultant hole: a CONSULTANT owns no in_progress
    task by definition, is a supported state, does real work, and can carry a
    wait on its completed anchor because metadata writes to a completed task
    land. So there IS a task context; the old predicate just refused to look at
    it. Only an owner with no task at all is a no-write now.
    """
    # Function-level: the whole module is imported only once a Bash frame
    # arrives, and these imports are needed only once identity is being bound.
    from .pact_context import resolve_agent_name
    from .session_registry import resolve as registry_resolve
    from .task_utils import iter_team_task_jsons

    if not isinstance(input_data, dict) or not team_name:
        return None
    session_id = input_data.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None

    named_by_name = isinstance(input_data.get("agent_name"), str) and bool(
        input_data.get("agent_name")
    )
    agent_id = input_data.get("agent_id")
    named_by_id_split = isinstance(agent_id, str) and "@" in agent_id
    # The measured in-process route: neither field above is present on a Bash
    # PostToolUse frame, and `agent_type` carries the name instead.
    named_by_membership = agent_type_names_a_member(
        input_data.get("agent_type"), team_name, agent_id=input_data.get("agent_id")
    )
    registry_name = None
    # THIS EARLY RETURN IS THE COLLAPSE PROTECTION. With no name on the frame,
    # no `@`-bearing id, and no validated membership match, the only remaining
    # route would be an UNVALIDATED type-strip of `agent_type` — which would
    # attribute a launch to whatever string that field happens to hold and
    # collapse same-type siblings onto one name. Refusing here is what makes
    # the registry silent rather than wrong, and silence is the acceptable
    # direction: a mis-bind names a teammate who did not launch the work.
    #
    # A second guard further down used to restate this. It was DEAD — reaching
    # it with every flag in the condition below false implies
    # `registry_name is not None`, because that exact combination already
    # returned here, so its conjunction
    # was unsatisfiable. Deleting it proved zero kills across the full suite.
    # Do not reintroduce one: a redundant predicate implies this line does not
    # already hold the property, which invites the next reader to delete the
    # wrong one of the two.
    if not named_by_name and not named_by_id_split and not named_by_membership:
        resolved = registry_resolve(session_id)
        if resolved and "@" in resolved:
            registry_name = resolved.split("@")[0]
        else:
            return None

    # THE VALUE THAT PASSED VALIDATION IS THE VALUE THAT GETS RECORDED.
    # `agent_type_names_a_member` validates the RAW `agent_type`; routing that
    # case back through `resolve_agent_name` would re-derive a DIFFERENT
    # string, because its Step 4 strips a `pact-` prefix. MEASURED before this
    # was fixed: a member named `pact-reviewer` validated, then bound to its
    # sibling `reviewer` and to that sibling's task — a launch attributed to a
    # teammate who did not make it, which is the unacceptable direction.
    #
    # THE RAW VALUE IS USED ON THE MEMBERSHIP ROUTE AND NOWHERE ELSE. On the
    # registry route and the `agent_name` / `@`-bearing `agent_id` routes the
    # resolved value is the correct one and `agent_type` may hold a genuine
    # type there, so letting the raw value reach those branches would move the
    # mis-bind rather than close it.
    #
    # Precedence is preserved: Steps 1 and 2 still win over the validated
    # Step-4 route, which is why they are tested before it rather than after.
    # The final branch needs no `named_by_membership` test — reaching it means
    # `registry_name` is None and neither earlier flag is set, and that
    # combination already returned at the early return above unless membership
    # matched.
    if registry_name:
        agent_name = registry_name
    elif named_by_name or named_by_id_split:
        agent_name = resolve_agent_name(input_data, team_name=team_name)
    else:
        agent_name = input_data.get("agent_type")
    if not agent_name:
        return None

    task_ids, anchor_completed = owner_anchor_tasks(
        list(iter_team_task_jsons(team_name)), agent_name
    )
    if not task_ids:
        return None
    return agent_name, session_id, task_ids, anchor_completed


def subagent_launcher_id(input_data: Any) -> str:
    """The Agent-tool subagent's own `agent_id` on this frame, or "".

    A subagent's id is "a" followed by 16 lowercase hex [LIVE] — the same shape
    `agent_type_names_a_member` refuses a membership match for.

    AN ABSENT `agent_id` NAMES NO LAUNCHER HERE, AND THAT IS THE WHOLE
    CONSERVATISM OF THIS PATH. Absence has been measured as the LEAD's own
    signature on the lead's own frames, but it is only INFERRED for an
    in-process teammate's spawn, which nobody has captured. Were that inference
    wrong, reading absence as "the lead" would record a TEAMMATE's launch as
    the lead's and refuse the lead a turn end it had every right to end — the
    same cardinal defect this recorder exists to close, arriving from the other
    side. So absence records NOTHING, which is an under-block and the
    acceptable direction.
    """
    agent_id = input_data.get("agent_id") if isinstance(input_data, dict) else None
    if not isinstance(agent_id, str):
        return ""
    return agent_id if _SUBAGENT_ID.fullmatch(agent_id) else ""


def record_background_launch(input_data: Any, now: datetime | None = None) -> bool:
    """Write one registry row for a recordable teammate or subagent launch.

    Fail-open on every path — the host calls this for its side effect only and
    must not be disturbed by anything that happens here.
    """
    if not is_background_launch(input_data):
        return False
    team_name, _name = frame_team_and_name(input_data)
    if not team_name:
        return False
    # TWO launcher populations, resolved differently and for different reasons.
    #
    # A TEAMMATE is decided by one predicate shared with the launch advisory,
    # so the two cover the same population. That predicate refuses the lead and
    # an Agent-tool subagent: a subagent would otherwise reach the session
    # registry on the LEAD's session id and be recorded against whichever
    # member that id names.
    #
    # A SUBAGENT is admitted here on its own EXPLICIT `agent_id`, and only
    # that. The shell it launches lands in the lead's background_tasks with no
    # owner and outlives the subagent, so unrecorded it is charged to the lead.
    # It is recorded UNDER THAT ID and never resolved to a member name, because
    # a subagent is not a member and guessing one would mis-bind the launch.
    subagent_id = subagent_launcher_id(input_data)
    if not subagent_id and not is_teammate_launch_frame(input_data, team_name):
        return False
    command = command_from_frame(input_data)
    # NO DURABILITY FILTER HERE, DELIBERATELY. A predicate over command text
    # DOES now run in the gate above, so be exact about what this forbids.
    #
    # What was deleted was a FILTER: it inferred program BEHAVIOUR ("will this
    # run a long time") from a word, and it SUBTRACTED from the population.
    # It caught the intended population (`npm run dev`) and also silently
    # dropped ordinary one-shot work whose text merely contains a token
    # (`pytest -k start`), and those two are indistinguishable to any matcher
    # — so the over-fire was drawn FROM the target population rather than
    # being a tunable miss rate. Both directions were measured and neither
    # was fixable by patching the pattern.
    #
    # The gate's trailing-`&` check differs on both axes: it reads shell
    # GRAMMAR, which the string does answer, and it ADDS rows. An over-fire
    # there costs one extra record, which is visible and can be discharged;
    # an over-fire in a filter costs a record nobody knows is missing. So the
    # standing rule is not "no text matching" — it is that nothing here may
    # REMOVE a launch from the registry.
    #
    # The durability question IS answerable, just not here: `intentional_wait`
    # carries it later, when the agent says what it is waiting for.
    if subagent_id:
        # No identity resolution and no task lookup: a subagent is not a member
        # and holds no task, so the id IS the identity and the row carries no
        # task ids. OWNER_ROLE_SUBAGENT holds why that is safe at every reader.
        session_id = input_data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return False
        agent_name, task_ids, anchor_completed = subagent_id, [], False
    else:
        bound = bind_launcher_identity(input_data, team_name)
        if bound is None:
            return False
        agent_name, session_id, task_ids, anchor_completed = bound
    # The harness's own id for this job, which lets the turn-end gate match a
    # running job to the teammate that launched it. Every tool_response key is
    # optional: a shell `&` launch carries none, and `_sanitize_record` keeps
    # the field only when it is a non-empty string.
    tool_response = input_data.get("tool_response")
    harness_task_id = (
        tool_response.get("backgroundTaskId") if isinstance(tool_response, dict) else None
    )
    return append_record(
        {
            "agent_name": agent_name,
            "session_id": session_id,
            "task_ids": task_ids,
            # Write-time fact, not a status to be re-read later: it records
            # that this owner had no in_progress task when the launch
            # happened, which is what exempts the row from completion-expiry
            # in has_live_listed_task. A teammate whose task completes AFTER
            # this point must still expire, and only the write-time value
            # separates those two.
            "anchor_completed": anchor_completed,
            # Present only on a subagent's row; absence is a teammate row.
            "owner_role": OWNER_ROLE_SUBAGENT if subagent_id else None,
            "command": command,
            "harness_task_id": harness_task_id,
            # Every clock on this path takes `now`. A bare iso_now() here
            # falls through to canonical_since(), which reads datetime.now
            # DIRECTLY and is not reachable from this module's utc_now — so an
            # injected clock would prune at `now` while stamping at real-now,
            # manufacturing a divergence the system clock cannot produce.
            "registered_at": iso_now(now),
        },
        team_name=team_name,
        now=now,
    )
