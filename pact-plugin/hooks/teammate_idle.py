#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teammate_idle.py
Summary: TeammateIdle hook — resource-management for zombie teammates and a
         bounded positive-inbox grace gate for fresh lead-completion waits.
         Tracks consecutive idle events for teammates whose task is
         completed; at threshold N=3 suggests shutdown, at N=5 advises the
         team-lead to `TaskStop` the teammate.
Used by: hooks.json TeammateIdle hook

# livelock-safe: threshold-escalation, not a nag. Message emissions
# transition only at the IDLE_SUGGEST_THRESHOLD and IDLE_FORCE_THRESHOLD
# boundaries — not every idle tick. Above IDLE_FORCE_THRESHOLD the stop
# advisory is re-emitted per tick until the team-lead processes it and the
# teammate is stopped (safety is protocol-level, not a structural cap).
# A fresh awaiting_lead_completion wait polls for positive inbox evidence at
# 0.1-second intervals only within 10 seconds of its existing `since`; empty
# input never blocks, and later idles cannot restart that budget. Exits
# deterministically on every code path. Does NOT consume intentional_wait.

Idle cleanup: Track consecutive idle events for completed agents. After 3,
suggest shutdown. After 5, advise the team-lead to `TaskStop` the teammate.

Input: JSON from stdin with teammate_name, team_name
Output: JSON with systemMessage (shutdown suggestion / stop advisory)
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.background_work import frame_team_and_name
from shared.intentional_wait import validate_wait
from shared.paths import get_claude_config_dir
from shared.session_state import is_safe_path_component
from shared.task_utils import iter_team_task_jsons
from shared import state_file


# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

IDLE_PREAMBLE = "[System idle notification — no response needed] "

IDLE_SUGGEST_THRESHOLD = 3
IDLE_FORCE_THRESHOLD = 5


def find_teammate_task(
    tasks: list[dict],
    teammate_name: str,
) -> dict | None:
    """
    Find the most recent task owned by this teammate.

    Looks for tasks with owner matching teammate_name. Returns the
    in_progress task if one exists, otherwise the most recently completed one.

    Args:
        tasks: The team's tasks, as main() reads them
        teammate_name: Name of the idle teammate

    Returns:
        Task dict, or None if no task found for this teammate
    """
    in_progress = None
    completed = None

    for task in tasks:
        owner = task.get("owner", "")
        if owner != teammate_name:
            continue

        status = task.get("status", "")
        if status == "in_progress":
            in_progress = task
        elif status == "completed":
            # Keep the highest-ID completed task (most recent)
            # Task IDs are numeric strings — compare as int to avoid
            # lexicographic errors (e.g., "3" > "20" in string comparison)
            try:
                task_id_num = int(task.get("id", "0"))
                completed_id_num = int(completed.get("id", "0")) if completed else -1
            except (ValueError, TypeError):
                task_id_num = 0
                completed_id_num = -1 if completed is None else 0
            if completed is None or task_id_num > completed_id_num:
                completed = task

    return in_progress or completed


GRACE_SECONDS = 10.0
POLL_SECONDS = 0.1
INBOX_MAX_BYTES = 1024 * 1024
INBOX_BLOCK_REASON = (
    "A valid teammate message is queued. Process the inbox normally now, then "
    "fresh-read the current task JSON before any completion action. The queued "
    "message is not proof of acceptance; do not send a redundant confirmation "
    "merely because this continuation fired."
)


def _has_valid_inbox_message(messages: list) -> bool:
    """Only minimal positive message shape counts; payload text is never used."""
    return any(
        isinstance(message, dict)
        and isinstance(message.get("from"), str)
        and bool(message["from"])
        and isinstance(message.get("text"), str)
        and bool(message["text"])
        for message in messages
    )


def _select_grace_task(
    tasks: list[dict], teammate_name: str, wall_now: datetime
) -> tuple[dict, float] | None:
    """Select the earliest eligible wait, breaking ties by numeric task ID."""
    candidates = []
    for task in tasks:
        if (
            task.get("owner") != teammate_name
            or task.get("status") not in {"in_progress", "completed"}
        ):
            continue
        metadata = task.get("metadata", {})
        wait = metadata.get("intentional_wait") if isinstance(metadata, dict) else None
        if not validate_wait(wait) or wait.get("reason") != "awaiting_lead_completion":
            continue
        since = datetime.fromisoformat(wait["since"].replace("Z", "+00:00"))
        age = (wall_now - since).total_seconds()
        if not 0 <= age < GRACE_SECONDS:
            continue
        try:
            task_id = int(task.get("id", "0"))
        except (ValueError, TypeError):
            task_id = 0
        candidates.append((since, task_id, task, age))
    if not candidates:
        return None
    _since, _task_id, task, age = min(candidates, key=lambda item: (item[0], item[1]))
    return task, age


def _contains_invalid_unicode(value) -> bool:
    """Reject replacement and surrogate codepoints in decoded JSON containers."""
    if isinstance(value, str):
        return any(
            character == "�" or "\ud800" <= character <= "\udfff"
            for character in value
        )
    if isinstance(value, list):
        return any(_contains_invalid_unicode(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_invalid_unicode(key) or _contains_invalid_unicode(item)
            for key, item in value.items()
        )
    return False


def check_inbox_grace(
    tasks: list[dict],
    teammate_name: str,
    team_name: str,
    teams_root: Path,
    *,
    now: datetime | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict | None:
    """Hold a fresh lead-completion idle only for positive inbox evidence."""
    wall_now = now if now is not None else datetime.now(timezone.utc)
    selected = _select_grace_task(tasks, teammate_name, wall_now)
    if selected is None:
        return None
    _task, age = selected
    if not (
        is_safe_path_component(team_name)
        and is_safe_path_component(teammate_name)
    ):
        return None

    inbox_dir = teams_root / team_name / "inboxes"
    inbox_path = inbox_dir / f"{teammate_name}.json"
    deadline = monotonic() + GRACE_SECONDS - age
    while monotonic() < deadline:
        try:
            inbox_json = state_file.read_text(
                inbox_path, root=teams_root, max_bytes=INBOX_MAX_BYTES
            )
        except FileNotFoundError as error:
            # read_text wraps containment refusals in FileNotFoundError; only absence retries.
            if error.__cause__ is not None:
                return None
            inbox_json = "[]"
        except (OSError, ValueError):
            return None
        # Invalid UTF-8 is replaced by state_file; decoded replacement is not evidence.
        if "�" in inbox_json:
            return None
        try:
            messages = json.loads(inbox_json)
        except json.JSONDecodeError:
            pass
        else:
            if not isinstance(messages, list):
                return None
            if _contains_invalid_unicode(messages):
                return None
            if _has_valid_inbox_message(messages):
                return {"decision": "block", "reason": INBOX_BLOCK_REASON}

        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(POLL_SECONDS, remaining))
    return None


def _state_root(path: Path, root: Path | None) -> Path:
    """The directory an idle-count file must stay under.

    None is for callers that own their path (tests); production entry points
    pass the config root, so containment applies to the path main() builds.
    """
    return root if root is not None else path.parent


def read_idle_counts(idle_counts_path: str, root: Path | None = None) -> dict:
    """
    Read the idle counts tracking file.

    Args:
        idle_counts_path: Path to the idle_counts.json file
        root: Directory the file must stay under (see `_state_root`)

    Returns:
        Dict mapping teammate_name to consecutive idle count
    """
    path = Path(idle_counts_path)
    try:
        return json.loads(state_file.read_text(path, _state_root(path, root)))
    except (json.JSONDecodeError, OSError):
        return {}


def write_idle_counts(
    idle_counts_path: str, counts: dict, root: Path | None = None
) -> None:
    """
    Replace the idle counts tracking file under its state-file lock.

    Args:
        idle_counts_path: Path to the idle_counts.json file
        counts: Dict mapping teammate_name to consecutive idle count
        root: Directory the file must stay under (see `_state_root`)
    """
    path = Path(idle_counts_path)
    state_file.write_text(path, json.dumps(counts), _state_root(path, root))


def _atomic_update_idle_counts(
    idle_counts_path: str,
    mutator: Callable[[dict], dict],
    root: Path | None = None,
) -> dict:
    """
    Atomically read, mutate, and write the idle counts file under a single lock.

    This prevents TOCTOU races where two concurrent TeammateIdle events both
    read stale state before either writes, causing one update to be lost. The
    new content is written to a temp file and swapped in, so a failure part-way
    leaves the previous file intact.

    The mutator may run twice (once on empty input when the file is absent), so
    it must derive its result from the counts it is given.

    Args:
        idle_counts_path: Path to the idle_counts.json file
        mutator: Callable that receives the current counts dict and returns
                 the updated counts dict to write back
        root: Directory the file must stay under (see `_state_root`)

    Returns:
        The updated counts dict after mutation
    """
    path = Path(idle_counts_path)

    def _apply(content: str):
        try:
            counts = json.loads(content) if content.strip() else {}
        except json.JSONDecodeError:
            counts = {}
        counts = mutator(counts)
        return json.dumps(counts), True, counts

    return state_file.locked_update(path, _apply, _state_root(path, root))


def check_idle_cleanup(
    tasks: list[dict],
    teammate_name: str,
    idle_counts_path: str,
    root: Path | None = None,
) -> tuple[str | None, bool]:
    """
    Track idle counts for completed agents and determine cleanup action.

    Only counts idles for teammates whose task is completed (not stalled agents,
    which need triage, not shutdown). Resets count when the teammate's task
    changes (detected via last_seen_task_id).

    The idle counts file stores per-teammate entries as:
        {teammate_name: {"count": N, "task_id": "X"}}

    Args:
        tasks: List of all tasks
        teammate_name: Name of the idle teammate
        idle_counts_path: Path to the idle_counts.json file
        root: Directory the file must stay under (see `_state_root`)

    Returns:
        Tuple of (message, should_force_shutdown):
        - message: systemMessage text or None
        - should_force_shutdown: True if the team-lead should be advised to
          `TaskStop` this teammate
    """
    task = find_teammate_task(tasks, teammate_name)

    # Only track idles for completed tasks
    if not task or task.get("status") != "completed":
        # Reset count if agent no longer has a completed task (got new work)
        def _remove(counts: dict) -> dict:
            counts.pop(teammate_name, None)
            return counts
        _atomic_update_idle_counts(idle_counts_path, _remove, root=root)
        return None, False

    # Don't count stalled agents for idle cleanup — they need triage
    metadata = task.get("metadata", {})
    if metadata.get("stalled") or metadata.get("terminated"):
        return None, False

    current_task_id = task.get("id", "")

    # Atomically read-modify-write the idle count to prevent TOCTOU races
    # between concurrent TeammateIdle events for different agents.
    result = {"count": 0}

    def _increment(counts: dict) -> dict:
        entry = counts.get(teammate_name, {})

        # Migrate legacy format: plain int -> structured dict
        if isinstance(entry, int):
            entry = {"count": entry, "task_id": ""}

        # Reset count if the teammate's task changed (reassigned to new work)
        last_task_id = entry.get("task_id", "")
        if last_task_id and last_task_id != current_task_id:
            entry = {"count": 0, "task_id": current_task_id}

        # Increment idle count
        entry["count"] = entry.get("count", 0) + 1
        entry["task_id"] = current_task_id
        counts[teammate_name] = entry

        # Capture the count for the caller via closure
        result["count"] = entry["count"]
        return counts

    _atomic_update_idle_counts(idle_counts_path, _increment, root=root)
    current = result["count"]

    if current >= IDLE_FORCE_THRESHOLD:
        return (
            f"Teammate '{teammate_name}' has been idle for {current} consecutive "
            f"events with no new work. Recommending shutdown."
        ), True

    if current >= IDLE_SUGGEST_THRESHOLD:
        return (
            f"Teammate '{teammate_name}' has been idle for {current} consecutive "
            f"events with no new work. Consider shutting down to free resources."
        ), False

    return None, False


UNFLAGGED_ADVISORY = (
    "You launched background work and have no flagged wait; it may already "
    "have finished. Either collect "
    "the result now, or SET metadata.intentional_wait on the task, naming what "
    "you are waiting for. validate_wait accepts a free-form reason."
)


def _clear_unflagged_idle(teammate_name: str, team_name: str) -> None:
    from shared.background_work import update_unflagged_idle_counts

    def _drop(counts: dict) -> dict:
        counts.pop(teammate_name, None)
        return counts

    update_unflagged_idle_counts(_drop, team_name)


def check_unflagged_background(
    tasks: list, teammate_name: str, team_name: str, now: datetime | None = None
) -> str | None:
    """Layer 2 — advise once at three consecutive unflagged idles.

    Uses `unflagged_background_idle.json`, NOT `idle_counts.json`. That file's
    writer pops the teammate key on every tick where the task is not
    `completed`, and this counter's entire population is a teammate idling on
    an `in_progress` task — the exact branch that pops. Sharing the file would
    reset the counter every tick and this could never reach three.
    """
    from shared.background_work import (
        UNFLAGGED_IDLE_THRESHOLD,
        discharge_acknowledged_for_owner,
        stamp_idled_at,
        teammate_is_separate_process,
        unflagged_fire,
        update_unflagged_idle_counts,
    )

    # DISCHARGE BEFORE TESTING, AND BEFORE THE STATUS GATE. A teammate that
    # flagged a wait covering its launch has demonstrably associated the two,
    # so the record has done its job and must not outlive the acknowledgment.
    #
    # It covers EVERY task this teammate owns, whatever its status, in ONE
    # registry update. A teammate owning no in_progress task still has records
    # (anchored on its most recently completed task) and still idles, so a
    # discharge placed behind the status gate below would never retire them.
    # And a record lists the tasks held at LAUNCH, which need not include the
    # task resolved here: a teammate holding two tasks may flag the other one,
    # and a consultant may have completed a newer task since. A record is
    # dropped only when a task it lists is owned here and that task's wait
    # covers its launch, so a teammate with no covering wait drops nothing.
    #
    # WHAT THIS BUYS, STATED AT ITS ACTUAL SIZE: an ACCURATE CITATION, not the
    # removal of a false alarm. Without it, a teammate that flagged, collected
    # the result and cleared the wait keeps a live record, and a LATER
    # unflagged idle draws an advisory naming a job that finished hours ago.
    # The advice is still correct at that moment — the counter below only
    # reaches its threshold on three CONSECUTIVE idles with no valid wait, so
    # the teammate really is idling unflagged — but the reason given is stale.
    # An agent that checks, finds the job long done, and concludes the alarm
    # is unreliable is the cost this prevents.
    #
    # DO NOT RESTATE THIS AS "the alarm would otherwise fire at teammates who
    # behaved correctly". That is FALSE and was checked: the counter is
    # cleared on every tick where the fire predicate is false, so a teammate
    # that flags never accumulates toward the threshold, and one that keeps
    # working does not tick at all.
    discharge_acknowledged_for_owner(tasks, teammate_name, team_name=team_name, now=now)

    # A separate-process (tmux) teammate is woken by its own background
    # completion and collects the result on that turn, so the record outlives
    # the job and an idle-count advisory would name work it already has. The
    # discharge above still runs for it; only the advisory is skipped.
    if teammate_is_separate_process(team_name, teammate_name):
        _clear_unflagged_idle(teammate_name, team_name)
        return None

    task = find_teammate_task(tasks, teammate_name)
    if not task or task.get("status") != "in_progress":
        _clear_unflagged_idle(teammate_name, team_name)
        return None

    fire, _wait_class, record = unflagged_fire(
        task, team_name=team_name, tasks=tasks, now=now
    )
    if not fire or record is None:
        _clear_unflagged_idle(teammate_name, team_name)
        return None

    task_id = str(task.get("id") or "")
    if task_id:
        stamp_idled_at(task_id, team_name=team_name, now=now)

    result = {"emit": False}

    def _bump(counts: dict) -> dict:
        entry = counts.get(teammate_name, {})
        if isinstance(entry, int):
            entry = {"count": entry, "task_id": ""}
        if not isinstance(entry, dict):
            entry = {}
        last_task_id = entry.get("task_id", "")
        if last_task_id and last_task_id != task_id:
            entry = {"count": 0, "task_id": task_id}
        # TOTAL COERCION. A hand-edited or corrupted counter file can carry a
        # non-numeric `count`, and a bare int() raises ValueError from inside
        # the atomic update. Treating an unreadable count as 0 restarts the
        # threshold rather than crashing the tick — the advisory fires later
        # than it might have, which is the safe direction for an alarm.
        try:
            current = int(entry.get("count", 0) or 0)
        except (TypeError, ValueError):
            current = 0
        # Emit once at N == threshold; later same-task ticks must not re-emit.
        if last_task_id == task_id and current >= UNFLAGGED_IDLE_THRESHOLD:
            return counts
        entry["count"] = current + 1
        entry["task_id"] = task_id
        counts[teammate_name] = entry
        result["emit"] = entry["count"] == UNFLAGGED_IDLE_THRESHOLD
        return counts

    update_unflagged_idle_counts(_bump, team_name)
    return UNFLAGGED_ADVISORY if result["emit"] else None


def reset_idle_count(
    teammate_name: str, idle_counts_path: str, root: Path | None = None
) -> None:
    """
    Reset a teammate's idle count (e.g., when they receive new work).

    Args:
        teammate_name: Name of the teammate
        idle_counts_path: Path to the idle_counts.json file
        root: Directory the file must stay under (see `_state_root`)
    """
    def _remove(counts: dict) -> dict:
        counts.pop(teammate_name, None)
        return counts
    _atomic_update_idle_counts(idle_counts_path, _remove, root=root)


def _in_teammate_process(input_data: dict, team_name: str) -> bool:
    """True iff this call runs in a separate-process teammate's own process.

    All three must hold: no PACT context (only the lead's session gets one), a
    frame `session_id`, and a team-config `leadSessionId` that differs from it.
    A lead whose context file is missing still matches its own session, so it
    keeps the idle cleanup, and any value that cannot be read keeps it too.
    """
    if pact_context.get_team_name():
        return False
    session_id = input_data.get("session_id")
    lead_session_id = pact_context._read_lead_session_id(team_name)
    return (
        isinstance(session_id, str)
        and bool(session_id)
        and bool(lead_session_id)
        and session_id != lead_session_id
    )


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        team_name, _name = frame_team_and_name(input_data)
        if not team_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        teammate_name = input_data.get("teammate_name", "")
        if not teammate_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        tasks = list(iter_team_task_jsons(team_name))
        if not tasks:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        teams_root = get_claude_config_dir() / "teams"
        idle_counts_path = str(teams_root / team_name / "idle_counts.json")

        try:
            grace_decision = check_inbox_grace(
                tasks, teammate_name, team_name, teams_root
            )
        except Exception:
            grace_decision = None
        if grace_decision is not None:
            print(json.dumps(grace_decision))
            sys.exit(0)

        messages = []
        cleanup_msg, should_shutdown = None, False
        # Idle cleanup addresses the lead ("Consider shutting down", TaskStop).
        # In a separate-process teammate's own process this hook's output
        # reaches the teammate itself, so only the teammate-facing Layer 2
        # check below runs there.
        if not _in_teammate_process(input_data, team_name):
            cleanup_msg, should_shutdown = check_idle_cleanup(
                tasks, teammate_name, idle_counts_path, root=teams_root
            )
        if cleanup_msg:
            messages.append(cleanup_msg)

        # APPENDED, NOT `elif`. The cleanup message and this advisory come
        # from predicates that are mutually exclusive TODAY — cleanup fires on
        # `completed` tasks, the advisory requires `in_progress` — so an
        # `elif` would be equivalent now and silently lossy the moment either
        # predicate widens. Appending costs nothing and does not depend on
        # that coincidence holding.
        # Own try/except: an advisory must never cost the zombie cleanup.
        try:
            unflagged_msg = check_unflagged_background(
                tasks, teammate_name, team_name
            )
            if unflagged_msg:
                messages.append(unflagged_msg)
        except Exception:
            pass

        if messages:
            if should_shutdown:
                # Hooks cannot call tools directly. Instruct the orchestrator to
                # stop the teammate via systemMessage.
                messages.append(
                    f"ACTION REQUIRED: TaskStop(\"{teammate_name}\") — call it "
                    f"directly; do NOT send a shutdown_request first."
                )

            output = {"systemMessage": IDLE_PREAMBLE + " | ".join(messages)}
            print(json.dumps(output))
        else:
            print(_SUPPRESS_OUTPUT)

        sys.exit(0)

    except Exception as e:
        # Don't block on errors — just warn and exit cleanly
        print(f"Hook warning (teammate_idle): {e}", file=sys.stderr)
        print(hook_error_json("teammate_idle", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
