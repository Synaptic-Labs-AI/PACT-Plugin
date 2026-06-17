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

from __future__ import annotations

import json
import sys

import shared.pact_context as pact_context
from shared.agent_handoff_marker import (
    already_emitted,
    is_signal_task,
    occupant_hash,
    sanitize_path_component,
    unclaim,
)
from shared.session_journal import append_event, get_journal_path, make_event
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
        # #917 R2 (validate-before-claim): treat a WHITESPACE-only subject as
        # missing, not just a falsy one. The journal schema rejects empty /
        # whitespace-only str fields (session_journal._validate_event_schema),
        # so a "   " subject would pass this bare-falsy check, claim the O_EXCL
        # marker, then FAIL append_event — the claim-without-write poison the
        # writability gate only NARROWED. Substituting the sentinel here makes
        # the subject deterministically schema-valid before the claim.
        task_subject_was_missing = not (
            raw_task_subject and str(raw_task_subject).strip()
        )
        task_id = raw_task_id or "unknown"
        task_subject = (
            raw_task_subject if not task_subject_was_missing else "(no subject)"
        )

        # task_id (from stdin) is sanitized for the two filesystem sinks it
        # feeds: read_task_json for the status read, and the marker join inside
        # shared.agent_handoff_marker.already_emitted for the O_EXCL dedup
        # marker. team_name is NOT sanitized here: it is read RAW from the
        # session context (the SSOT minted by pact_context.generate_team_name(),
        # which produces a "session-<id8>" name constrained to [a-f0-9-] at
        # production time — path-safe by construction, not an untrusted input).
        # Reading it raw matches the lead-side emit twin's marker-key derivation
        # (task_lifecycle_gate._emit_lead_side_agent_handoff reads the same
        # `get_pact_context().get("team_name", "")`), so all three emit paths
        # converge on one O_EXCL marker key by construction. The deprecated
        # stdin `team_name` read is dropped (a teammate-frame stdin value could
        # diverge from the SSOT and split the marker dir).
        task_id = sanitize_path_component(str(task_id))
        team_name = pact_context.get_pact_context().get("team_name", "")

        task_data = read_task_json(task_id, team_name)

        # Owner field (set at dispatch) is the authoritative "agent completed
        # this task" signal. Platform-provided teammate_name is fallback for
        # tasks without an owner (e.g. direct Agent dispatches).
        # #917 R2 (validate-before-claim): a WHITESPACE-only owner is not a valid
        # agent name — it fails the journal's non-empty-str `agent` schema
        # (session_journal._validate_event_schema) and would claim the O_EXCL
        # marker then fail append_event (the claim-without-write poison the
        # writability gate only narrowed). Treat it as ABSENT so the stdin
        # teammate_name fallback can still preserve the handoff; suppress only
        # when NEITHER source yields a non-whitespace name.
        owner_value = task_data.get("owner")
        if isinstance(owner_value, str) and not owner_value.strip():
            owner_value = None
        teammate_name = owner_value or input_data.get("teammate_name")
        if not teammate_name or not str(teammate_name).strip():
            # Non-agent completion (feature/infra task) OR no valid (non-
            # whitespace) agent name from either source — no HANDOFF to persist.
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

        # Handoff-presence + type gate. Suppress emission AND skip marker
        # creation when `metadata.handoff` is absent, empty, or NOT A DICT.
        # Required so that a fire arriving before the teammate has stored
        # handoff cannot claim the O_EXCL marker with empty content; the
        # substantive completion (with handoff populated) claims it instead.
        # M1: the journal schema requires handoff to be a dict, so a
        # truthy-but-non-dict handoff (str/list) would pass a bare presence
        # check, claim the marker, then FAIL append_event's schema validation
        # — an orphaned/poisoned marker (the same failure class the #917
        # writability gate below closes for the unwritable case). isinstance
        # makes a malformed handoff DEFER (claim nothing) too.
        handoff = task_metadata.get("handoff")
        if not isinstance(handoff, dict) or not handoff:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # #917: canonical-journal writability precondition. The O_EXCL marker
        # below is an optimistic "we emitted it" promise claimed BEFORE
        # append_event. On a teammate TaskCompleted frame, b1 resolves the
        # marker team_name from the session context (get_pact_context, above)
        # while its OWN session context may be unpersisted (teammate persist is
        # is_lead-gated, #877) -> get_journal_path() == "" -> the append below
        # would FAIL after the marker is claimed, poisoning it and permanently
        # suppressing the lead-side b2 emit via already_emitted(). Gate the claim
        # on writability so a non-writable fire DEFERS to a writable one (the
        # lead's b2) instead of poisoning. This is a PURE read (get_journal_path
        # does not create the marker); mark-then-write exactly-once below is
        # unchanged — only the precondition is added. The marker team_name and
        # get_journal_path()/append_event() now resolve from the SAME
        # get_pact_context()/get_session_dir() source, so a resolvable team_name
        # implies a resolvable journal path: the gate fires at least as often as
        # it must and cannot false-negative a fire that append would have
        # written.
        # F3: this gate is the TWIN of task_lifecycle_gate._emit_lead_side_agent_handoff
        # — keep both in parity. Mark-then-write / O_EXCL contract:
        # shared/agent_handoff_marker.already_emitted.
        if not get_journal_path():
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Idempotency guard — suppress duplicate fires for the same
        # (team, task_id, occupant). The marker is created ONLY when
        # handoff-presence is verified above; the optimistic ordering (marker
        # created before append_event completes) is now made safe by the #917
        # R1 compensating-unclaim below: a write that fails rolls the claim back
        # so a later writable/valid fire can re-emit. (Concurrency dedup is
        # preserved — the claim is still taken FIRST, so two simultaneous fires
        # cannot both write; only the loser-on-write rolls back its own marker.)
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
        #
        # #917 R1 (compensating-unclaim): we OWN the marker here (already_emitted
        # returned False = a fresh O_EXCL claim). If the write returns False
        # (schema rejection, or a resolvable-but-unwritable journal dir — the
        # residual paths the writability gate does NOT cover) OR raises, roll the
        # claim back so a later writable/valid fire can re-emit instead of being
        # permanently suppressed by the poisoned marker. Best-effort + fail-safe:
        # worst case the marker persists (today's pre-R1 behavior). F3 twin:
        # mirror this in task_lifecycle_gate._emit_lead_side_agent_handoff.
        try:
            written = append_event(
                make_event(
                    "agent_handoff",
                    agent=teammate_name,
                    task_id=task_id,
                    task_subject=task_subject,
                    handoff=handoff,
                ),
            )
        except Exception:
            written = False
        if not written:
            unclaim(team_name, task_id, occupant)

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
