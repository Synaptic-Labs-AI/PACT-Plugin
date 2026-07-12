"""
Location: pact-plugin/hooks/shared/session_journal.py
Summary: Append-only JSONL event store for GC-proof workflow state persistence.
Used by: session_init.py, session_end.py, agent_handoff_emitter.py (hooks);
         orchestrate.md, comPACT.md, peer-review.md, wrap-up.md, pause.md
         (commands invoke via CLI: python3 session_journal.py write|read|read-last).

Write path: O_APPEND append, protected by an exclusive advisory lock
(`fcntl.flock(LOCK_EX)`) around the short-write loop in `_atomic_write`.
POSIX only guarantees `os.write` atomicity up to PIPE_BUF (512 bytes on
macOS, 4096 on Linux); for larger events the short-write loop would
otherwise open an interleaving window between iterations where another
O_APPEND writer could splice its bytes into the middle of ours. The
flock closes that window, so concurrent writers (hooks + orchestrator
CLI calls) are safe for events of any size on single-host local
filesystems. The guarantee is single-host only — advisory locks do not
cross machine boundaries and NFS flock semantics are unreliable — which
is fine because pact-sessions is per-host already. The short-write
loop itself remains the guard against partial writes from signal
interruption.

Read path: Sequential scan with type filtering. For typical sessions
(<200 events, <80KB), full scan completes in <5ms. For crash recovery,
read from end to find last checkpoint, then replay forward.

Durability: Best-effort. The write+rename+lock cycle protects against
interleaving and partial writes from concurrent writers, but `_atomic_write`
does NOT call `fsync` after the write. After a hard crash (power loss,
kernel panic), the most recent event may be lost even though
`append_event` returned True. This is intentional — the journal lives on
the orchestrate hot path (every checkpoint, phase transition, dispatch)
and a per-write fsync is too expensive there. Durability "to OS buffers"
is the contract; cross-process visibility is immediate after the lock
releases.

Dual API pattern:
- Implicit API (hooks): append_event(), read_events(), read_last_event(),
  get_journal_path() — derive path via pact_context.get_session_dir().
- Explicit API (resume/CLI): read_events_from(session_dir), read_last_event_from(session_dir)
  — caller provides session directory path.

File location: ~/.claude/pact-sessions/{slug}/{session_id}/session-journal.jsonl
Permissions: 0o600 (owner read/write only)
Directory permissions: 0o700 (owner only)
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Schema version for journal events.
_SCHEMA_VERSION = 1


# Tail window size for `_read_last_event_at`. Reverse-scan reads at most
# this many trailing bytes before falling back to a full-file slurp. In a
# typical active session 32 KB covers ~100-300 JSONL entries, which is
# the common-case window for the most-recent event of any tracked type.
# Worst case (target event older than 32 KB from EOF) falls back to the
# pre-optimization full-slurp cost.
_TAIL_WINDOW_BYTES = 32 * 1024


# Per-type required fields, derived from actual writer call sites. Every
# entry here reflects what a production writer ACTUALLY produces (grep
# `make_event("{type}"` in hooks/ and `write --type {type}` in commands/),
# not an aspirational schema. Unknown types (e.g. "test" in unit tests)
# bypass per-type validation by design and only get baseline v/type/ts
# checks — this preserves test ergonomics without loosening production
# safety. Note the trade-off: a typo in `event_type` at a writer call site
# (e.g. `make_event("phse_transition", ...)`) will silently bypass per-type
# validation rather than raise. The mitigation is the test in
# TestValidateEventSchemaPerType — every production type MUST have a test
# entry, which catches typos in tests rather than at runtime.
#
# Each required field maps to its expected Python type. At validate time,
# the validator checks presence AND `isinstance(value, expected_type)` AND
# — for str fields — rejects empty or whitespace-only values. The expected
# types reflect what the writer literally produces: unquoted values in the
# --data JSON become int/bool/list/dict, quoted values become str.
#
# When adding a new event type, add it here with its required field → type
# mapping AND add a test to TestValidateEventSchemaPerType in
# test_session_journal.py.
#
# Trust boundary: write path validates events against this dict; read path
# trusts disk content. Loosening this dict without auditing all readers
# will silently break extractors assuming validated shape.
_REQUIRED_FIELDS_BY_TYPE: dict[str, dict[str, type]] = {
    # hooks/session_init.py writes session_start with team, session_id,
    # project_dir, worktree on the valid-stdin path only (under R3, the event
    # is dropped entirely when stdin lacks session_id to avoid an unreapable
    # `unknown-*` directory leak). Of these, session_id and project_dir are
    # the load-bearing fields downstream consumers depend on; team is
    # redundant with CLAUDE.md and worktree is empty at write time.
    "session_start": {"session_id": str, "project_dir": str},
    # commands/orchestrate.md writes variety_assessed with task_id (quoted
    # string) and variety (nested JSON object → dict). This is the FEATURE-level
    # variety (written once for the feature task) — distinct from the
    # per-dispatch dispatch_variety below.
    "variety_assessed": {"task_id": str, "variety": dict},
    # hooks/task_lifecycle_gate.py emits dispatch_variety on the TaskCreate of a
    # Task-B carrying metadata.variety (one per dispatch). The GC-immune mirror
    # of the per-dispatch variety stamp (#955) — the task store that holds
    # metadata.variety is reaped by the teams/tasks reaper, so wrap-up Q5 read
    # false-empty after GC; this journal event is the durable source.
    # task_id is the Task-B id; variety is the 5-key dict (4 dims + total).
    # The emitter PROJECTS metadata.variety to exactly these 5 keys
    # (DISPATCH_VARIETY_KEYS) before append — the *_rationale strings are NOT
    # mirrored (pact-variety.md §5.1). Read by wrap-up Q5 as the GC-immune
    # source for compute_variety_divergence's dispatch_varieties list, which
    # consumes only .total. (variety is typed `dict`, so the schema check
    # enforces only the top-level task_id+variety keys — the projection lives
    # at the emit site, not here.)
    "dispatch_variety": {"task_id": str, "variety": dict},
    # hooks/task_lifecycle_gate.py emits teachback_ack on the lead's
    # TaskUpdate(A, status="completed") accepting a teachback whose Task-A
    # metadata carries teachback_submit.variety_acknowledgment (#955). task_id is
    # the Task-A id; rationale_articulates_this_dispatch is the teammate's
    # "yes"|"no"|"concern" flag. The GC-immune mirror read by wrap-up Q6's
    # cargo_cult_signal_rate (the task store goes false-empty after GC).
    "teachback_ack": {"task_id": str, "rationale_articulates_this_dispatch": str},
    # hooks/shared/task_metadata_snapshot.emit_task_metadata_snapshot writes
    # task_metadata_snapshot at completion (seams in task_lifecycle_gate.py +
    # agent_handoff_emitter.py) — the GC-immune mirror of non-handoff task
    # metadata (the N-key generalization of dispatch_variety/teachback_ack).
    # task_id is the source task; metadata is the size-bounded sibling-key
    # payload (SNAPSHOT_EXCLUDE'd, truncation-marked — see the substrate module).
    "task_metadata_snapshot": {"task_id": str, "metadata": dict},
    # commands/orchestrate.md + comPACT.md write phase_transition with
    # phase + status (both quoted strings). session_resume._build_journal_resume
    # subscripts `p["phase"]` — this schema check is the defensive bulwark
    # against F1.
    "phase_transition": {"phase": str, "status": str},
    # commands/orchestrate.md writes checkpoint with phase (quoted string) +
    # completed_phases + active_agents + variety + pending_phases + safe_to_retry.
    # Only `phase` is universally required; the rest vary per checkpoint context.
    "checkpoint": {"phase": str},
    # commands/orchestrate.md + comPACT.md write agent_dispatch with agent,
    # task_id, phase (all quoted strings) + scope (list).
    "agent_dispatch": {"agent": str, "task_id": str, "phase": str},
    # hooks/agent_handoff_emitter.py writes agent_handoff with agent, task_id,
    # task_subject (all strings) and handoff (dict from task metadata).
    # All four are load-bearing for the secretary.
    "agent_handoff": {
        "agent": str,
        "task_id": str,
        "task_subject": str,
        "handoff": dict,
    },
    # hooks/missed_wake_scan.py writes missed_wake (the #903 deferred
    # missed-wake alarm) with task_id, agent (the idle teammate / task owner),
    # and since (the intentional_wait timestamp — also the re-arm discriminator
    # for the per-(team,task_id,since) marker). All three are load-bearing:
    # which task is stuck, who is waiting, and since when.
    "missed_wake": {
        "task_id": str,
        "agent": str,
        "since": str,
    },
    # commands/orchestrate.md writes s2_state_seeded with worktree (quoted
    # string), agents (JSON list), and boundaries (JSON object → dict).
    # No hook-based writer; CLI-only event.
    "s2_state_seeded": {"worktree": str, "agents": list, "boundaries": dict},
    # commands/orchestrate.md + comPACT.md write commit with sha, message,
    # phase (all quoted strings).
    "commit": {"sha": str, "message": str, "phase": str},
    # commands/peer-review.md writes review_dispatch with pr_number (unquoted
    # int), pr_url (quoted string), reviewers (JSON list).
    "review_dispatch": {"pr_number": int, "pr_url": str, "reviewers": list},
    # commands/peer-review.md writes review_finding with severity, finding,
    # reviewer, task_id (all quoted strings).
    "review_finding": {"severity": str, "finding": str, "reviewer": str},
    # commands/peer-review.md writes remediation with cycle (unquoted int),
    # items (JSON list), fixer (quoted string).
    "remediation": {"cycle": int, "items": list, "fixer": str},
    # commands/peer-review.md writes pr_ready with pr_number (unquoted int),
    # pr_url (quoted string), commits (unquoted int).
    "pr_ready": {"pr_number": int, "pr_url": str, "commits": int},
    # commands/pause.md writes session_paused with pr_number (unquoted int),
    # pr_url/branch/worktree_path (quoted strings),
    # consolidation_completed (unquoted bool), team_name (quoted string).
    "session_paused": {
        "pr_number": int,
        "pr_url": str,
        "branch": str,
        "worktree_path": str,
        "consolidation_completed": bool,
    },
    # commands/refresh.md writes session_refreshed at the PERSIST/VERIFY step with
    # consolidation_completed (unquoted bool) and halt_active (unquoted bool).
    # Optional mid-flight fields (halt_task_ids, feature_task_id, feature_subject,
    # team_name, next_phase, worktrees, pr_number) are validated when present via
    # _OPTIONAL_FIELDS_BY_TYPE; a degenerate no-workstream refresh writes the two
    # required fields only. worktrees is typed list — the validator is SHALLOW on
    # list fields (same caveat as artifact_paths); per-element validity is the
    # writer's responsibility. pr_number is surface-only and never gates surfacing.
    "session_refreshed": {
        "consolidation_completed": bool,
        "halt_active": bool,
    },
    # commands/bootstrap.md writes session_refresh_consumed at CONFIRMED resumption
    # (never at surface time). refresh_ts (quoted string) binds the consumption to
    # ONE specific session_refreshed event's ts — a later refresh is never retired
    # by an earlier consumption.
    "session_refresh_consumed": {
        "refresh_ts": str,
    },
    # hooks/session_end.py writes session_end with NO required fields — one
    # writer passes an optional `warning` (line 119), the other passes
    # nothing (line 316). commands/wrap-up.md CLI also writes session_end
    # with no --data. Baseline v/type/ts validation is the only requirement.
    "session_end": {},
    # hooks/session_end.py writes cleanup_summary after the teams/tasks
    # reaper runs (#412 Fix B). No required fields — the event is a counts
    # audit trail and every field is optional by design. The empty-dict
    # entry is structurally necessary: _validate_event_schema short-circuits
    # on unknown types and skips the _OPTIONAL_FIELDS_BY_TYPE loop, so a
    # type must be registered here to activate optional-field type checks.
    "cleanup_summary": {},
    # commands/wrap-up.md + pause.md write session_consolidated after the
    # secretary's memory-consolidation Pass 2 completes (#453 Fix B). No
    # required fields — the event's mere existence is the detector signal
    # consumed by session_end.check_unpaused_pr. Empty-dict registration
    # activates the _OPTIONAL_FIELDS_BY_TYPE enforcement below (same pattern
    # as session_end and cleanup_summary).
    "session_consolidated": {},
    # The lead-frame command emit sites (plan-mode.md, peer-review.md, and
    # orchestrate.md's PREPARE/ARCHITECT/CODE phase-output validation) write
    # artifact_paths via the CLI write path — a path-only, GC-durable pointer
    # to each phase's on-disk artifact(s). It outlives `git worktree remove`
    # because the journal lives under ~/.claude/pact-sessions/, outside the
    # worktree; only the pointed-at file is worktree-ephemeral. The secretary
    # resolves these events at harvest and distills the artifact substance into
    # pact-memory. `workflow` is the lowercase phase/workflow tag (one of
    # plan-mode / prepare / architect / peer-review / code-auditor) and the
    # dedup/precedence axis; `feature` is the slug scoping the event to one arc;
    # `paths` is the PLURAL full-enumeration list of worktree-absolute artifact
    # paths (a phase may write >1 file).
    #
    # Validator-depth caveat: `paths` is typed `list`, so the schema check
    # enforces only isinstance(value, list) — it does NOT descend into the list
    # to require each element be a non-empty str (the per-field empty-string
    # guard applies to `str` fields only). Per-element path validity is the
    # WRITER's responsibility: the emit sites drop empty/invalid entries and
    # drop the whole emit when the glob found nothing (an empty paths list
    # passes isinstance but is meaningless — that "missing artifact" case is the
    # task_lifecycle_gate backstop's job, NOT a zero-length event).
    "artifact_paths": {"workflow": str, "feature": str, "paths": list},
}


# Per-type optional fields, with expected Python type. Fields listed here
# are NOT required — an event missing them still passes validation — but
# when they ARE present, the validator enforces type. This is the schema
# contract counterpart to runtime clamps (e.g. the `_VALID_SOURCES` clamp
# in session_init.py): if a future writer bypasses the normalization
# path and emits the wrong type directly to `make_event`, the event is
# rejected at validate time rather than landing on disk.
# Same type-symmetry rules as _REQUIRED_FIELDS_BY_TYPE: `int` fields
# reject `bool` because bool subclasses int.
#
# When adding a new optional field, add it here and add a matching
# happy-path + wrong-type case to TestValidateOptionalFieldTypes in
# test_session_journal.py.
_OPTIONAL_FIELDS_BY_TYPE: dict[str, dict[str, type]] = {
    # hooks/session_init.py writes session_start with an optional `source`
    # drawn from stdin. The session_init normalization path clamps non-str
    # inputs to "unknown" before the journal write; this schema contract
    # catches any future writer that bypasses that path.
    "session_start": {"source": str},
    # hooks/session_end.py writes session_end with an optional `warning`
    # string when check_unpaused_pr detects an open PR that was NOT
    # paused (no memory consolidation). The empty-dict registration in
    # _REQUIRED_FIELDS_BY_TYPE above ("session_end": {}) is what
    # ACTIVATES this optional check — _validate_event_schema
    # short-circuits on unknown event types and would otherwise skip
    # the optional loop. Symmetric with the cleanup_summary registration
    # shipped in the same PR (#412 Fix B).
    "session_end": {"warning": str},
    # hooks/session_end.py writes cleanup_summary after the teams/tasks
    # reaper runs (#412 Fix B). Counts-only payload; no identifying names
    # (audit surface area minimization). `teams_ran`/`tasks_ran`
    # discriminate "reaper executed and found nothing" (True, 0/0) from
    # "reaper short-circuited at callsite" (False, 0/0) per side; without
    # them the two states are indistinguishable in the journal. Cycle-8
    # split these from the older single `reaper_ran` bool and the single
    # `ttl_days` int into per-reaper fields so an auditor can tell WHICH
    # side short-circuited and which TTL applied on either side
    # (currently both default to 30 days; split future-proofs against
    # TTL divergence).
    "cleanup_summary": {
        "teams_reaped": int,
        "teams_skipped": int,
        "tasks_reaped": int,
        "tasks_skipped": int,
        "teams_ttl_days": int,
        "tasks_ttl_days": int,
        "teams_ran": bool,
        "tasks_ran": bool,
    },
    # commands/wrap-up.md + pause.md write session_consolidated after the
    # secretary's consolidation Pass 2 completes (#453 Fix B). The existence
    # of the event is the signal consumed by session_end.check_unpaused_pr;
    # these fields are audit trail for session_resume summaries and future
    # observability. `pass` distinguishes which consolidation pass ran
    # (1 or 2); the two count fields are advisory and may be 0 when the
    # secretary cannot produce exact numbers.
    "session_consolidated": {
        "pass": int,
        "task_count": int,
        "memories_saved": int,
    },
    # commands/refresh.md writes session_refreshed with optional mid-flight
    # pointers, present only when the refresh found an active workstream:
    # halt_task_ids (JSON list of signal-task ids — diagnostic cross-check;
    # the live task store is the SSOT at resume time), feature_task_id /
    # feature_subject (the in_progress feature task), next_phase (bounded
    # vocabulary prepare|architect|code|test|peer-review|deploy — a WRITER-side
    # contract documented in refresh.md; the shallow validator does not
    # enforce enums), worktrees (list of absolute paths; SHALLOW list check —
    # per-element validity is the writer's responsibility, same caveat as
    # artifact_paths), team_name, and pr_number (surface-only, never gates
    # surfacing). The required-fields registration above
    # ("session_refreshed": {...}) is what ACTIVATES this optional check —
    # _validate_event_schema short-circuits on unknown types and would
    # otherwise skip the optional loop (same activation pattern as
    # session_end / missed_wake / teachback_ack).
    "session_refreshed": {
        "halt_task_ids": list,
        "feature_task_id": str,
        "feature_subject": str,
        "team_name": str,
        "next_phase": str,
        "worktrees": list,
        "pr_number": int,
    },
    # hooks/missed_wake_scan.py writes missed_wake with optional task_subject
    # (human-readable task label) and reason (the intentional_wait reason —
    # always "awaiting_lead_completion" for this alarm, recorded for journal
    # readers). The required-fields registration above ("missed_wake": {...})
    # is what ACTIVATES this optional check — _validate_event_schema
    # short-circuits on unknown types and would otherwise skip the optional loop.
    "missed_wake": {
        "task_subject": str,
        "reason": str,
    },
    # hooks/task_lifecycle_gate.py writes teachback_ack with an optional concern
    # string — the teammate's variety_acknowledgment.concern, present only when
    # rationale_articulates_this_dispatch is "no"/"concern" (per pact-variety.md;
    # a "yes" ack legitimately omits it). The required-fields registration above
    # ("teachback_ack": {...}) is what ACTIVATES this optional check —
    # _validate_event_schema short-circuits on unknown types and would otherwise
    # skip the optional loop (same activation pattern as session_end /
    # missed_wake).
    "teachback_ack": {
        "concern": str,
    },
    # hooks/shared/task_metadata_snapshot.emit_task_metadata_snapshot writes
    # task_metadata_snapshot with these optionals. subject is sentinel-
    # substituted "(no subject)" when degenerate (never required-invalid,
    # #917 validate-before-claim); owner is absent for ownerless (e.g.
    # signal) tasks; task_type mirrors metadata.type so readers distinguish
    # signal snapshots (signal tasks DO snapshot — the agent_handoff
    # suppression's reader-purity basis does not transfer to the durability
    # mirror); truncated is present (True) only when size-bounding fired;
    # occupant is occupant_hash(owner or "", subject) — the task-id-reuse
    # discriminator readers join against the agent_handoff event's
    # (agent, task_subject) identity. The required-fields registration above
    # ("task_metadata_snapshot": {...}) is what ACTIVATES this optional check
    # — _validate_event_schema short-circuits on unknown types and would
    # otherwise skip the optional loop (same activation pattern as
    # session_end / missed_wake / teachback_ack).
    "task_metadata_snapshot": {
        "subject": str,
        "owner": str,
        "task_type": str,
        "truncated": bool,
        "occupant": str,
    },
    # commands/peer-review.md writes remediation with an optional task_id —
    # the fixer's Task-B task id. The Q5 coverage denominator
    # (variety_divergence.count_task_b_dispatch_sites) uses it to dedup a
    # comPACT/orchestrate-dispatched remediation that ALSO emits
    # agent_dispatch for the same task_id, so the site is counted once.
    # Optional because a remediation may omit it; an id-less remediation is
    # counted as a distinct site (fail-safe — never undercounts the
    # denominator). The required-fields registration above
    # ("remediation": {...}) activates this optional check.
    "remediation": {
        "task_id": str,
    },
    # The lead-frame emit sites write artifact_paths with an optional `task_id`
    # — the phase task id the lead completed when emitting (provenance /
    # cross-link). Absent for plan-mode/peer-review syntheses that have no phase
    # task; present (and a non-empty str) for the PREPARE/ARCHITECT/CODE phase
    # emits. The required-fields registration above ("artifact_paths": {...})
    # is what ACTIVATES this optional check — _validate_event_schema
    # short-circuits on unknown types and would otherwise skip the optional loop
    # (same activation pattern as session_end / missed_wake / teachback_ack).
    "artifact_paths": {
        "task_id": str,
    },
}


# --- Write API ---


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """
    Construct a journal event dict with common fields pre-filled.

    Sets v=1 and ts=current UTC time. Caller provides type-specific fields.
    A caller-supplied `ts` (in **fields) is honored — it is only auto-set
    when the caller does not provide one. This lets test fixtures and
    backfill tooling stamp deterministic timestamps without round-tripping
    through the journal.

    Args:
        event_type: Event type string (e.g., "agent_handoff", "session_start")
        **fields: Type-specific fields to include in the event. May include
            an explicit `ts` to override the auto-set timestamp.

    Returns:
        Complete event dict ready for append_event()
    """
    event: dict[str, Any] = {
        "v": _SCHEMA_VERSION,
        "type": event_type,
    }
    event.update(fields)
    # Use setdefault so a caller-supplied ts in **fields is preserved.
    # Without setdefault, the previous unconditional assignment silently
    # discarded any caller ts and contradicted the docstring.
    event.setdefault(
        "ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return event


def _validate_event_schema(event: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate that an event dict has the required schema fields.

    Baseline (all event types):
    - 'v' is an int (and NOT a bool — Python bool is a subclass of int,
      so it must be rejected explicitly)
    - 'type' is a non-empty str (whitespace-only is rejected)

    Per-type (only for types in _REQUIRED_FIELDS_BY_TYPE):
    - Every required field is present and not None.
    - Every required field has the expected Python type (isinstance check).
      `int` fields reject `bool` explicitly because bool is an int subclass;
      a writer passing `pr_number=True` would otherwise slip through.
    - `str` fields additionally reject empty and whitespace-only values —
      a blank `phase` or `agent` is functionally indistinguishable from
      missing for every downstream consumer, and the baseline `type` check
      already uses the same semantics for consistency.
    - Unknown event types (e.g. free-form "test" used in unit tests) pass
      per-type validation by design — the whitelist is opt-in enforcement
      for known types. The trade-off: a typo in a production
      `make_event("…")` call site silently bypasses per-type checks. The
      TestValidateEventSchemaPerType suite catches that at test time.

    Optional fields (for types in _OPTIONAL_FIELDS_BY_TYPE):
    - Absent fields pass (the field is optional by definition).
    - Present fields must match the declared type, applying the same
      bool-in-int + empty-str rules as required fields. This is the
      schema-level counterpart to runtime clamping paths such as the
      `source` isinstance guard in session_init.py — a future writer
      that bypasses the clamp and emits the wrong type directly to
      `make_event` is rejected at validate time.

    This is the bulwark that prevents BugF1: a malformed `phase_transition`
    event (missing `phase` field, or `phase=""`, or `phase=42`) from any
    writer causes `append_event` or the CLI write path to return False
    BEFORE the bad line reaches disk, so `_build_journal_resume` in the
    next session never has to deal with it. The defensive consumer is
    still a backstop for anything that slips past this (e.g. events from
    prior schema versions).

    Returns:
        A `(ok, reason)` tuple. `ok` is True only when every check passes;
        `reason` is a short human-readable string. On success `reason` is
        "ok". On failure `reason` identifies the first failing check so
        callers (notably the CLI write path) can surface a precise error
        to stderr instead of a generic "invalid event schema" line.
    """
    v = event.get("v")
    if not isinstance(v, int) or isinstance(v, bool):
        return False, "v must be int"
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return False, "type must be non-empty str"
    required = _REQUIRED_FIELDS_BY_TYPE.get(event_type)
    if required is None:
        # Unknown event type — opt-in enforcement, pass through.
        return True, "ok"
    for field, expected_type in required.items():
        if field not in event or event[field] is None:
            return (
                False,
                f"missing required field '{field}' for type '{event_type}'",
            )
        value = event[field]
        # int fields must reject bool even though bool subclasses int —
        # symmetric with the baseline v check above.
        if expected_type is int and isinstance(value, bool):
            return (
                False,
                f"field '{field}' for type '{event_type}' must be int, "
                f"got bool",
            )
        if not isinstance(value, expected_type):
            return (
                False,
                f"field '{field}' for type '{event_type}' must be "
                f"{expected_type.__name__}, got {type(value).__name__}",
            )
        # Fix B (RG2): str fields additionally reject empty or
        # whitespace-only values — a blank phase/agent/task_id would
        # pass the isinstance check but break every downstream consumer.
        if expected_type is str and not value.strip():
            return (
                False,
                f"field '{field}' for type '{event_type}' must be "
                f"non-empty string",
            )
    # artifact_paths element-level guard (belt-and-suspenders): the generic
    # per-field check above is SHALLOW — it confirms `paths` is a `list` but
    # does not descend into its elements. The locked design makes per-element
    # validity the writer's responsibility (the emit sites drop empty/invalid
    # paths and skip an empty glob); this schema-layer check augments that with
    # a defensive backstop so a writer that bypasses the emit-site discipline
    # cannot land a `paths` list holding a non-str or empty/whitespace-only
    # element on disk, where a downstream reader would treat it as a real path.
    # Scoped to artifact_paths ONLY — other list-typed required fields
    # (s2_state_seeded.agents, review_dispatch.reviewers, remediation.items)
    # keep their existing shallow contract and are untouched.
    if event_type == "artifact_paths":
        for element in event["paths"]:
            if not isinstance(element, str) or not element.strip():
                return (
                    False,
                    "field 'paths' for type 'artifact_paths' must contain "
                    "only non-empty strings",
                )
    # Per-type optional field checks. Absent fields pass (that's what
    # "optional" means); present fields must match the declared type.
    # Symmetric with required-field checks: rejects bool in int fields,
    # rejects empty/whitespace-only str. Event types with no optional
    # declarations (the common case) get a no-op empty dict from .get()
    # and skip the loop entirely.
    optional = _OPTIONAL_FIELDS_BY_TYPE.get(event_type, {})
    for field, expected_type in optional.items():
        if field not in event or event[field] is None:
            continue  # Absent optional field — pass through.
        value = event[field]
        if expected_type is int and isinstance(value, bool):
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be int, got bool",
            )
        if not isinstance(value, expected_type):
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be {expected_type.__name__}, got {type(value).__name__}",
            )
        if expected_type is str and not value.strip():
            return (
                False,
                f"optional field '{field}' for type '{event_type}' must "
                f"be non-empty string",
            )
    return True, "ok"


def append_event(event: dict[str, Any]) -> bool:
    """
    Append a single event to the current session's journal.

    Path is derived implicitly via pact_context.get_session_dir().
    Creates the session directory if it doesn't exist (mkdir -p, 0o700).
    Serializes event to JSON, appends newline, writes atomically via
    O_WRONLY | O_APPEND | O_CREAT with 0o600 permissions.

    Args:
        event: Event dict. Must include 'v' (int) and 'type' (non-empty str).
            'ts' is auto-set if missing. Invalid events cause a silent
            return False (fail-open).

    Returns:
        True if write succeeded, False on any error (fail-open).
    """
    try:
        # Validate required schema fields (shared with CLI write path).
        # In-process API is fail-open: the caller gets a bool and the
        # reason is intentionally discarded — hooks never surface per-type
        # validator messages to end users. The CLI write path below has a
        # symmetric call site that DOES print the reason.
        ok, _reason = _validate_event_schema(event)
        if not ok:
            return False

        # Auto-set timestamp if missing
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        # Derive path from session context (implicit — current session)
        journal = _journal_path()
        if journal is None:
            # AdvF2 Approach 4: warn (but do not fail) when the implicit API
            # is invoked before pact_context.init(). The return value still
            # honors the existing fail-open contract — the warning is purely
            # additive so a missing init() in a hook surfaces as a visible
            # signal during development instead of a silent no-op in
            # production. The is_initialized() check pinpoints the missing
            # init root cause; if pact_context IS initialized but the path is
            # still unavailable, that's a different failure mode (e.g.
            # missing session_id) and we leave the existing silent fail-open
            # in place to avoid noise.
            if not _pact_context_is_initialized():
                print(
                    "session_journal: append_event called before "
                    "pact_context.init() — returning False (this may "
                    "indicate a hook missing session_id)",
                    file=sys.stderr,
                )
            return False

        # Ensure directory exists (mkdir -p with 0o700)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Serialize and write atomically via O_APPEND
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print(
                "session_journal: append_event failed: write error",
                file=sys.stderr,
            )
            return False
        return True

    except Exception as e:
        print(
            f"session_journal: append_event failed: {e}",
            file=sys.stderr,
        )
        return False


# --- Read API ---


def read_events(
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from the current session's journal, optionally filtered by type.

    Path is derived implicitly via pact_context.get_session_dir().
    Reads the full journal file, parses each line as JSON, and returns
    events matching the filter. Malformed lines are silently skipped
    (each event is self-contained — one bad line doesn't affect others).

    Args:
        event_type: If provided, only return events with this type.
            If None, return all events.

    Returns:
        List of event dicts, in chronological order (oldest first).
        Empty list if journal doesn't exist or on any error.
    """
    try:
        journal = _journal_path()
        if journal is None:
            # AdvF2 Approach 4: see append_event for rationale. Warns only
            # when the path is unavailable AND pact_context was never
            # initialized — the canonical "hook forgot to call init()" bug.
            if not _pact_context_is_initialized():
                print(
                    "session_journal: read_events called before "
                    "pact_context.init() — returning [] (this may indicate "
                    "a hook missing session_id)",
                    file=sys.stderr,
                )
            return []
        return _read_events_at(journal, event_type, since)
    except Exception:
        return []


def read_events_from(
    session_dir: str,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from a specific session's journal (explicit path).

    Used for cross-session reads (resume, CLI) where the caller knows
    the session directory path.

    Args:
        session_dir: Absolute path to the session directory.
        event_type: If provided, only return events with this type.
            If None, return all events.

    Returns:
        List of event dicts, in chronological order (oldest first).
        Empty list if journal doesn't exist or on any error.
    """
    if not session_dir:
        # AdvF2 Approach 4 (parity with implicit API): emit a stderr
        # warning before the silent fallback so an unset/empty
        # session_dir at the call site surfaces as a visible signal
        # rather than a mute empty result. The empty list is preserved
        # so callers see the same return contract.
        print(
            "session_journal: read_events_from called with empty session_dir",
            file=sys.stderr,
        )
        return []
    journal = _journal_path_from(session_dir)
    return _read_events_at(journal, event_type, since)


def resolve_latest_artifacts(
    events: list[dict[str, Any]],
    feature: str,
) -> dict[str, list[str]]:
    """Resolve the superseded artifact path-list per workflow for one feature.

    Pure (no I/O): the caller supplies already-read `artifact_paths` events
    (e.g. `read_events_from(session_dir, "artifact_paths")`) and the feature
    slug; this returns one entry per workflow that emitted artifacts for that
    feature, valued by that workflow's latest path-list.

    Supersede semantics: filter to `e["feature"] ==
    feature`; group by `e["workflow"]`; within each group keep the
    latest-`ts` event. Each `artifact_paths` event carries the COMPLETE
    path-list for its `(workflow, feature)` (a full enumeration per emit,
    not a delta), so the latest event is self-sufficient — paths are NEVER
    merged across events. A phase re-run that regenerates its doc in place
    therefore supersedes the prior emit instead of duplicating it.

    Tie-break = LAST-wins: when two events for the same `(workflow, feature)`
    carry an equal `ts`, the one iterated later (the more-recently-written in
    journal order) wins — see `_ts_supersedes`. `make_event` stamps `ts` at
    second granularity, so a same-second double-emit is resolved to the last
    write, which is the authoritative complete snapshot.

    Defensive: non-dict entries and events missing `workflow`/`feature`/
    `paths` are skipped (parity with the `_read_events_at` `isinstance(...,
    dict)` guard), and any non-string element inside a surviving event's
    `paths` list is dropped from the emitted list (the same isinstance-guard
    discipline applied at element granularity, so a malformed path entry can
    never flow through to the JSON output). Timestamp handling is FAIL-OPEN
    (see `_ts_supersedes`): a
    missing/unparseable `ts` on a candidate does not let it supersede a
    well-formed incumbent, and an unresolved incumbent ts is replaced by any
    well-formed candidate. A pair of parseable-but-incomparable timestamps
    (e.g. one tz-aware, one tz-naive) is caught at the comparison and keeps
    the incumbent rather than raising — the resolution never crashes on a
    malformed `ts`.

    Args:
        events: Candidate journal events (typically all `artifact_paths`
            events for the session). Any list of dicts is accepted; the
            feature/type filtering happens here.
        feature: The feature slug to resolve (matched against `e["feature"]`).

    Returns:
        `{workflow: paths}` — one key per workflow with a surviving event,
        valued by that workflow's superseded (latest-`ts`, last-wins on a
        tie) complete path-list, with any non-string element filtered out.
        Empty dict if no event matches.
    """
    latest_by_workflow: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("feature") != feature:
            continue
        workflow = event.get("workflow")
        paths = event.get("paths")
        if not isinstance(workflow, str) or not isinstance(paths, list):
            continue
        prior = latest_by_workflow.get(workflow)
        if prior is None or _ts_supersedes(event.get("ts"), prior.get("ts")):
            latest_by_workflow[workflow] = event
    return {
        workflow: [p for p in event["paths"] if isinstance(p, str)]
        for workflow, event in latest_by_workflow.items()
    }


def _ts_supersedes(candidate_ts: Any, incumbent_ts: Any) -> bool:
    """Return True if a later-iterated `candidate_ts` should supersede the
    incumbent — i.e. the candidate is newer than OR EQUAL TO the incumbent.

    Used by `resolve_latest_artifacts` to pick the surviving event per
    workflow. Timestamps are PARSED (via `_parse_ts`), never lexically
    string-compared — see `_parse_ts` for the `Z` vs `+00:00` rationale.

    Tie-break = LAST-wins (`>=`, not `>`): on an equal `ts`, the candidate
    (iterated later, hence the more-recently-written event in journal order)
    supersedes. Each `artifact_paths` emit is a COMPLETE snapshot, so the
    last write for a `(workflow, feature)` is the authoritative one even when
    two emits collide in the same wall-clock second (`make_event` stamps `ts`
    at second granularity).

    Fail-open comparison (matches the in-house `_ts_ge` pattern): the parse
    AND the comparison are guarded. A missing/unparseable `candidate_ts`
    returns False, so a malformed candidate never supersedes a well-formed
    incumbent. A missing/unparseable `incumbent_ts` returns True, so a
    well-formed candidate replaces a malformed incumbent.

    Naive/aware coercion: if a parsed value is tz-NAIVE (only possible from a
    corrupted/externally-merged journal — `make_event` always stamps aware
    `...Z`), it is assumed UTC and coerced to tz-aware before the comparison,
    so a naive-vs-aware pair compares by actual INSTANT (the later instant
    wins) instead of raising `TypeError`. The comparison stays wrapped in a
    `try/except TypeError` that fail-opens (returns False, keeps the incumbent)
    for any residual uncomparable pair, so the resolution never crashes.
    """
    try:
        candidate = _parse_ts(candidate_ts)
    except (ValueError, TypeError):
        return False
    try:
        incumbent = _parse_ts(incumbent_ts)
    except (ValueError, TypeError):
        return True
    # Coerce a tz-naive value to tz-aware UTC so a naive-vs-aware pair compares
    # by instant rather than raising TypeError (which the outer guard would turn
    # into a fail-open keep-stale). Assuming UTC is the safe interpretation; a
    # naive ts only arises from a corrupted journal (make_event always stamps Z).
    candidate = candidate if candidate.tzinfo is not None else candidate.replace(tzinfo=timezone.utc)
    incumbent = incumbent if incumbent.tzinfo is not None else incumbent.replace(tzinfo=timezone.utc)
    try:
        return candidate >= incumbent
    except TypeError:
        return False


def _normalize_trailing_z(value: Any) -> str:
    """Rewrite a SINGLE trailing `Z` UTC designator to `+00:00`, leaving any
    interior `Z` intact.

    The anchor is TRAILING-ONLY (`str.endswith`, no `re` dependency): an
    interior `Z` is not a valid ISO-8601 field, so leaving it intact lets the
    downstream `fromisoformat` reject the whole string rather than a blanket
    `.replace("Z", "+00:00")` rewriting it mid-string. On `_parse_ts`'s
    return/raise the trailing-only and replace-all forms are observationally
    identical (any interior `Z` is unparseable either way); the anchor matters
    at the STRING layer, which this helper isolates and makes testable.
    """
    s = str(value)
    return s[:-1] + "+00:00" if s.endswith("Z") else s


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp, normalizing a trailing `Z` to `+00:00`
    (via `_normalize_trailing_z`).

    `make_event` stamps `ts` as `...Z` while `canonical_since()` emits
    `...+00:00`; normalizing lets the two compare as equal-instant
    datetimes. A lexical string compare would be WRONG — `'+'` 0x2B sorts
    before `'Z'` 0x5A, so a `+00:00` ts would sort before an equal-instant
    `Z` ts. Raises ValueError/TypeError on missing/malformed input; callers
    decide the fail-open policy.
    """
    return datetime.fromisoformat(_normalize_trailing_z(value))


def _ts_ge(event_ts: Any, since: str | None) -> bool:
    """Return True if `event_ts >= since`, compared as parsed datetimes.

    The arc-scope filter (`--since`) MUST parse the timestamps (via
    `_parse_ts`), not string-compare them — see `_parse_ts` for the
    format-mismatch rationale.

    Fail-open: when `since` is falsy the event is included (no filtering);
    when either timestamp is missing or unparseable the event is INCLUDED
    (returns True) so a malformed/absent ts is never silently dropped from
    a scoped read.
    """
    if not since:
        return True
    try:
        return _parse_ts(event_ts) >= _parse_ts(since)
    except (ValueError, TypeError):
        return True


def _read_events_at(
    journal: Path,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Shared read implementation for both implicit and explicit APIs.

    Reads with `errors="replace"` so a single invalid byte sequence
    (e.g., from a botched write or a truncated multibyte character)
    substitutes U+FFFD for the bad bytes instead of raising
    UnicodeDecodeError. A bad line corrupts at most its own line; every
    other event in the file is still returned. Two per-line hazards are
    isolated: (1) a line that fails to parse as JSON is dropped by the
    `except (json.JSONDecodeError, ValueError)` below; (2) a line that
    parses as valid JSON but is NOT a dict (e.g. `[1,2,3]`, `"str"`,
    `42`, `null`) is dropped by the `isinstance(event, dict)` guard —
    without that guard, `.get()` on a non-dict value raises
    AttributeError (not in the except tuple), which would propagate to
    the outer `except Exception` and drop the WHOLE file, hiding every
    otherwise-valid event behind one bad line.

    `since`: when set, only events whose `ts` is >= `since` (inclusive,
    parsed via `_ts_ge`) are returned — the arc-scope filter. None/empty
    returns the whole journal (single-arc behavior unchanged).
    """
    try:
        if not journal.exists():
            return []

        events: list[dict[str, Any]] = []
        for line in journal.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                # A line can be valid JSON yet NOT a dict (e.g. `[1,2,3]`,
                # `"str"`, `42`, `null`). `.get()` on such a value raises
                # AttributeError — which is NOT in the except tuple below, so it
                # would propagate to the outer `except Exception` and drop the
                # WHOLE file's events (every event hidden behind one bad line).
                # Skip a non-dict line exactly like a malformed one so it
                # corrupts at most itself, preserving the per-line isolation the
                # docstring promises.
                if not isinstance(event, dict):
                    continue
                if event_type and event.get("type") != event_type:
                    continue
                if not _ts_ge(event.get("ts"), since):
                    continue
                events.append(event)
            except (json.JSONDecodeError, ValueError):
                continue  # Skip malformed lines
        return events

    except Exception:
        return []


def read_last_event(
    event_type: str,
    since: str | None = None,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from the current session's journal.

    Path is derived implicitly via pact_context.get_session_dir().
    Scans lines in reverse for efficiency — returns as soon as the
    first (most recent) match is found.

    Args:
        event_type: Event type to search for.

    Returns:
        The last matching event dict, or None if not found.
    """
    try:
        journal = _journal_path()
        if journal is None:
            # AdvF2 Approach 4: see append_event for rationale. Warns only
            # when the path is unavailable AND pact_context was never
            # initialized — the canonical "hook forgot to call init()" bug.
            if not _pact_context_is_initialized():
                print(
                    "session_journal: read_last_event called before "
                    "pact_context.init() — returning None (this may "
                    "indicate a hook missing session_id)",
                    file=sys.stderr,
                )
            return None
        return _read_last_event_at(journal, event_type, since)
    except Exception:
        return None


def read_last_event_from(
    session_dir: str,
    event_type: str,
    since: str | None = None,
) -> dict[str, Any] | None:
    """
    Read the most recent event of a given type from a specific session's journal.

    Used for cross-session reads (resume, CLI) where the caller knows
    the session directory path.

    Args:
        session_dir: Absolute path to the session directory.
        event_type: Event type to search for.

    Returns:
        The last matching event dict, or None if not found.
    """
    if not session_dir:
        # AdvF2 Approach 4 (parity with implicit API): emit a stderr
        # warning before the silent fallback so an unset/empty
        # session_dir at the call site surfaces as a visible signal
        # rather than a mute None result. The None is preserved so
        # callers see the same return contract.
        print(
            "session_journal: read_last_event_from called with "
            "empty session_dir",
            file=sys.stderr,
        )
        return None
    journal = _journal_path_from(session_dir)
    return _read_last_event_at(journal, event_type, since)


def _scan_lines_for_event(
    lines: list[str],
    event_type: str,
    since: str | None = None,
) -> dict[str, Any] | None:
    """Reverse-iterate decoded lines, returning the first matching event.

    Shared by tail-window and full-slurp scan paths. Skips blank lines and
    silently drops malformed JSON (symmetric with the pre-optimization
    contract: corrupted lines never poison the scan). A line that parses as
    valid JSON but is NOT a dict (e.g. `[1,2,3]`, `"str"`, `42`, `null`) is
    skipped by the `isinstance(event, dict)` guard — parity with
    `_read_events_at`: without it, `.get()` on a non-dict value raises
    AttributeError (not in the except tuple), which would propagate to the
    caller's outer `except Exception` and abort the whole reverse scan,
    making `read_last_event*` return None (e.g. `session_end` would conclude
    the session was never paused).

    `since`: when set, an event matching `event_type` whose `ts` is < `since`
    (parsed via `_ts_ge`) is skipped — the reverse scan then returns the
    most recent matching event at/after `since`, or None.
    """
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            if event.get("type") == event_type and _ts_ge(
                event.get("ts"), since
            ):
                return event
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _read_last_event_at(
    journal: Path,
    event_type: str,
    since: str | None = None,
) -> dict[str, Any] | None:
    """Shared reverse-scan implementation for both implicit and explicit APIs.

    Performance: reads the trailing `_TAIL_WINDOW_BYTES` first and scans
    that window in reverse; only falls back to a full-file slurp when the
    target event is not found in the tail window. For the steady-state
    common case (target event written within the most recent ~100-300
    journal entries) the per-call cost is O(_TAIL_WINDOW_BYTES) instead
    of O(file_size). The fallback preserves the pre-optimization contract:
    return the most recent matching event anywhere in the file, or None
    if no such event exists.

    Reads with `errors="replace"` symmetric with `_read_events_at`. A single
    invalid byte sequence (e.g., from a botched write or truncated multibyte
    character) would otherwise raise `UnicodeDecodeError` and poison the
    entire reverse scan — `read_last_event_from(session_dir, "session_paused")`
    would then return None and `session_end.py` would conclude the session
    was never paused. The replacement substitutes U+FFFD for bad bytes, so
    at most the corrupted line is dropped by the per-line `json.loads`.

    Tail-window safety: line boundaries are ASCII 0x0A and cannot fall
    inside a multibyte UTF-8 sequence, so splitting bytes on b"\\n" before
    decoding never bisects a character. When the tail window starts mid-
    line (file size > tail window), the leading partial line is discarded.
    """
    try:
        if not journal.exists():
            return None

        size = journal.stat().st_size
        if size == 0:
            return None

        if size <= _TAIL_WINDOW_BYTES:
            # Small journal: single full read, no tail-window optimization.
            return _scan_lines_for_event(
                journal.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines(),
                event_type,
                since,
            )

        # Large journal: tail-window-first, full-slurp fallback.
        with journal.open("rb") as fh:
            fh.seek(size - _TAIL_WINDOW_BYTES)
            tail_bytes = fh.read()
        tail_text = tail_bytes.decode("utf-8", errors="replace")
        tail_lines = tail_text.splitlines()
        # Discard the leading partial line: the tail window starts mid-
        # file, so the first line is almost certainly truncated. Skipping
        # it costs at most one event that an event older than the tail
        # window would be missed by — the full-slurp fallback below
        # catches that case.
        if tail_lines:
            tail_lines = tail_lines[1:]

        match = _scan_lines_for_event(tail_lines, event_type, since)
        if match is not None:
            return match

        # Tail miss: full-slurp fallback preserves the pre-optimization
        # contract. The target event is older than _TAIL_WINDOW_BYTES from
        # EOF, or absent entirely. With `since` set, a tail that holds only
        # pre-`since` matches also lands here and the full scan returns the
        # most recent match at/after `since` (or None).
        return _scan_lines_for_event(
            journal.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(),
            event_type,
            since,
        )

    except Exception:
        return None


def get_journal_path() -> str:
    """
    Return the absolute path to the journal file for the current session.

    Path is derived implicitly via pact_context.get_session_dir().
    Does not check existence. Used by callers that need the path
    for logging or external tooling.

    Returns:
        Absolute path string, or empty string if session dir unavailable.
    """
    journal = _journal_path()
    if journal is None:
        return ""
    return str(journal)


# --- Internal ---


def _get_session_dir() -> str:
    """
    Resolve session directory via pact_context.get_session_dir().

    Uses lazy import to avoid module-level coupling. Tries the package
    path first (shared.pact_context — works from hooks/ and tests), then
    falls back to bare module (pact_context — works when CLI runs
    session_journal.py directly, where shared/ is on sys.path).

    Separated into its own function so tests can monkeypatch it at
    `session_journal._get_session_dir` without dealing with import paths.

    Returns:
        Session directory path string, or "" if unavailable.
    """
    try:
        from shared.pact_context import get_session_dir
    except ImportError:
        from pact_context import get_session_dir  # type: ignore[no-redef]
    return get_session_dir()


def _pact_context_is_initialized() -> bool:
    """
    Return True iff pact_context.init() has been called for this process.

    AdvF2 Approach 4 (universal visibility): the implicit-API entry points
    use this to print a stderr warning when a caller invokes them BEFORE
    the surrounding hook has called `pact_context.init(input_data)`. The
    warning is purely additive — the existing fail-open semantics (return
    [], None, or False) are preserved so a missed init() never crashes a
    hook. The signal lets maintainers find the missing init() during
    development instead of debugging silent empty results in production.

    Lazy import mirrors `_get_session_dir` so tests that monkeypatch the
    helper at the session_journal level continue to work.

    Returns:
        True if pact_context._context_path is set, False otherwise.
    """
    try:
        from shared.pact_context import is_initialized
    except ImportError:
        from pact_context import is_initialized  # type: ignore[no-redef]
    return is_initialized()


def _journal_path() -> Path | None:
    """
    Derive the journal file path from the current session context.

    Returns:
        Path to session-journal.jsonl, or None if session dir unavailable.
    """
    session_dir = _get_session_dir()
    if not session_dir:
        return None
    return Path(session_dir) / "session-journal.jsonl"


def _journal_path_from(session_dir: str) -> Path:
    """Compute the journal file path from an explicit session directory."""
    return Path(session_dir) / "session-journal.jsonl"


def _validate_cli_session_dir(session_dir: str) -> int:
    """
    Validate the CLI `--session-dir` flag, returning a non-zero exit code
    on failure (and printing the reason to stderr) or 0 on success.

    Two checks, applied to write/read/read-last alike:

    1. Empty string — argparse `required=True` only catches a missing
       flag; an explicit `--session-dir ""` slips past it. Without this
       guard the path silently resolves to `./session-journal.jsonl`
       in the caller's CWD and creates a stray journal file.

    2. Non-absolute path — relative paths are also caller-CWD-relative
       and equally surprising. The journal MUST live under
       `~/.claude/pact-sessions/{slug}/{session_id}/`, so requiring an
       absolute path eliminates an entire class of stray-file bugs.

    Returns exit code 1 (matching the prior empty-string regression test
    contract — non-zero is the load-bearing property; hooks watch for it
    rather than discriminating on the specific code).
    """
    if not session_dir:
        print(
            "session_journal: --session-dir must be non-empty",
            file=sys.stderr,
        )
        return 1
    if not Path(session_dir).is_absolute():
        print(
            "session_journal: --session-dir must be an absolute path",
            file=sys.stderr,
        )
        return 1
    return 0


def _atomic_write(path: Path, data: bytes) -> bool:
    """
    Append *data* to *path* under an exclusive advisory lock.

    Returns True on success, False on OSError or a non-progressing write.
    File is created with 0o600 if it does not exist. The caller is
    responsible for ensuring the parent directory exists before calling.

    Concurrency guarantee: `fcntl.flock(LOCK_EX)` serializes the entire
    write block against other writers that honor the same lock. POSIX
    only guarantees `os.write` atomicity up to PIPE_BUF (512 bytes on
    macOS, 4096 on Linux); for larger payloads the short-write loop
    below would otherwise leave a window between iterations where an
    interleaving O_APPEND from another process could splice bytes into
    the middle of our event and produce a malformed JSONL line. The
    lock closes that window for any event size. Single-host only
    (advisory locks do not cross machines, and NFS flock semantics
    are unreliable) — fine because pact-sessions is per-host.

    Short-write loop rationale: `os.write` can still return fewer bytes
    than requested when interrupted by a signal even while the lock is
    held; the loop retries from where we left off. A non-positive
    return from os.write indicates a failure we cannot recover from —
    bail out and let the caller see False.

    Durability semantics — best-effort, NO fsync. The function returns
    True once the bytes have been handed to the kernel, but does not
    invoke `os.fsync` or `os.fdatasync`. After a hard crash (power loss,
    kernel panic) the most recent event(s) may be lost even though the
    caller saw True. This is intentional: the journal sits on the
    orchestrate hot path (every checkpoint, phase transition, dispatch)
    and a per-write fsync is too expensive — observed write rates would
    drop by 1-2 orders of magnitude on rotational disks. Cross-process
    visibility is immediate after the lock releases; only post-crash
    durability is sacrificed. Callers that need stronger durability
    should fsync at a coarser granularity (e.g., session_end).
    """
    try:
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            return False
        try:
            view = memoryview(data)
            total = 0
            while total < len(view):
                try:
                    n = os.write(fd, view[total:])
                except OSError:
                    return False
                if n <= 0:
                    # Non-progressing write — treat as failure so the
                    # caller can log and return False up the stack rather
                    # than spin forever.
                    return False
                total += n
            return True
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# --- CLI ---


def main() -> int:
    """
    CLI entry point for session journal operations.

    Subcommands:
        write  — Append an event via make_event() + append_event()
        read   — Read events, optionally filtered by type, output JSON
        read-last — Read the most recent event of a given type, output JSON

    Returns:
        0 on success, 1 on error.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Session journal CLI — append and query JSONL events.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    # --- write ---
    write_p = sub.add_parser("write", help="Append an event to the journal")
    write_p.add_argument("--type", required=True, dest="event_type",
                         help="Event type string (e.g. phase_transition)")
    write_p.add_argument("--session-dir", required=True,
                         help="Session directory path")
    # --data and --stdin are mutually exclusive ways to supply event fields.
    # --data accepts a JSON object as a CLI argument; --stdin reads the same
    # JSON object from standard input. The stdin path exists so that command
    # files can pipe JSON via heredoc — eliminating shell quoting bugs where
    # an apostrophe in a template-substituted value (e.g., a commit message
    # like "fix: don't crash") would otherwise close the bash single-quoted
    # --data argument and silently drop the journal event under set -e + ERR
    # trap. See r9 HIGH "template injection" finding (PR #350).
    data_group = write_p.add_mutually_exclusive_group()
    data_group.add_argument("--data", default=None,
                            help="JSON object of extra event fields "
                                 "(mutually exclusive with --stdin)")
    data_group.add_argument("--stdin", action="store_true",
                            help="Read the JSON object of extra event "
                                 "fields from standard input "
                                 "(mutually exclusive with --data)")

    # --- read ---
    read_p = sub.add_parser("read", help="Read events (JSON array to stdout)")
    read_p.add_argument("--session-dir", required=True,
                        help="Session directory path")
    read_p.add_argument("--type", default=None, dest="event_type",
                        help="Filter by event type")
    read_p.add_argument("--since", default=None,
                        help="Arc-scope lower bound (inclusive): only events "
                             "with ts >= this ISO-8601 UTC timestamp. Parsed, "
                             "not string-compared; fail-open on unparseable.")

    # --- read-last ---
    last_p = sub.add_parser("read-last",
                            help="Read the most recent event of a type")
    last_p.add_argument("--session-dir", required=True,
                        help="Session directory path")
    last_p.add_argument("--type", required=True, dest="event_type",
                        help="Event type to find")
    last_p.add_argument("--since", default=None,
                        help="Arc-scope lower bound (inclusive): only consider "
                             "events with ts >= this ISO-8601 UTC timestamp.")

    args = parser.parse_args()

    if args.command == "write":
        # Resolve the JSON payload from either --stdin or --data. The
        # mutually-exclusive group above guarantees both cannot be set
        # simultaneously; if neither is set we default to "{}" so writers
        # that pass no extra fields (e.g., wrap-up's session_end) keep
        # working unchanged.
        if args.stdin:
            data_source = "stdin"
            try:
                raw = sys.stdin.read()
            except OSError as exc:
                print(f"session_journal: failed to read --stdin: {exc}",
                      file=sys.stderr)
                return 1
        else:
            data_source = "--data"
            raw = args.data if args.data is not None else "{}"

        try:
            extra = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"session_journal: invalid {data_source} JSON: {exc}",
                  file=sys.stderr)
            return 1

        if not isinstance(extra, dict):
            print(f"session_journal: {data_source} must be a JSON object",
                  file=sys.stderr)
            return 1

        event = make_event(args.event_type, **extra)

        # Apply the same schema validation as append_event() — extra fields
        # in --data may shadow defaults from make_event() (e.g., a caller
        # passing {"v": true} would overwrite the default v=1 with a bool).
        # Unlike append_event (which is fail-open), the CLI surfaces the
        # exact failure reason so operators see precisely which check fired
        # instead of a generic "v must be int" line that may not apply.
        ok, reason = _validate_event_schema(event)
        if not ok:
            print(
                f"session_journal: invalid event schema ({reason})",
                file=sys.stderr,
            )
            return 1

        # AdvF4 + AdvF5: validate --session-dir up front. Helper enforces
        # both the empty-string guard and the absolute-path requirement,
        # mirroring the read/read-last subcommands so all three share one
        # contract.
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc

        journal = _journal_path_from(args.session_dir)
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = json.dumps(event, separators=(",", ":")) + "\n"
        if not _atomic_write(journal, entry.encode("utf-8")):
            print("session_journal: write failed", file=sys.stderr)
            return 1
        return 0

    elif args.command == "read":
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc
        events = read_events_from(
            args.session_dir, event_type=args.event_type, since=args.since
        )
        print(json.dumps(events))
        return 0

    elif args.command == "read-last":
        rc = _validate_cli_session_dir(args.session_dir)
        if rc != 0:
            return rc
        event = read_last_event_from(
            args.session_dir, args.event_type, since=args.since
        )
        if event is None:
            print("null")
        else:
            print(json.dumps(event))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
