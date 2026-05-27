#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teardown_request_emitter.py
Summary: TaskCompleted hook — emits the Teardown directive (Tier-1 fast
         path) when the lead-driven terminal-status TaskUpdate drives the
         team's lifecycle-relevant active-task count to zero.
Used by: hooks.json TaskCompleted registration (sibling to
         agent_handoff_emitter.py — both fire in parallel on every
         TaskCompleted event).

Responsibilities:
- On TaskCompleted in the LEAD session AND on a 1->0 active-task
  transition AND no same-teammate continuation deferral, write a single
  teardown_request event to the session journal keyed by (team_name,
  task_id) for idempotent emission, AND emit the _TEARDOWN_DIRECTIVE
  via additionalContext so the lead invokes
  Skill("PACT:stop-pending-scan") on its next turn.

NOT responsible for:
- The Tier-2 carve-out fallback path (teammate-side self-completions
  for SELF_COMPLETE_EXEMPT agent types). Tier-2 is handled by the
  wake_inbox marker producer in wake_lifecycle_emitter.py +
  consumer in wake_inbox_drain.py.
- HANDOFF metadata validation (agent_handoff_emitter.py owns that).
- Stall / nag detection.
- Replaying historical teardown_request events from the journal
  (Tier-4 cron-staleness fallback is a separate surface).

Emission invariant: write exactly one teardown_request event iff
(0)  is_lead_context(input_data, team_name) — defense-in-depth Gate 0
AND
(1)  hook_event_name == "TaskCompleted" OR (fallback) disk-read task
     status == "completed" — primary transition signal + fallback
AND
(6)  NOT has_in_progress_umbrella_orchestration(team_name) —
     OPERATIONAL-LULL-AT-PHASE-BOUNDARY suppression. Evaluated BEFORE
     Gate 3 so the umbrella-orchestration short-circuit fires before
     the count_active_tasks iteration, which is the structurally
     load-bearing ordering — Gate 3's 0 reading IS the misfire signal
     during the lull window. See Gate 6 audit-anchor comment for the
     class taxonomy and parent-class see-also.
AND
(2)  the per-(team, task_id) sidecar O_EXCL marker does not yet exist
     — idempotency across Stop-sweep secondary firings + retries
AND
(3)  count_active_tasks(team_name) == 0 — 1->0 lifecycle transition
AND
(4)  NOT has_same_teammate_continuation(completed_task, team_name) —
     same-teammate continuation deferral preserves the brief cron-down
     window suppression from the legacy PostToolUse Teardown branch.

Gate 0 — Lead-Session Guard (Layer 0 — defense-in-depth):
- Mirrors wake_lifecycle_emitter.py:654 semantics. The platform's
  Stop-sweep secondary firing source (stopHooks.ts:334-425) fires this
  hook in teammate sessions for every in_progress owned task; Gate 0
  short-circuits those before any disk I/O. The Teardown directive is
  lead-targeted; teammate-session emission would target the wrong
  session.

Gate 1 — Transition signal (primary: hook_event_name; fallback: disk-
status):
- Templates off agent_handoff_emitter.py:281-289 line-by-line. The
  string-literal compare fail-closes on non-string hook_event values.
  The disk-status read is a fallback only — the platform's persistence
  of status="completed" to disk is asynchronous relative to the hook
  fire, so hook_event_name is the load-bearing primary signal.

Gate 2 — Sidecar O_EXCL marker idempotency:
- Mirrors agent_handoff_emitter.py:319 + _already_emitted at L117. The
  marker lives at ~/.claude/teams/{team}/.teardown_request_emitted/
  {task_id}. The _already_emitted predicate is copy-pasted from C2 per
  the helper_location_elevation_pattern memory's "elevate on 3rd
  consumer" rule — this is consumer #2 of the predicate (after
  agent_handoff_emitter.py); C4 (wake_inbox_drain.py teardown drain)
  will be consumer #3 trigger but for now consumer #2 lives inline.
- O_EXCL marker creation is the LAST step before the journal write.
  Optimistic ordering: marker created before append_event completes,
  trading one rare write-failure event loss against repeated duplicate
  emission (see agent_handoff_emitter.py:313-321 for the empirical
  basis: up to 37x per task without the marker).

Gate 6 — OPERATIONAL-LULL umbrella-orchestration suppression
(introduced for #842):
- has_in_progress_umbrella_orchestration(team_name) returns True iff
  the team has at least one in_progress task with a canonical
  umbrella-orchestration subject prefix (UMBRELLA_SUBJECT_PREFIXES at
  shared/wake_lifecycle.py). Evaluated BEFORE Gate 3 because the
  OPERATIONAL-LULL misfire IS a 0 reading at Gate 3 during the brief
  window between phase-N specialists wrapping up and phase-(N+1)
  specialists arriving. Without Gate 6, the 0 reading triggers a
  spurious teardown_request even though the umbrella signals live
  orchestration. Numbered "6" rather than appending after Gate 5
  because the gate ordinality reflects the ORDER OF EVALUATION (Gate
  0 -> 1 -> 6 -> ...), not chronological introduction order.

Gate 3 — 1->0 active-task transition:
- count_active_tasks(team_name) excludes signal-tasks (blocker /
  algedonic) AND self-complete-exempt agent owners (pact-secretary
  today) via its lifecycle-relevant filter. The 1->0 transition AFTER
  the platform persists this task's completed status is the canonical
  Teardown trigger.

Gate 4 — Same-teammate continuation deferral:
- Mirrors wake_lifecycle_emitter.py:714-716. When the just-completed
  task has a same-teammate-owned active continuation in its blocks
  chain, defer Teardown — the teammate is staged to claim the next
  task imminently, so emitting Teardown would produce a phantom
  cron-down audit signal and a brief cron-down window for inbound
  completion-authority work.

# livelock-safe: pure journal-writer + single additionalContext directive
# emission per (team, task_id); zero stderr noise on the gate-fail paths.
# Exits 0 with suppressOutput on every code path. Does NOT consume
# intentional_wait, does NOT emit systemMessage, and does NOT block
# completion.

Input: JSON from stdin with task_id, task_subject, task_description,
       teammate_name, team_name (TaskCompleted schema).
Output: hookSpecificOutput with additionalContext on Teardown emission;
        {"suppressOutput": true} on every other path; exit 0.
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
from shared.wake_lifecycle import (
    count_active_tasks,
    has_in_progress_umbrella_orchestration,
    has_same_teammate_continuation,
    is_lead_context,
)

# Reuse the canonical _TEARDOWN_DIRECTIVE literal from the emitter so the
# directive prose has a single source of truth. The audit-anchor literal-
# prose pin on the emitter side covers that constant; this import makes
# any future drift immediately break this hook too.
from wake_lifecycle_emitter import _TEARDOWN_DIRECTIVE


# Suppress false "hook error" display in Claude Code UI on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _sanitize_path_component(value: str) -> str:
    """
    Strip path-traversal fragments from a value destined for filesystem joins.

    Mirrors agent_handoff_emitter._sanitize_path_component byte-for-byte
    (NUL, CR/LF, control chars 0x00-0x1f, slash, backslash, `..`
    substrings). Applied symmetrically at both sink sites (read_task_json
    status read, marker O_EXCL create) so they can never diverge.
    """
    return re.sub(r"[/\\\x00-\x1f]|\.\.", "", value)


def _marker_dir(team_name: str) -> Path:
    """
    Return the per-team Teardown-emit marker directory path.

    Lives under ~/.claude/teams/{team}/.teardown_request_emitted/ — a
    sibling to .agent_handoff_emitted/ and the team's inboxes/ +
    config.json. session_end.py's team reaper removes the whole team
    directory (shutil.rmtree), so the marker dir is cleaned up
    automatically when the team ages out.

    Kept task-scoped (not session-scoped) so fire-once semantics survive
    pause/resume: a completion that lands across a pause boundary still
    emits its teardown_request event exactly once across the whole team
    lifespan.
    """
    return Path.home() / ".claude" / "teams" / team_name / ".teardown_request_emitted"


def _already_emitted(team_name: str, task_id: str) -> bool:
    """
    Test-and-set the per-(team, task_id) marker.

    Returns True iff a prior fire for the same (team, task_id) already
    created the marker (caller should suppress the journal write).
    Returns False on fresh fires — the marker is created as a side-effect
    of this call, making the test-and-set atomic at the kernel level.

    Copy-pasted from agent_handoff_emitter._already_emitted (with the
    marker directory rebased to .teardown_request_emitted) per the
    helper_location_elevation_pattern: elevate to shared/ on the 3rd
    consumer. C4 (wake_inbox_drain.py teardown drain) will use the same
    marker dir for cross-tier dedup but does NOT need to share the
    predicate at the call site — the third-consumer threshold materializes
    when the shared elevation actually pays off (today: 2 copies, 0 shared).

    Fail-open: on any OSError other than EEXIST (permission denied,
    ENOSPC, filesystem race), returns False so the caller emits the
    event anyway. Data-integrity (preserving the teardown_request
    in the journal) outweighs duplication-prevention when the marker
    subsystem itself breaks.

    Graceful-degrade caveat: a pre-existing non-symlink file at the
    marker path (e.g., from a manually-created file or stale state
    surviving an unclean cleanup) also returns True via EEXIST and
    suppresses emission permanently for that (team, task_id) — the
    O_EXCL test cannot distinguish "prior fire owns it" from "external
    file was placed here." Acceptable trade-off versus the alternative
    (read-the-file-to-verify, which races and complicates the atomic
    test-and-set).
    """
    # Degenerate post-sanitization values collapse the marker path onto
    # an existing directory (`Path(marker_dir) / "."` -> marker_dir
    # itself, `Path(marker_dir) / ".."` -> parent). In either case
    # os.open(..., O_CREAT | O_EXCL) on an existing directory returns
    # EEXIST and PERMANENTLY SUPPRESSES every future emit for the
    # degenerate key. The regex strips `/`, `\`, and `..` substrings but
    # leaves single `.` segments untouched. Emit on degenerate keys
    # rather than silent permanent suppression.
    if (
        not team_name
        or team_name in (".", "..")
        or not task_id
        or task_id in (".", "..")
    ):
        return False

    marker_dir = _marker_dir(team_name)
    # Symlink-on-dir defense-in-depth: a TOCTOU window exists between
    # this is_symlink() check and the mkdir(exist_ok=True) below, so
    # this pre-check is NOT a structural containment guarantee — it is
    # a fast-fail for the cooperative case. The load-bearing structural
    # defense is O_NOFOLLOW on the final O_EXCL open below, which
    # refuses to follow any symlink at the marker_path component
    # regardless of what races may have swapped the marker_dir. Under
    # the same-user trust model this defense-in-depth posture is fine;
    # under a different threat model the pre-check would need a
    # dirfd-based open to actually contain symlink swaps.
    if marker_dir.is_symlink():
        return False
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return False

    marker_path = marker_dir / task_id
    # O_NOFOLLOW is the load-bearing structural defense against a pre-
    # planted symlink at the marker path; mirrors the
    # agent_handoff_emitter Sec-M1 pattern. POSIX O_CREAT|O_EXCL already
    # refuses to follow a trailing symlink; O_NOFOLLOW adds intermediate-
    # symlink coverage. Together with the (racy) is_symlink pre-check
    # above this forms layered defense-in-depth — the pre-check filters
    # the cooperative case cheaply, O_NOFOLLOW filters the adversarial
    # case structurally.
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
        return False  # any other error -> fail-open, emit anyway


def _emit_teardown_directive() -> None:
    """Print the _TEARDOWN_DIRECTIVE additionalContext payload with the
    required hookEventName field. TaskCompleted hooks emit
    `additionalContext` consumed by the lead's NEXT prompt — same
    delivery mechanism as the legacy PostToolUse path.
    """
    output = {
        "hookSpecificOutput": {
            "hookEventName": "TaskCompleted",
            "additionalContext": _TEARDOWN_DIRECTIVE,
        }
    }
    print(json.dumps(output))


def main() -> None:
    # Outer catch-all preserves the exit-0 suppressOutput contract
    # against any unexpected exception (malformed task.json, filesystem
    # race, import-time error past the inner guards). The Teardown
    # mechanism is opportunistic — a crashed hook degrades to user-
    # invoked /PACT:stop-pending-scan, not a hard failure. The bare
    # `except Exception` is deliberate: livelock-safety via the
    # "exits 0 on every code path" invariant outweighs observability
    # for unexpected errors (nonzero exit would surface as a hook-error
    # UI on every TaskCompleted dispatch — the livelock-capable failure
    # class the categorical standard forbids).
    try:
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        if not isinstance(input_data, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)

        # Resolve team_name from session context first; without it,
        # neither the lead-session guard nor the active-task count can
        # be evaluated.
        team_name = _sanitize_path_component(
            str(input_data.get("team_name") or get_team_name()).lower()
        )
        if not team_name:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Gate 0 — defense-in-depth lead-context check. Mirrors
        # wake_lifecycle_emitter.py outer gate; teammate-context input
        # never reaches the journal write or directive emission paths.
        # The compound `is_lead_context` discriminator (`'agent_id'
        # not in stdin and 'teammate_name' not in stdin`) is the
        # consolidated lead-vs-teammate classifier across the 4
        # wake-lifecycle hook sites — empirical provenance and
        # SessionStart-specific follow-up scope are documented at the
        # helper definition in shared/wake_lifecycle.py.
        if not is_lead_context(input_data, team_name):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Sanitize task_id at the producer boundary symmetrically with
        # read_task_json (which sanitizes inside) and the marker O_EXCL
        # create site.
        raw_task_id = input_data.get("task_id")
        if not raw_task_id:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        task_id = _sanitize_path_component(str(raw_task_id))
        if not task_id:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Gate 1 — transition signal: hook_event_name primary, disk-
        # status fallback. Templates agent_handoff_emitter.py:281-289.
        # The string-literal compare fail-closes on non-string values
        # (None, int, bool compare unequal to "TaskCompleted").
        # Disk-status fallback accepts BOTH terminal statuses
        # ("completed", "deleted") symmetric with the retired PostToolUse
        # Teardown branch's _TERMINAL_STATUSES set at
        # wake_lifecycle_emitter.py:267. Lead-driven
        # TaskUpdate(status="deleted") on a 1->0 transition produces no
        # TaskCompleted hook event, so without "deleted" in this set the
        # deletion path drops the Teardown directive entirely (Tier-2
        # also misses it via its lead-session early-return).
        hook_event = input_data.get("hook_event_name", "")
        task_data: dict = {}
        if hook_event != "TaskCompleted":
            task_data = read_task_json(task_id, team_name)
            if not isinstance(task_data, dict):
                task_data = {}
            if task_data.get("status") not in ("completed", "deleted"):
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

        # Gate 6 — OPERATIONAL-LULL-AT-PHASE-BOUNDARY suppression.
        # Audit-anchor class taxonomy:
        #   Child class: OPERATIONAL-LULL-AT-PHASE-BOUNDARY — the brief
        #     window between phase-N specialists wrapping up and
        #     phase-(N+1) specialists arriving (8-30s per Phase A
        #     diagnostic on session pact-450f3d63). During this window
        #     count_active_tasks legitimately reaches 0 (lead-owned
        #     umbrella is excluded by the teammate-owner filter); Gate
        #     3's 0 reading misfires a phantom 1->0 transition signal.
        #   Parent class (see also): COUNT-BASED-LIFECYCLE-INVARIANT-
        #     MISFIRE — hypothesized at N=2 instances (this + #843's
        #     FIRST-OBSERVABLE-WRITE). The shared shape: hook reads an
        #     aggregate count to derive a transition signal, but the
        #     count is briefly degenerate at protocol boundaries.
        # Mitigation pattern: gate the count-reading BEFORE the count
        # itself with a structural-signature predicate that detects the
        # protocol's still-live state. Here:
        # has_in_progress_umbrella_orchestration reads the umbrella
        # task's signature (subject prefix from UMBRELLA_SUBJECT_PREFIXES
        # at shared/wake_lifecycle.py) — signature-based, NOT owner-
        # based, because umbrella tasks have owner=null on disk.
        # Evaluated BEFORE Gate 3 because the misfire IS Gate 3's 0
        # reading during the lull window.
        if has_in_progress_umbrella_orchestration(team_name):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Gate 3 — 1->0 active-task transition. count_active_tasks
        # excludes signal-tasks and self-complete-exempt agent owners
        # via its lifecycle-relevant filter, so a zero return means the
        # team has no remaining lifecycle-relevant work.
        # Evaluated BEFORE Gate 2 (marker claim) so a non-1->0 fire
        # doesn't burn a marker that would then suppress a legitimate
        # later 1->0 fire on the same task_id.
        if count_active_tasks(team_name) != 0:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Gate 4 — same-teammate continuation deferral. Read the task
        # JSON if Gate 1 didn't already (i.e., when hook_event_name was
        # the primary signal we skipped the disk read). Mirrors the
        # legacy PostToolUse Teardown branch's deferral semantics at
        # wake_lifecycle_emitter.py:714-716.
        if not task_data:
            task_data = read_task_json(task_id, team_name)
            if not isinstance(task_data, dict):
                task_data = {}
        if has_same_teammate_continuation(task_data, team_name):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Gate 2 — sidecar O_EXCL marker idempotency. Evaluated LAST so
        # only fires that would otherwise emit consume the marker
        # (avoid burning markers on Stop-sweep secondaries that fail
        # gates 0/1/3/4 — they'd permanently block legitimate later
        # emissions otherwise).
        if _already_emitted(team_name, task_id):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # All gates pass — write the journal event AND emit the
        # additionalContext Teardown directive. Event is the falsifiable
        # trace; directive is the wake-hint.
        append_event(
            make_event(
                "teardown_request",
                task_id=task_id,
                team_name=team_name,
                tier="1",
                reason="lead_terminal_taskupdate",
            ),
        )
        _emit_teardown_directive()
        sys.exit(0)
    except SystemExit:
        # Re-raise — the explicit sys.exit(0) paths above are expected
        # control-flow, not errors.
        raise
    except Exception:
        # Outer catch-all: every code path must exit 0 suppressOutput.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
