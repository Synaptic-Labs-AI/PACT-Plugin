#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/agent_handoff_emitter.py
Summary: TaskCompleted hook — pure journal-writer for agent_handoff events.
Used by: hooks.json TaskCompleted registration.

Responsibilities:
- On TaskCompleted, write a single agent_handoff event to the session
  journal, keyed by (team_name, task_id) for idempotent emission.
- Bypass non-agent completions (no owner + no platform teammate_name) and
  signal-type tasks (metadata.type in ("blocker", "algedonic")).

NOT responsible for:
- HANDOFF metadata validation (no blocking, no stderr prompts).
- memory_saved enforcement (advisory only at validate_handoff.py).
- Stall / nag detection (not this hook's responsibility).

Emission invariant: write exactly once iff
((1a) hook_event_name == "TaskCompleted" in stdin
      OR
 (1b) (fallback) disk-read task status == "completed")
AND
(2)  task_metadata.get("handoff") is truthy (handoff stored on disk)
AND
(3)  the per-(team, task_id) sidecar marker does not yet exist.

The status-disk-gate is retained as a FALLBACK only — when stdin lacks
`hook_event_name`. The PRIMARY transition signal is the platform-supplied
`hook_event_name == "TaskCompleted"` field, captured verbatim across all
3 real-platform probes in PREPARE phase of #551 (see
docs/preparation/551-emitter-regression-diagnostic.md § "Real-platform
stdin shape").

The disk-state read CANNOT be the primary transition signal because the
platform's persistence of `status="completed"` to disk is async relative
to the hook fire (#551 root cause; 3/3 probes in PREPARE confirmed
`status="in_progress"` on disk at hook-fire time, then `status="completed"`
moments later when the same TaskUpdate finished writing).

Memory `21b4576b` documents 200+ phantom TaskCompleted fires pre-#538
during metadata-only TaskUpdates against the OLD handoff_gate.py. If
that platform behavior recurs (every metadata-only TaskUpdate carrying
`hook_event_name="TaskCompleted"`), the handoff-presence gate (Option E)
suppresses every fire that arrives BEFORE the teammate has stored
`metadata.handoff` — early metadata-only fires (briefing_delivered,
intentional_wait toggles, claim flags) all skip the marker creation.
The genuine completion (which has `metadata.handoff` populated) is the
fire that claims the marker and writes the journal entry with full
handoff content. Net cost under revert: zero empty-handoff entries; the
marker is consumed exactly by the substantive completion. Strictly
better than 0/51 in the genuine sense — the journal carries real
HANDOFF data, not phantom counts.

Idempotency: sidecar O_EXCL marker at
~/.claude/teams/{team}/.agent_handoff_emitted/{task_id}. Claude Code's
stopHooks.ts dispatches TaskCompleted on every matching owner during a
Stop flow; without the marker the journal would see the same completion
up to 37× per task (empirically sampled across 36 sessions).

# livelock-safe: pure journal-writer; zero emission sinks. Writes at most
# one agent_handoff event per (team, task_id) via an O_EXCL sidecar marker
# gated on (a) hook_event_name OR disk-status, (b) handoff-presence in
# task_metadata, and exits 0 suppressOutput on every code path. Does NOT
# consume intentional_wait, does NOT emit systemMessage or stderr prompts,
# and does NOT block completion.

Input: JSON from stdin with task_id, task_subject, task_description,
       teammate_name, team_name (TaskCompleted schema).
Output: {"suppressOutput": true} on every path; exit 0.
"""

import errno
import json
import os
import re
import sys
from pathlib import Path

import shared.pact_context as pact_context
from shared.pact_context import get_team_name
from shared.session_journal import append_event, make_event
from shared.task_utils import read_task_json

# Suppress false "hook error" display in Claude Code UI on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# Signal-task types — inline literal, matches the
# `task_type in ("blocker", "algedonic")` check inside task_utils.find_blockers
# and the session_resume convention. Do NOT import is_signal_task: no
# such helper exists.
_SIGNAL_TASK_TYPES = ("blocker", "algedonic")


def _sanitize_path_component(value: str) -> str:
    """
    Strip path-traversal fragments from a value destined for filesystem joins.

    Mirrors the regex used inside task_utils.read_task_json so the gate
    site (status read) and the write site (O_EXCL marker create) apply
    symmetric sanitization. Without this, an attacker-crafted task_id or
    team_name that happens to sanitize (in read_task_json) into a matching
    existing completed-task file could still carry raw "../" fragments into
    the marker-path join and cause zero-byte file creation outside the team's
    marker directory.
    """
    return re.sub(r"[/\\]|\.\.", "", value)


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
    breaks; worst case the caller falls back to per-fire emission for
    this one task (up to 37× per task, empirically measured).
    """
    # Degenerate post-sanitization values collapse the marker path onto an
    # existing directory:
    #   `Path(marker_dir) / "."`  → marker_dir itself
    #   `Path(marker_dir) / ".."` → marker_dir's parent
    # In either case `os.open(..., O_CREAT | O_EXCL)` on that existing
    # directory returns EEXIST, which the catch below interprets as "prior
    # fire owns the marker" and PERMANENTLY SUPPRESSES every future emit for
    # the degenerate key. The regex in _sanitize_path_component strips `/`,
    # `\`, and `..` substrings but leaves single `.` segments untouched — so
    # inputs like `"."`, `"..."`, `"/./"` and the two-segment form `"/./."`
    # (which sanitizes to `".."`) all reach this site as `.` or `..`. Guard
    # both task_id and team_name: emit the event, accept the rare-degenerate
    # duplication risk over silent event loss.
    if (
        not team_name
        or team_name in (".", "..")
        or not task_id
        or task_id in (".", "..")
    ):
        # No valid marker key → cannot dedupe. Emit rather than suppress.
        return False

    marker_dir = _marker_dir(team_name)
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        # Directory creation failed; fall back to fail-open (emit).
        return False

    marker_path = marker_dir / task_id
    # O_NOFOLLOW defends against a pre-planted symlink at the marker path —
    # mirrors the Sec-M1 pattern in session_init's symlink-defense path. POSIX O_CREAT|O_EXCL
    # already refuses to follow a trailing symlink; O_NOFOLLOW is defense-in-depth
    # against any future flag-combination divergence and against intermediate-symlink
    # variants. getattr graceful-degrades on platforms that lack the flag.
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(
            str(marker_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
        )
        os.close(fd)
        return False  # we created it; proceed with emit
    except OSError as e:
        if e.errno == errno.EEXIST:
            return True  # prior fire owns the marker; suppress
        return False  # any other error (incl. ELOOP) → fail-open, emit anyway


def main() -> None:
    # Outer catch-all preserves the exit-0 suppressOutput contract (see
    # docstring) against any unexpected exception (malformed task.json
    # with non-dict metadata, import-time race, filesystem errors past
    # the inner guards). The bare `except Exception` is deliberate —
    # livelock-safety via the "exits 0 on every code path" invariant
    # outweighs observability for unexpected errors here. Callers of this
    # hook (Claude Code's TaskCompleted dispatch) treat nonzero exit as a
    # hook-error UI surface; nonzero exit would produce the livelock-capable
    # failure class: TeammateIdle/TaskCompleted/Stop hooks emitting
    # systemMessage or error output on every event dispatch until the
    # owner task resolves — which the categorical standard forbids.
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)

        # Fallback substitution attempts preservation of the agent_handoff
        # event when the platform omits required fields: the journal schema
        # rejects empty strings on str-typed required fields, so if task_id
        # or task_subject is missing we substitute sentinels. Note asymmetry:
        # a missing task_subject still emits (status gate reads the real
        # task.json via raw_task_id), but a missing task_id falls through
        # to read_task_json("unknown", team_name) → {} → status gate exits
        # early. "Preservation" is best-effort, not guaranteed.
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
                f"using fallback values to attempt preservation of agent_handoff event",
                file=sys.stderr,
            )

        # Sanitize path-joining components symmetrically with
        # task_utils.read_task_json. task_id and team_name both flow into
        # filesystem paths (read_task_json for the status read, _marker_dir /
        # marker_path for the O_EXCL dedup marker). A helper applied at a
        # single producer-side site ensures the two sink paths can never
        # diverge.
        task_id = _sanitize_path_component(str(task_id))
        team_name = _sanitize_path_component(
            str(input_data.get("team_name") or get_team_name()).lower()
        )

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

        # Transition signal — primary is the platform-supplied
        # `hook_event_name == "TaskCompleted"`. PREPARE-phase probes for #551
        # captured this field verbatim in 3/3 real-platform fires
        # (docs/preparation/551-emitter-regression-diagnostic.md). The
        # `read_task_json` call above races the platform's own write of
        # status=completed — at hook-fire time the disk frequently still
        # shows in_progress, which is the #551 0/51-cumulative regression.
        # We trust the platform event-name signal and let the
        # `_already_emitted` O_EXCL marker dedupe per (team, task_id) for
        # the phantom-fire-revert scenario (memory `21b4576b`: pre-#538,
        # 200+ TaskCompleted fires on a single in_progress task during
        # metadata-only TaskUpdates).
        #
        # See pact-plugin/hooks/shared/HOOK_INPUT_CONVENTIONS.md for the
        # routing convention this hook codifies (string-literal compare;
        # never used as a path component; fail-closed on non-string values).
        #
        # The disk-status fallback fires only when stdin lacks
        # hook_event_name (forward-compat / malformed payload).
        # DO NOT DELETE the fallback branch — it is the forward-compat
        # path for platforms that omit hook_event_name; pinned by
        # TestStatusFallbackGate (and TestProductionShapeMetadataOnly
        # exercises the post-Option-B production shape).
        hook_event = input_data.get("hook_event_name", "")
        # Comparison with string literal naturally fail-closes on non-string
        # values; do not cast or trim. A non-string `hook_event` (None, int,
        # bool) compares unequal to "TaskCompleted" and falls through to the
        # disk-status fallback.
        if hook_event != "TaskCompleted":
            if task_data.get("status") != "completed":
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

        # `or {}` handles explicit JSON null in addition to missing key —
        # .get("metadata", {}) returns None when the key is present with a
        # null value, which would crash the subsequent .get("type") call
        # and violate the exit-0 invariant.
        task_metadata = task_data.get("metadata") or {}

        # Signal-task bypass: blocker/algedonic tasks MUST NOT emit a phantom
        # agent_handoff event (would pollute read_events("agent_handoff") +
        # mis-route secretary harvest).
        if task_metadata.get("type") in _SIGNAL_TASK_TYPES:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Handoff-presence gate (Option E) — under platform-revert, an early
        # metadata-only TaskUpdate fires TaskCompleted BEFORE the teammate
        # has stored metadata.handoff. Without this guard, the early fire
        # would consume the O_EXCL marker with empty handoff content,
        # suppressing the later genuine completion's full-handoff write.
        # By suppressing emission AND skipping marker creation when handoff
        # is missing, the genuine completion (which has handoff stored)
        # claims the marker and writes the substantive journal entry.
        if not task_metadata.get("handoff"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Idempotency guard — suppress duplicate fires for the same
        # (team, task_id). Ordering: the marker is created OPTIMISTICALLY
        # BEFORE append_event completes. If the subsequent journal write
        # fails (session_journal is fail-open and may silently no-op on
        # write errors), the marker persists and any retry will see it
        # and suppress — the event is lost on disk AND marked as
        # already-emitted. Intentional trade-off: preventing 37× duplicate
        # emission (empirically measured) outweighs the rare single-event
        # loss under write failure.
        if _already_emitted(team_name, task_id):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Journal-write — the sole purpose of this hook.
        # DO NOT forward additional stdin fields beyond these 4 — the
        # journal event payload contract is intentionally minimal, and
        # TestStdinShapePin asserts no leakage of session_id /
        # transcript_path / cwd / hook_event_name / team_name /
        # teammate_name / task_description into the event.
        append_event(
            make_event(
                "agent_handoff",
                agent=teammate_name,
                task_id=task_id,
                task_subject=task_subject,
                handoff=task_metadata.get("handoff") or {},
            ),
        )

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    except SystemExit:
        # Re-raise — the explicit sys.exit(0) paths above are expected
        # control-flow, not errors; swallowing them would skip the intended
        # _SUPPRESS_OUTPUT emission side-effect on those paths.
        raise
    except Exception:
        # AC #8: exit 0 suppressOutput on every code path. Any unexpected
        # error (including malformed task_data shapes not caught by the
        # `or {}` guard above) falls back to a clean no-op to preserve the
        # livelock-safe invariant.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
