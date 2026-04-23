#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/agent_handoff_emitter.py
Summary: TaskCompleted hook — pure journal-writer for agent_handoff events.
Used by: hooks.json TaskCompleted registration (replaces handoff_gate.py
         post-#538).

Responsibilities:
- On TaskCompleted, write a single agent_handoff event to the session
  journal, keyed by (team_name, task_id) for idempotent emission.
- Bypass non-agent completions (no owner + no platform teammate_name) and
  signal-type tasks (metadata.type in ("blocker", "algedonic")).

NOT responsible for:
- HANDOFF metadata validation (no blocking, no stderr prompts).
- memory_saved enforcement (advisory only at validate_handoff.py per #538).
- Stall / nag detection (#538 removes that category of hook entirely).

Emission invariant: write exactly once iff
(1) disk-read task status == "completed" AND
(2) the per-(team, task_id) sidecar marker does not yet exist.

The disk-status check is the substitute for the missing `previous_status`
field in the TaskCompleted stdin payload (architect §2.3 [MEDIUM] flagged
this gap). Claude Code fires the `TaskCompleted` hook event on ANY
TaskUpdate call — not only on transitions to `completed` (verified
empirically in session pact-114c988a / preparer-538 §R1). Trusting the
event name as the transition signal is the #528 regression — see that
issue for the secretary-task-#1 dogfood evidence of hundreds of nags on
metadata-only TaskUpdates. The on-disk status read is the only source of
truth for "did this TaskUpdate actually flip status to completed."

Idempotency: sidecar O_EXCL marker at
~/.claude/teams/{team}/.agent_handoff_emitted/{task_id}. Claude Code's
stopHooks.ts dispatches TaskCompleted on every matching owner during a
Stop flow; without the marker the journal would see the same completion
up to 37× per task (empirically sampled across 36 sessions pre-#538).
The marker and the status gate defend orthogonal failure modes: the
marker dedupes repeated fires of the SAME (team, task_id) completion;
the status gate rejects metadata-only TaskUpdates on in-progress tasks
that would otherwise pass the marker's first-fire check.

# livelock-safe: pure journal-writer; zero emission sinks. Writes at most
# one agent_handoff event per (team, task_id) via an O_EXCL sidecar marker
# gated on on-disk status == "completed", and exits 0 suppressOutput on
# every code path. Does NOT consume intentional_wait, does NOT emit
# systemMessage or stderr prompts, and does NOT block completion.
# Satisfies #538 AC #8 by construction.

Input: JSON from stdin with task_id, task_subject, task_description,
       teammate_name, team_name (TaskCompleted schema).
Output: {"suppressOutput": true} on every path; exit 0.
"""

import errno
import json
import os
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_team_name
from shared.session_journal import append_event, make_event
from shared.task_utils import read_task_json

# Suppress false "hook error" display in Claude Code UI on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Signal-task types — inline literal, matches task_utils.py:184 and
# session_resume.py:525 convention. Do NOT import is_signal_task: that
# helper is removed in #538 C3 alongside intentional_wait cleanup.
_SIGNAL_TASK_TYPES = ("blocker", "algedonic")


def _marker_dir(team_name: str) -> Path:
    """
    Return the per-team marker directory path.

    Lives under ~/.claude/teams/{team}/.agent_handoff_emitted/ — a sibling
    to the team's inboxes/ and config.json. session_end.py's team reaper
    removes the whole team directory (shutil.rmtree), so the marker dir is
    cleaned up automatically when the team ages out.

    Kept task-scoped (not session-scoped) so fire-once semantics survive
    pause/resume: a secretary standing task that spans sessions must emit
    its agent_handoff event exactly once across the whole team lifespan.
    """
    return Path.home() / ".claude" / "teams" / team_name / ".agent_handoff_emitted"


def _already_emitted(team_name: str, task_id: str) -> bool:
    """
    Test-and-set the per-(team, task_id) marker.

    Returns True iff a prior fire for the same (team, task_id) already
    created the marker (caller should suppress the journal write).
    Returns False on fresh fires — the marker is created as a side-effect
    of this call, making the test-and-set atomic at the kernel level.

    Fail-open: on any OSError other than EEXIST (permission denied,
    ENOSPC, filesystem race), returns False so the caller emits the
    event anyway. Data-integrity (preserving the HANDOFF in the journal)
    outweighs duplication-prevention when the marker subsystem itself
    breaks; worst case the caller falls back to pre-#538 duplication
    behavior for this one task.
    """
    if not team_name or not task_id:
        # No valid marker key → cannot dedupe. Emit rather than suppress.
        return False

    marker_dir = _marker_dir(team_name)
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        # Directory creation failed; fall back to fail-open (emit).
        return False

    marker_path = marker_dir / task_id
    try:
        fd = os.open(
            str(marker_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(fd)
        return False  # we created it; proceed with emit
    except OSError as e:
        if e.errno == errno.EEXIST:
            return True  # prior fire owns the marker; suppress
        return False  # any other error → fail-open, emit anyway


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)

    # Fallback substitution mirrors handoff_gate.py:229-242. The journal
    # schema rejects empty strings on str-typed required fields; if the
    # platform ever omits task_id/task_subject, fall back to sentinels so
    # the event still persists (preserving the HANDOFF is strictly better
    # than dropping it).
    raw_task_id = input_data.get("task_id")
    raw_task_subject = input_data.get("task_subject")
    task_id_was_missing = not raw_task_id
    task_subject_was_missing = not raw_task_subject
    task_id = raw_task_id or "unknown"
    task_subject = raw_task_subject or "(no subject)"
    if task_id_was_missing or task_subject_was_missing:
        print(
            f"agent_handoff_emitter: missing required field(s) in "
            f"TaskCompleted payload "
            f"(task_id={'MISSING' if task_id_was_missing else 'present'}, "
            f"task_subject={'MISSING' if task_subject_was_missing else 'present'}); "
            f"using fallback values to preserve agent_handoff event",
            file=sys.stderr,
        )

    team_name = (input_data.get("team_name") or get_team_name()).lower()

    task_data = read_task_json(task_id, team_name)

    # Owner field (set at dispatch) is the authoritative "agent completed
    # this task" signal. Platform-provided teammate_name is fallback for
    # tasks without an owner (e.g. direct Agent dispatches).
    teammate_name = task_data.get("owner") or input_data.get("teammate_name")
    if not teammate_name:
        # Non-agent completion (feature task, infrastructure task, etc.).
        # No HANDOFF to persist.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Status gate — substitute for the missing `previous_status` field in
    # TaskCompleted stdin. Claude Code fires this hook on ANY TaskUpdate,
    # not only on transitions to `completed` (regression of #528 if we
    # trust the event name as the transition signal). The on-disk
    # `status` is the only reliable source of truth for "did this
    # TaskUpdate actually flip status to completed." Metadata-only
    # TaskUpdates (claim flags, briefing_delivered, intentional_wait
    # toggles, etc.) keep status=in_progress and MUST NOT emit.
    if task_data.get("status") != "completed":
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    task_metadata = task_data.get("metadata", {})

    # Signal-task bypass: blocker/algedonic tasks MUST NOT emit a phantom
    # agent_handoff event (would pollute read_events("agent_handoff") +
    # mis-route secretary harvest).
    if task_metadata.get("type") in _SIGNAL_TASK_TYPES:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Idempotency guard — suppress duplicate fires for the same (team, task_id).
    if _already_emitted(team_name, task_id):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Journal-write — the sole purpose of this hook.
    append_event(
        make_event(
            "agent_handoff",
            agent=teammate_name,
            task_id=task_id,
            task_subject=task_subject,
            handoff=task_metadata.get("handoff", {}),
        ),
    )

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
