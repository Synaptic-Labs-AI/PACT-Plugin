#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/dispatch_gate.py
Summary: PreToolUse hook (matcher='Agent') enforcing PACT specialist
         dispatch-protocol invariants F1-F7, F14, F15, F21, F23, F26.
Used by: hooks.json PreToolUse matcher='Agent' (sibling of team_guard.py).

Closes #662 silent-failure surface: spawning pact-* specialists without
name/team_name, with malformed names, against unregistered subagent_types,
into the wrong team, before TaskCreate, with long inline missions.

Safety: F21 fail-closed on module-load failure AND on runtime gate-logic
exception (mirrors PR #660 ``_emit_load_failure_deny`` and the
bootstrap_gate analogue). hookEventName always emitted (#658 invariant).
DENY → exit 2 + permissionDecision; ALLOW → suppressOutput + exit 0;
WARN → additionalContext + exit 0 (advisory; runbook validates injection
empirically per architect §7(a) / tests/runbooks/662-dispatch-gate.md).

Cheapest-rule-first ordering with short-circuit on first non-ALLOW:
  ① SOLO_EXEMPT carve-out          ⑥ F5 session-team match (decision h)
  ② non-pact-* carve-out            ⑦ F14 uniqueness in team members
  ③ F1/F2 string-presence           ⑧ F6 task assigned
  ④ F3 length/NFKC/regex/reserved   ⑨ F7 prompt heuristic (WARN)
  ⑤ F15 plugin_root + F4 registry

F23: every gate decision (ALLOW/DENY/WARN) is journaled. F26: prompt
text is redacted at the journal-write boundary (sk-/xoxb-/ghp_/AKIA/JWT
patterns) so credentials accidentally pasted into a prompt never persist
to disk; the in-memory ``permissionDecisionReason`` keeps the verbatim
prompt-fragment for the user-facing error.

Configuration:
  ``PACT_DISPATCH_INLINE_MISSION_MODE`` env-var (default ``"warn"``)
  controls the F7 prompt-heuristic disposition (the heuristic that flags
  dispatchers inlining mission text into ``prompt=`` instead of using the
  canonical "check TaskList" form). Allowed values:
    ``"warn"``   advisory ``additionalContext`` (default)
    ``"deny"``   blocking deny — flip after the F22 counter-test in
                 ``tests/runbooks/662-dispatch-gate.md`` confirms
                 ``additionalContext`` is silently dropped under
                 PreToolUse
    ``"shadow"`` journal-only; F7 trigger is observable in the session
                 journal but does not WARN or DENY (calibration mode).
  Unknown values fall back to ``"warn"``. F1-F6/F14/F15 are unaffected.

Input: JSON from stdin (tool_name, tool_input, agent_id, etc.)
Output: stdout JSON per harness contract.
"""

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ─
import json
import sys
import os
from typing import NoReturn


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def _emit_load_failure_deny(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-closed deny for module-load or runtime gate-logic
    failure. Mirrors PR #660 ``merge_guard_pre._emit_load_failure_deny``
    and bootstrap_gate.py analogue. hookEventName MUST be present.
    """
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"PACT dispatch_gate {stage} failure — blocking for safety. "
                f"{type(error).__name__}: {error}. Check hook installation "
                "and shared module availability."
            ),
        }
    }))
    print(
        f"Hook load error (dispatch_gate / {stage}): {error}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── F21: fail-closed wrapper on cross-package imports ─────────────────────
try:
    import re
    import unicodedata
    from pathlib import Path

    import shared.pact_context as pact_context
    from shared.dispatch_helpers import (
        SOLO_EXEMPT,
        is_registered_pact_specialist,
        has_task_assigned,
    )
    from shared.session_journal import append_event, make_event
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# F3 name validation. Order: length cap → NFKC normalize → regex → reserved.
# NFKC defends against fullwidth/lookalike chars that pass naive regex.
NAME_REGEX = re.compile(r"^[a-z0-9-]+$")
NAME_MAX_LENGTH = 64

# Reserved tokens (per Task #25 description / security HANDOFF). Names
# that would collide with PACT routing literals or schema actor types.
# Recall pinned memory: ``"team-lead"`` is the canonical lead name AND
# routing literal — a teammate named ``team-lead`` would shadow message
# routing. ``lead`` / ``peer`` / ``user`` / ``external`` are
# ``KNOWN_RESOLVERS`` schema values. ``unknown`` / ``solo`` are
# semantic-reserved.
RESERVED_NAMES = frozenset({
    "team-lead",
    "lead",
    "user",
    "external",
    "peer",
    "unknown",
    "solo",
})

# F7 prompt heuristic. Long inline mission OR no TaskList reference
# suggests the dispatcher embedded the mission in the prompt instead of
# the task description (defeats the harvest pipeline).
PROMPT_MAX_LENGTH = 800
TASK_REFERENCE_PHRASES = (
    "TaskList",
    "task list",
    "tasks assigned",
    "check your tasks",
)

# Inline-mission mode (F7). Read at module-load from
# ``PACT_DISPATCH_INLINE_MISSION_MODE`` env-var. The internal Python
# identifier is named after the behavior the heuristic checks (whether the
# dispatcher inlined mission text into ``prompt=`` rather than using the
# canonical "check TaskList" form); the F7 reference in the comment is a
# back-pointer to the failure-mode index from #662, retained for traceability.
# Allowed values:
#   ``"warn"``   — emit additionalContext (advisory, default; behavior
#                  unchanged from initial Commit 2 implementation).
#   ``"deny"``   — promote to a blocking deny. Flip to this if the
#                  post-merge F22 counter-test confirms additionalContext
#                  is silently dropped under PreToolUse (architect §7(a),
#                  runbook 662-dispatch-gate.md §F7).
#   ``"shadow"`` — emit a journal event but neither WARN nor DENY
#                  (first-session safety net for calibration; the gate
#                  observes without intervening). DENY decisions from
#                  F1-F6/F14/F15 still fire normally; only F7 is muted.
# Unknown values fall back to ``"warn"`` so a typo never disables the
# gate's other rules. Default ``"warn"`` preserves Commit 2 behavior.
_ALLOWED_INLINE_MISSION_MODES = frozenset({"warn", "deny", "shadow"})
F7_MODE = os.environ.get("PACT_DISPATCH_INLINE_MISSION_MODE", "warn")
if F7_MODE not in _ALLOWED_INLINE_MISSION_MODES:
    F7_MODE = "warn"

# F26 redaction patterns. Applied to the journal-written prompt only;
# the in-memory ``permissionDecisionReason`` keeps the verbatim prompt
# for the dispatcher's debugging.
REDACTION_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xoxb-[A-Za-z0-9-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    # JWT shape: three base64url segments joined with dots.
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


# ─── helpers ───────────────────────────────────────────────────────────────

def _redact(prompt: str) -> str:
    """F26: scrub credential patterns BEFORE journal write.

    Applied at the journal-write boundary, not at gate-decision boundary
    — the user-facing ``permissionDecisionReason`` keeps the verbatim
    prompt fragment so the dispatcher can self-diagnose. Only the
    on-disk journal entry is redacted.
    """
    if not isinstance(prompt, str):
        return ""
    redacted = prompt
    for pat in REDACTION_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)
    return redacted


def _team_member_names(team_name: str) -> set[str]:
    """F14 helper. Read ``~/.claude/teams/{team_name}/config.json`` and
    return the set of currently-live member names. Tolerant: any error
    returns ``set()`` (no collision detected).

    Private to dispatch_gate (only F14 uses it). The architect §5
    contract intentionally did NOT include this in dispatch_helpers.py
    because task_lifecycle_gate has no need for the member roster.
    """
    cfg_path = Path.home() / ".claude" / "teams" / team_name / "config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    members = data.get("members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        return set()
    names: set[str] = set()
    for entry in members:
        if isinstance(entry, dict):
            n = entry.get("name")
            if isinstance(n, str) and n:
                names.add(n)
    return names


# ─── pure rule-eval composition (testable without stdin/stdout) ────────────

def evaluate_dispatch(tool_input: dict) -> tuple[str, str | None, str | None]:
    """Single composition function. Returns ``(decision, reason, f_row)``.

    decision ∈ {``"ALLOW"``, ``"DENY"``, ``"WARN"``}.
    reason: human-readable explanation (None for ALLOW).
    f_row: which F-row fired (e.g. ``"F1"``); None for ALLOW or carve-out.

    Cheapest-rule-first ordering with short-circuit on first non-ALLOW.
    Pure function — no stdin/stdout, no FS writes, no exceptions raised
    to caller. ALL exceptions escape to ``main()`` which routes them
    through ``_emit_load_failure_deny`` (F25 runtime fail-closed).
    """
    if not isinstance(tool_input, dict):
        tool_input = {}

    subagent_type = tool_input.get("subagent_type", "") or ""
    name = tool_input.get("name", "") or ""
    team_name = tool_input.get("team_name", "") or ""
    prompt = tool_input.get("prompt", "") or ""

    # ① Carve-outs — sub-microsecond. SOLO_EXEMPT covers research agents
    # (general-purpose / Explore / Plan) that legitimately spawn without
    # name/team_name per pinned feedback_direct_agent_calls.md.
    if subagent_type in SOLO_EXEMPT:
        return ("ALLOW", None, None)
    # ② Non-pact-* spawns are not this gate's business — fall through.
    if not isinstance(subagent_type, str) or not subagent_type.startswith("pact-"):
        return ("ALLOW", None, None)

    # ③ F1, F2 — string presence (mandatory protocol fields).
    if not isinstance(name, str) or not name:
        return ("DENY",
                "PACT dispatch_gate F1: pact-* specialist requires name=. "
                "Use Agent(subagent_type='pact-*', name='<role>', "
                "team_name='<session-team>', ...). See pact-orchestrator §11.",
                "F1")
    if not isinstance(team_name, str) or not team_name:
        return ("DENY",
                "PACT dispatch_gate F2: pact-* specialist requires team_name=. "
                "Use the team name listed in CLAUDE.md §Current Session.",
                "F2")

    # ④ F3 — name validation. Length cap FIRST (cheap), then NFKC
    # normalization (defends against fullwidth/lookalike unicode that
    # would otherwise pass the regex), then regex on the NORMALIZED form,
    # then reserved-token check on the normalized form.
    if len(name) > NAME_MAX_LENGTH:
        return ("DENY",
                f"PACT dispatch_gate F3: name length={len(name)} > "
                f"{NAME_MAX_LENGTH}. Names must be ≤{NAME_MAX_LENGTH} chars.",
                "F3")
    normalized_name = unicodedata.normalize("NFKC", name)
    if not NAME_REGEX.match(normalized_name):
        return ("DENY",
                f"PACT dispatch_gate F3: name={name!r} violates "
                r"^[a-z0-9-]+$ (after NFKC normalization). "
                "Names must be lowercase ASCII alphanumeric + hyphen.",
                "F3")
    if normalized_name in RESERVED_NAMES:
        return ("DENY",
                f"PACT dispatch_gate F3: name={name!r} is reserved "
                "(would collide with PACT routing literal or schema "
                "resolver type). Choose a unique role-descriptive name.",
                "F3")

    # ⑤ F15 — plugin agents/ presence (cheap stat). Caught BEFORE F4 so
    # a missing plugin install gets the more actionable "plugin broken"
    # message rather than "specialist not registered".
    plugin_root = pact_context.get_plugin_root()
    if not plugin_root or not (Path(plugin_root) / "agents").is_dir():
        return ("DENY",
                "PACT dispatch_gate F15: plugin agents/ directory "
                "unavailable. Plugin install may be broken; check "
                "pact-session-context.json plugin_root field.",
                "F15")
    # F4 — subagent_type registered. Empty registry (which would also
    # trigger F15 above) is fail-closed by is_registered_pact_specialist.
    if not is_registered_pact_specialist(subagent_type):
        return ("DENY",
                f"PACT dispatch_gate F4: subagent_type={subagent_type!r} "
                "is not a registered PACT specialist. See "
                "pact-plugin/agents/pact-*.md for the canonical list.",
                "F4")

    # ⑥ F5 — session-team match with empty-source fail-closed (decision h).
    # Adversary passing team_name='' would equal an empty session_team if
    # we didn't reject empty session_team upfront — F2 already caught
    # explicit empty team_name on the spawn-input side.
    session_team = pact_context.get_team_name()
    if not session_team:
        return ("DENY",
                "PACT dispatch_gate F5: session team_name unavailable. "
                "pact-session-context.json missing or unreadable; "
                "fail-closed. Re-run /PACT:bootstrap to restore session "
                "context.",
                "F5")
    if team_name.lower() != session_team:
        return ("DENY",
                f"PACT dispatch_gate F5: team_name={team_name!r} doesn't "
                f"match session team {session_team!r}. Use the team name "
                "listed in CLAUDE.md §Current Session.",
                "F5")

    # ⑦ F14 — uniqueness against live team members.
    members = _team_member_names(team_name)
    if name in members:
        return ("DENY",
                f"PACT dispatch_gate F14: name={name!r} is already a "
                f"live member of team {team_name!r}. Use a unique name "
                "(append a numeric suffix or role-descriptor variant).",
                "F14")

    # ⑧ F6 — TaskCreate before Agent spawn.
    if not has_task_assigned(team_name, name):
        return ("DENY",
                f"PACT dispatch_gate F6: no Task in team {team_name!r} "
                f"with owner={name!r}. TaskCreate(owner={name!r}) "
                "before Agent spawn so the teammate has work on arrival.",
                "F6")

    # ⑨ F7 — prompt heuristic. Mode controlled by
    # PACT_DISPATCH_INLINE_MISSION_MODE env-var (warn|deny|shadow; default
    # warn). Shadow is a calibration mode: F7 fires the journal event but
    # returns ALLOW so first-session operators can observe trigger
    # frequency without WARN-noise.
    if (len(prompt) > PROMPT_MAX_LENGTH
            or not any(phrase in prompt for phrase in TASK_REFERENCE_PHRASES)):
        msg = (f"PACT dispatch_gate F7: prompt length={len(prompt)} "
               f"(>{PROMPT_MAX_LENGTH}) or no TaskList reference. "
               "Mission belongs in the Task description, not the spawn "
               "prompt. WARN means STOP and re-dispatch correctly: put "
               "the mission in TaskCreate(description=...) and let the "
               "teammate read it via TaskList/TaskGet.")
        if F7_MODE == "deny":
            return ("DENY", msg, "F7")
        if F7_MODE == "shadow":
            # Journal sees F7 fired; caller treats as ALLOW (no advisory).
            return ("ALLOW", msg, "F7")
        return ("WARN", msg, "F7")

    return ("ALLOW", None, None)


# ─── main ──────────────────────────────────────────────────────────────────

def _journal_decision(decision: str, reason: str | None, f_row: str | None,
                       tool_input: dict) -> None:
    """F23: emit one journal event per gate decision. Best-effort sink —
    errors are swallowed so the gate's primary decision always stands.

    Note: ``"dispatch_decision"`` is not registered in
    ``_REQUIRED_FIELDS_BY_TYPE`` in shared/session_journal.py, so the
    schema validator passes via the unknown-type opt-in pass-through
    (validator L317-L320). If a future change registers this type with
    required fields, update both this call site AND the validator
    declaration in tandem. F26 redaction applied to the prompt fragment
    BEFORE the journal write so credentials never persist.
    """
    try:
        prompt = tool_input.get("prompt", "") if isinstance(tool_input, dict) else ""
        event = make_event(
            "dispatch_decision",
            decision=decision,
            f_row=f_row,
            subagent_type=tool_input.get("subagent_type") if isinstance(tool_input, dict) else None,
            name=tool_input.get("name") if isinstance(tool_input, dict) else None,
            team_name=tool_input.get("team_name") if isinstance(tool_input, dict) else None,
            reason=reason,
            prompt_redacted=_redact(prompt)[:1024],
        )
        append_event(event)
    except Exception:
        # Journal is best-effort; gate decision stands regardless.
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-OPEN (input-side failure is the harness's
        # domain; cannot DENY meaningfully without parsed input). Mirrors
        # bootstrap_gate.py and the other PreToolUse gates.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Agent":
        # Hook is registered under matcher='Agent' but defensive belt: if
        # something else routes here, no-op rather than misclassify.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    tool_input = input_data.get("tool_input", {}) or {}

    try:
        decision, reason, f_row = evaluate_dispatch(tool_input)
    except Exception as e:
        # F25 (runtime fail-closed): runtime exception in the rule logic
        # is the same defect class as #658 — must DENY, must include
        # hookEventName.
        _emit_load_failure_deny("runtime", e)

    _journal_decision(decision, reason, f_row, tool_input)

    if decision == "ALLOW":
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    if decision == "DENY":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        sys.exit(2)
    # WARN: emit additionalContext, exit 0 (advisory; per architect §7(a)
    # the empirical injection-vs-silent-drop is validated post-merge).
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": reason,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
