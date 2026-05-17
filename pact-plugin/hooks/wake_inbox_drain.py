#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wake_inbox_drain.py
Summary: UserPromptSubmit hook that drains the team's wake-inbox markers
         (cross-session teammate-Arm and teammate-Teardown signals) on
         the lead's prompts. Drain returns per-type counts; Teardown
         markers route to _emit_teardown (with a Tier-1 idempotency
         check against `.teardown_request_emitted/{task_id}`) and Arm
         markers route to _emit_arm. Falls back to a
         count_active_tasks(team_name) >= 1 predicate for the
         lead-side unowned-create-then-owner-update dispatch surface.
Used by: hooks.json UserPromptSubmit hook (third entry, after
         bootstrap_marker_writer + bootstrap_prompt_gate).

Role in the asymmetric-guard Arm/Teardown model:
- wake_lifecycle_emitter.py's teammate pre-branches write per-marker
  JSON files to ~/.claude/teams/{team_name}/wake_inbox/ tagged with
  `type` ∈ {"arm", "teardown"}:
    * `_maybe_write_teammate_arm_marker` emits `type="arm"` when a
      teammate self-claims a task (TaskUpdate(status='in_progress'))
      or when a TaskCreate carries a teammate owner-at-create.
    * `_maybe_write_teammate_teardown_marker` emits `type="teardown"`
      when a self-complete-exempt teammate drives the team to a 1->0
      lifecycle-relevant active count from its own session.
  PostToolUse `additionalContext` targets the teammate's session —
  wrong session for an Arm/Teardown directive — so the filesystem
  marker IS the cross-session signal.
- This hook (UserPromptSubmit) runs on every lead prompt. It drains
  markers, counts them per `type`, and routes:
    * Any `type="teardown"` drained → check the Tier-1 sidecar
      `.teardown_request_emitted/{task_id}` marker first (de-dupes
      against teardown_request_emitter's TaskCompleted-fired Tier-1
      emission for the same task); if not pre-empted, write a
      `teardown_request` journal event (tier="2") and emit
      _TEARDOWN_DIRECTIVE.
    * Else `type="arm"` drained OR fallback predicate positive →
      emit _ARM_DIRECTIVE.
  Backward-compat: legacy markers without a `type` field default to
  "arm"; unknown / corrupt types fail-conservative to "arm".
- Both emissions are single-shot per prompt. The skill bodies'
  CronList exact-suffix-match (start-pending-scan) and CronDelete
  best-effort (stop-pending-scan) provide the idempotency truth — a
  redundant directive is benign because the skills no-op on
  cron-already-registered / cron-already-deleted.

Lead-Session Guard (Layer 0 — defense-in-depth):
- The drain hook ONLY emits in the lead session. The Layer 0 check
  is `is_lead_drain_authorized`, which checks
  `input_data.get('agent_id') is None` — the platform stamps
  `agent_id` on in-process subagent frames, so a teammate's
  UserPromptSubmit (if it ever fired in subagent — it does not, per
  Claude Code docs) would short-circuit to suppressOutput. The skill
  body's Layer 1 lead-session guard remains as backstop.

Single-emit discipline:
- Drain path consumed → emit Arm; do NOT also run the fallback or the
  producer-side idempotency check. Fallback is the LEAD-side
  unowned-dispatch recovery path; if markers were drained, the
  teammate-side signal already covered the surface and a second emit
  would be redundant (still benign under skill-body idempotency, but
  the single-emit shape is cleaner). Drain-path markers are FRESH
  cross-session signals — surface them regardless of armed-state.
- Drain path empty → producer-side idempotency check on the B-1
  fallback path: read both `scan_armed` and `scan_disarmed` events
  from this session's journal and compare timestamps. Suppress only
  when `scan_armed` is present AND (`scan_disarmed` is absent OR
  `scan_armed.armed_at` strictly greater than
  `scan_disarmed.disarmed_at`) — i.e., the most recent lifecycle
  event in this session was an arm, not a disarm. Otherwise (no
  scan_armed event, or scan_disarmed is at least as recent as
  scan_armed) run the count_active_tasks fallback. Positive count
  → emit Arm. Zero count → suppressOutput.
- Event-presence is the primary predicate; the timestamp comparison
  is gated on both events having well-typed int timestamps. A
  malformed event with `armed_at=None` is treated as if absent
  (fail-conservative emit). Schema validation at write-time
  (`_REQUIRED_FIELDS_BY_TYPE` in shared/session_journal.py) makes
  this an edge case rather than a happy path, but the explicit type
  check keeps the producer-side check robust to journal corruption.

Performance hygiene:
- Non-lead session short-circuits to suppressOutput before any
  task-store I/O. Per-prompt cost on the hot teammate path: one
  O(1) dict lookup (inside `is_lead_drain_authorized`); zero
  filesystem I/O. Task-store I/O (inbox glob + `count_active_tasks`)
  only runs in the lead session.

SACROSANCT module-load failure pattern:
- Module-load failures emit a fail-closed advisory via the stdlib-only
  `_emit_load_failure_advisory` sentinel (mirrors
  bootstrap_marker_writer.py / bootstrap_prompt_gate.py). Runtime
  exceptions in main logic suppressOutput at exit 0 — the wake
  mechanism is opportunistic; a crashed drain degrades to baseline
  user-invoked /PACT:start-pending-scan, not a hard failure.

Input: JSON from stdin with session_id, hook_event_name, etc.
Output: hookSpecificOutput with additionalContext on Arm trigger;
        {"suppressOutput": true} on every other path.
"""

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import sys
from typing import NoReturn


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed advisory for module-load failure.

    UserPromptSubmit cannot DENY the prompt; the strongest available
    signal is `additionalContext` injection. Uses ONLY stdlib (json,
    sys) so it remains functional even when every wrapped import below
    fails. Audit anchor: hookEventName must be present in any structured
    output.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"PACT wake_inbox_drain {stage} failure — the hook could "
                f"not drain the wake-inbox. {type(error).__name__}: "
                f"{error}. The pending-scan mechanism degrades to "
                f"user-invoked /PACT:start-pending-scan; teammate-Arm "
                f"signals may be missed until the underlying error is "
                f"resolved."
            ),
        }
    }))
    print(
        f"Hook load error (wake_inbox_drain / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ───────────────────────
try:
    import errno
    import os
    import re
    from typing import Any
    from pathlib import Path

    # Ensure shared package import resolves under the hooks directory.
    _hooks_dir = Path(__file__).parent
    if str(_hooks_dir) not in sys.path:
        sys.path.insert(0, str(_hooks_dir))

    import shared.pact_context as pact_context
    from shared.pact_context import get_team_name
    from shared.session_journal import append_event, make_event, read_last_event
    from shared.session_state import is_safe_path_component
    from shared.wake_lifecycle import count_active_tasks, is_lead_drain_authorized
    # Reuse the canonical _ARM_DIRECTIVE / _TEARDOWN_DIRECTIVE literals
    # from the emitter so the directive prose has a single source of
    # truth. The audit-anchor literal-prose pins on the emitter side
    # cover those constants; these imports make any future drift
    # immediately break the drain hook too.
    from wake_lifecycle_emitter import _ARM_DIRECTIVE, _TEARDOWN_DIRECTIVE
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


_SUPPRESS_OUTPUT = json.dumps({
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"},
})

# Bound stdin payload at 1 MB. UserPromptSubmit payloads carry the user's
# prompt text + session metadata; a 1 MB cap is generous and serves as
# defense-in-depth against parser amplification.
_MAX_PAYLOAD_BYTES = 1024 * 1024

# Bound per-marker body read at 8 KiB. The canonical marker schema is
# 7 fields well under 1 KiB; 8 KiB is generous headroom against schema
# growth while still capping parser-amplification risk on a corrupted
# or hostile file under the inbox path.
_MAX_MARKER_BYTES = 8192


def _wake_inbox_path(team_name: str) -> Path | None:
    """Resolve the team's wake-inbox directory or return None on
    path-safety failure.

    Path-traversal defense: `is_safe_path_component(team_name)` is
    the same allowlist applied by the teammate-Arm pre-branch in the
    emitter. team_name is read from pact-session-context.json which
    is itself written by trusted plugin code; this re-check is
    belt-and-suspenders. (Note: the wake_lifecycle.is_lead_*
    predicates no longer read team_config and thus no longer
    re-apply this allowlist — the allowlist surface contracted to
    the path-using callsites.)
    """
    if not isinstance(team_name, str) or not team_name:
        return None
    if not is_safe_path_component(team_name):
        return None
    return Path.home() / ".claude" / "teams" / team_name / "wake_inbox"


def _drain_markers(inbox_dir: Path) -> dict[str, Any]:
    """Drain every JSON marker in the inbox directory.

    Returns a dispatch dict with per-type consumed counts AND per-type
    task_id lists captured from successfully-classified marker bodies:
      {
        "arm": <int count>,
        "teardown": <int count>,
        "teardown_task_ids": <list[str] captured from drained
                              type="teardown" markers (deduplicated)>,
      }
    The `teardown_task_ids` list enables the Tier-1/Tier-2 double-
    emission guard at the caller (each id is checked against the
    shared `.teardown_request_emitted/{task_id}` sidecar marker dir
    before _emit_teardown fires). Arm markers do NOT carry task_ids in
    the return value — the caller doesn't need per-task identity for
    the Arm directive emission.

    Marker payloads without a `type` field, with an unrecognized type,
    or that fail JSON parsing default to `"arm"` — backward-compat
    with pre-type-field markers AND fail-conservative on corrupt/
    oversize bodies (the wake intent stands; Arm is the safe default
    because over-emit is benign under the skill body's CronList exact-
    suffix-match idempotency).

    Read failures and unlink failures are silently swallowed — the drain
    is best-effort; a stuck marker stays on disk and gets re-drained
    next prompt. The skill body's CronList match makes redundant Arm
    emits benign; redundant Teardown emits are similarly benign
    (stop-pending-scan no-ops if no cron entry exists).

    Sorted-glob iteration: the marker filename schema starts with an
    ISO-8601 compact UTC timestamp, so lexical order IS chronological.
    Forensic value only; the drain side consumes file PRESENCE not the
    timestamp.

    Size cap on marker read: the writer schema is tiny (8 fields,
    well under 1 KiB). An attacker (or a corrupted FS) producing a
    multi-MB file under the inbox path could amplify the drain read
    cost on every prompt. Pre-check via `os.path.getsize` and skip
    oversize markers (>8 KiB) — log to stderr and unlink without
    reading. The wake intent still stands (PRESENCE is the signal)
    so the unlink + count-as-consumed-arm posture is preserved.
    Oversize markers default to `"arm"` because the body cannot be
    read to classify; Arm is the safe fail-conservative branch.

    Backward-compat contract: existing callers that branch on a
    truthy/falsy total can keep doing so via `sum(result.values()) > 0`;
    the type-aware dispatch surface is opt-in.

    Pure-after-side-effect; never raises.
    """
    result: dict[str, Any] = {
        "arm": 0,
        "teardown": 0,
        "teardown_task_ids": [],
    }
    if not inbox_dir.exists():
        return result
    try:
        markers = sorted(inbox_dir.glob("*.json"))
    except OSError:
        return result
    seen_teardown_ids: set[str] = set()
    for marker in markers:
        try:
            # Size-cap pre-check: 8 KiB is generous against the
            # canonical 8-field payload. Oversize markers are treated
            # as wake signals (delete + count) but the body is NOT
            # read — protects against parser-amplification on a
            # corrupted or hostile file. Defaults to "arm" because
            # the type field cannot be classified without reading.
            try:
                size = os.path.getsize(str(marker))
            except OSError:
                size = -1
            marker_type = "arm"
            marker_task_id: str | None = None
            if size > _MAX_MARKER_BYTES:
                print(
                    f"wake_inbox_drain: oversize marker "
                    f"({size} bytes); skipping body read, unlinking",
                    file=sys.stderr,
                )
            else:
                # Read body to classify by `type` field. Fail-
                # conservative on malformed JSON / missing field /
                # unknown value: still treat as a wake signal (delete
                # + count as arm). A truncated/corrupt marker MEANS a
                # teammate session attempted to write and was
                # interrupted; the wake intent stands and Arm is the
                # safe default.
                try:
                    body_text = marker.read_text(encoding="utf-8")
                    body = json.loads(body_text)
                    if isinstance(body, dict):
                        body_type = body.get("type")
                        if body_type in ("arm", "teardown"):
                            marker_type = body_type
                        if marker_type == "teardown":
                            raw_task_id = body.get("task_id")
                            if (
                                isinstance(raw_task_id, str)
                                and raw_task_id
                            ):
                                marker_task_id = raw_task_id
                            else:
                                # Malformed teardown marker: type field
                                # says "teardown" but task_id is missing,
                                # empty, or non-string. Demote to "arm"
                                # so the downstream branch does not emit
                                # a Teardown directive WITHOUT going
                                # through the Tier-1/Tier-2 dedup guard
                                # (which keys on task_id). The wake intent
                                # still stands; Arm is the fail-
                                # conservative default that aligns with
                                # the corrupt-body fallback above. Log
                                # to stderr so the disk-corruption /
                                # producer-bug signal is not silent.
                                marker_type = "arm"
                                print(
                                    f"wake_inbox_drain: teardown marker "
                                    f"missing task_id ({marker.name}); "
                                    f"demoting to arm signal",
                                    file=sys.stderr,
                                )
                except (OSError, json.JSONDecodeError, ValueError):
                    # Default-to-arm already set above.
                    pass
            os.unlink(str(marker))
            result[marker_type] += 1
            if (
                marker_type == "teardown"
                and marker_task_id is not None
                and marker_task_id not in seen_teardown_ids
            ):
                seen_teardown_ids.add(marker_task_id)
                result["teardown_task_ids"].append(marker_task_id)
        except OSError:
            # Couldn't unlink — leave the marker on disk; next drain
            # will retry. Do NOT count this marker as consumed so the
            # fallback can still fire if needed.
            continue
    return result


def _emit_arm() -> None:
    """Print the _ARM_DIRECTIVE additionalContext payload with the
    required hookEventName field. Caller is responsible for sys.exit(0).
    """
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _ARM_DIRECTIVE,
        }
    }
    print(json.dumps(output))


def _emit_teardown() -> None:
    """Print the _TEARDOWN_DIRECTIVE additionalContext payload with the
    required hookEventName field. Caller is responsible for sys.exit(0).

    Sibling of _emit_arm; reached when a type="teardown" marker drains.
    Skill-body idempotency: stop-pending-scan no-ops if no cron entry
    exists, so a redundant Teardown emit is benign.
    """
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _TEARDOWN_DIRECTIVE,
        }
    }
    print(json.dumps(output))


def _sanitize_path_component(value: str) -> str:
    """Strip path-traversal fragments from a value destined for
    filesystem joins. Byte-identical to
    teardown_request_emitter._sanitize_path_component / agent_handoff_
    emitter._sanitize_path_component — see those modules' docstrings
    for the full rationale.
    """
    return re.sub(r"[/\\\x00-\x1f]|\.\.", "", value)


def _teardown_marker_dir(team_name: str) -> Path:
    """Per-team Teardown-emit marker directory path.

    SHARED with teardown_request_emitter.py — both Tier-1 (TaskCompleted
    handler) and Tier-2 (this drain) check + claim the same
    ~/.claude/teams/{team}/.teardown_request_emitted/{task_id}
    sidecar marker for cross-tier double-emission prevention.
    """
    return Path.home() / ".claude" / "teams" / team_name / ".teardown_request_emitted"


def _already_emitted_teardown(team_name: str, task_id: str) -> bool:
    """Test-and-set the per-(team, task_id) Teardown-emit marker.

    Copy-pasted from teardown_request_emitter._already_emitted with
    `_teardown_marker_dir` substituted as the dir provider, per the
    helper_location_elevation_pattern memory ("elevate on 3rd consumer").
    The shared marker dir means a Tier-1 fire that already created the
    marker for `task_id` causes this predicate to return True here,
    suppressing the Tier-2 double-emit.

    Returns True iff a prior fire (this tier OR Tier-1) already
    created the marker. Returns False on fresh fires — the marker is
    created as a side-effect of this call, atomic at the kernel level
    via O_CREAT|O_EXCL.

    Fail-open: on any OSError other than EEXIST, returns False so the
    caller emits. Data-integrity over duplication-prevention when the
    marker subsystem breaks. Symmetric with the Tier-1 helper.
    """
    if (
        not team_name
        or team_name in (".", "..")
        or not task_id
        or task_id in (".", "..")
    ):
        return False

    marker_dir = _teardown_marker_dir(team_name)
    marker_path = marker_dir / task_id
    # Pre-claimed fast path: when this predicate is called from
    # _decide_and_emit's loop over `teardown_task_ids`, every id whose
    # marker was already created by Tier-1 will hit this branch with
    # one cheap stat syscall instead of falling through to mkdir +
    # O_EXCL open (~3 syscalls). The race window between this exists()
    # check and the O_EXCL open below is BENIGN — a marker that
    # appears in the gap still produces the correct return True via
    # EEXIST. Semantics unchanged; cost is one stat per fresh-fire id
    # (negligible) in exchange for ~2 saved syscalls per pre-claimed
    # id under storm conditions. Only matters under theoretical storm
    # of N pre-claimed ids in one drain.
    try:
        if marker_path.exists():
            return True
    except OSError:
        # Stat failure (e.g. permission denied on the parent) — fall
        # through to the full mkdir+open path which has its own fail-
        # open behavior.
        pass
    # Symlink-on-dir defense-in-depth (mirrors teardown_request_emitter
    # Tier-1): a TOCTOU window exists between is_symlink() and
    # mkdir(exist_ok=True); the structural defense is O_NOFOLLOW on
    # the final O_EXCL open below, not this pre-check. Same-user trust
    # model keeps this acceptable; the pre-check is a fast-fail for the
    # cooperative case.
    if marker_dir.is_symlink():
        return False
    try:
        marker_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        return False

    # O_NOFOLLOW is the load-bearing structural defense (intermediate-
    # symlink coverage on top of O_EXCL's trailing-symlink refusal).
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
            return True  # prior fire (this tier or Tier-1) owns it
        return False  # any other error → fail-open, emit anyway


def _decide_and_emit(input_data: dict) -> None:
    """Run the drain + fallback decision tree and emit at most one
    _ARM_DIRECTIVE block.

    Decision order:
      1. Resolve team_name from session context. No team → suppressOutput.
      2. Resolve inbox_dir from team_name. Path-safety failure →
         suppressOutput.
      3. Lead-session guard. Non-lead session → suppressOutput
         (regardless of inbox state — teammate sessions never emit
         the Arm/Teardown directives; the directives are lead-targeted).
      4. Drain markers. _drain_markers returns a per-type count dict
         ({"arm": N, "teardown": M}). If sum > 0 → dispatch on type:
         teardown takes precedence over arm (fresh terminal-status
         signal outweighs stale in_progress signal); emit and return
         (single-emit discipline; skip the fallback). Drain-path
         markers are fresh cross-session signals — surface them
         regardless of armed-state.
      5. Producer-side idempotency: read `scan_armed` and
         `scan_disarmed` events from this session's journal.
         Suppress only when scan_armed is present with a well-typed
         armed_at AND (scan_disarmed is absent OR scan_armed.armed_at
         > scan_disarmed.disarmed_at). Any journal-read failure or
         malformed event falls through to step 6 (fail-conservative).
      6. Fallback: count_active_tasks(team_name) >= 1 → emit Arm.
         Otherwise → suppressOutput.

    Pure-after-side-effect; outer main() handles exception fail-open.
    """
    pact_context.init(input_data)
    team_name = get_team_name()
    if not team_name:
        print(_SUPPRESS_OUTPUT)
        return

    inbox_dir = _wake_inbox_path(team_name)
    if inbox_dir is None:
        print(_SUPPRESS_OUTPUT)
        return

    # Performance hygiene: non-lead-session early-out before any
    # task-store I/O. The non-lead path is the hot common case (every
    # teammate prompt fires this hook); short-circuit before the drain
    # glob and the count_active_tasks fallback. `is_lead_drain_authorized`
    # checks `agent_id` field-presence on the stdin payload (O(1) dict
    # lookup, no filesystem I/O), so the per-prompt cost on the hot
    # path is now zero disk reads — an improvement over the legacy
    # `is_lead_session` body which read team_config.json each fire.
    # Semantically a no-op under current platform behavior because
    # UserPromptSubmit does not fire in in-process subagent frames
    # per Claude Code docs; this migration is documentation-symmetry
    # with the emit-side callsites in the corridor, plus a future-
    # extension surface if UserPromptSubmit semantics ever change.
    if not is_lead_drain_authorized(input_data, team_name):
        print(_SUPPRESS_OUTPUT)
        return

    drained = _drain_markers(inbox_dir)
    drained_total = drained["arm"] + drained["teardown"]
    if drained_total > 0:
        # Type-aware dispatch surface. Teardown takes precedence over
        # Arm when both kinds drain in the same prompt: a fresh
        # terminal-status signal reflects the most recent completion-
        # authority state and outweighs a stale in_progress signal.
        #
        # Same-prompt arm+teardown disambiguation: when BOTH marker
        # kinds drain together AND the team still has lifecycle-
        # relevant work on disk (count_active_tasks > 0), the teardown
        # marker is STALER than the arm. Concrete race: teammate Y
        # completes terminal task Z (writes teardown marker when count
        # was 0); teammate X then claims a new task A (writes arm
        # marker); lead UserPromptSubmit drains both in one pass. The
        # arm signal reflects current state; the teardown is stale.
        # Demote teardown to arm in that case — single-emit discipline
        # preserved, cron stays armed, and the next Tier-1 fire (or a
        # later carve-out completion at count=0) handles the eventual
        # Teardown. Without this check the lead is told to tear down
        # cron while task A is active.
        if (
            drained["arm"] > 0
            and drained["teardown"] > 0
            and count_active_tasks(team_name) > 0
        ):
            _emit_arm()
            return
        if drained["teardown"] > 0:
            # Tier-1 / Tier-2 double-emission guard: each drained
            # teardown task_id is checked against the SHARED
            # .teardown_request_emitted/{task_id} sidecar marker dir
            # (also claimed by teardown_request_emitter.py). If Tier-1
            # already fired for the same (team, task_id), the marker
            # exists and the predicate returns True — suppressing the
            # Tier-2 emit. The first unclaimed id (if any) wins the
            # marker here and triggers the journal write + directive.
            #
            # Multiple teardown markers in one drain may come from
            # Stop-sweep secondary firings on the same task or from
            # genuinely-distinct carve-out completions on the same
            # prompt-boundary. Either way, ONE journal event + ONE
            # additionalContext directive per drain is the correct
            # shape (the directive is wake-hint, not per-task work).
            teardown_task_ids = drained.get("teardown_task_ids") or []
            claimed_task_id: str | None = None
            for tid in teardown_task_ids:
                if not _already_emitted_teardown(team_name, tid):
                    claimed_task_id = tid
                    break

            # When the drained markers carry NO task_ids (oversize or
            # corrupt-body fallback path classified them as teardown
            # via... wait, that can't happen — only successfully-
            # classified bodies with isinstance(body, dict) reach the
            # teardown branch). The empty case here covers a future
            # refactor that drops task_ids; fail-conservative emit
            # without journal write rather than silent suppression.
            if not teardown_task_ids:
                _emit_teardown()
                return

            if claimed_task_id is None:
                # Every drained id was pre-claimed by Tier-1.
                # Suppress — Tier-1 has already done the work.
                print(_SUPPRESS_OUTPUT)
                return

            # Write the journal event keyed to the FIRST claimed id;
            # the marker write inside _already_emitted_teardown above
            # already locked the sidecar for this id, so a subsequent
            # Tier-1 fire for the same id will be suppressed there.
            try:
                append_event(
                    make_event(
                        "teardown_request",
                        task_id=claimed_task_id,
                        team_name=team_name,
                        tier="2",
                        reason="wake_inbox_drained",
                    )
                )
            except Exception:
                # Journal write is the falsifiable trace, but the
                # directive emission is the wake-signal. Fail-
                # conservative: emit the directive even if the
                # journal write fails — under-emit on the lead's
                # directive is the worse failure mode.
                pass
            _emit_teardown()
        else:
            _emit_arm()
        return

    # Producer-side idempotency on the B-1 fallback path: read the most
    # recent `scan_armed` and `scan_disarmed` events from this session's
    # journal. Suppress the redundant Arm directive only when the lead's
    # most recent lifecycle action was an arm (not a disarm) — i.e., the
    # cron is currently armed in this session. The strict-greater
    # comparison ensures a re-arm following a teardown surfaces an Arm
    # directive (re-arm dominance); only a stale arm-without-subsequent-
    # disarm suppresses.
    #
    # Event-presence is the primary predicate; timestamp comparison is
    # gated on both fields being well-typed int values. A malformed
    # event (missing armed_at, or wrong type) is treated as if absent
    # and falls through to the existing emit behavior — over-emit is
    # benign under the skill body's CronList exact-suffix-match
    # idempotency; under-emit could miss a teammate's completion-
    # authority signal.
    #
    # Outer-except rationale: the producer-side check has a strict
    # fail-conservative contract — any unexpected failure must fall
    # through to the existing emit behavior, not silently suppress.
    # `except Exception` aligns the catch with the contract in the
    # comment block above ("over-emit is benign... under-emit could miss
    # a teammate's completion-authority signal"). A narrower catch
    # (e.g., `(ImportError, AttributeError, TypeError)`) would let any
    # other exception class propagate up to main()'s outer Exception
    # handler, which prints _SUPPRESS_OUTPUT — i.e., under-emit, which
    # is exactly the failure mode this contract forbids. The wider
    # catch closes that future-refactor footgun.
    #
    # Today the call surface is benign — eager top-level imports at the
    # wrapped-imports block above, and read_last_event has its own
    # outer `except Exception` returning None — so this catch is
    # functionally a no-op on the currently-exercisable paths. It pins
    # the contract for any future refactor that introduces lazy
    # imports, missing attributes on a reshaped event dict, or new
    # exception classes from the journal-read path.
    #
    # Producer-side deterministic Python check, NOT LLM-self-diagnosis
    # at the directive site — distinct from the failure mode that
    # start-pending-scan.md §Audit forbids.
    try:
        armed = read_last_event("scan_armed")
        disarmed = read_last_event("scan_disarmed")
        if armed is not None:
            armed_at = armed.get("armed_at")
            if isinstance(armed_at, int) and not isinstance(armed_at, bool):
                if disarmed is None:
                    print(_SUPPRESS_OUTPUT)
                    return
                disarmed_at = disarmed.get("disarmed_at")
                if (
                    isinstance(disarmed_at, int)
                    and not isinstance(disarmed_at, bool)
                    and armed_at > disarmed_at
                ):
                    print(_SUPPRESS_OUTPUT)
                    return
    except Exception:
        pass

    # B-1 fallback: lead-side unowned-create-then-owner-update dispatch
    # pattern produces no teammate-side write opportunity. Cover it via
    # count_active_tasks — any lifecycle-relevant teammate task on disk
    # warrants the scan being armed.
    if count_active_tasks(team_name) >= 1:
        _emit_arm()
        return

    print(_SUPPRESS_OUTPUT)


def main() -> None:
    # Outer catch-all preserves the exit-0 fail-open contract against
    # any unexpected exception. UserPromptSubmit hooks fire on every
    # prompt; a raise here would surface as a hook-error UI on every
    # turn. The wake mechanism is opportunistic — a crashed drain
    # degrades to user-invoked /PACT:start-pending-scan.
    try:
        try:
            buffer = sys.stdin.read(_MAX_PAYLOAD_BYTES + 1)
        except (IOError, OSError):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        if len(buffer) > _MAX_PAYLOAD_BYTES:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        try:
            input_data = json.loads(buffer)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        if not isinstance(input_data, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        _decide_and_emit(input_data)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)


if __name__ == "__main__":
    main()
