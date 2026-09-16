"""
Location: pact-plugin/hooks/shared/turn_end_gate.py
Summary: The turn-end background-work decision: who is ending the turn, which
         running background jobs are theirs and unacknowledged, and whether
         each has already been reported once.
Used by: hooks/stop_background_gate.py (Stop: the lead and separate-process
         teammates) and hooks/validate_handoff.py (SubagentStop: in-process
         teammates), so each turn end gets exactly one PACT decision.

`evaluate` reads and never writes. The entry point that prints a block then
calls `mark_told`, then `write_trace`, so an allowed stop never uses up a
job's one-time report.

Identification is positive-only. A plain session, an Agent-tool subagent and
any frame this module does not recognise resolve to no role, and the stop is
allowed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import background_work, pact_context, state_file, turn_end_jobs
from .paths import get_claude_config_dir
from .session_journal import append_event_checked, make_event
from .task_utils import iter_team_task_jsons

TURN_END_EVENTS = ("Stop", "SubagentStop")
TRACE_EVENT = "background_stop_gate"
TOLD_FILENAME = "background-stop-told.json"
IN_PROCESS_TEAMMATE_KIND = "in_process_teammate"

ROLE_LEAD = "lead"
ROLE_TEAMMATE = "teammate"
ROLE_UNRESOLVED = "unresolved"

VERDICT_BLOCK = "block"
VERDICT_ALLOW_FLAGGED = "allow_flagged"
VERDICT_ALLOW_ALREADY_TOLD = "allow_already_told"
VERDICT_ALLOW_LOOP_GUARD = "allow_loop_guard"
VERDICT_ALLOW_ROLE_UNRESOLVED = "allow_role_unresolved"
VERDICT_ALLOW_ERROR = "allow_error"
VERDICT_ALLOW_NO_JOB = "allow_no_job"

MAX_LISTED_JOBS = 5
JOB_LABEL_MAX_CHARS = 80
META_MAX_BYTES = 64 * 1024

_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f]+")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

LEAD_BLOCK_TEXT = (
    "Background work is still running in this session and nothing is "
    "scheduled to wake you: {jobs}. Its completion notice usually starts your "
    "next turn, but a notice can go undelivered. Before ending this turn, do "
    "one of these: wait for it to finish, schedule a wake with CronCreate, or "
    "stop it if it is no longer needed. If a job listed here belongs to a "
    "teammate, end the turn again. Each job is reported once."
)
TEAMMATE_BLOCK_TEXT = (
    "You are ending your turn while background work you started is still "
    "running and no wait covers it: {jobs}. Its completion notice will not "
    "wake you. Before ending this turn, either wait for the result, or SET "
    "metadata.intentional_wait on every task the wait covers, naming this "
    "job, so the lead knows to wake you. Each job is reported once."
)
SEPARATE_PROCESS_TEAMMATE_BLOCK_TEXT = (
    "Background work you started is still running and no wait covers it: "
    "{jobs}. Its completion notice usually starts your next turn, but a notice "
    "can go undelivered. Before ending this turn, do one of these: wait for it "
    "to finish, schedule a wake with CronCreate, stop it if it is no longer "
    "needed, or SET metadata.intentional_wait on every task the wait covers, "
    "naming this job. Each job is reported once."
)


@dataclass
class Verdict:
    """One turn end's background-work decision."""

    role: str
    verdict: str
    running: int
    ids: list = field(default_factory=list)
    cause: str = ""
    reason: str = ""
    session_dir: str = ""

    @property
    def blocks(self) -> bool:
        return self.verdict == VERDICT_BLOCK


def evaluate(input_data: Any) -> "Verdict | None":
    """The verdict for one turn end, or None when no job of any counted type is running.

    Reads only, and never raises: an exception becomes `allow_error`. While
    `stop_hook_active` is set the verdict is `allow_loop_guard`, but its ids
    and reason are kept, so a caller degrading several reasons can still
    report this one.
    """
    running = turn_end_jobs.running_jobs(input_data)
    if not running or input_data.get("hook_event_name") not in TURN_END_EVENTS:
        return None
    try:
        verdict = _decide(input_data, running)
    except Exception as exc:  # the stop is allowed whatever went wrong here
        verdict = Verdict(
            ROLE_UNRESOLVED, VERDICT_ALLOW_ERROR, len(running),
            cause=type(exc).__name__, session_dir=_session_dir_or_empty(input_data),
        )
    if input_data.get("stop_hook_active"):
        verdict.verdict = VERDICT_ALLOW_LOOP_GUARD
    return verdict


def _job_types_for(role: str, event: Any) -> frozenset:
    """The job types this role counts on this turn-end event."""
    if role == ROLE_LEAD:
        return turn_end_jobs.LEAD_JOB_TYPES
    if event == "SubagentStop":
        return turn_end_jobs.IN_PROCESS_TEAMMATE_JOB_TYPES
    return turn_end_jobs.OWN_PROCESS_JOB_TYPES


def _block_text_for(role: str, event: Any) -> str:
    """The block text for this role on this turn-end event."""
    if role == ROLE_LEAD:
        return LEAD_BLOCK_TEXT
    if event == "SubagentStop":
        return TEAMMATE_BLOCK_TEXT
    return SEPARATE_PROCESS_TEAMMATE_BLOCK_TEXT


def _decide(input_data: dict, running: list) -> Verdict:
    pact_context.init(input_data)
    role, name, team = resolve_role(input_data)
    if role == ROLE_UNRESOLVED:
        # Only an existing session context names a directory here, so an
        # unidentified session never has a session folder created for it.
        return Verdict(
            role, VERDICT_ALLOW_ROLE_UNRESOLVED, len(running),
            session_dir=pact_context.get_session_dir(),
        )
    event = input_data.get("hook_event_name")
    session_dir = session_dir_for(input_data)
    jobs = turn_end_jobs.running_jobs(input_data, _job_types_for(role, event))
    count = len(jobs)
    if not jobs:
        return Verdict(role, VERDICT_ALLOW_NO_JOB, count, session_dir=session_dir)
    crons = input_data.get("session_crons")
    # Stop only: a SubagentStop frame carries the lead process's crons, which
    # wake the lead, not the in-process teammate ending its turn.
    if event == "Stop" and isinstance(crons, list) and crons:
        return Verdict(
            role, VERDICT_ALLOW_FLAGGED, count, cause="session_cron", session_dir=session_dir
        )
    candidates = _candidates(input_data, role, name, team, jobs)
    if not candidates:
        return Verdict(role, VERDICT_ALLOW_FLAGGED, count, session_dir=session_dir)
    if not session_dir:
        # Without the told-once record every later turn end would block again.
        return Verdict(role, VERDICT_ALLOW_ROLE_UNRESOLVED, count, cause="no_session_dir")
    told = read_told_ids(session_dir)
    new = [e for e in candidates if e["id"] not in told]
    if not new:
        return Verdict(
            role, VERDICT_ALLOW_ALREADY_TOLD, count,
            ids=[e["id"] for e in candidates], session_dir=session_dir,
        )
    text = _block_text_for(role, event)
    return Verdict(
        role, VERDICT_BLOCK, count,
        ids=[e["id"] for e in new],
        reason=text.format(jobs=describe_jobs(new)),
        session_dir=session_dir,
    )


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def session_dir_for(input_data: dict) -> str:
    """This frame's session directory, or "".

    The session context file names it for the lead, and for in-process
    teammates, which share the lead's session. A separate-process teammate
    has no context file, so its directory is rebuilt from CLAUDE_PROJECT_DIR
    and its own session id with the same path builder.
    """
    session_dir = pact_context.get_session_dir()
    if session_dir:
        return session_dir
    session_id = input_data.get("session_id")
    if not isinstance(session_id, str):
        return ""
    return pact_context.reconstruct_session_dir(
        os.environ.get("CLAUDE_PROJECT_DIR", ""), session_id
    )


def _session_dir_or_empty(input_data: dict) -> str:
    """The existing context's session directory for an error trace, or ""."""
    try:
        return pact_context.get_session_dir()
    except Exception:
        return ""


def resolve_role(input_data: dict) -> "tuple[str, str, str]":
    """(role, member name, team) for a turn-end frame."""
    if input_data.get("hook_event_name") == "SubagentStop":
        team = pact_context.get_team_name()
        name = teammate_identity(input_data, team)
        return (ROLE_TEAMMATE, name, team) if name else (ROLE_UNRESOLVED, "", team)
    return _stop_role(input_data)


def _stop_role(input_data: dict) -> "tuple[str, str, str]":
    agent_type = input_data.get("agent_type")
    agent_id = input_data.get("agent_id")
    if not _is_text(agent_type) and not _is_text(agent_id):
        return ROLE_UNRESOLVED, "", ""  # a plain session
    # A separate-process teammate has no session context of its own, so its
    # team and name come from the session registry through this resolver.
    team, frame_name = background_work.frame_team_and_name(input_data)
    if not team:
        return ROLE_UNRESOLVED, "", ""
    if _is_text(agent_id):
        # Only a member's `name@team` counts. A subagent-shaped id should not
        # reach Stop, and this hook never guesses.
        member, _, id_team = agent_id.partition("@")
        if id_team.lower() == team.lower() and _is_member(member, team):
            return ROLE_TEAMMATE, member, team
        return ROLE_UNRESOLVED, "", team
    session_id = input_data.get("session_id")
    if pact_context.is_lead(input_data):
        return ROLE_LEAD, "", team
    # Nothing else in the lead's own process ends on Stop, so the lead session
    # is the lead whatever its agent_type spelling.
    if _is_text(session_id) and session_id == pact_context._read_lead_session_id(team):
        return ROLE_LEAD, "", team
    if _is_member(frame_name, team):
        return ROLE_TEAMMATE, frame_name, team
    return ROLE_UNRESOLVED, "", team


def teammate_identity(input_data: Any, team: str) -> str:
    """The member name of an in-process teammate ending its turn, or "".

    Either signal is enough: the platform's subagent metadata, which names the
    teammate whatever the frame's agent_type holds, or an agent_type that is
    itself a member's name. An Agent-tool subagent is refused by its agent_id
    shape even when its agent_type matches a member's name.
    """
    if not isinstance(input_data, dict) or not _is_text(team):
        return ""
    meta = subagent_metadata(input_data)
    name = meta.get("name")
    team_name = meta.get("teamName")
    if (
        meta.get("taskKind") == IN_PROCESS_TEAMMATE_KIND
        and _is_text(team_name)
        and team_name.lower() == team.lower()
        and _is_member(name, team)
    ):
        return name
    agent_type = input_data.get("agent_type")
    if background_work.agent_type_names_a_member(
        agent_type, team, agent_id=input_data.get("agent_id")
    ):
        return agent_type
    return ""


def subagent_metadata(input_data: dict) -> dict:
    """The platform's `agent-<agent_id>.meta.json` for a subagent frame, or {}.

    Looked for beside `agent_transcript_path`, then in the session's
    `subagents` folder beside `transcript_path`. Read only from under the
    config root's `projects` folder, and only when at most META_MAX_BYTES.
    """
    agent_id = input_data.get("agent_id")
    if not isinstance(agent_id, str) or not _SAFE_ID.match(agent_id):
        return {}
    filename = f"agent-{agent_id}.meta.json"
    candidates = []
    agent_transcript = input_data.get("agent_transcript_path")
    if _is_text(agent_transcript):
        candidates.append(Path(agent_transcript).parent / filename)
    transcript = input_data.get("transcript_path")
    session_id = input_data.get("session_id")
    if _is_text(transcript) and isinstance(session_id, str) and _SAFE_ID.match(session_id):
        candidates.append(Path(transcript).parent / session_id / "subagents" / filename)
    root = get_claude_config_dir() / "projects"
    for path in candidates:
        try:
            if os.lstat(path).st_size > META_MAX_BYTES:
                return {}
            data = json.loads(state_file.read_text(path, root))
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def _is_member(name: Any, team: str) -> bool:
    if not _is_text(name):
        return False
    return any(
        isinstance(m, dict) and m.get("name") == name
        for m in pact_context._iter_members(team)
    )


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


def _candidates(input_data: dict, role: str, name: str, team: str, running: list) -> list:
    """The running jobs this role must account for before ending the turn."""
    records = background_work.load_records_for_discharge(team)
    if role == ROLE_LEAD:
        # background_tasks lists every job in the process with no owner; a
        # registry record is what marks a job as someone else's launch — a
        # teammate's, or a subagent's.
        #
        # THIS BRANCH SUBTRACTS EVERY ROW CARRYING A JOB ID AND NEVER READS
        # `owner_role`. That is safe only because no writer produces a row the
        # lead owns. A writer that adds one must filter those rows out here, or
        # the lead is acquitted of its own running job.
        #
        # JOB-ID REUSE, AND WHICH WAY IT FAILS HERE. The match is on the job id
        # alone, and ids are not established unique: they look random rather
        # than like counters, but uniqueness has not been measured. If a row
        # still inside the TTL carries an id that a job in this process's list
        # reuses, that job is subtracted. This branch only ever removes
        # candidates, so a reused id can only UNDER-block: it acquits the lead of
        # that job.
        teammate_jobs = {r["harness_task_id"] for r in records if r.get("harness_task_id")}
        return [e for e in running if e["id"] not in teammate_jobs]
    if input_data.get("hook_event_name") == "SubagentStop":
        # The lead process's whole list: only this teammate's recorded jobs count.
        #
        # `owner_role` EXCLUDES A NON-TEAMMATE ROW FROM THIS NAME MATCH, and the
        # reason is semantic rather than defensive: a subagent's row is NOT a
        # member's row, so matching it by MEMBER NAME is wrong whatever any
        # member happens to be called. This and `extend_records_for_claim` are
        # the two reads that compare a row's name with a member's, and both
        # exclude the row. The store's other name reads, in missed_wake_scan,
        # are safe for a different reason: they see only rows that
        # `outstanding_unflagged` passes, and its expiry gate refuses a row
        # with no task ids unless `anchor_completed` is set, which the recorder
        # never sets on a subagent's row.
        #
        # The test is truthiness, not equality with one role, so a role added
        # later is excluded here by default. That is the safe direction: a new
        # owner role must opt IN to being treated as a member.
        by_job = {
            r["harness_task_id"]: r for r in records
            if r.get("agent_name") == name
            and not r.get("owner_role")
            and r.get("harness_task_id")
        }
        # JOB-ID REUSE HERE FAILS THE OTHER WAY. If a row of THIS teammate still
        # inside the TTL carries an id that another agent's running job in this
        # process's list reuses, that job enters this teammate's candidates, and
        # unless a wait anchored no earlier than that row's launch covers it, the
        # teammate is refused its turn end over work it did not launch — an
        # OVER-block.
        running = [e for e in running if e["id"] in by_job]
    else:
        # JOB-ID REUSE HERE ALSO OVER-BLOCKS, BY A DIFFERENT MECHANISM. This
        # process lists only its own jobs, so no other agent's job can enter its
        # candidates. Instead, a row of ANY owner still inside the TTL whose id
        # matches one of this process's running jobs makes that job coverable
        # only by a wait anchored no earlier than the row's launch, where with no
        # row any valid wait covers it. A reused id can only raise that bar,
        # never lower it.
        by_job = {r["harness_task_id"]: r for r in records if r.get("harness_task_id")}
    waiting = _tasks_with_valid_wait(team, name)
    return [e for e in running if not _covered(waiting, by_job.get(e["id"]))]


def _tasks_with_valid_wait(team: str, name: str) -> list:
    tasks = list(iter_team_task_jsons(team))
    anchor_ids, _anchor_completed = background_work.owner_anchor_tasks(tasks, name)
    return [
        t for t in tasks
        if str(t.get("id")) in anchor_ids and background_work.classify_wait(t) is None
    ]


def _covered(waiting: list, record: "dict | None") -> bool:
    """A job is covered by a valid wait; a recorded one only by a wait anchored after its launch."""
    if record is None:
        return bool(waiting)
    return any(background_work.wait_covers_record(t, record) for t in waiting)


def describe_jobs(entries: list) -> str:
    """The jobs as the block text lists them: at most MAX_LISTED_JOBS, then a count."""
    labels = [_job_label(e) for e in entries[:MAX_LISTED_JOBS]]
    extra = len(entries) - MAX_LISTED_JOBS
    if extra > 0:
        labels.append(f"and {extra} more")
    return ", ".join(labels)


def _job_label(entry: dict) -> str:
    text = _clean(entry.get("description")) or _clean(entry.get("command"))
    job_type = _clean(entry.get("type")) or "unknown"
    return f"`{_clean(entry.get('id'))}` ({job_type}): {text}"[:JOB_LABEL_MAX_CHARS]


def _clean(value: Any) -> str:
    return _UNPRINTABLE.sub(" ", value).strip() if isinstance(value, str) else ""


# --------------------------------------------------------------------------
# Told-once record and trace
# --------------------------------------------------------------------------


def _told_path(session_dir: str) -> Path:
    return Path(session_dir) / TOLD_FILENAME


def _sessions_root() -> Path:
    return get_claude_config_dir() / "pact-sessions"


def _parse_told(text: str) -> list:
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return []
    ids = data.get("ids") if isinstance(data, dict) else None
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def read_told_ids(session_dir: str) -> set:
    """Job ids already reported in this session. Raises OSError other than absence."""
    try:
        return set(_parse_told(state_file.read_text(_told_path(session_dir), _sessions_root())))
    except FileNotFoundError:
        return set()


def mark_told(verdict: Verdict) -> bool:
    """Record a block's job ids as reported. Call only after the block is printed.

    Never raises. Does nothing for a verdict that did not block, so a stop
    allowed under stop_hook_active leaves its jobs reportable.
    """
    if not verdict.blocks or not verdict.ids or not verdict.session_dir:
        return False

    def apply(text: str) -> "tuple[str, bool, bool]":
        told = _parse_told(text)
        added = [i for i in verdict.ids if i not in told]
        return json.dumps({"ids": told + added}), bool(added), bool(added)

    try:
        return bool(
            state_file.locked_update(_told_path(verdict.session_dir), apply, _sessions_root())
        )
    except (OSError, TypeError, ValueError):
        return False


def write_trace(verdict: Verdict) -> bool:
    """Append this verdict's background_stop_gate journal event. Never raises."""
    try:
        fields = {"role": verdict.role, "verdict": verdict.verdict, "running": verdict.running}
        if verdict.ids:
            fields["ids"] = list(verdict.ids)
        if verdict.cause:
            fields["cause"] = verdict.cause
        return append_event_checked(
            make_event(TRACE_EVENT, **fields), TRACE_EVENT,
            session_dir=verdict.session_dir or None,
        )
    except Exception:
        return False
