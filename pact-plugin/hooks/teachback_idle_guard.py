#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teachback_idle_guard.py
Summary: TeammateIdle hook that tracks consecutive idle events for
         teammates stuck in teachback_under_review (valid submit,
         waiting on lead response) and emits an algedonic ALERT via
         systemMessage when the count reaches
         TEACHBACK_TIMEOUT_IDLE_COUNT (= 3).
Used by: hooks.json TeammateIdle hook (registered BETWEEN
         teammate_completion_gate and teammate_idle).

ARCHITECTURE (COMPONENT-DESIGN.md Hook 4):
  - Sidecar file ~/.claude/teams/{team_name}/teachback_idle_counts.json
    holds per-teammate {count, task_id, first_idle_ts}. Mirrors
    teammate_idle.py:184-232 atomic-update pattern (fcntl.flock,
    read-modify-write under exclusive lock). Task-id in the sidecar
    entry lets us reset the count when the teammate is reassigned
    (R4 recommendation).
  - Resets on resolution observation: teammate's in_progress task has
    teachback_approved with unaddressed=[], OR teachback_corrections
    present (lead responded — no longer algedonic), OR task_id changed.
  - Exit 0 always — this is a NOTIFY hook, not a BLOCK hook. Keeping
    the teammate "working" doesn't help when the LEAD is the blocker.
    Contrast with teammate_completion_gate which exits 2 because the
    teammate has actionable work.
  - Emits teachback_idle_algedonic journal event at threshold (+3, +4,
    +5, ...) so observers can see persistence. Per JOURNAL-EVENTS.md
    §Re-emit: re-emit at every count >= threshold rather than once at
    threshold, to let consumers count event persistence without
    needing an "already emitted" sidecar flag.

SACROSANCT fail-open: ANY exception at ANY layer exits 0 with
suppressOutput. Mirrors teammate_idle.py:395-399.

Input: JSON from stdin (TeammateIdle payload: teammate_name, team_name)
Output:
    At threshold: {"systemMessage": "<algedonic ALERT text>"}, exit 0
    Otherwise:    {"suppressOutput": true}, exit 0
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False

# Ensure hooks dir is on sys.path for shared package imports.
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared import (  # noqa: E402
    TEACHBACK_BLOCKING_THRESHOLD,
    TEACHBACK_TIMEOUT_IDLE_COUNT,
)
from shared.error_output import hook_error_json  # noqa: E402
import shared.pact_context as pact_context  # noqa: E402
from shared.pact_context import get_team_name  # noqa: E402
from shared.session_journal import append_event, make_event  # noqa: E402
from shared.session_state import is_safe_path_component  # noqa: E402
from shared.task_utils import get_task_list  # noqa: E402
from shared.teachback_scan import is_exempt_agent  # noqa: E402
from shared.teachback_validate import _strip_control_chars  # noqa: E402


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Prefixed so observability aggregators can grep for the signal without
# also catching non-PACT systemMessages.
_ALGEDONIC_PREAMBLE = (
    "[ALGEDONIC ALERT — teachback stall] "
)


def _sidecar_path(team_name: str) -> Path:
    """Path to the team-scoped teachback idle-count sidecar file.

    Co-located with teammate_idle.py's idle_counts.json but named
    distinctly so a single team's regular-idle tracking and teachback-
    idle tracking don't collide on writes.
    """
    return (
        Path.home() / ".claude" / "teams" / team_name
        / "teachback_idle_counts.json"
    )


def _find_teammate_task(
    tasks: list[dict], teammate_name: str
) -> dict | None:
    """Return the teammate's active in_progress task, or None.

    Mirrors teammate_idle.find_teammate_task structure but scoped to
    IN_PROGRESS only. Completed tasks are irrelevant to the teachback
    idle guard — a completed task can't be stuck awaiting lead review.
    """
    for task in tasks:
        if task.get("owner") != teammate_name:
            continue
        if task.get("status") != "in_progress":
            continue
        return task
    return None


def _inferred_state_needs_algedonic(metadata: dict) -> bool:
    """Return True iff the task is currently in teachback_under_review
    state per content-presence inference (STATE-MACHINE.md invariant #1).

    - approved with unaddressed=[] → active (no algedonic)
    - approved with unaddressed non-empty → correcting (lead responded;
      no longer algedonic — ball is in teammate's court)
    - corrections present → correcting (lead responded)
    - submit present, no approved/corrections → under_review (algedonic
      IF stall persists)
    - no submit → pending (teammate hasn't started; completion_gate
      handles this; teachback idle guard doesn't fire)
    """
    if not isinstance(metadata, dict):
        return False

    corrections = metadata.get("teachback_corrections")
    if isinstance(corrections, dict) and corrections:
        return False  # lead responded with corrections

    approved = metadata.get("teachback_approved")
    if isinstance(approved, dict) and approved:
        # Either active (no algedonic) or auto-downgraded to correcting
        # (ball in teammate's court — no algedonic). Either way no
        # algedonic.
        return False

    submit = metadata.get("teachback_submit")
    if isinstance(submit, dict) and submit:
        return True

    return False


def _atomic_update_idle_counts(
    sidecar_path: Path,
    mutator,
) -> dict:
    """Atomically read-modify-write the sidecar JSON under exclusive
    lock. Mirrors teammate_idle._atomic_update_idle_counts:184-232 but
    hardened per #401 cycle-3 fix B:

      - `mkdir(..., mode=0o700)` matches canonical PACT secure-by-default
        permission scheme (failure_log.py:128, session_journal.py:502,
        symlinks.py:47/66, pact_context.py:384, claude_md_manager.py:418).
      - mkdir wrapped in try/except OSError: return {} — closes the
        contract-leak where a PermissionError from mkdir propagated past
        the "fail-open: any OS error returns empty dict" promise.
      - `open(sidecar_path, "a+")` upgraded to `os.open(..., O_RDWR |
        O_CREAT | O_NOFOLLOW, 0o600)` + `os.fdopen` so a pre-existing
        symlink at the sidecar path fails the open with `ELOOP` rather
        than writing through to the symlink target. Matches failure_log's
        symlink-guard posture. Append-mode was not load-bearing — we
        always `seek(0)` before read and `seek(0) + truncate()` before
        write, so read/write mode suffices.

    Fail-open: any OS error (mkdir / os.open / flock / read / write /
    symlink-rejection) returns an empty dict without raising.
    """
    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return {}

    if HAS_FLOCK:
        fd = -1
        try:
            # O_NOFOLLOW rejects the open with ELOOP if sidecar_path is
            # a symlink. TOCTOU defense — no separate is_symlink() check
            # needed because the open itself is the atomic guard.
            fd = os.open(
                str(sidecar_path),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "r+", encoding="utf-8") as f:
                # os.fdopen has taken ownership of the fd; do NOT close
                # it in the except handler below.
                fd = -1
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    content = f.read()
                    try:
                        counts = json.loads(content) if content.strip() else {}
                    except json.JSONDecodeError:
                        counts = {}

                    counts = mutator(counts)

                    f.seek(0)
                    f.truncate()
                    f.write(json.dumps(counts))
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            return counts
        except OSError:
            # If os.fdopen raised before taking ownership, close the raw
            # fd to avoid a leak. After successful fdopen, fd was reset
            # to -1 above and the context manager closes the file on exit.
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
            return {}
    else:
        # Best-effort non-atomic fallback (Windows).
        try:
            if sidecar_path.exists():
                counts = json.loads(sidecar_path.read_text(encoding="utf-8") or "{}")
            else:
                counts = {}
        except (json.JSONDecodeError, OSError):
            counts = {}
        counts = mutator(counts)
        try:
            sidecar_path.write_text(json.dumps(counts), encoding="utf-8")
        except OSError:
            pass
        return counts


def _increment_teachback_idle(
    sidecar_path: Path,
    teammate_name: str,
    task_id: str,
) -> int:
    """Atomically increment the teammate's idle count. Reset to 1 if
    the stored task_id differs from current (agent reassigned to new
    work)."""
    result = {"count": 0}

    def _mutate(counts: dict) -> dict:
        entry = counts.get(teammate_name) or {}
        if not isinstance(entry, dict):
            entry = {}
        prior_task = entry.get("task_id", "")
        if prior_task and prior_task != task_id:
            entry = {"count": 0, "task_id": task_id}

        entry["count"] = int(entry.get("count", 0)) + 1
        entry["task_id"] = task_id
        counts[teammate_name] = entry
        result["count"] = entry["count"]
        return counts

    _atomic_update_idle_counts(sidecar_path, _mutate)
    return result["count"]


def _reset_teachback_idle(
    sidecar_path: Path,
    teammate_name: str,
) -> None:
    """Remove the teammate's entry from the sidecar. Called when a
    resolution observation (approved/corrections present, or task
    reassigned) lands."""
    def _mutate(counts: dict) -> dict:
        counts.pop(teammate_name, None)
        return counts

    _atomic_update_idle_counts(sidecar_path, _mutate)


def _check_teachback_idle(input_data: dict) -> tuple[str | None, dict]:
    """Return (algedonic_message, telemetry).

    algedonic_message is None when no alert should be emitted.
    telemetry carries task_id + agent + idle_count + variety_total
    for the journal event. Never raises — caller wraps in try/except
    for fail-open.
    """
    pact_context.init(input_data)

    teammate_name = input_data.get("teammate_name", "") or ""
    if not teammate_name:
        return (None, {})

    if is_exempt_agent(teammate_name):
        return (None, {})

    team_name = (input_data.get("team_name") or get_team_name() or "").lower()
    if not team_name:
        return (None, {})

    # Cycle 2 M2 path sanitization: reject any team_name that is not a
    # positive-regex path component before it reaches _sidecar_path.
    # An unsafe value like "../foo" would escape ~/.claude/teams/ and
    # read/write outside the team scope. Caller contract stays
    # fail-open (no algedonic emitted) via the (None, {}) return.
    if not is_safe_path_component(team_name):
        return (None, {})

    tasks = get_task_list()
    if not tasks:
        return (None, {})

    task = _find_teammate_task(tasks, teammate_name)
    sidecar = _sidecar_path(team_name)

    if not task:
        # No in_progress task — nothing to guard; clear stale entry.
        _reset_teachback_idle(sidecar, teammate_name)
        return (None, {})

    metadata = task.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    # Carve-outs: signal task, skipped, stalled, terminated, low-variety
    if metadata.get("type") in ("blocker", "algedonic"):
        _reset_teachback_idle(sidecar, teammate_name)
        return (None, {})
    if metadata.get("completion_type") == "signal":
        _reset_teachback_idle(sidecar, teammate_name)
        return (None, {})
    if metadata.get("skipped") or metadata.get("stalled") or metadata.get("terminated"):
        _reset_teachback_idle(sidecar, teammate_name)
        return (None, {})

    variety = metadata.get("variety")
    variety_total = 0
    if isinstance(variety, dict):
        t = variety.get("total")
        if isinstance(t, int) and not isinstance(t, bool):
            variety_total = t
    if variety_total < TEACHBACK_BLOCKING_THRESHOLD:
        _reset_teachback_idle(sidecar, teammate_name)
        return (None, {})

    # Only increment+alert when inferred state is under_review
    # (submit present, no approved/corrections response from lead yet).
    if not _inferred_state_needs_algedonic(metadata):
        _reset_teachback_idle(sidecar, teammate_name)
        return (None, {})

    task_id = task.get("id", "") or ""
    count = _increment_teachback_idle(sidecar, teammate_name, task_id)

    if count < TEACHBACK_TIMEOUT_IDLE_COUNT:
        return (None, {})

    # At or above threshold — emit an algedonic systemMessage.
    # Sanitize teammate_name before interpolation — defense-in-depth
    # against role-marker injection via the PR #426 unified strip set.
    # Mirrors the deny-reason rendering pathway in
    # teachback_example.format_deny_reason.
    safe_teammate_name = _strip_control_chars(teammate_name)
    message = (
        _ALGEDONIC_PREAMBLE
        + f"Teammate '{safe_teammate_name}' has been idle for {count} consecutive "
        + f"events while task #{task_id} is in teachback_under_review "
        + f"(variety={variety_total}). The lead has not written "
        + "metadata.teachback_approved OR metadata.teachback_corrections. "
        + "Review the teammate's teachback_submit and respond (approve or "
        + "request corrections) to unblock them."
    )
    telemetry = {
        "task_id": task_id,
        "agent_name": teammate_name,
        "idle_count": count,
        "variety_total": variety_total,
    }
    return (message, telemetry)


def _emit_algedonic_event(telemetry: dict) -> None:
    """Append the teachback_idle_algedonic journal event. Fail-open."""
    try:
        append_event(
            make_event(
                "teachback_idle_algedonic",
                task_id=telemetry.get("task_id", ""),
                agent=telemetry.get("agent_name", ""),
                idle_count=int(telemetry.get("idle_count", 0)),
                variety_total=int(telemetry.get("variety_total", 0)),
            )
        )
    except Exception:
        pass


def main() -> None:
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        algedonic_msg, telemetry = _check_teachback_idle(input_data)
        if algedonic_msg:
            _emit_algedonic_event(telemetry)
            print(json.dumps({"systemMessage": algedonic_msg}))
        else:
            print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (teachback_idle_guard): {e}", file=sys.stderr)
        print(hook_error_json("teachback_idle_guard", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
