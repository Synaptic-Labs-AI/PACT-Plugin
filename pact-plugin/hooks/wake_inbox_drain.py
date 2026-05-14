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
- Drain path consumed → emit Arm; do NOT also run the fallback. Fallback
  is the LEAD-side unowned-dispatch recovery path; if markers were
  drained, the teammate-side signal already covered the surface and a
  second emit would be redundant (still benign under skill-body
  idempotency, but the single-emit shape is cleaner).
- Drain path empty → run the count_active_tasks fallback. Positive
  count → emit Arm. Zero count → suppressOutput.

Performance hygiene:
- Empty inbox AND non-lead session short-circuits to suppressOutput
  before any task-store I/O. Cost on every teammate prompt: one
  directory glob + one config.json read + one session_id compare.

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
            # Read for forensic logging only — fail-conservative on
            # malformed JSON: still treat as a wake signal (delete +
            # count). A truncated / corrupted marker file MEANS a
            # teammate session attempted to write and was interrupted;
            # the wake intent stands.
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
         (single-emit discipline; skip the fallback).
      5. Fallback: count_active_tasks(team_name) >= 1 → emit Arm.
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

    # Performance hygiene: empty-inbox + non-lead-session early-out.
    # The non-lead path is the hot common case (every teammate prompt
    # fires this hook); short-circuit before any further I/O. Note we
    # MUST still evaluate is_lead_session even on empty inbox for the
    # lead-side B-1 fallback path below.
    if not is_lead_session(input_data, team_name):
        print(_SUPPRESS_OUTPUT)
        return

    drained = _drain_markers(inbox_dir)
    if drained > 0:
        _emit_arm()
        return

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
