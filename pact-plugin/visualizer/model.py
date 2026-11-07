"""Location: pact-plugin/visualizer/model.py
Summary: The derivation engine: turns a tolerant journal read plus the team
         config / cloud roster / task store into the contract's full session
         model (phases, agents, tasks, events, handoffs, gates, variety,
         metrics) and the cheap /api/sessions listing and /api/health payload.
Used by: visualizer.server (all API routes), visualizer.demo (the scripted
         model runs through the SAME derivation), visualizer.cli (--list).

Every function is pure over its inputs: no network, no writes, and the
``now`` clock is injectable so tests can pin liveness and generated_at.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from . import classify, discovery, journal, roster
from .settings import (
    APP_NAME,
    DEFAULT_SESSIONS_LIMIT,
    MAX_AGENTS,
    MAX_ALERTS,
    MAX_CHECKPOINTS,
    MAX_COMMITS,
    MAX_DETAIL_CHARS,
    MAX_EVENTS,
    MAX_FIELD_CHARS,
    MAX_FINDINGS,
    MAX_GATE_RULES,
    MAX_GATE_TOOLS,
    MAX_HANDOFFS,
    MAX_HEALTH_FAILURES,
    MAX_REVIEWS,
    MAX_TASKS,
    MAX_VARIETY_HISTORY,
    MAX_WARNINGS,
    PLUGIN_ROOT,
    SESSION_PREFIX_MIN,
    VERSION,
    normalize_variety,
    ORCHESTRATION_MARKER_TYPES,
)

PHASE_ORDER: tuple[str, ...] = ("PREPARE", "ARCHITECT", "CODE", "TEST")

# Event types that make a run an orchestration rather than a bare session.
# The set itself lives in settings (ORCHESTRATION_MARKER_TYPES) so the cheap
# session summary reads the SAME definition; this alias keeps model-internal
# references readable.
_ORCHESTRATION_TYPES = ORCHESTRATION_MARKER_TYPES

# Events a lead drives directly: they carry no agent field in the journal, so
# without this set the lead would look idle while the run it commands is full
# of activity. Used only for the lead's crew statistics, never to rewrite an
# event's own agent field.
_LEAD_EVENT_TYPES = frozenset(
    {
        "session_start",
        "session_end",
        "phase_transition",
        "checkpoint",
        "dispatch_decision",
        "lifecycle_decision",
        "gate_health",
        "journal_emit_skipped",
        "pr_ready",
        "review_dispatch",
        "compaction_attributed",
        "team_realigned",
        "variety_assessed",
        "session_paused",
        "session_refreshed",
        "session_consolidated",
    }
)



def utcnow(now: datetime | None = None) -> datetime:
    """The reference clock: injected in tests, wall clock in production."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def iso_utc(moment: datetime | None) -> str | None:
    """Render a datetime as the journal's ``YYYY-MM-DDTHH:MM:SSZ`` form."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: object) -> datetime | None:
    """Parse a journal timestamp (ISO-8601 with Z accepted), or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if len(text) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _basename(value: object) -> str:
    """Final component of a path-like string ("" for non-strings)."""
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


# The label alphabet the API exposes for a project. The value ultimately
# comes from a DIRECTORY NAME the operator chose, so it is constrained here
# rather than trusted by every future consumer (select options, CLI output,
# docs snippets). Anything else is replaced, and the caller adds a marker.
_PROJECT_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def project_label(raw: object) -> tuple[str, bool]:
    """Constrain a project label to ``[A-Za-z0-9._-]``.

    Location: model.project_label.
    Summary: Returns (label, conformed). ``conformed`` is False when the raw
             value carried any character outside the alphabet, which is the
             caller's signal to mark the entry — the session is never dropped
             and the label is never silently rewritten without a trace.
    Used by: build_sessions_list.
    """
    text = raw if isinstance(raw, str) else ""
    label = _PROJECT_LABEL_UNSAFE.sub("_", text)
    return label, label == text


def _text(value: object, limit: int = 200) -> str:
    """A bounded plain-text string from any scalar ("" otherwise)."""
    return classify.as_text(value, limit)


def _int(value: object, default: int | None = None) -> int | None:
    """Bounded integer coercion delegated to the classifier helper."""
    return classify.as_int(value, default)


def _list(value: object) -> list:
    """A JSON array as a Python list ([] for anything else)."""
    return value if isinstance(value, list) else []


def _mapping(value: object) -> Mapping[str, Any]:
    """A JSON object as a Mapping ({} for anything else)."""
    return value if isinstance(value, Mapping) else {}


def _normalize_phase(value: object) -> str:
    """Upper-case phase token, or "" when absent."""
    text = _text(value, 40).upper()
    return text


def _session_start(events: Sequence[journal.RawEvent]) -> Mapping[str, Any] | None:
    """The first session_start event's payload, or None."""
    for raw in events:
        if raw.data.get("type") == "session_start":
            return raw.data
    return None


def _last_of_type(
    events: Sequence[journal.RawEvent], event_type: str
) -> Mapping[str, Any] | None:
    """The last event payload of a type, or None."""
    for raw in reversed(events):
        if raw.data.get("type") == event_type:
            return raw.data
    return None


def _phase_items(events: Sequence[journal.RawEvent]) -> dict:
    """Derive the phases block from phase_transition events.

    Location: model._phase_items.
    Summary: Builds the canonical four-phase track plus any extra phase seen.
             A repeat ``started`` after a terminal status increments ``run``
             (the rerun marker); a ``skipped`` transition carries its reason.
             ``current`` is the phase of the most recent transition, which is
             how the dev-demo exemplar reports TEST after the final phase
             completed.
    Used by: derive_phases, build_model.
    """
    items: dict[str, dict] = {}
    order: list[str] = list(PHASE_ORDER)
    last_phase: str = ""
    for raw in events:
        data = raw.data
        if data.get("type") != "phase_transition":
            continue
        phase = _normalize_phase(data.get("phase"))
        if not phase:
            continue
        if phase not in order:
            order.append(phase)
        item = items.get(phase)
        if item is None:
            item = {
                "name": phase,
                "family": phase,
                "status": "pending",
                "started_seq": None,
                "ended_seq": None,
                "started_at": None,
                "ended_at": None,
                "run": 0,
                "skip_reason": "",
            }
            items[phase] = item
        status = _text(data.get("status"), 20).lower()
        ts = _text(data.get("ts"), 64) or None
        last_phase = phase
        if status in {"started", "active"}:
            item["run"] = int(item["run"]) + 1
            item["started_seq"] = raw.seq
            item["started_at"] = ts
            item["status"] = "active"
        elif status == "completed":
            if int(item["run"]) == 0:
                item["run"] = 1
            item["ended_seq"] = raw.seq
            item["ended_at"] = ts
            item["status"] = "completed"
        elif status == "skipped":
            if int(item["run"]) == 0:
                item["run"] = 1
            item["ended_seq"] = raw.seq
            item["ended_at"] = ts
            item["status"] = "skipped"
            item["skip_reason"] = _text(
                data.get("skip_reason") or data.get("reason"), MAX_FIELD_CHARS
            )
    phase_items: list[dict] = []
    for name in order:
        item = items.get(name)
        if item is None:
            item = {
                "name": name,
                "family": name,
                "status": "pending",
                "started_seq": None,
                "ended_seq": None,
                "started_at": None,
                "ended_at": None,
                "run": 0,
                "skip_reason": "",
            }
        phase_items.append(item)
    completed = [item["name"] for item in phase_items if item["status"] == "completed"]
    pending = [item["name"] for item in phase_items if item["status"] == "pending"]
    return {
        "order": order,
        "current": last_phase or None,
        "completed": completed,
        "pending": pending,
        "items": phase_items,
    }


def derive_phases(events: Sequence[journal.RawEvent]) -> dict:
    """Public alias for the phases derivation (used by tests/demo)."""
    return _phase_items(events)


class _AgentAccumulator:
    """Mutable per-agent statistics used by derive_agents.

    Location: model._AgentAccumulator.
    Summary: Counts feed events, dispatches, handoffs and findings, tracks the
             first/last sighting and the latest event, and receives the
             phase-attributed commit counts from the commit pass.
    Used by: derive_agents only.
    """

    __slots__ = (
        "seed",
        "events",
        "dispatches",
        "handoffs",
        "commits",
        "findings",
        "first_seq",
        "last_seq",
        "first_ts",
        "last_ts",
        "latest",
        "task_id",
        "phase",
    )

    def __init__(self, seed: roster.MemberSeed | None) -> None:
        self.seed = seed
        self.events = 0
        self.dispatches = 0
        self.handoffs = 0
        self.commits = 0
        self.findings = 0
        self.first_seq: int | None = None
        self.last_seq: int | None = None
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.latest: dict | None = None
        self.task_id = ""
        self.phase = ""

    def observe(self, event: Mapping[str, Any]) -> None:
        """Fold one classified event into the accumulator."""
        event_type = event.get("type")
        self.events += 1
        if event_type == "agent_dispatch":
            self.dispatches += 1
        elif event_type == "agent_handoff":
            self.handoffs += 1
        elif event_type == "review_finding":
            self.findings += 1
        seq = event.get("seq")
        if isinstance(seq, int):
            if self.first_seq is None:
                self.first_seq = seq
            self.last_seq = seq
        ts = event.get("ts")
        if isinstance(ts, str) and ts:
            if self.first_ts is None:
                self.first_ts = ts
            self.last_ts = ts
        if event_type in {
            "agent_dispatch",
            "agent_handoff",
            "teachback_ack",
            "task_metadata_snapshot",
            "remediation",
        }:
            task_id = event.get("task_id")
            if isinstance(task_id, str) and task_id:
                self.task_id = task_id
        phase = event.get("phase")
        if isinstance(phase, str) and phase:
            self.phase = phase
        self.latest = {
            "seq": event.get("seq"),
            "ts": event.get("ts"),
            "type": event_type,
            "summary": _text(event.get("title"), 120),
        }


def derive_agents(
    events: Sequence[Mapping[str, Any]],
    team_info: roster.TeamInfo,
    *,
    lead_name: str = "team-lead",
    meta: Mapping[str, Any] | None = None,
    agent_seeds: Sequence[roster.MemberSeed] | None = None,
) -> tuple[list[dict], int]:
    """Derive the agents array and the full (pre-cap) agent count.

    Location: model.derive_agents.
    Summary: Seeds the roster from the demo seeds (when supplied), else the
             synthesized lead + team config members + cloud roster intents;
             folds every event that names an agent into stats; attributes
             agent-less commit events to the most recent dispatch in the same
             phase; then appends observed-only agents in first-seen order.
             Returns (capped list, total count).
    Used by: derive_model, tests.
    """
    settings_meta = meta or {}
    lead_address = _text(settings_meta.get("lead_address"), 200)
    if lead_address in {"team-lead", "main"}:
        lead_name = lead_address
    seeds: list[roster.MemberSeed] = []
    if agent_seeds is not None:
        seeds.extend(agent_seeds)
    else:
        if team_info.known or team_info.cloud or _text(settings_meta.get("team"), 120):
            seeds.append(
                roster.MemberSeed(
                    name=lead_name,
                    role="lead",
                    agent_type=lead_name,
                    color=roster.color_for_role("lead"),
                    model="",
                    cloud=bool(settings_meta.get("cloud")),
                    kind="lead",
                    source="session",
                )
            )
        seeds.extend(team_info.members)
        seeds.extend(team_info.intents)

    # Every spelling of the lead collapses onto ONE entry: the first lead
    # seed's name (the synthesized lead, named by lead_address, when there is
    # one). A config member with agentType team-lead, or a member or event
    # named team-lead/main/lead_address, is that same lead.
    def is_lead_seed(seed: roster.MemberSeed) -> bool:
        return (
            seed.kind == "lead"
            or seed.agent_type == "team-lead"
            or seed.name.strip() in {lead_name, "team-lead", "main"}
        )

    lead_aliases = {lead_name, "team-lead", "main"}
    lead_key: str | None = None
    order: list[str] = []
    seed_by_name: dict[str, roster.MemberSeed] = {}
    for seed in seeds:
        name = seed.name.strip()
        if not name or name in seed_by_name:
            continue
        if is_lead_seed(seed):
            lead_aliases.add(name)
            if lead_key is not None:
                continue
            lead_key = name
        seed_by_name[name] = seed
        order.append(name)

    accumulators: dict[str, _AgentAccumulator] = {
        name: _AgentAccumulator(seed_by_name[name]) for name in order
    }
    for event in events:
        agent = event.get("agent")
        name = agent if isinstance(agent, str) and agent else ""
        if not name:
            continue
        if name in lead_aliases:
            if lead_key is None:
                lead_key = name
            name = lead_key
        accumulator = accumulators.get(name)
        if accumulator is None:
            accumulator = _AgentAccumulator(None)
            accumulators[name] = accumulator
            order.append(name)
        accumulator.observe(event)
    # Fold lead-driven events (which carry no agent) into the lead's stats so
    # the crew panel shows the commander acting, not idle.
    lead_key = next(
        (
            name
            for name in order
            if accumulators[name].seed is not None
            and accumulators[name].seed.kind == "lead"
        ),
        None,
    )
    if lead_key is not None:
        lead_accumulator = accumulators[lead_key]
        for event in events:
            if event.get("agent"):
                continue
            if event.get("type") in _LEAD_EVENT_TYPES:
                lead_accumulator.observe(event)

    # Most commit events carry no agent of their own, so attribute each to the
    # most recent dispatch in the same phase (the documented design): a commit
    # before any same-phase dispatch stays unattributed rather than guessing.
    # A commit that DOES name an agent is attributed to it directly — the
    # first pass already opened its accumulator, and nothing else counts
    # commits.
    latest_dispatch_by_phase: dict[str, str] = {}
    for event in events:
        event_type = event.get("type")
        if event_type == "agent_dispatch":
            dispatched = event.get("agent")
            if isinstance(dispatched, str) and dispatched:
                latest_dispatch_by_phase[str(event.get("phase") or "")] = dispatched
        elif event_type == "commit":
            own_agent = event.get("agent")
            attributed = (
                own_agent
                if isinstance(own_agent, str) and own_agent
                else latest_dispatch_by_phase.get(str(event.get("phase") or ""))
            )
            accumulator = accumulators.get(attributed) if attributed else None
            if accumulator is not None:
                accumulator.commits += 1

    ended = _text(settings_meta.get("status"), 20) not in {"", "live"}
    agents: list[dict] = []
    for name in order:
        accumulator = accumulators[name]
        seed = accumulator.seed
        role = seed.role if seed is not None else name
        agent_type = seed.agent_type if seed is not None else ""
        is_lead = (
            (seed is not None and (seed.kind == "lead" or seed.agent_type == "team-lead"))
            or name == lead_name
            or name in {"team-lead", "main"}
        )
        if is_lead:
            role, kind, color = "lead", "lead", roster.color_for_role("lead")
        else:
            kind = seed.kind if seed is not None else "specialist"
            color = (
                seed.color
                if seed is not None and seed.color
                else roster.color_for_role(role)
            )
        latest = accumulator.latest
        if ended:
            state = "done" if accumulator.events > 0 else "standby"
        elif accumulator.events == 0:
            state = "standby"
        elif latest is not None and latest.get("type") == "agent_handoff":
            state = "done"
        else:
            state = "active"
        agents.append(
            {
                "name": name,
                "role": role,
                "agent_type": agent_type,
                "color": color or roster.color_for_role(role),
                "model": seed.model if seed is not None else "",
                "cloud": bool(seed.cloud) if seed is not None else False,
                "kind": kind,
                "state": state,
                "phase": accumulator.phase or "",
                "task_id": accumulator.task_id or None,
                "first_seen": accumulator.first_ts,
                "last_seen": accumulator.last_ts,
                "stats": {
                    "events": accumulator.events,
                    "dispatches": accumulator.dispatches,
                    "handoffs": accumulator.handoffs,
                    "commits": accumulator.commits,
                    "findings": accumulator.findings,
                },
                "latest": latest,
            }
        )
    # The lead always leads the crew list; everything else keeps seed/first
    # sighting order.
    agents.sort(key=lambda agent: 0 if agent["kind"] == "lead" else 1)
    total = len(agents)
    return agents[:MAX_AGENTS], total


# Task status is a closed set raised by evidence in this precedence order.
_TASK_STATUS_RANK = {"pending": 0, "active": 1, "handoff": 2, "completed": 3}
_PLATFORM_TO_TASK_STATUS = {
    "pending": "pending",
    "in_progress": "active",
    "completed": "completed",
}
_SIGNAL_KINDS = frozenset({"blocker", "algedonic"})
# What the snapshot writer records for a task with no subject.
_NO_SUBJECT = "(no subject)"
_TEACHBACK_SUBJECT = re.compile(r"\bTEACHBACK\b", re.IGNORECASE)
# Journal evidence that a task is being worked on.
_ACTIVE_EVIDENCE = frozenset({"agent_dispatch", "dispatch_site", "remediation"})


def task_key(task_id: str, occupant: str) -> str:
    """A task card's identity: ``<task_id>:<occupant>`` (ids are reused)."""
    return f"{task_id}:{occupant}"


def _raise_status(task: dict, status: str) -> None:
    """Raise a task's status by precedence completed > handoff > active > pending."""
    if status in _TASK_STATUS_RANK and (
        _TASK_STATUS_RANK[status] > _TASK_STATUS_RANK.get(task["status"], -1)
    ):
        task["status"] = status


def _new_task(task_id: str, occupant: str, source: str, first_seq: int | None) -> dict:
    """A blank contract-shaped task (``_``-prefixed keys are internal)."""
    return {
        "id": task_id,
        "key": task_key(task_id, occupant),
        "occupant": occupant,
        "subject": "",
        "owner": "",
        "phase": "",
        "status": "",
        "kind": "task",
        "variety": None,
        "teachback": False,
        "handoff": False,
        "updated_at": "",
        "source": source,
        "_first_seq": first_seq,
        "_signal": None,
    }


def _set_kind(task: dict, kind: str) -> None:
    """Record a task kind; a signal kind is never overwritten by another."""
    if kind and task["kind"] not in _SIGNAL_KINDS:
        task["kind"] = kind


def task_segments(events: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[int, str]]]:
    """Each task id's cards as (boundary seq, occupant) in seq order.

    Location: model.task_segments.
    Summary: An id's snapshots are cut into SUBJECT segments: a new segment
             starts at a snapshot whose subject differs from the current
             segment's (an owner change never splits — the occupant hash
             includes the owner, so it changes when an owner is assigned). A
             blank subject (empty, or the writers' "(no subject)") is no
             change and never splits.
             A segment's boundary is the newest session_start strictly
             between the previous segment's last snapshot and its own first
             snapshot, else that first snapshot; the first segment's boundary
             is -1, so every earlier record joins it. A segment's occupant is
             its first snapshot's, which keeps the key stable as the journal
             grows; when that occupant already names an earlier segment of the
             same id (subjects A, B, A under one owner), the later segment's
             term gains ``:<segment index>`` so no two cards share a key.
    Used by: occupant_keys, segment_key, derive_tasks.
    """
    starts = [
        event["seq"] for event in events
        if event.get("type") == "session_start" and isinstance(event.get("seq"), int)
    ]
    raw_segments: dict[str, list[dict]] = {}
    for event in events:
        if event.get("type") != "task_metadata_snapshot":
            continue
        task_id = _text(event.get("task_id"), 60)
        seq = event.get("seq")
        if not task_id or not isinstance(seq, int):
            continue
        data = _mapping(event.get("raw"))
        subject = _text(data.get("subject"), MAX_FIELD_CHARS)
        if subject == _NO_SUBJECT:
            subject = ""
        segments = raw_segments.setdefault(task_id, [])
        if segments and (not subject or not segments[-1]["subject"]):
            # A blank subject is "no change": it neither opens a segment nor,
            # in the segment it lands in, counts as a different subject.
            segments[-1]["last"] = seq
            segments[-1]["subject"] = segments[-1]["subject"] or subject
        elif not segments or segments[-1]["subject"] != subject:
            segments.append({"subject": subject, "first": seq, "last": seq,
                             "occupant": _text(data.get("occupant"), 64)})
        else:
            segments[-1]["last"] = seq
    result: dict[str, list[tuple[int, str]]] = {}
    for task_id, segments in raw_segments.items():
        cards: list[tuple[int, str]] = []
        used: set[str] = set()
        for index, segment in enumerate(segments):
            term = segment["occupant"]
            if term in used:
                term = f"{term}:{index}"
            used.add(term)
            if index == 0:
                boundary = -1
            else:
                previous_last = segments[index - 1]["last"]
                between = [seq for seq in starts if previous_last < seq < segment["first"]]
                boundary = between[-1] if between else segment["first"]
            cards.append((boundary, term))
        result[task_id] = cards
    return result


def segment_key(
    segments: Mapping[str, Sequence[tuple[int, str]]], task_id: str, seq: int | None
) -> str:
    """The card key a record of ``task_id`` at ``seq`` joins.

    The segment whose span [boundary, next boundary) contains ``seq``; a seq
    of None (a live task file) joins the newest segment; an id with no
    snapshot is one card with the empty occupant.
    """
    cards = segments.get(task_id)
    if not cards:
        return task_key(task_id, "")
    if seq is None:
        return task_key(task_id, cards[-1][1])
    chosen = cards[0][1]
    for boundary, occupant in cards:
        if boundary <= seq:
            chosen = occupant
        else:
            break
    return task_key(task_id, chosen)


def occupant_keys(events: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """The task card key each task-bearing event joins, by event seq.

    Location: model.occupant_keys.
    Summary: segment_key over task_segments for every event that names a
             task id, snapshots included.
    Used by: derive_tasks, derive_handoffs, fill_task_agents.
    """
    segments = task_segments(events)
    keys: dict[int, str] = {}
    for event in events:
        task_id = _text(event.get("task_id"), 60)
        seq = event.get("seq")
        if task_id and isinstance(seq, int):
            keys[seq] = segment_key(segments, task_id, seq)
    return keys


def _seed_status(value: object) -> str:
    """A demo/explicit seed status mapped onto the closed task status set."""
    text = value.strip().lower() if isinstance(value, str) else ""
    if text in _TASK_STATUS_RANK:
        return text
    return _PLATFORM_TO_TASK_STATUS.get(text, "pending")


def _task_from_seed(seed: Mapping[str, Any]) -> dict | None:
    """Convert a demo/explicit task seed to the contract task shape."""
    raw_id = seed.get("id")
    if raw_id is None:
        return None
    task_id = _text(raw_id, 60)
    if not task_id:
        return None
    task = _new_task(task_id, _text(seed.get("occupant"), 64), _text(seed.get("source"), 20) or "derived", None)
    task.update(
        {
            "subject": _text(seed.get("subject"), MAX_FIELD_CHARS),
            "owner": _text(seed.get("owner"), 200),
            "phase": _normalize_phase(seed.get("phase")),
            "status": _seed_status(seed.get("status")),
            "kind": _text(seed.get("kind"), 40) or "task",
            "variety": normalize_variety(seed.get("variety")),
            "teachback": bool(seed.get("teachback")),
            "handoff": bool(seed.get("handoff")),
            "updated_at": _text(seed.get("updated_at"), 64),
        }
    )
    return task


def derive_tasks(
    events: Sequence[Mapping[str, Any]],
    team_info: roster.TeamInfo,
    task_seeds: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[list[dict], int, list[dict]]:
    """Derive (capped tasks, full count, every task) — internal keys kept.

    Location: model.derive_tasks.
    Summary: One card per subject segment of a task id — see
             task_segments. Explicit
             seeds (demo) start the board; journal evidence is folded in event
             order; then the live task store joins each id's newest occupant.
             Status is the closed set {pending, active, handoff, completed},
             raised by precedence: dispatch, dispatch_site and remediation
             mean active, a handoff means handoff, and the platform status
             maps pending/in_progress/completed (a deleted live task drops its
             card). A teachback_ack completes its own gate card and gives it
             kind "gate"; a card whose subject matches TEACHBACK is a gate too,
             and each task a gate's live file ``blocks`` gets teachback. No
             teachback evidence changes the work task's status.
             Signal tasks keep kind blocker/algedonic. Cards are ordered by
             numeric id then first seq, and the cap keeps the NEWEST.
    Used by: derive_model, tests.
    """
    tasks: dict[str, dict] = {}
    keys = occupant_keys(events)

    def ensure(event: Mapping[str, Any]) -> dict | None:
        seq = event.get("seq")
        key = keys.get(seq) if isinstance(seq, int) else None
        if key is None:
            return None
        task = tasks.get(key)
        if task is None:
            task_id, occupant = key.split(":", 1)
            task = _new_task(task_id, occupant, "derived", seq)
            tasks[key] = task
        return task

    if task_seeds is not None:
        for seed in task_seeds:
            task = _task_from_seed(seed)
            if task is not None and task["key"] not in tasks:
                tasks[task["key"]] = task

    segments = task_segments(events)
    for event in events:
        event_type = event.get("type")
        data = _mapping(event.get("raw"))
        ts = _text(event.get("ts"), 64)
        if event_type == "checkpoint":
            # Checkpoints name tasks inside active_agents; the free-text
            # status there is a note, never a task status.
            phase = _normalize_phase(data.get("phase"))
            for active in _list(data.get("active_agents")):
                if not isinstance(active, Mapping):
                    continue
                task_id = _text(active.get("task_id"), 60)
                if not task_id:
                    continue
                key = segment_key(segments, task_id, event.get("seq"))
                task = tasks.setdefault(
                    key, _new_task(task_id, key.split(":", 1)[1], "derived", event.get("seq"))
                )
                name = _text(active.get("name"), 200)
                if name:
                    task["owner"] = name
                if phase and not task["phase"]:
                    task["phase"] = phase
            continue
        task = ensure(event)
        if task is None:
            continue
        if event_type in _ACTIVE_EVIDENCE:
            owner = _text(data.get("fixer"), 200) if event_type == "remediation" else ""
            owner = owner or _text(event.get("agent"), 200)
            if owner and event_type != "dispatch_site":
                task["owner"] = owner
            phase = _normalize_phase(event.get("phase"))
            if phase and event_type == "agent_dispatch":
                task["phase"] = phase
            _raise_status(task, "active")
        elif event_type == "teachback_ack":
            # The ack is written when the lead completes the gate that accepts
            # a teachback, so it completes ITS OWN card, whatever the subject.
            task["teachback"] = True
            _raise_status(task, "completed")
            _set_kind(task, "gate")
        elif event_type == "agent_handoff":
            subject = _text(data.get("task_subject"), MAX_FIELD_CHARS)
            if subject:
                task["subject"] = subject
            owner = _text(event.get("agent"), 200)
            if owner:
                task["owner"] = owner
            task["handoff"] = True
            _raise_status(task, "handoff")
        elif event_type == "task_created":
            subject = _text(data.get("subject"), MAX_FIELD_CHARS)
            if subject:
                task["subject"] = subject
            _raise_status(task, "pending")
        elif event_type == "task_metadata_snapshot":
            subject = _text(data.get("subject"), MAX_FIELD_CHARS)
            if subject and subject != _NO_SUBJECT:
                task["subject"] = subject
            owner = _text(data.get("owner"), 200)
            if owner:
                task["owner"] = owner
            metadata = _mapping(data.get("metadata"))
            variety = normalize_variety(metadata.get("variety"))
            if variety is not None:
                task["variety"] = variety
            if metadata.get("teachback_submit"):
                task["teachback"] = True
            kind = _text(metadata.get("type"), 40) or _text(data.get("task_type"), 40)
            _set_kind(task, kind)
            if kind in _SIGNAL_KINDS and task["_signal"] is None:
                task["_signal"] = {
                    "seq": event.get("seq"),
                    "ts": ts,
                    "level": _text(metadata.get("level"), 40).lower(),
                    "category": _text(metadata.get("category"), 80),
                    "resolution": roster.resolution_text(metadata.get("resolution")),
                }
            elif kind in _SIGNAL_KINDS:
                resolution = roster.resolution_text(metadata.get("resolution"))
                if resolution:
                    task["_signal"]["resolution"] = resolution
        elif event_type in {"variety_assessed", "dispatch_variety"}:
            variety = normalize_variety(data.get("variety"))
            if variety is not None:
                task["variety"] = variety
        if event_type == "dispatch_site":
            variety = normalize_variety(data.get("variety"))
            if variety is not None:
                task["variety"] = variety
        task["updated_at"] = ts or task["updated_at"]

    if task_seeds is None:
        _fold_task_store(tasks, team_info.tasks, task_segments(events))

    final: list[dict] = []
    for task in tasks.values():
        if not task["subject"]:
            task["subject"] = f"task #{task['id']}"
        if task["status"] not in _TASK_STATUS_RANK:
            task["status"] = "pending"
        if _TEACHBACK_SUBJECT.search(task["subject"]) and task["kind"] not in _SIGNAL_KINDS:
            task["kind"] = "gate"
        final.append(task)
    final.sort(key=lambda task: (
        roster.task_order_key(task["id"]),
        task["_first_seq"] if isinstance(task["_first_seq"], int) else -1,
    ))
    total = len(final)
    return _cap_tasks(final), total, final


def _cap_tasks(tasks: list[dict]) -> list[dict]:
    """The newest MAX_TASKS cards, never dropping a signal (alert) card.

    Every alert names a card, so blocker/algedonic cards are always kept and
    the remaining slots go to the newest other cards; order is preserved.
    """
    if len(tasks) <= MAX_TASKS:
        return tasks
    signal = {id(task) for task in tasks if task["kind"] in _SIGNAL_KINDS}
    room = max(0, MAX_TASKS - len(signal))
    others = [task for task in tasks if id(task) not in signal]
    kept = signal | {id(task) for task in (others[-room:] if room else [])}
    return [task for task in tasks if id(task) in kept]


def _fold_task_store(
    tasks: dict[str, dict],
    stored_tasks: Sequence[roster.StoredTask],
    segments: Mapping[str, Sequence[tuple[int, str]]],
) -> None:
    """Join each live task file to its id's newest card (segment)."""
    blocked_by_gates: list[str] = []
    for stored in stored_tasks:
        key = segment_key(segments, stored.id, None)
        if stored.status == "deleted":
            tasks.pop(key, None)
            continue
        task = tasks.get(key)
        if task is None:
            task = _new_task(stored.id, key.split(":", 1)[1], "store", None)
            tasks[key] = task
        task["source"] = "store"
        if stored.subject:
            task["subject"] = stored.subject
        if stored.owner:
            task["owner"] = stored.owner
        if stored.variety is not None:
            task["variety"] = stored.variety
        task["teachback"] = task["teachback"] or stored.teachback
        if stored.handoff:
            task["handoff"] = True
            _raise_status(task, "handoff")
        _raise_status(task, _PLATFORM_TO_TASK_STATUS.get(stored.status, ""))
        _set_kind(task, stored.kind if stored.kind != "task" else "")
        if stored.kind in _SIGNAL_KINDS:
            signal = task["_signal"] or {
                "seq": None, "ts": stored.updated_at, "level": "", "category": "",
                "resolution": None,
            }
            signal["level"] = signal["level"] or stored.level
            signal["category"] = signal["category"] or stored.category
            signal["resolution"] = stored.resolution or signal["resolution"]
            task["_signal"] = signal
        task["updated_at"] = task["updated_at"] or stored.updated_at
        if _TEACHBACK_SUBJECT.search(stored.subject or task["subject"]):
            blocked_by_gates.extend(stored.blocks)
    for blocked_id in blocked_by_gates:
        blocked = tasks.get(segment_key(segments, blocked_id, None))
        if blocked is not None:
            blocked["teachback"] = True


def public_task(task: Mapping[str, Any]) -> dict:
    """A task without its internal (``_``-prefixed) keys."""
    return {name: value for name, value in task.items() if not name.startswith("_")}


def derive_handoffs(
    events: Sequence[Mapping[str, Any]],
    tasks_by_key: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict], int]:
    """Derive the handoffs array and the full (pre-cap) count.

    Location: model.derive_handoffs.
    Summary: One row per agent_handoff event, in seq order, linked to its
             card by ``task_key`` (occupant_keys). ``fields`` always carries
             the six canonical keys (None when absent) and legacy spellings
             are resolved server-side, but schema_complete is decided on the
             RAW payload with the SSOT's presence-only rule (every required
             key — all but recommended reasoning_chain — is present, whatever
             its value).
    Used by: derive_model, tests.
    """
    keys = occupant_keys(events)
    handoffs: list[dict] = []
    for event in events:
        if event.get("type") != "agent_handoff":
            continue
        data = _mapping(event.get("raw"))
        raw_handoff = data.get("handoff")
        fields = classify.extract_handoff_fields(raw_handoff)
        task_id = _text(event.get("task_id"), 60)
        seq = event.get("seq")
        key = keys.get(seq, "") if isinstance(seq, int) else ""
        task_subject = _text(data.get("task_subject"), MAX_FIELD_CHARS)
        if not task_subject and key:
            known = tasks_by_key.get(key)
            if known is not None:
                task_subject = _text(known.get("subject"), MAX_FIELD_CHARS)
        handoffs.append(
            {
                "seq": seq,
                "ts": _text(event.get("ts"), 64),
                "agent": _text(event.get("agent"), 200),
                "task_id": task_id,
                "task_key": key,
                "task_subject": task_subject,
                "fields": fields,
                "schema_complete": classify.handoff_schema_complete_raw(raw_handoff),
            }
        )
    total = len(handoffs)
    return handoffs[-MAX_HANDOFFS:], total


def derive_commits(events: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    """Derive the commits array and the full count from commit events."""
    commits: list[dict] = []
    for event in events:
        if event.get("type") != "commit":
            continue
        data = _mapping(event.get("raw"))
        sha = _text(data.get("sha"), 60)
        message = _text(data.get("message"), 240)
        commits.append(
            {
                "seq": event.get("seq"),
                "ts": _text(event.get("ts"), 64),
                "sha": sha,
                "message": message,
                "phase": _normalize_phase(event.get("phase")),
            }
        )
    total = len(commits)
    return commits[-MAX_COMMITS:], total


def derive_reviews(events: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    """Derive the reviews array and the full count from review_dispatch events."""
    reviews: list[dict] = []
    for event in events:
        if event.get("type") != "review_dispatch":
            continue
        data = _mapping(event.get("raw"))
        reviews.append(
            {
                "seq": event.get("seq"),
                "ts": _text(event.get("ts"), 64),
                "pr_number": _int(data.get("pr_number"), None),
                "pr_url": _text(data.get("pr_url"), MAX_FIELD_CHARS),
                "reviewers": classify.as_str_list(data.get("reviewers"), 40),
            }
        )
    total = len(reviews)
    return reviews[-MAX_REVIEWS:], total


def derive_findings(events: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    """Derive the findings array and the full count from review_finding events."""
    findings: list[dict] = []
    for event in events:
        if event.get("type") != "review_finding":
            continue
        data = _mapping(event.get("raw"))
        finding = _text(data.get("finding"), MAX_FIELD_CHARS) or _text(
            event.get("detail"), MAX_DETAIL_CHARS
        )
        findings.append(
            {
                "seq": event.get("seq"),
                "ts": _text(event.get("ts"), 64),
                "severity": _text(data.get("severity"), 40).lower(),
                "finding": finding,
                "reviewer": _text(data.get("reviewer"), 200)
                or _text(event.get("agent"), 200),
                "task_id": _text(data.get("task_id"), 60),
            }
        )
    total = len(findings)
    return findings[-MAX_FINDINGS:], total


def _alert(
    *,
    seq: object,
    ts: str,
    kind: str,
    level: str,
    category: str,
    summary: str,
    task_id: str,
    key: str,
    status: str,
    resolution: str | None,
) -> dict:
    """One alert in the contract shape."""
    level_text = level.lower() or "alert"
    category_text = category or kind
    return {
        "seq": seq if isinstance(seq, int) else None,
        "ts": ts,
        "kind": kind,
        "level": level_text,
        "category": category_text,
        "category_key": category_text.lower(),
        "summary": summary,
        "task_id": task_id,
        "task_key": key,
        "status": status,
        "resolved": bool(resolution) or status == "completed",
        "resolution": resolution,
    }


def derive_alerts(
    events: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[list[dict], int]:
    """Derive the alerts array and the full count.

    Location: model.derive_alerts.
    Summary: A task whose metadata type (or snapshot task_type) is blocker or
             algedonic is an alert: level lower-cased (blocker default
             "alert"), category verbatim with a lower-cased category_key,
             the subject as summary, and resolved when it carries a
             resolution or is completed. seq/ts come from the first snapshot
             that showed the type, else null plus the task file's mtime. The
             algedonic_signal / blocker_raised journal types are accepted as
             forward-compatible inputs in the same shape.
    Used by: derive_model, tests.
    """
    alerts: list[dict] = []
    for task in tasks:
        signal = task.get("_signal")
        if task.get("kind") not in _SIGNAL_KINDS or not isinstance(signal, Mapping):
            continue
        alerts.append(
            _alert(
                seq=signal.get("seq"),
                ts=_text(signal.get("ts"), 64) or _text(task.get("updated_at"), 64),
                kind=str(task["kind"]),
                level=_text(signal.get("level"), 40),
                category=_text(signal.get("category"), 80),
                summary=_text(task.get("subject"), MAX_FIELD_CHARS),
                task_id=_text(task.get("id"), 60),
                key=_text(task.get("key"), 140),
                status=_text(task.get("status"), 20),
                resolution=signal.get("resolution") if isinstance(signal.get("resolution"), str) else None,
            )
        )
    for event in events:
        event_type = event.get("type")
        if event_type not in {"algedonic_signal", "blocker_raised"}:
            continue
        data = _mapping(event.get("raw"))
        kind = "algedonic" if event_type == "algedonic_signal" else "blocker"
        alerts.append(
            _alert(
                seq=event.get("seq"),
                ts=_text(event.get("ts"), 64),
                kind=kind,
                level=_text(data.get("level"), 40) if kind == "algedonic" else "alert",
                category=_text(data.get("category"), 80),
                summary=_text(data.get("summary"), MAX_FIELD_CHARS)
                or _text(event.get("detail"), MAX_DETAIL_CHARS),
                task_id=_text(event.get("task_id"), 60),
                key="",
                status="",
                resolution=roster.resolution_text(data.get("resolution")),
            )
        )
    alerts.sort(key=lambda alert: (alert["seq"] is None, alert["seq"] or 0))
    total = len(alerts)
    return alerts[-MAX_ALERTS:], total


def derive_checkpoints(events: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    """Derive the checkpoints array and the full count.

    ``active_agents[].note`` carries the free-text status a checkpoint
    records for each agent; it is prose, not a task status.
    """
    checkpoints: list[dict] = []
    for event in events:
        if event.get("type") != "checkpoint":
            continue
        data = _mapping(event.get("raw"))
        active_agents: list[dict] = []
        for active in _list(data.get("active_agents")):
            if not isinstance(active, Mapping):
                continue
            active_agents.append(
                {
                    "name": _text(active.get("name"), 200),
                    "task_id": _text(active.get("task_id"), 60),
                    "note": _text(active.get("status"), MAX_DETAIL_CHARS),
                }
            )
        checkpoints.append(
            {
                "seq": event.get("seq"),
                "ts": _text(event.get("ts"), 64),
                "phase": _normalize_phase(data.get("phase")),
                "completed_phases": [
                    _normalize_phase(phase)
                    for phase in _list(data.get("completed_phases"))
                    if _normalize_phase(phase)
                ],
                "active_agents": active_agents,
                "variety": normalize_variety(data.get("variety")),
            }
        )
    total = len(checkpoints)
    return checkpoints[-MAX_CHECKPOINTS:], total


def derive_gates(events: Sequence[Mapping[str, Any]]) -> dict:
    """Derive the gates/telemetry block.

    Location: model.derive_gates.
    Summary: Counts lifecycle_decision allow/advisory verdicts by tool, ranks
             advisory rules, lists gate_health failures (hook/stage/error), and
             counts the two telemetry loss signals (journal_emit_skipped and
             handoff_refusal_degraded). Counters are full-journal totals; the
             health_failures list is bounded and by_tool carries at most
             MAX_GATE_TOOLS tools (by_tool_total keeps the honest count).
    Used by: derive_model, tests.
    """
    allowed = 0
    advisory = 0
    by_tool: dict[str, dict[str, int]] = {}
    rule_counts: Counter[str] = Counter()
    health_failures: list[dict] = []
    health_total = 0
    telemetry = {"journal_emit_skipped": 0, "handoff_refusal_degraded": 0}
    for event in events:
        event_type = event.get("type")
        data = _mapping(event.get("raw"))
        if event_type == "lifecycle_decision":
            tool = _text(data.get("tool_name"), 80) or "unknown"
            bucket = by_tool.setdefault(tool, {"allow": 0, "advisory": 0})
            verdict = _text(data.get("verdict"), 20).lower()
            count = _int(data.get("advisories_count"), None)
            is_advisory = verdict == "advisory" or (verdict != "allow" and bool(count))
            if is_advisory:
                advisory += 1
                bucket["advisory"] += 1
                for rule in classify.as_str_list(data.get("rules"), 50):
                    rule_counts[rule] += 1
            else:
                allowed += 1
                bucket["allow"] += 1
        elif event_type == "gate_health":
            health_total += 1
            if len(health_failures) < MAX_HEALTH_FAILURES:
                health_failures.append(
                    {
                        "seq": event.get("seq"),
                        "ts": _text(event.get("ts"), 64),
                        "hook": _text(data.get("hook"), 120),
                        "stage": _text(data.get("stage"), 80),
                        "error": _text(data.get("error"), MAX_FIELD_CHARS),
                    }
                )
        elif event_type == "journal_emit_skipped":
            telemetry["journal_emit_skipped"] += 1
        elif event_type == "handoff_refusal_degraded":
            telemetry["handoff_refusal_degraded"] += 1
    top_rules = [
        {"rule": rule, "count": count}
        for rule, count in sorted(rule_counts.items(), key=lambda item: (-item[1], item[0]))
    ][:MAX_GATE_RULES]
    by_tool_total = len(by_tool)
    if by_tool_total > MAX_GATE_TOOLS:
        by_tool = dict(
            sorted(
                by_tool.items(),
                key=lambda item: (-sum(item[1].values()), item[0]),
            )[:MAX_GATE_TOOLS]
        )
    return {
        "allowed": allowed,
        "advisory": advisory,
        "by_tool": by_tool,
        "by_tool_total": by_tool_total,
        "top_rules": top_rules,
        "health_failures": health_failures,
        "telemetry": telemetry,
        "_health_total": health_total,
    }


def derive_variety(
    events: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> dict:
    """Derive the variety block (feature/latest/history).

    Location: model.derive_variety.
    Summary: Collects every variety stamp in event order: variety_assessed is
             "feature" unless it carries the dispatch scope, dispatch_variety
             and dispatch_site are "dispatch", checkpoint stamps are
             "checkpoint". ``feature`` is the last feature-level assessment
             (falling back to the first task-store feature stamp), ``latest``
             the most recent stamp of any source, and ``history`` the ordered
             sparkline points.
    Used by: derive_model, tests.
    """
    entries: list[dict] = []
    for event in events:
        event_type = event.get("type")
        if event_type not in {
            "variety_assessed",
            "dispatch_variety",
            "dispatch_site",
            "checkpoint",
        }:
            continue
        data = _mapping(event.get("raw"))
        variety = normalize_variety(data.get("variety"))
        if variety is None:
            continue
        if event_type == "variety_assessed":
            source = "dispatch" if _text(data.get("scope"), 40) == "dispatch" else "feature"
        elif event_type == "checkpoint":
            source = "checkpoint"
        else:
            source = "dispatch"
        entries.append(
            {
                "seq": event.get("seq"),
                "ts": _text(event.get("ts"), 64),
                "total": int(variety["total"]),
                "source": source,
                "variety": variety,
            }
        )
    feature_variety: dict | None = None
    feature_source = "feature"
    for entry in entries:
        if entry["source"] == "feature":
            feature_variety = entry["variety"]
    if feature_variety is None:
        for task in tasks:
            variety = task.get("variety")
            if isinstance(variety, Mapping):
                feature_variety = dict(variety)
                feature_source = "task"
                break
    latest_variety = entries[-1]["variety"] if entries else None
    history = [
        {
            "seq": entry["seq"],
            "ts": entry["ts"],
            "total": entry["total"],
            "source": entry["source"],
        }
        for entry in entries[-MAX_VARIETY_HISTORY:]
    ]
    return {
        "feature": feature_variety,
        "latest": latest_variety,
        "_feature_source": feature_source,
        "history": history,
    }


def derive_wrapup(events: Sequence[Mapping[str, Any]]) -> dict | None:
    """The last wrap-up session_end, or None.

    Location: model.derive_wrapup.
    Summary: A session_end counts as a wrap-up only when its ``source`` is
             "wrap-up"; a bare session_end is a process exit. The block
             carries source, upper-case pr_state, pr_number and ts of the
             LATEST wrap-up record.
    Used by: derive_model, tests.
    """
    wrapup: dict | None = None
    for event in events:
        if event.get("type") != "session_end":
            continue
        data = _mapping(event.get("raw"))
        source = _text(data.get("source"), 40)
        if source != "wrap-up":
            continue
        state = _text(data.get("pr_state"), 20).upper()
        wrapup = {
            "source": source,
            "pr_state": state,
            "pr_number": _int(data.get("pr_number"), None),
            "ts": _text(event.get("ts"), 64),
        }
    return wrapup


def _build_meta(
    events: Sequence[journal.RawEvent],
    *,
    slug: str,
    session_id: str,
    team_info: roster.TeamInfo,
    session_context: Mapping[str, Any],
    warnings: Sequence[str],
    mtime: float,
    meta_seed: Mapping[str, Any] | None,
    now: datetime,
    event_count: int,
    activity_ts: float = 0.0,
) -> dict:
    """Assemble the meta block (project/team/times/status/run_kind/cloud).

    Status is journal.run_status over the whole event list: ended by its own
    session_end only when that is the newest lifecycle event with nothing but
    end trailers after it; otherwise the activity clock (``activity_ts``, the
    newest of the journal and team/task file mtimes) decides.
    """
    seed = meta_seed or {}
    session_start = _session_start(events) or {}
    session_end = _last_of_type(events, "session_end") or {}

    project_dir = (
        _text(seed.get("project_dir"), MAX_FIELD_CHARS)
        or _text(session_start.get("project_dir"), MAX_FIELD_CHARS)
        or _text(session_context.get("project_dir"), MAX_FIELD_CHARS)
    )
    # The run header, About tab and stage render this label, so it is
    # constrained by the SAME rule as the session list's `project`: a
    # journal-derived basename must never reach the UI as arbitrary text.
    project, project_conformed = project_label(
        _text(seed.get("project"), 200) or _basename(project_dir) or slug
    )
    team = (
        _text(seed.get("team"), 200)
        or _text(session_start.get("team"), 200)
        or _text(session_context.get("team_name"), 200)
        or (f"session-{session_id[:8]}" if session_id else "")
    )
    # The LATEST session_start that records a lead address decides: older
    # starts may lack it, and compact/resume starts carry it again.
    lead_address = _text(seed.get("lead_address"), 200)
    if not lead_address:
        for raw in reversed(events):
            if raw.data.get("type") == "session_start":
                lead_address = _text(raw.data.get("lead_address"), 200)
                if lead_address:
                    break
    if not lead_address:
        lead_address = _text(session_context.get("lead_address"), 200)
    cloud = bool(seed.get("cloud")) or lead_address == "main" or team_info.cloud

    ended_at = _text(seed.get("ended_at"), 64) or _text(session_end.get("ts"), 64)
    started_at = _text(seed.get("started_at"), 64) or _text(session_start.get("ts"), 64)
    if not started_at and events:
        started_at = _text(events[0].data.get("ts"), 64) or None

    clock = max(float(mtime or 0.0), float(activity_ts or 0.0))
    lifecycle = journal.lifecycle_of_events(raw.data for raw in reversed(events))
    status, ended_by = journal.run_status(lifecycle, clock, now.timestamp())
    if _text(seed.get("status"), 20):
        status = _text(seed.get("status"), 20)
        ended_by = None if status == "live" else (_text(seed.get("ended_by"), 20) or "session_end")
    if ended_by != "session_end":
        # A session_end that is not the newest lifecycle event (a resume, or
        # work after a wrap-up record) does not end the run.
        ended_at = _text(seed.get("ended_at"), 64)

    if not ended_at and status != "live" and events:
        ended_at = _text(events[-1].data.get("ts"), 64) or None

    duration_s: int | None = None
    if _int(seed.get("duration_s"), None) is not None:
        duration_s = _int(seed.get("duration_s"), None)
    else:
        started_dt = parse_ts(started_at)
        ended_dt = parse_ts(ended_at if ended_at else (_text(events[-1].data.get("ts")) if events else None))
        if started_dt is not None and ended_dt is not None:
            duration_s = max(0, int((ended_dt - started_dt).total_seconds()))

    run_kind = _text(seed.get("run_kind"), 40)
    if not run_kind:
        # ONE rule for both views: a marker event makes the run an
        # orchestration. The cheap list derives the same answer from the same
        # set within a bounded scan, so no team-store fact can promote one
        # view and not the other.
        has_orchestration = any(
            _text(event.data.get("type"), 80) in _ORCHESTRATION_TYPES for event in events
        )
        run_kind = "orchestrate" if has_orchestration else "session"

    meta = {
        "project": project,
        "project_dir": project_dir,
        "team": team,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
        "ended_by": ended_by,
        "activity_at": iso_utc(datetime.fromtimestamp(clock, timezone.utc)) if clock else None,
        "duration_s": duration_s,
        "run_kind": run_kind,
        "cloud": bool(cloud),
        "lead_address": lead_address,
        "event_count": _int(seed.get("event_count"), None) or event_count,
        "warnings": list(warnings)[:MAX_WARNINGS],
    }
    if not project_conformed:
        # Same visible marker the session list carries: the label was altered.
        meta["project_altered"] = True
    return meta


def derive_model(
    events: Sequence[journal.RawEvent],
    *,
    source: Mapping[str, Any],
    etag: str,
    root_id: str,
    slug: str,
    session_id: str,
    team_info: roster.TeamInfo | None = None,
    session_context: Mapping[str, Any] | None = None,
    warnings: Sequence[str] = (),
    meta_seed: Mapping[str, Any] | None = None,
    agent_seeds: Sequence[roster.MemberSeed] | None = None,
    task_seeds: Sequence[Mapping[str, Any]] | None = None,
    event_count: int | None = None,
    now: datetime | None = None,
    mtime: float = 0.0,
    activity_ts: float = 0.0,
    version: str = VERSION,
) -> dict:
    """Derive the full contract model from an already-parsed journal.

    Location: model.derive_model.
    Summary: The single pipeline shared by real journals and the demo. Meta is
             derived first (so session_start rendering has project/team/cloud),
             then events are classified once, then each derived section is
             built from the classified feed plus the non-journal state. Caps
             are applied here and recorded in ``truncated``/``warnings``.
    Used by: build_model, demo.build_demo_model, tests.
    """
    reference = utcnow(now)
    info = team_info or roster.TeamInfo()
    context_file = session_context or {}
    collected_warnings: list[str] = [w for w in warnings if isinstance(w, str)]
    total_events = int(event_count) if event_count is not None else len(events)

    meta = _build_meta(
        events,
        slug=slug,
        session_id=session_id,
        team_info=info,
        session_context=context_file,
        warnings=collected_warnings,
        mtime=mtime,
        meta_seed=meta_seed,
        now=reference,
        event_count=total_events,
        activity_ts=activity_ts,
    )
    render_context = {
        "project": meta["project"],
        "team": meta["team"],
        "cloud": meta["cloud"],
    }

    classified: list[dict] = []
    unknown_types: Counter[str] = Counter()
    for raw in events:
        event = classify.classify_event(raw, render_context)
        if event is None:
            event_type = _text(raw.data.get("type"), 80) or "(missing type)"
            unknown_types[event_type] += 1
            continue
        classified.append(event)
    if unknown_types:
        total_unknown = sum(unknown_types.values())
        collected_warnings.append(
            f"{total_unknown} event(s) of {len(unknown_types)} unrecognized type(s) omitted"
        )

    visible = classified
    truncated = False
    if len(visible) > MAX_EVENTS:
        dropped = len(visible) - MAX_EVENTS
        visible = visible[-MAX_EVENTS:]
        truncated = True
        collected_warnings.append(
            f"event feed truncated to the most recent {MAX_EVENTS} events "
            f"({dropped} earlier events omitted)"
        )

    phases = _phase_items(events)
    agents, agents_total = derive_agents(
        classified,
        info,
        meta=meta,
        agent_seeds=agent_seeds,
    )
    tasks, tasks_total, all_tasks = derive_tasks(
        events=classified, team_info=info, task_seeds=task_seeds
    )
    handoffs, handoffs_total = derive_handoffs(
        classified, {task["key"]: task for task in all_tasks}
    )
    commits, commits_total = derive_commits(classified)
    reviews, reviews_total = derive_reviews(classified)
    findings, findings_total = derive_findings(classified)
    alerts, alerts_total = derive_alerts(classified, all_tasks)
    tasks = [public_task(task) for task in tasks]
    checkpoints, checkpoints_total = derive_checkpoints(classified)
    gates = derive_gates(classified)
    health_total = int(gates.pop("_health_total", 0))
    variety = derive_variety(classified, tasks)
    variety.pop("_feature_source", None)
    wrapup = derive_wrapup(classified)

    if handoffs_total > MAX_HANDOFFS or len(handoffs) < handoffs_total:
        truncated = True
        collected_warnings.append(
            f"handoffs capped at the most recent {MAX_HANDOFFS}"
        )
    if tasks_total > MAX_TASKS:
        truncated = True
        collected_warnings.append(f"tasks capped at {MAX_TASKS}")
    if findings_total > MAX_FINDINGS or len(findings) < findings_total:
        truncated = True
        collected_warnings.append(f"findings capped at {MAX_FINDINGS}")
    if agents_total > MAX_AGENTS:
        truncated = True
        collected_warnings.append(f"agents capped at {MAX_AGENTS}")

    # Finding severity breakdown counts the FULL stream (classified is not
    # capped), not just the bounded findings list carried in the response.
    blocking = sum(
        1
        for event in classified
        if event["type"] == "review_finding"
        and _text(_mapping(event.get("raw")).get("severity"), 40).lower() == "blocking"
    )
    suggestions = sum(
        1
        for event in classified
        if event["type"] == "review_finding"
        and _text(_mapping(event.get("raw")).get("severity"), 40).lower() == "suggestion"
    )
    metrics = {
        "events_total": total_events,
        "events_visible": len(visible),
        "agents": agents_total,
        "tasks": tasks_total,
        "handoffs": handoffs_total,
        "commits": commits_total,
        "reviews": reviews_total,
        "findings": {
            "total": findings_total,
            "blocking": blocking,
            "suggestion": suggestions,
        },
        "gates_allowed": gates["allowed"],
        "gates_advisory": gates["advisory"],
        "gate_health_failures": health_total,
        "alerts": alerts_total,
        "teachbacks": sum(1 for event in classified if event["type"] == "teachback_ack"),
        "checkpoints": checkpoints_total,
        "compactions": sum(
            1 for event in classified if event["type"] == "compaction_attributed"
        ),
        "phases_completed": len(phases["completed"]),
        "phases_skipped": sum(1 for item in phases["items"] if item["status"] == "skipped"),
        "prs_ready": sum(1 for event in classified if event["type"] == "pr_ready"),
        "variety_total": int((variety["latest"] or {}).get("total", 0)),
    }
    meta["warnings"] = collected_warnings[:MAX_WARNINGS]

    return {
        "ok": True,
        "version": version,
        "etag": etag,
        "source": dict(source),
        "meta": meta,
        "phases": phases,
        "agents": agents,
        "tasks": tasks,
        "events": visible,
        "handoffs": handoffs,
        "commits": commits,
        "reviews": reviews,
        "findings": findings,
        "alerts": alerts,
        "checkpoints": checkpoints,
        "gates": gates,
        "wrapup": wrapup,
        "variety": variety,
        "metrics": metrics,
        "truncated": truncated,
        "warnings": collected_warnings[:MAX_WARNINGS],
    }


def build_source(ref: discovery.SessionRef, read: journal.JournalRead) -> dict:
    """The model's source block for a real journal."""
    return {
        "kind": "real",
        "root": ref.root_id,
        "slug": ref.slug,
        "session_id": ref.session_id,
        "journal": ref.relative_journal,
        "mtime": float(read.mtime),
        "size": int(read.size),
    }


def resolve_team_name(
    events: Sequence[journal.RawEvent],
    session_context: Mapping[str, Any],
    session_id: str,
) -> str:
    """Resolve which team directory a session belongs to.

    Location: model.resolve_team_name.
    Summary: session_start.team wins, then the session context file's
             team_name, then the conventional ``session-<id prefix>`` (which
             matches what /api/sessions reports even when no team exists).
    Used by: build_model, tests.
    """
    start = _session_start(events) or {}
    for candidate in (
        _text(start.get("team"), 200),
        _text(session_context.get("team_name"), 200),
    ):
        if candidate:
            return candidate
    return f"session-{session_id[:8]}" if session_id else ""


def session_team(ref: discovery.SessionRef) -> str:
    """The team directory a session belongs to, from bounded reads only.

    Location: model.session_team.
    Summary: resolve_team_name over the journal's head window and the session
             context file, so the ETag short-circuit, the session list and the
             full model all name the same team without a full parse.
    Used by: session_token, build_model, build_sessions_list.
    """
    head = journal.read_head_events(ref.journal)
    return resolve_team_name(head, roster.read_session_context(ref.session_dir), ref.session_id)


def session_activity_ns(ref: discovery.SessionRef, team_name: str) -> int:
    """Newest mtime (ns) of a session's non-journal inputs, or 0.

    Location: model.session_activity_ns.
    Summary: The team's task and team files (roster.activity_mtime_ns) plus
             the session's pact-session-context.json, which build_model reads
             for the team-name fallback. The journal mtime is folded in by the
             caller.
    Used by: session_token, build_sessions_list.
    """
    newest = roster.activity_mtime_ns(ref.root.path, team_name) if team_name else 0
    context = ref.session_dir / "pact-session-context.json"
    if discovery.contained(ref.root.path / "pact-sessions", context):
        try:
            newest = max(newest, int(context.stat().st_mtime_ns))
        except OSError:
            pass
    return newest


def session_token(
    ref: discovery.SessionRef, now: datetime | None = None, team_name: str | None = None
) -> tuple[str, float]:
    """(ETag token, activity clock seconds) for one session, from stats only.

    Location: model.session_token.
    Summary: ``<journal mtime_ns>-<journal size>-<activity_ns>-<status>-
             <ended_by|->-<build version>``. Every input the model's freshness
             depends on is in it: the journal identity, the newest team/task
             file mtime, and the derived run status, which changes when the
             activity clock crosses the live window even if no file changed.
             Costs one cached bounded lifecycle walk plus a few stats.
    Used by: build_model, server (/api/session since short-circuit).
    """
    lifecycle, stat = journal.lifecycle_scan(ref.journal)
    team = session_team(ref) if team_name is None else team_name
    activity_ns = session_activity_ns(ref, team)
    journal_ns = int(stat.st_mtime_ns) if stat is not None else 0
    size = int(stat.st_size) if stat is not None else 0
    activity_ns = max(activity_ns, journal_ns)
    activity_ts = activity_ns / 1e9
    status, ended_by = journal.run_status(lifecycle, activity_ts, utcnow(now).timestamp())
    token = f"{journal_ns}-{size}-{activity_ns}-{status}-{ended_by or '-'}-{VERSION}"
    return token, activity_ts


def build_model(ref: discovery.SessionRef, now: datetime | None = None) -> dict:
    """Read one discovered journal and return its full contract model.

    Location: model.build_model.
    Summary: Journal read + session-context read + team/task-store load +
             derive_model. The payload's ``etag`` is session_token, the same
             token the server's since short-circuit compares.
    Used by: server (/api/session), tests.
    """
    team_name = session_team(ref)
    token, activity_ts = session_token(ref, now, team_name)
    read = journal.read_journal(ref.journal)
    session_context = roster.read_session_context(ref.session_dir)
    team_info = roster.load_team(ref.root.path, team_name) if team_name else roster.TeamInfo()
    return derive_model(
        read.events,
        source=build_source(ref, read),
        etag=token,
        root_id=ref.root_id,
        slug=ref.slug,
        session_id=ref.session_id,
        team_info=team_info,
        session_context=session_context,
        warnings=read.warnings,
        event_count=len(read.events),
        now=now,
        mtime=read.mtime,
        activity_ts=activity_ts,
    )


def build_sessions_list(
    roots: Sequence[Any],
    *,
    limit: int = DEFAULT_SESSIONS_LIMIT,
    project_filter: str = "",
    session_filter: str = "",
    now: datetime | None = None,
) -> dict:
    """Build the cheap /api/sessions payload.

    Location: model.build_sessions_list.
    Summary: One bounded head/tail read per journal (never a full parse), then
             filter with match_project / match_session, sort newest
             updated_at first, and cap at ``limit``. ``started_at`` is None
             when the head window has no timestamp.
    Used by: server (/api/sessions), cli (--list), tests.
    """
    reference = utcnow(now)
    sessions: list[dict] = []
    raw_names: dict[str, str] = {}
    for ref in discovery.discover_sessions(roots):
        team_name = session_team(ref)
        activity_ns = session_activity_ns(ref, team_name)
        summary = journal.summarize_journal(ref.journal, reference, activity_ns / 1e9)
        raw_project = _basename(summary.project_dir) or ref.slug
        project, project_conformed = project_label(raw_project)
        raw_names[ref.key] = raw_project
        entry = {
            "key": ref.key,
            "root": ref.root_id,
            "slug": ref.slug,
            "session_id": ref.session_id,
            "project": project,
            "project_dir": classify.redact(summary.project_dir),
            "team": classify.as_text(summary.team, 200)
            or (f"session-{ref.session_id[:8]}" if ref.session_id else ""),
            "started_at": summary.started_at,
            "updated_at": summary.updated_at,
            "size_bytes": int(summary.size),
            "is_live": bool(summary.is_live),
            "status": summary.status,
            "ended_by": summary.ended_by,
            "last_event_type": summary.last_event_type,
            "last_phase": summary.last_phase,
            "run_kind": summary.run_kind,
        }
        if not project_conformed:
            # Visible marker, never a silent rewrite: the session stays in the
            # list and the payload says the label lost characters.
            entry["project_altered"] = True
        sessions.append(entry)
    if project_filter:
        sessions = match_project(project_filter, sessions, raw_names)
    if session_filter:
        sessions = match_session(session_filter, sessions)
    sessions.sort(
        key=lambda entry: (
            entry.get("updated_at") or "",
            entry.get("key") or "",
        ),
        reverse=True,
    )
    default_root = ""
    for root in roots:
        if getattr(root, "default", False):
            default_root = root.id
            break
    if not default_root and roots:
        default_root = roots[0].id
    return {
        "ok": True,
        "generated_at": iso_utc(reference),
        "default_root": default_root,
        "roots": discovery.root_payload(roots),
        "sessions": sessions[:limit],
    }


def match_project(
    wanted: str, sessions: Sequence[dict], raw_names: Mapping[str, str]
) -> list[dict]:
    """The sessions a ``project`` selection names.

    Location: model.match_project.
    Summary: Tiered and exact first: the slug, then the constrained label,
             then the raw directory basename (``raw_names`` by session key).
             Only when all three tiers miss does a case-insensitive pass over
             the same three names run. Never a substring match.
    Used by: build_sessions_list (/api/sessions and --list).
    """
    def names(session: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(session.get("slug") or ""),
            str(session.get("project") or ""),
            raw_names.get(str(session.get("key") or ""), ""),
        )

    for tier in range(3):
        hits = [session for session in sessions if names(session)[tier] == wanted]
        if hits:
            return hits
    folded = wanted.lower()
    return [
        session for session in sessions
        if folded in {name.lower() for name in names(session)}
    ]


def match_session(wanted: str, sessions: Sequence[dict]) -> list[dict]:
    """The sessions a ``session`` selection names: exact id, else a prefix.

    Location: model.match_session.
    Summary: An exact session id wins. Otherwise a prefix of at least
             settings.SESSION_PREFIX_MIN characters selects every session it
             starts; the page treats zero or several matches as an error.
    Used by: build_sessions_list (/api/sessions and --list).
    """
    exact = [session for session in sessions if session.get("session_id") == wanted]
    if exact or len(wanted) < SESSION_PREFIX_MIN:
        return exact
    return [
        session for session in sessions
        if str(session.get("session_id") or "").startswith(wanted)
    ]


def build_health(
    roots: Sequence[Any],
    *,
    pid: int,
    instance_id: str,
    uptime_s: float,
    now: datetime | None = None,
) -> dict:
    """Build the /api/health payload (served only with the launch key).

    Location: model.build_health.
    Summary: Identity (version and plugin_root, the build the CLI compares on
             reuse), pid, random instance token, uptime and the roots array
             (with live journal counts). ``demo_available`` is the capability:
             the scripted demo is selectable by URL on every server.
    Used by: server (/api/health), tests.
    """
    reference = utcnow(now)
    return {
        "ok": True,
        "name": APP_NAME,
        "version": VERSION,
        "plugin_root": str(PLUGIN_ROOT),
        "pid": int(pid),
        "instance_id": str(instance_id),
        "uptime_s": round(max(0.0, float(uptime_s)), 1),
        "roots": discovery.root_payload(roots),
        "demo_available": True,
        "generated_at": iso_utc(reference),
    }
