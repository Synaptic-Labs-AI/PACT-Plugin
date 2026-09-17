#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/missed_wake_scan.py
Summary: UserPromptSubmit + SessionStart hook — lead-side missed-wake SURFACER.
         On the lead's turn-start (UserPromptSubmit) or a session start
         (SessionStart), re-scans the team's task list for a teammate idling on
         intentional_wait.reason == "awaiting_lead_completion" past the
         staleness threshold and SURFACES an actionable additionalContext
         prompt naming what to check and the responses available.
         Also writes a once-per-(task,since) forensic `missed_wake` journal
         event (GC-proof record), deduped by reading the journal — no marker.
Used by: hooks.json UserPromptSubmit + SessionStart registration.

WHY SURFACE (not just record): a missed-wake alarm that only writes a journal
event has zero consumers — it detects but never alerts. additionalContext is the
lead-injectable channel: UserPromptSubmit fires at the lead's turn-START (can
inject context into the turn the lead is about to take) and SessionStart covers
cross-session recovery. (The earlier Stop carrier fired at turn-END and could
only suppressOutput — it recorded but never surfaced; that was the B1 gap.)

WHY DEFERRED / DURATION-KEYED: SendMessage fires no hookable event and the inbox
is written async-on-delivery, so a synchronous wake-confirmation read is
dead-by-construction (the retired completion_no_paired_send was ~100%
false-positive for exactly this). This alarm instead keys on the DURATION of the
wait via wait_stale() (the existing 30-min threshold) — by which time a wake, if
sent, would already have landed.

WHY LEAD-SIDE (is_lead-gated): only the lead can ACT on any of the causes this
condition has — sending an owed wake, letting a deliberate hold stand, or telling
a teammate to clear its own flag are all lead actions. Do NOT restate this as
"the missed wake is a LEAD failure because the lead forgot the paired wake": a
task the lead completed is filtered out by the in_progress gate below, so that
particular cause cannot reach this surface at all, and naming it as THE cause is
what this justification previously got wrong.
The journal is NOT the cause: an in-process teammate frame reaches
the canonical journal too (see is_canonical_journal_frame), so the ROLE is what
makes this hook lead-side. Journal-resolvability stays process-scoped.
Teammate / plain frames no-op — the in-process-default
fail-safe branch. Activation keys on a RUNTIME STRUCTURAL signal (is_lead via
agent_type), never a mode flag. UserPromptSubmit has no Agent()-spawned-teammate
fire path, so the surfacer is single-writer (the lead's process) by construction.

DEDUP — NO MARKER (current-stale-state IS the dedup):
- SURFACE: re-scan find_stale_missed_wakes(get_task_list()) over the LIVE task
  list every fire. Surfacing is PERSISTENT-while-stale (re-prompts each
  UserPromptSubmit until the wait resolves) and SELF-CLEARS the moment the lead
  resolves the wait — the live intentional_wait state is the source of truth, so
  no surface marker / namespace / cleanup / TOCTOU is needed. wait_stale's 30-min
  pre-filter removes transients, so persistent surfacing is a true-positive
  reminder, not a #897-class cry-wolf.
- FORENSIC EMIT: the `missed_wake` journal event is KEPT (GC-proof recovery
  record) but deduped via a JOURNAL READ — read this session's existing
  missed_wake events and emit only for (task_id, since) not already recorded
  (once-per-(task,since); re-arms on a fresh `since`). The kept journal record IS
  the dedup state; single-writer (lead-process) makes read-then-emit race-free.
  No filesystem marker exists anywhere in this hook.

# livelock-safe: additionalContext ONLY on the surface path (a stale wait
# exists); suppressOutput on EVERY other path; informational (no loop, never
# blocks); the journal is read/written ONLY when a stale wait exists. The only
# filesystem write is the journal append_event (hardened in session_journal.py).

Input: JSON from stdin (UserPromptSubmit / SessionStart schema; agent_type is
       the role discriminator, hook_event_name names the firing event).
Output: hookSpecificOutput.additionalContext on the surface path; otherwise
        {"suppressOutput": true}. Exit 0 on every path.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add hooks directory to path for shared package imports (mirrors teammate_idle.py).
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import shared.pact_context as pact_context
from shared.constants import COMPACTION_TEAMMATE_CLAUSE
from shared.intentional_wait import validate_wait, wait_stale
from shared.pact_context import is_lead
from shared.session_journal import append_event, get_journal_path, make_event, read_events
from shared.session_state import _sanitize_member_name
from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# The intentional_wait reason that signals a teammate idling for the lead's
# completion + paired wake-SendMessage. This is the canonical missed-wake gap:
# the lead writes completion metadata but forgets the wake, and the teammate
# idles indefinitely (blockedBy is pull-only — an idle teammate cannot
# self-wake). We deliberately match THIS exact reason rather than any
# expected_resolver=="lead" wait, to scope the alarm to the documented gap.
_MISSED_WAKE_REASON = "awaiting_lead_completion"

# additionalContext events whose hookEventName the platform expects echoed back.
# The hook is registered ONLY on these two; the firing event is read from stdin.
_SURFACE_EVENTS = ("UserPromptSubmit", "SessionStart")


def find_stale_missed_wakes(tasks: list, now: "datetime | None" = None) -> list:
    """Return the tasks idling on awaiting_lead_completion past the staleness threshold.

    A task qualifies iff: status == "in_progress" AND metadata.intentional_wait
    is a WELL-FORMED wait (validate_wait) with reason == awaiting_lead_completion
    AND wait_stale() (reusing the existing 30-min threshold in
    shared/intentional_wait.py — staleness logic is NOT reinvented here).
    validate_wait gates first so a malformed wait (which wait_stale would treat
    as stale) does not surface or produce a missed_wake with a malformed `since`.
    Pure; never raises on plain dicts. This is the SINGLE scan feeding both the
    surface path and the forensic-emit path.
    """
    stale = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("status") != "in_progress":
            continue
        metadata = task.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        wait = metadata.get("intentional_wait")
        if not validate_wait(wait):
            continue
        if wait.get("reason") != _MISSED_WAKE_REASON:
            continue
        if not wait_stale(wait, _now=now):
            continue
        stale.append(task)
    return stale


def _wait_fields(task: dict) -> "tuple[str, str, str, str]":
    """Extract (task_id, owner, since, subject) for a stale task. Strings only;
    empty where absent. Pure."""
    task_id = str(task.get("id") or "")
    owner = task.get("owner") or ""
    subject = task.get("subject") or ""
    wait = (task.get("metadata") or {}).get("intentional_wait") or {}
    since = wait.get("since") or ""
    return task_id, owner, since, subject


def _emitted_keys() -> set:
    """Build the set of (task_id, since) already recorded as missed_wake events in
    THIS session's journal — the JOURNAL-READ dedup state (no filesystem marker).

    read_events never raises (returns [] on any error / missing journal), so the
    worst case is an empty set → at most one duplicate forensic emit, never a
    crash. Single-writer (lead-process) makes this read-then-emit race-free.
    """
    keys = set()
    for ev in read_events("missed_wake"):
        if not isinstance(ev, dict):
            continue
        task_id = ev.get("task_id")
        since = ev.get("since")
        if task_id and since:
            keys.add((task_id, since))
    return keys


def emit_forensic(stale: list, now: "datetime | None" = None) -> None:
    """Write a once-per-(task,since) forensic `missed_wake` journal event for each
    stale wait NOT already recorded (JOURNAL-READ dedup — no marker).

    Called only when `stale` is non-empty (journal read/write happens ONLY when a
    stale wait exists, per the livelock contract). Best-effort: a writability
    precondition (get_journal_path()) gates the read+write so a non-resolvable
    context is a clean no-op; individual append failures are tolerated. Never
    raises (caller preserves the exit-0 contract).
    """
    try:
        if not stale:
            return
        # Writability precondition: only the lead's process resolves the journal
        # path. is_lead already gated the caller; this is the belt-and-braces
        # no-op for an unresolvable context (surfacing still works without it).
        if not get_journal_path():
            return
        emitted = _emitted_keys()
        for task in stale:
            task_id, owner, since, subject = _wait_fields(task)
            if not task_id or not owner or not since:
                continue
            if (task_id, since) in emitted:
                continue
            # R2-F1 (defense-in-depth): owner/subject sanitized at WRITE for
            # safe-by-construction symmetry with build_surface (closes the
            # asymmetric-defense pattern the F31 lesson warns against) — no current
            # consumer renders missed_wake, but this avoids relying on that.
            # task_id + since are kept RAW as the dedup key — they are matched
            # against the raw (task_id, since) that _emitted_keys() reads back;
            # sanitizing them would entangle dedup with the sanitizer and break
            # convergence (security #64).
            safe_owner = _sanitize_member_name(owner)
            if not safe_owner:
                # Pathological all-control-char owner sanitizes to empty, which the
                # journal's non-empty `agent` schema would reject anyway — skip the
                # forensic event EXPLICITLY rather than attempt a doomed write. The
                # SURFACE still alerts (build_surface falls back to 'unknown'). Do
                # NOT mark (task_id, since) emitted, so a later valid value records.
                continue
            safe_subject = _sanitize_member_name(subject) if subject else ""
            fields = {"task_id": task_id, "agent": safe_owner, "since": since}
            if safe_subject:
                fields["task_subject"] = safe_subject
            fields["reason"] = _MISSED_WAKE_REASON
            if now is not None:
                fields["ts"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            append_event(make_event("missed_wake", **fields))
            # Track within this fire so two stale waits sharing a (task_id, since)
            # — impossible in practice, but cheap — cannot double-emit.
            emitted.add((task_id, since))
    except Exception:
        # Best-effort forensic record; never break the surface path or exit-0.
        pass


def _age_minutes(since: str, now: datetime) -> "int | None":
    """Whole minutes since `since` (tz-aware ISO-8601), or None if unparseable.
    `since` has already passed validate_wait in find_stale_missed_wakes, so this
    parses cleanly on the happy path; the guard keeps surfacing robust anyway."""
    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return max(0, int((now - parsed).total_seconds() // 60))
    except (ValueError, TypeError):
        return None


def build_surface(stale: list, now: "datetime | None" = None) -> "str | None":
    """Build the actionable additionalContext for still-stale missed wakes, or
    None if nothing is stale. Concise: one line per stranded task naming
    owner + task id + subject + age, plus the corrective action. `now` is
    injectable for deterministic tests."""
    if not stale:
        return None
    now = now or datetime.now(timezone.utc)
    lines = []
    for task in stale:
        task_id, owner, since, subject = _wait_fields(task)
        # F31: sanitize the teammate-authored fields (task_id, owner, subject)
        # BEFORE interpolating them into the lead's turn-start additionalContext.
        # Without this, embedded \n / NEL / U+2028 / U+2029 / control chars could
        # forge extra alarm or system-looking lines in the injected context. The
        # canonical render-bound sanitizer (shared.session_state) strips exactly
        # those. `since` is NOT interpolated — only its int age is — so it needs
        # no sanitization. Empty-after-sanitize degrades gracefully: the label
        # falls back to '?' / 'unknown' / no-subject.
        task_id = _sanitize_member_name(task_id)
        owner = _sanitize_member_name(owner)
        subject = _sanitize_member_name(subject)
        age = _age_minutes(since, now)
        age_str = f"~{age}min" if age is not None else "stale"
        label = f"#{task_id or '?'} ({owner or 'unknown'}"
        label += f": {subject}" if subject else ""
        label += ")"
        lines.append(f"- Task {label} — idle {age_str} on awaiting_lead_completion")
    return (
        "PACT missed-wake alarm: the teammate(s) below are idling on "
        "awaiting_lead_completion past the staleness threshold. KNOWN: the wait "
        "is well-formed and stale and an idle teammate cannot self-wake, so it "
        "will not resolve itself. THE CAUSE IS NOT KNOWN; several produce this, "
        "needing three responses. (1) SEND A wake-SendMessage — a rejection "
        "went out without its paired wake, or a wake was sent and not "
        "delivered. (2) NOTHING, THE WAIT IS LEGITIMATE — a deliberate hold, "
        "not reached yet, or a re-stamped teammate still genuinely waiting; no "
        "hook can see a hold. (3) THE TEAMMATE MUST CLEAR ITS OWN FLAG — it was "
        "woken and did not. ACTION: check which, then send a wake-SendMessage "
        "to each that needs one (or re-set / complete the task) — re-shows "
        "every turn until it resolves.\n" + "\n".join(lines)
    )


_UNFLAGGED_EVENT = "unflagged_background_wait"


def _unflagged_emitted_keys() -> set:
    """(agent, registered_at) pairs already recorded THIS session.

    A DISTINCT dedup key from missed_wake's (task_id, since): a record covers
    a LIST of tasks, so a per-task key would emit once per held task for one
    launch. `registered_at` is a timestamp on a per-agent record, so the pair
    is unique per launch.
    """
    keys = set()
    for ev in read_events(_UNFLAGGED_EVENT):
        if not isinstance(ev, dict):
            continue
        agent, registered = ev.get("agent"), ev.get("registered_at")
        if agent and registered:
            keys.add((agent, registered))
    return keys


def find_stale_unflagged_background(
    team_name: str, tasks: "list | None" = None, now: "datetime | None" = None
) -> list:
    """Records past their own staleness window. Never raises.

    SHARES A PROCESS WITH THE MISSED-WAKE ALARM, NOT A VOCABULARY. This keeps
    a separate journal event, a separate dedup key and separate surface text;
    it reuses only the subprocess and the lead-frame guard. It must never
    reuse `missed_wake` or the `awaiting_lead_completion` reason.

    GATES ARE NOT OPTIONAL ON THIS PATH. It reads through
    `outstanding_unflagged`, which applies the task-status and flagged-wait
    gates, and NEVER through `_load_records` or `load_records_for_discharge`,
    which apply neither. Read either one here and the surface names records
    for completed tasks and for correctly flagged waits, while its text tells
    the lead there is "no flagged wait", a claim nothing evaluated.

    Pass `tasks` when the caller has already read the task list, so a lead
    prompt reads it once; without it the list is read here.
    """
    try:
        from shared.background_work import (
            lead_stale,
            outstanding_unflagged,
            teammate_is_separate_process,
        )

        if tasks is None:
            tasks = get_task_list()
        # A separate-process (tmux) teammate is woken by its own completion and
        # collects the result on that turn, so its records are omitted from the
        # surface and from its forensic event.
        return [
            r for r in outstanding_unflagged(tasks, team_name, now=now)
            if lead_stale(r, now=now)
            and not teammate_is_separate_process(team_name, r.get("agent_name"))
        ]
    except Exception:
        return []


def emit_unflagged_forensic(stale: list, now: "datetime | None" = None) -> None:
    """Once-per-(agent, registered_at) forensic event. Best-effort, never raises."""
    try:
        if not stale or not get_journal_path():
            return
        emitted = _unflagged_emitted_keys()
        for record in stale:
            agent = record.get("agent_name")
            registered = record.get("registered_at")
            task_ids = record.get("task_ids")
            if not agent or not registered or not isinstance(task_ids, list):
                continue
            if (agent, registered) in emitted:
                continue
            payload = {
                "agent": _sanitize_member_name(str(agent)),
                "registered_at": str(registered),
                "task_ids": [
                    s for s in (_sanitize_member_name(str(t)) for t in task_ids) if s
                ],
            }
            command = record.get("command")
            if isinstance(command, str) and command:
                payload["command"] = _sanitize_member_name(command)
            if now is not None:
                payload["ts"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            append_event(make_event(_UNFLAGGED_EVENT, **payload))
            emitted.add((agent, registered))
    except Exception:
        return


def build_unflagged_surface(stale: list) -> "str | None":
    """Lead-facing text naming teammates with an unflagged recorded launch.

    THE LIST IS NOT A CENSUS, and the surface says so. A launch reaches the
    registry only from a Bash frame carrying the harness background flag or
    whose command ends in a bare `&`. A teammate with an outstanding monitor,
    Agent-tool subagent, MCP task, workflow or scheduled wakeup is absent
    from it, and so is one whose shell launch was backgrounded any other way.
    The sentence is true of everyone it names and says nothing about anyone
    it omits — a reader who takes an empty or short list as an all-clear has
    drawn a completeness inference the data does not support.
    """
    if not stale:
        return None
    lines = []
    for record in stale:
        agent = _sanitize_member_name(str(record.get("agent_name") or ""))
        # Task ids are rendered into the lead's additionalContext, so they get
        # the same sanitising as the agent name beside them.
        raw_ids = record.get("task_ids")
        ids = raw_ids if isinstance(raw_ids, list) else []
        tasks = ", ".join(
            s for s in (_sanitize_member_name(str(t)) for t in ids) if s
        )
        if not agent:
            continue
        lines.append(f"{agent} (task(s) {tasks})" if tasks else agent)
    if not lines:
        return None
    return (
        "UNFLAGGED BACKGROUND WORK — these teammates launched background work "
        "and have no flagged wait: "
        + "; ".join(lines)
        + ". The job may already have finished. SendMessage each one to "
        "collect its result or SET "
        "metadata.intentional_wait. This is NOT a missed wake — nobody is "
        "waiting on you; they failed to flag their own wait. "
        "These teammates are not woken by their own job finishing. "
        "This list covers recorded shell launches only: a teammate waiting "
        "on a monitor, a subagent, an MCP task, a workflow or a scheduled "
        "wakeup will not appear, and neither will a shell launch "
        "backgrounded in a shape the recorder does not match, so do not "
        "read a short list as an all-clear."
    )


def find_mutual_waits(tasks: list, now: "datetime | None" = None) -> list:
    """Distinct owners each idling on `peer`, all aged past the threshold.

    WHAT THIS DETECTS IS A CANDIDATE, NOT A PROVEN CYCLE, AND THE SURFACE MUST
    SAY SO. `expected_resolver` records the KIND of resolver, never which one,
    so the task store cannot say that X waits on Y and Y waits on X. What it
    can say is that two or more agents are each waiting on some peer and none
    of them is working — which is what a mutual wait looks like from outside,
    and is also what two agents independently waiting on a third looks like.
    Both are worth a look; only the first is a deadlock.

    BLIND BY CONSTRUCTION — `user` and `external` resolvers. Neither is a task
    store entity, so a cycle running through the user cannot be seen here at
    all. This is peer-peer only and no accumulation of evidence makes it
    otherwise: it is not that such cycles are rare, it is that this instrument
    cannot represent them. Do not let an empty result read as "no deadlock".

    AGE IS MEASURED FROM THE ANCHOR, NOT FROM `since`, AND THAT IS WHY THIS
    DEPENDS ON THE ANCHOR EXISTING. Agents are instructed to re-SET `since` so
    a long wait does not read as stale, so two agents deadlocked against each
    other and dutifully re-stamping stay permanently fresh and never age into
    this detector — the exact case it exists to catch. wait_scope_anchor
    returns the first-set anchor where there is one and falls back to `since`
    otherwise, so a pair that predates the anchor is still only as detectable
    as it was before: no worse, and better once anchors are being written.

    Threshold is the existing 30-minute wait_stale. No second constant.

    Never raises on a malformed task: non-dict metadata is skipped and a
    non-string owner is not counted, because a raise here drops every lead
    surface.
    """
    try:
        from shared.background_work import wait_scope_anchor
    except Exception:
        return []
    waiting = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if task.get("status") != "in_progress":
            continue
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            continue
        wait = metadata.get("intentional_wait")
        if not validate_wait(wait):
            continue
        if wait.get("expected_resolver") != "peer":
            continue
        anchor, _ = wait_scope_anchor(wait)
        if anchor is None:
            continue
        # Reuse wait_stale for BOTH the threshold and the staleness logic,
        # evaluated against the anchor rather than the re-stampable clock.
        if not wait_stale({**wait, "since": anchor.isoformat()}, _now=now):
            continue
        waiting.append(task)
    owners = {
        t["owner"] for t in waiting
        if isinstance(t.get("owner"), str) and t["owner"]
    }
    return waiting if len(owners) >= 2 else []


def build_mutual_surface(mutual: list) -> "str | None":
    """Lead-facing text for a candidate mutual wait. Names it as a candidate."""
    if not mutual:
        return None
    lines = []
    for task in mutual:
        task_id = _sanitize_member_name(str(task.get("id") or "")) or "?"
        owner = _sanitize_member_name(task.get("owner") or "") or "unknown"
        subject = _sanitize_member_name(task.get("subject") or "")
        label = f"#{task_id} ({owner}"
        label += f": {subject}" if subject else ""
        lines.append(f"- Task {label})")
    if not lines:
        return None
    return (
        "POSSIBLE MUTUAL WAIT — every one of these is idling on a PEER and "
        "none has moved past the staleness threshold:\n"
        + "\n".join(lines)
        + "\nNobody here is waiting on you, so this will not resolve itself "
        "and no teammate can see it — each one can only see its own wait. "
        "This is a CANDIDATE, not a proven cycle: the flag records that a "
        "peer is expected, never which peer, so two agents waiting on a third "
        "look identical to two waiting on each other. Ask each what it is "
        "waiting for. Waits on `user` or `external` CANNOT appear here at "
        "all, so this finding never rules a deadlock out."
    )


def find_unanchored_waits(
    tasks: list, team_name: "str | None" = None, now: "datetime | None" = None
) -> list:
    """Unanchored waits that are ACTIVELY DISCHARGING a record on the fallback.

    Agents are instructed to write `covers_since` on every SET, so a wait
    without one predates the field, was written under the earlier instruction
    or from a template that omits it, or lost it on a re-SET that rewrote the
    wait without carrying it forward. Nothing here can tell which. The wait
    still works; what is missing is the field pinning WHICH launches it covers,
    so coverage falls back to the re-stampable `since`. Surfacing that keeps
    the fallback from being a silent decision — but only where the fallback
    has actually decided something.

    🔴 GATED ON A COVERED RECORD, NOT ON STALENESS, AND THE DIFFERENCE INVERTS
    THE SIGNAL. Gating on `wait_stale` looks right and is backwards: it reads
    `since`, and re-stamping is BOTH what makes an unanchored wait dangerous
    and what makes it look fresh. MEASURED — a never-re-stamped 90-minute wait
    (harmless, because its fallback still equals its true anchor) reads stale
    and would surface; a wait re-stamped two minutes ago (harmful, its fallback
    has drifted forward over launches it never acknowledged) reads fresh and
    would be hidden. The gate would show exactly the population that is fine
    and suppress the one that is not.

    A record this wait covers is the actionable condition, and it is the whole
    of it: the anchor exists to scope records, so with no covered record there
    is nothing for the absence to have affected and nothing to tell the lead.
    That is also why a fresh team with no background work produces no surface —
    not a special case, just the general rule with an empty record set.

    Never raises: an import failure, an unusable task list, or an unreadable
    registry yields [].
    """
    try:
        from shared.background_work import (
            load_records_for_discharge,
            record_task_ids,
            wait_anchor_class,
            wait_covers_record,
        )
    except Exception:
        return []
    try:
        records = (
            load_records_for_discharge(team_name, now=now) if team_name else []
        )
    except Exception:
        return []
    if not records:
        return []
    out = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        if task.get("status") != "in_progress":
            continue
        try:
            anchor_class = wait_anchor_class(task)
            if not anchor_class:
                continue
            task_id = str(task.get("id"))
            covered = any(
                task_id in record_task_ids(r) and wait_covers_record(task, r)
                for r in records
            )
        except Exception:
            continue
        if covered:
            out.append((task, anchor_class))
    return out


def build_unanchored_surface(unanchored: list) -> "str | None":
    """Lead-facing text naming each wait with no usable scoping anchor."""
    if not unanchored:
        return None
    lines = []
    for task, anchor_class in unanchored:
        if not isinstance(task, dict):
            continue
        # Teammate-authored fields, sanitized before interpolation into the
        # lead's additionalContext for the same reason as build_surface.
        task_id = _sanitize_member_name(str(task.get("id") or "")) or "?"
        owner = _sanitize_member_name(task.get("owner") or "") or "unknown"
        lines.append(f"- Task #{task_id} ({owner}) — anchor {anchor_class}")
    if not lines:
        return None
    return (
        "BACKGROUND WORK DISCHARGED ON A FALLBACK ANCHOR — each of these waits "
        "is valid and is currently acquitting a recorded launch WITHOUT a "
        "`covers_since` to say which launches it covers, so the scope is taken "
        "from `since`, which agents re-stamp:\n"
        + "\n".join(lines)
        + "\nNothing is stalled. What is uncertain "
        "is whether the wait was really raised BEFORE the launch it is "
        "acquitting: if it was re-stamped, `since` has moved forward and may "
        "now cover work the teammate never acknowledged. Ask the teammate what "
        "its wait was raised for. `absent` means the wait predates the field, "
        "was written under the earlier instruction or from a template that "
        "omits it, or an agent dropped it on a re-SET by rewriting the wait "
        "without `covers_since`; the scan cannot tell which. `malformed` "
        "means an agent wrote an unparseable value and has a bug worth naming."
    )


def run_surface(input_data: dict, now: "datetime | None" = None) -> "str | None":
    """Lead-side missed-wake surface + forensic emit. is_lead-gated; teammate /
    plain frames no-op (the structural fail-safe default).

    Returns the additionalContext string when a still-stale awaiting_lead_completion
    wait exists, else None. The SAME live re-scan feeds both the forensic emit and
    the surface text — current-stale-state is the dedup, so the notice
    auto-clears when the lead resolves the wait.

    `now` is the one clock every alarm below is measured against. Omitted, it
    is read from the wall clock here, once; tests pass a fixed time.
    """
    if not is_lead(input_data):
        return None
    now = now if now is not None else datetime.now(timezone.utc)

    # INDEPENDENT ALARMS SHARING ONE SUBPROCESS. Each is computed and
    # emitted separately, and NONE early-returns on another's absence — an
    # empty missed-wake scan must not suppress the background surface, and
    # vice versa. They share only this process and the lead-frame guard above.
    # The task-list alarms below run outside any try, so their independence
    # also rests on each finder skipping a malformed task instead of raising:
    # one raise there drops every surface.
    parts = []

    tasks = get_task_list()
    if tasks:
        stale = find_stale_missed_wakes(tasks, now=now)
        if stale:
            emit_forensic(stale, now=now)
            surface = build_surface(stale, now=now)
            if surface:
                parts.append(surface)
        mutual = find_mutual_waits(tasks, now=now)
        if mutual:
            surface = build_mutual_surface(mutual)
            if surface:
                parts.append(surface)

    try:
        from shared.pact_context import get_team_name

        team_name = get_team_name()
        if team_name:
            unflagged = find_stale_unflagged_background(team_name, tasks, now=now)
            if unflagged:
                emit_unflagged_forensic(unflagged, now=now)
                surface = build_unflagged_surface(unflagged)
                if surface:
                    parts.append(surface)
            # Needs the team name to read the registry, so it lives here rather
            # than with the task-only alarms above.
            unanchored = find_unanchored_waits(tasks, team_name, now=now)
            if unanchored:
                surface = build_unanchored_surface(unanchored)
                if surface:
                    parts.append(surface)
    except Exception:
        pass

    if not parts:
        return None
    surface = "\n\n".join(parts)
    # An in-process teammate's compaction arrives lead-shaped, and nothing at
    # this point can tell it from the lead's, so the scan runs for both. The
    # clause lets a teammate that receives this surface set it aside.
    if input_data.get("hook_event_name") == "SessionStart" and input_data.get("source") == "compact":
        surface = f"{COMPACTION_TEAMMATE_CLAUSE}\n\n{surface}"
    return surface


def main() -> None:
    # Outer catch-all preserves the exit-0 contract against any unexpected
    # exception. The bare `except Exception` is deliberate — livelock-safety via
    # the "exits 0 on every code path" invariant outweighs observability here; a
    # UserPromptSubmit / SessionStart hook emitting error output on every dispatch
    # is the livelock-capable failure class the categorical standard forbids.
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        # A non-dict stdin payload would crash is_lead/.get(...) calls and
        # violate the exit-0 invariant.
        if not isinstance(input_data, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        pact_context.init(input_data)
        surface = run_surface(input_data)
        if surface:
            # Echo the firing event's name back per the additionalContext
            # contract (hookSpecificOutput.hookEventName MUST match the event —
            # session_init.py / bootstrap_prompt_gate.py establish the shape).
            event = input_data.get("hook_event_name")
            if not isinstance(event, str) or event not in _SURFACE_EVENTS:
                # The hook is registered only on the two surface events; fall back
                # to UserPromptSubmit if stdin omitted a recognizable name.
                event = "UserPromptSubmit"
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": surface,
                }
            }))
        else:
            print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    except SystemExit:
        # Re-raise — the explicit sys.exit(0) paths above are expected
        # control-flow, not errors.
        raise
    except Exception:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
