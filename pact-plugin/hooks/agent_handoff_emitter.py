#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/agent_handoff_emitter.py
Summary: TaskCompleted hook — pure journal-writer for agent_handoff events.
Used by: hooks.json TaskCompleted registration.

Responsibilities:
- On TaskCompleted, write a single agent_handoff event to the session
  journal, keyed by (team_name, task_id, occupant) for idempotent
  emission, where occupant = hash(agent + subject) (see
  shared/agent_handoff_marker.py — the SSOT shared with the #869 lead-side
  emit in task_lifecycle_gate.py).
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
(2)  task_metadata.get("handoff") is truthy
AND
(3)  the per-(team, task_id, occupant) sidecar marker does not yet exist.

The transition signal is `hook_event_name`. The disk-status read is a
fallback only — used when stdin lacks `hook_event_name`. The disk-state
read cannot serve as the primary transition signal because the platform's
persistence of `status="completed"` to disk is asynchronous relative to
the hook fire.

The handoff-presence gate suppresses fires that arrive before
`metadata.handoff` is stored on disk. Metadata-only TaskUpdates
(briefing flags, intentional_wait toggles, claim flags) skip marker
creation; only the fire with `metadata.handoff` populated claims the
marker and writes the journal entry.

Idempotency: sidecar O_EXCL marker at
~/.claude/teams/{team}/.agent_handoff_emitted/{task_id}-{occupant_hash}.
The platform's Stop flow dispatches TaskCompleted on every matching owner;
the marker deduplicates these so the journal records exactly one entry per
(team, task_id, occupant). Occupant-identity keying (vs. the prior bare
{task_id}) fixes the #887 stale-marker collision on team-name reuse while
preserving the standing-task fire-once-across-lifespan dedup. The marker
machinery lives in shared/agent_handoff_marker.py (SSOT shared with
task_lifecycle_gate.py's #869 lead-side emit).

# livelock-safe: pure journal-writer; zero emission sinks. Writes at most
# one agent_handoff event per (team, task_id, occupant) via an O_EXCL
# sidecar marker gated on (a) hook_event_name OR disk-status, (b)
# handoff-presence in task_metadata, and exits 0 suppressOutput on every
# code path. Does NOT consume intentional_wait, does NOT emit systemMessage
# or stderr prompts, and does NOT block completion.

Input: JSON from stdin with task_id, task_subject, task_description,
       teammate_name, team_name (TaskCompleted schema).
Output: {"suppressOutput": true} on every path; exit 0.
"""

import json
import sys

import shared.pact_context as pact_context
from shared.agent_handoff_marker import (
    already_emitted,
    is_signal_task,
    occupant_hash,
    sanitize_path_component,
)
from shared.pact_context import get_team_name
from shared.session_journal import append_event, make_event
from shared.task_utils import read_task_json

# Suppress false "hook error" display in Claude Code UI on bare exit paths.
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


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

        # Type-guard: a non-dict stdin payload (JSON array, string, number,
        # null) would crash the subsequent `.get(...)` calls and violate
        # the exit-0 invariant. Cross-hook pattern follow-up tracked
        # separately.
        if not isinstance(input_data, dict):
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
        # The "MISSING required field(s)" stderr warning fires below, only
        # after all bypass gates clear and a journal write is actually
        # attempted — silencing the noise on signal-task / handoff-absent /
        # no-owner paths that suppress emission anyway.
        raw_task_id = input_data.get("task_id")
        raw_task_subject = input_data.get("task_subject")
        task_id_was_missing = not raw_task_id
        task_subject_was_missing = not raw_task_subject
        task_id = raw_task_id or "unknown"
        task_subject = raw_task_subject or "(no subject)"

        # Sanitize path-joining components symmetrically with
        # task_utils.read_task_json. task_id and team_name both flow into
        # filesystem paths (read_task_json for the status read; the marker
        # join inside shared.agent_handoff_marker.already_emitted for the
        # O_EXCL dedup marker). A helper applied at a single producer-side
        # site ensures the sink paths can never diverge.
        task_id = sanitize_path_component(str(task_id))
        team_name = sanitize_path_component(
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

        # Transition signal: `hook_event_name == "TaskCompleted"` is the
        # primary signal; the disk-status check is a fallback used only
        # when stdin omits `hook_event_name`.
        #
        # See pact-plugin/hooks/shared/HOOK_INPUT_CONVENTIONS.md for the
        # routing convention (string-literal compare; never used as a path
        # component; fail-closed on non-string values).
        #
        # DO NOT DELETE the fallback branch — forward-compat path for
        # platforms that omit `hook_event_name`. It is pinned by
        # TestStatusFallbackGate (TestProductionShapeMetadataOnly
        # exercises the production-shape path).
        hook_event = input_data.get("hook_event_name", "")
        # Comparison with the string literal fail-closes on non-string
        # values; do not cast or trim. A non-string `hook_event` (None,
        # int, bool) compares unequal to "TaskCompleted" and falls through
        # to the disk-status fallback.
        if hook_event != "TaskCompleted":  # Invariant (1a) primary check
            if task_data.get("status") != "completed":  # Invariant (1b) fallback
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

        # `or {}` handles explicit JSON null in addition to missing key —
        # .get("metadata", {}) returns None when the key is present with a
        # null value, which would crash the subsequent .get("type") call
        # and violate the exit-0 invariant.
        task_metadata = task_data.get("metadata") or {}

        # Signal-task bypass: blocker/algedonic tasks MUST NOT emit a phantom
        # agent_handoff event (would pollute read_events("agent_handoff") +
        # mis-route secretary harvest). Shared predicate (SSOT) — the #869
        # lead-side emit in task_lifecycle_gate.py applies the same exclusion.
        if is_signal_task(task_metadata):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Handoff-presence gate. Suppress emission AND skip marker creation
        # when `metadata.handoff` is absent or empty. Required so that a
        # fire arriving before the teammate has stored handoff cannot claim
        # the O_EXCL marker with empty content; the substantive completion
        # (with handoff populated) claims it instead.
        if not task_metadata.get("handoff"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Idempotency guard — suppress duplicate fires for the same
        # (team, task_id, occupant). The marker is created ONLY when
        # handoff-presence is verified above; the optimistic ordering (marker
        # created before append_event completes) trades one rare write-failure
        # event loss against repeated duplicate emission (see
        # shared.agent_handoff_marker.already_emitted for empirical basis).
        #
        # Fix B (#887): occupant-identity marker key. A re-scoped subject →
        # one extra emit (biases to HANDOFF preservation, never loss).
        occupant = occupant_hash(teammate_name, task_subject)
        if already_emitted(team_name, task_id, occupant):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Stderr warning fires here — after all bypass gates clear and
        # the marker is claimed, so noise is limited to fires that
        # actually attempt a journal write.
        if task_id_was_missing or task_subject_was_missing:
            print(
                f"agent_handoff_emitter: missing required field(s) in "
                f"TaskCompleted payload "
                f"(task_id={'MISSING' if task_id_was_missing else 'present'}, "
                f"task_subject={'MISSING' if task_subject_was_missing else 'present'}); "
                f"using fallback values to attempt preservation of agent_handoff event",
                file=sys.stderr,
            )

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
                handoff=task_metadata.get("handoff"),
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
        # Outer catch-all: every code path must exit 0 suppressOutput. Any
        # unexpected error (including malformed task_data shapes not caught
        # by the `or {}` guard above) falls back to a clean no-op to
        # preserve the livelock-safe invariant.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
