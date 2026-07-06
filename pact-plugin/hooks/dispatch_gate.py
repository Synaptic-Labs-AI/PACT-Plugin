#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/dispatch_gate.py
Summary: PreToolUse hook (matcher='Agent') validating PACT specialist
         spawns: required name, name regex/length/reserved tokens,
         registered specialist type, SSOT session-team resolution, member
         uniqueness, task assignment, and prompt heuristics. (#979: the
         caller team_name arg is platform-ignored — the session team is
         resolved solely from get_team_name(), never from the spawn arg.)
Used by: hooks.json PreToolUse matcher='Agent'.

Closes #662 silent-failure surface: spawning pact-* specialists without a
name, with malformed names, against unregistered subagent_types, before
TaskCreate, with long inline missions.

Safety: fail-closed on module-load failure AND on runtime gate-logic
exception (mirrors PR #660 ``_emit_load_failure_deny`` and the
bootstrap_gate analogue). hookEventName always emitted (#658 invariant).
DENY → exit 2 + permissionDecision; ALLOW → suppressOutput + exit 0;
WARN → additionalContext + exit 0 (advisory; runbook validates injection
empirically per architect §7(a) / tests/runbooks/662-dispatch-gate.md).

Cheapest-rule-first ordering with short-circuit on first non-ALLOW:
  ① SOLO_EXEMPT carve-out          ⑥ SSOT session-team resolve (#979)
  ② non-pact-* carve-out            ⑦ member-name uniqueness in team
  ③ name presence                   ⑧ task-assigned check
  ④ name length/NFKC/regex/reserved ⑨ prompt heuristic (WARN)
  ⑤ plugin agents/ + specialist registry

Every gate decision (ALLOW/DENY/WARN) is journaled. Prompt text is
redacted at the journal-write boundary (sk-/xoxb-/ghp_/AKIA/JWT
patterns) so credentials accidentally pasted into a prompt never persist
to disk; the in-memory ``permissionDecisionReason`` keeps the verbatim
prompt-fragment for the user-facing error.

Configuration:
  ``PACT_DISPATCH_INLINE_MISSION_MODE`` env-var (default ``"warn"``)
  controls the inline-mission heuristic disposition (the heuristic that
  flags dispatchers inlining mission text into ``prompt=`` instead of
  using the canonical "check TaskList" form). Allowed values:
    ``"warn"``   advisory ``additionalContext`` (default)
    ``"deny"``   blocking deny — flip after the matcher-fidelity
                 counter-test in ``tests/runbooks/662-dispatch-gate.md``
                 confirms ``additionalContext`` is silently dropped under
                 PreToolUse
    ``"shadow"`` journal-only; the trigger is observable in the session
                 journal but does not WARN or DENY (calibration mode).
  Unknown values fall back to ``"warn"``. The other rules are unaffected.

Input: JSON from stdin (tool_name, tool_input, agent_id, etc.)
Output: stdout JSON per harness contract.
"""

from __future__ import annotations

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


# ─── fail-closed wrapper on cross-package imports ──────────────────────────
try:
    import re
    import unicodedata
    from pathlib import Path

    import shared.pact_context as pact_context
    import shared.pact_config as pact_config
    from shared.dispatch_helpers import (
        SOLO_EXEMPT,
        is_registered_pact_specialist,
        has_task_assigned,
    )
    from shared.session_journal import append_event, make_event
    from shared.paths import get_claude_config_dir
    from shared.stale_session import detect_stale_session_block
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# Name validation. Order: length cap → NFKC normalize → regex → reserved.
# NFKC defends against fullwidth/lookalike chars that pass naive regex.
# The regex requires at least one alphanumeric and forbids leading or
# trailing hyphens, so degenerate names like "-", "--", "-foo", "foo-"
# are rejected. Internal hyphens are permitted; the single-character
# form must itself be alphanumeric.
NAME_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
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
# Audit: `secretary` / `pact-secretary` are NOT reserved here. The session
# secretary is canonically spawned with `name="secretary"` (see
# bootstrap_marker_writer._SECRETARY_NAME and commands/bootstrap.md
# Step 2), and the dispatch sites that re-assign housekeeping work to
# the secretary use `TaskUpdate(owner="secretary")` literally. Reserving
# the name would block the legitimate ritual.
#
# The previous reservation existed as a defense-in-depth name perimeter
# against a confused-deputy attack on the self-completion carve-out
# (when the carve-out was keyed on owner name). Post-#682 the
# task_lifecycle_gate carve-out keys on team-config `agentType`
# (member.agentType ∈ shared.intentional_wait.SELF_COMPLETE_EXEMPT_AGENT_TYPES,
# looked up via the harness-managed team config) — NOT on owner name.
# Owner-name spoofing alone therefore cannot bypass the
# lead-only-completion advisory; the agentType-keyed predicate is the
# load-bearing defense, and that defense is independent of which names
# RESERVED_NAMES holds. See:
#   - shared/intentional_wait.SELF_COMPLETE_EXEMPT_AGENT_TYPES
#   - shared/intentional_wait._is_exempt_agent_type
#   - shared/intentional_wait.is_self_complete_exempt (TRUST BOUNDARY block)

# Inline-mission heuristic. Long inline mission OR no TaskList reference
# suggests the dispatcher embedded the mission in the prompt instead of
# the task description (defeats the harvest pipeline).
#
# Audit: the 800-char threshold is duplicated as prose in
# `agents/pact-orchestrator.md` §Agent Teams Dispatch.
# The duplication is intentional — the agent-reader-primary axiom prefers
# inline values over cross-refs because LLM readers under token pressure
# don't follow cross-refs reliably. There is currently no source-side
# enforcement that the prose stays in sync; update both surfaces if
# changing this threshold.
PROMPT_MAX_LENGTH = 800
# Audit: this tuple is duplicated as prose in `agents/pact-orchestrator.md`
# §Agent Teams Dispatch (the spawn-prompt example commentary lists these
# accepted phrases). The duplication is intentional per the
# agent-reader-primary axiom (see PROMPT_MAX_LENGTH audit anchor above);
# update both surfaces together if changing the accepted phrases. Order
# does not matter — the heuristic uses `any(... in prompt ...)`.
TASK_REFERENCE_PHRASES = (
    "TaskList",
    "task list",
    "tasks assigned",
    "check your tasks",
)

# Inline-mission mode. Read at module-load from
# ``PACT_DISPATCH_INLINE_MISSION_MODE`` env-var. The internal Python
# identifier is named after the behavior the heuristic checks (whether the
# dispatcher inlined mission text into ``prompt=`` rather than using the
# canonical "check TaskList" form).
# Allowed values:
#   ``"warn"``   — emit additionalContext (advisory, default; behavior
#                  unchanged from initial Commit 2 implementation).
#   ``"deny"``   — promote to a blocking deny. Flip to this if the
#                  post-merge matcher-fidelity counter-test confirms
#                  additionalContext is silently dropped under PreToolUse
#                  (architect §7(a), runbook 662-dispatch-gate.md
#                  inline-mission section).
#   ``"shadow"`` — emit a journal event but neither WARN nor DENY
#                  (first-session safety net for calibration; the gate
#                  observes without intervening). DENY decisions from the
#                  other rules still fire normally; only the inline-mission
#                  heuristic is muted.
# Unknown values fall back to ``"warn"`` so a typo never disables the
# gate's other rules. Default ``"warn"`` preserves Commit 2 behavior.
# Resolution is delegated to the shared PACT_* resolver
# (``shared.pact_config.get_enum``), which applies the same ``.strip().lower()``
# normalization BEFORE the membership check (``"DENY"`` / ``" deny "`` → deny;
# ``""`` / bogus → warn) and owns the allowed-value set in its registry (SSOT).
# This replaces the read that was copy-pasted identically in
# handoff_ordering_gate.py's PACT_DISPATCH_VARIETY_MODE knob. get_enum is total
# (never raises) and is imported inside the fail-closed module-import block
# above, so a resolver-load failure routes through _emit_load_failure_deny.
INLINE_MISSION_MODE = pact_config.get_enum("PACT_DISPATCH_INLINE_MISSION_MODE")

# Credential redaction patterns. Applied to the journal-written prompt
# only; the in-memory ``permissionDecisionReason`` keeps the verbatim
# prompt for the dispatcher's debugging.
REDACTION_PATTERNS = (
    # Anthropic API keys, including the sk-ant-api03-... family. Matched
    # before the generic sk- prefix so the longer, more specific shape
    # is captured cleanly.
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    # OpenAI-style sk- keys.
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xoxb-[A-Za-z0-9-]{20,}"),
    # GitHub tokens: personal-access (ghp_), OAuth (gho_), user-server
    # (ghu_), server-to-server (ghs_), refresh (ghr_).
    re.compile(r"gh[oprsu]_[A-Za-z0-9]{20,}"),
    # AWS access key id.
    re.compile(r"AKIA[A-Z0-9]{16}"),
    # Google API keys (39-char total: AIza prefix + 35 chars).
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    # JWT shape: three base64url segments joined with dots.
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    # PEM private-key blocks (any flavor: RSA, EC, OPENSSH, plain
    # PRIVATE KEY, ENCRYPTED PRIVATE KEY). DOTALL so the body across
    # newlines is consumed by the redactor; non-greedy to stop at the
    # first END line.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


# ─── helpers ───────────────────────────────────────────────────────────────

def _redact(prompt: str) -> str:
    """Scrub credential patterns BEFORE journal write.

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
    """Member-roster reader. Read ``~/.claude/teams/{team_name}/config.json``
    and return the set of currently-live member names. Tolerant: any error
    returns ``set()`` (no collision detected).

    Private to dispatch_gate (only the uniqueness rule uses it). The
    architect §5 contract intentionally did NOT include this in
    dispatch_helpers.py because task_lifecycle_gate has no need for the
    member roster.

    ACCEPTED CONFIG-LESS GAP: under the config-less Desktop/SDK
    substrate there is no ``config.json``, so this returns ``set()`` and the
    name-uniqueness rule degrades to "no collision detected". This is an
    ACCEPTED degradation, not a deadlock: the rule fails OPEN (permits), so
    dispatch is not blocked; only the (advisory) duplicate-name check is
    silently inactive in that substrate.
    """
    cfg_path = get_claude_config_dir() / "teams" / team_name / "config.json"
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
    """Single composition function. Returns ``(decision, reason, rule)``.

    decision ∈ {``"ALLOW"``, ``"DENY"``, ``"WARN"``}.
    reason: human-readable explanation (None for ALLOW).
    rule: behavioral rule identifier (e.g. ``"name_required"``,
        ``"long_inline_mission"``); None for ALLOW or carve-out. Values
        describe what the rule checks.

    Cheapest-rule-first ordering with short-circuit on first non-ALLOW.
    Pure function — no stdin/stdout, no FS writes, no exceptions raised
    to caller. ALL exceptions escape to ``main()`` which routes them
    through ``_emit_load_failure_deny`` (runtime fail-closed).
    """
    if not isinstance(tool_input, dict):
        tool_input = {}

    subagent_type = tool_input.get("subagent_type", "") or ""
    name = tool_input.get("name", "") or ""
    prompt = tool_input.get("prompt", "") or ""
    # NOTE (#979): the spawn-arg team_name is intentionally NOT bound to a
    # local here. Claude Code v2.1.178+ ignores Agent(team_name=); the session
    # team is resolved SOLELY from the SSOT (get_team_name()) at ⑥/⑦/⑧ below,
    # so the caller arg is never used as a path component in this gate. It is
    # still recorded verbatim in the decision journal (read directly from
    # tool_input in _journal_decision) for diagnostics.

    # ① Carve-outs — sub-microsecond. SOLO_EXEMPT covers research agents
    # (general-purpose / Explore / Plan) that legitimately spawn without
    # name/team_name per pinned feedback_direct_agent_calls.md.
    if subagent_type in SOLO_EXEMPT:
        return ("ALLOW", None, None)
    # ② Non-pact-* spawns are not this gate's business — fall through.
    if not isinstance(subagent_type, str) or not subagent_type.startswith("pact-"):
        return ("ALLOW", None, None)

    # ③ Required string presence on name. (AC-2 / #979: the team_name-presence
    # check was dropped — Claude Code v2.1.178+ ignores Agent(team_name=), so
    # requiring or validating the caller-supplied team_name enforces a contract
    # the platform no longer honors. name-presence + ALL of rule ④ are KEPT:
    # name validation is a real injection/reserved-token defense, and the
    # session team is resolved from the SSOT at ⑥/⑦/⑧, never from the arg, so
    # the caller team_name needs no presence/normalization handling here.)
    if not isinstance(name, str) or not name:
        return ("DENY",
                "PACT dispatch_gate: name= parameter is required for "
                "pact-* specialist spawns. See orchestrator persona §11.",
                "name_required")

    # ④ Name validation. Length cap FIRST (cheap), then NFKC normalization
    # (defends against fullwidth/lookalike unicode that would otherwise
    # pass the regex), then regex on the NORMALIZED form, then
    # reserved-token check on the normalized form.
    if len(name) > NAME_MAX_LENGTH:
        return ("DENY",
                f"PACT dispatch_gate: name length {len(name)} exceeds "
                f"limit {NAME_MAX_LENGTH}.",
                "name_too_long")
    normalized_name = unicodedata.normalize("NFKC", name)
    if not NAME_REGEX.match(normalized_name):
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} must match "
                r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$ "
                "(lowercase alphanumerics, with hyphens permitted only "
                "between alphanumerics, checked after NFKC normalization).",
                "name_invalid_regex")
    if normalized_name in RESERVED_NAMES:
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} is in the "
                "reserved-token set (would collide with a PACT routing "
                "literal or schema resolver type). Choose a unique "
                "role-descriptive name.",
                "name_reserved_token")

    # ⑤a Context/plugin_root resolution. With the env fallback in
    # get_plugin_root() and the UserPromptSubmit self-heal, an empty
    # plugin_root requires context-file-missing AND env-missing — when it
    # does happen, name the REAL cause (the derived context path and the
    # session_init root cause) instead of misdirecting recovery at the
    # plugin install. Caught BEFORE the agents/ stat so the two causes
    # (context unresolvable vs plugin install broken) stay separable.
    plugin_root = pact_context.get_plugin_root()
    if not plugin_root:
        return ("DENY",
                "PACT dispatch_gate: plugin_root is unavailable. "
                + (pact_context.describe_context_failure()
                   or "pact-session-context.json has an empty plugin_root "
                      "and CLAUDE_PLUGIN_ROOT is not exported."),
                "plugin_root_unavailable")
    # ⑤b Plugin agents/ presence (cheap stat). Caught BEFORE the registry
    # check so a missing plugin install gets the more actionable
    # "plugin broken" message rather than "specialist not registered".
    if not (Path(plugin_root) / "agents").is_dir():
        return ("DENY",
                "PACT dispatch_gate: plugin agents/ directory is "
                "unavailable. Plugin install may be broken; check "
                "pact-session-context.json plugin_root field.",
                "plugin_agents_missing")
    # subagent_type registered in the agent registry. Empty registry
    # (which would also trigger the plugin_agents_missing rule above) is
    # fail-closed by is_registered_pact_specialist.
    if not is_registered_pact_specialist(subagent_type):
        return ("DENY",
                f"PACT dispatch_gate: subagent_type {subagent_type!r} "
                "is not a registered PACT specialist (no matching "
                "agents/pact-*.md).",
                "specialist_not_registered")

    # ⑥ Session-team resolution from the SSOT, with empty-source fail-closed.
    # AC-1 / #979: the prior team_name-arg equality check was DROPPED. Claude
    # Code v2.1.178+ ignores Agent(team_name=), so the session team is resolved
    # SOLELY from get_team_name() (the persisted SSOT that generate_team_name
    # mints as "session-<id8>"). An equality gate against the platform-ignored
    # arg would DENY a legitimately-dispatched teammate. ⑦/⑧ below resolve
    # their team-dir reads against session_team, NEVER the caller arg, so the
    # spawn-arg team_name is not a path component anywhere in this gate. The
    # empty-session_team fail-closed is RETAINED because ⑦/⑧ structurally
    # depend on session_team being a non-empty path segment.
    session_team = pact_context.get_team_name()
    if not session_team:
        message = ("PACT dispatch_gate: session team_name is unavailable "
                   "(pact-session-context.json missing or unreadable). "
                   "Re-run /PACT:bootstrap to restore session context.")
        # Append the shared context diagnosis (non-empty only when the
        # context file is underivable/absent) so the deny names the real
        # root cause instead of leaving recovery to guesswork.
        diagnosis = pact_context.describe_context_failure()
        if diagnosis:
            message += " " + diagnosis
        return ("DENY", message, "team_name_unavailable")

    # ⑦ Name uniqueness against live team members (resolved via the SSOT
    # session team, not the platform-ignored spawn arg).
    members = _team_member_names(session_team)
    if name in members:
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} is already a live "
                f"member of team {session_team!r}. Use a unique name "
                "(append a numeric suffix or role-descriptor variant).",
                "name_not_unique")

    # ⑧ Task assignment — TaskCreate must precede Agent spawn so the
    # teammate has work on arrival (resolved via the SSOT session team).
    if not has_task_assigned(session_team, name):
        return ("DENY",
                f"PACT dispatch_gate: no Task assigned to owner={name!r} "
                f"in team {session_team!r}. Create Task A (teachback) + "
                "Task B (work) before spawn so the teammate has work on "
                "arrival.",
                "no_task_assigned")

    # ⑨ Inline-mission heuristic. Mode controlled by
    # PACT_DISPATCH_INLINE_MISSION_MODE env-var (warn|deny|shadow; default
    # warn). Shadow is a calibration mode: the rule fires the journal
    # event but returns ALLOW so first-session operators can observe
    # trigger frequency without WARN-noise.
    if (len(prompt) > PROMPT_MAX_LENGTH
            or not any(phrase in prompt for phrase in TASK_REFERENCE_PHRASES)):
        msg = (f"PACT dispatch_gate: prompt is long ({len(prompt)} "
               f"chars, threshold {PROMPT_MAX_LENGTH}) or lacks a "
               "TaskList reference. Mission belongs in the Task "
               "description, not the spawn prompt. WARN means STOP and "
               "re-dispatch correctly: put the mission in "
               "TaskCreate(description=...) and let the teammate read "
               "it via TaskList/TaskGet. See orchestrator persona §11.")
        if INLINE_MISSION_MODE == "deny":
            return ("DENY", msg, "long_inline_mission")
        if INLINE_MISSION_MODE == "shadow":
            # Journal sees the rule fired; caller treats as ALLOW (no advisory).
            return ("ALLOW", msg, "long_inline_mission")
        return ("WARN", msg, "long_inline_mission")

    return ("ALLOW", None, None)


# ─── main ──────────────────────────────────────────────────────────────────

def _journal_decision(decision: str, reason: str | None, rule: str | None,
                       tool_input: dict) -> None:
    """Emit one journal event per gate decision. Best-effort sink —
    errors are swallowed so the gate's primary decision always stands.

    The ``rule`` field carries a behavioral identifier (e.g.
    ``"name_required"``, ``"long_inline_mission"``).

    Note: ``"dispatch_decision"`` is not registered in
    ``_REQUIRED_FIELDS_BY_TYPE`` in shared/session_journal.py, so the
    schema validator passes via the unknown-type opt-in pass-through
    (validator L317-L320). If a future change registers this type with
    required fields, update both this call site AND the validator
    declaration in tandem. Credential redaction applied to the prompt
    fragment BEFORE the journal write so credentials never persist.
    """
    try:
        prompt = tool_input.get("prompt", "") if isinstance(tool_input, dict) else ""
        event = make_event(
            "dispatch_decision",
            decision=decision,
            rule=rule,
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


# Deny rules whose root cause can be a stale-team/store mismatch after a
# Claude Code restart/fork (the persisted team_name/session_id diverge from
# the live platform team, so this gate resolves an orphaned task store while
# Task* tools write the live one). Both surface as misleading denials —
# ``team_name_unavailable`` (rule ⑥) and ``no_task_assigned`` (rule ⑧) — that
# never name the real cause. Other deny rules (name validation, plugin-install,
# registry) are NOT restart-symptoms, so they are deliberately excluded: a
# stale-session note on them would misdirect recovery.
_STALE_DIAGNOSABLE_RULES = frozenset({"team_name_unavailable", "no_task_assigned"})

# Actionable re-align guidance appended after the shared detector's stale-block
# warning. The detector names the live-vs-recorded session_id mismatch; this
# adds the dispatch-specific recovery (the gate read a different task store
# than the live session's).
_STALE_REALIGN_HINT = (
    " This dispatch denial is likely a STALE-TEAM/STORE MISMATCH, not a "
    "genuinely missing task: after a Claude Code restart/fork the platform "
    "minted a new team for the live session while PACT's persisted team_name "
    "went stale, so this gate read an orphaned task store. To re-align: update "
    "the `team_name` in this session's pact-session-context.json (and the "
    "project CLAUDE.md '- Team:' line) to the LIVE platform team, then "
    "re-dispatch. Completing /PACT:bootstrap also rewrites those records."
)


def _augment_deny_with_stale_diagnosis(
    rule: str | None, message: str, input_data: dict,
) -> str:
    """Return ``message`` augmented with a stale-team/store self-diagnosis when
    a restart-induced session mismatch is detected, else ``message`` UNCHANGED.

    MESSAGE-ONLY: this never alters the gate DECISION — it is called only after
    ``evaluate_dispatch`` has already returned DENY, and only rewrites the
    user-facing ``permissionDecisionReason`` text. The deny still fires on
    exactly the same inputs.

    NEVER RAISES (the single named place the detection call is wrapped — this
    is NEW code on a path that runs in EVERY consumer session). Any exception
    from the detector (unreadable/absent CLAUDE.md, missing keys, a tmux frame
    with no recorded block, an import-time surprise) falls back to the ORIGINAL
    ``message``. A thrown exception must NEVER break dispatch; a self-diagnosis
    is strictly a nicety on top of the already-correct deny.

    Gating:
      * Only the restart-symptom rules in ``_STALE_DIAGNOSABLE_RULES`` are
        augmented; other deny rules are returned verbatim (a stale-session note
        on a name-validation deny would misdirect recovery).
      * UN-GATED from the bootstrap-marker fast path: detection runs regardless
        of marker presence. On a restart the marker can be present but stamped
        for the STALE team, so a marker-gated check would miss exactly the case
        this diagnoses.
      * When the shared detector returns None (no mismatch — the healthy case,
        or CLAUDE.md absent as in worktrees), the ORIGINAL message is preserved
        verbatim. The augmentation is purely additive on a detected mismatch.
    """
    try:
        if rule not in _STALE_DIAGNOSABLE_RULES:
            return message
        if not isinstance(input_data, dict):
            return message
        stale_block = detect_stale_session_block(input_data)
        if not stale_block:
            return message
        return message + stale_block + _STALE_REALIGN_HINT
    except Exception:
        # Fail-safe: any error in the diagnosis path → original deny message.
        # The deny itself is unaffected; only the optional self-diagnosis is
        # dropped. NEVER re-raise out of the dispatch path.
        return message


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
        decision, reason, rule = evaluate_dispatch(tool_input)
    except Exception as e:
        # Runtime fail-closed: a runtime exception in the rule logic is
        # the same defect class as #658 — must DENY, must include
        # hookEventName.
        _emit_load_failure_deny("runtime", e)

    _journal_decision(decision, reason, rule, tool_input)

    if decision == "ALLOW":
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    if decision == "DENY":
        # MESSAGE-ONLY self-diagnosis: on a restart-symptom deny rule, append a
        # stale-team/store mismatch explanation + re-align steps when detected.
        # Journaling above recorded the canonical (un-augmented) reason; this
        # augments ONLY the user-facing message and never the decision. The
        # helper is never-raises — on any error the original reason stands.
        deny_message = _augment_deny_with_stale_diagnosis(rule, reason, input_data)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_message,
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
