#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/missed_wake_scan.py
Summary: Stop + SessionStart hook — lead-side DEFERRED missed-wake alarm. On
         the lead's turn-end (Stop) or a session start (SessionStart), scans
         the team's task list for a teammate idling on
         intentional_wait.reason == "awaiting_lead_completion" past the
         staleness threshold and emits a RE-ARMABLE `missed_wake` journal
         event, so a forgotten paired wake-SendMessage surfaces.
Used by: hooks.json Stop + SessionStart registration.

WHY DEFERRED (not synchronous): SendMessage fires no hookable event and the
inbox is written async-on-delivery, so a synchronous wake-confirmation read at
TaskUpdate-fire time races the write and is dead-by-construction (the retired
completion_no_paired_send was ~100% false-positive for exactly this reason).
This alarm instead keys on the DURATION of the wait: it checks, at a later
lead-action boundary (Stop / SessionStart), whether a teammate has idled on
awaiting_lead_completion past wait_stale()'s threshold — by which time a wake,
if sent, would already have landed.

WHY LEAD-SIDE (is_lead-gated): the missed wake is a LEAD failure (the lead
wrote completion metadata but forgot the paired wake-SendMessage), and only the
lead's process can both (a) act on the alarm and (b) write the canonical journal
(journal-resolvability is process-scoped — a teammate process has no persisted
session-context). Teammate / plain frames no-op — the in-process-default
fail-safe branch. Activation keys on a RUNTIME STRUCTURAL signal (is_lead via
agent_type), never a mode flag.

CARRIER = the lead's own `Stop` (turn-end, lead's process, BOTH topologies,
independent of teammate presence; highest cadence — catches a stale wait at the
next lead turn-end). This is DISTINCT from the Stop-*sweep* that skips
already-completed tasks: this scan reads IN_PROGRESS waiters, so the sweep's
completed-skip is irrelevant. SessionStart provides a cross-session recovery
backstop (a stale wait left by a prior session surfaces on the next start).

RE-ARMABLE dedup: a missed_wake fires at most once per (team, task_id, since).
Keying the marker on the intentional_wait `since` makes it re-fire on a LATER
stale cycle — a re-SET wait gets a fresh `since` -> new marker key -> re-arms.
This is NOT the fire-once `.agent_handoff_emitted` namespace (reusing that would
permanently suppress recovery); it lives in its own `.missed_wake_emitted/`
namespace and mirrors that module's hardened symlink/TOCTOU defenses.

# livelock-safe: emits on stale-CROSSING only (O_EXCL marker per
# (team, task_id, since)); exits 0 suppressOutput on EVERY code path; does NOT
# emit systemMessage / stderr prompts on the steady-state path; does NOT consume
# intentional_wait; does NOT block. Mark-then-write with a writability
# precondition + compensating unclaim so a write failure cannot poison the
# marker for the (still-unresolved) wait.

Input: JSON from stdin (Stop / SessionStart schema; agent_type is the role
       discriminator).
Output: {"suppressOutput": true} on every path; exit 0.
"""

import errno
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Add hooks directory to path for shared package imports (mirrors teammate_idle.py).
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

import shared.pact_context as pact_context
from shared.intentional_wait import validate_wait, wait_stale
from shared.pact_context import get_team_name, is_lead
from shared.session_journal import append_event, get_journal_path, make_event
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

# Hex chars of the SHA-256 digest retained for the `since` component of the
# re-arm marker key. 16 hex chars = 64 bits — collision-negligible for the tiny
# per-(team, task_id) since-namespace, while keeping the filename short.
_SINCE_HASH_LEN = 16


def _sanitize_path_component(value: str) -> str:
    """Strip path-traversal fragments + C0 control chars from a filesystem-join value.

    Mirrors shared.agent_handoff_marker.sanitize_path_component. Kept local
    rather than imported so this #903 marker does NOT couple to the
    backend-owned agent_handoff marker module and lives in its OWN re-armable
    namespace. Strips `/`, `\\`, `..`, and the 0x00-0x1f control range.
    """
    return re.sub(r"[/\\\x00-\x1f]|\.\.", "", value)


def _since_hash(since: str) -> str:
    """Return a stable, path-safe hash of the intentional_wait `since` value.

    hashlib (NOT builtin hash()): CPython salts str hashing with PYTHONHASHSEED,
    so builtin hash() differs across processes and would defeat the
    cross-process O_EXCL dedup. The `since` is the RE-ARM discriminator: a
    re-SET wait gets a fresh `since` -> a different key -> the marker re-arms and
    the alarm re-fires for the new wait cycle.
    """
    return hashlib.sha256(since.encode("utf-8")).hexdigest()[:_SINCE_HASH_LEN]


def _marker_dir(team_name: str) -> Path:
    """Per-team re-armable missed-wake marker directory.

    Lives under ~/.claude/teams/{team}/.missed_wake_emitted/ — a SEPARATE
    namespace from .agent_handoff_emitted (which is fire-once; reusing it would
    permanently suppress missed-wake recovery). session_end.py's team reaper
    removes the whole team directory, so these markers are cleaned up when the
    team ages out.
    """
    return Path.home() / ".claude" / "teams" / team_name / ".missed_wake_emitted"


def already_emitted(team_name: str, task_id: str, since: str) -> bool:
    """Re-armable test-and-set for the per-(team, task_id, since) missed_wake marker.

    Returns True iff a prior fire for the SAME (team, task_id, since) already
    created the marker (caller should suppress the journal write). Returns False
    on a fresh fire — the marker is created as a side-effect, making the
    test-and-set atomic at the kernel level (O_CREAT | O_EXCL) — and on any
    fail-open path.

    RE-ARMABLE: the marker filename is f"{task_id}-{hash(since)}". A re-SET wait
    (fresh `since`) -> new key -> not-yet-claimed -> re-fires. Within ONE
    (task, since) cycle the marker dedups repeated Stop fires to a single
    missed_wake event (no nag spam).

    Mirrors the hardened symlink/TOCTOU defenses of
    shared.agent_handoff_marker.already_emitted, in this module's OWN namespace:
    symlink pre-check, mkdir, commonpath containment re-check, and an
    intermediate-dir pin via O_DIRECTORY|O_NOFOLLOW dir_fd with an
    openat-relative O_CREAT|O_EXCL|O_NOFOLLOW create.

    Fail-open: on any OSError other than EEXIST (permission denied, ENOSPC,
    filesystem race, symlink breach), returns False so the caller emits anyway
    — surfacing the missed wake outweighs duplicate-prevention when the marker
    subsystem itself breaks.
    """
    team_name = _sanitize_path_component(team_name.lower())
    task_id = _sanitize_path_component(task_id)
    since_key = _since_hash(since)

    # Degenerate post-sanitization values collapse the marker path onto an
    # existing directory; emit rather than suppress (accept rare duplication
    # over silent event loss). since_key is always 16 hex chars for any
    # non-empty since, but a guard keeps the contract explicit.
    if (
        not team_name
        or team_name in (".", "..")
        or not task_id
        or task_id in (".", "..")
        or not since_key
    ):
        return False

    marker_dir = _marker_dir(team_name)
    # Symlink-containment pre-check: refuse a pre-planted symlink that could
    # redirect marker creation outside the team directory.
    if marker_dir.is_symlink():
        return False
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return False

    # TOCTOU containment re-check (commonpath, not str.startswith — defeats the
    # /teams/foo vs /teams/foobar prefix-collision). is_relative_to is 3.9+;
    # pyproject pins requires-python lower, so commonpath is the portable test.
    team_base = Path.home() / ".claude" / "teams" / team_name
    try:
        real_marker = os.path.realpath(marker_dir)
        real_base = os.path.realpath(team_base)
        if os.path.commonpath([real_marker, real_base]) != real_base:
            return False
    except (OSError, ValueError):
        return False

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    # Intermediate-dir TOCTOU close-out: pin marker_dir as a dir_fd opened with
    # O_DIRECTORY|O_NOFOLLOW (a symlinked marker_dir makes THIS open fail ->
    # fail-open), then create the marker RELATIVE to that pinned fd so the
    # directory identity is held by the descriptor, not re-resolved by path.
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    try:
        dir_fd = os.open(str(marker_dir), dir_flags)
    except OSError:
        return False
    try:
        fd = os.open(
            f"{task_id}-{since_key}",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=dir_fd,
        )
        os.close(fd)
        return False  # we created it; proceed with emit
    except OSError as e:
        if e.errno == errno.EEXIST:
            return True  # prior fire owns the marker; suppress
        return False  # any other error (incl. ELOOP) -> fail-open, emit anyway
    finally:
        os.close(dir_fd)


def _unclaim(team_name: str, task_id: str, since: str) -> None:
    """Best-effort removal of the marker THIS process just created.

    Compensating-unclaim for the optimistic mark-then-write ordering: if
    append_event fails AFTER we claimed the marker, unlink it so a later Stop
    fire for the SAME (task, since) retries instead of being permanently
    suppressed. This matters because the lead may never re-SET the wait — that
    is exactly the forgotten-wake case, so a since-keyed re-arm would never
    trigger on its own. Fail-safe: any error reverts to the
    suppressed-until-re-set behavior, never raises.
    """
    try:
        team = _sanitize_path_component(team_name.lower())
        tid = _sanitize_path_component(task_id)
        since_key = _since_hash(since)
        if not team or not tid or not since_key:
            return
        (_marker_dir(team) / f"{tid}-{since_key}").unlink(missing_ok=True)
    except OSError:
        pass


def find_stale_missed_wakes(tasks: list) -> list:
    """Return the tasks idling on awaiting_lead_completion past the staleness threshold.

    A task qualifies iff: status == "in_progress" AND metadata.intentional_wait
    is a WELL-FORMED wait (validate_wait) with reason == awaiting_lead_completion
    AND wait_stale() (reusing the existing 30-min threshold in
    shared/intentional_wait.py — staleness logic is NOT reinvented here).
    validate_wait gates first so a malformed wait (which wait_stale would treat
    as stale) does not produce a missed_wake with a malformed `since`. Pure;
    never raises on plain dicts.
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


def emit_missed_wake(team_name: str, task: dict) -> None:
    """Emit a single re-armable missed_wake journal event for one stale task.

    Order — mark-then-write with a writability precondition + compensating
    unclaim:
    1. Resolve the load-bearing fields; skip if any required field is empty
       (keeps the journal schema's non-empty-str contract satisfied).
    2. Writability precondition: get_journal_path() must resolve — gate the
       marker claim on it so a non-writable fire cannot claim-without-write and
       suppress a writable retry (the #917 poisoning class).
    3. Claim the re-armable marker; suppress if already emitted this (task, since).
    4. append_event; on failure, compensating-unclaim so a later fire retries.

    Best-effort; never raises (the caller preserves the exit-0 contract).
    """
    try:
        task_id = str(task.get("id") or "")
        owner = task.get("owner") or ""
        wait = (task.get("metadata") or {}).get("intentional_wait") or {}
        since = wait.get("since") or ""
        if not task_id or not owner or not since:
            return

        # Writability precondition before the optimistic marker claim (#917).
        if not get_journal_path():
            return

        if already_emitted(team_name, task_id, since):
            return

        fields = {"task_id": task_id, "agent": owner, "since": since}
        subject = task.get("subject") or ""
        if subject:
            fields["task_subject"] = subject
        fields["reason"] = _MISSED_WAKE_REASON

        if not append_event(make_event("missed_wake", **fields)):
            _unclaim(team_name, task_id, since)
    except Exception:
        pass


def run_scan(input_data: dict) -> None:
    """Lead-side deferred missed-wake scan. is_lead-gated; teammate/plain no-op.

    The is_lead gate is the structural, fail-safe-default discriminator: only
    the lead's process scans + writes the canonical journal. In-process the
    single (lead) process runs it; in tmux the lead's Stop fires lead-side while
    a teammate's Stop no-ops here.
    """
    if not is_lead(input_data):
        return
    team_name = get_team_name()
    if not team_name:
        return
    tasks = get_task_list()
    if not tasks:
        return
    for task in find_stale_missed_wakes(tasks):
        emit_missed_wake(team_name, task)


def main() -> None:
    # Outer catch-all preserves the exit-0 suppressOutput contract against any
    # unexpected exception. The bare `except Exception` is deliberate —
    # livelock-safety via the "exits 0 on every code path" invariant outweighs
    # observability here; a Stop/SessionStart hook emitting error output on
    # every dispatch is the livelock-capable failure class the categorical
    # standard forbids.
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
        run_scan(input_data)
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
