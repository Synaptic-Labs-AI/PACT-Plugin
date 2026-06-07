#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/missed_wake_scan.py
Summary: UserPromptSubmit + SessionStart hook — lead-side missed-wake SURFACER.
         On the lead's turn-start (UserPromptSubmit) or a session start
         (SessionStart), re-scans the team's task list for a teammate idling on
         intentional_wait.reason == "awaiting_lead_completion" past the
         staleness threshold and SURFACES an actionable additionalContext
         prompt so the lead actually sends the forgotten paired wake-SendMessage.
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

WHY LEAD-SIDE (is_lead-gated): the missed wake is a LEAD failure (the lead wrote
completion metadata but forgot the paired wake), and only the lead can ACT on
the alarm AND write the canonical journal (journal-resolvability is
process-scoped). Teammate / plain frames no-op — the in-process-default
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

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add hooks directory to path for shared package imports (mirrors teammate_idle.py).
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import shared.pact_context as pact_context
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


def find_stale_missed_wakes(tasks: list) -> list:
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
        if not wait_stale(wait):
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


def emit_forensic(stale: list) -> None:
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
        "awaiting_lead_completion past the staleness threshold. You likely wrote "
        "their completion metadata but did NOT send the paired wake-SendMessage, "
        "so they are stranded (an idle teammate cannot self-wake). ACTION: send a "
        "wake-SendMessage to each (or re-set / complete the task) — this notice "
        "re-shows every turn until the wait resolves.\n" + "\n".join(lines)
    )


def run_surface(input_data: dict) -> "str | None":
    """Lead-side missed-wake surface + forensic emit. is_lead-gated; teammate /
    plain frames no-op (the structural fail-safe default).

    Returns the additionalContext string when a still-stale awaiting_lead_completion
    wait exists, else None. The SAME live re-scan feeds both the forensic emit and
    the surface text — current-stale-state is the dedup, so the notice
    auto-clears when the lead resolves the wait.
    """
    if not is_lead(input_data):
        return None
    tasks = get_task_list()
    if not tasks:
        return None
    stale = find_stale_missed_wakes(tasks)
    if not stale:
        return None
    emit_forensic(stale)
    return build_surface(stale)


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
