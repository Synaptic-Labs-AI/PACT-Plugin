#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/bootstrap_prompt_gate.py
Summary: UserPromptSubmit hook that injects a bootstrap-first instruction
         alongside every user message until the bootstrap-complete marker exists.
Used by: hooks.json UserPromptSubmit hook (no matcher — fires on every prompt)

Layer 2 of the four-layer bootstrap gate enforcement (#401). On each user
message, checks for the session-scoped bootstrap-complete marker file:
  - Marker exists → suppressOutput (zero tokens, sub-ms)
  - No marker + PACT team-lead session (is_lead) → inject additionalContext instructing bootstrap.
    When the session journal has no session_start event (session_init did not
    record this lead), the instruction is prefixed with a lead note, and the
    lead is recorded here instead: see _record_unrecorded_lead
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
import sys
from typing import NoReturn


def _safe_error_detail(error: BaseException) -> str:
    """Return ``"<TypeName>: <message>"`` for an exception, NEVER raising.

    A hostile exception whose ``__str__`` / ``__repr__`` raises (or a type
    whose ``__name__`` access raises) must not make the load-failure advisory
    itself raise while composing its message — that would defeat the
    fail-closed advisory's whole purpose. Each part is computed behind its own
    guard with a safe placeholder. stdlib-only (no wrapped imports) so it holds
    even when the module-load failure that triggered the advisory broke every
    wrapped import.
    """
    try:
        type_name = type(error).__name__
    except BaseException:  # noqa: BLE001 — hostile __name__; never propagate
        type_name = "UnprintableError"
    try:
        message = str(error)
    except BaseException:  # noqa: BLE001 — hostile __str__; never propagate
        message = "<error message unavailable: str(error) raised>"
    return f"{type_name}: {message}"


def _emit_load_failure_advisory(stage: str, error: BaseException) -> NoReturn:
    """Emit fail-closed advisory for module-load failure.

    UserPromptSubmit cannot DENY the prompt; the strongest available signal
    is `additionalContext` injection. Uses ONLY stdlib (json, sys) so it
    remains functional even when every wrapped import below fails. Audit
    anchor: hookEventName must be present in any structured output. The error
    detail is composed via _safe_error_detail so a hostile exception whose
    __str__ raises cannot make this advisory raise while emitting.
    """
    error_detail = _safe_error_detail(error)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"PACT bootstrap_prompt_gate {stage} failure — the hook "
                f"could not verify bootstrap state. {error_detail}. "
                f"Until this is resolved, you should invoke "
                'Skill("PACT:bootstrap") before any code-editing or agent '
                "dispatch action; the companion `bootstrap_gate` PreToolUse "
                "will block those tools fail-closed."
            ),
        }
    }))
    print(
        f"Hook load error (bootstrap_prompt_gate / {stage}): {error_detail}",
        file=sys.stderr,
    )
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ───────────────────────
try:
    from pathlib import Path

    import shared.pact_context as pact_context
    from bootstrap_gate import is_marker_set
    # Stale-session detector moved to the shared/ leaf (single SSOT, also
    # consumed by dispatch_gate's deny-message self-diagnosis). Re-bound to the
    # historical module-private name so this module's existing call site and
    # the test suite that imports/monkeypatches
    # `bootstrap_prompt_gate._detect_stale_session_block` stay behavior-identical.
    from shared.stale_session import (
        detect_stale_session_block as _detect_stale_session_block,
    )
    from shared.session_journal import read_last_event_from
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

# Prepended for a lead whose session journal has no session_start event.
# session_init writes that event only for a frame it treats as the lead, so a
# lead without one either was not recognised at SessionStart or did not reach
# the journal write. Not recognised: a fork without `--agent`, or a lead resumed
# without `--agent` whose own session dir no longer holds its context file
# (reaped after the TTL, or the project moved); each got the no-role notice.
# Did not reach the write: session_init raised, had no session id, or got input
# that did not parse; each got the ladder. The note is true in every case: its
# second sentence speaks only of a notice, so it is vacuous where none was
# given. Keyed on the journal, not on the heal's return: the marker writer heals
# the same file in parallel and can win that race.
_NOT_TREATED_AS_LEAD_NOTE = (
    "This session is the PACT team-lead. Any startup notice saying it has no "
    "recognized agent role, or that PACT cannot dispatch specialist agents "
    "in this session, does not apply.\n\n"
)

# `_detect_stale_session_block` (and its `_RESUME_LINE_RE` /
# `_STALENESS_WARNING_TEMPLATE` constants) moved to shared/stale_session.py —
# the single SSOT now also consumed by dispatch_gate. The historical
# module-private name is re-bound at import time above so this module's call
# site and tests are behavior-identical.


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
    # dir. Both branches run ONLY here (lead + no-marker): the marker-set fast
    # path above keeps its zero-tokens/sub-ms contract (no per-prompt file
    # read), and a marker-set session has by definition completed bootstrap.
    instruction = _BOOTSTRAP_INSTRUCTION_TEMPLATE.format(
        session_dir_hint=_SESSION_DIR_HINT.format(session_dir=session_dir)
    )
    if read_last_event_from(session_dir, "session_start") is not None:
        # Recorded by session_init (or by an earlier prompt here): append the
        # staleness advisory (or "").
        return instruction + (_detect_stale_session_block(input_data) or "")
    # Not recorded: session_init's block, journal anchor, worktree record and
    # session values are missing or name another session, so record them now.
    # No staleness advisory: the block is replaced below.
    return (
        _NOT_TREATED_AS_LEAD_NOTE
        + instruction
        + _record_unrecorded_lead(input_data, session_dir)
    )


def _record_unrecorded_lead(input_data: dict, session_dir: str) -> str:
    """Record a lead session_init did not record; return what follows the
    instruction.

    Reached on a lead prompt with no bootstrap marker and no session_start in
    the journal. The session_start written here closes that branch, so this
    runs once per session. In order:

    1. If the project CLAUDE.md holds a Current Session block (both markers),
       read it. When the block names another session, read that session's
       pause or refresh claim. Then replace the block with this session's
       values (every field can be stale: the session id, the team, or the
       Session dir of a project that moved). A file with no block, and a
       missing file, are left alone: this never creates or migrates CLAUDE.md.
    2. Record the worktree identity, as session_init does for a lead.
    3. Append session_start (source "prompt") and, when a claim was read,
       session_resumption_surfaced. Both follow every read above.
       bootstrap_marker_writer leaves this lead's block alone until it sees a
       session_start, so whichever hook runs first, the old block is read
       before anything replaces it.

    Returns the session-value sentence, followed by the claim. A fork's
    transcript carries its parent's session values, so the sentence says that
    these replace them. Never raises: on any error it returns "", and the note
    and instruction still go out.
    """
    if pact_context._is_unknown_or_missing_session(input_data.get("session_id")):
        return ""
    try:
        # Imported here: this branch runs once per session, and the modules
        # below are not needed on any other prompt.
        import os

        from shared.claude_md_manager import (
            SESSION_END_MARKER,
            SESSION_START_MARKER,
            resolve_project_claude_md_path,
        )
        from shared.project_scope import _record_worktree_identity
        from shared.session_journal import append_event, make_event
        from shared.session_resume import (
            RESUMPTION_MARKER_MISSING_DIRECTIVE,
            _extract_prev_session_dir,
            check_resume_state,
            format_session_substitutions,
            update_session_info,
        )
        from shared.stale_session import _RESUME_LINE_RE

        session_id = str(input_data["session_id"])
        team = pact_context.get_team_name()
        plugin_root = pact_context.get_plugin_root()
        # update_session_info writes the file CLAUDE_PROJECT_DIR names, so the
        # block is looked for there, through the same resolver.
        env_project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        project_dir = env_project_dir or os.getcwd()

        claim = None
        if env_project_dir:
            claude_md, source = resolve_project_claude_md_path(env_project_dir)
            content = (
                "" if source == "new_default"
                else claude_md.read_text(encoding="utf-8")
            )
            if SESSION_START_MARKER in content and SESSION_END_MARKER in content:
                recorded = _RESUME_LINE_RE.search(content)
                if recorded is None or recorded.group(1) != session_id:
                    claim = check_resume_state(
                        _extract_prev_session_dir(env_project_dir)
                    )
                update_session_info(session_id, team, session_dir, plugin_root)
        _record_worktree_identity(session_id, project_dir)
        append_event(
            make_event(
                "session_start",
                team=team,
                session_id=session_id,
                project_dir=project_dir,
                worktree="",
                source="prompt",
            ),
            session_dir=session_dir,
        )
        parts = [
            format_session_substitutions(team, session_dir, plugin_root)
            + " These replace any session values earlier in this conversation."
        ]
        if claim:
            parts.append(claim)
            if not append_event(
                make_event("session_resumption_surfaced"), session_dir=session_dir
            ):
                parts.append(RESUMPTION_MARKER_MISSING_DIRECTIVE)
        return "\n\n" + "\n\n".join(parts)
    except Exception as e:  # noqa: BLE001 — never block the instruction
        print(
            f"bootstrap_prompt_gate: could not record this lead session: {e}",
            file=sys.stderr,
        )
        return ""


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
