#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wake_inbox_drain.py
Summary: UserPromptSubmit hook that drains the team's wake-inbox markers
         (cross-session teammate-Arm signals) on the lead's prompts and
         emits a single _ARM_DIRECTIVE via hookSpecificOutput.additionalContext
         when either (a) markers are present OR (b) the count_active_tasks
         fallback shows at least one lifecycle-relevant teammate task.
Used by: hooks.json UserPromptSubmit hook (third entry, after
         bootstrap_marker_writer + bootstrap_prompt_gate).

Role in the asymmetric-guard Arm/Teardown model:
- wake_lifecycle_emitter.py's teammate-Arm pre-branch writes per-marker
  JSON files to ~/.claude/teams/{team_name}/wake_inbox/ when a teammate
  self-claims a task (TaskUpdate(status='in_progress')) or when a
  TaskCreate carries a teammate owner-at-create. PostToolUse
  `additionalContext` targets the teammate's session — wrong session
  for an Arm directive — so the filesystem marker IS the cross-session
  signal.
- This hook (UserPromptSubmit) runs on every lead prompt. It drains
  markers + falls back to a count_active_tasks(team_name) >= 1
  predicate that covers the lead-side unowned-create-then-owner-update
  dispatch pattern surface (where no teammate-side write opportunity
  ever exists — the lead's TaskCreate is unowned, the subsequent
  TaskUpdate(owner) carries no status transition).
- Either trigger emits exactly one _ARM_DIRECTIVE block per prompt
  (combined drain + fallback single-emit discipline). The skill body's
  CronList exact-suffix-match is the single idempotency truth — a
  redundant Arm directive is benign because start-pending-scan no-ops
  if its cron entry already exists.

Lead-Session Guard (Layer 0 — defense-in-depth):
- The drain hook ONLY emits in the lead session. A teammate's
  UserPromptSubmit fires this hook too, but `is_lead_session` returns
  False there and the hook short-circuits to suppressOutput. The skill
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
  team_config.json read (inside `is_lead_session`) + one session_id
  compare. Task-store I/O (inbox glob + `count_active_tasks`) only
  runs in the lead session.

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
    import os
    from pathlib import Path

    # Ensure shared package import resolves under the hooks directory.
    _hooks_dir = Path(__file__).parent
    if str(_hooks_dir) not in sys.path:
        sys.path.insert(0, str(_hooks_dir))

    import shared.pact_context as pact_context
    from shared.pact_context import get_team_name
    from shared.session_journal import read_last_event
    from shared.session_state import is_safe_path_component
    from shared.wake_lifecycle import count_active_tasks, is_lead_session
    # Reuse the canonical _ARM_DIRECTIVE literal from the emitter so the
    # directive prose has a single source of truth. The audit-anchor
    # literal-prose pin (test in test_wake_lifecycle_bug_b_rearm.py)
    # covers the emitter side; this import makes any future drift
    # immediately break the drain hook too.
    from wake_lifecycle_emitter import _ARM_DIRECTIVE
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

    Path-traversal defense: `is_safe_path_component(team_name)` is the
    same allowlist applied by `is_lead_session` and the teammate-Arm
    pre-branch in the emitter. team_name is read from
    pact-session-context.json which is itself written by trusted
    plugin code; this re-check is belt-and-suspenders.
    """
    if not isinstance(team_name, str) or not team_name:
        return None
    if not is_safe_path_component(team_name):
        return None
    return Path.home() / ".claude" / "teams" / team_name / "wake_inbox"


def _drain_markers(inbox_dir: Path) -> int:
    """Drain every JSON marker in the inbox directory.

    Returns the count of markers successfully consumed (read + deleted).
    Read failures and unlink failures are silently swallowed — the drain
    is best-effort; a stuck marker stays on disk and gets re-drained
    next prompt. The skill body's CronList match makes redundant Arm
    emits benign.

    Sorted-glob iteration: the marker filename schema starts with an
    ISO-8601 compact UTC timestamp, so lexical order IS chronological.
    Forensic value only; the drain side consumes file PRESENCE not the
    timestamp.

    Size cap on marker read: the writer schema is tiny (7 fields,
    well under 1 KiB). An attacker (or a corrupted FS) producing a
    multi-MB file under the inbox path could amplify the drain read
    cost on every prompt. Pre-check via `os.path.getsize` and skip
    oversize markers (>8 KiB) — log to stderr and unlink without
    reading. The wake intent still stands (PRESENCE is the signal)
    so the unlink + count-as-consumed posture is preserved.

    Pure-after-side-effect; never raises.
    """
    if not inbox_dir.exists():
        return 0
    try:
        markers = sorted(inbox_dir.glob("*.json"))
    except OSError:
        return 0
    consumed = 0
    for marker in markers:
        try:
            # Size-cap pre-check: 8 KiB is generous against the
            # canonical 7-field payload. Oversize markers are treated
            # as wake signals (delete + count) but the body is NOT
            # read — protects against parser-amplification on a
            # corrupted or hostile file.
            try:
                size = os.path.getsize(str(marker))
            except OSError:
                size = -1
            if size > _MAX_MARKER_BYTES:
                print(
                    f"wake_inbox_drain: oversize marker "
                    f"({size} bytes); skipping body read, unlinking",
                    file=sys.stderr,
                )
            else:
                # Read for forensic logging only — fail-conservative
                # on malformed JSON: still treat as a wake signal
                # (delete + count). A truncated / corrupted marker
                # file MEANS a teammate session attempted to write
                # and was interrupted; the wake intent stands.
                try:
                    marker.read_text(encoding="utf-8")
                except OSError:
                    pass
            os.unlink(str(marker))
            consumed += 1
        except OSError:
            # Couldn't unlink — leave the marker on disk; next drain
            # will retry. Do NOT count this marker as consumed so the
            # fallback can still fire if needed.
            continue
    return consumed


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


def _decide_and_emit(input_data: dict) -> None:
    """Run the drain + fallback decision tree and emit at most one
    _ARM_DIRECTIVE block.

    Decision order:
      1. Resolve team_name from session context. No team → suppressOutput.
      2. Resolve inbox_dir from team_name. Path-safety failure →
         suppressOutput.
      3. Lead-session guard. Non-lead session → suppressOutput
         (regardless of inbox state — teammate sessions never emit
         the Arm directive; the directive is lead-targeted).
      4. Drain markers. If drained count > 0 → emit Arm and return
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
    # glob and the count_active_tasks fallback. `is_lead_session`
    # itself reads team_config.json (one disk read on every prompt),
    # which is the per-prompt cost on the hot path.
    if not is_lead_session(input_data, team_name):
        print(_SUPPRESS_OUTPUT)
        return

    drained = _drain_markers(inbox_dir)
    if drained > 0:
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
