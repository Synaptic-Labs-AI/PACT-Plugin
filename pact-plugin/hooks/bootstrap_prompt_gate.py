#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_prompt_gate.py
Summary: UserPromptSubmit hook that injects a bootstrap-first instruction
         alongside every user message until the bootstrap-complete marker exists.
Used by: hooks.json UserPromptSubmit hook (no matcher — fires on every prompt)

Layer 2 of the four-layer bootstrap gate enforcement (#401). On each user
message, checks for the session-scoped bootstrap-complete marker file:
  - Marker exists → suppressOutput (zero tokens, sub-ms)
  - No marker + PACT team-lead session (is_lead) → inject additionalContext instructing bootstrap
  - Non-PACT session (no context file) → no-op passthrough
  - Non-lead / plain primary frame (not is_lead) → no-op passthrough
    (NOT a teammate: teammates have no UserPromptSubmit-fire path)

SACROSANCT (post-#662 module-load fail-closed retrofit): module-load
failures emit an advisory `additionalContext` block at exit 0 —
UserPromptSubmit cannot
DENY the prompt itself, so the strongest signal we can send is to surface
the load-failure to the LLM via additionalContext so the user is informed
and the orchestrator persona can react. Runtime exceptions in gate logic
remain fail-OPEN (suppressOutput) because injecting bootstrap-required
text on a hook-side bug would mislead a healthy session into rebooting.

Input: JSON from stdin with hook_event_name, session_id, prompt, etc.
Output: JSON with hookSpecificOutput.additionalContext (inject case)
        or {"suppressOutput": true} (fast path / passthrough)
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import os
import re
import sys
from typing import NoReturn


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed advisory for module-load failure.

    UserPromptSubmit cannot DENY the prompt; the strongest available signal
    is `additionalContext` injection. Uses ONLY stdlib (json, sys) so it
    remains functional even when every wrapped import below fails. Audit
    anchor: hookEventName must be present in any structured output.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"PACT bootstrap_prompt_gate {stage} failure — the hook "
                f"could not verify bootstrap state. {type(error).__name__}: "
                f"{error}. Until this is resolved, you should invoke "
                'Skill("PACT:bootstrap") before any code-editing or agent '
                "dispatch action; the companion `bootstrap_gate` PreToolUse "
                "will block those tools fail-closed."
            ),
        }
    }))
    print(
        f"Hook load error (bootstrap_prompt_gate / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ───────────────────────
try:
    from pathlib import Path

    import shared.pact_context as pact_context
    from bootstrap_gate import is_marker_set
    from shared import BOOTSTRAP_MARKER_NAME
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


_SUPPRESS_OUTPUT = json.dumps({
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"},
})

_BOOTSTRAP_INSTRUCTION_TEMPLATE = (
    "REQUIRED: Before responding to this message, invoke "
    'Skill("PACT:bootstrap"). Code-editing tools (Edit, Write) and agent '
    "dispatch (Agent) are mechanically blocked until bootstrap completes. "
    "This loads your operating instructions, governance policy, and "
    "workflow protocols."
    "{session_dir_hint}"
)

_SESSION_DIR_HINT = (
    "\n\nPACT_SESSION_DIR={session_dir}"
)

# Mirrors the Resume-line fallback regex in session_init's
# _extract_prev_session_dir — the established defensive parse for the
# session_resume.update_session_info managed block. Parity with
# claude_md_manager.resolve_project_claude_md_path's existing-file
# precedence is pinned by test (NOT by a runtime import — every new
# top-level import here widens the import-failure blast radius the
# bootstrap-resilience work exists to shrink).
_RESUME_LINE_RE = re.compile(r"- Resume:\s*`claude --resume\s+([0-9a-f-]+)`")

_STALENESS_WARNING_TEMPLATE = (
    "\n\nWARNING — stale session block: the project CLAUDE.md 'Current "
    "Session' block records session {recorded} but this session is "
    "{actual}. session_init likely failed at SessionStart this session "
    "(or the CLAUDE.md write failed). Do NOT trust the recorded Team/"
    "Session dir/Resume lines for THIS session; completing bootstrap "
    "will rewrite them."
)


def _detect_stale_session_block(input_data: dict) -> str | None:
    """Detect a stale 'Current Session' block in the project CLAUDE.md.

    When session_init crashes at SessionStart, the previous session's
    Resume/Team/Session-dir lines survive in CLAUDE.md and misdirect
    recovery. Compare the recorded Resume-line session_id against this
    frame's raw stdin session_id; on mismatch, return an advisory warning
    string for additionalContext composition. Returns None (no warning)
    when:

      1. stdin session_id is missing or invalid per the canonical
         _is_unknown_or_missing_session predicate (None/non-string/empty/
         whitespace-only/`unknown-*` sentinel/C0-control chars) — nothing
         trustworthy to compare, and an unvalidated stdin id must never be
         interpolated into the warning text
      2. CLAUDE_PROJECT_DIR is unset (cannot locate CLAUDE.md)
      3. neither CLAUDE.md location exists, or reading raises OSError or
         UnicodeDecodeError (worktrees: CLAUDE.md is gitignored/absent →
         silent skip; a non-UTF-8/corrupted CLAUDE.md → silent skip — this
         helper is ADVISORY, so its failure budget is "no warning", never
         "no bootstrap instruction": an uncaught raise here would propagate
         to main()'s fail-open and suppress the ENTIRE injection, primary
         instruction included)
      4. no Resume line matches the regex (tampered/garbage → no claim)
      5. recorded session_id equals this session's (healthy resume)

    Stdlib-only two-path read: .claude/CLAUDE.md preferred, legacy
    ./CLAUDE.md fallback — same existing-file precedence as
    resolve_project_claude_md_path (parity pinned by test). False
    positives: none in healthy flows — session_init rewrites the block
    before the first prompt on startup/clear, and resume keeps the same
    session_id.
    """
    raw_id = input_data.get("session_id")
    # Canonical validity predicate (shared with the heal gate and
    # session_init's persist/CLAUDE.md-write gates) — subsumes the plain
    # truthiness check and additionally rejects non-string, whitespace-only,
    # `unknown-*` sentinel, and C0-control-char ids, so `actual` below is
    # never an attacker-shaped or sentinel value interpolated into the
    # warning. The predicate is total for any input.
    if pact_context._is_unknown_or_missing_session(raw_id):
        return None
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None
    try:
        content = None
        for candidate in (
            Path(project_dir) / ".claude" / "CLAUDE.md",
            Path(project_dir) / "CLAUDE.md",
        ):
            if candidate.exists():
                content = candidate.read_text(encoding="utf-8")
                break
        if content is None:
            return None
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError (a ValueError, NOT an OSError) from read_text
        # on a non-UTF-8 CLAUDE.md — e.g. a latin-1 byte from a wrong-editor
        # save, or the partial/corrupted session_init write this detector
        # exists to flag. Must be swallowed HERE: this helper composes into
        # the load-bearing bootstrap instruction by concatenation, and an
        # escape to main()'s fail-open suppresses the whole injection.
        return None
    match = _RESUME_LINE_RE.search(content)
    if not match:
        return None
    recorded = match.group(1)
    actual = str(raw_id)
    if recorded != actual:
        return _STALENESS_WARNING_TEMPLATE.format(
            recorded=recorded, actual=actual
        )
    return None


def _check_bootstrap_needed(input_data: dict) -> str | None:
    """Determine whether a bootstrap instruction should be injected.

    Returns the additionalContext string to inject, or None if the gate
    should be a no-op (marker exists, non-PACT session, or a plain/non-lead
    primary frame — NOT a teammate; teammates never fire UserPromptSubmit).
    """
    # Initialize context (sets session-scoped path from input_data)
    pact_context.init(input_data)

    # Self-heal: re-create a MISSING context file (session_init crashed at
    # SessionStart) so this gate and downstream consumers can resolve the
    # session again. Total/never-raises; no-op unless lead frame + valid
    # session_id + file absent. Does NOT forge bootstrap completion — a
    # healed session still flows into the no-marker inject branch below.
    pact_context.heal_context_if_missing(input_data)

    # Fast path: check marker first (cheapest check, most common case)
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        # No session dir → non-PACT session or uninitialized context → no-op
        return None

    # Use the same safe-marker-check helper as the sibling
    # bootstrap_gate.py so both enforcement points share one safe-check
    # contract. The helper enforces leaf-symlink, ancestor-symlink, and
    # marker-content fingerprint defenses (post-#662).
    if is_marker_set(Path(session_dir)):
        # Bootstrap already done → suppress (zero tokens)
        return None

    # Lead-role gate (#878): only the team-lead drives the bootstrap ritual.
    # This is NOT a teammate discriminator: an Agent-spawned team teammate has
    # no UserPromptSubmit-fire path (it wakes via inbox/SendMessage, which is
    # not hookable), so this event never carries a teammate frame (empirically
    # confirmed by the discriminator audit). The guard ensures a plain /
    # non-PACT primary frame (agent_type absent → is_lead False) does not drive
    # bootstrap. Migrated from the negative `resolve_agent_name(...) != ""`
    # heuristic — which returned non-empty for BOTH lead spellings (Step-4
    # prefix-strip), so under tmux the lead itself took this non-lead bypass
    # branch — to the positive is_lead predicate keyed on the harness-set
    # agent_type directly.
    if not pact_context.is_lead(input_data):
        return None

    # Lead session, no marker → inject bootstrap instruction with session
    # dir, composed with the staleness advisory (or "") by concatenation.
    # Staleness runs ONLY here (lead + no-marker): the marker-set fast path
    # above keeps its zero-tokens/sub-ms contract (no per-prompt file read),
    # and a marker-set session has by definition completed bootstrap.
    return _BOOTSTRAP_INSTRUCTION_TEMPLATE.format(
        session_dir_hint=_SESSION_DIR_HINT.format(session_dir=session_dir)
    ) + (_detect_stale_session_block(input_data) or "")


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-open
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        instruction = _check_bootstrap_needed(input_data)
    except Exception:
        # Runtime exception in gate logic → fail-OPEN: injecting
        # bootstrap-required text on a hook-side bug would mislead a healthy
        # session. Module-load failures are handled separately (advisory) by
        # the module-load wrapper above.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if instruction:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": instruction,
            }
        }
        print(json.dumps(output))
    else:
        print(_SUPPRESS_OUTPUT)

    sys.exit(0)


if __name__ == "__main__":
    main()
