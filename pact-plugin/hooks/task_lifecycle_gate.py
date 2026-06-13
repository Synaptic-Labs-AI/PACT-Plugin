#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/task_lifecycle_gate.py
Summary: PostToolUse hook (matcher='TaskCreate|TaskUpdate') enforcing PACT
         lifecycle invariants. Cannot DENY (post-action); emits structural
         advisory via additionalContext, plus a metadata writeback for
         self-completion violations.
Used by: hooks.json PostToolUse matcher='TaskCreate|TaskUpdate' (per the
         unified Task-mutating-tool matcher convention shared with
         agent_handoff_emitter).

Self-completion writeback recursion mitigation: the metadata writeback marks
metadata.gate_writeback=true. The gate's first check skips on this marker
so the gate's own write does not re-trigger the self-completion advisory
on itself.

Safety: fail-closed-as-advisory on module-load failure (mirrors the
bootstrap_gate fail-closed-as-deny pattern, adapted for PostToolUse —
cannot DENY, emits advisory + exit 0).
hookEventName always emitted on every output path (per the
hookSpecificOutput schema-rejection defense — missing hookEventName
triggers silent platform-layer rejection).

Rule coverage:
  - teachback_addblocks_missing — Teachback Task owner-wiring
    TaskUpdate landed without addBlocks=[<work_task_id>]. Fires at the
    canonical Step-3 wiring boundary (lead sets owner on a teachback
    Task) rather than at TaskCreate — the historical TaskCreate-time
    check was structurally unsatisfiable because the work-task id did
    not exist yet at TaskCreate(A).
    Fire condition (4-clause AND): subject is teachback-shaped AND
    tool_input.owner is set AND tool_input.addBlocks is absent/empty
    AND task_a.blocks is empty (benign late-wiring guard).
  - work_addblockedby_missing — pact-* work Task created without
    addBlockedBy=[<teachback_id>]
  - self_completion — Teammate self-completed a Task without carve-out
    → advisory + completion_disputed writeback
  - teachback_submit_missing — Teachback Task completed without
    metadata.teachback_submit payload
  - teachback_submit_schema_invalid — metadata.teachback_submit present
    but malformed against the 5-field canonical schema (disjoint with
    teachback_submit_missing)
  - reasoning_reconstruction_missing_at_required_band — Teachback
    submitted at REQUIRED band (Task B variety.total >= 11) without
    reasoning_reconstruction
  - reasoning_reconstruction_band_unresolvable — band traversal failed
    (missing blocks, missing Task B, missing variety, or variety present
    but no resolvable total); fail-open advisory documents the gap without
    blocking lifecycle
  - variety_missing_on_dispatch_task — pact-* work Task created without
    metadata.variety, with malformed per-dimension rationales, OR with no
    resolvable variety total
  - variety_acknowledgment_missing — Teachback submitted without
    variety_acknowledgment field (D10 teammate verification)
  - variety_acknowledgment_schema_invalid_at_write_time — Teachback
    submitted with variety_acknowledgment present but malformed
    (STRING instead of OBJECT; invalid enum; missing concern)
  - reasoning_reconstruction_in_handoff — reasoning_reconstruction
    placed inside metadata.handoff (wrong slot — belongs on
    teachback_submit)
  - reasoning_reconstruction_subkeys_invalid — reasoning_reconstruction
    present on teachback_submit but the 3 sub-keys are wrong-shape
    (non-canonical names, missing keys, or empty/non-string values)
  - intentional_wait_nested_in_teachback_submit — intentional_wait
    placed inside teachback_submit instead of as a sibling top-level
    metadata key (Step 3 of the canonical 3-step shape)
  - Every gate decision emits a session_journal lifecycle_decision event
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_advisory BEFORE wrapped imports) ─
import json
import os
import sys
from typing import NoReturn


_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


# Cap on exception text interpolated into context-bound output (warning
# strings reaching Claude's context and the user banner). Exception
# messages can embed attacker-influencable content (file contents, paths,
# crafted payloads in tracebacks) — bound + sanitize before interpolation.
# The stderr diagnostic line keeps the full text (debug channel).
_ERROR_TEXT_MAX = 200

# Cap on the crash-path stdin read in _emit_gate_health_event. Generous:
# real PostToolUse frames embed tool_response payloads (file contents,
# command output) and stay well under this; anything larger is not a
# realistic hook frame and must not be slurped unbounded by a best-effort
# emitter. An over-cap frame truncates mid-JSON → JSONDecodeError → the
# guard's stderr disposition (marker-only outcome, never a raise).
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


def _bounded_error_text(error: BaseException) -> str:
    """Sanitized, length-bounded rendering of an exception for embedding in
    context-bound warning text: control/non-printable characters become
    spaces, and the result is truncated to _ERROR_TEXT_MAX chars with an
    explicit marker. Full text still goes to stderr at the call site.

    Total over hostile exceptions: rendering the message runs the
    exception's own __str__ (arbitrary code that can itself raise) — fall
    back to the type name, which renders for any exception class."""
    try:
        text = f"{type(error).__name__}: {error}"
    except BaseException:  # noqa: BLE001 — hostile __str__ must not escape the renderer
        text = f"{type(error).__name__}: <exception str() raised>"
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    if len(text) > _ERROR_TEXT_MAX:
        text = text[:_ERROR_TEXT_MAX] + "...[truncated]"
    return text


def _emit_gate_health_event(
    stage: str, error_text: str, input_data: dict | None
) -> None:
    """Best-effort durable journal emit for a crash-path gate_health event.

    Lazy-imports pact_context + session_journal so it stays functional on
    the import-stage crash path (works unless the breakage hits those very
    modules or shared/__init__ — then the lazy import raises into the guard
    below and the stdout marker remains the only record; prep §2.1 table).
    On the import-stage path stdin is still unconsumed: read (capped at
    _STDIN_READ_MAX) + init here.
    Never raises; never load-bearing (tmux teammate fires self-drop, #877).
    """
    try:
        import shared.pact_context as _lazy_pact_context
        import shared.session_journal as _lazy_session_journal

        if input_data is None:
            input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
        if not isinstance(input_data, dict):
            return
        if not _lazy_pact_context.is_initialized():
            _lazy_pact_context.init(input_data)
        # tool_name is attacker-set stdin on the import-stage path (main()'s
        # TaskCreate/TaskUpdate allowlist short-circuit never ran) — apply
        # the same sanitize+bound discipline as the error text before it
        # reaches the durable journal.
        tool_name = input_data.get("tool_name", "")
        if not isinstance(tool_name, str):
            tool_name = f"<non-str {type(tool_name).__name__}>"
        tool_name = "".join(ch if ch.isprintable() else " " for ch in tool_name)
        if len(tool_name) > _ERROR_TEXT_MAX:
            tool_name = tool_name[:_ERROR_TEXT_MAX] + "...[truncated]"
        event = _lazy_session_journal.make_event(
            "gate_health",
            hook="task_lifecycle_gate",
            status="failed",
            stage=stage,
            error=error_text,
            tool_name=tool_name,
        )
        written = _lazy_session_journal.append_event(event)
        if not written:
            print(
                "task_lifecycle_gate: gate_health journal emit skipped "
                "(append_event returned False)",
                file=sys.stderr,
            )
    except BaseException:  # noqa: BLE001 — the crash handler must not crash:
        # mirror the import gauntlet's breadth (except BaseException at the
        # wrapped-import block). The lazy imports execute arbitrary module
        # bodies — a module-level sys.exit or KeyboardInterrupt surfacing
        # here would exit nonzero AFTER the floor marker printed, and stdout
        # JSON is only honored on exit 0.
        print(
            "task_lifecycle_gate: gate_health journal emit unavailable "
            "(late import or init failed)",
            file=sys.stderr,
        )


def _emit_load_failure_advisory(
    stage: str, error: BaseException, input_data: dict | None = None
) -> NoReturn:
    """Stdlib-only fail-advisory (PostToolUse cannot DENY).

    Mirrors bootstrap_gate._emit_load_failure_deny but for PostToolUse —
    advisory output + exit 0 since deny is not a valid PostToolUse verdict.
    Uses ONLY stdlib (json, sys) so it remains functional even when every
    wrapped import below fails.

    Crash-path health surfacing: a top-level pactGateHealth key (stripped by
    the platform's non-strict output schema; assertable on RAW stdout only)
    plus a systemMessage mirror of the advisory, plus a best-effort
    gate_health journal event. Error text is bounded/sanitized for all
    context-bound output; the stderr diagnostic keeps the full text.
    """
    # Thin call-site fallback (defense-in-depth over the now-total helper):
    # the FLOOR below must print no matter what the renderer does. The type
    # name alone is renderable for any exception class.
    try:
        error_text = _bounded_error_text(error)
    except BaseException:  # noqa: BLE001 — floor must survive any renderer defect
        error_text = type(error).__name__
    advisory = (
        f"PACT task_lifecycle_gate {stage} failure — lifecycle "
        f"rule enforcement skipped this turn. "
        f"{error_text}. Investigate hook "
        "installation and shared module availability."
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": advisory,
        },
        "systemMessage": advisory,
        "pactGateHealth": {
            "v": 1,
            "hook": "task_lifecycle_gate",
            "status": "failed",
            "stage": stage,
            "error": error_text,
        },
    }
    print(json.dumps(output))                                   # floor FIRST
    # Guarded full-text rendering: this line runs AFTER the floor printed,
    # but an unguarded str(error) raising here would exit nonzero — and
    # stdout JSON is only honored on exit 0, voiding the floor retroactively.
    try:
        error_full = f"{error}"
    except BaseException:  # noqa: BLE001 — hostile __str__; keep the exit-0 path
        error_full = "<exception str() raised>"
    print(
        f"Hook load error (task_lifecycle_gate / {stage}): {error_full}",  # full text
        file=sys.stderr,
    )
    _emit_gate_health_event(stage, error_text, input_data)      # bonus LAST
    sys.exit(0)


# ─── fail-closed wrapper around cross-package imports ─────────────────────────
try:
    import re
    from pathlib import Path

    import shared.pact_context as pact_context
    from shared.paths import get_claude_config_dir
    from shared.agent_handoff_marker import (
        already_emitted,
        is_signal_task,
        occupant_hash,
        sanitize_path_component,
        unclaim,
    )
    from shared.dispatch_helpers import trustworthy_actor_name
    from shared.intentional_wait import is_self_complete_exempt, is_teachback_exempt
    from shared.session_journal import append_event, get_journal_path, make_event
    from shared.task_utils import read_task_json
    from shared.teachback_schema import (
        TEACHBACK_RECOMMENDED_BAND_MIN,
        TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
        TEACHBACK_REQUIRED_FIELDS,
        TEACHBACK_REQUIRED_SUBKEYS,
        TEACHBACK_VARIETY_ACK_VALID_VALUES,
        resolve_variety_total,
        validate_reasoning_reconstruction,
    )
    from shared.tool_response import extract_tool_response
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_advisory("module imports", _module_load_error)


# ─── constants ────────────────────────────────────────────────────────────────

# Self-completion carve-out resolution is delegated entirely to
# is_self_complete_exempt(task, team_name) in shared.intentional_wait
# (the SSOT). The predicate keys on team-config agentType (NOT owner
# name) — secretaries spawned under any name reach the carve-out as
# long as the team-config records agentType=pact-secretary. team_name
# is resolved via pact_context at the call site below. The dispatch_gate
# RESERVED_NAMES set still reserves the `secretary`/`pact-secretary`
# literals as a defense-in-depth name perimeter — see
# dispatch_gate.RESERVED_NAMES comment block for the name-perimeter
# rationale (the SSOT for self-completion exemption is agentType, but
# the name perimeter blocks teammates from spawning under reserved
# names that could shadow legitimate secretary spawn).

# Teachback-exempt dispatch carve-out resolution is delegated entirely to
# is_teachback_exempt(owner, team_name) in shared.intentional_wait.

# Required handoff schema fields (advisory if present-but-malformed).
_HANDOFF_REQUIRED_FIELDS = (
    "produced",
    "decisions",
    "reasoning_chain",
    "uncertainty",
    "integration",
    "open_questions",
)

# Teachback schema constants (TEACHBACK_REQUIRED_FIELDS,
# TEACHBACK_VARIETY_ACK_VALID_VALUES, TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN)
# and the reasoning_reconstruction validator are imported from
# shared.teachback_schema (SSOT). TEACHBACK_REQUIRED_SUBKEYS and
# validate_reasoning_reconstruction are consumed by the write-time advisory
# rules below.

# Required per-dimension rationale fields on metadata.variety (D11).
# 4-tuple. Each rationale is one sentence explaining THIS dispatch's score
# on THAT dimension. Cargo-cult-via-single-rationale (D4 legacy) is no longer
# schema-conformant; four distinct rationales force four fresh articulations.
_VARIETY_REQUIRED_RATIONALES = (
    "novelty_rationale",
    "scope_rationale",
    "uncertainty_rationale",
    "risk_rationale",
)

# Sentinel problem string returned by _validate_variety_schema when the
# rationales are well-formed but no resolvable variety total exists. The R4
# rule keys on this exact value to select the distinct unresolvable-total
# advisory message (vs. the rationale-malformed message).
_NO_RESOLVABLE_TOTAL = (
    "no resolvable total (need an integer 4-16 under key 'total', "
    "or a recoverable fallback)"
)


# Canonical Teachback Task subject pattern: `<teammate-name>: TEACHBACK
# for <mission descriptor>`. The leading `[a-z0-9-]+:` is the canonical
# teammate-prefix shape used across the plugin (matches names like
# `backend-coder-2`, `secretary`, `architect-1`); `TEACHBACK for ` is
# the canonical mission-framing per pact-completion-authority.md.
#
# WHY a structural match instead of substring `"teachback" in subject`:
# the substring form fires on ANY task subject containing the word —
# including planning/discussion subjects like `"Plan: wake-lifecycle
# teachback re-arm fix"` — and produces benign-but-noisy false-positive
# advisories. The structural match pins the pattern to the canonical
# Teachback-Gated Dispatch shape so only actual teachback tasks trip
# the rules.
_TEACHBACK_SUBJECT_PATTERN = re.compile(r"^[a-z0-9-]+: TEACHBACK for ")


def _is_teachback_subject(subject: str) -> bool:
    """Return True iff `subject` matches the canonical Teachback Task
    shape (`<teammate-name>: TEACHBACK for <mission>`).

    Pure function; never raises. Returns False on non-string input or
    any subject that does not match the anchored pattern. Replaces the
    legacy `"TEACHBACK" in subject_upper` substring check across the
    gate's TaskCreate and TaskUpdate rule paths.
    """
    if not isinstance(subject, str):
        return False
    return _TEACHBACK_SUBJECT_PATTERN.match(subject) is not None


# ─── metadata.completion_disputed writeback (direct FS) ──────────────────────


def _writeback_dispute(task_id: str) -> bool:
    """Set metadata.completion_disputed=true and metadata.gate_writeback=true
    on the task JSON, directly via filesystem write (no harness round-trip).

    Per user decision: direct FS write (no CLI shim). Reads via
    shared.task_utils.read_task_json (path-traversal safe), mutates metadata,
    writes back atomically (.tmp + os.replace).

    The gate_writeback marker is a recursion guard: if anything (the harness
    or a future tool) replays the metadata change as a TaskUpdate, this
    gate's step ① recursion check will skip it.

    Fail-OPEN by design: any IOError swallowed and returns False — advisory
    is still emitted by the caller (per the writeback-failure convention:
    the advisory's user-facing surface is the load-bearing signal; the
    metadata writeback is best-effort accounting). Logged via
    lifecycle_decision journal event.

    Returns True iff the writeback succeeded.
    """
    if not task_id or not isinstance(task_id, str):
        return False
    # M2 (security): sanitize_path_component strips C0 / \x00 control chars too —
    # a bare `re.sub(r'[/\\]|\.\.')` lets a NUL byte through to task_file.exists(),
    # which raises an uncaught ValueError ('embedded null byte') that propagates to
    # the gate catch-all and suppresses rule-enforcement for the turn (advisory-
    # suppression DoS). read_task_json's ValueError catch backstops other callers.
    sanitized_id = sanitize_path_component(task_id)
    if not sanitized_id:
        return False
    try:
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", team_name):
        return False

    task = read_task_json(sanitized_id, team_name)
    if not task:
        return False

    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        task["metadata"] = metadata
    metadata["completion_disputed"] = True
    metadata["gate_writeback"] = True

    tasks_root = get_claude_config_dir() / "tasks" / team_name
    target = tasks_root / f"{sanitized_id}.json"
    tmp = tasks_root / f".{sanitized_id}.json.tmp"
    try:
        tasks_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp.write_text(json.dumps(task), encoding="utf-8")
        os.replace(str(tmp), str(target))
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


# #906: auditor verdict severity ladder for destructive-downgrade detection.
# Higher rank = more severe. A lead overwrite that LOWERS the rank (e.g.
# RED->GREEN) is a destructive downgrade that escalates the advisory SEVERITY
# only — ranks are NEVER used to gate preservation (always-preserve regardless
# of direction, per the architect ruling). Unknown / non-dict signals yield
# None → no escalation (the advisory still fires, without the downgrade
# emphasis).
_AUDIT_SIGNAL_RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2}


def _audit_signal_rank(audit_summary: object) -> "int | None":
    """Return the severity rank of an audit_summary's `signal`, or None if the
    shape/signal is unrankable. Pure; never raises."""
    if not isinstance(audit_summary, dict):
        return None
    signal = audit_summary.get("signal")
    if not isinstance(signal, str):
        return None
    return _AUDIT_SIGNAL_RANK.get(signal.strip().upper())


def _is_destructive_audit_downgrade(prior: object, incoming: object) -> bool:
    """Return True iff `incoming` LOWERS `prior`'s verdict severity (e.g.
    RED->GREEN). Unknown signals on either side → False (cannot rank → no
    escalation). Pure; never raises. Used ONLY for advisory wording — preservation
    is unconditional regardless of the return value."""
    prior_rank = _audit_signal_rank(prior)
    incoming_rank = _audit_signal_rank(incoming)
    if prior_rank is None or incoming_rank is None:
        return False
    return incoming_rank < prior_rank


def _writeback_audit_recovery(task_id: str, updates: dict) -> bool:
    """#906 codified-mirror writeback — durable, direct-FS metadata update for
    auditor-verdict overwrite-protection. Mirrors _writeback_dispute exactly
    (read via shared.task_utils.read_task_json [path-traversal safe] → mutate
    metadata → atomic .tmp + os.replace), and sets the gate_writeback recursion
    guard so a replayed metadata change cannot re-fire the gate.

    Used by BOTH branches of the codified mirror:
      - MIRROR  : updates={"audit_summary_authored": <authored verdict>}
      - RECOVER : updates={"lead_close_note": <lead's overwriting value>}

    Task JSON lives in the team dir (~/.claude/tasks/{team}/), which is writable
    from ANY teammate process — unlike the session journal, which self-drops in
    a teammate context (#877). That is precisely why the auditor's verdict can
    be captured at AUTHOR time (the MIRROR), sidestepping the post-overwrite
    read-of-a-clobbered-value problem.

    Fail-OPEN by design: any IOError swallowed → returns False. The advisory is
    the load-bearing user-facing signal; the metadata writeback is best-effort
    accounting (same convention as _writeback_dispute). Returns True iff the
    writeback succeeded.
    """
    if not task_id or not isinstance(task_id, str):
        return False
    # M2 (security): sanitize_path_component strips C0 / \x00 control chars too —
    # a bare `re.sub(r'[/\\]|\.\.')` lets a NUL byte through to task_file.exists(),
    # which raises an uncaught ValueError ('embedded null byte') that propagates to
    # the gate catch-all and suppresses rule-enforcement for the turn (advisory-
    # suppression DoS). read_task_json's ValueError catch backstops other callers.
    sanitized_id = sanitize_path_component(task_id)
    if not sanitized_id:
        return False
    try:
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""
    if not team_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", team_name):
        return False

    task = read_task_json(sanitized_id, team_name)
    if not task:
        return False

    metadata = task.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        task["metadata"] = metadata
    for key, value in updates.items():
        metadata[key] = value
    metadata["gate_writeback"] = True

    tasks_root = get_claude_config_dir() / "tasks" / team_name
    target = tasks_root / f"{sanitized_id}.json"
    tmp = tasks_root / f".{sanitized_id}.json.tmp"
    try:
        tasks_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp.write_text(json.dumps(task), encoding="utf-8")
        os.replace(str(tmp), str(target))
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _emit_lead_side_agent_handoff(
    team_name: str,
    task_id: str,
    owner: str,
    subject: str,
    task_metadata: dict,
) -> None:
    """Fix A (#869): emit a single agent_handoff event at the lead's
    acceptance-commit — the lead's TaskUpdate(status="completed") on a work
    task carrying a populated metadata.handoff.

    WHY HERE (the b2 emit point): agent_handoff is TaskCompleted-keyed; a
    stage-ready task completes mid-turn, so it is already "completed" at the
    lead's Stop-sweep and swept over → the TaskCompleted-keyed
    agent_handoff_emitter (b1) never fires for it. The lead's process has a
    populated context, so append_event writes the canonical journal via
    get_session_dir() with no resolver.

    EMIT-ELIGIBILITY MIRRORS agent_handoff_emitter (b1) — that hook is the
    CANONICAL source for the emit-shape (owner non-empty, not a teachback
    task, not a signal task, handoff present). The divergence-critical atoms
    (is_signal_task, occupant_hash, already_emitted) are SHARED via
    shared.agent_handoff_marker so the two paths cannot drift on signal-task
    exclusion or the dedup key (#887 class). The b2-specific topology gate
    (is_lead) is applied by the caller. Eligibility keys on handoff PRESENCE
    (owner / not-teachback / not-signal / handoff-present), never on an
    owner-name prefix — bare owner names are the convention, so a `pact-`
    prefix gate would no-op. (A now-retired completion-time branch above once
    carried such a prefix gate; it was permanently dormant and was removed.)

    Shares the occupant-keyed marker with b1: if a mid-turn TaskUpdate ALSO
    dispatches TaskCompleted (R2 — open until the real-tmux smoke), b1 and b2
    test-and-set the SAME marker and dedup to exactly one event.

    Best-effort: fail-open on any error (matches append_event's policy and
    this hook's livelock-safe exit-0 posture; never raises to the caller).
    """
    try:
        # Emit-eligibility (mirrors b1). owner-empty / teachback / signal-task
        # / handoff-absent all suppress, same as the emitter's bypass gates.
        # #917 R2 (validate-before-claim): also reject a WHITESPACE-only owner —
        # it passes a bare falsy check but FAILS the journal's non-empty-str
        # `agent` schema, so it would claim the O_EXCL marker then fail
        # append_event (claim-without-write poison). Mirrors b1's owner guard.
        if not owner or not owner.strip() or _is_teachback_subject(subject):
            return
        if is_signal_task(task_metadata):
            return
        # M1: handoff must be a dict (the journal schema requires it). A
        # truthy-but-non-dict handoff (str/list) would pass a bare presence
        # check, claim the O_EXCL marker, then FAIL append_event's schema
        # validation — an orphaned/poisoned marker. isinstance makes a
        # malformed handoff DEFER (claim nothing). Mirrors b1's gate.
        handoff = task_metadata.get("handoff")
        if not isinstance(handoff, dict) or not handoff:
            return
        # #917 R2 (validate-before-claim): substitute the sentinel for a
        # whitespace-only / empty subject (mirrors b1's falsy+whitespace
        # substitution) so a degenerate subject is schema-valid BEFORE the
        # claim rather than poisoning the marker. _is_teachback_subject above
        # already ran on the original subject (a blank subject is not a
        # teachback), so substituting here does not change the teachback gate.
        if not subject or not subject.strip():
            subject = "(no subject)"
        # #917 symmetry: same writability precondition as b1
        # (agent_handoff_emitter). In the lead's gate process this is a no-op
        # (the lead's context is persisted -> get_journal_path() resolves), but
        # keeping both emit paths' marker-claim preconditions IDENTICAL prevents
        # the b1/b2 divergence class (#887/#901): a future change that makes b2
        # reachable from a non-lead / unresolvable context cannot silently
        # claim-without-write and poison the shared marker. Pure read; the
        # mark-then-write order below is unchanged.
        # F3: this gate is the TWIN of agent_handoff_emitter.main — keep both
        # in parity. Mark-then-write / O_EXCL contract:
        # shared/agent_handoff_marker.already_emitted.
        if not get_journal_path():
            return
        occupant = occupant_hash(owner, subject)
        if already_emitted(team_name, task_id, occupant):
            return
        # #917 R1 (compensating-unclaim): we OWN the marker here (already_emitted
        # returned False = fresh O_EXCL claim). Roll the claim back if the write
        # returns False (schema rejection / unwritable dir — the residual paths
        # the writability gate does NOT cover) OR raises, so a later writable
        # fire can re-emit instead of being permanently suppressed by the
        # poisoned marker. Best-effort + fail-safe (worst case reverts to
        # today's behavior). F3 twin of agent_handoff_emitter.main.
        try:
            written = append_event(
                make_event(
                    "agent_handoff",
                    agent=owner,
                    task_id=task_id,
                    task_subject=subject,
                    handoff=handoff,
                )
            )
        except Exception:
            written = False
        if not written:
            unclaim(team_name, task_id, occupant)
    except Exception:
        # Fail-open: a journal-emit failure must never break the gate's
        # advisory evaluation or its exit-0 contract.
        pass


# ─── core evaluation ─────────────────────────────────────────────────────────


def _validate_handoff_schema(handoff: object) -> str | None:
    """Return None if handoff is well-formed, or a short reason string
    describing the schema problem (suitable for advisory text).
    """
    if not isinstance(handoff, dict):
        return f"metadata.handoff must be object, got {type(handoff).__name__}"
    missing = [f for f in _HANDOFF_REQUIRED_FIELDS if f not in handoff]
    if missing:
        return f"metadata.handoff missing required fields: {', '.join(missing)}"
    return None


def _validate_variety_acknowledgment(ack: object) -> str | None:
    """Return None if variety_acknowledgment is well-formed per D10, or a
    short reason string. Pure function; never raises.

    Schema:
      - must be dict
      - rationale_articulates_this_dispatch: enum 'yes' | 'no' | 'concern'
      - concern: non-empty string when value != 'yes'; optional/empty when 'yes'
    """
    if not isinstance(ack, dict):
        return f"must be object, got {type(ack).__name__}"
    value = ack.get("rationale_articulates_this_dispatch")
    if value not in TEACHBACK_VARIETY_ACK_VALID_VALUES:
        return (
            f"rationale_articulates_this_dispatch must be one of "
            f"{TEACHBACK_VARIETY_ACK_VALID_VALUES}, got {value!r}"
        )
    if value != "yes":
        concern = ack.get("concern")
        if not isinstance(concern, str) or not concern.strip():
            return (
                "concern field required (non-empty string) when "
                "rationale_articulates_this_dispatch != 'yes'"
            )
    return None


def _validate_teachback_submit_schema(teachback: object) -> str | None:
    """Return None if teachback_submit is well-formed, or a short reason
    string. Mirrors _validate_handoff_schema.

    Validates the 5 canonical fields per pact-teachback skill (4 string
    fields + variety_acknowledgment dict per D10). reasoning_reconstruction
    is checked separately at R3 dispatch time, not here.
    """
    if not isinstance(teachback, dict):
        return (
            f"metadata.teachback_submit must be object, "
            f"got {type(teachback).__name__}"
        )
    missing = [f for f in TEACHBACK_REQUIRED_FIELDS if f not in teachback]
    if missing:
        return (
            f"metadata.teachback_submit missing required fields: "
            f"{', '.join(missing)}"
        )
    # Non-empty-string check on the 4 string fields; variety_acknowledgment
    # is a dict, validated by the dedicated sub-validator below.
    string_fields = tuple(
        f for f in TEACHBACK_REQUIRED_FIELDS if f != "variety_acknowledgment"
    )
    empty = [
        f for f in string_fields
        if not isinstance(teachback.get(f), str) or not teachback[f].strip()
    ]
    if empty:
        return (
            f"metadata.teachback_submit fields empty/non-string: "
            f"{', '.join(empty)}"
        )
    ack_problem = _validate_variety_acknowledgment(
        teachback.get("variety_acknowledgment")
    )
    if ack_problem:
        return (
            f"metadata.teachback_submit.variety_acknowledgment "
            f"{ack_problem}"
        )
    return None


def _validate_variety_schema(
    variety: object, metadata: object = None
) -> str | None:
    """Return None if metadata.variety is well-formed per D11, or a short
    reason string. Pure function; never raises.

    Validates the four per-dimension rationale fields (presence +
    non-empty string) AND that the stamp carries a resolvable variety
    total. The total check consults the same shared resolver the
    read-time band resolver uses (resolve_variety_total) — this is the
    cross-rule consistency property: any variety shape that passes
    write-time validation MUST resolve at read-time. Dimension score
    range checks (1-4) are the orchestrator's authority; this hook is
    defense-in-depth for the cargo-cult-prevention property D11 codifies.

    The optional `metadata` argument is forwarded to the resolver so the
    non-canonical top-level `variety_score` sibling is reachable. Callers
    that only have the variety dict may omit it (the resolver simply skips
    that candidate). Rationale problems are checked first (cheap dict +
    string checks), then the total (one resolver call); the first problem
    found is returned, preserving the single-string-return contract.
    """
    if not isinstance(variety, dict):
        return f"must be object, got {type(variety).__name__}"
    missing = [
        r for r in _VARIETY_REQUIRED_RATIONALES if r not in variety
    ]
    if missing:
        return (
            f"missing required per-dimension rationales: "
            f"{', '.join(missing)}"
        )
    empty = [
        r for r in _VARIETY_REQUIRED_RATIONALES
        if not isinstance(variety.get(r), str) or not variety[r].strip()
    ]
    if empty:
        return (
            f"per-dimension rationales empty/non-string: "
            f"{', '.join(empty)}"
        )
    if resolve_variety_total(variety, metadata) is None:
        return _NO_RESOLVABLE_TOTAL
    return None


def _resolve_required_band_via_blocks(
    task_a: dict, team_name: str
) -> str:
    """Resolve the REQUIRED band for reasoning_reconstruction from Task A
    via blocks traversal to Task B.

    Returns one of:
      - "required": resolved total >= TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN
      - "recommended": TEACHBACK_RECOMMENDED_BAND_MIN <= total < required-min
      - "skipped": total < TEACHBACK_RECOMMENDED_BAND_MIN
      - "unresolvable": blocks link missing, Task B file missing, or
        variety absent/malformed/untotaled on Task B (fail-open: caller
        emits a separate band_unresolvable advisory documenting the gap)

    The total is resolved via the shared resolve_variety_total helper, so a
    non-canonical stamp (score / top-level variety_score / dimension-sum)
    resolves rather than reading as "unresolvable" — the same resolver the
    write-time validator consults (the cross-rule consistency property).
    """
    blocks = task_a.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return "unresolvable"
    # Convention: Task A blocks Task B (the work task). The first blocked
    # ID is the canonical work-task pointer in the Teachback-Gated Dispatch
    # shape — multi-block teachback tasks are not in current convention.
    task_b_id = blocks[0]
    if not isinstance(task_b_id, str) or not task_b_id:
        return "unresolvable"
    if not team_name:
        return "unresolvable"
    task_b = read_task_json(task_b_id, team_name)
    if not task_b:
        return "unresolvable"
    metadata = task_b.get("metadata")
    if not isinstance(metadata, dict):
        return "unresolvable"
    variety = metadata.get("variety")
    if not isinstance(variety, dict):
        return "unresolvable"
    total = resolve_variety_total(variety, metadata)
    if total is None:
        return "unresolvable"
    if total >= TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN:
        return "required"
    if total >= TEACHBACK_RECOMMENDED_BAND_MIN:
        return "recommended"
    return "skipped"


def evaluate_lifecycle(input_data: dict) -> list[tuple[str, str]]:
    """Return list of (rule, message) advisory tuples. Empty list → ALLOW
    silently.

    The ``rule`` element is a behavioral identifier (e.g.
    ``"teachback_addblocks_missing"``, ``"self_completion"``) used in
    journal events; the ``message`` element is the human-readable
    advisory text emitted via additionalContext.
    """
    advisories: list[tuple[str, str]] = []
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input") or {}
    # Defense-in-depth: extract_tool_response is the SSOT helper that prefers
    # the canonical `tool_response` field, falls back to legacy `tool_output`
    # (covers captured-from-production test fixtures predating the rename and
    # any future platform envelope rename), and warns to stderr on
    # dual-envelope payloads (categorically suspicious — no legitimate platform
    # fire emits both). This hook fires on every Task-tool use, so a missed
    # read here would silently disable lifecycle advisories. DO NOT remove.
    tool_response = extract_tool_response(input_data)
    if not isinstance(tool_input, dict):
        tool_input = {}

    # ① Recursion guard (self-completion writeback self-trigger): skip
    # silently if THIS update is the gate's own writeback. Checked FIRST
    # before any other rule.
    incoming_metadata = tool_input.get("metadata") or {}
    if isinstance(incoming_metadata, dict) and incoming_metadata.get("gate_writeback") is True:
        return []

    # Resolve team_name once at function scope. Both branches need it:
    # TaskCreate's work_addblockedby_missing rule consumes it via
    # is_teachback_exempt; TaskUpdate's self-completion carve-out consumes
    # it via is_self_complete_exempt; and the TaskUpdate disk-fallback read
    # uses it for the team-scoped task path. Empty string on failure
    # (fail-closed downstream: both predicates return False on empty
    # team_name).
    try:
        team_name = pact_context.get_pact_context().get("team_name", "")
    except Exception:
        team_name = ""

    # ②a #906 auditor-verdict overwrite-protection (codified mirror).
    # ONE hook, TWO structural branches keyed on is_lead (no new matcher):
    #   MIRROR (non-lead writes audit_summary): durably snapshot the authored
    #     verdict → metadata.audit_summary_authored so a later lead overwrite of
    #     the live audit_summary key cannot lose it. Task JSON is team-dir-scoped
    #     → writable from the auditor's teammate process (unlike the session
    #     journal, which self-drops in a teammate context, #877). Capturing at
    #     AUTHOR time is what sidesteps the post-overwrite read-of-a-clobbered-
    #     value problem (the prior is saved BEFORE the lead can clobber it).
    #   RECOVER (lead overwrites a DIVERGENT authored verdict): the prior is
    #     ALREADY preserved in audit_summary_authored (no clobbered read); emit
    #     the audit_summary_overwrite advisory + route the lead's value →
    #     metadata.lead_close_note. Preservation is UNCONDITIONAL; a destructive
    #     downgrade (severity lowered, e.g. RED->GREEN) escalates the advisory
    #     SEVERITY only — never gates preservation (an upgrade / lateral change
    #     must preserve the auditor's detail too). Gated on audit_summary_authored
    #     EXISTING so a lead-authored-from-scratch summary is not a false fire.
    # Both branches persist via _writeback_audit_recovery (the _writeback_dispute
    # sibling). The platform's synchronous task-JSON write has already landed by
    # PostToolUse, so this writeback is the LAST write and is not clobbered.
    if tool_name == "TaskUpdate":
        incoming_audit = (
            incoming_metadata.get("audit_summary")
            if isinstance(incoming_metadata, dict)
            else None
        )
        ah_task_id = tool_input.get("taskId", "") or ""
        if incoming_audit is not None and team_name and ah_task_id:
            ah_task = read_task_json(ah_task_id, team_name)
            ah_meta = ah_task.get("metadata") if isinstance(ah_task, dict) else None
            authored = (
                ah_meta.get("audit_summary_authored")
                if isinstance(ah_meta, dict)
                else None
            )
            if pact_context.is_lead(input_data):
                # RECOVER — only when a DIFFERENT authored verdict exists.
                if authored is not None and authored != incoming_audit:
                    downgrade = _is_destructive_audit_downgrade(
                        authored, incoming_audit
                    )
                    _writeback_audit_recovery(
                        ah_task_id, {"lead_close_note": incoming_audit}
                    )
                    advisories.append((
                        "audit_summary_overwrite",
                        f"PACT task_lifecycle_gate: lead TaskUpdate "
                        f"{'DESTRUCTIVELY DOWNGRADED' if downgrade else 'overwrote'} "
                        f"Task {ah_task_id}'s auditor audit_summary. The auditor's "
                        "authored verdict is PRESERVED in "
                        "metadata.audit_summary_authored and the lead's value was "
                        "routed to metadata.lead_close_note — the verdict is not "
                        "lost. "
                        + (
                            "DESTRUCTIVE DOWNGRADE (verdict severity lowered, "
                            "e.g. RED->GREEN / terminal->reopen): confirm the "
                            "auditor's concern was genuinely resolved before "
                            "closing. "
                            if downgrade
                            else ""
                        )
                        + "Never infer auditor silence from a read-after-write "
                        "timeout — the window is unbounded.",
                    ))
            else:
                # MIRROR — snapshot/refresh the authored verdict. Idempotent:
                # skip the FS write when the mirror already matches.
                if authored != incoming_audit:
                    _writeback_audit_recovery(
                        ah_task_id, {"audit_summary_authored": incoming_audit}
                    )

    # ② TaskCreate rules — teachback addBlocks + work-task addBlockedBy
    if tool_name == "TaskCreate":
        subject = (tool_input.get("subject") or "")
        is_teachback = _is_teachback_subject(subject)
        owner = tool_input.get("owner") or ""
        if not isinstance(owner, str):
            owner = ""

        # Clause order is intentional: the cheap checks
        # (is_teachback dict-lookup, owner.startswith string-prefix,
        # tool_input.get dict-lookup) precede the disk-reading
        # is_teachback_exempt predicate. Common path (well-formed
        # dispatch with addBlockedBy provided) short-circuits at the
        # 3rd clause and never hits disk. Failure path (missing
        # addBlockedBy) does hit disk via _iter_members, but this is
        # the rare misconfiguration path — amortized cost near zero.
        #
        # Cached short-circuit: `_teachback_exempt` is computed at most
        # once per TaskCreate and reused by R4 below. Both rules share
        # the same disk-touching predicate; the cache amortizes the
        # `is_teachback_exempt(owner, team_name)` cost across rules.
        # `None` sentinel means "not yet computed"; `False/True` is the
        # cached result. The lazy fill preserves the cheap-first clause
        # ordering (no eager disk read when cheap predicates fail).
        _teachback_exempt: bool | None = None

        def _exempt() -> bool:
            nonlocal _teachback_exempt
            if _teachback_exempt is None:
                _teachback_exempt = is_teachback_exempt(owner, team_name)
            return _teachback_exempt

        if (
            not is_teachback
            and owner.startswith("pact-")
            and not tool_input.get("addBlockedBy")
            and not _exempt()
        ):
            advisories.append((
                "work_addblockedby_missing",
                f"PACT task_lifecycle_gate: pact-* Task created "
                f"(owner={owner!r}) without addBlockedBy=[<teachback_id>]. "
                "Work tasks must block on teachback acceptance.",
            ))

        # R4: variety_missing_on_dispatch_task (D11-refined).
        # Same discriminator pattern as work_addblockedby_missing
        # (not-teachback + pact-* owner + not exempt). Single rule with
        # three trigger paths (absent variety OR malformed per-dimension
        # rationales OR no resolvable total); distinct message text per
        # path, same rule name — the lead-side correction is the same
        # (re-stamp variety) in every case. Clause order mirrors L575-580:
        # cheap dict-lookups first, disk-touching exempt-check last
        # (cached via `_exempt()` so the second invocation reuses the
        # first call's result).
        if (
            not is_teachback
            and owner.startswith("pact-")
            and not _exempt()
        ):
            incoming_metadata = tool_input.get("metadata") or {}
            incoming_variety = incoming_metadata.get("variety")
            if not incoming_variety:
                advisories.append((
                    "variety_missing_on_dispatch_task",
                    f"PACT task_lifecycle_gate: pact-* Task created "
                    f"(owner={owner!r}) without metadata.variety. "
                    "Per-dispatch variety stamping is required for hook "
                    "band-resolution. See orchestrate / comPACT / "
                    "peer-review dispatch surfaces.",
                ))
            else:
                # The validator forwards the surrounding metadata so the
                # non-canonical top-level variety_score sibling is reachable
                # by the shared resolver. It returns the rationale problem
                # first (if any), then the total problem; the total problem
                # carries the distinctive _NO_RESOLVABLE_TOTAL sentinel so
                # the two paths emit distinct message text under one rule.
                schema_problem = _validate_variety_schema(
                    incoming_variety, incoming_metadata
                )
                if schema_problem == _NO_RESOLVABLE_TOTAL:
                    advisories.append((
                        "variety_missing_on_dispatch_task",
                        f"PACT task_lifecycle_gate: pact-* Task created "
                        f"(owner={owner!r}) with metadata.variety carrying "
                        "no resolvable total (need an integer 4-16 under "
                        "key 'total', or a recoverable fallback). Per "
                        "pact-variety.md the canonical key is 'total'. The "
                        "hook's read-time band resolver requires a "
                        "resolvable total; stamping without one trips a "
                        "band-unresolvable advisory at teachback-submit time.",
                    ))
                elif schema_problem:
                    advisories.append((
                        "variety_missing_on_dispatch_task",
                        f"PACT task_lifecycle_gate: pact-* Task created "
                        f"(owner={owner!r}) with malformed "
                        f"metadata.variety — {schema_problem}. Per D11 "
                        "schema, all four per-dimension rationales must "
                        "be present as non-empty strings.",
                    ))

    # ③ TaskUpdate-to-completed rules — paired-send, handoff presence,
    # handoff schema, self-completion
    if tool_name == "TaskUpdate" and tool_input.get("status") == "completed":
        task_id = tool_input.get("taskId", "") or ""
        # team_name resolved at function scope above; consumed here by the
        # disk-fallback read and the self-completion carve-out predicate
        # (is_self_complete_exempt keys on team-config agentType).
        # Final post-state — prefer tool_response.task; fallback to bare dict.
        task = tool_response.get("task") if isinstance(tool_response, dict) else None
        if not isinstance(task, dict):
            # Fall back to disk if harness output didn't include the task.
            task = read_task_json(task_id, team_name) if team_name else {}

        subject = task.get("subject") or ""
        is_teachback = _is_teachback_subject(subject)
        owner = task.get("owner") or ""
        if not isinstance(owner, str):
            owner = ""
        raw_metadata = task.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

        # RETIRED: the work-task handoff-presence/schema branch (gated on
        # `is_work_task = not is_teachback and owner.startswith("pact-")`,
        # emitting handoff_missing / handoff_schema_invalid) was permanently
        # dormant. Real teammate owners are bare names (devops, auditor,
        # backend), never "pact-"-prefixed, so the guard was always False and
        # neither advisory could fire. Disposition is to retire rather than
        # re-point at bare owners: the lead-side emitter below already covers
        # HANDOFF presence at acceptance-commit (its own handoff-present
        # eligibility), so re-activating would duplicate coverage, not fill a
        # gap. _validate_handoff_schema is retained for its co-location pattern
        # and as the structural sibling of _validate_teachback_submit_schema
        # (which mirrors it).

        # Fix A (#869): lead-side agent_handoff emission at acceptance-commit.
        # Self-contained, with no owner-name prefix gate (none is needed — bare
        # owner names are the convention; cf. the retired branch above):
        # _emit_lead_side_agent_handoff applies the b1-mirrored emit-eligibility
        # (owner / not-teachback / not-signal / handoff-present) + the shared
        # occupant-keyed dedup. Gated on
        # is_lead so the emit only runs in the lead's process, where
        # get_session_dir() resolves the canonical journal; a teammate
        # self-completion (carve-out / disputed) has no populated context and
        # is correctly skipped (it would no-op AND must not be double-counted).
        if pact_context.is_lead(input_data):
            _emit_lead_side_agent_handoff(
                team_name, task_id, owner, subject, metadata
            )

        # Teachback-subject completion-time checks: teachback_submit presence
        # + schema. R1/R2 are disjoint on the missing-vs-malformed split:
        #   teachback_submit missing/empty → R1, skip schema check.
        #   present but malformed → R2.
        if is_teachback and owner:
            teachback_submit = metadata.get("teachback_submit")
            if not teachback_submit:
                advisories.append((
                    "teachback_submit_missing",
                    f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                    f"completed without metadata.teachback_submit. The "
                    "teammate skipped the canonical teachback gate "
                    "(pact-teachback skill).",
                ))
            else:
                schema_problem = _validate_teachback_submit_schema(
                    teachback_submit
                )
                if schema_problem:
                    advisories.append((
                        "teachback_submit_schema_invalid",
                        f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                        f"{schema_problem}.",
                    ))

            # NOTE (#897): a per-completion paired-wake detector was removed
            # here. It read inboxes/{owner}.json, but that store is
            # platform-written ASYNC on delivery (file mtime == embedded msg
            # ts) — AFTER this synchronous PostToolUse(TaskUpdate-completed)
            # read — and SendMessage is not in the Pre/PostToolUse-hookable
            # tool set, so there is no send event to key on. The check was
            # ~100% false-positive by construction (the from-field was already
            # literal "team-lead", so it was never a from-field bug). Do NOT
            # restore it pointed at the same store. The genuine missed-wake gap
            # (teammate idles on awaiting_lead_completion) has no automated net
            # today; it is tracked as a separate follow-up.

        # Teammate self-completion check. Carve-out is a single boolean
        # via is_self_complete_exempt(task, team_name) — the predicate is
        # the SSOT for both surfaces (team-config agentType + signal-task).
        actor = trustworthy_actor_name(input_data)
        if (
            actor is not None
            and owner
            and owner == actor
            and not is_self_complete_exempt(task, team_name)
        ):
            advisories.append((
                "self_completion",
                f"PACT task_lifecycle_gate: teammate {actor!r} "
                f"self-completed Task {task_id} without carve-out. "
                "Lead-only completion authority — see "
                "pact-completion-authority.md. Task marked "
                "metadata.completion_disputed=true; lead must re-complete "
                "intentionally to clear.",
            ))
            _writeback_dispute(task_id)

    # ④ TaskUpdate write-time rules — R3 + R5 fire when a teammate writes
    # metadata.teachback_submit (NOT at completion). Co-located: both rules
    # share one read_task_json(task_a) for the subject + blocks traversal.
    # Gated on status != "completed" so R3/R5 do not double up with R1/R2
    # in the rare case a teammate bundles teachback_submit + completion in
    # one TaskUpdate (R1/R2 cover the completion-time surface; R3/R5 are
    # the teammate-facing first-line write-time correction).
    #
    # Also at this block: 4 cross-slot/cross-key shape advisories that catch
    # canonical-schema deviations at the wrong-write moment (rather than at
    # lead-review-time). Rule names are LOAD-BEARING grep-anchors for the
    # pact-teachback skill's Common Mistakes section — do not rename without
    # paired SKILL.md edit.
    if (
        tool_name == "TaskUpdate"
        and tool_input.get("status") != "completed"
    ):
        incoming_teachback = (
            incoming_metadata.get("teachback_submit")
            if isinstance(incoming_metadata, dict)
            else None
        )
        incoming_handoff = (
            incoming_metadata.get("handoff")
            if isinstance(incoming_metadata, dict)
            else None
        )

        # Cross-slot check: reasoning_reconstruction belongs on
        # teachback_submit, NOT on handoff. Fires whenever HANDOFF is
        # being written with rr nested inside it, regardless of whether
        # teachback_submit is also being written — catches the wrong-slot
        # mistake at the work-task surface (where HANDOFFs land) as well
        # as the teachback-task surface. handoff.reasoning_chain is the
        # canonical sender-side field; reasoning_reconstruction is the
        # receiver-side teachback field. Symmetric concept, different slot.
        if isinstance(incoming_handoff, dict) and "reasoning_reconstruction" in incoming_handoff:
            task_id = tool_input.get("taskId", "") or ""
            advisories.append((
                "reasoning_reconstruction_in_handoff",
                f"PACT task_lifecycle_gate: Task {task_id} "
                "placed reasoning_reconstruction inside metadata.handoff "
                "(wrong slot). Correct destination depends on task type: "
                "for a Teachback Task, place reasoning_reconstruction at "
                "top-level on metadata.teachback_submit. For a work task "
                "carrying a HANDOFF, the HANDOFF schema has "
                "reasoning_chain (sender's view) — not "
                "reasoning_reconstruction (the receiver-side teachback "
                "field). See pact-teachback skill Common mistakes row 2.",
            ))

        # Shared task_a disk read — lifted to sibling of the cross-slot
        # handoff check above so the wiring-boundary
        # teachback_addblocks_missing rule and the teachback_submit
        # write-time rules below all consume one disk read per
        # non-completed TaskUpdate. Single-pay disk cost across all
        # write-time rules that need task_a's subject + blocks.
        task_id = tool_input.get("taskId", "") or ""
        task_a = read_task_json(task_id, team_name) if team_name else {}
        if not isinstance(task_a, dict):
            task_a = {}
        subject = task_a.get("subject") or ""

        # Wiring-boundary teachback_addblocks_missing — fires when the
        # canonical Step-3 wiring TaskUpdate (lead sets owner on a
        # teachback Task) lands WITHOUT addBlocks=[<work_task_id>].
        # Predicates: subject is teachback-shaped AND incoming owner is
        # being set AND addBlocks is absent on the wiring update AND the
        # task_a already-on-disk record does not have blocks wired (benign
        # late-wiring guard: if an earlier TaskUpdate already wired blocks,
        # a later owner-only update is not a violation). Re-times the
        # historical TaskCreate-time check (which was structurally
        # unsatisfiable because the work-task id did not exist yet at
        # TaskCreate(A)) to the moment the lead can satisfy both clauses
        # in one TaskUpdate.
        if (
            _is_teachback_subject(subject)
            and tool_input.get("owner")
            and not tool_input.get("addBlocks")
            and not task_a.get("blocks")
        ):
            advisories.append((
                "teachback_addblocks_missing",
                f"PACT task_lifecycle_gate: Teachback Task {task_id} "
                "owner-wiring TaskUpdate landed without "
                "addBlocks=[<work_task_id>]. Canonical sequence pairs "
                "owner + addBlocks in one update; split-write leaves "
                "the teachback gate unwired.",
            ))

        # task_id, subject, task_a are hoisted to L776-780 (sibling-of-cross-slot-handoff-check); this branch consumes the hoisted values.
        if isinstance(incoming_teachback, dict):
            if _is_teachback_subject(subject):
                # R5 (D10): variety_acknowledgment presence check.
                # Presence-only at write-time; full schema validation is
                # the next rule's job (write-time schema check forwarded
                # from R2's completion-time surface).
                if "variety_acknowledgment" not in incoming_teachback:
                    advisories.append((
                        "variety_acknowledgment_missing",
                        f"PACT task_lifecycle_gate: Teachback Task "
                        f"{task_id} submitted without "
                        "variety_acknowledgment. Per D10, the teammate "
                        "must record judgment of the orchestrator's "
                        "variety scoring before lead-side teachback "
                        "review. See pact-teachback skill.",
                    ))
                else:
                    # variety_acknowledgment present → forward the schema
                    # check from R2's completion-time surface to write-time
                    # so the wrong shape (e.g. STRING instead of OBJECT) is
                    # caught at the wrong-write moment. Disjoint with R5
                    # by predicate (R5 fires on absent; this rule fires on
                    # present-but-malformed).
                    ack_problem = _validate_variety_acknowledgment(
                        incoming_teachback["variety_acknowledgment"]
                    )
                    if ack_problem:
                        advisories.append((
                            "variety_acknowledgment_schema_invalid_at_write_time",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} submitted with malformed "
                            f"variety_acknowledgment — {ack_problem}. "
                            "Per D10, variety_acknowledgment is an OBJECT "
                            "(NOT a free-text string) with "
                            f"rationale_articulates_this_dispatch in "
                            f"{TEACHBACK_VARIETY_ACK_VALID_VALUES} + concern "
                            "(when != 'yes'). See pact-teachback skill "
                            "Common mistakes row 1.",
                        ))

                # reasoning_reconstruction sub-key schema check —
                # disjoint from R3 (R3 fires on absent-at-required-band;
                # this rule fires on present-but-malformed regardless of
                # band). Pure validator from shared.teachback_schema; lead
                # will reject with malformed_reasoning_reconstruction or
                # empty_reasoning_reconstruction_field.
                if "reasoning_reconstruction" in incoming_teachback:
                    rr_problem = validate_reasoning_reconstruction(
                        incoming_teachback["reasoning_reconstruction"]
                    )
                    if rr_problem:
                        advisories.append((
                            "reasoning_reconstruction_subkeys_invalid",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} reasoning_reconstruction "
                            f"rejected — {rr_problem}. Canonical 3 "
                            f"sub-keys are {TEACHBACK_REQUIRED_SUBKEYS} "
                            "as non-empty strings. Lead will reject with "
                            "the same reason enum. See pact-teachback "
                            "skill Common mistakes row 3.",
                        ))

                # Cross-key check: intentional_wait is a sibling
                # top-level metadata key (Step 3 of the canonical 3-step
                # shape), NOT nested inside teachback_submit. Nested
                # placement is invisible to is_self_complete_exempt
                # (shared/intentional_wait.py reads metadata.intentional_wait,
                # not nested locations) — the teammate's idle is
                # unprotected.
                if "intentional_wait" in incoming_teachback:
                    advisories.append((
                        "intentional_wait_nested_in_teachback_submit",
                        f"PACT task_lifecycle_gate: Teachback Task "
                        f"{task_id} placed intentional_wait inside "
                        "teachback_submit. It must be a top-level "
                        "metadata key (separate TaskUpdate call per "
                        "the canonical Step 1 / Step 3 ordering). Your "
                        "idle is unprotected — is_self_complete_exempt "
                        "reads metadata.intentional_wait, not the "
                        "nested location. See pact-teachback skill "
                        "Common mistakes row 4.",
                    ))

                # R3: reasoning_reconstruction required at >= 11 band.
                # Traversal helper returns "required" | "recommended" |
                # "skipped" | "unresolvable". Fail-open: traversal
                # failure emits a separate band_unresolvable advisory
                # rather than no signal — the gap is documented but
                # not blocking.
                band = _resolve_required_band_via_blocks(task_a, team_name)
                if band == "required":
                    rr = incoming_teachback.get("reasoning_reconstruction")
                    if not isinstance(rr, dict) or not rr:
                        advisories.append((
                            "reasoning_reconstruction_missing_at_required_band",
                            f"PACT task_lifecycle_gate: Teachback Task "
                            f"{task_id} submitted at REQUIRED band "
                            "(Task B variety >= 11) without "
                            "reasoning_reconstruction. Per "
                            "pact-ct-teachback.md §When to "
                            "Method-Reconstruct, lead will reject; "
                            "revise before submission.",
                        ))
                elif band == "unresolvable":
                    advisories.append((
                        "reasoning_reconstruction_band_unresolvable",
                        f"PACT task_lifecycle_gate: Teachback Task "
                        f"{task_id} cannot resolve the variety band. One "
                        "of: Task A.blocks is missing/empty; team context "
                        "is unavailable; the Task B file is missing; or "
                        "Task B.metadata.variety is absent OR present but "
                        "carries no resolvable total (no integer 4-16 under "
                        "'total' or a recoverable fallback). Advisory only "
                        "— not blocking. If the variety stamp is the cause, "
                        "re-stamp Task B with a canonical "
                        "metadata.variety.total (see pact-variety.md "
                        "Per-Dispatch Variety Stamping).",
                    ))

    return advisories


# ─── journal emit ────────────────────────────────────────────────────────────


def _journal_lifecycle_decision(
    input_data: dict, advisories: list[tuple[str, str]]
) -> None:
    """Emit a free-form 'lifecycle_decision' journal event. Best-effort —
    fail-open if journal append fails (matches existing append_event policy).

    Each advisory is a (rule, message) tuple; the journal records both the
    behavioral rule identifiers and the human-readable messages so
    downstream tooling can group by rule without parsing prose.
    """
    try:
        rules = [rule for rule, _ in advisories]
        messages = [msg for _, msg in advisories]
        event = make_event(
            "lifecycle_decision",
            tool_name=input_data.get("tool_name", ""),
            advisories_count=len(advisories),
            rules=rules,
            advisories=messages,
            verdict="advisory" if advisories else "allow",
        )
        append_event(event)
    except Exception:
        pass


# ─── main ────────────────────────────────────────────────────────────────────


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → no-op (input-side failure is harness's domain).
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    # Short-circuit on tools we don't care about (defensive — matcher should
    # already restrict to TaskCreate|TaskUpdate, but PostToolUse hooks can
    # be invoked from a co-located matcher entry alongside other tools).
    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("TaskCreate", "TaskUpdate"):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        pact_context.init(input_data)
        advisories = evaluate_lifecycle(input_data)
    except Exception as e:  # noqa: BLE001 — runtime catch-all → advisory
        _emit_load_failure_advisory("runtime", e, input_data)
        return  # unreachable; helper exits

    _journal_lifecycle_decision(input_data, advisories)

    if not advisories:
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n\n".join(msg for _, msg in advisories),
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
